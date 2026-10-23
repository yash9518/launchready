"""Standalone HTML report (printable to PDF) with evidence crops embedded."""
from __future__ import annotations

import base64
import datetime as dt
import html
import io
from pathlib import Path

from PIL import Image, ImageDraw

COLORS = {"FAIL": "#c62828", "REVIEW": "#b26a00", "PASS": "#2e7d32", "NA": "#6b7280"}
LABELS = {"FAIL": "Must fix", "REVIEW": "Needs a person", "PASS": "Passed", "NA": "Not applicable"}


def _crop(run: dict, finding) -> str:
    art = run["artwork"]
    ev = [e for e in finding.evidence if e.kind != "panel"] or finding.evidence
    if not ev:
        return ""
    page = art.pages[ev[0].page]
    img_path = Path(run["run_dir"]) / page.image
    if not img_path.exists():
        return ""
    img = Image.open(img_path).convert("RGB")
    s = page.render_scale
    boxes = [[v * s for v in e.bbox] for e in ev if e.page == ev[0].page]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    pad = max(18, 0.15 * max(x1 - x0, y1 - y0))
    cx0, cy0 = max(0, int(x0 - pad)), max(0, int(y0 - pad))
    cx1, cy1 = min(img.width, int(x1 + pad)), min(img.height, int(y1 + pad))
    crop = img.crop((cx0, cy0, cx1, cy1))
    d = ImageDraw.Draw(crop)
    for b in boxes:
        d.rectangle([b[0] - cx0 - 2, b[1] - cy0 - 2, b[2] - cx0 + 2, b[3] - cy0 + 2],
                    outline=COLORS[finding.verdict], width=3)
    if crop.width > 900:
        crop = crop.resize((900, int(crop.height * 900 / crop.width)))
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def build_report(run: dict) -> str:
    e = html.escape
    art = run["artwork"]
    s = run["summary"]
    rows = []
    for f in run["findings"] + run["future"]:
        crop = _crop(run, f) if f.verdict in ("FAIL", "REVIEW") else ""
        diff = ""
        if f.expected or f.found:
            diff = (f"<div class='ef'><div><b>Expected</b><br>{e(f.expected or '—')}</div>"
                    f"<div><b>Found</b><br>{e(f.found or '—')}</div></div>")
        notes = "".join(f"<li>{e(n)}</li>" for n in f.notes)
        timeline = f"<span class='tl'>{e(f.timeline)}</span>" if f.timeline else ""
        method = {"code": "Decided by deterministic code", "ai": "AI-assisted — confirm",
                  "none": "Needs human judgment"}.get(f.method, f.method)
        rows.append(f"""
<section class="f {f.verdict}">
  <div class="fh"><span class="badge" style="background:{COLORS[f.verdict]}">{LABELS[f.verdict]}</span>
  <b>{e(f.rule_id)} · {e(f.title)}</b> <span class="sev">{e(f.severity)}</span> {timeline}</div>
  <p>{e(f.message)}</p>{diff}
  {f'<img src="data:image/jpeg;base64,{crop}" alt="evidence">' if crop else ''}
  <blockquote>“{e(f.quote)}”<cite>{e(str(f.source.get('doc', '')))}{(' · ' + e(str(f.source.get('section') or f.source.get('page') or ''))) if f.source else ''}</cite></blockquote>
  {f'<p class="why"><b>Why it matters:</b> {e(f.why)}</p>' if f.why else ''}
  <p class="meta">{method} · confidence {f.confidence:.0%}</p>
  {f'<ul class="notes">{notes}</ul>' if notes else ''}
</section>""")
    status_color = {"NOT_READY": "#c62828", "NEEDS_REVIEW": "#b26a00", "READY": "#2e7d32"}[s["status"]]
    c = s["counts"]
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>LaunchReady report — {e(art.filename)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1f2328;margin:0;background:#f6f6f3}}
main{{max-width:960px;margin:0 auto;padding:28px 20px 60px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#57606a;font-size:13px}}
.hero{{background:#fff;border-radius:12px;padding:20px 22px;margin:18px 0;border-left:6px solid {status_color}}}
.hero h2{{margin:0 0 8px;font-size:20px;color:{status_color}}}
.counts span{{display:inline-block;margin-right:14px;font-size:14px}}
.f{{background:#fff;border-radius:10px;padding:14px 18px;margin:10px 0;border-left:5px solid #ccc;break-inside:avoid}}
.f.FAIL{{border-color:#c62828}} .f.REVIEW{{border-color:#b26a00}} .f.PASS{{border-color:#2e7d32}} .f.NA{{border-color:#9ca3af}}
.fh{{font-size:15px}} .badge{{color:#fff;font-size:11px;padding:2px 8px;border-radius:99px;margin-right:6px;text-transform:uppercase;letter-spacing:.03em}}
.sev{{font-size:11px;color:#57606a;border:1px solid #d0d7de;border-radius:99px;padding:1px 7px;margin-left:6px}}
.tl{{font-size:11px;background:#eef2ff;color:#3730a3;border-radius:99px;padding:2px 8px;margin-left:6px}}
.ef{{display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:13px;background:#f6f8fa;padding:10px;border-radius:8px}}
img{{max-width:100%;border:1px solid #d0d7de;border-radius:6px;margin:10px 0}}
blockquote{{margin:10px 0;padding:8px 12px;border-left:3px solid #d0d7de;color:#3d444d;font-size:13px}}
cite{{display:block;font-style:normal;color:#6e7781;font-size:11px;margin-top:4px}}
.why,.meta{{font-size:13px;color:#3d444d}} .meta{{color:#6e7781}} .notes{{font-size:12px;color:#6e7781}}
@media print{{body{{background:#fff}} .f,.hero{{border:1px solid #ddd}}}}
</style></head><body><main>
<h1>LaunchReady — artwork compliance report</h1>
<div class="sub">Artwork: <b>{e(art.filename)}</b> ({e(art.kind)}, {e(art.quality)} text) · Rulebook: <b>{e(run['rulebook_name'])}</b>
({len(run['rules'])} rules) · Approved data: <b>{e(run.get('approved_name') or 'none')}</b> · Checked as of {e(run['as_of'])} ·
Generated {dt.datetime.now().strftime('%d %b %Y %H:%M')}</div>
<div class="hero"><h2>{e(s['headline'])}</h2><div class="counts">
<span>✗ {c['FAIL']} must fix</span><span>? {c['REVIEW']} need a person</span><span>✓ {c['PASS']} passed</span>
<span>– {c['NA']} not applicable</span><span>⏱ {s['future_risks']} future risk(s)</span></div></div>
{''.join(rows)}
<p class="sub">Verdict policy: FAIL only when deterministic code finds a violation on a confident read; anything uncertain,
judgment-based or AI-produced is marked for a person. Every finding cites its rule and evidence.</p>
</main></body></html>"""
