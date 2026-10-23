"""Exact text extraction from vector PDFs (no OCR): text, position, size, weight, layer, visibility.

Real print-ready packaging PDFs are messier than office documents, so besides reading
the text layer this module repairs and classifies it:

* layers   - text on dieline / proof layers ("Front panel", "Art orientation", dimensions)
             is marked `technical`: it does not print and must not count as label copy.
* hidden   - text that a later, opaque object paints over (an old table left under a new
             one) or invisible text is marked `hidden`: it is in the file but not on the pack.
* spacing  - letter-spaced (tracked) words such as "H I G H - G R A I N" are rebuilt
             from character positions.
* arcs     - words set on a curve are stored as one letter per text line; neighbouring
             letters are joined back into words ("G R A N O L A" -> "GRANOLA").
* doubles  - outline effects repeat the same text two or three times; duplicates are dropped.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from typing import Optional

import pymupdf

from app.models import Span, TextLine

TECH_LAYER = re.compile(r"(?i)dimension|die[\s_-]?line|\bcut\b|cutting|crease|\bfold|glue|technical|\btech\b|"
                        r"annotation|\bnotes?\b|guide|bleed|\bsafe|\b3d\b|structur|\.ard\b|varnish|coating|"
                        r"ink[\s_-]?free|\bother\b|\binfo\b|artwork\s*panel|measure")
TECH_TEXT = re.compile(r"(?i)^\s*(art\s*orientation|orientation|copy\s*free|(front|back|left|right|top|bottom|side|"
                       r"base|end)\s*(panel|flap)|principal\s*display\s*panel|glue(\s*(area|flap|zone))?|gl\d|"
                       r"bleed|safe\s*(area|zone)|non[\s-]*print\w*|die\s*line|cut\s*line|crease|"
                       r"\d{1,2},\d[A-Z]\d[A-Z]\d[A-Z]\d[A-Z]\d\s*\d[A-Z]\d)\s*$")
PANEL_WORDS = re.compile(r"(?i)\b(principal\s*display\s*panel|(front|back|left|right|top|bottom|side|base)\s*panel)\b")


def _hex(color_int: int) -> str:
    return f"#{color_int:06x}"


def _is_bold(span: dict) -> bool:
    font = (span.get("font") or "").lower()
    return bool(span.get("flags", 0) & 16) or any(w in font for w in ("bold", "black", "heavy", "semibold", "demi"))


def _span_text(span: dict) -> str:
    if "chars" in span:
        return "".join(c["c"] for c in span["chars"])
    return span.get("text", "")


# ---------------------------------------------------------------- spacing repair

def _looks_letter_spaced(text: str) -> bool:
    toks = text.split()
    singles = [t for t in toks if len(t) == 1 and t.isalnum()]
    return len(singles) >= 3 and len(singles) >= 0.4 * len(toks)


def _respace(spans: list[dict], size: float) -> Optional[str]:
    """Rebuild words of a tracked (letter-spaced) line from character positions."""
    chars = [c for s in spans for c in s.get("chars", []) if c["c"].strip()]
    if len(chars) < 3:
        return None
    chars.sort(key=lambda c: c["bbox"][0])
    gaps = [b["bbox"][0] - a["bbox"][2] for a, b in zip(chars, chars[1:])]
    if not gaps:
        return None
    med = statistics.median(gaps)
    thr = med + 0.22 * max(size, 1.0)
    out = chars[0]["c"]
    for g, c in zip(gaps, chars[1:]):
        out += (" " if g > thr else "") + c["c"]
    return out


# ---------------------------------------------------------------- layers and visibility

def _char_key(origin, c: str) -> tuple:
    return (round(origin[0], 1), round(origin[1], 1), c)


def _paint_index(page: pymupdf.Page) -> dict[tuple, list[tuple[Optional[str], bool]]]:
    """Map every painted character (origin + glyph) to (layer, covered?).

    A character is *covered* when it is invisible text, or when an opaque filled shape
    on an artwork layer is painted later (higher sequence number) over its whole span.
    """
    fills = []
    for d in page.get_drawings():
        if d.get("fill") is None or float(d.get("fill_opacity") or 1.0) < 0.95:
            continue
        layer = d.get("layer")
        if layer and TECH_LAYER.search(layer):
            continue
        r = d["rect"]
        if r.width > 2 and r.height > 2:
            fills.append((d.get("seqno", -1), r))
    fills.sort(key=lambda f: f[0])
    index: dict[tuple, list[tuple[Optional[str], bool]]] = defaultdict(list)
    for s in page.get_texttrace():
        seq = s.get("seqno", -1)
        box = pymupdf.Rect(s["bbox"]) + (-0.5, -0.5, 0.5, 0.5)
        covered = s.get("type") == 3 or float(s.get("opacity", 1.0)) == 0.0 or \
            any(fs > seq and fr.contains(box) for fs, fr in fills)
        for ch in s.get("chars", []):
            index[_char_key(ch[2], chr(ch[0]))].append((s.get("layer"), covered))
    return index


def _classify(items: list[tuple[TextLine, list[tuple]]], page: pymupdf.Page) -> None:
    try:
        index = _paint_index(page)
    except Exception:
        index = {}
    def lookup(k: tuple) -> list:
        hit = index.get(k)
        if hit:
            return hit
        x, y, c = k
        for dx in (-0.1, 0.1, 0.0):
            for dy in (-0.1, 0.1, 0.0):
                hit = index.get((round(x + dx, 1), round(y + dy, 1), c))
                if hit:
                    return hit
        return []

    for ln, keys in items:
        layers: Counter = Counter()
        covered: list[bool] = []
        for k in keys:
            for layer, cov in lookup(k):
                if layer:
                    layers[layer] += 1
                covered.append(cov)
        if layers:
            ln.layer = layers.most_common(1)[0][0]
        if covered and all(covered):
            ln.hidden = True
        if (ln.layer and TECH_LAYER.search(ln.layer)) or TECH_TEXT.match(ln.text):
            ln.technical = True


# ---------------------------------------------------------------- duplicates and arched letters

def _dedupe(items: list[tuple[TextLine, list]]) -> list[tuple[TextLine, list]]:
    out: list[tuple[TextLine, list]] = []
    for ln, keys in items:
        pos = None
        for k in range(max(0, len(out) - 60), len(out)):
            o = out[k][0]
            if o.text == ln.text and _iou(o.bbox, ln.bbox) > 0.8:
                pos = k
                break
        if pos is None:
            out.append((ln, keys))
            continue
        o, okeys = out[pos]
        # Keep one copy; prefer the coloured fill over a white outline, and remember all glyphs.
        keep = ln if (o.color or "").lower() == "#ffffff" and (ln.color or "").lower() != "#ffffff" else o
        out[pos] = (keep, okeys + keys)
    return out


def _iou(a: list[float], b: list[float]) -> float:
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _center(b: list[float]) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _merge_letters(lines: list[TextLine]) -> list[TextLine]:
    """Join single letters placed one by one (curved or stacked headings) back into words."""
    singles = [ln for ln in lines if len(ln.text.strip()) == 1 and ln.text.strip().isalnum()]
    if len(singles) < 3:
        return lines
    groups: dict[tuple, list[TextLine]] = defaultdict(list)
    for ln in singles:
        groups[(ln.page, ln.font, round((ln.font_size or 0) / 2))].append(ln)
    used: set[int] = set()
    merged: list[TextLine] = []
    for members in groups.values():
        pool = list(members)
        while pool:
            seed = pool.pop(0)
            cluster = [seed]
            changed = True
            while changed:
                changed = False
                for o in list(pool):
                    size = max(o.font_size or 8.0, 4.0)
                    ox, oy = _center(o.bbox)
                    if any(math.dist((ox, oy), _center(c.bbox)) <= 1.75 * size for c in cluster):
                        cluster.append(o)
                        pool.remove(o)
                        changed = True
            if len(cluster) < 3:
                continue
            pts = [_center(c.bbox) for c in cluster]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            horizontal = (max(xs) - min(xs)) >= (max(ys) - min(ys))
            cluster.sort(key=lambda c: _center(c.bbox)[0] if horizontal else _center(c.bbox)[1])
            dists = [math.dist(_center(a.bbox), _center(b.bbox)) for a, b in zip(cluster, cluster[1:])]
            med = statistics.median(dists) if dists else 0.0
            text = cluster[0].text.strip()
            for d, c in zip(dists, cluster[1:]):
                text += (" " if med and d > 1.45 * med else "") + c.text.strip()
            bbox = [min(c.bbox[0] for c in cluster), min(c.bbox[1] for c in cluster),
                    max(c.bbox[2] for c in cluster), max(c.bbox[3] for c in cluster)]
            first = cluster[0]
            merged.append(TextLine(id="", page=first.page, text=text, bbox=[round(v, 2) for v in bbox],
                                   font_size=first.font_size, font=first.font, bold=first.bold, color=first.color,
                                   source="pdf", confidence=1.0, block=f"arc{len(merged)}", layer=first.layer,
                                   technical=all(c.technical for c in cluster), hidden=all(c.hidden for c in cluster),
                                   merged=True, spans=[Span(text=text, bbox=[round(v, 2) for v in bbox])]))
            used.update(id(c) for c in cluster)
    if not merged:
        return lines
    return [ln for ln in lines if id(ln) not in used] + merged


# ---------------------------------------------------------------- main entry

def pdf_page_lines(page: pymupdf.Page, page_index: int, start_id: int) -> list[TextLine]:
    flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    data = page.get_text("rawdict", flags=flags)
    items: list[tuple[TextLine, list]] = []
    for bi, block in enumerate(data.get("blocks", [])):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if _span_text(s).strip()]
            if not spans:
                continue
            text = re.sub(r"\s+", " ", "".join(_span_text(s) for s in line["spans"])).strip()
            if not text:
                continue
            sizes = [s.get("size", 0) for s in spans]
            if _looks_letter_spaced(text):
                fixed = _respace(spans, statistics.median(sizes))
                if fixed:
                    text = fixed
            weights = [len(_span_text(s).strip()) for s in spans]
            bold_chars = sum(w for s, w in zip(spans, weights) if _is_bold(s))
            dominant = max(spans, key=lambda s: len(_span_text(s).strip()))
            keys = [_char_key(c["origin"], c["c"]) for s in spans for c in s.get("chars", []) if c["c"].strip()]
            items.append((TextLine(
                id="", page=page_index, text=text,
                bbox=[round(v, 2) for v in line["bbox"]],
                font_size=round(max(sizes), 2),
                font=dominant.get("font"),
                bold=bold_chars >= 0.9 * max(1, sum(weights)),
                color=_hex(dominant.get("color", 0)),
                source="pdf", confidence=1.0, block=f"p{page_index}b{bi}",
                spans=[Span(text=_span_text(s).strip(), bbox=[round(v, 2) for v in s["bbox"]]) for s in spans],
            ), keys))
    items = _dedupe(items)
    _classify(items, page)
    lines = _merge_letters([ln for ln, _ in items])
    n = start_id
    for ln in lines:
        n += 1
        ln.id = f"L{n}"
    return lines


def text_layer_usable(lines: list[TextLine]) -> bool:
    """Decide whether the PDF text layer is real text (vs. outlined/garbled fonts)."""
    lines = [ln for ln in lines if not ln.technical and not ln.hidden]
    if len(lines) < 3:
        return False
    text = "".join(ln.text for ln in lines)
    if len(text) < 30:
        return False
    good = sum(ch.isalnum() or ch in " .,:;%()-/&'\"+" for ch in text)
    return good / max(1, len(text)) > 0.75


def panel_labels(lines: list[TextLine]) -> list[tuple[str, list[float], int]]:
    """Panel names written on the dieline layer: (name, bbox, page)."""
    out = []
    for ln in lines:
        if not ln.technical:
            continue
        m = PANEL_WORDS.search(ln.text)
        if m:
            out.append((re.sub(r"\s+", " ", m.group(1)).strip().lower(), ln.bbox, ln.page))
    return out
