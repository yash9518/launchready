"""Turn an uploaded artwork file (PDF or image) into an `Artwork`.

PDF with a real text layer  -> exact text + font sizes ("vector" quality)
PDF with outlined text      -> rendered and OCR'd ("raster" quality)
Image (PNG/JPG/...)         -> deskewed and OCR'd ("raster" quality)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps

from app.extract import ocr as ocr_mod
from app.extract.pdf_text import panel_labels, pdf_page_lines, text_layer_usable
from app.extract.vision import (MM_PER_PT, detect_barcodes, detect_food_marks, grid_panels, pdf_layer_panels,
                                pdf_panel_geometry, raster_panel_splits)
from app.models import Artwork, Barcode, PageInfo, Panel, Symbol, TextLine
from app.util import bbox_center, bbox_contains_point

log = logging.getLogger(__name__)

DISPLAY_DPI = 150
ANALYSIS_DPI = 300
MAX_PAGES = 6
MAX_DISPLAY_PX = 3600
MAX_ANALYSIS_PX = 7000
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}


def _pix_to_np(pix: pymupdf.Pixmap) -> np.ndarray:
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[..., :3]
    elif pix.n == 1:
        arr = np.repeat(arr, 3, axis=2)
    return np.ascontiguousarray(arr)


def load_artwork(path: Path, outdir: Path, filename: Optional[str] = None) -> Artwork:
    outdir.mkdir(parents=True, exist_ok=True)
    filename = filename or path.name
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return _load_image(path, outdir, filename)
    try:
        return _load_pdf(path, outdir, filename)
    except Exception as exc:
        raise ValueError(f"Could not open '{filename}' as a PDF or image ({exc}).") from exc


# ---------------------------------------------------------------- PDF

def _load_pdf(path: Path, outdir: Path, filename: str) -> Artwork:
    doc = pymupdf.open(path)
    pages: list[PageInfo] = []
    lines: list[TextLine] = []
    panels: list[Panel] = []
    symbols: list[Symbol] = []
    barcodes: list[Barcode] = []
    warnings: list[str] = []
    regions: dict[int, list[float]] = {}
    meta: dict = {}
    any_ocr = False
    for pi, page in enumerate(doc):
        if pi >= MAX_PAGES:
            warnings.append(f"Only the first {MAX_PAGES} pages were checked.")
            break
        if page.rotation:
            try:
                page.remove_rotation()
            except Exception:
                pass
        W, H = page.rect.width, page.rect.height
        zoom = min(DISPLAY_DPI / 72.0, MAX_DISPLAY_PX / max(W, H))
        disp = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        img_name = f"page{pi}.png"
        disp.save(str(outdir / img_name))
        disp_np = _pix_to_np(disp)

        az = min(ANALYSIS_DPI / 72.0, MAX_ANALYSIS_PX / max(W, H))
        analysis = _pix_to_np(page.get_pixmap(matrix=pymupdf.Matrix(az, az), alpha=False))

        plines = pdf_page_lines(page, pi, len(lines))
        source = "pdf"
        if not text_layer_usable(plines):
            if ocr_mod.ocr_available():
                res = ocr_mod.reread_uncertain(analysis, ocr_mod.ocr_image(analysis))
                tech = [l for l in plines if l.technical]
                plines = ocr_mod.ocr_to_lines(res, pi, len(lines), scale=az)
                source = "ocr"
                any_ocr = True
                warnings.append(f"Page {pi + 1}: no usable text layer (fonts outlined?) — text was read by OCR.")
                plines += tech
            else:
                source = "none"
                warnings.append(f"Page {pi + 1}: no text layer and OCR is unavailable ({ocr_mod.OCR_ERROR}).")
                plines = []
        elif _rich_graphics(page) and ocr_mod.ocr_available():
            # Seals, logos and barcode digits are often vector art or images without a text layer:
            # read them with OCR so certification marks and codes can be checked too.
            extra = _graphic_text(disp_np, zoom, plines, pi, len(lines) + len(plines))
            plines += extra
            if extra:
                meta.setdefault("graphic_text", 0)
                meta["graphic_text"] += len(extra)
        hidden = [l for l in plines if l.hidden]
        if hidden:
            warnings.append(f"Page {pi + 1}: {len(hidden)} text line(s) are in the file but covered by other artwork "
                            f"(they will not print) — excluded from the checks.")
        tech = [l for l in plines if l.technical]
        if tech:
            meta.setdefault("technical_lines", 0)
            meta["technical_lines"] += len(tech)
        lines.extend(plines)

        layer_panels, how = pdf_layer_panels(page)
        if layer_panels:
            regions[pi] = [0.0, 0.0, W, H]
            for k, cell in enumerate(sorted(layer_panels, key=lambda b: (round(b[1] / 40), b[0]))):
                panels.append(Panel(id=f"P{pi + 1}.{k + 1}", page=pi, bbox=cell, source=how))
            meta["panels_from"] = how
        else:
            region, xs, ys = pdf_panel_geometry(page)
            regions[pi] = region
            for k, cell in enumerate(grid_panels(region, xs, ys)):
                panels.append(Panel(id=f"P{pi + 1}.{k + 1}", page=pi, bbox=[round(v, 2) for v in cell],
                                    source="fold lines" if (xs or ys) else "whole page"))

        for m in detect_food_marks(disp_np):
            symbols.append(Symbol(kind=m["kind"], page=pi, bbox=[round(v / zoom, 2) for v in m["bbox"]],
                                  confidence=m["confidence"], shape=m["shape"]))
        found = detect_barcodes(analysis)
        scale = az
        if not found and max(W, H) * az * 2 <= 12000:
            hi = 2 * az
            found = detect_barcodes(_pix_to_np(page.get_pixmap(matrix=pymupdf.Matrix(hi, hi), alpha=False)))
            scale = hi
        for b in found:
            barcodes.append(Barcode(text=b["text"], format=b["format"], page=pi,
                                    bbox=[round(v / scale, 2) for v in b["bbox"]], checksum_ok=b["checksum_ok"]))
        pages.append(PageInfo(index=pi, width=W, height=H, unit="pt", mm_per_unit=MM_PER_PT, image=img_name,
                              render_scale=zoom, text_source=source))
    for n, ln in enumerate(lines, 1):
        ln.id = f"L{n}"
    art = Artwork(filename=filename, kind="pdf", quality="raster" if any_ocr else "vector", pages=pages,
                  lines=lines, panels=panels, symbols=symbols, barcodes=barcodes, warnings=warnings, meta=meta)
    _finish(art, regions)
    return art


def _rich_graphics(page: pymupdf.Page) -> bool:
    """Real packaging art (photos, logos, seals) rather than a plain text label?"""
    try:
        if page.get_images():
            return True
        return len(page.get_drawings()) > 400
    except Exception:
        return False


def _graphic_text(img: np.ndarray, zoom: float, plines: list[TextLine], page_index: int, start_id: int) -> list[TextLine]:
    """OCR the rendered page and keep only text that is not already in the PDF text layer."""
    try:
        res = ocr_mod.ocr_image(img)
    except Exception:
        return []
    known = [l.bbox for l in plines if not l.hidden]
    keep = []
    for r in res:
        b = [v / zoom for v in r["bbox"]]
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if any(k[0] - 2 <= cx <= k[2] + 2 and k[1] - 2 <= cy <= k[3] + 2 for k in known):
            continue
        if r["conf"] < 0.6 or len(re.sub(r"\W", "", r["text"])) < 2:
            continue
        keep.append(r)
    out = ocr_mod.ocr_to_lines(keep, page_index, start_id, scale=zoom)
    for ln in out:
        ln.graphic = True
        ln.block = f"g{ln.block}"
    return out


# ---------------------------------------------------------------- images

def _rotate(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=(255, 255, 255))


def _load_image(path: Path, outdir: Path, filename: str) -> Artwork:
    pil = Image.open(path)
    dpi = pil.info.get("dpi")
    pil = ImageOps.exif_transpose(pil).convert("RGB")
    warnings: list[str] = []
    arr = np.array(pil)
    h, w = arr.shape[:2]
    shrink = 1.0
    if max(h, w) > MAX_ANALYSIS_PX:
        shrink = MAX_ANALYSIS_PX / max(h, w)
        arr = cv2.resize(arr, (int(w * shrink), int(h * shrink)), interpolation=cv2.INTER_AREA)
        h, w = arr.shape[:2]
    mm_per_px = None
    try:
        dpi_val = float(dpi[0]) if dpi else 0.0
    except Exception:
        dpi_val = 0.0
    if dpi_val >= 100:
        mm_per_px = 25.4 / (dpi_val * shrink)
    else:
        warnings.append("Image has no reliable physical scale (DPI); physical sizes are marked for review.")

    if not ocr_mod.ocr_available():
        warnings.append(f"OCR unavailable: {ocr_mod.OCR_ERROR}")
        results: list[dict] = []
    else:
        results = ocr_mod.ocr_image(arr)
    skew = ocr_mod.estimate_skew(results)
    deskew = 0.0
    if abs(skew) > 0.35:
        arr = _rotate(arr, skew)
        deskew = skew
        results = ocr_mod.ocr_image(arr)
        warnings.append(f"Image was straightened by {skew:.1f}° before reading.")
    results = ocr_mod.reread_uncertain(arr, results)
    lines = ocr_mod.ocr_to_lines(results, 0, 0, scale=1.0)

    disp_scale = min(1.0, MAX_DISPLAY_PX / max(h, w))
    disp = arr if disp_scale == 1.0 else cv2.resize(arr, (int(w * disp_scale), int(h * disp_scale)),
                                                     interpolation=cv2.INTER_AREA)
    Image.fromarray(disp).save(outdir / "page0.png")

    panels = [Panel(id=f"P1.{k + 1}", page=0, bbox=[round(v, 2) for v in cell])
              for k, cell in enumerate(grid_panels([0, 0, w, h], raster_panel_splits(arr), []))]
    det_img, det_scale = arr, 1.0
    if max(h, w) > 3500:
        det_scale = 3500 / max(h, w)
        det_img = cv2.resize(arr, (int(w * det_scale), int(h * det_scale)), interpolation=cv2.INTER_AREA)
    symbols = [Symbol(kind=m["kind"], page=0, bbox=[round(v / det_scale, 2) for v in m["bbox"]],
                      confidence=m["confidence"] * 0.95, shape=m["shape"]) for m in detect_food_marks(det_img)]
    barcodes = [Barcode(text=b["text"], format=b["format"], page=0, bbox=[round(v, 2) for v in b["bbox"]],
                        checksum_ok=b["checksum_ok"]) for b in detect_barcodes(arr)]
    page = PageInfo(index=0, width=w, height=h, unit="px", mm_per_unit=mm_per_px, image="page0.png",
                    render_scale=disp_scale, text_source="ocr" if lines else "none", deskew_deg=round(deskew, 2))
    art = Artwork(filename=filename, kind="image", quality="raster", pages=[page], lines=lines, panels=panels,
                  symbols=symbols, barcodes=barcodes, warnings=warnings)
    _finish(art, {0: [0, 0, w, h]})
    # Keep the straightened analysis image for later measurements.
    Image.fromarray(arr).save(outdir / "analysis0.png")
    return art


# ---------------------------------------------------------------- shared post-processing

def _finish(art: Artwork, regions: dict[int, list[float]]) -> None:
    def panel_for(page: int, bbox: list[float]) -> Optional[str]:
        cx, cy = bbox_center(bbox)
        for p in art.panels:
            if p.page == page and bbox_contains_point(p.bbox, cx, cy):
                return p.id
        region = regions.get(page)
        if region and not bbox_contains_point(region, cx, cy):
            return "outside"
        return None

    # Smallest containing panel wins (dieline panels can nest inside a larger outline).
    art.panels.sort(key=lambda p: (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1]))
    for ln in art.lines:
        ln.panel = panel_for(ln.page, ln.bbox)
    for s in art.symbols:
        s.panel = panel_for(s.page, s.bbox)
    for b in art.barcodes:
        b.panel = panel_for(b.page, b.bbox)
    art.panels.sort(key=lambda p: (p.page, round(p.bbox[1] / 40), p.bbox[0]))

    page_mm = {pg.index: pg.mm_per_unit for pg in art.pages}
    for p in art.panels:
        mm = page_mm.get(p.page)
        if mm:
            p.area_cm2 = round((p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1]) * mm * mm / 100.0, 1)

    if _name_from_dieline(art):
        return
    # No dieline labels: the principal display panel is the panel carrying the largest text.
    best, best_score = None, -1.0
    for p in art.panels:
        sizes = [(ln.font_size or (ln.bbox[3] - ln.bbox[1]) * 0.8) for ln in art.lines
                 if ln.panel == p.id and ln.active]
        score = max(sizes) if sizes else 0.0
        area = (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1])
        key = score + area * 1e-9
        if key > best_score:
            best, best_score = p, key
    others = [p for p in art.panels if p is not best]
    for i, p in enumerate(others):
        p.label = "Back panel" if len(others) == 1 else f"Panel {chr(66 + i)}"
        p.kind = "main"
    if best is not None:
        best.is_pdp = True
        best.kind = "main"
        best.label = "Front / principal display panel" if len(art.panels) > 1 else "Whole artwork (single panel)"


def _name_from_dieline(art: Artwork) -> bool:
    """Name panels from the labels printed on the dieline layer ("Principal Display Panel", "Back panel"...)."""
    labels = panel_labels(art.lines)
    if not labels or len(art.panels) < 2:
        return False
    named: dict[str, list[str]] = {}
    for name, bbox, page in labels:
        cx, cy = bbox_center(bbox)
        for p in art.panels:
            if p.page == page and bbox_contains_point(p.bbox, cx, cy):
                named.setdefault(p.id, []).append(name)
                break
    pdp = next((p for p in art.panels if "principal display panel" in named.get(p.id, [])), None)
    if pdp is None:
        fronts = [p for p in art.panels if "front panel" in named.get(p.id, [])]
        pdp = max(fronts, key=lambda p: (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1]), default=None)
    if pdp is None:
        return False
    pdp.is_pdp = True
    pdp.label = "Front / principal display panel"
    pdp.kind = "main"
    py0, py1 = pdp.bbox[1], pdp.bbox[3]
    used: dict[str, int] = {}
    for p in art.panels:
        if p is pdp:
            continue
        overlap = min(p.bbox[3], py1) - max(p.bbox[1], py0)
        main_row = overlap >= 0.7 * (py1 - py0) and (p.bbox[3] - p.bbox[1]) >= 0.7 * (py1 - py0)
        names = [n for n in named.get(p.id, []) if n != "principal display panel"]
        if main_row:
            p.kind = "main"
            base = names[0].capitalize() if names else "Side panel"
        else:
            p.kind = "flap"
            where = "Top" if p.bbox[3] <= py0 + 1 else "Bottom" if p.bbox[1] >= py1 - 1 else "Side"
            base = f"{where} flap" if not names or names[0] == "front panel" else f"{names[0].capitalize()} (flap)"
        used[base] = used.get(base, 0) + 1
        p.label = base if used[base] == 1 else f"{base} {used[base]}"
    return True
