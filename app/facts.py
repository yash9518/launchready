"""Fact extraction: text lines -> structured facts, each tied to the lines it came from.

Every fact keeps `line_ids`, so any finding built on it can point at the exact
place on the artwork. Patterns tolerate common OCR quirks (missing spaces,
I/l/1 swaps) because raster artwork is read by OCR.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from typing import Any, Optional

from rapidfuzz import fuzz

from app.models import Artwork, Fact, TextLine
from app.util import NUM, UNIT_RE, norm_space, parse_quantity, split_top_level, to_float

FACT_LABELS = {
    "product_name": "product name", "net_quantity": "net quantity", "mrp": "MRP (retail sale price)",
    "fssai_license": "FSSAI licence number", "ingredients": "ingredient list",
    "allergen_statement": "allergen declaration", "precautionary_statement": "'may contain' statement",
    "nutrition": "nutrition information table", "best_before": "best before / use by date",
    "mfg_date": "date of manufacture / packing", "batch_no": "batch / lot number",
    "manufacturer": "manufacturer name and address", "customer_care": "consumer care details",
    "claims": "front-of-pack claims", "gtin": "retail barcode (GTIN)", "qr_code": "QR code",
    "food_mark": "veg / non-veg symbol", "energy_declaration": "energy value outside the nutrition table",
    "certification": "certification mark", "dietary_statement": "dietary statement",
}

# Keywords used to look for "near misses" before declaring something missing.
FACT_KEYWORDS = {
    "net_quantity": ["net wt", "net weight", "net quantity", "net qty", "net content"],
    "mrp": ["mrp", "m.r.p", "maximum retail price", "incl. of all taxes"],
    "fssai_license": ["lic. no", "licence no", "license no", "fssai"],
    "ingredients": ["ingredients"],
    "allergen_statement": ["contains", "allergen"],
    "nutrition": ["nutrition", "energy", "protein", "carbohydrate"],
    "best_before": ["best before", "use by", "expiry"],
    "mfg_date": ["mfd", "mfg", "date of manufacture", "packed on", "pkd"],
    "batch_no": ["batch", "lot no"],
    "manufacturer": ["manufactured by", "marketed by", "packed by", "mfd by"],
    "customer_care": ["customer care", "consumer care", "toll free", "helpline"],
    "product_name": [],
}

STOP = re.compile(
    r"(?i)^\s*(contains\b|allergen|allergy|may\s*contain|nutrition|n[e3]t\s*(wt|weight|qty|quantity|content)|"
    r"best\s*before|use\s*by|expiry|mfd|mfg|batch|lot\s*no|manufactured|marketed|packed\s*(by|on)|storage|"
    r"store\b|directions|serving\s*suggestion|m\.?\s*r\.?\s*p|customer|consumer|fssai|lic\.?\s*no|ingredients|"
    r"for\s*allergens|keep\s*in|\*)")

DEFAULT_ALLERGENS: dict[str, dict[str, list[str]]] = {
    "gluten": {"kw": ["wheat", "atta", "maida", "semolina", "sooji", "suji", "rava", "barley", "rye", "oat", "oats",
                      "spelt", "gluten", "malt", "wheat flour", "whole wheat", "rolled oats"],
               "not": ["buckwheat", "gluten free", "gluten-free"]},
    "crustacean": {"kw": ["crustacean", "shrimp", "prawn", "crab", "lobster"], "not": []},
    "egg": {"kw": ["egg", "eggs", "albumin"], "not": ["eggplant", "egg free", "eggless"]},
    "fish": {"kw": ["fish", "anchovy", "tuna", "salmon", "sardine"], "not": []},
    "peanut": {"kw": ["peanut", "peanuts", "groundnut", "groundnuts"], "not": []},
    "tree_nuts": {"kw": ["almond", "almonds", "cashew", "cashews", "walnut", "walnuts", "pistachio", "pistachios",
                         "hazelnut", "hazelnuts", "pecan", "macadamia", "tree nut", "tree nuts", "nuts"],
                  "not": ["coconut", "nutmeg", "peanuts", "groundnuts", "doughnut"]},
    "soy": {"kw": ["soy", "soya", "soybean", "soybeans", "soyabean", "soy lecithin", "soya lecithin", "soy protein",
                   "soy flour", "soya flour"], "not": []},
    "milk": {"kw": ["milk", "butter", "ghee", "whey", "casein", "caseinate", "cheese", "cream", "lactose", "curd",
                    "paneer", "khoa", "yoghurt", "yogurt", "milk solids", "milk powder", "milk fat", "skimmed milk",
                    "whole milk", "milk protein"],
             "not": ["cocoa butter", "peanut butter", "shea butter", "nut butter", "coconut milk", "almond milk",
                     "oat milk", "soy milk", "soya milk", "cream of tartar", "milk thistle"]},
    "sulphite": {"kw": ["sulphite", "sulfite", "sulphites", "sulfites", "sulphur dioxide", "sulfur dioxide",
                        "ins 220", "ins 221", "ins 222", "ins 223", "ins 224", "ins 225", "ins 226", "ins 227",
                        "ins 228"], "not": []},
}

# US major food allergens (FALCPA 2004 + FASTER Act 2021). Oats/gluten are not US major allergens;
# coconut was removed from FDA's tree-nut list in January 2025.
US_ALLERGENS: dict[str, dict[str, list[str]]] = {
    "milk": DEFAULT_ALLERGENS["milk"],
    "egg": DEFAULT_ALLERGENS["egg"],
    "fish": DEFAULT_ALLERGENS["fish"],
    "crustacean": {"kw": ["crustacean", "shellfish", "shrimp", "prawn", "crab", "lobster", "crayfish", "crawfish"],
                   "not": []},
    "tree_nuts": {"kw": ["almond", "almonds", "brazil nut", "brazil nuts", "cashew", "cashews", "hazelnut",
                         "hazelnuts", "filbert", "macadamia", "pecan", "pecans", "pine nut", "pine nuts", "pistachio",
                         "pistachios", "walnut", "walnuts", "tree nut", "tree nuts", "nuts"],
                  "not": ["coconut", "nutmeg", "peanuts", "peanut", "groundnut", "doughnut", "butternut", "nutrition",
                          "nutrient"]},
    "peanut": {"kw": ["peanut", "peanuts", "groundnut", "groundnuts"], "not": []},
    "wheat": {"kw": ["wheat", "semolina", "durum", "spelt", "farro", "einkorn", "bulgur", "triticale", "kamut"],
              "not": ["buckwheat"]},
    "soy": DEFAULT_ALLERGENS["soy"],
    "sesame": {"kw": ["sesame", "tahini", "benne"], "not": []},
}
ALLERGEN_TABLES = {"fssai": DEFAULT_ALLERGENS, "india": DEFAULT_ALLERGENS, "us": US_ALLERGENS,
                   "us_fda": US_ALLERGENS}

ALLERGEN_NAMES = {"gluten": "cereals containing gluten", "crustacean": "crustaceans", "egg": "egg", "fish": "fish",
                  "peanut": "peanut", "tree_nuts": "tree nuts", "soy": "soy", "milk": "milk",
                  "sulphite": "sulphites", "wheat": "wheat", "sesame": "sesame"}


def desquash(text: str) -> str:
    """Re-insert spaces OCR commonly drops: 'MilkSolids' -> 'Milk Solids', 'Oats(12%)' -> 'Oats (12%)'."""
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text or "")
    t = re.sub(r"(?<=[A-Za-z])(?=[(\[])", " ", t)
    t = re.sub(r"(?<=[)\]])(?=[A-Za-z])", " ", t)
    t = re.sub(r"[,;](?=\S)", lambda m: m.group(0) + " ", t)
    return t


def find_allergens(text: str, table: Optional[dict] = None, loose: bool = False) -> dict[str, list[str]]:
    """Return {category: [matched keyword, ...]} for allergen keywords in `text` (OCR-tolerant).

    `loose=True` also accepts a keyword at the start of a longer word ("Milkand" -> milk). It is used only
    for OCR-read allergen *declarations*, where a dropped space must not turn into a false "undeclared".
    """
    table = table or DEFAULT_ALLERGENS
    low = " " + norm_space(desquash(text)).casefold() + " "
    low_nospace = re.sub(r"[\s\W_]+", "", low)
    hits: dict[str, list[str]] = {}
    for cat, spec in table.items():
        masked, masked_ns = low, low_nospace
        for neg in spec.get("not", []):
            masked = masked.replace(neg.casefold(), " " * len(neg))
            masked_ns = masked_ns.replace(re.sub(r"[\s\W_]+", "", neg.casefold()), "#")
        found = []
        for kw in spec.get("kw", []):
            k = kw.casefold()
            k_ns = re.sub(r"[\s\W_]+", "", k)
            if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", masked):
                found.append(kw)
            elif len(k_ns) >= 6 and k_ns in masked_ns:
                # OCR sometimes drops every space ("milksolids"): accept longer keywords inside merged words.
                found.append(kw)
            elif loose and len(k) >= 3 and re.search(r"(?<![a-z])" + re.escape(k), masked):
                found.append(kw)
        if found:
            hits[cat] = sorted(set(found))
    return hits


# ---------------------------------------------------------------- context

def reading_order(lines: list[TextLine]) -> list[TextLine]:
    """Sort lines into rows (similar vertical centre), rows top-to-bottom, words left-to-right.

    Justified paragraphs in packaging PDFs are often stored word by word; a plain (y, x)
    sort can interleave words of one row when their boxes differ by a fraction of a point.
    """
    if not lines:
        return []
    def height(l: TextLine) -> float:
        h = max(1.0, l.bbox[3] - l.bbox[1])
        return min(h, 1.3 * l.font_size) if l.font_size else h  # rotated text has a tall box

    items = sorted(lines, key=lambda l: ((l.bbox[1] + l.bbox[3]) / 2, l.bbox[0]))
    rows: list[list[TextLine]] = []
    for ln in items:
        cy = (ln.bbox[1] + ln.bbox[3]) / 2
        if rows:
            last = rows[-1]
            lcy = statistics.mean((l.bbox[1] + l.bbox[3]) / 2 for l in last)
            if abs(cy - lcy) <= 0.35 * max(height(ln), height(last[0])):
                last.append(ln)
                continue
        rows.append([ln])
    return [ln for row in rows for ln in sorted(row, key=lambda l: l.bbox[0])]


class Ctx:
    def __init__(self, art: Artwork):
        self.art = art
        self.lines = art.content_lines()
        self.by_block: dict[str, list[TextLine]] = defaultdict(list)
        for ln in self.lines:
            self.by_block[ln.block or ln.id].append(ln)
        for k in list(self.by_block):
            self.by_block[k] = reading_order(self.by_block[k])
        heights = [ln.bbox[3] - ln.bbox[1] for ln in self.lines] or [10.0]
        self.lh = statistics.median(heights)
        pdp = art.pdp()
        self.pdp_id = pdp.id if pdp else None
        self.multi_panel = len(art.panels) > 1
        self.structured: set[str] = set()

    def following(self, ln: TextLine, max_lines: int = 14, stop: re.Pattern = STOP) -> list[TextLine]:
        """Lines after `ln` in the same block, until a line that starts a new section."""
        blk = self.by_block.get(ln.block or ln.id, [ln])
        out: list[TextLine] = []
        started = False
        for other in blk:
            if other.id == ln.id:
                started = True
                continue
            if not started:
                continue
            if stop.search(other.text) or len(out) >= max_lines:
                break
            out.append(other)
        return out

    def neighbour(self, ln: TextLine, pattern: str) -> Optional[TextLine]:
        """Closest line to the right (same row) or just below that matches `pattern`."""
        best, best_d = None, 1e9
        h = max(1.0, ln.bbox[3] - ln.bbox[1])
        for o in self.lines:
            if o.id == ln.id or o.page != ln.page or not re.search(pattern, o.text, re.I):
                continue
            same_row = abs((o.bbox[1] + o.bbox[3]) / 2 - (ln.bbox[1] + ln.bbox[3]) / 2) < 0.6 * h
            right = same_row and 0 <= o.bbox[0] - ln.bbox[2] < 12 * h
            below = 0 <= o.bbox[1] - ln.bbox[3] < 1.6 * h and abs(o.bbox[0] - ln.bbox[0]) < 8 * h
            if right or below:
                d = (o.bbox[0] - ln.bbox[2]) if right else 3 * h + (o.bbox[1] - ln.bbox[3])
                if d < best_d:
                    best, best_d = o, d
        return best


def join_lines(lines: list[TextLine]) -> str:
    out = ""
    for ln in lines:
        t = ln.text.strip()
        if out.endswith("-") and t[:1].islower():
            out = out[:-1] + t
        else:
            out = (out + " " + t).strip()
    return norm_space(out)


def _fact(key: str, lines: list[TextLine], value: Any, raw: str = "", **attrs) -> Fact:
    return Fact(key=key, value=value, raw=raw or join_lines(lines), line_ids=[l.id for l in lines],
                confidence=round(min([l.confidence for l in lines] or [1.0]), 3),
                panel=lines[0].panel if lines else None, attrs=attrs)


# ---------------------------------------------------------------- extractors

NETQ_LABEL = r"\bn[e3]t\.?\s*(?:wt|weight|wgt|qty|quantity|contents?|vol(?:ume)?|mass)?\.?\s*[:\-]?\s*"
NOT_NETQ = re.compile(r"(?i)\b(per|serv(e|ing)|rda|kcal|approx|%|each|pieces?)\b")
QTY_ONLY = re.compile(r"(?i)^\s*" + NUM + r"\s*" + UNIT_RE + r"\s*e?\s*(\(\s*" + NUM + r"\s*" + UNIT_RE +
                      r"\s*\)\s*)?$")
NUTRIENT_WORD = re.compile(r"(?i)\b(fat|saturates?|sugars?|salt|sodium|energy|calories|kcal|kj|protein|carb\w*|"
                           r"fib(re|er)|cholesterol)\b")


def _near_nutrient_label(ctx: Ctx, ln: TextLine) -> bool:
    """A bare '3.8g' under 'Fat' is a nutrition value, not a pack size."""
    h = max(1.0, ln.bbox[3] - ln.bbox[1])
    for o in ctx.lines:
        if o.id == ln.id or o.page != ln.page or not NUTRIENT_WORD.search(o.text):
            continue
        dx = max(0.0, max(o.bbox[0], ln.bbox[0]) - min(o.bbox[2], ln.bbox[2]))
        dy = max(0.0, max(o.bbox[1], ln.bbox[1]) - min(o.bbox[3], ln.bbox[3]))
        if dx < 1.5 * h and dy < 1.0 * h:
            return True
    return False


def _net_quantity(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    out: list[Fact] = []
    seen: set[str] = set()
    for ln in ctx.lines:
        m = re.search(NETQ_LABEL + NUM + r"\s*" + UNIT_RE + r"(?![a-z])", ln.text, re.I)
        if m:
            q = parse_quantity(ln.text[m.start(1):])
            if q:
                out.append(_fact("net_quantity", [ln], q, raw=ln.text, labelled=True))
                seen.add(ln.id)
                continue
        if re.search(NETQ_LABEL + r"$", ln.text.strip(), re.I) and re.search(r"(?i)n[e3]t", ln.text):
            nb = ctx.neighbour(ln, r"^\s*" + NUM + r"\s*" + UNIT_RE + r"(?![a-z])")
            if nb and nb.id not in seen:
                q = parse_quantity(nb.text)
                if q:
                    out.append(_fact("net_quantity", [ln, nb], q, raw=f"{ln.text} {nb.text}", labelled=True))
                    seen.update({ln.id, nb.id})
    has_front = any(f.panel == ctx.pdp_id for f in out)
    for ln in ctx.lines:
        if ln.id in seen or ln.id in ctx.structured or NOT_NETQ.search(ln.text) or not QTY_ONLY.match(ln.text):
            continue
        on_front = ln.panel == ctx.pdp_id
        if on_front and has_front:
            continue
        if _near_nutrient_label(ctx, ln):
            continue
        q = parse_quantity(ln.text)
        if not q or (q["base"] < 10 and "." in ln.text):
            continue
        f = _fact("net_quantity", [ln], q, raw=ln.text, labelled=False)
        f.confidence = round(f.confidence * 0.85, 3)
        f.notes.append("Quantity shown without a 'Net Wt.' / 'Net Contents' label.")
        out.append(f)
    return out


MRP_RE = r"(?:\bM\.?\s*R\.?\s*P\.?(?![a-z])|maximum\s*retail\s*price)"
TAX_RE = re.compile(r"(?i)incl(?:\.|usive|uding)?\s*(?:of\s*)?all\s*taxes")


def _mrp(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    out = []
    for ln in ctx.lines:
        m = re.search(MRP_RE + r"[^\d]{0,24}?" + NUM, ln.text, re.I)
        if not m:
            continue
        lines = [ln]
        tax_line = ln if TAX_RE.search(ln.text) else None
        if not tax_line:
            for o in ctx.following(ln, 2, stop=re.compile(r"(?!x)x")):
                if TAX_RE.search(o.text):
                    tax_line = o
                    lines.append(o)
                    break
        if not tax_line:
            nb = ctx.neighbour(ln, TAX_RE.pattern)
            if nb:
                tax_line = nb
                lines.append(nb)
        out.append(_fact("mrp", lines, to_float(m.group(1)), raw=join_lines(lines),
                         inclusive_of_taxes=tax_line is not None))
    return out


LIC_RE = r"(?:lic(?:en[cs]e)?\.?\s*n[o0]\.?|fssa[il1|]\b|fssa[il1|](?=\d)|licen[cs]e\s*number|reg(?:istration)?\.?\s*n[o0]\.?)"


def _fssai(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    out, seen = [], set()
    for ln in ctx.lines:
        if not re.search(LIC_RE, ln.text, re.I):
            continue
        m = re.search(LIC_RE + r"[^\d]{0,25}(\d[\d\s]{6,24}\d)", ln.text, re.I)
        lines = [ln]
        digits = re.sub(r"\D", "", m.group(1)) if m else ""
        if not digits:
            nb = ctx.neighbour(ln, r"^\s*\d[\d\s]{8,22}\d\s*$")
            if nb:
                digits = re.sub(r"\D", "", nb.text)
                lines.append(nb)
        if digits and digits not in seen:
            seen.add(digits)
            out.append(_fact("fssai_license", lines, digits, raw=join_lines(lines), length=len(digits)))
    return out


ING_START = re.compile(r"(?i)^\s*(ingredients?|ingredient\s*list|composition)\s*[:\-]\s*|^\s*ingredients?\b\s*")


def _ingredients(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    out = []
    for ln in ctx.lines:
        if not re.match(r"(?i)^\s*(ingredients?|ingredient\s*list)\b", ln.text):
            continue
        lines = [ln] + ctx.following(ln, 60)
        text = ING_START.sub("", join_lines(lines), count=1).strip()
        if len(text) < 3:
            continue
        items = split_top_level(text.rstrip(". "))
        out.append(_fact("ingredients", lines, {"text": text.rstrip(". "), "items": items}))
        ctx.structured.update(l.id for l in lines)
    return out


ALG_START = re.compile(r"(?i)^\s*(contains|allergen(?:s|\s*information|\s*declaration|\s*advice)?|allergy\s*"
                       r"(?:advice|information))\b\s*[:\-]?\s*")


PRECAUTION_SPLIT = re.compile(r"(?i)(?=\bmay\s*contain\b|\b(?:produced|processed|made|manufactured|packed)\s+in\s+"
                              r"a\b|\bmay\s*be\s*present\b)")
PRECAUTION_START = re.compile(r"(?i)^\s*(may\s*contain|(?:produced|processed|made|manufactured|packed)\s+in\s+a\b|"
                              r"may\s*be\s*present)")


def split_precautionary(text: str) -> tuple[str, str]:
    """'Contains: Milk. May contain nuts.' -> ('Contains: Milk.', 'May contain nuts.')."""
    decl, prec = [], []
    for seg in PRECAUTION_SPLIT.split(text or ""):
        if not seg.strip():
            continue
        (prec if PRECAUTION_START.match(seg) else decl).append(seg.strip())
    return " ".join(decl), " ".join(prec)


def _allergens(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    out = []
    for ln in ctx.lines:
        t = ln.text.strip()
        if re.match(r"(?i)^\s*may\s*contain", t) or re.search(r"(?i)(processed|made|manufactured)\s+in\s+a?\s*"
                                                             r"(facility|factory|plant)", t):
            lines = [ln] + ctx.following(ln, 3)
            out.append(_fact("precautionary_statement", lines, {"text": join_lines(lines)}))
            ctx.structured.update(l.id for l in lines)
            continue
        if not ALG_START.match(t):
            continue
        lines = [ln] + ctx.following(ln, 6)
        text = ALG_START.sub("", join_lines(lines), count=1)
        decl, prec = split_precautionary(text)
        if prec:
            f = _fact("precautionary_statement", lines, {"text": prec}, raw=join_lines(lines))
            f.notes.append("Advisory ('may contain') wording — this is not an allergen declaration.")
            out.append(f)
        if decl.strip(" .,;:") or not prec:
            cats = find_allergens(decl)
            out.append(_fact("allergen_statement", lines, {"text": decl, "categories": sorted(cats), "matched": cats},
                             raw=join_lines(lines), bold=all(l.bold for l in lines),
                             style_known=all(l.source == "pdf" for l in lines)))
        ctx.structured.update(l.id for l in lines)
    return out


NUTRIENTS = [
    ("added_sugars_g", r"added\s*sugars?"),
    ("total_sugars_g", r"(?:total\s*)?sugars?"),
    ("saturated_fat_g", r"saturated|saturates|sat\.?\s*fat|\bsfa\b"),
    ("trans_fat_g", r"trans"),
    ("mufa_g", r"mono\s*unsat|monounsat|\bmufa\b"),
    ("pufa_g", r"poly\s*unsat|polyunsat|\bpufa\b"),
    ("total_fat_g", r"(?:total\s*)?fat"),
    ("dietary_fibre_g", r"fib(?:re|er)"),
    ("carbohydrate_g", r"carbo\s*hydrates?|carbs?\b"),
    ("protein_g", r"protein"),
    ("energy_kcal", r"energy|calories|kcal"),
    ("cholesterol_mg", r"cholesterol"),
    ("sodium_mg", r"sodium"),
    ("salt_g", r"\bsalt\b"),
]
HEADER_COLS = [("per_100", r"per\s*100\s*(?:g|ml|gm)?"),
               ("per_serve", r"per\s*serv(?:e|ing)(?:\s*\(?\s*\d+(?:[.,]\d+)?\s*(?:g|ml)\s*\)?)?"),
               ("rda", r"%\s*rda\*?|rda\s*%|%\s*dv|%\s*daily|daily\s*value|%\s*gda")]
NUM_TOKEN = re.compile(r"^[<~≤]?\s*(\d+(?:[.,]\d+)?)\s*(%)?\*?$|^[-–—]$")
SERVE_RE = re.compile(r"(?i)serv(?:ing|e)\s*(?:size)?\s*[:=]?\s*\(?\s*(\d+(?:[.,]\d+)?)\s*(g|ml)\b")


def _tokens(ln: TextLine) -> list[dict]:
    """Split a line into whitespace tokens with approximate x positions."""
    toks = []
    text = ln.text
    n = max(1, len(text))
    x0, x1 = ln.bbox[0], ln.bbox[2]
    for m in re.finditer(r"\S+", text):
        a = x0 + (x1 - x0) * m.start() / n
        b = x0 + (x1 - x0) * m.end() / n
        toks.append({"t": m.group(0), "x0": a, "x1": b, "line": ln})
    return toks


def _parse_table(ctx: Ctx, hdr: TextLine) -> Optional[Fact]:
    page, panel = hdr.page, hdr.panel
    pbox = None
    for p in ctx.art.panels:
        if p.id == panel:
            pbox = p.bbox
    if pbox is None:
        pg = ctx.art.pages[page]
        pbox = [0, 0, pg.width, pg.height]
    lh = max(1.0, statistics.median([l.bbox[3] - l.bbox[1] for l in ctx.lines if l.page == page] or [10.0]))
    x_min = hdr.bbox[0] - 2.5 * lh
    max_y = hdr.bbox[3] + 0.85 * (pbox[3] - pbox[1])
    cands = [l for l in ctx.lines if l.page == page and l.id != hdr.id and (panel is None or l.panel == panel)
             and l.bbox[1] >= hdr.bbox[1] - 0.2 * lh and (l.bbox[0] + l.bbox[2]) / 2 >= x_min
             and l.bbox[1] <= max_y]
    cands.sort(key=lambda l: (l.bbox[1] + l.bbox[3]) / 2)
    rows: list[list[TextLine]] = []
    for l in cands:
        cy = (l.bbox[1] + l.bbox[3]) / 2
        if rows:
            rcy = statistics.mean([(r.bbox[1] + r.bbox[3]) / 2 for r in rows[-1]])
            if abs(cy - rcy) <= 0.45 * max(lh, l.bbox[3] - l.bbox[1]):
                rows[-1].append(l)
                continue
        rows.append([l])

    columns: list[dict] = []
    per100, perserve, rda = {}, {}, {}
    conf: dict[str, dict[str, float]] = {"per_100": {}, "per_serve": {}, "rda": {}}
    row_lines: dict[str, list[str]] = {}
    units: dict[str, str] = {}
    used_ids: list[str] = [hdr.id]
    serving = None
    misses, found_rows = 0, 0
    for ri, row in enumerate(rows):
        row.sort(key=lambda l: l.bbox[0])
        row_text = " ".join(l.text for l in row)
        sm = SERVE_RE.search(row_text)
        if sm and serving is None:
            serving = to_float(sm.group(1))
        if not columns and ri < 4 and re.search(r"(?i)per\s*100|per\s*serv|rda|daily\s*value", row_text):
            for l in row:
                n = max(1, len(l.text))
                for name, pat in HEADER_COLS:
                    for m in re.finditer(pat, l.text, re.I):
                        a = l.bbox[0] + (l.bbox[2] - l.bbox[0]) * m.start() / n
                        b = l.bbox[0] + (l.bbox[2] - l.bbox[0]) * m.end() / n
                        if not any(c["name"] == name for c in columns):
                            columns.append({"name": name, "x0": a, "x1": b})
            used_ids.extend(l.id for l in row)
            continue
        toks = [t for l in row for t in _tokens(l)]
        if columns:
            # Ignore anything printed beyond the table's last column (e.g. front-panel text on the same row).
            x_max = max(c["x1"] for c in columns) + 2.5 * lh
            toks = [t for t in toks if t["x0"] <= x_max]
        label_toks, value_toks = [], []
        for t in toks:
            if not value_toks and not NUM_TOKEN.match(t["t"]):
                label_toks.append(t)
            elif NUM_TOKEN.match(t["t"]):
                value_toks.append(t)
        label = " ".join(t["t"] for t in label_toks)
        key = None
        for k, pat in NUTRIENTS:
            if re.search(pat, label, re.I):
                key = k
                break
        if key is not None and (key in row_lines or not any(NUM_TOKEN.match(t["t"]) for t in value_toks)):
            key = None  # a label without numbers ('LOW FAT' badge) or a repeated nutrient is not a table row
        if key is None:
            if found_rows >= 3:
                misses += 1
                if misses >= 2 or STOP.search(row_text) and not row_text.startswith("*"):
                    break
            continue
        misses = 0
        found_rows += 1
        unit = "g"
        um = re.search(r"\((kcal|kj|g|mg|mcg|µg)\)|\b(kcal|kj|mg|mcg)\b", label, re.I)
        if um:
            unit = (um.group(1) or um.group(2)).lower()
        units[key] = unit
        row_lines[key] = [l.id for l in row]
        used_ids.extend(l.id for l in row)
        plain_i = 0
        for t in value_toks:
            m = NUM_TOKEN.match(t["t"])
            if not m or m.group(1) is None:
                plain_i += 0 if columns else 1
                continue
            val = float(m.group(1).replace(",", "."))
            is_pct = bool(m.group(2))
            col = None
            if columns:
                tx1, txc = t["x1"], (t["x0"] + t["x1"]) / 2
                col = min(columns, key=lambda c: min(abs(tx1 - c["x1"]), abs(txc - (c["x0"] + c["x1"]) / 2)))["name"]
                if is_pct and any(c["name"] == "rda" for c in columns):
                    col = "rda"
            else:
                if is_pct:
                    col = "rda"
                else:
                    col = ["per_100", "per_serve"][min(plain_i, 1)]
                    plain_i += 1
            if key == "sodium_mg" and unit == "g" and col != "rda":
                val = val * 1000.0
            if key == "energy_kcal" and unit == "kj" and col != "rda":
                val = val / 4.184
            target = {"per_100": per100, "per_serve": perserve, "rda": rda}[col]
            if key not in target:
                target[key] = val
                conf[col][key] = t["line"].confidence
    if found_rows < 3:
        return None
    lines = [ctx.art.line(i) for i in dict.fromkeys(used_ids)]
    lines = [l for l in lines if l is not None]
    if serving is None:
        for l in ctx.lines:
            sm = SERVE_RE.search(l.text)
            if sm:
                serving = to_float(sm.group(1))
                break
    f = _fact("nutrition", lines, {"per_100": per100, "per_serve": perserve, "rda": rda, "serving_size": serving},
              raw=f"Nutrition table ({found_rows} rows)", rows=row_lines, header=hdr.id, units=units, conf=conf,
              columns=[c["name"] for c in columns])
    ctx.structured.update(f.line_ids)
    return f


# ---------------------------------------------------------------- US "Nutrition Facts" panel (21 CFR 101.9)

US_ROWS = [
    ("added_sugars_g", r"added\s*sugars?"),
    ("total_sugars_g", r"total\s*sugars?|^sugars?\b"),
    ("saturated_fat_g", r"saturated\s*fat|sat\.?\s*fat"),
    ("trans_fat_g", r"trans\s*fat"),
    ("total_fat_g", r"total\s*fat|^fat\b"),
    ("cholesterol_mg", r"cholesterol"),
    ("sodium_mg", r"sodium"),
    ("dietary_fibre_g", r"dietary\s*fib(?:er|re)|^fib(?:er|re)"),
    ("carbohydrate_g", r"total\s*carb\w*|carbohydrate"),
    ("protein_g", r"protein"),
    ("vitamin_d_mcg", r"vitamin\s*d\b"),
    ("calcium_mg", r"calcium"),
    ("iron_mg", r"\biron\b"),
    ("potassium_mg", r"potassium"),
]
US_VAL = re.compile(r"(?i)(<\s*)?(\d+(?:\.\d+)?)\s*(mcg|µg|ug|mg|g)\b")
US_DV = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_UNIT_FACTOR = {("g", "mg"): 1000.0, ("mg", "g"): 0.001, ("mg", "mcg"): 1000.0, ("mcg", "mg"): 0.001,
                ("g", "mcg"): 1e6}


def _rows(lines: list[TextLine]) -> list[list[TextLine]]:
    rows: list[list[TextLine]] = []
    for l in sorted(lines, key=lambda l: (l.bbox[1] + l.bbox[3]) / 2):
        cy = (l.bbox[1] + l.bbox[3]) / 2
        h = max(1.0, min(l.bbox[3] - l.bbox[1], 14.0))
        if rows:
            r = rows[-1]
            rcy = statistics.mean((x.bbox[1] + x.bbox[3]) / 2 for x in r)
            if abs(cy - rcy) <= 0.45 * h:
                r.append(l)
                continue
        rows.append([l])
    return [sorted(r, key=lambda l: l.bbox[0]) for r in rows]


def _parse_us_panel(ctx: Ctx, hdr: TextLine) -> Optional[Fact]:
    w = max(hdr.bbox[2] - hdr.bbox[0], 60.0)
    x0, x1 = hdr.bbox[0] - 0.15 * w, hdr.bbox[0] + 1.3 * w
    y0, y1 = hdr.bbox[1] - 1, hdr.bbox[3] + 4.0 * w
    cands = [l for l in ctx.lines if l.page == hdr.page and l.id != hdr.id and l.bbox[0] >= x0 and l.bbox[2] <= x1
             and y0 <= l.bbox[1] <= y1 and (hdr.panel is None or l.panel == hdr.panel)]
    per_serve: dict[str, float] = {}
    dv: dict[str, float] = {}
    rows_ids: dict[str, list[str]] = {}
    conf: dict[str, dict[str, float]] = {"per_serve": {}, "dv": {}}
    units: dict[str, str] = {}
    used = [hdr.id]
    info: dict[str, Any] = {"servings_per_container": None, "serving_size_raw": None, "serving_size": None,
                            "serving_size_unit": None, "household": False}
    extra_ids: dict[str, list[str]] = {}
    for row in _rows(cands):
        text = " ".join(l.text for l in row)
        ids = [l.id for l in row]
        rconf = min(l.confidence for l in row)
        if re.search(r"(?i)daily\s*value\s*\(dv\)|tells\s*you\s*how\s*much|calories\s*a\s*day", text):
            extra_ids.setdefault("footnote", []).extend(ids)
            used.extend(ids)
            continue
        m = re.search(r"(?i)(\d+(?:\.\d+)?|about\s*\d+)\s*servings?\s*per\s*(container|package|pack|box|bag)", text)
        if m:
            info["servings_per_container"] = to_float(m.group(1))
            extra_ids["servings"] = ids
            used.extend(ids)
            if not re.search(r"(?i)serving\s*size", text):
                continue
        m = re.search(r"(?i)serving\s*size\s*(.*)$", text)
        if m:
            raw = m.group(1).strip()
            info["serving_size_raw"] = raw
            metric = re.search(r"\(\s*(\d+(?:\.\d+)?)\s*(g|ml|mL)\s*\)", raw) or \
                re.search(r"(\d+(?:\.\d+)?)\s*(g|ml|mL)\b", raw)
            if metric:
                info["serving_size"] = float(metric.group(1))
                info["serving_size_unit"] = metric.group(2).lower()
            info["household"] = bool(re.search(r"(?i)\b(cups?|tbsp|tsp|tablespoons?|teaspoons?|pieces?|bars?|"
                                               r"slices?|pouch|bottle|can|container|package|oz|fl\s*oz|"
                                               r"biscuits?|cookies?|crackers?)\b", raw))
            extra_ids["serving_size"] = ids
            used.extend(ids)
            continue
        m = re.search(r"(?i)\bcalories\b\D{0,12}(\d+(?:\.\d+)?)", text)
        if m and "energy_kcal" not in per_serve and not re.search(r"(?i)calories\s*a\s*day", text):
            per_serve["energy_kcal"] = float(m.group(1))
            conf["per_serve"]["energy_kcal"] = rconf
            rows_ids["energy_kcal"] = ids
            units["energy_kcal"] = "kcal"
            used.extend(ids)
            continue
        label = re.sub(r"(?i)^incl(?:udes|\.)?\s*(<\s*)?\d+(?:\.\d+)?\s*(mcg|µg|mg|g)\s*", "", text).strip()
        key = None
        for k, pat in US_ROWS:
            if re.search(pat, label, re.I):
                key = k
                break
        if key is None or key in per_serve:
            continue
        vm = US_VAL.search(text)
        if vm:
            val, unit = float(vm.group(2)), vm.group(3).lower().replace("µg", "mcg").replace("ug", "mcg")
            want = key.rsplit("_", 1)[1]
            if unit != want:
                val = val * _UNIT_FACTOR.get((unit, want), 1.0)
            per_serve[key] = round(val, 4)
            units[key] = want
            conf["per_serve"][key] = rconf
        pm = list(US_DV.finditer(text))
        if pm:
            dv[key] = float(pm[-1].group(1))
            conf["dv"][key] = rconf
        rows_ids[key] = ids
        used.extend(ids)
    if len(per_serve) < 4:
        return None
    lines = [ctx.art.line(i) for i in dict.fromkeys(used)]
    lines = [l for l in lines if l is not None]
    value = {"per_serve": per_serve, "per_100": {}, "rda": {}, "dv": dv, "format": "us", **info}
    f = _fact("nutrition", lines, value, raw=f"Nutrition Facts panel ({len(per_serve)} nutrients)", rows=rows_ids,
              header=hdr.id, units=units, conf=conf, columns=["per_serve", "dv"], extra_rows=extra_ids)
    ctx.structured.update(f.line_ids)
    return f


def _nutrition(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    best = None
    for ln in ctx.lines:
        if re.fullmatch(r"(?i)\s*nutrition\s*facts\s*", ln.text):
            f = _parse_us_panel(ctx, ln)
            if f and (best is None or len(f.attrs["rows"]) > len(best.attrs["rows"])):
                best = f
    if best:
        return [best]
    for ln in ctx.lines:
        if re.search(r"(?i)nutrition(al)?|nutrient\s*value", ln.text) and len(ln.text) < 70:
            f = _parse_table(ctx, ln)
            if f and (best is None or len(f.attrs["rows"]) > len(best.attrs["rows"])):
                best = f
    return [best] if best else []


BB_RE = re.compile(r"(?i)\b(best\s*before|use\s*by|expiry(?:\s*date)?|exp\.?\s*date|expires?\s*on|consume\s*before)")
MFG_RE = re.compile(r"(?i)\b(mfd\.?\s*(?:on|date)|mfg\.?\s*(?:date|dt)?(?!\s*lic)|date\s*of\s*(?:mfg|manufactur\w*|"
                    r"packing|packaging|pkg)|manufactured\s*on|packed\s*on|pkd\.?(?:\s*on)?|packing\s*date|"
                    r"month\s*(?:and|&)\s*year\s*of\s*(?:mfg|manufacture|packing))\b")
BATCH_RE = re.compile(r"(?i)\b(batch|lot)\s*(no\.?|number|code|#)?")


def _dates_batch(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    out = []
    for ln in ctx.lines:
        m = BB_RE.search(ln.text)
        if m:
            out.append(_fact("best_before", [ln], ln.text, kind=m.group(1).lower()))
        m2 = MFG_RE.search(ln.text)
        if m2 and not re.search(r"(?i)(mfd|mfg)\.?\s*by", m2.group(0) + ln.text[m2.end():m2.end() + 4]):
            out.append(_fact("mfg_date", [ln], ln.text))
        if BATCH_RE.search(ln.text):
            out.append(_fact("batch_no", [ln], ln.text))
    return out


MFR_RE = re.compile(r"(?i)\b(manufactured|mfd\.?|marketed|packed|mkt\.?|mfg\.?|imported|distributed|produced|made)"
                    r"\s*(?:(?:&|and)\s*(?:marketed|packed|manufactured|distributed|imported|mfd\.?|mkt\.?)\s*)?"
                    r"(by|for)\b")
PIN_RE = re.compile(r"(?<!\d)\d{6}(?!\d)|\b\d{3}\s\d{3}\b")
US_ADDR_RE = re.compile(r"\b[A-Z][A-Za-z .'-]+,\s*[A-Z]{2}\.?\s+\d{5}(?:-\d{4})?\b")


def _manufacturer(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    out = []
    for ln in ctx.lines:
        if not MFR_RE.search(ln.text):
            continue
        lines = [ln] + ctx.following(ln, 5)
        text = join_lines(lines)
        us = US_ADDR_RE.search(text)
        out.append(_fact("manufacturer", lines, text,
                         has_address=bool(PIN_RE.search(text) or us or re.search(r"(?i)\bindia\b", text)),
                         us_address=bool(us)))
        ctx.structured.update(l.id for l in lines)
    return out


CC_RE = re.compile(r"(?i)(customer|consumer)\s*(care|service|support|helpline|complaints?|relations)|toll[\s-]*"
                   r"free|helpline|for\s*(feedback|complaints?|queries)|write\s*to\s*us|call\s*us")
PHONE_RE = re.compile(r"\+?\d[\d\s\-]{7,}\d")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _customer_care(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    out = []
    for ln in ctx.lines:
        if not CC_RE.search(ln.text):
            continue
        lines = [ln] + ctx.following(ln, 2)
        text = join_lines(lines)
        phone, email = PHONE_RE.search(text), EMAIL_RE.search(text)
        out.append(_fact("customer_care", lines, text, phone=phone.group(0) if phone else None,
                         email=email.group(0) if email else None, has_contact=bool(phone or email)))
        ctx.structured.update(l.id for l in lines)
    return out


CLAIM_VOCAB = [r"low[\s-]*fat", r"fat[\s-]*free", r"sugar[\s-]*free", r"no\s*added\s*sugar", r"low\s*sugar",
               r"high[\s-]*protein", r"source\s*of\s*protein", r"rich\s*in\s*protein", r"protein[\s-]*rich",
               r"high[\s-]*fib(re|er)", r"source\s*of\s*fib(re|er)", r"low\s*(sodium|salt)", r"no\s*added\s*salt",
               r"(zero|no|0\s*g)\s*trans[\s-]*fat", r"trans[\s-]*fat[\s-]*free", r"cholesterol[\s-]*free",
               r"gluten[\s-]*free", r"baked,?\s*not\s*fried", r"no\s*preservatives?", r"100\s*%\s*natural",
               r"all\s*natural", r"healthy", r"immunity", r"organic", r"no\s*palm\s*oil", r"high\s*in\s*\w+",
               r"gmo[\s-]*free", r"non[\s-]*gmo", r"vegan", r"suitable\s*for\s*vegetarians?", r"oven[\s-]*baked",
               r"high[\s-]*grain", r"whole[\s-]*grain", r"no\s*artificial\s*\w+"]


def claim_lines(ctx_or_facts_lines: list[TextLine], structured: set[str]) -> list[TextLine]:
    return [l for l in ctx_or_facts_lines if l.id not in structured]


def claim_texts(lines: list[TextLine], structured: set[str]) -> list[tuple[str, list[TextLine]]]:
    """Texts in which claims are searched: every free-text line, plus short blocks joined
    (badges often set 'NO / ADDED / SUGAR' as three stacked lines)."""
    free = [l for l in lines if l.id not in structured]
    out: list[tuple[str, list[TextLine]]] = [(l.text, [l]) for l in free]
    blocks: dict[str, list[TextLine]] = defaultdict(list)
    for l in free:
        blocks[l.block or l.id].append(l)
    seen_groups: set[tuple[str, ...]] = set()
    for blk in blocks.values():
        if 2 <= len(blk) <= 6:
            blk = reading_order(blk)
            seen_groups.add(tuple(sorted(l.id for l in blk)))
            out.append((join_lines(blk), blk))
    # Rotated badge text is often split into one block per word: also group nearby lines of one style.
    styled: dict[tuple, list[TextLine]] = defaultdict(list)
    for l in free:
        if len(l.text) <= 30:
            styled[(l.page, l.font, round((l.font_size or 0) * 2))].append(l)
    for group in styled.values():
        pool = list(group)
        while pool:
            cluster = [pool.pop(0)]
            grew = True
            while grew:
                grew = False
                for o in list(pool):
                    size = max(o.font_size or (o.bbox[3] - o.bbox[1]), 3.0)
                    for c in cluster:
                        dx = max(0.0, max(o.bbox[0], c.bbox[0]) - min(o.bbox[2], c.bbox[2]))
                        dy = max(0.0, max(o.bbox[1], c.bbox[1]) - min(o.bbox[3], c.bbox[3]))
                        if dx <= 0.8 * size and dy <= 0.8 * size:
                            cluster.append(o)
                            pool.remove(o)
                            grew = True
                            break
            key = tuple(sorted(l.id for l in cluster))
            if 2 <= len(cluster) <= 6 and key not in seen_groups:
                seen_groups.add(key)
                cluster = reading_order(cluster)
                out.append((join_lines(cluster), cluster))
    return out


def _claims(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    out = []
    seen: set[tuple[str, str]] = set()
    for text, lines in claim_texts(ctx.lines, ctx.structured):
        for pat in CLAIM_VOCAB:
            m = re.search(pat, text, re.I)
            if not m:
                continue
            key = (re.sub(r"\W+", "", m.group(0).lower()), lines[0].block or lines[0].id)
            if key in seen:
                continue
            seen.add(key)
            out.append(_fact("claims", lines, m.group(0), raw=text))
    return out


def _nospace(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s.casefold())


def _product_name(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    name = (approved or {}).get("product_name")
    pool = [l for l in ctx.lines if ctx.pdp_id is None or l.panel == ctx.pdp_id] or ctx.lines
    if name:
        target = _nospace(name)
        best, best_score = None, 0.0
        for i, l in enumerate(pool):
            cands = [[l]]
            nxt = [o for o in pool if o.block == l.block and o.bbox[1] > l.bbox[1]]
            if nxt:
                cands.append([l, min(nxt, key=lambda o: o.bbox[1])])
            for c in cands:
                s = fuzz.ratio(_nospace(" ".join(x.text for x in c)), target)
                if s > best_score:
                    best, best_score = c, s
        if best and best_score >= 70:
            return [_fact("product_name", best, join_lines(best), match_score=round(best_score, 1))]
        return []
    sized = [l for l in pool if len(l.text) > 3]
    if not sized:
        return []
    top = max(sized, key=lambda l: (l.font_size or (l.bbox[3] - l.bbox[1]) * 0.8))
    f = _fact("product_name", [top], top.text, method_note="largest text on front panel")
    f.confidence = round(f.confidence * 0.7, 3)
    return [f]


def _codes_marks(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    out = []
    for b in ctx.art.barcodes:
        key = "qr_code" if "QR" in b.format.upper() else "gtin"
        out.append(Fact(key=key, value=b.text, raw=f"{b.format}: {b.text}", line_ids=[], confidence=0.97,
                        method="vision", panel=b.panel,
                        attrs={"format": b.format, "checksum_ok": b.checksum_ok, "bbox": b.bbox, "page": b.page}))
    for s in ctx.art.symbols:
        out.append(Fact(key="food_mark", value=s.kind, raw=f"{s.kind.replace('_', ' ')} ({s.shape})", line_ids=[],
                        confidence=s.confidence, method="vision", panel=s.panel,
                        attrs={"bbox": s.bbox, "page": s.page, "shape": s.shape}))
    return out


KCAL_RE = re.compile(r"(?i)(\d+(?:[.,]\d+)?)\s*kcal\b")


def _energy_declarations(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    """Energy values printed outside the nutrition table (front-of-pack badges, claims)."""
    out = []
    for ln in ctx.lines:
        if ln.id in ctx.structured:
            continue
        for m in KCAL_RE.finditer(ln.text):
            out.append(_fact("energy_declaration", [ln], to_float(m.group(1)), raw=ln.text))
    return out


CERT_MARKS = [
    ("usda_organic", "USDA Organic seal", [r"usda"], [r"organic"]),
    ("certified_vegan", "Certified Vegan mark", [r"certified\s*vegan|vegan\.?\s*org|v\.org"], []),
    ("non_gmo_project", "Non-GMO Project mark", [r"non[\s-]*gmo\s*project"], []),
    ("recyclable", "recycling mark", [r"recyclable|how2recycle|recycle"], []),
    ("kosher", "kosher mark", [r"\bkosher\b"], []),
]


def _certifications(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    """Certification seals and marks, mostly read by OCR from inside the artwork's graphics."""
    out = []
    for key, name, first, near in CERT_MARKS:
        for ln in ctx.lines:
            if not any(re.search(p, ln.text, re.I) for p in first):
                continue
            lines = [ln]
            if near:
                h = max(1.0, ln.bbox[3] - ln.bbox[1])
                nb = [o for o in ctx.lines if o.id != ln.id and o.page == ln.page and
                      any(re.search(p, o.text, re.I) for p in near) and abs(o.bbox[0] - ln.bbox[0]) < 6 * h and
                      0 <= o.bbox[1] - ln.bbox[1] < 3 * h]
                if not nb and not any(re.search(p, ln.text, re.I) for p in near):
                    continue
                lines += nb[:1]
            f = _fact("certification", lines, key, raw=join_lines(lines), name=name)
            if any(l.graphic for l in lines):
                f.notes.append("Read by OCR from the artwork graphics (the mark has no text layer).")
            out.append(f)
            break
    return out


def _dietary(ctx: Ctx, approved: Optional[dict]) -> list[Fact]:
    out = []
    for ln in ctx.lines:
        if re.search(r"(?i)dietary\s*information|suitable\s*for\s*(vegans?|vegetarians?)|\bvegan\b", ln.text):
            out.append(_fact("dietary_statement", [ln], ln.text))
    return out


EXTRACTORS = [_ingredients, _allergens, _nutrition, _manufacturer, _customer_care, _net_quantity, _mrp, _fssai,
              _dates_batch, _product_name, _codes_marks, _energy_declarations, _certifications, _dietary, _claims]


def extract_facts(art: Artwork, approved: Optional[dict] = None) -> dict[str, list[Fact]]:
    ctx = Ctx(art)
    facts: dict[str, list[Fact]] = defaultdict(list)
    for fn in EXTRACTORS:
        for f in fn(ctx, approved):
            facts[f.key].append(f)
    return dict(facts)


def facts_for_display(facts: dict[str, list[Fact]]) -> list[dict]:
    rows = []
    for key, lst in facts.items():
        for f in lst:
            v = f.value
            if key == "nutrition":
                v = {k: v.get(k) for k in ("format", "per_100", "per_serve", "rda", "dv", "serving_size",
                                           "serving_size_raw", "servings_per_container") if v.get(k) not in (None, {})}
            elif isinstance(v, dict) and "text" in v:
                v = v["text"]
            elif isinstance(v, dict) and "amount" in v:
                v = f"{v['amount']:g} {v['unit']}"
            rows.append({"key": key, "label": FACT_LABELS.get(key, key), "value": v, "raw": f.raw,
                         "line_ids": f.line_ids, "confidence": f.confidence, "method": f.method, "panel": f.panel,
                         "notes": f.notes, "attrs": {k: a for k, a in f.attrs.items() if k not in ("conf",)}})
    return rows
