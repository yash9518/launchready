"""Data model shared by every stage of the pipeline.

Coordinates: every bbox is [x0, y0, x1, y1] in *page units* of its page —
PDF points for PDFs, pixels for images. Each page also has a rendered PNG and
a `render_scale` (image pixels per page unit) so the UI can overlay boxes.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, PrivateAttr

Verdict = Literal["PASS", "FAIL", "REVIEW", "NA"]


class Span(BaseModel):
    text: str
    bbox: list[float]


class TextLine(BaseModel):
    id: str
    page: int
    text: str
    bbox: list[float]
    font_size: Optional[float] = None
    font: Optional[str] = None
    bold: bool = False
    color: Optional[str] = None
    source: Literal["pdf", "ocr"] = "pdf"
    confidence: float = 1.0
    block: Optional[str] = None
    panel: Optional[str] = None
    spans: list[Span] = Field(default_factory=list)
    alt_text: Optional[str] = None      # a second OCR reading that disagreed
    verified: bool = False              # two OCR readings agreed
    layer: Optional[str] = None         # PDF optional-content layer (e.g. "DESIGN", "Dimensions and text")
    technical: bool = False             # non-printing dieline / proof annotation ("Front panel", "Art orientation")
    hidden: bool = False                # in the file but covered by later artwork, so it will not print
    graphic: bool = False               # read by OCR from inside a logo, seal or image of a vector PDF
    merged: bool = False                # rebuilt from separately placed letters (arched / stacked text)

    @property
    def active(self) -> bool:
        """Printable label content that facts and checks should use."""
        return not (self.technical or self.hidden) and self.panel != "outside"


class PageInfo(BaseModel):
    index: int
    width: float
    height: float
    unit: Literal["pt", "px"]
    mm_per_unit: Optional[float] = None
    image: str
    render_scale: float
    text_source: Literal["pdf", "ocr", "none"] = "pdf"
    deskew_deg: float = 0.0


class Panel(BaseModel):
    id: str
    page: int
    bbox: list[float]
    is_pdp: bool = False
    area_cm2: Optional[float] = None
    label: str = ""
    kind: str = ""                      # "main" face or "flap" (cartons)
    source: str = ""                    # how the panel was found: dieline layer, fold lines, image analysis


class Symbol(BaseModel):
    kind: str
    page: int
    bbox: list[float]
    confidence: float
    shape: str = ""
    panel: Optional[str] = None


class Barcode(BaseModel):
    text: str
    format: str
    page: int
    bbox: list[float]
    checksum_ok: Optional[bool] = None
    panel: Optional[str] = None


class Artwork(BaseModel):
    filename: str
    kind: Literal["pdf", "image"]
    quality: Literal["vector", "raster"]
    pages: list[PageInfo]
    lines: list[TextLine]
    panels: list[Panel] = Field(default_factory=list)
    symbols: list[Symbol] = Field(default_factory=list)
    barcodes: list[Barcode] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    _idx: Optional[dict] = PrivateAttr(default=None)

    def line(self, line_id: str) -> Optional[TextLine]:
        if self._idx is None or len(self._idx) != len(self.lines):
            self._idx = {ln.id: ln for ln in self.lines}
        return self._idx.get(line_id)

    def content_lines(self) -> list[TextLine]:
        return [ln for ln in self.lines if ln.active]

    def pdp(self) -> Optional[Panel]:
        for p in self.panels:
            if p.is_pdp:
                return p
        return None


class Fact(BaseModel):
    key: str
    value: Any = None
    raw: str = ""
    line_ids: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    method: Literal["parser", "ai", "vision"] = "parser"
    panel: Optional[str] = None
    attrs: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    page: int
    bbox: list[float]
    label: str = ""
    line_id: Optional[str] = None
    kind: Literal["line", "symbol", "barcode", "panel", "region"] = "line"


class Rule(BaseModel):
    id: str
    title: str
    check: str
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["critical", "major", "minor"] = "major"
    quote: str = ""
    source: dict[str, Any] = Field(default_factory=dict)
    basis: str = ""
    why: str = ""
    status: Literal["in_force", "upcoming", "proposed"] = "in_force"
    effective_from: Optional[str] = None
    enabled: bool = True
    origin: Literal["rulebook", "ai_compiled", "pattern_compiled"] = "rulebook"
    quote_verified: bool = True
    notes: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    rule_id: str
    title: str
    verdict: Verdict
    severity: str
    message: str
    method: Literal["code", "ai", "code+ai", "none"] = "code"
    confidence: float = 1.0
    expected: Optional[str] = None
    found: Optional[str] = None
    diff: Optional[list[dict[str, str]]] = None
    evidence: list[Evidence] = Field(default_factory=list)
    why: str = ""
    quote: str = ""
    source: dict[str, Any] = Field(default_factory=dict)
    basis: str = ""
    notes: list[str] = Field(default_factory=list)
    timeline: Optional[str] = None
    check: str = ""


class AiEvent(BaseModel):
    stage: str
    action: Literal["accepted", "rejected", "downgraded", "used", "error", "skipped", "confirmed"]
    detail: str
