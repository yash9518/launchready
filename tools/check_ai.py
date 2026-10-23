"""Check that the AI key in .env works:  python tools/check_ai.py

Prints which provider, endpoint and model answered, or the error. The key itself is never printed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

env = ROOT / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from app.llm import LLMClient  # noqa: E402

llm = LLMClient(ROOT / ".cache" / "llm_check")
if not llm.provider:
    print("No AI key found. Copy .env.example to .env and add GEMINI_API_KEY=... (or ANTHROPIC_/OPENAI_API_KEY).")
    sys.exit(1)
reply = llm.json("You are a test.", 'Return {"ok": true, "model_says": "<one short word>"}', max_tokens=50)
info = llm.describe()
if reply:
    print(f"AI assist works: provider={info['provider']} endpoint={info['endpoint'] or '-'} model={info['model']}")
    print(f"Reply: {reply}")
else:
    print(f"AI call failed: {info['last_error']}")
    sys.exit(2)
