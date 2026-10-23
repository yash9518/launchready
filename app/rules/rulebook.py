"""Load a rulebook: structured rule cards (Markdown/YAML/JSON) or a free-text document.

Free-text documents (PDF/DOCX/TXT) are compiled into rule cards by the LLM when
one is configured, otherwise by a conservative pattern compiler. Either way,
every rule keeps a verbatim quote of its source text.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Optional

import yaml

from app.models import AiEvent, Rule

VALID_CHECKS = {"fact_present", "fact_format", "matches_approved", "cross_consistency", "min_text_height",
                "text_style", "symbol_present", "allergen_coverage", "nutrition_math", "claim_threshold",
                "barcode_valid", "qr_present", "text_present", "text_absent", "fop_warning", "ai_judgment",
                "identity_on_panel", "net_quantity_format", "approved_copy", "claims_approved", "claim_conflict",
                "precautionary_conflict", "energy_consistency", "hidden_text"}


def document_pages(path: Path) -> list[tuple[int, str]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        import pymupdf
        doc = pymupdf.open(path)
        return [(i + 1, p.get_text("text")) for i, p in enumerate(doc)]
    if ext == ".docx":
        import docx
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return [(1, "\n".join(parts))]
    return [(1, path.read_text(encoding="utf-8", errors="replace"))]


def _date_str(v) -> Optional[str]:
    if v in (None, ""):
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


def parse_rule_cards(md: str, doc_name: str) -> list[Rule]:
    rules: list[Rule] = []
    lines = md.splitlines()
    starts = [i for i, l in enumerate(lines) if l.startswith("## ")]
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        heading = lines[start][3:].strip()
        body = "\n".join(lines[start + 1:end])
        block = re.search(r"```rule\s*\n(.*?)```", body, re.S)
        if not block:
            continue
        card = yaml.safe_load(block.group(1)) or {}
        m = re.match(r"([A-Za-z]{1,4}-?\d+[A-Za-z]?)\s*[—–:\-]+\s*(.+)", heading)
        rid, title = (m.group(1), m.group(2).strip()) if m else (card.get("id", f"R{k + 1:02d}"), heading)

        def field(name: str) -> str:
            fm = re.search(r"\*\*" + name + r":\*\*\s*(.+)", body)
            return fm.group(1).strip() if fm else ""

        check = card.get("check", "ai_judgment")
        notes = []
        if check not in VALID_CHECKS:
            notes.append(f"Unknown check type '{check}' — treated as a human-judgment rule.")
            check = "ai_judgment"
        rules.append(Rule(
            id=str(card.get("id", rid)), title=title, check=check, params=card.get("params") or {},
            severity=card.get("severity", "major"), quote=field("Rule") or title, basis=field("Basis"),
            why=field("Why it matters"), status=card.get("status", "in_force"),
            effective_from=_date_str(card.get("effective_from")), enabled=card.get("enabled", True),
            source={"doc": doc_name, "section": heading, "line": start + 1}, origin="rulebook", notes=notes,
        ))
    return rules


# ---------------------------------------------------------------- pattern compiler (no LLM)

_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}


def _find_date(s: str) -> Optional[str]:
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", s)
    if m:
        return m.group(0)
    m = re.search(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", s, re.I)
    if m:
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b", s, re.I)
    if m:
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return None


TEMPLATES: list[tuple[str, str, dict, str, str]] = [
    (r"\bbold\b.*allergen|allergen.*\bbold\b", "text_style", {"fact": "allergen_statement", "bold": True}, "minor",
     "Allergen statement in bold"),
    (r"allergen", "allergen_coverage", {}, "critical", "Allergen declaration"),
    (r"net\s*(quantity|weight|content)", "fact_present", {"fact": "net_quantity"}, "critical", "Net quantity"),
    (r"(fssai|licen[cs]e)\s*(number|no\b)", "fact_format",
     {"fact": "fssai_license", "regex": r"^\d{14}$", "description": "exactly 14 digits"}, "critical",
     "FSSAI licence number"),
    (r"\b(veg|vegetarian|non-veg)\w*\b.*\b(symbol|mark|logo)|\b(symbol|mark|logo)\b.*\b(veg|vegetarian)",
     "symbol_present", {"kind": "auto", "scope": "front"}, "critical", "Veg / non-veg symbol"),
    (r"nutrition(al)?\s*(information|facts|table|panel|declaration)", "fact_present", {"fact": "nutrition"}, "major",
     "Nutrition information"),
    (r"\bingredient", "fact_present", {"fact": "ingredients"}, "critical", "Ingredient list"),
    (r"\b(m\.?r\.?p|maximum\s*retail\s*price|retail\s*sale\s*price)\b", "fact_present", {"fact": "mrp"}, "major",
     "Retail sale price"),
    (r"best\s*before|use\s*by|expiry", "fact_present", {"fact": "best_before"}, "major", "Date marking"),
    (r"date\s*of\s*(manufacture|packing)|(manufactur\w*|packing)\s*date", "fact_present", {"fact": "mfg_date"},
     "major", "Date of manufacture / packing"),
    (r"\b(batch|lot)\b", "fact_present", {"fact": "batch_no"}, "minor", "Batch / lot number"),
    (r"(name|address).{0,40}(manufacturer|packer|marketer)|(manufacturer|packer|marketer).{0,40}(name|address)",
     "fact_present", {"fact": "manufacturer", "require": {"has_address": True}}, "major",
     "Manufacturer name and address"),
    (r"(consumer|customer)\s*care|complaint", "fact_present",
     {"fact": "customer_care", "require": {"has_contact": True}}, "major", "Consumer care details"),
    (r"barcode|\bgtin\b|\bean\b", "barcode_valid", {}, "major", "Retail barcode"),
    (r"\bqr\s*code\b", "qr_present", {}, "minor", "QR code"),
    (r"\bclaims?\b", "claim_threshold", {}, "critical", "Nutrition claims"),
    (r"product\s*name|name\s*of\s*the\s*(food|product)", "fact_present", {"fact": "product_name", "scope": "front"},
     "major", "Product name"),
]
MODAL = re.compile(r"(?i)\b(must|shall|should|required|mandatory|has to|have to|need to|needs to|is to be|"
                   r"are to be|must not|shall not)\b")


def compile_rules_patterns(pages: list[tuple[int, str]], doc_name: str, events: list[AiEvent]) -> list[Rule]:
    rules: list[Rule] = []
    seen: set[tuple[str, str]] = set()
    judgment = 0
    for pno, text in pages:
        flat = re.sub(r"\s+", " ", text)
        for sent in re.split(r"(?<=[.;!?])\s+(?=[A-Z0-9\"“(])", flat):
            s = sent.strip()
            if len(s) < 20 or not MODAL.search(s):
                continue
            low = s.lower()
            status, eff = "in_force", None
            if re.search(r"\b(proposed|draft|not yet (in force|law))\b", low):
                status = "proposed"
            d = _find_date(s)
            if d and re.search(r"\b(from|with effect from|effective|starting|w\.e\.f\.?)\b", low) and \
                    d > dt.date.today().strftime("%Y-%m-%d"):
                status, eff = "upcoming", d
            matched = False
            for pat, check, params, sev, title in TEMPLATES:
                if re.search(pat, low):
                    params = dict(params)
                    if check == "fact_present" and params.get("fact") == "net_quantity" and re.search(
                            r"principal display|front", low):
                        params["scope"] = "front"
                    if check == "fact_present" and params.get("fact") == "mrp" and "tax" in low:
                        params["require"] = {"inclusive_of_taxes": True}
                    key = (check, json.dumps(params, sort_keys=True))
                    if key in seen:
                        matched = True
                        break
                    seen.add(key)
                    rules.append(Rule(id=f"T{len(rules) + 1:02d}", title=title, check=check, params=params,
                                      severity=sev, quote=s[:400], source={"doc": doc_name, "page": pno},
                                      status=status, effective_from=eff, origin="pattern_compiled",
                                      notes=["Created by the offline pattern compiler — review the rule table."]))
                    matched = True
                    break
            if not matched and judgment < 8:
                judgment += 1
                rules.append(Rule(id=f"T{len(rules) + 1:02d}", title=s[:70] + ("…" if len(s) > 70 else ""),
                                  check="ai_judgment", params={"instruction": s}, severity="major", quote=s[:400],
                                  source={"doc": doc_name, "page": pno}, status=status, effective_from=eff,
                                  origin="pattern_compiled", notes=["No deterministic check matched; needs judgment."]))
    events.append(AiEvent(stage="rules", action="skipped",
                          detail=f"No LLM configured: '{doc_name}' compiled by the offline pattern compiler into "
                                 f"{len(rules)} rules."))
    return rules


# ---------------------------------------------------------------- entry point

def load_rulebook(path: Path, display_name: str, llm=None, events: Optional[list[AiEvent]] = None) -> dict:
    events = events if events is not None else []
    ext = path.suffix.lower()
    if ext in (".yaml", ".yml", ".json"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if ext != ".json" else json.loads(path.read_text())
        items = data.get("rules", data) if isinstance(data, dict) else data
        rules = [Rule(**{**r, "effective_from": _date_str(r.get("effective_from"))}) for r in items]
        return {"rules": rules, "kind": "structured", "name": display_name}
    pages = document_pages(path)
    full = "\n".join(t for _, t in pages)
    if "```rule" in full:
        return {"rules": parse_rule_cards(full, display_name), "kind": "rule cards", "name": display_name}
    if llm is not None and llm.enabled:
        from app.ai import compile_rules_llm
        rules = compile_rules_llm(pages, display_name, llm, events)
        if rules:
            return {"rules": rules, "kind": "compiled by AI (quotes verified)", "name": display_name}
    return {"rules": compile_rules_patterns(pages, display_name, events), "kind": "compiled by pattern matcher",
            "name": display_name}
