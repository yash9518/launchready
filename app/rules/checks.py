"""The check library. Each check turns (rule, artwork facts, approved data) into an Outcome.

Checks are deterministic code. The only exception is `ai_judgment`, whose
result can never be a definite failure (see engine.decide).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pymupdf
from PIL import Image

from app.extract.vision import measure_pdf_char_height_mm, measure_raster_text_height_px
from app.facts import (ALLERGEN_NAMES, ALLERGEN_TABLES, DEFAULT_ALLERGENS, FACT_LABELS, claim_texts,
                       find_allergens)
from app.models import AiEvent, Artwork, Evidence, Fact, Rule, TextLine
from app.util import (fmt_num, gtin_valid, norm_cmp, norm_space, ocr_plausible_misread, parse_quantity,
                      quantity_str, split_top_level, to_float, word_diff)

NUTRIENT_LABELS = {
    "energy_kcal": ("Energy", "kcal"), "protein_g": ("Protein", "g"), "carbohydrate_g": ("Carbohydrate", "g"),
    "total_sugars_g": ("Total sugars", "g"), "added_sugars_g": ("Added sugars", "g"),
    "total_fat_g": ("Total fat", "g"), "saturated_fat_g": ("Saturated fat", "g"), "trans_fat_g": ("Trans fat", "g"),
    "cholesterol_mg": ("Cholesterol", "mg"), "sodium_mg": ("Sodium", "mg"), "dietary_fibre_g": ("Dietary fibre", "g"),
    "salt_g": ("Salt", "g"), "vitamin_d_mcg": ("Vitamin D", "mcg"), "calcium_mg": ("Calcium", "mg"),
    "iron_mg": ("Iron", "mg"), "potassium_mg": ("Potassium", "mg"),
}
# FDA Daily Values for adults and children 4+ (21 CFR 101.9(c)(8)(iv) and (c)(9)).
FDA_DV = {"total_fat_g": 78, "saturated_fat_g": 20, "cholesterol_mg": 300, "sodium_mg": 2300, "carbohydrate_g": 275,
          "dietary_fibre_g": 28, "added_sugars_g": 50, "protein_g": 50, "vitamin_d_mcg": 20, "calcium_mg": 1300,
          "iron_mg": 18, "potassium_mg": 4700}

DEFAULT_CLAIMS = [
    {"name": "Low fat", "patterns": [r"low[\s-]*fat"], "nutrient": "total_fat_g", "op": "<=", "value": 3.0, "unit": "g"},
    {"name": "Sugar free", "patterns": [r"sugar[\s-]*free"], "nutrient": "total_sugars_g", "op": "<=", "value": 0.5,
     "unit": "g"},
    {"name": "No added sugar", "patterns": [r"no\s*added\s*sugars?"], "nutrient": "added_sugars_g", "op": "<=",
     "value": 0.0, "unit": "g"},
    {"name": "High protein", "patterns": [r"high[\s-]*protein"], "derived": "protein_energy_pct", "op": ">=",
     "value": 20, "unit": "% energy"},
]


@dataclass
class Outcome:
    status: str                      # pass | fail | unknown | na
    message: str
    evidence: list[Evidence] = field(default_factory=list)
    expected: Optional[str] = None
    found: Optional[str] = None
    diff: Optional[list] = None
    confidence: float = 1.0
    method: str = "code"
    absence: bool = False
    absence_fact: Optional[str] = None
    absence_label: Optional[str] = None
    notes: list[str] = field(default_factory=list)


class CheckContext:
    def __init__(self, art: Artwork, facts: dict[str, list[Fact]], approved: Optional[dict], run_dir: Path,
                 artwork_path: Optional[Path], llm=None, events: Optional[list[AiEvent]] = None):
        self.art = art
        self.facts = facts
        self.approved = approved or {}
        self.run_dir = run_dir
        self.artwork_path = artwork_path
        self.llm = llm
        self.events = events if events is not None else []
        self._pdf = None
        self._gray: dict[tuple[int, int], np.ndarray] = {}
        self.pdp = art.pdp()
        self.lines = art.content_lines()
        self.structured_ids: set[str] = set()
        for key in ("ingredients", "allergen_statement", "precautionary_statement", "nutrition", "manufacturer",
                    "customer_care"):
            for f in facts.get(key, []):
                self.structured_ids.update(f.line_ids)
        self._claim_texts: Optional[list[tuple[str, list[TextLine]]]] = None

    def claim_texts(self, scope: str = "any") -> list[tuple[str, list[TextLine]]]:
        """Free text (single lines and short badge groups) where claims and marketing terms are searched."""
        if self._claim_texts is None:
            self._claim_texts = claim_texts(self.lines, self.structured_ids)
        if scope == "front" and self.pdp is not None:
            return [(t, ls) for t, ls in self._claim_texts if all(l.panel == self.pdp.id for l in ls)]
        return self._claim_texts

    def nutrition_basis(self, n: Fact, prefer: str = "per_100") -> tuple[str, dict]:
        """('per_100', values) for tables per 100 g, ('per_serve', values) for a US Nutrition Facts panel."""
        v100, vs = n.value.get("per_100") or {}, n.value.get("per_serve") or {}
        if prefer == "per_serve" and vs:
            return "per_serve", vs
        if v100:
            return "per_100", v100
        return "per_serve", vs

    def ingredient_hits(self, patterns: list[str]) -> list[tuple[str, str]]:
        """(ingredient item, matched text) for ingredients matching any pattern."""
        ing = (self.facts.get("ingredients") or [None])[0]
        if ing is None:
            return []
        hits = []
        for item in ing.value.get("items", []):
            for p in patterns:
                m = re.search(p, item, re.I)
                if m:
                    hits.append((item, m.group(0)))
                    break
        return hits

    def lines_with(self, f: Fact, text: str) -> list[str]:
        ids = [lid for lid in f.line_ids if self.art.line(lid) and
               re.search(re.escape(text.split()[0]), self.art.line(lid).text, re.I)]
        return ids or f.line_ids[:1]

    # ------------------------------------------------------------ helpers
    def occ(self, key: str, scope: str = "any") -> list[Fact]:
        lst = self.facts.get(key, [])
        if scope == "front" and self.pdp is not None:
            lst = [f for f in lst if f.panel == self.pdp.id]
        return lst

    def panel_name(self, panel_id: Optional[str]) -> str:
        for p in self.art.panels:
            if p.id == panel_id:
                return "front panel" if p.is_pdp and len(self.art.panels) > 1 else (
                    "artwork" if p.is_pdp else p.label.lower())
        return "artwork"

    def ev_fact(self, f: Fact, label: str = "") -> list[Evidence]:
        out = []
        if f.line_ids:
            for lid in f.line_ids:
                ln = self.art.line(lid)
                if ln:
                    out.append(Evidence(page=ln.page, bbox=ln.bbox, label=label or FACT_LABELS.get(f.key, f.key),
                                        line_id=lid, kind="line"))
        elif "bbox" in f.attrs:
            kind = "barcode" if f.key in ("gtin", "qr_code") else "symbol"
            out.append(Evidence(page=f.attrs.get("page", 0), bbox=f.attrs["bbox"],
                                label=label or FACT_LABELS.get(f.key, f.key), kind=kind))
        return out

    def ev_lines(self, line_ids: list[str], label: str) -> list[Evidence]:
        out = []
        for lid in line_ids:
            ln = self.art.line(lid)
            if ln:
                out.append(Evidence(page=ln.page, bbox=ln.bbox, label=label, line_id=lid, kind="line"))
        return out

    def ev_panel(self, label: str = "expected location") -> list[Evidence]:
        p = self.pdp
        if not p:
            return []
        return [Evidence(page=p.page, bbox=p.bbox, label=label, kind="panel")]

    def raster(self, f: Optional[Fact] = None) -> bool:
        if f is not None and f.line_ids:
            ln = self.art.line(f.line_ids[0])
            return bool(ln and ln.source == "ocr")
        return self.art.quality == "raster"

    def pdf(self) -> Optional[pymupdf.Document]:
        if self._pdf is None and self.artwork_path and self.art.kind == "pdf":
            self._pdf = pymupdf.open(self.artwork_path)
        return self._pdf

    def gray(self, page: int, dpi: int = 600) -> tuple[Optional[np.ndarray], float]:
        """Grayscale page image and its scale (image px per page unit)."""
        if self.art.kind == "image":
            key = (page, 0)
            if key not in self._gray:
                p = self.run_dir / f"analysis{page}.png"
                if not p.exists():
                    return None, 1.0
                self._gray[key] = np.array(Image.open(p).convert("L"))
            return self._gray[key], 1.0
        doc = self.pdf()
        if doc is None:
            return None, 1.0
        key = (page, dpi)
        if key not in self._gray:
            pg = doc[page]
            pix = pg.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
            self._gray[key] = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
        return self._gray[key], dpi / 72.0

    def nutrition(self) -> Optional[Fact]:
        lst = self.facts.get("nutrition", [])
        return lst[0] if lst else None

    def nutr_rows_ev(self, n: Fact, keys: list[str], label: str = "") -> list[Evidence]:
        rows = n.attrs.get("rows", {})
        out = []
        for k in keys:
            out += self.ev_lines(rows.get(k, []), label or NUTRIENT_LABELS.get(k, (k, ""))[0])
        return out

    def value_conf(self, n: Fact, col: str, keys: list[str]) -> float:
        conf = n.attrs.get("conf", {}).get(col, {})
        vals = [conf.get(k, n.confidence) for k in keys]
        return min(vals) if vals else n.confidence


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _absent(ctx: CheckContext, key: str, scope: str, extra: str = "") -> Outcome:
    label = FACT_LABELS.get(key, key)
    where = " on the front panel" if scope == "front" and len(ctx.art.panels) > 1 else ""
    elsewhere = ctx.occ(key, "any") if scope == "front" else []
    if elsewhere:
        names = sorted({ctx.panel_name(f.panel) for f in elsewhere})
        ev = []
        for f in elsewhere:
            ev += ctx.ev_fact(f, f"{label} (not on front)")
        return Outcome("fail", f"The {label} appears only on the {', '.join(names)} — not on the front panel.",
                       evidence=ev + ctx.ev_panel("front panel"), expected=f"{label} on the front panel",
                       found=f"only on {', '.join(names)}")
    return Outcome("fail", f"No {label} found{where}.{extra}", evidence=ctx.ev_panel() if where else [],
                   expected=f"{label}{where}", found="not found", absence=True, absence_fact=key,
                   absence_label=label)


# ---------------------------------------------------------------- presence & format

def check_fact_present(rule: Rule, ctx: CheckContext) -> Outcome:
    p = rule.params
    keys = p.get("fact")
    keys = keys if isinstance(keys, list) else [keys]
    scope = p.get("scope", "any")
    require = p.get("require") or {}
    evidence, found_desc = [], []
    for key in keys:
        occ = ctx.occ(key, scope)
        if not occ:
            return _absent(ctx, key, scope)
        good = [f for f in occ if all(f.attrs.get(a) == v for a, v in require.items())]
        if not good:
            f = occ[0]
            missing_attrs = [a for a, v in require.items() if f.attrs.get(a) != v]
            missing = ", ".join(p.get("require_desc", {}).get(a) or _req_label(a) for a in missing_attrs)
            return Outcome("fail", f"The {FACT_LABELS.get(key, key)} is present but incomplete: missing {missing}.",
                           evidence=ctx.ev_fact(f), expected=f"{FACT_LABELS.get(key, key)} with {missing}",
                           found=f.raw, confidence=f.confidence, absence=True,
                           absence_fact=f"{key}:{missing_attrs[0]}", absence_label=missing)
        f = good[0]
        evidence += ctx.ev_fact(f)
        found_desc.append(f.raw if len(f.raw) < 90 else f.raw[:87] + "…")
    confs = [1.0] + [f.confidence for k in keys for f in ctx.occ(k, scope)[:1]]
    return Outcome("pass", "Found: " + " · ".join(f"“{d}”" for d in found_desc), evidence=evidence,
                   found="; ".join(found_desc), confidence=min(confs))


def _dedupe_plural(words: list[str]) -> list[str]:
    """['almond', 'almonds', 'pecans'] -> ['almonds', 'pecans'] (generic 'nuts' dropped when specific nuts exist)."""
    ws = sorted(set(words), key=len, reverse=True)
    out = [w for w in ws if not any(o != w and o.lower() in (w.lower() + "s", w.lower() + "es") for o in ws)]
    if len(out) > 1:
        out = [w for w in out if w.lower() not in ("nuts", "tree nuts", "tree nut")]
    return sorted(out)


def _req_label(attr: str) -> str:
    return {"inclusive_of_taxes": "'inclusive of all taxes'", "has_address": "a complete address with PIN code",
            "has_contact": "a phone number or e-mail"}.get(attr, attr.replace("_", " "))


def check_fact_format(rule: Rule, ctx: CheckContext) -> Outcome:
    p = rule.params
    key = p["fact"]
    occ = ctx.occ(key, p.get("scope", "any"))
    if not occ:
        return _absent(ctx, key, p.get("scope", "any"))
    rx = re.compile(p["regex"])
    bad = [f for f in occ if not rx.search(str(f.value))]
    if bad:
        f = bad[0]
        extra = f" (it has {len(str(f.value))} digits)" if str(f.value).isdigit() else ""
        return Outcome("fail", f"The {FACT_LABELS.get(key, key)} “{f.value}” is not {p.get('description', 'valid')}"
                               f"{extra}.", evidence=ctx.ev_fact(f), expected=p.get("description"),
                       found=str(f.value), confidence=f.confidence)
    f = occ[0]
    return Outcome("pass", f"{cap(FACT_LABELS.get(key, key))} “{f.value}” is {p.get('description', 'valid')}.",
                   evidence=ctx.ev_fact(f), found=str(f.value), confidence=f.confidence)


def _trigger(ctx: CheckContext, patterns: list[str]) -> list[tuple[str, list[TextLine]]]:
    """Free-text snippets (incl. graphics read by OCR) matching any of the patterns."""
    rx = [re.compile(x, re.I) for x in patterns]
    texts = ctx.claim_texts() + [(l.text, [l]) for l in ctx.lines if l.id in ctx.structured_ids]
    return [(t, ls) for t, ls in texts if any(r.search(t) for r in rx)]


def check_text_present(rule: Rule, ctx: CheckContext) -> Outcome:
    p = rule.params
    if p.get("when"):
        triggers = _trigger(ctx, p["when"])
        if not triggers:
            return Outcome("na", f"Not applicable: {p.get('when_label', 'the triggering claim or mark')} does not "
                                 f"appear on the artwork.")
    else:
        triggers = []
    pats = [re.compile(x, re.I) for x in p.get("patterns", [])]
    lines = [l for l in ctx.lines if p.get("scope") != "front" or not ctx.pdp or l.panel == ctx.pdp.id]
    hits = [l for l in lines if any(r.search(l.text) for r in pats)]
    if not hits:  # a required sentence may wrap over several lines
        texts = [(t, ls) for t, ls in ctx.claim_texts(p.get("scope", "any")) if any(r.search(t) for r in pats)]
        if texts:
            hits = texts[0][1]
    if hits:
        return Outcome("pass", f"Found: “{' '.join(h.text for h in hits[:3])[:120]}”",
                       evidence=ctx.ev_lines([h.id for h in hits[:3]], p.get("label", "")),
                       confidence=min(h.confidence for h in hits[:3]))
    ev = []
    trig_desc = ""
    for t, ls in triggers[:3]:
        ev += ctx.ev_lines([l.id for l in ls], f"triggers this rule: {t[:30]}")
    if triggers:
        trig_desc = " The artwork shows " + ", ".join(f"“{t[:40]}”" for t, _ in triggers[:3]) + ", which requires it."
    return Outcome("fail", f"Required text not found: {p.get('label', 'required statement')}.{trig_desc}",
                   evidence=ev, expected=p.get("label"), found="not found", absence=True,
                   absence_label=p.get("label", "required statement"))


def check_text_absent(rule: Rule, ctx: CheckContext) -> Outcome:
    p = rule.params
    pats = [re.compile(x, re.I) for x in p.get("patterns", [])]
    hits: list[tuple[list[TextLine], str]] = []
    covered: set[str] = set()
    texts = ctx.claim_texts() if p.get("skip_structured", True) else \
        [(l.text, [l]) for l in ctx.lines] + ctx.claim_texts()
    for text, ls in texts:
        if all(l.id in covered for l in ls):
            continue
        for r in pats:
            m = r.search(text)
            if m:
                hits.append((ls, m.group(0)))
                covered.update(l.id for l in ls)
                break
    if not hits:
        return Outcome("pass", f"None of the {p.get('label', 'prohibited terms')} appear on the artwork.")
    terms = sorted({re.sub(r"\s+", " ", h[1]).upper() for h in hits})
    ev = []
    for ls, term in hits:
        shown = re.sub(r"\s+", " ", term)
        ev += ctx.ev_lines([l.id for l in ls], f"prohibited: {shown}")
    return Outcome("fail", f"Found {p.get('label', 'prohibited terms')}: {', '.join('“' + t + '”' for t in terms)}.",
                   evidence=ev, found=", ".join(terms), confidence=min(min(l.confidence for l in h[0]) for h in hits))


# ---------------------------------------------------------------- approved data

def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", norm_cmp(s))


def check_matches_approved(rule: Rule, ctx: CheckContext) -> Outcome:
    p = rule.params
    key, field_name, mode = p["fact"], p.get("approved_field", p["fact"]), p.get("mode", "text")
    scope = p.get("scope", "any")
    appr = ctx.approved.get(field_name)
    if appr in (None, "", [], {}):
        return Outcome("na", f"No approved value for '{field_name}' was supplied, so this comparison was skipped.")
    occ = ctx.occ(key, scope)
    if not occ:
        o = _absent(ctx, key, scope)
        if mode == "text":
            o.notes.append(f"Approved value: “{appr}”.")
        return o
    fn = {"text": _cmp_text, "quantity": _cmp_quantity, "number": _cmp_number, "digits": _cmp_digits,
          "ingredients": _cmp_ingredients, "nutrition": _cmp_nutrition}.get(mode, _cmp_text)
    return fn(rule, ctx, occ, appr)


def _cmp_text(rule, ctx, occ, appr) -> Outcome:
    f = occ[0]
    found = str(f.value)
    raster = ctx.raster(f)
    same = _nospace(found) == _nospace(str(appr)) if raster else norm_cmp(found) == norm_cmp(str(appr))
    label = FACT_LABELS.get(f.key, f.key)
    if same:
        return Outcome("pass", f"The {label} matches the approved text: “{appr}”.", evidence=ctx.ev_fact(f),
                       expected=str(appr), found=found, confidence=f.confidence)
    o = Outcome("fail", f"The {label} on the artwork (“{found}”) differs from the approved text (“{appr}”).",
                evidence=ctx.ev_fact(f), expected=str(appr), found=found, diff=word_diff(str(appr), found),
                confidence=f.confidence)
    if raster and ocr_plausible_misread(str(appr), found):
        o.status = "unknown"
        o.notes.append("The difference could be an OCR misread rather than a real error — please confirm.")
    return o


def _cmp_quantity(rule, ctx, occ, appr) -> Outcome:
    q_appr = parse_quantity(str(appr)) if not isinstance(appr, dict) else appr
    if not q_appr:
        return Outcome("unknown", f"Approved quantity “{appr}” could not be parsed.")
    bad = [f for f in occ if (f.value["base"], f.value["base_unit"]) != (q_appr["base"], q_appr["base_unit"])]
    ev = []
    for f in occ:
        ev += ctx.ev_fact(f, ("✗ " if f in bad else "✓ ") + quantity_str(f.value))
    if not bad:
        return Outcome("pass", f"Net quantity {quantity_str(q_appr)} matches the approved pack size "
                               f"({len(occ)} declaration{'s' if len(occ) > 1 else ''} checked).", evidence=ev,
                       expected=quantity_str(q_appr), found=", ".join(quantity_str(f.value) for f in occ),
                       confidence=min(f.confidence for f in occ))
    desc = "; ".join(f"{ctx.panel_name(f.panel)} says {quantity_str(f.value)}" for f in bad)
    return Outcome("fail", f"Net quantity does not match the approved {quantity_str(q_appr)}: {desc}.", evidence=ev,
                   expected=quantity_str(q_appr), found=", ".join(quantity_str(f.value) for f in bad),
                   confidence=min(f.confidence for f in bad))


def _cmp_number(rule, ctx, occ, appr) -> Outcome:
    a = to_float(str(appr))
    bad = [f for f in occ if f.value is None or abs(float(f.value) - a) > 0.005]
    key = occ[0].key
    label = FACT_LABELS.get(key, key)
    show = (lambda v: f"Rs. {float(v):.2f}") if key == "mrp" else (lambda v: fmt_num(v))  # noqa: E731
    if not bad:
        return Outcome("pass", f"The {label} on the artwork ({show(a)}) matches the approved value.",
                       evidence=ctx.ev_fact(occ[0]), expected=show(a), found=show(occ[0].value),
                       confidence=occ[0].confidence)
    f = bad[0]
    return Outcome("fail", f"The {label} on the artwork is {show(f.value)} but the approved value is {show(a)}.",
                   evidence=ctx.ev_fact(f), expected=show(a), found=show(f.value), confidence=f.confidence)


def _cmp_digits(rule, ctx, occ, appr) -> Outcome:
    a = re.sub(r"\D", "", str(appr))
    bad = [f for f in occ if re.sub(r"\D", "", str(f.value)) != a]
    label = FACT_LABELS.get(occ[0].key, occ[0].key)
    if not bad:
        return Outcome("pass", f"The {label} matches the approved number {a}.", evidence=ctx.ev_fact(occ[0]),
                       expected=a, found=str(occ[0].value), confidence=occ[0].confidence)
    f = bad[0]
    d = "".join(("[" + y + "]") if x != y else y for x, y in zip(a.ljust(len(str(f.value))), str(f.value)))
    return Outcome("fail", f"The {label} on the artwork ({f.value}) is not the approved number ({a}).",
                   evidence=ctx.ev_fact(f), expected=a, found=str(f.value), diff=[{"op": "equal", "text": d}],
                   confidence=f.confidence)


def _alnum(s: str) -> str:
    return re.sub(r"[^0-9a-z%]", "", norm_cmp(s))


def _cmp_ingredients(rule, ctx, occ, appr) -> Outcome:
    f = occ[0]
    raster = ctx.raster(f)
    appr_text = str(appr).strip().rstrip(".")
    found_text = f.value["text"].rstrip(".")
    a_items = split_top_level(appr_text)
    b_items = f.value["items"]
    ev = ctx.ev_fact(f)
    if raster and _alnum(appr_text) == _alnum(found_text):
        return Outcome("pass", f"Ingredient list matches the approved declaration ({len(a_items)} ingredients).",
                       evidence=ev, expected=appr_text, found=found_text, confidence=f.confidence,
                       notes=["Compared ignoring spaces and punctuation, which OCR does not read reliably."])
    if raster and ocr_plausible_misread(_alnum(appr_text), _alnum(found_text), max_edits=4):
        return Outcome("unknown", "Ingredient list matches the approved declaration except for characters OCR often "
                                  "confuses — please confirm.", evidence=ev, expected=appr_text, found=found_text,
                       diff=word_diff(appr_text, found_text), confidence=f.confidence,
                       notes=["Differences look like OCR misreads (e.g. I/l, 0/O); spaces and punctuation ignored."])
    key = _alnum if raster else norm_cmp
    a_n, b_n = [key(x) for x in a_items], [key(x) for x in b_items]
    if a_n == b_n:
        return Outcome("pass", f"Ingredient list matches the approved declaration ({len(a_items)} ingredients, same "
                               f"order and percentages).", evidence=ev, expected=appr_text, found=found_text,
                       confidence=f.confidence)
    missing, extra, changed = [], [], []
    plausible = True
    sm = difflib.SequenceMatcher(None, a_n, b_n, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        if op == "delete":
            missing += a_items[i1:i2]
            continue
        if op == "insert":
            extra += b_items[j1:j2]
            continue
        if raster and "".join(a_n[i1:i2]) == "".join(b_n[j1:j2]):
            continue  # OCR merged or split items (a lost comma) — same text
        free = list(range(j1, j2))
        for i in range(i1, i2):
            best, best_s = None, 0.0
            for j in free:
                sc = difflib.SequenceMatcher(None, a_n[i], b_n[j]).ratio()
                if sc > best_s:
                    best, best_s = j, sc
            if best is not None and best_s >= 0.72:
                free.remove(best)
                changed.append((a_items[i], b_items[best]))
                if not (raster and ocr_plausible_misread(a_items[i], b_items[best])):
                    plausible = False
            else:
                missing.append(a_items[i])
        extra += [b_items[j] for j in free]
    moved = [m for m in missing if key(m) in [key(x) for x in extra]]
    missing = [m for m in missing if m not in moved]
    extra = [x for x in extra if key(x) not in [key(m) for m in moved]]
    # Second pass across the whole list: pair a missing item with its closest added item ("Dried Blueberries"
    # -> "Dried Strawberries") so the reviewer sees one change instead of a removal plus an addition.
    for m in list(missing):
        best, best_s = None, 0.0
        for x in extra:
            sc = difflib.SequenceMatcher(None, key(m), key(x)).ratio()
            if sc > best_s:
                best, best_s = x, sc
        if best is not None and best_s >= 0.7:
            changed.append((m, best))
            missing.remove(m)
            extra.remove(best)
            if not (raster and ocr_plausible_misread(m, best)):
                plausible = False
    changed.sort(key=lambda ab: 0 if re.search(r"\d", ab[0] + ab[1]) else 1)  # % changes first
    issues = []
    if changed:
        issues.append("changed: " + ", ".join(f"“{a}” → “{b}”" for a, b in changed[:4]))
    if extra:
        issues.append("not in the approved list: " + ", ".join(f"“{x}”" for x in extra[:6]))
    if missing:
        issues.append("approved but not on the artwork: " + ", ".join(f"“{m}”" for m in missing[:6]))
    if moved:
        issues.append("order changed: " + ", ".join(f"“{m}”" for m in moved[:3]))
    if missing or extra or moved:
        plausible = False
    if not issues:
        return Outcome("pass", f"Ingredient list matches the approved declaration ({len(a_items)} ingredients).",
                       evidence=ev, expected=appr_text, found=found_text, confidence=f.confidence,
                       notes=["Only spacing/item breaks differ — typical OCR noise."])
    hot = [b for _, b in changed] + extra
    ev_hot = []
    for item in hot[:8]:
        ev_hot += ctx.ev_lines(ctx.lines_with(f, item), f"differs: {item[:28]}")
    o = Outcome("fail", "Ingredient list differs from the approved declaration — " + "; ".join(issues) + ".",
                evidence=ev_hot + [e for e in ev if e.line_id not in {x.line_id for x in ev_hot}],
                expected=appr_text, found=found_text, diff=word_diff(appr_text, found_text), confidence=f.confidence)
    if raster and plausible:
        o.status = "unknown"
        o.notes.append("Every difference looks like an OCR misread (e.g. I/l, a missing letter). A person should "
                       "confirm against the artwork.")
    return o


def _cmp_nutrition(rule, ctx, occ, appr) -> Outcome:
    f = occ[0]
    tol = float(rule.params.get("tolerance", 0.02))
    raster = ctx.raster(f)
    col = rule.params.get("basis", "per_100")
    found = f.value.get(col) or {}
    basis_txt = "per serving" if col == "per_serve" else "per-100 g"
    if not found:
        return Outcome("unknown", f"The label's nutrition table has no {basis_txt} column to compare with the "
                                  f"approved values.", evidence=ctx.ev_fact(f)[:1], confidence=0.5)
    bad, missing, keys = [], [], []
    for k, av in appr.items():
        if av is None:
            continue
        fv = found.get(k)
        name, unit = NUTRIENT_LABELS.get(k, (k, ""))
        if fv is None:
            missing.append(name)
            continue
        abs_tol = 1.0 if unit in ("kcal", "mg") else 0.051
        if abs(fv - float(av)) > max(tol * abs(float(av)), abs_tol):
            bad.append(f"{name}: label {fmt_num(fv)} {unit}, approved {fmt_num(float(av))} {unit}")
            keys.append(k)
    ev = ctx.nutr_rows_ev(f, keys) if keys else ctx.ev_fact(f)[:1]
    conf = ctx.value_conf(f, col, keys) if keys else f.confidence
    if not bad and not missing:
        return Outcome("pass", f"All {len(appr)} approved {basis_txt} nutrition values match the label "
                               f"(tolerance {tol:.0%}).", evidence=ev, confidence=f.confidence)
    miss_txt = (f"could not read: {', '.join(missing)}" if raster else f"missing from the label: {', '.join(missing)}")
    parts = bad + ([miss_txt] if missing else [])
    status = "fail" if bad or (missing and not raster) else "unknown"
    o = Outcome(status, "Nutrition values differ from the approved data — " + "; ".join(parts) + "."
                if bad or not raster else f"All readable values match; {miss_txt}.", evidence=ev,
                expected=f"approved {basis_txt} values", found="; ".join(parts), confidence=conf)
    if missing and raster:
        o.notes.append("Single digits such as '0' are often skipped by OCR; check those rows by eye.")
    return o


def check_cross_consistency(rule: Rule, ctx: CheckContext) -> Outcome:
    key = rule.params["fact"]
    occ = ctx.occ(key)
    if len(occ) < 2:
        if not occ:
            return Outcome("na", f"No {FACT_LABELS.get(key, key)} found to compare.")
        return Outcome("pass", f"Only one {FACT_LABELS.get(key, key)} declaration found; nothing to cross-check.",
                       evidence=ctx.ev_fact(occ[0]), confidence=occ[0].confidence)

    def val(f):
        return (f.value["base"], f.value["base_unit"]) if isinstance(f.value, dict) and "base" in f.value else str(f.value)

    groups: dict[Any, list[Fact]] = {}
    for f in occ:
        groups.setdefault(val(f), []).append(f)
    ev = []
    for f in occ:
        shown = quantity_str(f.value) if isinstance(f.value, dict) and "amount" in f.value else str(f.value)
        ev += ctx.ev_fact(f, f"{ctx.panel_name(f.panel)}: {shown}")
    if len(groups) == 1:
        return Outcome("pass", f"All {len(occ)} declarations agree.", evidence=ev, confidence=min(f.confidence for f in occ))
    desc = "; ".join(f"{ctx.panel_name(g[0].panel)} says "
                     f"{quantity_str(g[0].value) if isinstance(g[0].value, dict) else g[0].value}"
                     for g in groups.values())
    return Outcome("fail", f"The artwork declares different values: {desc}.", evidence=ev,
                   found=desc, confidence=min(f.confidence for f in occ))


# ---------------------------------------------------------------- physical size & style

def check_min_text_height(rule: Rule, ctx: CheckContext) -> Outcome:
    p = rule.params
    key = p["fact"]
    occ = ctx.occ(key, p.get("scope", "any"))
    if not occ:
        return Outcome("na", f"No {FACT_LABELS.get(key, key)} on the checked panel to measure.")
    min_mm = p.get("min_mm")
    area_note = ""
    if min_mm is None and p.get("area_table"):
        panel = ctx.pdp if p.get("scope") == "front" else next((x for x in ctx.art.panels if x.id == occ[0].panel), None)
        area = panel.area_cm2 if panel else None
        if area is None:
            return Outcome("unknown", "The panel's physical size is unknown (no scale information in the file), so the "
                                      "minimum height cannot be determined.", evidence=ctx.ev_fact(occ[0]),
                           confidence=occ[0].confidence)
        for max_area, mm in p["area_table"]:
            if max_area is None or area <= float(max_area):
                min_mm = float(mm)
                break
        area_note = f" for a {area:.0f} cm² display panel"
    results = []
    for f in occ:
        ln = ctx.art.line(next((lid for lid in f.line_ids if re.search(r"\d", ctx.art.line(lid).text)),
                               f.line_ids[0]))
        if ln is None:
            continue
        meas = None
        if ln.source == "pdf" and ctx.pdf() is not None:
            m = measure_pdf_char_height_mm(ctx.pdf()[ln.page], ln.bbox)
            if m:
                meas = (m["mm"], m["method"], 0.95)
        else:
            page = ctx.art.pages[ln.page]
            gray, scale = ctx.gray(ln.page)
            if gray is not None and page.mm_per_unit:
                px = measure_raster_text_height_px(gray, [v * scale for v in ln.bbox])
                if px:
                    meas = (round(px / scale * page.mm_per_unit, 2), "ink height of characters in the OCR box", 0.8)
        results.append((f, ln, meas))
    measured = [r for r in results if r[2]]
    if not measured:
        return Outcome("unknown", "Character height could not be measured (no physical scale or text too faint).",
                       evidence=ctx.ev_fact(occ[0]), confidence=0.5)
    f, ln, (mm, method, conf) = min(measured, key=lambda r: r[2][0])
    ev = ctx.ev_lines([ln.id], f"{mm:.1f} mm high")
    if mm + 1e-6 >= min_mm:
        return Outcome("pass", f"Numerals are {mm:.1f} mm high; the minimum{area_note} is {min_mm:g} mm.", evidence=ev,
                       expected=f"≥ {min_mm:g} mm", found=f"{mm:.1f} mm", confidence=conf, notes=[f"Measured by {method}."])
    o = Outcome("fail", f"Numerals are only {mm:.1f} mm high; the minimum{area_note} is {min_mm:g} mm.", evidence=ev,
                expected=f"≥ {min_mm:g} mm", found=f"{mm:.1f} mm", confidence=conf, notes=[f"Measured by {method}."])
    if ln.source != "pdf" and mm > 0.88 * min_mm:
        o.status = "unknown"
        o.notes.append("Close to the limit on a raster image — measure on the print-ready file.")
    return o


def check_text_style(rule: Rule, ctx: CheckContext) -> Outcome:
    p = rule.params
    key = p["fact"]
    occ = ctx.occ(key)
    if not occ:
        return Outcome("na", f"No {FACT_LABELS.get(key, key)} found, so its type style was not checked.")
    f = occ[0]
    lines = [ctx.art.line(i) for i in f.line_ids]
    if any(l is not None and l.source != "pdf" for l in lines):
        return Outcome("unknown", "Type weight cannot be verified reliably from a raster image; please check that the "
                                  "statement is bold.", evidence=ctx.ev_fact(f), confidence=0.5)
    if p.get("bold") and not all(l.bold for l in lines if l):
        fonts = ", ".join(sorted({l.font or "?" for l in lines if l}))
        return Outcome("fail", f"The {FACT_LABELS.get(key, key)} is not set in bold (font: {fonts}).",
                       evidence=ctx.ev_fact(f), expected="bold", found=f"regular ({fonts})")
    fonts = ", ".join(sorted({l.font or "?" for l in lines if l}))
    return Outcome("pass", f"The {FACT_LABELS.get(key, key)} is set in bold ({fonts}).", evidence=ctx.ev_fact(f))


# ---------------------------------------------------------------- symbols & codes

def check_symbol_present(rule: Rule, ctx: CheckContext) -> Outcome:
    p = rule.params
    kind = p.get("kind", "auto")
    if kind == "auto":
        veg = ctx.approved.get("veg")
        kind = None if veg is None else ("veg_mark" if veg in (True, "true", "yes", "veg", 1) else "nonveg_mark")
    scope = p.get("scope", "any")
    marks = ctx.occ("food_mark", scope)
    names = {"veg_mark": "green veg symbol", "nonveg_mark": "brown non-veg symbol"}
    if kind is None:
        if marks:
            m = marks[0]
            return Outcome("pass", f"Found the {names.get(m.value, m.value)} (no approved veg/non-veg status supplied).",
                           evidence=ctx.ev_fact(m), confidence=m.confidence)
        return _absent(ctx, "food_mark", scope)
    right = [m for m in marks if m.value == kind]
    wrong = [m for m in marks if m.value != kind]
    if right:
        m = right[0]
        return Outcome("pass", f"Found the {names[kind]} on the {ctx.panel_name(m.panel)}.", evidence=ctx.ev_fact(m),
                       expected=names[kind], found=names[kind], confidence=m.confidence)
    if wrong:
        m = wrong[0]
        return Outcome("fail", f"The artwork shows the {names[m.value]}, but the approved data says the product is "
                               f"{'vegetarian' if kind == 'veg_mark' else 'non-vegetarian'}.",
                       evidence=ctx.ev_fact(m, f"{names[m.value]} (wrong)"), expected=names[kind],
                       found=names[m.value], confidence=m.confidence)
    elsewhere = ctx.occ("food_mark", "any")
    if elsewhere and scope == "front":
        return _absent(ctx, "food_mark", scope)
    o = Outcome("fail", f"No {names[kind]} was found on the {'front panel' if scope == 'front' else 'artwork'}.",
                evidence=ctx.ev_panel(), expected=names[kind], found="not found", absence=True,
                absence_fact="food_mark", absence_label=names[kind], confidence=0.85)
    return o


def _barcode_printed_digits(ctx: CheckContext, b: Fact) -> Optional[tuple[str, list[str]]]:
    x0, y0, x1, y1 = b.attrs["bbox"]
    h = y1 - y0
    near = [l for l in ctx.lines if l.page == b.attrs.get("page", 0) and l.bbox[1] >= y0 + 0.3 * h
            and l.bbox[1] <= y1 + 0.9 * h and l.bbox[0] >= x0 - 0.25 * (x1 - x0) and l.bbox[2] <= x1 + 0.15 * (x1 - x0)
            and re.fullmatch(r"[\d\s]+", l.text)]
    if not near:
        return None
    near.sort(key=lambda l: l.bbox[0])
    return "".join(re.sub(r"\D", "", l.text) for l in near), [l.id for l in near]


def check_barcode_valid(rule: Rule, ctx: CheckContext) -> Outcome:
    codes = ctx.occ("gtin")
    if not codes:
        return Outcome("fail", "No retail barcode (EAN/UPC) could be decoded from the artwork.", absence=True,
                       absence_fact="gtin", absence_label="retail barcode", confidence=0.85,
                       notes=["The decoder tried the rendered artwork at 300 and 600 dpi."]
                       if ctx.art.kind == "pdf" else [])
    b = codes[0]
    shown = f"UPC-A {b.value[1:]}" if b.attrs.get("format", "").upper().startswith("EAN13") and \
        b.value.startswith("0") else b.value
    ev = ctx.ev_fact(b, f"{b.attrs.get('format')}: {b.value}")
    if not gtin_valid(b.value):
        return Outcome("fail", f"Barcode {b.value} has an invalid GS1 check digit.", evidence=ev, found=b.value)
    printed = _barcode_printed_digits(ctx, b)
    notes = []
    if printed:
        digits, ids = printed
        if digits and digits != b.value and not (len(digits) == len(b.value) - 1 and b.value.endswith(digits)):
            return Outcome("fail", f"The digits printed under the barcode ({digits}) differ from the encoded value "
                                   f"({b.value}).", evidence=ev + ctx.ev_lines(ids, "printed digits"),
                           expected=b.value, found=digits, confidence=min(ctx.art.line(i).confidence for i in ids))
        notes.append("Printed digits under the barcode match the encoded value.")
    appr = ctx.approved.get("gtin")
    if appr:
        a = re.sub(r"\D", "", str(appr))
        if a.lstrip("0") != b.value.lstrip("0"):
            return Outcome("fail", f"The barcode encodes {b.value}, but the GTIN approved for this SKU is {a}.",
                           evidence=ev, expected=a, found=b.value, notes=notes)
        return Outcome("pass", f"Barcode scans as {shown}: valid check digit and matches the approved code.",
                       evidence=ev, expected=a, found=b.value, notes=notes, confidence=b.confidence)
    return Outcome("pass", f"Barcode scans as {shown} with a valid check digit (no approved code to compare).",
                   evidence=ev, found=b.value, notes=notes, confidence=b.confidence)


def check_qr_present(rule: Rule, ctx: CheckContext) -> Outcome:
    q = ctx.occ("qr_code")
    if q:
        return Outcome("pass", f"QR code found ({q[0].value[:60]}).", evidence=ctx.ev_fact(q[0]))
    return Outcome("fail", "No QR code was found on the artwork.", absence=True, absence_fact="qr_code",
                   absence_label="QR code", confidence=0.85)


# ---------------------------------------------------------------- allergens

def check_allergen_coverage(rule: Rule, ctx: CheckContext) -> Outcome:
    table = rule.params.get("allergens") or DEFAULT_ALLERGENS
    if isinstance(table, str):
        table = ALLERGEN_TABLES.get(table.lower(), DEFAULT_ALLERGENS)
    ing = ctx.occ("ingredients")
    if not ing:
        return Outcome("unknown", "No ingredient list was found, so allergen declaration could not be verified.",
                       confidence=0.5)
    f_ing = ing[0]
    present = find_allergens(f_ing.value["text"], table)
    stmts = ctx.occ("allergen_statement")
    declared: dict[str, list[str]] = {}
    merged_word_hits: list[str] = []
    for s in stmts:
        strict = find_allergens(s.value["text"], table)
        for cat, kws in strict.items():
            declared.setdefault(cat, []).extend(kws)
        if ctx.raster(s):
            # OCR drops spaces ("Milkand Soy"): accept a keyword at the start of a merged word, and say so.
            for cat, kws in find_allergens(s.value["text"], table, loose=True).items():
                if cat not in strict:
                    declared.setdefault(cat, []).extend(kws)
                    merged_word_hits.append(ALLERGEN_NAMES.get(cat, cat))
    if not present:
        if stmts:
            return Outcome("pass", "No allergenic ingredients detected; an allergen statement is present.",
                           evidence=ctx.ev_fact(stmts[0]))
        return Outcome("pass", "No allergenic ingredients detected in the ingredient list.", evidence=ctx.ev_fact(f_ing))

    def ing_lines(cat: str) -> list[str]:
        ids = []
        for lid in f_ing.line_ids:
            ln = ctx.art.line(lid)
            if ln and find_allergens(ln.text, {cat: table[cat]}):
                ids.append(lid)
        return ids or f_ing.line_ids[:1]

    present = {c: _dedupe_plural(k) for c, k in present.items()}
    pres_desc = ", ".join(f"{ALLERGEN_NAMES.get(c, c)} ({', '.join(k)})" for c, k in present.items())
    if not stmts:
        ev = []
        for c in present:
            ev += ctx.ev_lines(ing_lines(c), f"contains {ALLERGEN_NAMES.get(c, c)}")
        o = Outcome("fail", f"The ingredients contain {pres_desc}, but there is no “Contains:” allergen declaration.",
                    evidence=ev, expected="Contains: " + ", ".join(
                        f"{ALLERGEN_NAMES.get(c, c).title()} ({', '.join(k.title() for k in kws if k != 'nuts')})"
                        if c == "tree_nuts" else ALLERGEN_NAMES.get(c, c).title() for c, kws in present.items()),
                    found="no allergen declaration", absence=True, absence_fact="allergen_statement",
                    absence_label="allergen declaration", confidence=f_ing.confidence)
        prec = ctx.occ("precautionary_statement")
        if prec:
            o.evidence += ctx.ev_fact(prec[0], "only a ‘may contain’ statement")
            o.notes.append("The pack has an advisory statement (“" + prec[0].value["text"][:80] +
                           "…”), but advisory ‘may contain’ wording is not a declaration of ingredients.")
        return o
    missing = [c for c in present if c not in declared]
    s0 = stmts[0]
    conf = min(f_ing.confidence, s0.confidence)
    if missing:
        ev = ctx.ev_fact(s0, "allergen declaration")
        for c in missing:
            ev += ctx.ev_lines(ing_lines(c), f"undeclared: {ALLERGEN_NAMES.get(c, c)}")
        def item_for(c):
            for it in f_ing.value.get("items", []):
                if find_allergens(it, {c: table[c]}):
                    return it
            return ", ".join(present[c])
        miss_desc = ", ".join(f"{ALLERGEN_NAMES.get(c, c)} (in “{item_for(c)}”)" for c in missing)
        return Outcome("fail", f"The allergen declaration omits {miss_desc}.", evidence=ev,
                       expected="declares " + ", ".join(ALLERGEN_NAMES.get(c, c) for c in present),
                       found=s0.raw, confidence=conf)
    extra = [c for c in declared if c not in present]
    o = Outcome("pass", f"All allergens found in the ingredients are declared: "
                        f"{', '.join(ALLERGEN_NAMES.get(c, c) for c in present)}.",
                evidence=ctx.ev_fact(s0, "allergen declaration"), found=s0.raw, confidence=conf)
    if merged_word_hits:
        o.notes.append("OCR joined words in the declaration (e.g. “Milkand”); " + ", ".join(merged_word_hits) +
                       " was read at the start of a merged word and counted as declared.")
    if extra:
        o.notes.append("Declared but not detected in the ingredients (possibly derived ingredients): " +
                       ", ".join(ALLERGEN_NAMES.get(c, c) for c in extra) + ".")
    return o


# ---------------------------------------------------------------- nutrition

def check_nutrition_math(rule: Rule, ctx: CheckContext) -> Outcome:
    n = ctx.nutrition()
    if n is None:
        return Outcome("unknown", "No nutrition table could be read, so the arithmetic could not be checked.",
                       absence=True, absence_fact="nutrition", absence_label="nutrition table", confidence=0.5)
    kind = rule.params.get("kind", "energy")
    v100, vserve, vrda = n.value.get("per_100", {}), n.value.get("per_serve", {}), n.value.get("rda", {})
    if kind == "energy":
        col, vals = ctx.nutrition_basis(n, rule.params.get("basis", "per_100"))
        where = "per serving" if col == "per_serve" else "/100 g"
        need = ["energy_kcal", "protein_g", "carbohydrate_g", "total_fat_g"]
        if any(k not in vals for k in need):
            miss = ", ".join(NUTRIENT_LABELS[k][0] for k in need if k not in vals)
            return Outcome("unknown", f"Cannot check energy: {miss} not read from the table.",
                           evidence=ctx.ev_fact(n)[:1], confidence=0.5)
        tol = float(rule.params.get("tolerance", 0.10))
        calc = 4 * vals["protein_g"] + 4 * vals["carbohydrate_g"] + 9 * vals["total_fat_g"]
        if "dietary_fibre_g" in vals and rule.params.get("include_fibre", True) and col == "per_100":
            calc += 2 * vals["dietary_fibre_g"]
        decl = vals["energy_kcal"]
        dev = abs(decl - calc) / max(calc, 1.0)
        ev = ctx.nutr_rows_ev(n, need)
        conf = ctx.value_conf(n, col, need)
        msg = (f"Declared energy {decl:.0f} kcal {where}; protein {fmt_num(vals['protein_g'])} g × 4 + carbohydrate "
               f"{fmt_num(vals['carbohydrate_g'])} g × 4 + fat {fmt_num(vals['total_fat_g'])} g × 9 = {calc:.0f} kcal "
               f"({dev:.0%} {'difference' if dev else 'match'}; tolerance {tol:.0%}).")
        return Outcome("pass" if dev <= tol else "fail", msg, evidence=ev, expected=f"≈ {calc:.0f} kcal",
                       found=f"{decl:.0f} kcal", confidence=conf)
    if kind == "subtotals":
        col, vals = ctx.nutrition_basis(n)
        probs, keys = [], []
        pairs = [("total_sugars_g", ["carbohydrate_g"], "total sugars exceed carbohydrate"),
                 ("added_sugars_g", ["total_sugars_g"], "added sugars exceed total sugars"),
                 ("dietary_fibre_g", ["carbohydrate_g"], "dietary fibre exceeds carbohydrate"),
                 (("saturated_fat_g", "trans_fat_g"), ["total_fat_g"], "saturated + trans fat exceed total fat")]
        for sub, tot, label in pairs:
            subs = sub if isinstance(sub, tuple) else (sub,)
            if any(s in vals for s in subs) and tot[0] in vals:
                sv = sum(vals.get(s, 0.0) for s in subs)
                if sv > vals[tot[0]] + 0.051:
                    probs.append(f"{label} ({fmt_num(sv)} g > {fmt_num(vals[tot[0]])} g)")
                    keys += list(subs) + tot
        checked = sum(1 for s, t, _ in pairs if t[0] in vals)
        if not checked:
            return Outcome("unknown", "Not enough nutrients were read to check sub-totals.", confidence=0.5)
        if probs:
            return Outcome("fail", "Impossible nutrition values: " + "; ".join(probs) + ".",
                           evidence=ctx.nutr_rows_ev(n, list(dict.fromkeys(keys))), found="; ".join(probs),
                           confidence=ctx.value_conf(n, col, keys))
        return Outcome("pass", "Sugars, added sugars, fibre, saturated and trans fat are all within their totals.",
                       evidence=ctx.ev_fact(n)[:1], confidence=n.confidence)
    if kind == "dv":
        return _check_dv(rule, ctx, n)
    if kind == "plausibility":
        return _check_plausibility(rule, ctx, n)
    if kind == "serving_size":
        raw = n.value.get("serving_size_raw")
        ids = n.attrs.get("extra_rows", {}).get("serving_size", [])
        ev = ctx.ev_lines(ids, "serving size")
        if raw is None:
            return Outcome("fail", "The Nutrition Facts panel has no 'Serving size' line.", evidence=ctx.ev_fact(n)[:1],
                           expected="Serving size in a household measure and grams, e.g. “2/3 cup (55g)”",
                           found="not found", confidence=n.confidence)
        missing = []
        if not n.value.get("serving_size_unit"):
            missing.append("a metric amount with its unit (g or mL)")
        if not n.value.get("household"):
            missing.append("a common household measure (cup, tbsp, pieces…)")
        if missing:
            return Outcome("fail", f"Serving size is printed as “{raw}” — it lacks {' and '.join(missing)}.",
                           evidence=ev, expected="e.g. “2/3 cup (60g)”", found=raw or "—",
                           confidence=min(ctx.art.line(i).confidence for i in ids) if ids else n.confidence)
        return Outcome("pass", f"Serving size “{raw}” has a household measure and a metric amount.", evidence=ev,
                       found=raw, confidence=n.confidence)
    if kind == "servings_net":
        spc = n.value.get("servings_per_container")
        ss, unit = n.value.get("serving_size"), n.value.get("serving_size_unit")
        q = next((f.value for f in ctx.occ("net_quantity")), None)
        if q is None and ctx.approved.get("net_quantity"):
            q = parse_quantity(str(ctx.approved["net_quantity"]))
        ids = n.attrs.get("extra_rows", {}).get("servings", []) + n.attrs.get("extra_rows", {}).get("serving_size", [])
        ev = ctx.ev_lines(ids, "servings / serving size")
        if not spc or not q:
            return Outcome("unknown", "Servings per container or net quantity not found — cannot cross-check them.",
                           evidence=ev, confidence=0.5)
        raw = n.value.get("serving_size_raw") or ""
        assumed = False
        if ss is None:
            ss = to_float(raw)
            assumed = ss is not None
        if ss is None:
            return Outcome("unknown", f"Serving size “{raw}” has no readable amount — cannot check "
                                      f"{fmt_num(spc)} servings against the {quantity_str(q)} pack.", evidence=ev,
                           confidence=0.5)
        total = spc * ss
        expect = q["base"] / spc
        tol = float(rule.params.get("tolerance", 0.1))
        ok = abs(total - q["base"]) <= tol * q["base"]
        msg = (f"{fmt_num(spc)} servings × {fmt_num(ss)} {unit or 'g'} = {fmt_num(total)} {q['base_unit']}, but the pack "
               f"holds {quantity_str(q)} (≈ {fmt_num(round(expect, 1))} {q['base_unit']} per serving expected).")
        if ok:
            return Outcome("pass", f"{fmt_num(spc)} servings × {fmt_num(ss)} {unit or 'g'} ≈ {quantity_str(q)} net.",
                           evidence=ev, confidence=n.confidence)
        o = Outcome("fail", msg, evidence=ev, expected=f"≈ {fmt_num(round(expect, 1))} {q['base_unit']} per serving",
                    found=f"{raw}", confidence=n.confidence)
        if assumed:
            o.status = "unknown"
            o.notes.append(f"No unit is printed after “{raw}”; grams were assumed for this cross-check.")
        return o
    if kind == "per_serve":
        ss = n.value.get("serving_size")
        if not ss:
            return Outcome("unknown", "Serving size not found, so per-serve values could not be checked.",
                           confidence=0.5)
        tol = float(rule.params.get("tolerance", 0.05))
        probs, keys = [], []
        for k, a in v100.items():
            if k not in vserve:
                continue
            exp = a * ss / 100.0
            unit = NUTRIENT_LABELS.get(k, (k, "g"))[1]
            abs_tol = 2.0 if unit in ("kcal", "mg") else 0.15
            if abs(vserve[k] - exp) > max(tol * exp, abs_tol):
                probs.append(f"{NUTRIENT_LABELS.get(k, (k,))[0]}: {fmt_num(vserve[k])} {unit} per serve, expected "
                             f"≈ {fmt_num(round(exp, 2))} {unit} ({fmt_num(a)} × {fmt_num(ss)}/100)")
                keys.append(k)
        if not vserve:
            return Outcome("unknown", "No per-serve column was read.", confidence=0.5)
        if probs:
            return Outcome("fail", "Per-serve values inconsistent with per-100 g values: " + "; ".join(probs) + ".",
                           evidence=ctx.nutr_rows_ev(n, keys), found="; ".join(probs),
                           confidence=min(ctx.value_conf(n, "per_serve", keys), ctx.value_conf(n, "per_100", keys)))
        return Outcome("pass", f"All {len([k for k in v100 if k in vserve])} per-serve values equal per-100 g × "
                               f"{fmt_num(ss)} g / 100 (within rounding).", evidence=ctx.ev_fact(n)[:1],
                       confidence=n.confidence)
    if kind == "rda":
        ref = rule.params.get("reference") or {}
        tolp = float(rule.params.get("tolerance_points", 1.5))
        probs, keys = [], []
        for k, pct in vrda.items():
            if k not in ref or k not in vserve:
                continue
            exp = vserve[k] / float(ref[k]) * 100
            if abs(pct - exp) > max(tolp, 0.12 * exp):
                probs.append(f"{NUTRIENT_LABELS.get(k, (k,))[0]}: {pct:.0f}% declared, ≈ {exp:.1f}% expected")
                keys.append(k)
        if not vrda:
            return Outcome("unknown", "No %RDA column was read.", confidence=0.5)
        if probs:
            return Outcome("fail", "%RDA values do not match the reference values: " + "; ".join(probs) + ".",
                           evidence=ctx.nutr_rows_ev(n, keys), found="; ".join(probs),
                           confidence=ctx.value_conf(n, "rda", keys))
        return Outcome("pass", "All %RDA values are consistent with the per-serve values and reference intakes.",
                       evidence=ctx.ev_fact(n)[:1], confidence=n.confidence)
    return Outcome("unknown", f"Unknown nutrition check '{kind}'.")


ANIMAL_FAT = [r"\bmilk", r"butter(?!nut)", r"\bcream", r"cheese", r"whey", r"\beggs?\b", r"ghee", r"\blard\b",
              r"tallow", r"\bbeef\b", r"\bpork\b", r"chicken", r"\bfish\b", r"\bmeat\b", r"yog(h)?urt"]
FORTIFICATION = {"vitamin_d_mcg": [r"vitamin\s*d", r"cholecalciferol", r"ergocalciferol"],
                 "calcium_mg": [r"calcium"], "iron_mg": [r"\biron\b", r"ferrous", r"ferric"],
                 "potassium_mg": [r"potassium"]}


def _check_plausibility(rule: Rule, ctx: CheckContext, n: Fact) -> Outcome:
    """Values that cannot be true (macros heavier than the serving) or that the ingredients do not explain."""
    vs = n.value.get("per_serve") or {}
    if not vs:
        return Outcome("na", "No per-serving values to sanity-check.")
    impossible, unusual, keys = [], [], []
    weight, how = None, ""
    if n.value.get("serving_size") and n.value.get("serving_size_unit") == "g":
        weight, how = n.value["serving_size"], f"the declared serving of {fmt_num(n.value['serving_size'])} g"
    else:
        spc = n.value.get("servings_per_container")
        q = next((f.value for f in ctx.occ("net_quantity")), None)
        if spc and q and q.get("base_unit") == "g":
            weight = q["base"] / spc
            how = f"{quantity_str(q)} ÷ {fmt_num(spc)} servings = {fmt_num(round(weight, 1))} g per serving"
    macros = sum(vs.get(k, 0.0) for k in ("protein_g", "carbohydrate_g", "total_fat_g"))
    if weight and macros > weight * 1.02:
        impossible.append(f"protein + carbohydrate + fat = {fmt_num(macros)} g per serving, more than the serving "
                          f"itself weighs ({how})")
        keys += ["protein_g", "carbohydrate_g", "total_fat_g"]
    if vs.get("trans_fat_g", 0) > 0.5 and not ctx.ingredient_hits([r"hydrogenated", r"shortening"]):
        unusual.append(f"trans fat {fmt_num(vs['trans_fat_g'])} g per serving, but no partially hydrogenated oil is "
                       f"listed (such oils are no longer permitted in US foods)")
        keys.append("trans_fat_g")
    if vs.get("cholesterol_mg", 0) > 2 and not ctx.ingredient_hits(ANIMAL_FAT):
        unusual.append(f"cholesterol {fmt_num(vs['cholesterol_mg'])} mg, but no ingredient of animal origin that "
                       f"contains cholesterol is listed")
        keys.append("cholesterol_mg")
    for k, pats in FORTIFICATION.items():
        if k in vs and FDA_DV.get(k) and vs[k] / FDA_DV[k] > 1.0 and not ctx.ingredient_hits(pats):
            unusual.append(f"{NUTRIENT_LABELS[k][0].lower()} {fmt_num(vs[k])} {NUTRIENT_LABELS[k][1]} = "
                           f"{vs[k] / FDA_DV[k]:.0%} DV, without any added {NUTRIENT_LABELS[k][0].lower()} in the "
                           f"ingredients")
            keys.append(k)
    ev = ctx.nutr_rows_ev(n, list(dict.fromkeys(keys)))
    if impossible:
        msg = "Physically impossible: " + "; ".join(impossible) + "."
        if unusual:
            msg += " Also implausible: " + "; ".join(unusual) + "."
        return Outcome("fail", msg, evidence=ev, found="; ".join(impossible + unusual),
                       confidence=ctx.value_conf(n, "per_serve", keys))
    if unusual:
        return Outcome("unknown", "Implausible for this ingredient list: " + "; ".join(unusual) + ".", evidence=ev,
                       found="; ".join(unusual), confidence=ctx.value_conf(n, "per_serve", keys),
                       notes=["Not impossible, so a person should confirm the values with the nutrition lab report."])
    return Outcome("pass", "Per-serving values are physically plausible and consistent with the ingredients.",
                   evidence=ctx.ev_fact(n)[:1], confidence=n.confidence)


def _check_dv(rule: Rule, ctx: CheckContext, n: Fact) -> Outcome:
    """%Daily Value = amount per serving / Daily Value, declared as a whole number (21 CFR 101.9(d)(7))."""
    ref = {**FDA_DV, **(rule.params.get("reference") or {})}
    dv, vs = n.value.get("dv") or {}, n.value.get("per_serve") or {}
    if not dv:
        return Outcome("unknown", "No %Daily Value column was read.", evidence=ctx.ev_fact(n)[:1], confidence=0.5)
    tolp = float(rule.params.get("tolerance_points", 1.5))
    wrong, frac, keys, ok = [], [], [], 0
    for k, pct in dv.items():
        if pct != int(pct):
            frac.append(f"{NUTRIENT_LABELS.get(k, (k,))[0]} {fmt_num(pct)}%")
            keys.append(k)
        if k not in ref or k not in vs:
            continue
        exp = vs[k] / float(ref[k]) * 100
        unit = NUTRIENT_LABELS.get(k, (k, "g"))[1]
        if abs(pct - exp) > max(tolp, 0.1 * exp):
            wrong.append(f"{NUTRIENT_LABELS.get(k, (k,))[0]} {fmt_num(vs[k])} {unit} → {exp:.0f}% DV, label says "
                         f"{fmt_num(pct)}%")
            keys.append(k)
        else:
            ok += 1
    if not wrong and not frac:
        return Outcome("pass", f"All {ok} %DV values match the amounts per serving and FDA Daily Values.",
                       evidence=ctx.ev_fact(n)[:1], confidence=n.confidence)
    parts = []
    if wrong:
        parts.append(f"{len(wrong)} wrong: " + "; ".join(wrong[:6]) + ("…" if len(wrong) > 6 else ""))
    if frac:
        parts.append("not whole numbers: " + ", ".join(frac))
    return Outcome("fail", "%Daily Values are incorrect — " + " | ".join(parts) + ".",
                   evidence=ctx.nutr_rows_ev(n, list(dict.fromkeys(keys))), found="; ".join(parts),
                   expected="%DV = amount ÷ Daily Value, rounded to a whole percent",
                   confidence=ctx.value_conf(n, "dv", keys))


def _claim_value(spec: dict, v100: dict) -> Optional[float]:
    if spec.get("derived") == "protein_energy_pct":
        p = v100.get("protein_g")
        e = v100.get("energy_kcal")
        if p is None:
            return None
        if not e:
            if all(k in v100 for k in ("carbohydrate_g", "total_fat_g")):
                e = 4 * p + 4 * v100["carbohydrate_g"] + 9 * v100["total_fat_g"]
            else:
                return None
        return 4 * p / e * 100
    return v100.get(spec.get("nutrient"))


_OPS: dict[str, Callable[[float, float], bool]] = {"<=": lambda a, b: a <= b + 1e-9, "<": lambda a, b: a < b,
                                                    ">=": lambda a, b: a >= b - 1e-9, ">": lambda a, b: a > b,
                                                    "==": lambda a, b: abs(a - b) < 1e-9}


def check_claim_threshold(rule: Rule, ctx: CheckContext) -> Outcome:
    claims = rule.params.get("claims") or DEFAULT_CLAIMS
    texts = ctx.claim_texts()
    found = []
    for spec in claims:
        for t, ls in texts:
            if any(re.search(p, t, re.I) for p in spec["patterns"]):
                found.append((spec, ls))
                break
    if not found:
        return Outcome("na", "No regulated nutrition claims were found on the artwork.")
    n = ctx.nutrition()
    col, v100 = ctx.nutrition_basis(n, rule.params.get("basis", "per_100")) if n else ("per_100", {})
    fails, passes, unknown, ev = [], [], [], []
    conf = 1.0
    for spec, ls in found:
        val = _claim_value(spec, v100)
        unit = spec.get("unit", "")
        ids = [l.id for l in ls]
        if val is None:
            unknown.append(spec["name"])
            ev += ctx.ev_lines(ids, f"claim: {spec['name']}")
            continue
        ok = _OPS[spec["op"]](val, float(spec["value"]))
        basis = "energy from protein" if spec.get("derived") else NUTRIENT_LABELS.get(spec.get("nutrient"), ("",))[0].lower()
        sym = {"<=": "≤", ">=": "≥", "<": "<", ">": ">", "==": "="}.get(spec["op"], spec["op"])
        per = "" if spec.get("derived") else (" per serving" if col == "per_serve" else " per 100 g")
        desc = (f"“{spec['name']}” needs {basis} {sym} {fmt_num(float(spec['value']))} {unit}{per}; "
                f"label has {fmt_num(round(val, 1))} {unit}")
        ev += ctx.ev_lines(ids, f"claim: {spec['name']}")
        conf = min([conf] + [l.confidence for l in ls])
        if n is not None:
            nk = ["protein_g", "energy_kcal"] if spec.get("derived") else [spec.get("nutrient")]
            ev += ctx.nutr_rows_ev(n, nk)
            conf = min(conf, ctx.value_conf(n, col, nk))
        (passes if ok else fails).append(desc)
    if fails:
        return Outcome("fail", "Unsupported claim: " + "; ".join(fails) + "." +
                       (f" Supported: {'; '.join(passes)}." if passes else ""), evidence=ev,
                       found="; ".join(fails), confidence=conf)
    if unknown:
        return Outcome("unknown", f"Claims found ({', '.join(unknown)}) but the nutrition values needed to verify them "
                                  f"were not read.", evidence=ev, confidence=0.5)
    return Outcome("pass", "All nutrition claims are supported: " + "; ".join(passes) + ".", evidence=ev,
                   confidence=conf)


def check_fop_warning(rule: Rule, ctx: CheckContext) -> Outcome:
    n = ctx.nutrition()
    if n is None:
        return Outcome("unknown", "No nutrition table was read; front-of-pack warning need could not be assessed.",
                       confidence=0.5)
    basis = rule.params.get("basis", "per_100")
    col = "per_serve" if basis == "dv" else "per_100"
    vals = n.value.get(col, {})
    front = [l for l in ctx.lines if not ctx.pdp or l.panel == ctx.pdp.id]
    needed, present, keys = [], [], []
    for spec in rule.params.get("nutrients", []):
        v = vals.get(spec["nutrient"])
        if v is None:
            continue
        name = NUTRIENT_LABELS.get(spec["nutrient"], (spec["nutrient"], ""))
        if basis == "dv":
            ref = FDA_DV.get(spec["nutrient"])
            if not ref:
                continue
            pct = v / ref * 100
            if pct < float(spec["threshold"]):
                continue
            label = f"{spec['warning']} ({name[0].lower()} {fmt_num(v)} {name[1]} = {pct:.0f}% DV per serving ≥ " \
                    f"{fmt_num(float(spec['threshold']))}%)"
        else:
            if v <= float(spec["threshold"]):
                continue
            label = (f"{spec['warning']} ({name[0].lower()} {fmt_num(v)} {spec.get('unit', '')} > "
                     f"{fmt_num(float(spec['threshold']))})")
        keys.append(spec["nutrient"])
        has = [l for l in front if any(re.search(p, l.text, re.I) for p in spec.get("patterns", []))]
        (present if has else needed).append(label)
    if not needed and not present:
        return Outcome("pass", "No nutrient reaches the thresholds; no front-of-pack warning would be needed.",
                       evidence=ctx.ev_fact(n)[:1], confidence=n.confidence)
    if needed:
        return Outcome("fail", f"This SKU would need {rule.params.get('what', 'front-of-pack warning(s)')}: " +
                       "; ".join(needed) + ".",
                       evidence=ctx.nutr_rows_ev(n, keys) + ctx.ev_panel("front panel (where it would go)"),
                       found="; ".join(needed), confidence=ctx.value_conf(n, col, keys))
    return Outcome("pass", "Required warnings already present: " + "; ".join(present) + ".", confidence=n.confidence)


# ---------------------------------------------------------------- identity, quantity format, copy

def _tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", norm_cmp(s)) if t]


def check_identity_on_panel(rule: Rule, ctx: CheckContext) -> Outcome:
    """Every word of the approved name / statement of identity appears on the principal display panel."""
    p = rule.params
    field_names = p.get("approved_field", ["statement_of_identity", "product_name"])
    field_names = field_names if isinstance(field_names, list) else [field_names]
    name = next((ctx.approved.get(f) for f in field_names if ctx.approved.get(f)), None)
    if not name:
        return Outcome("na", "No approved product name / statement of identity was supplied.")
    lines = [l for l in ctx.lines if p.get("scope", "front") != "front" or not ctx.pdp or l.panel == ctx.pdp.id]
    words = _tokens(str(name))
    found_ids, missing = [], []
    from rapidfuzz import fuzz
    for w in words:
        hit = None
        for l in lines:
            toks = _tokens(l.text)
            if w in toks or (l.source == "ocr" and any(fuzz.ratio(w, t) >= 85 for t in toks)):
                hit = l
                break
        if hit:
            found_ids.append(hit.id)
        else:
            missing.append(w)
    where = "front panel" if ctx.pdp and len(ctx.art.panels) > 1 else "artwork"
    ev = ctx.ev_lines(list(dict.fromkeys(found_ids)), "statement of identity")
    if not missing:
        return Outcome("pass", f"“{name}” appears on the {where}: " +
                       " + ".join(f"“{ctx.art.line(i).text}”" for i in dict.fromkeys(found_ids)) + ".",
                       evidence=ev, expected=str(name), found=" ".join(ctx.art.line(i).text for i in
                                                                          dict.fromkeys(found_ids)),
                       confidence=min([ctx.art.line(i).confidence for i in found_ids] or [1.0]))
    o = Outcome("fail", f"The {where} does not show the full name “{name}”: missing "
                        f"{', '.join('“' + m + '”' for m in missing)}.", evidence=ev or ctx.ev_panel(),
                expected=str(name), found=", ".join(ctx.art.line(i).text for i in dict.fromkeys(found_ids)) or
                "not found", confidence=0.95)
    if ctx.art.quality == "raster":
        o.status = "unknown"
    return o


def check_net_quantity_format(rule: Rule, ctx: CheckContext) -> Outcome:
    """US style net quantity: 'Net Wt' term, US customary unit AND metric unit, both values agreeing."""
    p = rule.params
    scope = p.get("scope", "any")
    occ = ctx.occ("net_quantity", scope)
    if not occ:
        occ = ctx.occ("net_quantity")
        if not occ:
            return Outcome("na", "No net quantity declaration found (see the presence rule).")
    f = occ[0]
    q = f.value
    parts = q.get("parts") or [q]
    systems = {x.get("system") for x in parts}
    problems = []
    if p.get("require_label", True) and not f.attrs.get("labelled"):
        problems.append("no “Net Wt” / “Net Contents” term")
    if p.get("require_us", True) and "us" not in systems:
        problems.append("no US customary unit (oz / lb / fl oz)")
    if p.get("require_metric", True) and "metric" not in systems:
        problems.append("no metric unit (g / kg / mL / L)")
    us = [x for x in parts if x.get("system") == "us"]
    met = [x for x in parts if x.get("system") == "metric"]
    if us and met:
        a, b = sum(x["base"] for x in us), met[0]["base"]
        if abs(a - b) > float(p.get("tolerance", 0.02)) * b:
            problems.append(f"the units disagree ({quantity_str({'parts': us})} = {a:.0f} {met[0]['base_unit']}, "
                            f"not {quantity_str({'parts': met})})")
    grams = met[0]["base"] if met else (us[0]["base"] if us else q["base"])
    unit = (met[0] if met else q)["base_unit"]
    if unit == "g":
        oz = grams / 28.349523125
        suggest = f"NET WT {oz:.1f} OZ ({fmt_num(grams)} g)" if oz < 16 else \
            f"NET WT {oz:.1f} OZ ({int(oz // 16)} LB {oz % 16:.1f} OZ) {fmt_num(grams)} g"
    else:
        suggest = f"{grams / 29.5735:.1f} FL OZ ({fmt_num(grams)} mL)"
    ev = ctx.ev_fact(f, "net quantity")
    read_conf = min([ctx.art.line(i).confidence for i in f.line_ids if ctx.art.line(i)] or [f.confidence])
    if problems:
        return Outcome("fail", f"Net quantity “{f.raw}” has " + "; ".join(problems) + ".", evidence=ev,
                       expected=suggest, found=f.raw, confidence=read_conf)
    return Outcome("pass", f"Net quantity “{f.raw}” uses the required term and units.", evidence=ev, found=f.raw,
                   confidence=f.confidence)


LABEL_PREFIX = re.compile(r"^\s*[*•]?\s*[A-Z][A-Z /&()-]{2,40}:\s*")


def _sentences(text: str) -> list[str]:
    text = norm_space(text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9*])", text)
    return [s.strip() for s in parts if len(re.sub(r"\W", "", s)) > 3]


def _sent_key(s: str) -> str:
    return re.sub(r"^[^a-z0-9]+|[^a-z0-9%)]+$", "", norm_cmp(LABEL_PREFIX.sub("", s)))


def check_approved_copy(rule: Rule, ctx: CheckContext) -> Outcome:
    """Each sentence of an approved copy field must appear on the artwork as a sentence, word for word."""
    from rapidfuzz import fuzz

    from app.facts import join_lines, reading_order
    p = rule.params
    field_name = p["field"]
    label = p.get("label", field_name.replace("_", " "))
    appr = ctx.approved.get(field_name)
    if not appr:
        return Outcome("na", f"No approved {label} was supplied.")
    appr_text = LABEL_PREFIX.sub("", str(appr))
    blocks: dict[str, list[TextLine]] = {}
    for l in ctx.lines:
        if not l.graphic:
            blocks.setdefault(l.block or l.id, []).append(l)
    art_sents: list[tuple[str, str, str]] = []   # (key, sentence, block)
    for k, v in blocks.items():
        for s in _sentences(LABEL_PREFIX.sub("", join_lines(reading_order(v)))):
            art_sents.append((_sent_key(s), s, k))
    ok, bad, ev = 0, [], []
    for s in _sentences(appr_text):
        key = _sent_key(s)
        exact = next((a for a in art_sents if a[0] == key), None)
        if exact:
            ok += 1
            ev += ctx.ev_lines([l.id for l in blocks[exact[2]]][:8], label)
            continue
        best = max(art_sents, key=lambda a: fuzz.ratio(key, a[0]), default=None)
        score = fuzz.ratio(key, best[0]) if best else 0
        bad.append((s, best if score >= 60 else None, score))
    if not bad:
        return Outcome("pass", f"The approved {label} appears on the artwork word for word ({ok} sentence"
                               f"{'s' if ok != 1 else ''}).", evidence=ev[:8], expected=appr_text, found=appr_text)
    msgs, diff, found = [], None, []
    for s, best, score in bad:
        if best:
            msgs.append(f"approved “{s}” but the artwork says “{best[1]}”")
            ev += ctx.ev_lines([l.id for l in blocks[best[2]]][:8], f"{label} — differs")
            found.append(best[1])
            diff = diff or word_diff(s, best[1])
        else:
            msgs.append(f"“{s}” is missing")
    o = Outcome("fail", f"The {label} on the artwork does not match the approved copy: " + "; ".join(msgs[:3]) + ".",
                evidence=ev[:10], expected=appr_text, found=" ".join(found) or "not found", diff=diff,
                confidence=min([l.confidence for k in {b[1][2] for b in bad if b[1]} for l in blocks[k]] or [1.0]))
    if ctx.art.quality == "raster":
        o.status = "unknown"
        o.notes.append("Compared with OCR text — confirm by eye.")
    return o


def check_claims_approved(rule: Rule, ctx: CheckContext) -> Outcome:
    """Marketing / nutrition claims on the pack must be on the brand's approved claims list."""
    from rapidfuzz import fuzz
    approved = ctx.approved.get(rule.params.get("approved_field", "claims")) or []
    if not approved:
        return Outcome("na", "No approved claims list was supplied.")
    approved_n = [norm_cmp(re.sub(r"\(.*?\)", "", a)) for a in approved]
    for fld in rule.params.get("also_approved_fields", []):
        if isinstance(ctx.approved.get(fld), str):
            approved_n.append(norm_cmp(ctx.approved[fld]))
    claims = ctx.occ("claims")
    ignore = [re.compile(x, re.I) for x in rule.params.get("ignore", [])]
    unapproved, matched, ev = [], [], []
    for f in claims:
        c = norm_cmp(str(f.value))
        if any(r.search(c) for r in ignore):
            continue
        if any(fuzz.partial_ratio(c, a) >= 88 or fuzz.partial_ratio(a, c) >= 88 for a in approved_n):
            matched.append(str(f.value))
            continue
        unapproved.append(f)
    if not unapproved:
        return Outcome("pass", f"All {len(matched)} claims on the pack are on the approved list.",
                       evidence=[e for f in claims[:4] for e in ctx.ev_fact(f, "approved claim")])
    for f in unapproved:
        ev += ctx.ev_fact(f, f"not approved: {f.value}")
    names = sorted({str(f.value).upper() for f in unapproved})
    return Outcome("fail", f"Claim(s) not on the approved claims list: {', '.join('“' + n + '”' for n in names)}. "
                           f"Approved: {'; '.join(approved)}.", evidence=ev, found=", ".join(names),
                   expected="; ".join(approved), confidence=min(f.confidence for f in unapproved))


# ---------------------------------------------------------------- reasoning across fields

_OPS_TXT = {"<=": "≤", ">=": "≥", "<": "<", ">": ">", "==": "="}


def check_claim_conflict(rule: Rule, ctx: CheckContext) -> Outcome:
    """A claim (or a declared nutrient value) contradicted by the ingredient list.

    params.claims: [{name, patterns: [regex]} | {name, nutrient, op, value}, forbidden: [regex], reason]
    """
    specs = rule.params.get("claims", [])
    ing = (ctx.facts.get("ingredients") or [None])[0]
    if ing is None:
        return Outcome("unknown", "No ingredient list was found, so claims could not be checked against it.",
                       confidence=0.5)
    n = ctx.nutrition()
    fails, passes, ev, reviews, expect = [], [], [], [], []
    conf = ing.confidence
    triggered = 0
    for spec in specs:
        trig_lines: list[TextLine] = []
        trig_desc = ""
        if spec.get("patterns"):
            hits = [(t, ls) for t, ls in ctx.claim_texts() if any(re.search(p, t, re.I) for p in spec["patterns"])]
            if not hits:
                continue
            trig_lines = hits[0][1]
            trig_desc = f"“{hits[0][0][:60]}”"
        elif spec.get("nutrient"):
            if n is None:
                continue
            col, vals = ctx.nutrition_basis(n, "per_serve")
            v = vals.get(spec["nutrient"])
            if v is None or not _OPS[spec.get("op", "==")](v, float(spec.get("value", 0))):
                continue
            rows = n.attrs.get("rows", {}).get(spec["nutrient"], [])
            trig_lines = [ctx.art.line(i) for i in rows if ctx.art.line(i)]
            trig_desc = f"the nutrition panel declares {NUTRIENT_LABELS.get(spec['nutrient'], (spec['nutrient'],))[0]} " \
                        f"{fmt_num(v)} {NUTRIENT_LABELS.get(spec['nutrient'], ('', 'g'))[1]}"
        else:
            continue
        triggered += 1
        bad = ctx.ingredient_hits(spec.get("forbidden", []))
        if bad:
            items = ", ".join(f"“{item}”" for item, _ in bad[:4])
            text = (f"{spec['name']}: {trig_desc} but the ingredients include {items}"
                    + (f" — {spec['reason']}" if spec.get("reason") else ""))
            (reviews if spec.get("review") else fails).append(text)
            expect.append(f"{spec['name']}: claim only if no ingredient contradicts it — remove the claim or change the "
                          f"recipe ({', '.join(item for item, _ in bad[:3])})")
            ev += ctx.ev_lines([l.id for l in trig_lines], f"claim: {spec['name']}")
            for item, _ in bad[:4]:
                ev += ctx.ev_lines(ctx.lines_with(ing, item), f"conflicts: {item[:30]}")
            conf = min([conf] + [l.confidence for l in trig_lines])
        else:
            passes.append(spec["name"])
    if not triggered:
        return Outcome("na", "None of the claims covered by this rule appear on the artwork.")
    if fails:
        return Outcome("fail", "Contradiction between claim and ingredients — " + " | ".join(fails) + ".",
                       evidence=ev, found="; ".join(fails), expected="; ".join(expect), confidence=conf)
    if reviews:
        return Outcome("unknown", "Likely contradiction — " + " | ".join(reviews) + ".", evidence=ev,
                       found="; ".join(reviews), expected="; ".join(expect), confidence=conf,
                       notes=["Rounding rules allow this in a narrow case, so a person must confirm it with the "
                              "recipe."])
    return Outcome("pass", f"Consistent with the ingredient list: {', '.join(passes)}.",
                   evidence=ctx.ev_fact(ing)[:2], confidence=conf)


def check_precautionary_conflict(rule: Rule, ctx: CheckContext) -> Outcome:
    """'May contain X' is for cross-contact only; it must not name allergens that are ingredients."""
    table = ALLERGEN_TABLES.get(str(rule.params.get("allergens", "")).lower()) or DEFAULT_ALLERGENS
    prec = ctx.occ("precautionary_statement")
    if not prec:
        return Outcome("na", "No precautionary ('may contain') statement on the artwork.")
    ing = (ctx.facts.get("ingredients") or [None])[0]
    if ing is None:
        return Outcome("unknown", "No ingredient list found to compare with the 'may contain' statement.",
                       confidence=0.5)
    s = prec[0]
    named = find_allergens(s.value["text"], table)
    conflicts = []
    for cat, kws in named.items():
        for kw in kws:
            if kw.lower() in ("nuts", "tree nut", "tree nuts"):
                continue
            stem = kw[:-1] if kw.lower().endswith("s") else kw
            hits = ctx.ingredient_hits([r"(?<![a-z])" + re.escape(stem) + r"(?:e?s)?(?![a-z])"])
            if hits:
                conflicts.append((kw, hits[0][0]))
    if not conflicts:
        return Outcome("pass", "The 'may contain' statement only names allergens that are not ingredients.",
                       evidence=ctx.ev_fact(s, "precautionary statement"), confidence=s.confidence)
    ev = ctx.ev_fact(s, "‘may contain’ statement")
    for kw, item in conflicts[:5]:
        ev += ctx.ev_lines(ctx.lines_with(ing, item), f"ingredient: {item[:30]}")
    names = [w.title() for w in _dedupe_plural([kw for kw, _ in conflicts])]
    return Outcome("fail", f"The 'may contain' statement names {', '.join(names)}, but "
                           f"{'these are' if len(names) > 1 else 'this is'} actually in the ingredient list. "
                           f"A precautionary statement cannot stand in for declaring an ingredient.",
                   evidence=ev, expected="‘Contains: Tree Nuts (…)’ for ingredients; ‘may contain’ only for "
                                         "cross-contact", found=s.value["text"][:160], confidence=s.confidence)


def check_energy_consistency(rule: Rule, ctx: CheckContext) -> Outcome:
    """Energy values printed elsewhere (front-of-pack badges) must match the nutrition panel."""
    decls = ctx.occ("energy_declaration")
    n = ctx.nutrition()
    if not decls:
        return Outcome("na", "No energy values are printed outside the nutrition panel.")
    if n is None:
        return Outcome("unknown", "No nutrition panel was read to compare the front-of-pack energy with.",
                       confidence=0.5)
    refs = {}
    if n.value.get("per_serve", {}).get("energy_kcal") is not None:
        refs["per serving"] = n.value["per_serve"]["energy_kcal"]
    if n.value.get("per_100", {}).get("energy_kcal") is not None:
        refs["per 100 g"] = n.value["per_100"]["energy_kcal"]
    spc = n.value.get("servings_per_container")
    if spc and "per serving" in refs:
        refs["per container"] = refs["per serving"] * spc
    tol = float(rule.params.get("tolerance", 0.1))
    bad, ev = [], []
    for d in decls:
        v = float(d.value or 0)
        if not any(abs(v - r) <= max(tol * r, 2) for r in refs.values()):
            bad.append(d)
    if not bad:
        return Outcome("pass", "Front-of-pack energy matches the nutrition panel.",
                       evidence=[e for d in decls for e in ctx.ev_fact(d)], confidence=n.confidence)
    for d in bad:
        ev += ctx.ev_fact(d, f"{fmt_num(d.value)} kcal")
    ev += ctx.nutr_rows_ev(n, ["energy_kcal"], "nutrition panel energy")
    ref_txt = ", ".join(f"{fmt_num(r)} kcal {k}" for k, r in refs.items())
    return Outcome("fail", f"Energy shown outside the nutrition panel ({', '.join(fmt_num(d.value) + ' kcal' for d in bad)}) "
                           f"matches none of the panel's values ({ref_txt}).", evidence=ev,
                   expected=ref_txt, found=", ".join(fmt_num(d.value) + " kcal" for d in bad),
                   confidence=min(d.confidence for d in bad))


def check_hidden_text(rule: Rule, ctx: CheckContext) -> Outcome:
    """Text that exists in the print file but is covered by other artwork."""
    hidden = [l for l in ctx.art.lines if l.hidden and not l.technical]
    if not hidden:
        return Outcome("pass", "No hidden text found in the artwork layers.")
    sample = "; ".join(f"“{l.text[:40]}”" for l in hidden[:5])
    ev = ctx.ev_lines([l.id for l in hidden[:12]], "hidden text")
    return Outcome("unknown", f"{len(hidden)} line(s) of text are in the file but painted over by other artwork, so they "
                              f"will not print — e.g. {sample}. Delete them from the print file: they confuse "
                              f"proofreading tools and can resurface if layers change.", evidence=ev,
                   found=f"{len(hidden)} hidden lines", confidence=0.9,
                   notes=["Detected from the PDF's paint order: an opaque shape is drawn over this text."])


# ---------------------------------------------------------------- AI judgment

def check_ai_judgment(rule: Rule, ctx: CheckContext) -> Outcome:
    from app.ai import judge_rule  # local import to avoid a cycle
    if not ctx.llm or not ctx.llm.enabled:
        return Outcome("unknown", "This rule needs human judgment and the AI assistant is offline — please review it "
                                  "manually against the front panel.", method="none", confidence=0.0)
    return judge_rule(rule, ctx)


CHECKS: dict[str, Callable[[Rule, CheckContext], Outcome]] = {
    "fact_present": check_fact_present,
    "fact_format": check_fact_format,
    "text_present": check_text_present,
    "text_absent": check_text_absent,
    "matches_approved": check_matches_approved,
    "cross_consistency": check_cross_consistency,
    "min_text_height": check_min_text_height,
    "text_style": check_text_style,
    "symbol_present": check_symbol_present,
    "barcode_valid": check_barcode_valid,
    "qr_present": check_qr_present,
    "allergen_coverage": check_allergen_coverage,
    "nutrition_math": check_nutrition_math,
    "claim_threshold": check_claim_threshold,
    "fop_warning": check_fop_warning,
    "identity_on_panel": check_identity_on_panel,
    "net_quantity_format": check_net_quantity_format,
    "approved_copy": check_approved_copy,
    "claims_approved": check_claims_approved,
    "claim_conflict": check_claim_conflict,
    "precautionary_conflict": check_precautionary_conflict,
    "energy_consistency": check_energy_consistency,
    "hidden_text": check_hidden_text,
    "ai_judgment": check_ai_judgment,
}
