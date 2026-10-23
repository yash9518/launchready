"""HTTP API smoke test (the same calls the web UI makes)."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_status_lists_samples_and_rulebooks():
    s = client.get("/api/status").json()
    ids = [x["id"] for x in s["samples"]]
    assert "cookies_errors" in ids
    esko_files = Path(__file__).resolve().parents[1] / "data" / "samples" / "esko_granola"
    assert ("esko_granola" in ids) == any(esko_files.glob("*.pdf"))
    assert any(r["name"] == "us_fda_granola_rulebook.md" for r in s["rulebooks"])


def test_check_rerun_ask_and_report():
    r = client.post("/api/check", data={"sample_id": "cookies_errors", "wait": "true"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["summary"]["status"] == "NOT_READY"
    fails = {f["rule_id"] for f in run["findings"] if f["verdict"] == "FAIL"}
    assert "R12" in fails

    # Switch the allergen rule off and re-run: the finding disappears, nothing else is re-read.
    r2 = client.post(f"/api/runs/{run['id']}/rerun", json={"disabled": ["R12"], "as_of": run["as_of"]}).json()
    assert "R12" not in {f["rule_id"] for f in r2["findings"]}

    a = client.post(f"/api/runs/{run['id']}/ask", json={"rule_id": "R03", "question": "How do I fix it?"}).json()
    assert a["answer"] and a["source"] == "template"

    html = client.get(f"/api/runs/{run['id']}/report.html").text
    assert "LaunchReady" in html and "R03" in html
    assert client.get(f"/api/runs/{run['id']}/pages/0.png").status_code == 200
