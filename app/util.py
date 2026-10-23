"""Small, dependency-light helpers: text normalisation, units, GTIN, diffs, boxes."""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Iterable, Optional

from rapidfuzz import fuzz

# ---------------------------------------------------------------- text

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}


def norm_space(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.translate(_DASHES).translate(_QUOTES)
    return re.sub(r"\s+", " ", s).strip()


def norm_cmp(s: str) -> str:
    """Normalisation used for 'is this the same text?' comparisons.

    Case, whitespace and spacing around punctuation are not considered
    meaningful differences; words, numbers and punctuation themselves are.
    """
    s = norm_space(s).casefold()
    s = re.sub(r"\s*([,;:()\[\]/])\s*", r"\1", s)
    s = re.sub(r"\s*\.\s*$", "", s)
    return s.strip()


def strip_label(text: str, label_regex: str) -> str:
    return re.sub(label_regex, "", text, count=1, flags=re.I).strip(" :-–")


def fuzzy_partial(a: str, b: str) -> float:
    return fuzz.partial_ratio(norm_cmp(a), norm_cmp(b))


def fuzzy_ratio(a: str, b: str) -> float:
    return fuzz.ratio(norm_cmp(a), norm_cmp(b))


def split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split on `sep` ignoring separators inside (), [] and {}."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [p.strip() for p in out if p.strip()]


# Characters OCR engines commonly confuse. Used to decide whether a text
# mismatch on a raster artwork could be an OCR misread rather than an error.
_CONFUSABLE = [set("il1|!I"), set("o0O"), set("s5S"), set("z2Z"), set("b6"), set("g9q"), set(",."),
               set("()[]{}"), set("-_"), set("'\"`")]


def _confusable(a: str, b: str) -> bool:
    if a == b:
        return True
    return any(a in grp and b in grp for grp in _CONFUSABLE)


def ocr_plausible_misread(expected: str, found: str, max_edits: int = 3) -> bool:
    """True if `found` differs from `expected` only in ways an OCR engine plausibly produces."""
    a, b = norm_cmp(expected), norm_cmp(found)
    if a == b:
        return True
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    edits = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        seg_a, seg_b = a[i1:i2], b[j1:j2]
        if op == "replace" and len(seg_a) == len(seg_b) and all(_confusable(x, y) for x, y in zip(seg_a, seg_b)):
            edits += len(seg_a) * 0.5
            continue
        # Digits changing is never treated as a misread: numbers are what matter.
        if re.search(r"\d", seg_a + seg_b):
            return False
        edits += max(len(seg_a), len(seg_b))
    return edits <= max_edits


def word_diff(expected: str, found: str) -> list[dict[str, str]]:
    """Word-level diff for the UI: list of {op, text} with op in equal|delete|insert."""
    a = norm_space(expected).split(" ")
    b = norm_space(found).split(" ")
    out: list[dict[str, str]] = []
    sm = difflib.SequenceMatcher(None, [w.casefold() for w in a], [w.casefold() for w in b], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.append({"op": "equal", "text": " ".join(b[j1:j2])})
        else:
            if i2 > i1:
                out.append({"op": "delete", "text": " ".join(a[i1:i2])})
            if j2 > j1:
                out.append({"op": "insert", "text": " ".join(b[j1:j2])})
    return out


# ---------------------------------------------------------------- numbers & units

NUM = r"(\d+(?:[.,]\d+)?)"


def to_float(s: str) -> Optional[float]:
    if s is None:
        return None
    s = str(s).strip().replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


_UNIT_MAP = {
    "g": ("g", 1.0), "gm": ("g", 1.0), "gms": ("g", 1.0), "gram": ("g", 1.0), "grams": ("g", 1.0), "gr": ("g", 1.0),
    "kg": ("g", 1000.0), "kgs": ("g", 1000.0), "kilogram": ("g", 1000.0), "kilograms": ("g", 1000.0),
    "mg": ("g", 0.001),
    "ml": ("ml", 1.0), "millilitre": ("ml", 1.0), "milliliter": ("ml", 1.0),
    "l": ("ml", 1000.0), "lt": ("ml", 1000.0), "ltr": ("ml", 1000.0), "litre": ("ml", 1000.0), "liter": ("ml", 1000.0),
    "litres": ("ml", 1000.0), "liters": ("ml", 1000.0),
    # US customary units (21 CFR 101.105 requires them next to the metric quantity)
    "oz": ("g", 28.349523125), "ozs": ("g", 28.349523125), "ounce": ("g", 28.349523125),
    "ounces": ("g", 28.349523125), "lb": ("g", 453.59237), "lbs": ("g", 453.59237), "pound": ("g", 453.59237),
    "pounds": ("g", 453.59237), "floz": ("ml", 29.5735295625),
}
US_UNITS = {"oz", "ozs", "ounce", "ounces", "lb", "lbs", "pound", "pounds", "floz"}
METRIC_UNITS = {"g", "gm", "gms", "gram", "grams", "gr", "kg", "kgs", "kilogram", "kilograms", "ml", "millilitre",
                "milliliter", "l", "lt", "ltr", "litre", "liter", "litres", "liters"}
UNIT_RE = (r"(fl\.?\s*oz|kgs?|kilograms?|grams?|gms?|gr|g|mg|ml|millilit(?:re|er)s?|lit(?:re|er)s?|ltr|lt|l|"
           r"ounces?|ozs?|lbs?|pounds?)")


def _unit_key(unit: str) -> str:
    return re.sub(r"[\s.]", "", unit.lower())


def parse_quantities(text: str) -> list[dict]:
    """Every quantity in a text, e.g. 'NET WT 16.9 OZ (480g)' -> [16.9 oz, 480 g]."""
    out = []
    for m in re.finditer(NUM + r"\s*" + UNIT_RE + r"(?![a-z])", text or "", re.I):
        amount = float(m.group(1).replace(",", "."))
        unit = _unit_key(m.group(2))
        base_unit, factor = _UNIT_MAP.get(unit, (unit, 1.0))
        out.append({"amount": amount, "unit": unit, "base": round(amount * factor, 6), "base_unit": base_unit,
                    "text": m.group(0), "system": "us" if unit in US_UNITS else "metric"})
    return out


def parse_quantity(text: str) -> Optional[dict]:
    """The declared quantity; when both US and metric units are given, the metric one is used for comparisons."""
    qs = parse_quantities(text)
    if not qs:
        return None
    metric = [q for q in qs if q["system"] == "metric"]
    q = dict(metric[0] if metric else qs[0])
    q["parts"] = [{k: v for k, v in p.items() if k != "parts"} for p in qs]
    return q


def quantity_str(q: Optional[dict]) -> str:
    if not q:
        return "—"
    parts = q.get("parts") or [q]
    unit_show = {"floz": "fl oz"}
    return " / ".join(f"{p['amount']:g} {unit_show.get(p['unit'], p['unit'])}" for p in parts)


# ---------------------------------------------------------------- GTIN

def gtin_check_digit(body: str) -> int:
    """Check digit for a GTIN body (all digits except the last)."""
    digits = [int(c) for c in body]
    total = 0
    for i, d in enumerate(reversed(digits)):
        total += d * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def gtin_valid(code: str) -> bool:
    code = re.sub(r"\D", "", code or "")
    if len(code) not in (8, 12, 13, 14):
        return False
    return gtin_check_digit(code[:-1]) == int(code[-1])


# ---------------------------------------------------------------- boxes

def bbox_union(boxes: Iterable[list[float]]) -> Optional[list[float]]:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def bbox_center(b: list[float]) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def bbox_contains_point(b: list[float], x: float, y: float) -> bool:
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def bbox_iou(a: list[float], b: list[float]) -> float:
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def bbox_height(b: list[float]) -> float:
    return b[3] - b[1]


def fmt_num(x: Optional[float], unit: str = "") -> str:
    if x is None:
        return "—"
    s = f"{x:.2f}".rstrip("0").rstrip(".") if abs(x) < 1000 else f"{x:.0f}"
    return f"{s} {unit}".strip()
