"""End-to-end regression tests on the shipped samples (AI assist off)."""
import json
from pathlib import Path

import pytest

from app.pipeline import run_check

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"
RULEBOOKS = ROOT / "data" / "rulebooks"
MANIFEST = {s["id"]: s for s in json.loads((SAMPLES / "manifest.json").read_text())}
HAVE_ESKO = (SAMPLES / "esko_granola" / MANIFEST["esko_granola"]["artwork"]).exists()
needs_esko = pytest.mark.skipif(not HAVE_ESKO, reason="Esko's sample files are not in this copy (see data/samples/esko_granola)")


def run_sample(sid: str, tmp_path: Path) -> dict:
    s = MANIFEST[sid]
    run = run_check(SAMPLES / sid / s["artwork"], s["artwork"], RULEBOOKS / s["rulebook"], s["rulebook"],
                    SAMPLES / sid / s["approved"], s["approved"], tmp_path / sid)
    verdicts: dict[str, set] = {}
    for f in run["findings"]:
        verdicts.setdefault(f.verdict, set()).add(f.rule_id)
    run["v"] = verdicts
    return run


@pytest.fixture(scope="module")
def esko(tmp_path_factory):
    return run_sample("esko_granola", tmp_path_factory.mktemp("esko"))


@needs_esko
def test_esko_carton_definite_problems(esko):
    fails = esko["v"].get("FAIL", set())
    # Problems a reviewer can confirm on Esko's sample carton:
    assert {"U02",   # net quantity missing from the principal display panel
            "U03",   # no "Net Wt" term, no US customary unit
            "U06",   # tree nuts in the ingredients but no "Contains" statement
            "U10",   # calories do not add up from protein/carbs/fat
            "U14",   # "No added sugar" with honey and coconut sugar in the ingredients
            "U16",   # "Suitable for Vegans" with honey in the ingredients
            } <= fails


@needs_esko
def test_esko_carton_passes_and_reviews(esko):
    assert {"U01", "U04", "U19", "U24", "U25", "U26"} <= esko["v"].get("PASS", set())
    # Judgment-only rule goes to a person when AI assist is off; hidden text is flagged for review.
    assert {"U27", "U28"} <= esko["v"].get("REVIEW", set())
    assert {f.rule_id for f in esko["future"]} == {"U29", "U30"}


@needs_esko
def test_esko_every_finding_has_rule_quote_and_evidence(esko):
    for f in esko["findings"]:
        assert f.quote, f.rule_id
        if f.verdict == "FAIL":
            assert f.method == "code"
            assert f.evidence or f.check in ("fact_present", "text_present"), f.rule_id


def test_cookies_with_planted_errors(tmp_path):
    run = run_sample("cookies_errors", tmp_path)
    assert run["v"]["FAIL"] == {"R03", "R04", "R08", "R09", "R12", "R14", "R16", "R17", "R19"}


@pytest.mark.parametrize("sid", ["cookies_clean", "peanuts"])
def test_clean_artworks_have_no_false_alarms(sid, tmp_path):
    run = run_sample(sid, tmp_path)
    assert not run["v"].get("FAIL"), run["v"].get("FAIL")
    assert run["summary"]["status"] in ("READY", "NEEDS_REVIEW")


def test_photo_of_proof_catches_the_same_errors(tmp_path):
    run = run_sample("cookies_photo", tmp_path)
    assert run["v"]["FAIL"] == {"R03", "R04", "R08", "R09", "R12", "R14", "R16", "R17", "R19"}
    assert run["artwork"].quality == "raster"
