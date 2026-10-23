"""Evaluation harness: planted-defect artworks with known ground truth.

For every defect variant (and two clean artworks) in three input forms —
vector PDF, 200-dpi PNG, simulated phone photo — run the checker and measure:
  * detection: is every expected rule flagged (FAIL or REVIEW)? as a definite FAIL?
  * false alarms: FAIL on a rule that should pass
  * review load: REVIEW items per artwork (excluding the judgment-only rule)

Usage:  python tools/evaluate.py [--forms pdf,png,photo] [--llm]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.labelgen import COOKIES, PEANUTS, approved_record, build_label, defects, render_png, save_pdf  # noqa: E402

EVAL = ROOT / "data" / "eval"
RULEBOOK = ROOT / "data" / "rulebooks" / "sunfield_labelling_rulebook.md"
JUDGMENT_RULES = {"R26"}


def build_set() -> list[dict]:
    EVAL.mkdir(parents=True, exist_ok=True)
    items = []
    for prod, name in ((COOKIES, "cookies"), (PEANUTS, "peanuts")):
        appr = EVAL / f"approved_{name}.json"
        appr.write_text(json.dumps(approved_record(prod), indent=2))
        items.append({"id": f"CLEAN_{name}", "doc": lambda p=prod: build_label(p), "expected": [], "approved": appr,
                      "desc": f"clean {name} artwork"})
    appr = EVAL / "approved_cookies.json"
    for did, (over, exp, desc) in defects(COOKIES).items():
        items.append({"id": did, "doc": lambda o=over: build_label(COOKIES, **o), "expected": exp, "approved": appr,
                      "desc": desc})
    for it in items:
        d = EVAL / it["id"]
        d.mkdir(exist_ok=True)
        pdf = d / "artwork.pdf"
        if not pdf.exists():
            save_pdf(it["doc"](), pdf)
        if not (d / "artwork.png").exists():
            render_png(pdf, d / "artwork.png", dpi=200)
        if not (d / "photo.jpg").exists():
            render_png(pdf, d / "photo.jpg", dpi=200, photo=True, seed=len(it["id"]))
        it["files"] = {"pdf": pdf, "png": d / "artwork.png", "photo": d / "photo.jpg"}
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default="pdf,png,photo")
    ap.add_argument("--llm", action="store_true", help="use the configured LLM (otherwise AI features are off)")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    if not args.llm:
        os.environ["LLM_PROVIDER"] = "none"
    from app.llm import LLMClient
    from app.pipeline import run_check
    llm = LLMClient(ROOT / ".cache" / "llm")
    items = build_set()
    if args.only:
        items = [i for i in items if i["id"] in args.only.split(",")]
    forms = args.forms.split(",")
    tmp = ROOT / ".cache" / "eval_runs"
    shutil.rmtree(tmp, ignore_errors=True)
    rows = []
    for it in items:
        for form in forms:
            f = it["files"][form]
            t = time.time()
            run = run_check(f, f.name, RULEBOOK, RULEBOOK.name, it["approved"], it["approved"].name,
                            tmp / f"{it['id']}_{form}", llm=llm)
            secs = time.time() - t
            v = {fd.rule_id: fd.verdict for fd in run["findings"]}
            exp = set(it["expected"])
            fails = {r for r, x in v.items() if x == "FAIL"}
            reviews = {r for r, x in v.items() if x == "REVIEW"}
            rows.append({"id": it["id"], "form": form, "desc": it["desc"], "expected": sorted(exp),
                         "fail": sorted(fails), "review": sorted(reviews), "seconds": round(secs, 1),
                         "caught": exp <= (fails | reviews), "caught_fail": exp <= fails,
                         "false_alarms": sorted(fails - exp), "review_noise": sorted(reviews - exp - JUDGMENT_RULES),
                         "missed": sorted(exp - fails - reviews)})
            r = rows[-1]
            flag = "OK " if r["caught"] and not r["false_alarms"] else "!! "
            print(f"{flag}{it['id']:32} {form:5} exp={','.join(r['expected']) or '-':14} FAIL={','.join(r['fail']) or '-':22} "
                  f"REVIEW={','.join(r['review']) or '-':18} missed={','.join(r['missed']) or '-'} "
                  f"FA={','.join(r['false_alarms']) or '-'} {secs:.1f}s", flush=True)
    summary = {}
    for form in forms:
        rs = [r for r in rows if r["form"] == form]
        defect_rows = [r for r in rs if r["expected"]]
        clean_rows = [r for r in rs if not r["expected"]]
        exp_hits = sum(len(r["expected"]) for r in defect_rows)
        summary[form] = {
            "artworks": len(rs),
            "defects": len(defect_rows),
            "defects_flagged": sum(r["caught"] for r in defect_rows),
            "defects_definite_fail": sum(r["caught_fail"] for r in defect_rows),
            "expected_rule_hits": exp_hits,
            "rule_hits_fail": sum(len(set(r["expected"]) & set(r["fail"])) for r in defect_rows),
            "rule_hits_review": sum(len(set(r["expected"]) & set(r["review"])) for r in defect_rows),
            "rule_hits_missed": sum(len(r["missed"]) for r in defect_rows),
            "false_alarms": sum(len(r["false_alarms"]) for r in rs),
            "false_alarms_on_clean": sum(len(r["false_alarms"]) for r in clean_rows),
            "avg_review_items": round(sum(len(r["review_noise"]) for r in rs) / max(1, len(rs)), 2),
            "avg_seconds": round(sum(r["seconds"] for r in rs) / max(1, len(rs)), 1),
        }
    out = ROOT / "eval"
    out.mkdir(exist_ok=True)
    (out / "results.json").write_text(json.dumps({"summary": summary, "rows": rows,
                                                  "llm": llm.describe()}, indent=2, default=str))
    names = {"pdf": "Vector PDF (print-ready file)", "png": "PNG, 200 dpi (flattened artwork)",
             "photo": "Phone-style photo (tilted, blurred, no scale)"}
    lines = ["# Evaluation results", "",
             f"Planted-defect test set: {summary[forms[0]]['defects']} single-defect artworks + 2 clean artworks, each "
             f"in {len(forms)} input form(s). Ground truth is known because every artwork is generated from data. "
             f"AI features: {'ON (' + str(llm.provider) + ')' if llm.enabled else 'OFF (offline run)'}.", "",
             "| Input | Defects flagged | …as definite FAIL | Expected rule hits (FAIL / REVIEW / missed) | "
             "False alarms (all files) | False alarms on clean files | Review items per file | Seconds per file |",
             "|---|---|---|---|---|---|---|---|"]
    for form in forms:
        s = summary[form]
        lines.append(f"| {names[form]} | {s['defects_flagged']}/{s['defects']} | {s['defects_definite_fail']}/{s['defects']} | "
                     f"{s['rule_hits_fail']} / {s['rule_hits_review']} / {s['rule_hits_missed']} of {s['expected_rule_hits']} | "
                     f"{s['false_alarms']} | {s['false_alarms_on_clean']} | {s['avg_review_items']} | {s['avg_seconds']} |")
    lines += ["", "*Flagged* = every rule the defect breaks is reported as FAIL or REVIEW. *False alarm* = a FAIL on a "
              "rule that should pass. Review items exclude the judgment-only rule R26, which always needs a person "
              "when AI is off.", "", "## Per-artwork detail", "",
              "| Artwork | Input | Expected | FAIL | REVIEW | Missed | False alarms |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} — {r['desc']} | {r['form']} | {', '.join(r['expected']) or '—'} | "
                     f"{', '.join(r['fail']) or '—'} | {', '.join(r['review']) or '—'} | {', '.join(r['missed']) or '—'} | "
                     f"{', '.join(r['false_alarms']) or '—'} |")
    (out / "results.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
