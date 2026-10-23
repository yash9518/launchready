"""Optional LLM layer (Google Gemini / Vertex AI, Anthropic or OpenAI), configured with environment variables.

The checker is fully functional without an LLM. When a key is present, the LLM
is used for: compiling free-text rulebooks into rule cards, a second
independent reading of raster artwork, judgment-only rules, confirming
"missing" items on raster artwork, and follow-up questions.

Every LLM output is treated as a *proposal*: callers verify it against the
artwork text or the rulebook before using it (see `AiEvent` in the results).
Responses are cached on disk, so demos are repeatable and cheap.

Google keys
-----------
* Keys starting with ``AIza`` are Gemini API (Google AI Studio) keys.
* Keys starting with ``AQ.`` are Google Cloud API keys, used with Vertex AI express mode.
The client tries the matching endpoint first and falls back to the other one, and
falls back through a short list of current Gemini models if a model is unavailable.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from PIL import Image

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-4.1-mini",
}
# Tried in order after the configured model when Google answers "model not found".
GEMINI_FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-3-flash", "gemini-2.5-flash-lite"]

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
VERTEX_EXPRESS = "https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent"


def _detect() -> tuple[Optional[str], Optional[str], Optional[str]]:
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower() or None
    keys = {
        "gemini": os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("VERTEX_API_KEY"),
        "anthropic": os.getenv("ANTHROPIC_API_KEY"),
        "openai": os.getenv("OPENAI_API_KEY"),
    }
    if provider in ("none", "off", "disabled"):
        return None, None, None
    if provider in ("google", "vertex"):
        provider = "gemini"
    if provider is None:
        provider = next((p for p, k in keys.items() if k), None)
    if provider is None or provider not in keys or not keys.get(provider):
        return None, None, None
    model = os.getenv(f"{provider.upper()}_MODEL") or os.getenv("LLM_MODEL") or DEFAULT_MODELS[provider]
    return provider, model, keys[provider].strip()


def parse_json(text: Optional[str]) -> Optional[Any]:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except Exception:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = t.find(open_c)
        end = t.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(t[start:end + 1])
            except Exception:
                continue
    return None


def prepare_image(data: bytes, max_side: int = 1568) -> tuple[str, str]:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        s = max_side / max(w, h)
        img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"


class _Retry(Exception):
    """Temporary failure (rate limit, overload): wait and try again."""


class _Unavailable(Exception):
    """This endpoint/model combination does not work for this key: try the next one."""


class LLMClient:
    def __init__(self, cache_dir: Path, http: Optional[httpx.Client] = None):
        self.provider, self.model, self._key = _detect()
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.last_error: Optional[str] = None
        self.calls = 0
        self._no_temperature = False
        self._http = http or httpx.Client(timeout=120)
        self._google_route: Optional[tuple[str, str]] = None   # (endpoint template, model) that worked
        self._dead = False                                       # every route failed: stop trying this session

    @property
    def enabled(self) -> bool:
        return bool(self.provider and self._key) and not self._dead

    def describe(self) -> dict:
        model = self._google_route[1] if self._google_route else self.model
        endpoint = None
        if self.provider == "gemini":
            endpoint = "Vertex AI (express)" if self._google_route and "aiplatform" in self._google_route[0] else (
                "Gemini API" if self._google_route else "auto")
        return {"enabled": self.enabled, "provider": self.provider, "model": model, "endpoint": endpoint,
                "last_error": self.last_error, "configured": bool(self.provider and self._key)}

    # ------------------------------------------------------------ public API
    def json(self, system: str, prompt: str, images: Optional[list[bytes]] = None,
             max_tokens: int = 4000) -> Optional[Any]:
        text = self.complete(system, prompt + "\n\nReply with one JSON object and nothing else.", images,
                             max_tokens=max_tokens, json_mode=True)
        out = parse_json(text)
        if text and out is None:
            self.last_error = "LLM reply was not valid JSON"
        return out

    def complete(self, system: str, prompt: str, images: Optional[list[bytes]] = None, max_tokens: int = 1500,
                 json_mode: bool = False) -> Optional[str]:
        if not self.enabled:
            return None
        images = images or []
        h = hashlib.sha256()
        for part in (self.provider, self.model, system, prompt, str(json_mode)):
            h.update((part or "").encode())
        for im in images:
            h.update(hashlib.sha256(im).digest())
        cache_file = self.cache_dir / f"{h.hexdigest()[:40]}.json"
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())["text"]
            except Exception:
                pass
        prepared = [prepare_image(im) for im in images]
        for attempt in range(3):
            try:
                self.calls += 1
                if self.provider == "anthropic":
                    text = self._anthropic(system, prompt, prepared, max_tokens)
                elif self.provider == "openai":
                    text = self._openai(system, prompt, prepared, max_tokens, json_mode)
                else:
                    text = self._gemini(system, prompt, prepared, max_tokens, json_mode)
                cache_file.write_text(json.dumps({"text": text}))
                self.last_error = None
                return text
            except _Retry as exc:
                self.last_error = str(exc)
                time.sleep(2 * (attempt + 1))
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("LLM call failed: %s", self.last_error)
                if isinstance(exc, (httpx.ConnectError, httpx.ProxyError, _Unavailable)):
                    # Network blocked or key unusable: switch AI assist off for this session
                    # instead of slowing every check down with failing calls.
                    self._dead = True
                return None
        return None

    # ------------------------------------------------------------ providers
    def _post(self, url: str, headers: dict, body: dict, params: Optional[dict] = None) -> dict:
        r = self._http.post(url, headers=headers, json=body, params=params)
        if r.status_code in (429, 500, 502, 503, 504, 529):
            raise _Retry(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    def _anthropic(self, system, prompt, images, max_tokens) -> str:
        content: list[dict] = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}}
                               for b64, mt in images]
        content.append({"type": "text", "text": prompt})
        body = {"model": self.model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": content}]}
        if not self._no_temperature:
            body["temperature"] = 0
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            data = self._post("https://api.anthropic.com/v1/messages", headers, body)
        except RuntimeError as exc:
            if "temperature" in str(exc).lower() and "temperature" in body:
                self._no_temperature = True
                body.pop("temperature")
                data = self._post("https://api.anthropic.com/v1/messages", headers, body)
            else:
                raise
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    def _openai(self, system, prompt, images, max_tokens, json_mode) -> str:
        parts: list[dict] = [{"type": "text", "text": prompt}]
        parts += [{"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64}"}} for b64, mt in images]
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens,
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": parts}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = self._post("https://api.openai.com/v1/chat/completions",
                          {"Authorization": f"Bearer {self._key}", "content-type": "application/json"}, body)
        return data["choices"][0]["message"]["content"] or ""

    def _google_routes(self) -> list[tuple[str, str]]:
        if self._google_route:
            return [self._google_route]
        endpoints = [VERTEX_EXPRESS, GEMINI_API] if self._key.startswith("AQ.") else [GEMINI_API, VERTEX_EXPRESS]
        models = list(dict.fromkeys([self.model] + GEMINI_FALLBACK_MODELS))
        return [(ep, m) for ep in endpoints for m in models]

    def _gemini(self, system, prompt, images, max_tokens, json_mode) -> str:
        parts: list[dict] = [{"inlineData": {"mimeType": mt, "data": b64}} for b64, mt in images]
        parts.append({"text": prompt})
        body: dict[str, Any] = {"systemInstruction": {"parts": [{"text": system}]},
                                "contents": [{"role": "user", "parts": parts}],
                                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0}}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        errors = []
        for endpoint, model in self._google_routes():
            url = endpoint.format(model=model)
            try:
                # Key in a header, never in the URL, so it cannot leak into request logs.
                data = self._post(url, {"content-type": "application/json", "x-goog-api-key": self._key}, body)
            except RuntimeError as exc:
                msg = str(exc)
                # 400 "API key not valid", 401/403 (wrong endpoint for this key type), 404 (model not offered)
                if re.match(r"HTTP (400|401|403|404)\b", msg):
                    errors.append(f"{'Vertex' if 'aiplatform' in endpoint else 'Gemini API'} {model}: {msg[:90]}")
                    continue
                raise
            self._google_route = (endpoint, model)
            cands = data.get("candidates") or []
            if not cands:
                reason = (data.get("promptFeedback") or {}).get("blockReason")
                return "" if not reason else f'{{"error": "blocked: {reason}"}}'
            return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", [])
                           if not p.get("thought"))
        raise _Unavailable("No Google endpoint accepted this key: " + " | ".join(errors[:3]))
