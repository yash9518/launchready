"""Classical computer vision: panels, veg/non-veg marks, barcodes, physical text height.

These are deliberately deterministic: each returns geometry the UI can show as
evidence, plus a confidence that the verdict policy uses.
"""
from __future__ import annotations

import math
import re
import statistics
from typing import Optional

import cv2
import numpy as np
import pymupdf

from app.util import bbox_iou, gtin_valid

MM_PER_PT = 25.4 / 72.0

# ---------------------------------------------------------------- panels


def _cluster(values: list[float], tol: float) -> list[float]:
    out: list[list[float]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def grid_panels(region: list[float], xs: list[float], ys: list[float]) -> list[list[float]]:
    x0, y0, x1, y1 = region
    w, h = x1 - x0, y1 - y0
    xs = [x for x in _cluster(xs, 0.015 * w) if x0 + 0.04 * w < x < x1 - 0.04 * w]
    ys = [y for y in _cluster(ys, 0.015 * h) if y0 + 0.04 * h < y < y1 - 0.04 * h]
    cols = [x0] + xs + [x1]
    rows = [y0] + ys + [y1]
    cells = []
    for r in range(len(rows) - 1):
        for c in range(len(cols) - 1):
            cell = [cols[c], rows[r], cols[c + 1], rows[r + 1]]
            if (cell[2] - cell[0]) * (cell[3] - cell[1]) >= 0.04 * w * h:
                cells.append(cell)
    return cells or [region]


def pdf_panel_geometry(page: pymupdf.Page) -> tuple[list[float], list[float], list[float]]:
    """Return (region, vertical splits, horizontal splits) from dieline-like vector lines."""
    W, H = page.rect.width, page.rect.height
    region = [0.0, 0.0, W, H]
    xs: list[float] = []
    ys: list[float] = []
    borders: list[list[float]] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return region, xs, ys
    for path in drawings:
        for it in path.get("items", []):
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                if abs(p1.x - p2.x) < 1.0 and abs(p1.y - p2.y) > 0.5 * H:
                    xs.append((p1.x + p2.x) / 2)
                elif abs(p1.y - p2.y) < 1.0 and abs(p1.x - p2.x) > 0.5 * W:
                    ys.append((p1.y + p2.y) / 2)
            elif it[0] == "re":
                r = it[1]
                if r.width < 2.5 and r.height > 0.5 * H:
                    xs.append(r.x0 + r.width / 2)
                elif r.height < 2.5 and r.width > 0.5 * W:
                    ys.append(r.y0 + r.height / 2)
                elif path.get("fill") is None and r.width > 0.5 * W and r.height > 0.5 * H:
                    borders.append([r.x0, r.y0, r.x1, r.y1])
    if borders:
        # The largest stroked rectangle is the artwork / dieline boundary.
        b = max(borders, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
        if (b[2] - b[0]) * (b[3] - b[1]) < 0.98 * W * H:
            region = b
    return region, xs, ys


PANEL_LAYER = re.compile(r"(?i)artwork\s*panel|^panels?$|safe\s*(area|zone)|live\s*area|panel\s*outline")
STRUCTURE_LAYER = re.compile(r"(?i)crease|\bfold|\bcut\b|cutting|die[\s_-]?line|\bperf")


def _layer_regions(page: pymupdf.Page, layer_rx: re.Pattern, min_frac: float = 0.004) -> list[list[float]]:
    """Closed outlines drawn on matching layers -> enclosed regions (page units).

    Outlines on dieline layers are usually many separate line segments, so they are
    rasterised and the enclosed areas found as holes in the drawing.
    """
    W, H = page.rect.width, page.rect.height
    s = min(1.5, 2400.0 / max(W, H))
    mask = np.zeros((int(H * s) + 4, int(W * s) + 4), np.uint8)
    n = 0
    for d in page.get_drawings():
        if not layer_rx.search(d.get("layer") or ""):
            continue
        for it in d.get("items", []):
            pts = []
            if it[0] == "l":
                pts = [it[1], it[2]]
            elif it[0] == "c":
                pts = [it[1], it[2], it[3], it[4]]
            elif it[0] == "re":
                r = it[1]
                pts = [r.tl, r.tr, r.br, r.bl, r.tl]
            elif it[0] == "qu":
                q = it[1]
                pts = [q.ul, q.ur, q.lr, q.ll, q.ul]
            for a, b in zip(pts, pts[1:]):
                cv2.line(mask, (int(a.x * s) + 2, int(a.y * s) + 2), (int(b.x * s) + 2, int(b.y * s) + 2), 255, 2)
                n += 1
    if n < 4:
        return []
    contours, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    out: list[list[float]] = []
    if hier is None:
        return out
    for c, h in zip(contours, hier[0]):
        if h[3] == -1:  # outer outline, not an enclosed area
            continue
        x, y, w, hh = cv2.boundingRect(c)
        if cv2.contourArea(c) < min_frac * mask.shape[0] * mask.shape[1]:
            continue
        box = [(x - 2) / s, (y - 2) / s, (x + w - 2) / s, (y + hh - 2) / s]
        if not any(bbox_iou(box, o) > 0.8 for o in out):
            out.append([round(v, 2) for v in box])
    return out


def pdf_layer_panels(page: pymupdf.Page) -> tuple[list[list[float]], str]:
    """Panels of a carton/label from its dieline layers, if the file has them."""
    try:
        regions = _layer_regions(page, PANEL_LAYER)
        if len(regions) >= 2:
            return regions, "artwork-panel layer"
        regions = _layer_regions(page, STRUCTURE_LAYER)
        if len(regions) >= 2:
            return regions, "cut and crease lines"
    except Exception:
        pass
    return [], ""


def raster_panel_splits(img: np.ndarray) -> list[float]:
    """Thin, long vertical lines (fold / dieline) in a raster image -> x positions in px."""
    h, w = img.shape[:2]
    if h < 200 or w < 200:
        return []
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    # Thin coloured fold lines lose saturation in photos (blur, noise), so use a soft threshold,
    # tolerate 1-px wobble left/right and close tiny vertical gaps before requiring a long run.
    mask = (((gray < 170) | (hsv[..., 1] > 60)) * 255).astype(np.uint8)
    mask = cv2.dilate(mask, np.ones((1, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 7)))
    k = max(25, int(0.5 * h))
    vert = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
    colsum = (vert > 0).sum(axis=0)
    cols = np.where(colsum > 0.5 * h)[0]
    xs: list[float] = []
    if len(cols) == 0:
        return xs
    start = prev = int(cols[0])
    for c in list(cols[1:]) + [None]:
        if c is not None and c == prev + 1:
            prev = int(c)
            continue
        width = prev - start + 1
        if width <= max(4, int(0.006 * w)):
            xs.append((start + prev) / 2.0)
        if c is not None:
            start = prev = int(c)
    return xs


# ---------------------------------------------------------------- veg / non-veg marks

def detect_food_marks(img: np.ndarray) -> list[dict]:
    """Find FSSAI-style veg (green square + green dot) and non-veg (brown square + triangle) marks."""
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    H, S, V = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    masks = {
        "veg_mark": (H >= 35) & (H <= 90) & (S >= 70) & (V >= 45),
        "nonveg_mark": (((H >= 3) & (H <= 24) & (V <= 200)) | (H >= 172) | (H <= 2)) & (S >= 90) & (V >= 35),
    }
    found: list[dict] = []
    min_side = max(8, int(0.004 * max(img.shape[:2])))
    for kind, m in masks.items():
        mask = (m * 255).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        squares, shapes = [], []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w < 4 or h < 4:
                continue
            area = cv2.contourArea(c)
            peri = cv2.arcLength(c, True) or 1.0
            approx = cv2.approxPolyDP(c, 0.04 * peri, True)
            fill = cv2.countNonZero(mask[y:y + h, x:x + w]) / float(w * h)
            if len(approx) == 4 and 0.8 < w / h < 1.25 and w >= min_side and fill < 0.75:
                squares.append((x, y, w, h))
            if area > 6:
                circ = 4 * math.pi * area / (peri * peri)
                shapes.append((x, y, w, h, circ, len(approx), area / float(w * h)))
        for sx, sy, sw, sh in squares:
            for x, y, w, h, circ, nv, solidity in shapes:
                cx, cy = x + w / 2, y + h / 2
                if not (sx + 0.3 * sw < cx < sx + 0.7 * sw and sy + 0.3 * sh < cy < sy + 0.7 * sh):
                    continue
                ratio = (w * h) / float(sw * sh)
                if not (0.06 < ratio < 0.55):
                    continue
                if circ > 0.72 and solidity > 0.6:
                    shape = "circle"
                elif nv == 3 and 0.35 < solidity < 0.7:
                    shape = "triangle"
                else:
                    continue
                if kind == "veg_mark" and shape != "circle":
                    continue
                conf = 0.9 if (kind == "veg_mark" or shape == "triangle") else 0.75
                cand = {"kind": kind, "bbox": [sx, sy, sx + sw, sy + sh], "confidence": conf, "shape": shape}
                if not any(bbox_iou(cand["bbox"], f["bbox"]) > 0.3 for f in found):
                    found.append(cand)
    return found


# ---------------------------------------------------------------- barcodes

def detect_barcodes(img: np.ndarray) -> list[dict]:
    try:
        import zxingcpp
    except Exception:
        return []
    out = []
    try:
        results = zxingcpp.read_barcodes(img)
    except Exception:
        return []
    for r in results:
        fmt = getattr(r.format, "name", str(r.format)).replace("BarcodeFormat.", "")
        pos = r.position
        pts = [pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left]
        xs, ys = [p.x for p in pts], [p.y for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if (y1 - y0) < 0.08 * (x1 - x0):  # 1D codes report a scan line; give it a visible height
            cy, half = (y0 + y1) / 2, 0.3 * (x1 - x0)
            y0, y1 = cy - half, cy + half
        text = r.text
        retail = fmt.upper().replace("-", "") in ("EAN13", "EAN8", "UPCA", "UPCE", "EANUPC")
        out.append({"text": text, "format": fmt, "bbox": [x0, y0, x1, y1],
                    "checksum_ok": gtin_valid(text) if retail else None})
    return out


# ---------------------------------------------------------------- physical text height

def _ink_rows_height(gray: np.ndarray) -> Optional[int]:
    if gray.size == 0 or gray.max() - gray.min() < 30:
        return None
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = th == 0
    if ink.mean() > 0.5:
        ink = ~ink
    rows = np.where(ink.any(axis=1))[0]
    if len(rows) == 0:
        return None
    return int(rows[-1] - rows[0] + 1)


def measure_pdf_char_height_mm(page: pymupdf.Page, bbox: list[float]) -> Optional[dict]:
    """Height of digits (or capitals) in a vector text line, measured from the rendered glyphs."""
    rect = pymupdf.Rect(bbox) + (-1, -1, 1, 1)
    raw = page.get_text("rawdict", clip=rect)
    chars = [c for b in raw.get("blocks", []) for ln in b.get("lines", []) for s in ln.get("spans", [])
             for c in s.get("chars", [])]
    digits = [c for c in chars if c["c"].isdigit()]
    target = digits or [c for c in chars if c["c"].isupper()]
    if not target:
        return None
    zoom = 1200 / 72.0
    heights = []
    for c in target[:12]:
        crect = pymupdf.Rect(c["bbox"]) + (-0.5, -0.5, 0.5, 0.5)
        try:
            pix = page.get_pixmap(clip=crect, matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY,
                                  alpha=False)
        except Exception:
            continue
        arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
        hpx = _ink_rows_height(arr)
        if hpx:
            heights.append(hpx / zoom * MM_PER_PT)
    if not heights:
        return None
    return {"mm": round(max(heights), 2), "method": "glyph ink height (vector render, 1200 dpi)",
            "chars": "digits" if digits else "capitals"}


def measure_raster_text_height_px(gray: np.ndarray, bbox_px: list[float]) -> Optional[float]:
    """Approximate cap/digit height in px inside an OCR text box, using connected components."""
    x0, y0, x1, y1 = [int(round(v)) for v in bbox_px]
    pad = max(1, int(0.1 * (y1 - y0)))
    crop = gray[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
    if crop.size == 0 or crop.max() - crop.min() < 30:
        return None
    _, th = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = (th == 0)
    if ink.mean() > 0.5:
        ink = ~ink
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    heights = [stats[i, cv2.CC_STAT_HEIGHT] for i in range(1, n)
               if stats[i, cv2.CC_STAT_AREA] > 4 and stats[i, cv2.CC_STAT_WIDTH] > 1
               and stats[i, cv2.CC_STAT_HEIGHT] < 0.98 * crop.shape[0]]
    if not heights:
        return None
    hmax = max(heights)
    tall = [h for h in heights if h >= 0.6 * hmax]
    return float(statistics.median(tall))
