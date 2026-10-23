"""Where AI is used — and how each AI output is verified before it can influence a result.

1. compile_rules      free-text rulebook -> rule cards. Each card must quote the source verbatim;
                      quotes are fuzzy-matched against the document and unverifiable cards are rejected.
2. second_reader      independent reading of raster artwork. Agreement with OCR raises confidence;
                      disagreement lowers it (-> human review). Text the AI claims to see must exist in
                      the OCR lines it cites, otherwise it is rejected.
3. judge_rule         judgment-only rules. The AI can pass a rule or flag it for review, never fail it.
4. confirm_absence    second look before reporting "missing" on raster artwork.
5. answer_question    follow-up questions about a finding, grounded in that finding's evidence.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from rapidfuzz import fuzz

from app.models import AiEvent, Fact, Finding, Rule
from app.util import norm_cmp, parse_quantity, to_float

# ---------------------------------------------------------------- shared helpers


def _page_images(ctx, max_pages: int = 2) -> list[bytes]:
    imgs = []
    for pg in ctx.art.pages[:max_pages]:
        p = ctx.run_dir / pg.image
        if p.exists():
            imgs.append(p.read_bytes())
    return imgs


def _lines_block(art, limit: int = 220) -> str:
    pdp = art.pdp()
    lines = sorted(art.content_lines(), key=lambda l: (0 if pdp and l.panel == pdp.id else 1, l.page, l.bbox[1], l.bbox[0]))
    out = []
    for l in lines[:limit]:
        where = "front" if pdp and l.panel == pdp.id else "other"
        out.append(f"[{l.id}] ({where}) {l.text}")
    return "\n".join(out)


def _ns(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", (s or "").casefold())


def _grounded(art, text: str, line_ids: list[str]) -> bool:
    lines = [art.line(i) for i in line_ids if art.line(i)]
    if not lines or not text:
        return False
    joined = " ".join(l.text for l in lines)
    return fuzz.partial_ratio(_ns(text), _ns(joined)) >= 80


# ---------------------------------------------------------------- 1. rule compiler

CHECK_CATALOG = """
fact_present      required element exists. params: fact (FACT or list of FACTs that are all required),
                  scope ("any"|"front"), require (optional {attr: true} using mrp.inclusive_of_taxes,
                  manufacturer.has_address, customer_care.has_contact)
fact_format       element exists and matches a regex. params: fact, regex, description
matches_approved  element equals the approved product data. params: fact, approved_field, mode
                  ("text"|"quantity"|"number"|"digits"|"ingredients"|"nutrition"), scope, tolerance
cross_consistency all occurrences of an element agree. params: fact
min_text_height   characters are at least a physical height. params: fact, scope, min_mm OR
                  area_table [[max_panel_cm2 or null, min_mm], ...]
text_style        element is bold. params: fact, bold: true
symbol_present    veg / non-veg mark. params: kind ("auto"|"veg_mark"|"nonveg_mark"), scope
allergen_coverage allergens in the ingredients are declared in a "Contains" statement. params: {}
nutrition_math    nutrition arithmetic. params: kind ("energy"|"subtotals"|"per_serve"|"rda"), tolerance, reference
claim_threshold   nutrition claims meet conditions. params: claims [{name, patterns:[regex], nutrient or
                  derived:"protein_energy_pct", op ("<="|"<"|">="|">"), value, unit}]
barcode_valid     barcode scans, valid check digit, matches approved GTIN. params: {}
qr_present        a QR code is present. params: {}
text_present      a required phrase appears. params: patterns [regex], label, scope
text_absent       forbidden phrases do not appear. params: patterns [regex], label
fop_warning       front-of-pack warning above nutrient thresholds. params: nutrients [{nutrient, threshold,
                  unit, warning, patterns}], basis ("per_100" | "dv" = % Daily Value per serving)
identity_on_panel every word of the approved name is on the principal display panel. params: approved_field
net_quantity_format US net quantity: 'Net Wt' term + oz/lb + metric. params: require_label, require_us,
                  require_metric, scope
approved_copy     an approved text field appears word for word. params: field (approved data field), label
claims_approved   claims on the pack are on the approved claims list. params: approved_field
claim_conflict    a claim contradicted by the ingredients. params: claims [{name, patterns:[regex] OR nutrient, op,
                  value; forbidden:[regex of ingredients]; reason}]
precautionary_conflict 'may contain' names an allergen that is an ingredient. params: allergens ("us"|"fssai")
energy_consistency energy printed outside the nutrition panel matches it. params: tolerance
hidden_text       text covered by other artwork in the print file. params: {}
ai_judgment       anything that needs judgment. params: instruction
FACTS: product_name, net_quantity, mrp, fssai_license, ingredients, allergen_statement, nutrition, best_before,
       mfg_date, batch_no, manufacturer, customer_care
NUTRIENTS: energy_kcal, protein_g, carbohydrate_g, total_sugars_g, added_sugars_g, total_fat_g, saturated_fat_g,
           trans_fat_g, cholesterol_mg, sodium_mg, dietary_fibre_g, vitamin_d_mcg, calcium_mg, iron_mg, potassium_mg
nutrition_math kinds also include: "dv" (%DV vs FDA Daily Values), "serving_size" (household + metric),
           "servings_net" (servings x serving size = net quantity)
"""

SYSTEM_COMPILER = f"""You convert packaging-labelling requirements written in natural language into rule cards
for an automated artwork checker.

Return JSON: {{"rules": [{{"title": str, "quote": str, "check": str, "params": object,
"severity": "critical"|"major"|"minor", "status": "in_force"|"upcoming"|"proposed",
"effective_from": "YYYY-MM-DD" or null, "why": str}}]}}

Hard rules:
1. "quote" MUST be copied word-for-word from the source (one or two sentences, at most 400 characters).
   Quotes are verified automatically; any card whose quote is not in the source is discarded.
2. Only create cards for requirements the source actually states. Never add requirements from your own
   knowledge of regulations.
3. Prefer the most specific deterministic check. Use ai_judgment only when nothing else fits.
4. severity: critical = consumer safety or legal identity (allergens, licence, net quantity, veg mark);
   major = other mandatory declarations; minor = presentation or style.
5. status: in_force unless the text says the requirement starts in the future (upcoming + effective_from)
   or is only proposed or draft (proposed).
6. "why": one short sentence on the consequence of breaking the rule.

Check catalogue:
{CHECK_CATALOG}"""

from app.rules.rulebook import VALID_CHECKS  # noqa: E402  (single source of truth)
VALID_FACTS = {"product_name", "net_quantity", "mrp", "fssai_license", "ingredients", "allergen_statement",
               "nutrition", "best_before", "mfg_date", "batch_no", "manufacturer", "customer_care"}


def verify_quote(quote: str, pages: list[tuple[int, str]]) -> tuple[bool, Optional[int], float]:
    q = norm_cmp(quote)
    if len(q) < 12:
        return False, None, 0.0
    best_page, best = None, 0.0
    for pno, text in pages:
        s = fuzz.partial_ratio(q, norm_cmp(text))
        if s > best:
            best_page, best = pno, s
    return best >= 90, best_page, best


def _valid_params(check: str, params: dict) -> Optional[str]:
    facts = params.get("fact")
    facts = facts if isinstance(facts, list) else ([facts] if facts else [])
    if check in ("fact_present", "fact_format", "matches_approved", "cross_consistency", "min_text_height",
                 "text_style") and not facts:
        return "missing 'fact'"
    bad = [f for f in facts if f not in VALID_FACTS]
    if bad:
        return f"unknown fact {bad}"
    if check == "fact_format":
        try:
            re.compile(params.get("regex", ""))
        except re.error:
            return "invalid regex"
    if check in ("text_present", "text_absent"):
        try:
            [re.compile(p) for p in params.get("patterns", [])]
        except re.error:
            return "invalid pattern"
        if not params.get("patterns"):
            return "missing patterns"
    return None


def compile_rules_llm(pages: list[tuple[int, str]], doc_name: str, llm, events: list[AiEvent]) -> list[Rule]:
    chunks, cur, cur_len = [], [], 0
    for pno, text in pages:
        if cur and cur_len + len(text) > 9000:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append((pno, text))
        cur_len += len(text)
    if cur:
        chunks.append(cur)
    rules: list[Rule] = []
    n = 0
    for chunk in chunks[:8]:
        src = "\n\n".join(f"--- page {p} ---\n{t}" for p, t in chunk)
        data = llm.json(SYSTEM_COMPILER, f"SOURCE DOCUMENT: {doc_name}\n\n{src}", max_tokens=6000)
        if not data:
            events.append(AiEvent(stage="rules", action="error",
                                  detail=f"Rule compilation failed for part of '{doc_name}': {llm.last_error}"))
            continue
        for card in (data.get("rules") or []) if isinstance(data, dict) else []:
            quote = str(card.get("quote", "")).strip()
            ok, page, score = verify_quote(quote, pages)
            title = str(card.get("title") or quote[:60])
            if not ok:
                events.append(AiEvent(stage="rules", action="rejected",
                                      detail=f"“{title}”: quote not found in the source (best match {score:.0f}%) — "
                                             f"card discarded."))
                continue
            check = card.get("check")
            params = card.get("params") if isinstance(card.get("params"), dict) else {}
            notes = []
            problem = None if check in VALID_CHECKS else f"unknown check '{check}'"
            problem = problem or _valid_params(check, params)
            if problem:
                notes.append(f"AI proposed an invalid check ({problem}); converted to a judgment rule.")
                events.append(AiEvent(stage="rules", action="downgraded",
                                      detail=f"“{title}”: {problem} — kept as a human-judgment rule."))
                check, params = "ai_judgment", {"instruction": quote}
            n += 1
            status = card.get("status") if card.get("status") in ("in_force", "upcoming", "proposed") else "in_force"
            eff = card.get("effective_from")
            if eff and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(eff)):
                eff = None
            sev = card.get("severity") if card.get("severity") in ("critical", "major", "minor") else "major"
            rules.append(Rule(id=f"C{n:02d}", title=title[:90], check=check, params=params, severity=sev, quote=quote,
                              source={"doc": doc_name, "page": page}, why=str(card.get("why", ""))[:240],
                              status=status, effective_from=eff, origin="ai_compiled", quote_verified=True,
                              notes=notes))
            events.append(AiEvent(stage="rules", action="accepted",
                                  detail=f"C{n:02d} “{title}” ({check}) — quote verified on page {page}."))
    return rules


# ---------------------------------------------------------------- 2. second reader (raster artwork)

SYSTEM_READER = """You read packaging artwork images precisely, character by character, like a proofreader.
You are also given OCR lines with IDs. For each field, report the text exactly as printed on the artwork
and the IDs of the OCR lines where it appears (even if the OCR text is garbled). If a field is not
visible, use null. Never guess or normalise text."""

READER_FIELDS = {
    "product_name": "product name on the front",
    "net_quantity": "every net quantity declaration, e.g. 'Net Wt. 200 g' (list)",
    "mrp": "the MRP / retail price declaration",
    "fssai_license": "the FSSAI licence number declaration",
    "allergen_statement": "the allergen 'Contains:' statement",
    "ingredients": "the full ingredient list",
    "nutrition_per_100g": "per-100 g values as numbers: energy_kcal, protein_g, carbohydrate_g, total_sugars_g, "
                          "added_sugars_g, total_fat_g, saturated_fat_g, trans_fat_g, sodium_mg",
}


def second_reader(art, facts: dict[str, list[Fact]], llm, run_dir: Path, events: list[AiEvent]) -> None:
    if not llm or not llm.enabled or art.quality != "raster":
        return
    imgs = [(run_dir / pg.image).read_bytes() for pg in art.pages[:2] if (run_dir / pg.image).exists()]
    fields = "\n".join(f'- "{k}": {v}' for k, v in READER_FIELDS.items())
    prompt = (f"Fields:\n{fields}\n\nFor text fields return {{\"text\": str, \"line_ids\": [str]}} (net_quantity: a "
              f"list of those). For nutrition_per_100g return an object of numbers.\n\nOCR LINES:\n{_lines_block(art)}")
    data = llm.json(SYSTEM_READER, prompt, images=imgs, max_tokens=3000)
    if not isinstance(data, dict):
        events.append(AiEvent(stage="facts", action="error", detail=f"Second reader unavailable: {llm.last_error}"))
        return

    def compare(key: str, ai: Any, parser_fact: Optional[Fact], parse=None):
        if not isinstance(ai, dict) or not ai.get("text"):
            return
        text, ids = str(ai["text"]), [str(i) for i in ai.get("line_ids") or []]
        if parser_fact is None:
            if not _grounded(art, text, ids):
                events.append(AiEvent(stage="facts", action="rejected",
                                      detail=f"{key}: AI reported “{text[:60]}” but the cited lines do not contain it."))
                return
            value = parse(text) if parse else text
            if value is None:
                return
            f = Fact(key=key, value=value, raw=text, line_ids=ids, confidence=0.7, method="ai",
                     panel=art.line(ids[0]).panel if ids and art.line(ids[0]) else None,
                     notes=["Found by the AI second reader; OCR text did not parse."])
            facts.setdefault(key, []).append(f)
            events.append(AiEvent(stage="facts", action="accepted", detail=f"{key}: AI found “{text[:60]}” "
                                                                            f"(grounded in {', '.join(ids)})."))
            return
        if _ns(text) == _ns(parser_fact.raw) or fuzz.partial_ratio(_ns(text), _ns(parser_fact.raw)) >= 97:
            parser_fact.confidence = max(parser_fact.confidence, 0.95)
            parser_fact.notes.append("Confirmed by the AI second reader.")
            events.append(AiEvent(stage="facts", action="confirmed", detail=f"{key}: OCR and AI agree on "
                                                                             f"“{text[:60]}”."))
        else:
            parser_fact.confidence = min(parser_fact.confidence, 0.5)
            parser_fact.notes.append(f"AI second reader saw “{text[:80]}” — readings disagree, needs a human.")
            events.append(AiEvent(stage="facts", action="downgraded",
                                  detail=f"{key}: OCR read “{parser_fact.raw[:50]}”, AI read “{text[:50]}” — "
                                         f"sent to review."))

    first = lambda k: (facts.get(k) or [None])[0]  # noqa: E731
    compare("product_name", data.get("product_name"), first("product_name"))
    compare("mrp", data.get("mrp"), first("mrp"), parse=lambda t: to_float(re.sub(r"(?i).*?r\.?p\.?", "", t)))
    compare("fssai_license", data.get("fssai_license"), first("fssai_license"),
            parse=lambda t: re.sub(r"\D", "", t) or None)
    compare("allergen_statement", data.get("allergen_statement"), first("allergen_statement"))
    compare("ingredients", data.get("ingredients"), first("ingredients"))
    nq = data.get("net_quantity")
    if isinstance(nq, dict):
        nq = [nq]
    for item in nq or []:
        if not isinstance(item, dict):
            continue
        q = parse_quantity(str(item.get("text", "")))
        ids = [str(i) for i in item.get("line_ids") or []]
        match = next((f for f in facts.get("net_quantity", []) if set(f.line_ids) & set(ids)), None)
        compare("net_quantity", item, match, parse=parse_quantity)
        if match and q and (q["base"], q["base_unit"]) != (match.value["base"], match.value["base_unit"]):
            match.confidence = min(match.confidence, 0.5)
    n = first("nutrition")
    ai_n = data.get("nutrition_per_100g")
    if n is not None and isinstance(ai_n, dict):
        conf = n.attrs.setdefault("conf", {}).setdefault("per_100", {})
        agree, disagree = [], []
        for k, v in ai_n.items():
            fv = n.value.get("per_100", {}).get(k)
            av = to_float(str(v)) if v is not None else None
            if fv is None or av is None:
                continue
            if abs(fv - av) <= max(0.01 * abs(fv), 0.051):
                conf[k] = max(conf.get(k, n.confidence), 0.95)
                agree.append(k)
            else:
                conf[k] = min(conf.get(k, n.confidence), 0.5)
                disagree.append(f"{k} OCR {fv:g} vs AI {av:g}")
        if agree:
            events.append(AiEvent(stage="facts", action="confirmed",
                                  detail=f"Nutrition: AI confirms {len(agree)} per-100 g values."))
        if disagree:
            events.append(AiEvent(stage="facts", action="downgraded",
                                  detail="Nutrition readings disagree (sent to review): " + "; ".join(disagree)))


# ---------------------------------------------------------------- 3. judgment rules

SYSTEM_JUDGE = """You are a careful packaging-compliance reviewer. You check ONE rule against a packaging artwork.
You get the rule (quoted from the company's rulebook), the artwork image, key facts and the artwork text as
numbered lines like [L12]. Judge only what is visible. Cite the line IDs that support your verdict.
verdict: "pass" = clearly satisfied, "fail" = clearly violated, "uncertain" = cannot decide.
Return JSON: {"verdict": str, "explanation": "2-3 plain sentences", "evidence_line_ids": [str], "confidence": number}"""


def judge_rule(rule: Rule, ctx) -> "Outcome":
    from app.rules.checks import Outcome
    ing = (ctx.facts.get("ingredients") or [None])[0]
    nut = (ctx.facts.get("nutrition") or [None])[0]
    facts_txt = []
    if ing:
        facts_txt.append(f"Ingredients: {ing.value['text']}")
    if nut:
        facts_txt.append(f"Nutrition per 100 g: {json.dumps(nut.value.get('per_100', {}))}")
    prompt = (f"RULE {rule.id}: {rule.title}\nRule text: \"{rule.quote}\"\n"
              f"Instruction: {rule.params.get('instruction', rule.quote)}\n\nKEY FACTS:\n" + "\n".join(facts_txt) +
              f"\n\nARTWORK TEXT:\n{_lines_block(ctx.art)}")
    data = ctx.llm.json(SYSTEM_JUDGE, prompt, images=_page_images(ctx), max_tokens=1200)
    if not isinstance(data, dict):
        ctx.events.append(AiEvent(stage="judgment", action="error", detail=f"{rule.id}: AI unavailable "
                                                                           f"({ctx.llm.last_error})."))
        return Outcome("unknown", "The AI assistant could not evaluate this judgment rule; please review manually.",
                       method="none", confidence=0.0)
    verdict = str(data.get("verdict", "uncertain")).lower()
    ids = [i for i in (data.get("evidence_line_ids") or []) if ctx.art.line(str(i))]
    expl = str(data.get("explanation", "")).strip()[:600]
    conf = float(to_float(str(data.get("confidence", 0.6))) or 0.6)
    ev = ctx.ev_lines([str(i) for i in ids], "AI-cited text")
    if verdict == "fail" and not ids:
        ctx.events.append(AiEvent(stage="judgment", action="rejected",
                                  detail=f"{rule.id}: AI claimed a violation without citing artwork text — treated "
                                         f"as uncertain."))
        verdict = "uncertain"
    if verdict == "pass":
        ctx.events.append(AiEvent(stage="judgment", action="used", detail=f"{rule.id}: AI found no problem."))
        return Outcome("pass", f"AI assessment: {expl}", evidence=ev, method="ai", confidence=min(conf, 0.9),
                       notes=["Judgment made by AI — spot-check if in doubt."])
    if verdict == "fail":
        ctx.events.append(AiEvent(stage="judgment", action="downgraded",
                                  detail=f"{rule.id}: AI flagged a possible violation — sent to human review."))
        return Outcome("fail", f"AI flagged a possible problem: {expl}", evidence=ev, method="ai", confidence=conf)
    return Outcome("unknown", f"AI could not decide: {expl}", evidence=ev, method="ai", confidence=conf)


# ---------------------------------------------------------------- 4. confirm absence

SYSTEM_VERIFY = """You double-check packaging artwork for a compliance tool. Another reader could NOT find an
element. Look carefully at the whole image, including small print and rotated text.
Return JSON: {"present": true | false | null, "text_seen": "exact text if present, else empty",
"where": "short location description"}. Use null if you cannot tell."""


def confirm_absence(ctx, label: str) -> tuple[Optional[bool], str]:
    data = ctx.llm.json(SYSTEM_VERIFY, f"Element to look for: {label}.", images=_page_images(ctx), max_tokens=400)
    if not isinstance(data, dict):
        return None, f"AI second look unavailable ({ctx.llm.last_error})"
    present = data.get("present")
    if present is True:
        seen = str(data.get("text_seen", ""))
        return True, f"AI second look found it: “{seen[:80]}” ({data.get('where', '')})"
    if present is False:
        return False, "AI second look also found nothing"
    return None, "AI second look was not sure"


# ---------------------------------------------------------------- 5. follow-up questions

SYSTEM_QA = """You are the assistant inside a packaging-artwork compliance checker. Answer the user's question
about ONE finding, using only the finding, the rule text and the evidence provided. If the answer needs
information that is not provided, say so. Be concise (under 120 words). Do not invent regulations; refer to the
rulebook quote instead. When suggesting a fix, give the exact corrected wording if possible."""


def answer_question(finding: Finding, question: str, run: dict, llm) -> dict:
    ev_text = []
    art = run["artwork"]
    for e in finding.evidence:
        if e.line_id and art.line(e.line_id):
            ev_text.append(f"[{e.line_id}] {art.line(e.line_id).text}")
    context = {
        "rule": {"id": finding.rule_id, "title": finding.title, "quote": finding.quote, "basis": finding.basis,
                 "why_it_matters": finding.why},
        "finding": {"verdict": finding.verdict, "message": finding.message, "expected": finding.expected,
                    "found": finding.found, "decided_by": finding.method, "notes": finding.notes},
        "evidence_text": ev_text,
    }
    if llm and llm.enabled:
        text = llm.complete(SYSTEM_QA, f"FINDING CONTEXT:\n{json.dumps(context, indent=1)}\n\nQUESTION: {question}",
                            max_tokens=500)
        if text:
            return {"answer": text.strip(), "source": "ai"}
    return {"answer": _template_answer(finding, question, ev_text), "source": "template"}


def _template_answer(f: Finding, q: str, ev_text: list[str]) -> str:
    ql = q.lower()
    status = {"FAIL": "a definite problem", "REVIEW": "something a person should verify", "PASS": "compliant",
              "NA": "not applicable to this artwork"}[f.verdict]
    parts = [f"{f.rule_id} is marked as {status}. {f.message}"]
    if any(w in ql for w in ("why", "matter", "important", "risk")) and f.why:
        parts.append(f"Why it matters: {f.why}")
    if any(w in ql for w in ("fix", "correct", "should", "change", "how")):
        if f.expected:
            parts.append(f"To fix it, the artwork should show: {f.expected}.")
        parts.append(f"The rule says: “{f.quote}”")
    if any(w in ql for w in ("sure", "confiden", "evidence", "where", "how do you know")):
        parts.append(f"Decided by {'deterministic code' if f.method == 'code' else f.method}"
                     f" with confidence {f.confidence:.0%}. Evidence: " + ("; ".join(ev_text) or "see highlighted area"))
    parts.append("(Connect an LLM API key for free-form answers.)")
    return " ".join(parts)
