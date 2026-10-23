"""One full check: artwork + rulebook + approved data -> findings with evidence."""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any, Callable, Optional

from app.ai import second_reader
from app.approved import approved_warnings, load_approved
from app.extract.artwork import load_artwork
from app.facts import extract_facts, facts_for_display
from app.models import AiEvent, Rule
from app.rules.checks import CheckContext
from app.rules.engine import run_rules, summarize
from app.rules.rulebook import load_rulebook


def run_check(artwork_path: Path, artwork_name: str, rulebook_path: Path, rulebook_name: str,
              approved_path: Optional[Path], approved_name: Optional[str], run_dir: Path, llm=None,
              as_of: Optional[dt.date] = None, progress: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    say = progress or (lambda _s: None)
    timings: dict[str, float] = {}
    t = time.time()
    say("Reading the artwork: text layer, dieline layers, graphics and barcodes")
    art = load_artwork(artwork_path, run_dir, artwork_name)
    timings["read artwork"] = time.time() - t

    approved, approved_kind = None, None
    if approved_path is not None:
        say("Reading the approved product data")
        approved, approved_kind = load_approved(approved_path)
    approved_issues = approved_warnings(approved)

    t = time.time()
    say("Extracting facts: quantities, ingredients, nutrition panel, claims")
    facts = extract_facts(art, approved)
    events: list[AiEvent] = []
    second_reader(art, facts, llm, run_dir, events)
    timings["extract facts"] = time.time() - t

    t = time.time()
    say("Reading the rulebook")
    rb = load_rulebook(rulebook_path, rulebook_name, llm, events)
    timings["load rules"] = time.time() - t
    say(f"Running {len(rb['rules'])} checks and verifying every possible problem")

    run = {
        "artwork": art, "artwork_path": artwork_path, "facts": facts, "approved": approved,
        "approved_name": approved_name, "approved_kind": approved_kind, "rules": rb["rules"],
        "rulebook_name": rulebook_name, "rulebook_kind": rb["kind"], "events": events, "run_dir": run_dir,
        "timings": timings, "llm": llm, "approved_warnings": approved_issues,
    }
    evaluate(run, as_of)
    return run


def evaluate(run: dict, as_of: Optional[dt.date] = None) -> dict:
    """(Re)run the rules on an already-extracted artwork — fast, used when the user toggles rules or the date."""
    t = time.time()
    as_of = as_of or dt.date.today()
    ctx = CheckContext(run["artwork"], run["facts"], run["approved"], run["run_dir"], run["artwork_path"],
                       llm=run.get("llm"), events=run["events"])
    current, future = run_rules(run["rules"], ctx, as_of)
    run["findings"], run["future"] = current, future
    run["summary"] = summarize(current, future)
    run["as_of"] = as_of.isoformat()
    run["timings"]["run checks"] = time.time() - t
    return run


def run_to_json(run: dict, run_id: str) -> dict:
    art = run["artwork"]
    llm = run.get("llm")
    return {
        "id": run_id,
        "as_of": run["as_of"],
        "summary": run["summary"],
        "artwork": {
            "filename": art.filename, "kind": art.kind, "quality": art.quality, "warnings": art.warnings,
            "meta": art.meta,
            "pages": [p.model_dump() | {"image_url": f"/api/runs/{run_id}/pages/{p.index}.png"} for p in art.pages],
            "panels": [p.model_dump() for p in art.panels],
            "lines": [l.model_dump(exclude={"spans"}) for l in art.lines],
            "symbols": [s.model_dump() for s in art.symbols],
            "barcodes": [b.model_dump() for b in art.barcodes],
        },
        "rulebook": {"name": run["rulebook_name"], "kind": run["rulebook_kind"],
                     "rules": [r.model_dump() for r in run["rules"]]},
        "approved": {"name": run.get("approved_name"), "kind": run.get("approved_kind"),
                     "data": {k: v for k, v in (run.get("approved") or {}).items() if not k.startswith("_")},
                     "notes": (run.get("approved") or {}).get("_notes", {}),
                     "warnings": run.get("approved_warnings", [])},
        "facts": facts_for_display(run["facts"]),
        "findings": [f.model_dump() for f in run["findings"]],
        "future": [f.model_dump() for f in run["future"]],
        "ai": {"llm": llm.describe() if llm else {"enabled": False},
               "events": [e.model_dump() for e in run["events"]]},
        "timings": {k: round(v, 2) for k, v in run["timings"].items()},
    }


def set_rules_enabled(run: dict, disabled: set[str]) -> None:
    for r in run["rules"]:
        r.enabled = r.id not in disabled


def rules_from_json(items: list[dict]) -> list[Rule]:
    return [Rule(**i) for i in items]
