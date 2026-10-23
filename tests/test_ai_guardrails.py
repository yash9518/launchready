"""AI outputs are proposals: they are verified, and an AI alone can never produce a definite FAIL."""
import json

import httpx

from app.ai import verify_quote
from app.llm import LLMClient, parse_json
from app.rules.checks import Outcome
from app.rules.engine import decide


def test_ai_verdicts_never_become_definite_failures():
    assert decide(Outcome("fail", "AI thinks so", method="ai", confidence=0.99)) == "REVIEW"
    assert decide(Outcome("fail", "code proved it", method="code", confidence=0.99)) == "FAIL"
    assert decide(Outcome("fail", "blurry read", method="code", confidence=0.5)) == "REVIEW"


def test_rule_quotes_must_exist_in_the_source():
    pages = [(1, "The net quantity shall be declared on the principal display panel in both metric and US units.")]
    ok, page, _ = verify_quote("The net quantity shall be declared on the principal display panel", pages)
    assert ok and page == 1
    ok, _, _ = verify_quote("All products must carry a QR code linking to the website", pages)
    assert not ok


def test_parse_json_tolerates_code_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json("Here you go: {\"rules\": []} thanks") == {"rules": []}
    assert parse_json("not json") is None


def _client(monkeypatch, tmp_path, key, handler):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", key)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    return LLMClient(tmp_path, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_google_cloud_key_uses_vertex_and_falls_back_to_an_available_model(monkeypatch, tmp_path):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        assert req.headers["x-goog-api-key"] == "AQ.test-key"
        assert "key=" not in str(req.url)                      # never put the key in the URL
        if "gemini-2.5-flash:" in str(req.url):
            return httpx.Response(404, json={"error": {"message": "model not found"}})
        body = json.loads(req.content)
        assert body["contents"][0]["parts"][-1]["text"].startswith("Say hi")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{\"ok\": true}"}]}}]})

    llm = _client(monkeypatch, tmp_path, "AQ.test-key", handler)
    assert llm.enabled
    assert llm.json("system", "Say hi") == {"ok": True}
    assert seen[0].startswith("https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash")
    assert "gemini-3.5-flash" in seen[-1]
    assert llm.describe()["endpoint"] == "Vertex AI (express)"


def test_ai_studio_key_uses_the_gemini_api(monkeypatch, tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "generativelanguage.googleapis.com"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hello"}]}}]})

    llm = _client(monkeypatch, tmp_path, "AIzaTEST", handler)
    assert llm.complete("system", "hi") == "hello"


def test_unreachable_ai_switches_itself_off(monkeypatch, tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked")

    llm = _client(monkeypatch, tmp_path, "AQ.test-key", handler)
    assert llm.complete("system", "hi") is None
    assert not llm.enabled and llm.describe()["configured"]
