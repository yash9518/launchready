"""OCR for raster artwork (photos, scans, PDFs with outlined text).

Uses RapidOCR (PaddleOCR PP-OCRv3 models running on ONNX Runtime, bundled in
the pip wheel, so it works offline). Large images are processed in tiles so
small print is not lost to down-scaling.
"""
from __future__ import annotations

import math
import re
import statistics
import threading
from typing import Optional

import cv2
import numpy as np

from app.models import Span, TextLine
from app.util import bbox_iou

_engine = None
_lock = threading.Lock()
OCR_ERROR: Optional[str] = None


def get_engine():
    global _engine, OCR_ERROR
    if _engine is None and OCR_ERROR is None:
        with _lock:
            if _engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    _engine = RapidOCR()
                except Exception as exc:  # pragma: no cover - depends on platform
                    OCR_ERROR = f"OCR engine unavailable: {exc}"
    return _engine


def ocr_available() -> bool:
    return get_engine() is not None


def _run(engine, img: np.ndarray) -> list[dict]:
    res, _ = engine(img)
    out = []
    for box, text, score in res or []:
        pts = np.array(box, dtype=float)
        x0, y0 = pts[:, 0].min(), pts[:, 1].min()
        x1, y1 = pts[:, 0].max(), pts[:, 1].max()
        dx, dy = pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]
        angle = math.degrees(math.atan2(dy, dx)) if abs(dx) > 1 else 0.0
        out.append({"text": str(text).strip(), "bbox": [x0, y0, x1, y1], "conf": float(score),
                    "angle": angle, "w": x1 - x0, "h": y1 - y0})
    return [o for o in out if o["text"]]


def ocr_image(img: np.ndarray, tile: int = 2200, overlap: int = 220) -> list[dict]:
    """OCR an RGB uint8 image. Returns dicts with text, bbox (px), conf, angle."""
    engine = get_engine()
    if engine is None:
        return []
    h, w = img.shape[:2]
    if max(h, w) <= tile * 1.25:
        return _run(engine, img)
    results: list[dict] = []
    step = tile - overlap
    for ty in range(0, max(1, h - overlap), step):
        for tx in range(0, max(1, w - overlap), step):
            crop = img[ty:min(h, ty + tile), tx:min(w, tx + tile)]
            if crop.shape[0] < 32 or crop.shape[1] < 32:
                continue
            for r in _run(engine, crop):
                b = r["bbox"]
                r["bbox"] = [b[0] + tx, b[1] + ty, b[2] + tx, b[3] + ty]
                results.append(r)
    # De-duplicate detections from overlapping tiles: keep the longer/more confident one.
    results.sort(key=lambda r: (-len(r["text"]), -r["conf"]))
    kept: list[dict] = []
    for r in results:
        if any(bbox_iou(r["bbox"], k["bbox"]) > 0.3 for k in kept):
            continue
        kept.append(r)
    return kept


def _norm_tok(t: str) -> str:
    return re.sub(r"\s+", "", t).casefold()


def reread_uncertain(img: np.ndarray, results: list[dict], max_len: int = 14, conf_below: float = 0.88) -> list[dict]:
    """Second, independent OCR reading of short or low-confidence text (recogniser only, no rotation guess).

    Agreement raises confidence; disagreement keeps the item uncertain and records the alternative,
    so downstream checks send it to human review instead of raising a false alarm.
    """
    engine = get_engine()
    if engine is None or not results:
        return results
    crops, idx = [], []
    H, W = img.shape[:2]
    for i, r in enumerate(results):
        if r["conf"] >= 0.97 or len(r["text"]) > max_len:
            continue
        x0, y0, x1, y1 = r["bbox"]
        h = y1 - y0
        pad = int(0.22 * h) + 2
        crop = img[max(0, int(y0) - pad):min(H, int(y1) + pad), max(0, int(x0) - pad):min(W, int(x1) + pad)]
        if crop.size == 0 or crop.shape[0] < 6 or crop.shape[1] < 6:
            continue
        if crop.shape[0] < 48:
            f = 48.0 / crop.shape[0]
            crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        crops.append(np.ascontiguousarray(crop))
        idx.append(i)
    if not crops:
        return results
    try:
        rec, _ = engine.text_recognizer(crops)
    except Exception:
        return results
    for i, (text2, conf2) in zip(idx, rec):
        r = results[i]
        text2 = str(text2).strip()
        conf2 = float(conf2)
        if _norm_tok(text2) == _norm_tok(r["text"]):
            r["conf"] = max(r["conf"], conf2, 0.9)
            r["verified"] = True
        elif text2 and conf2 > r["conf"] + 0.12:
            r["alt"] = r["text"]
            r["text"] = text2
            r["conf"] = min(conf2, 0.8)
        else:
            r["alt"] = text2
            r["conf"] = min(r["conf"], 0.6)
    return results


def estimate_skew(results: list[dict]) -> float:
    """Median text-line angle in degrees (positive = clockwise in image coords)."""
    angles = [r["angle"] for r in results if r["w"] > 4 * r["h"] and abs(r["angle"]) < 15]
    if len(angles) < 3:
        return 0.0
    return float(statistics.median(angles))


def ocr_to_lines(results: list[dict], page_index: int, start_id: int, scale: float) -> list[TextLine]:
    """Convert OCR results (px) into TextLines in page units (px / scale)."""
    items = sorted(results, key=lambda r: (r["bbox"][1], r["bbox"][0]))
    lines: list[TextLine] = []
    n = start_id
    for r in items:
        n += 1
        b = [round(v / scale, 2) for v in r["bbox"]]
        lines.append(TextLine(
            id=f"L{n}", page=page_index, text=r["text"], bbox=b,
            font_size=None, bold=False, source="ocr", confidence=round(r["conf"], 3),
            spans=[Span(text=r["text"], bbox=b)], alt_text=r.get("alt"), verified=bool(r.get("verified")),
        ))
    assign_blocks(lines, page_index)
    return lines


def assign_blocks(lines: list[TextLine], page_index: int) -> None:
    """Group OCR lines into paragraph-like blocks (left-aligned, vertically adjacent)."""
    blocks: list[dict] = []
    for ln in sorted(lines, key=lambda l: (l.bbox[1], l.bbox[0])):
        h = max(1.0, ln.bbox[3] - ln.bbox[1])
        placed = False
        for blk in blocks:
            last = blk["last"]
            lh = max(1.0, last.bbox[3] - last.bbox[1])
            gap = ln.bbox[1] - last.bbox[3]
            aligned = abs(ln.bbox[0] - blk["x0"]) < 1.5 * max(h, lh)
            overlap = min(ln.bbox[2], last.bbox[2]) - max(ln.bbox[0], last.bbox[0])
            similar_size = 0.6 < h / lh < 1.6
            if -0.3 * h < gap < 0.9 * max(h, lh) and similar_size and (aligned or overlap > 0.5 * min(
                    ln.bbox[2] - ln.bbox[0], last.bbox[2] - last.bbox[0])):
                blk["last"] = ln
                ln.block = blk["id"]
                placed = True
                break
        if not placed:
            bid = f"p{page_index}o{len(blocks)}"
            blocks.append({"id": bid, "x0": ln.bbox[0], "last": ln})
            ln.block = bid
