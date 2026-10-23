"""Runs rules, applies the verify-before-alarm step and the verdict policy.

Verdict policy (the core of the 'avoid false alarms' design):
  FAIL    only when deterministic code finds a violation on a confident read.
  REVIEW  when the read is uncertain, the rule needs judgment, an AI produced the finding,
          or something looks missing but could not be confirmed by a second, independent look.
  PASS    always with the evidence that satisfied the rule.
  NA      the rule does not apply (e.g. no claims are made, no approved value supplied).
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Optional

import cv2
import numpy as np
from PIL import Image
from rapidfuzz import fuzz

from app.extract import ocr as ocr_mod
from app.extract.vision import detect_barcodes, detect_food_marks
from app.facts import FACT_KEYWORDS
from app.models import AiEvent, Finding, Rule
from app.rules.checks import CHECKS, CheckContext, Outcome

log = logging.getLogger(__name__)

FAIL_CONFIDENCE = 0.8
SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}
VERDICT_ORDER = {"FAIL": 0, "REVIEW": 1, "PASS": 2, "NA": 3}


def _ngram_hit(keyword: str, text: str, threshold: float = 86) -> bool:
    words = re.findall(r"[a-z0-9.]+", text.lower())
    kw = keyword.lower()
    n = max(1, len(kw.split()))
    for size in {n, n + 1, max(1, n - 1)}:
        for i in range(0, max(1, len(words) - size + 1)):
            gram = " ".join(words[i:i + size]).strip(".")
            if fuzz.ratio(gram, kw) >= threshold or fuzz.ratio(gram.replace(" ", ""), kw.replace(" ", "")) >= threshold:
                return True
    return False


class Rescanner:
    """A second look at raster artwork at 2x resolution (OCR, symbol detector, barcode decoder)."""

    def __init__(self, ctx: CheckContext):
        self.ctx = ctx
        self._img: Optional[np.ndarray] = None
        self._ocr: Optional[list[str]] = None

    def image(self) -> Optional[np.ndarray]:
        if self._img is None:
            art = self.ctx.art
            if art.kind == "image":
                p = self.ctx.run_dir / "analysis0.png"
                if not p.exists():
                    return None
                img = np.array(Image.open(p).convert("RGB"))
            else:
                doc = self.ctx.pdf()
                if doc is None:
                    return None
                pix = doc[0].get_pixmap(dpi=300, alpha=False)
                img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3].copy()
            h, w = img.shape[:2]
            f = min(2.0, 6000 / max(h, w))
            self._img = cv2.resize(img, (int(w * f), int(h * f)), interpolation=cv2.INTER_CUBIC) if f > 1.05 else img
        return self._img

    def text(self) -> list[str]:
        if self._ocr is None:
            img = self.image()
            self._ocr = [r["text"] for r in ocr_mod.ocr_image(img)] if img is not None else []
        return self._ocr

    def has_keywords(self, keywords: list[str]) -> Optional[str]:
        for t in self.text():
            for kw in keywords:
                if _ngram_hit(kw, t):
                    return t
        return None

    def has_mark(self, kind: str) -> bool:
        img = self.image()
        return img is not None and any(m["kind"] == kind for m in detect_food_marks(img))

    def has_barcode(self) -> bool:
        img = self.image()
        return img is not None and bool(detect_barcodes(img))


PARTIAL_KEYWORDS = {"inclusive_of_taxes": ["incl. of all taxes", "inclusive of all taxes"],
                    "has_contact": ["customer care", "toll free", "email"], "has_address": []}


def verify_absence(o: Outcome, rule: Rule, ctx: CheckContext, rescanner: Rescanner) -> Outcome:
    """Before reporting something as missing, look again — and only keep FAIL if the second look agrees."""
    if o.status != "fail" or not o.absence:
        return o
    key, _, attr = (o.absence_fact or "").partition(":")
    kws = PARTIAL_KEYWORDS.get(attr, []) if attr else FACT_KEYWORDS.get(key, [])
    assigned = {lid for lst in ctx.facts.values() for f in lst if f.key != key for lid in f.line_ids}
    # 1. Near misses in text we already have (e.g. OCR garbled a label, so the parser missed it).
    if kws:
        cands = [l for l in ctx.art.content_lines() if l.id not in assigned
                 and l.id not in {e.line_id for e in o.evidence} and any(_ngram_hit(kw, l.text) for kw in kws)]
        if cands:
            o.status = "unknown"
            o.evidence += ctx.ev_lines([c.id for c in cands[:3]], "possible match")
            o.notes.append("Possible match: " + "; ".join(f"“{c.text[:60]}”" for c in cands[:2]) +
                           " — the parser could not confirm it, so a person should check.")
            return o
    if ctx.art.quality != "raster":
        o.notes.append("Absence is reliable: the PDF text layer was read exactly (no OCR involved).")
        return o
    # 2. Raster artwork: a second, independent look before reporting something as missing.
    rescan_used, second_found = False, None
    try:
        if key == "food_mark":
            rescan_used = True
            want = "nonveg_mark" if "non-veg" in (o.absence_label or "") else "veg_mark"
            if rescanner.has_mark(want):
                second_found = "the symbol detector found it at 2× resolution"
        elif key == "gtin":
            rescan_used = True
            if rescanner.has_barcode():
                second_found = "the barcode decoder read it at 2× resolution"
        elif kws:
            rescan_used = True
            hit = rescanner.has_keywords(kws)
            if hit:
                second_found = f"an OCR re-scan at 2× resolution read “{hit[:60]}”"
    except Exception as exc:  # pragma: no cover
        log.warning("rescan failed: %s", exc)
    ai_view, ai_note = None, ""
    if ctx.llm is not None and ctx.llm.enabled:
        from app.ai import confirm_absence
        ai_view, ai_note = confirm_absence(ctx, o.absence_label or key)
        ctx.events.append(AiEvent(stage="verify", action="confirmed" if ai_view is False else "downgraded",
                                  detail=f"{rule.id}: {ai_note}."))
    if second_found or ai_view is True:
        o.status = "unknown"
        o.notes.append("The first reading found nothing, but a second look disagrees: " +
                       "; ".join(x for x in (second_found, ai_note if ai_view else "") if x) + ". Please verify.")
        return o
    if ai_view is None and ctx.llm is not None and ctx.llm.enabled:
        o.status = "unknown"
        o.notes.append(f"{ai_note} — please verify.")
        return o
    confirmations = (["re-scan at 2× resolution"] if rescan_used else []) + (["AI second look"] if ai_view is False else [])
    if confirmations:
        o.notes.append(f"Confirmed missing by a second look ({' and '.join(confirmations)}).")
        return o
    o.status = "unknown"
    o.notes.append("Raster artwork and no second reading available — a person should confirm it is missing.")
    return o


def decide(o: Outcome) -> str:
    if o.status == "na":
        return "NA"
    if o.status == "pass":
        return "PASS"
    if o.status == "unknown":
        return "REVIEW"
    if o.method in ("ai", "none"):
        return "REVIEW"
    if o.confidence < FAIL_CONFIDENCE:
        o.notes.append(f"Read confidence is {o.confidence:.0%}, below the {FAIL_CONFIDENCE:.0%} needed for a "
                       f"definite failure — sent to review.")
        return "REVIEW"
    return "FAIL"


def timeline(rule: Rule, as_of: dt.date) -> Optional[str]:
    if rule.status == "proposed":
        return "Proposed — not yet law"
    if rule.effective_from:
        try:
            eff = dt.date.fromisoformat(rule.effective_from)
        except ValueError:
            eff = None
        if eff and eff > as_of:
            return f"Applies from {eff.strftime('%d %b %Y')}"
        return None
    if rule.status == "upcoming":
        return "Upcoming"
    return None


def run_rules(rules: list[Rule], ctx: CheckContext, as_of: dt.date) -> tuple[list[Finding], list[Finding]]:
    rescanner = Rescanner(ctx)
    current, future = [], []
    for rule in rules:
        if not rule.enabled:
            continue
        fn = CHECKS.get(rule.check)
        try:
            o = fn(rule, ctx) if fn else Outcome("unknown", f"Unknown check type '{rule.check}'.", confidence=0.0)
        except Exception as exc:
            log.exception("check %s failed", rule.id)
            o = Outcome("unknown", f"This check could not run ({type(exc).__name__}: {exc}).", confidence=0.0)
        tl = timeline(rule, as_of)
        if tl is None:
            o = verify_absence(o, rule, ctx, rescanner)
        verdict = decide(o)
        notes = list(o.notes) + list(rule.notes)
        if not rule.quote_verified:
            notes.append("The rule's quote could not be verified against its source document.")
        finding = Finding(rule_id=rule.id, title=rule.title, verdict=verdict, severity=rule.severity,
                          message=o.message, method=o.method if o.method in ("code", "ai", "none") else "code",
                          confidence=round(float(o.confidence), 3), expected=o.expected, found=o.found, diff=o.diff,
                          evidence=o.evidence, why=rule.why, quote=rule.quote, source=rule.source, basis=rule.basis,
                          notes=notes, timeline=tl, check=rule.check)
        (future if tl else current).append(finding)
    key = lambda f: (VERDICT_ORDER[f.verdict], SEVERITY_ORDER.get(f.severity, 3), f.rule_id)  # noqa: E731
    return sorted(current, key=key), sorted(future, key=key)


def summarize(current: list[Finding], future: list[Finding]) -> dict:
    counts = {v: sum(1 for f in current if f.verdict == v) for v in ("FAIL", "REVIEW", "PASS", "NA")}
    crit = sum(1 for f in current if f.verdict == "FAIL" and f.severity == "critical")
    if counts["FAIL"]:
        status, headline = "NOT_READY", f"Not ready to print — {counts['FAIL']} issue{'s' if counts['FAIL'] != 1 else ''} " \
                                         f"must be fixed"
    elif counts["REVIEW"]:
        status, headline = "NEEDS_REVIEW", f"No definite problems — {counts['REVIEW']} item" \
                                           f"{'s' if counts['REVIEW'] != 1 else ''} need a human check"
    else:
        status, headline = "READY", "Ready to print — every applicable rule passed"
    fut = [f for f in future if f.verdict in ("FAIL", "REVIEW")]
    return {"status": status, "headline": headline, "counts": counts, "critical_failures": crit,
            "future_risks": len(fut), "future_total": len(future)}
