"""LaunchReady web app (FastAPI). Run:  uvicorn app.main:app --port 8000"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

from app.ai import answer_question  # noqa: E402
from app.extract.ocr import OCR_ERROR, ocr_available  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.pipeline import evaluate, run_check, run_to_json, set_rules_enabled  # noqa: E402
from app.report import build_report  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)   # never log request URLs
log = logging.getLogger("launchready")

RUNS = ROOT / ".runs"
SAMPLES = ROOT / "data" / "samples"
RULEBOOKS = ROOT / "data" / "rulebooks"
WEB = ROOT / "web"
MAX_UPLOAD = 60 * 1024 * 1024

app = FastAPI(title="LaunchReady", version="1.0")
llm = LLMClient(ROOT / ".cache" / "llm")
STORE: dict[str, dict] = {}
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
app.mount("/static", StaticFiles(directory=WEB), name="static")


def _manifest() -> list[dict]:
    """Samples whose files are present (the public repo ships without Esko's sample files)."""
    p = SAMPLES / "manifest.json"
    items = json.loads(p.read_text()) if p.exists() else []
    return [s for s in items if (SAMPLES / s["id"] / s["artwork"]).exists()]


def _save_upload(up: UploadFile, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    name = Path(up.filename or "upload.bin").name.replace(" ", "_")
    dest = folder / name
    size = 0
    with dest.open("wb") as fh:
        while chunk := up.file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                raise HTTPException(413, "File too large (limit 60 MB).")
            fh.write(chunk)
    return dest


def _get(run_id: str) -> dict:
    run = STORE.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found (the server may have restarted). Please run the check again.")
    return run


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((WEB / "index.html").read_text(encoding="utf-8"))


def _rulebook_title(p: Path) -> str:
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except Exception:
        pass
    return p.stem.replace("_", " ")


@app.get("/api/status")
def status() -> dict:
    return {
        "llm": llm.describe(),
        "ocr": {"available": ocr_available(), "error": OCR_ERROR},
        "samples": _manifest(),
        "rulebooks": [{"name": p.name, "title": _rulebook_title(p)} for p in sorted(RULEBOOKS.iterdir())
                      if p.is_file()],
        "today": dt.date.today().isoformat(),
    }


@app.post("/api/check")
def check(artwork: Optional[UploadFile] = File(None), rulebook: Optional[UploadFile] = File(None),
          approved: Optional[UploadFile] = File(None), sample_id: Optional[str] = Form(None),
          rulebook_name: Optional[str] = Form(None), as_of: Optional[str] = Form(None),
          use_sample_approved: Optional[str] = Form("true"), wait: Optional[str] = Form("false")) -> JSONResponse:
    run_id = uuid.uuid4().hex[:10]
    run_dir = RUNS / run_id
    inputs = run_dir / "inputs"
    sample = next((s for s in _manifest() if s["id"] == sample_id), None) if sample_id else None
    if artwork is not None and artwork.filename:
        art_path = _save_upload(artwork, inputs)
    elif sample:
        art_path = SAMPLES / sample["id"] / sample["artwork"]
    else:
        raise HTTPException(400, "Choose a sample or upload an artwork file (PDF or image).")
    if rulebook is not None and rulebook.filename:
        rb_path = _save_upload(rulebook, inputs)
    else:
        name = rulebook_name or (sample["rulebook"] if sample else "us_fda_granola_rulebook.md")
        rb_path = RULEBOOKS / Path(name).name
        if not rb_path.exists():
            raise HTTPException(400, f"Unknown rulebook '{name}'.")
    appr_path = None
    if approved is not None and approved.filename:
        appr_path = _save_upload(approved, inputs)
    elif sample and use_sample_approved != "false":
        appr_path = SAMPLES / sample["id"] / sample["approved"]
    try:
        day = dt.date.fromisoformat(as_of) if as_of else dt.date.today()
    except ValueError:
        day = dt.date.today()
    JOBS[run_id] = {"status": "running", "stage": "Starting", "started": time.time()}

    def work() -> None:
        try:
            run = run_check(art_path, art_path.name, rb_path, rb_path.name, appr_path,
                            appr_path.name if appr_path else None, run_dir, llm=llm, as_of=day,
                            progress=lambda s: JOBS[run_id].update(stage=s))
            with LOCK:
                STORE[run_id] = run
            JOBS[run_id].update(status="done", stage="Done")
        except ValueError as exc:
            JOBS[run_id].update(status="error", error=str(exc))
        except Exception as exc:  # pragma: no cover
            log.exception("check failed")
            JOBS[run_id].update(status="error", error=f"The check failed: {type(exc).__name__}: {exc}")

    if wait == "true":
        work()
        if JOBS[run_id]["status"] == "error":
            raise HTTPException(400, JOBS[run_id]["error"])
        return JSONResponse(run_to_json(STORE[run_id], run_id) | {"status": "done"})
    threading.Thread(target=work, daemon=True).start()
    return JSONResponse({"id": run_id, "status": "running", "stage": "Starting"})


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> JSONResponse:
    job = JOBS.get(run_id)
    if job and job["status"] != "done":
        return JSONResponse({"id": run_id, "status": job["status"], "stage": job.get("stage"),
                             "error": job.get("error"), "elapsed": round(time.time() - job["started"], 1)})
    return JSONResponse(run_to_json(_get(run_id), run_id) | {"status": "done"})


@app.get("/api/samples/{sample_id}/thumb.png")
def sample_thumb(sample_id: str) -> FileResponse:
    sample = next((s for s in _manifest() if s["id"] == sample_id), None)
    if not sample:
        raise HTTPException(404, "Unknown sample")
    out = ROOT / ".cache" / "thumbs" / f"{sample_id}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        src = SAMPLES / sample_id / sample["artwork"]
        if src.suffix.lower() == ".pdf":
            import pymupdf
            page = pymupdf.open(src)[0]
            zoom = 520 / max(page.rect.width, page.rect.height)
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(str(out))
        else:
            from PIL import Image
            img = Image.open(src).convert("RGB")
            img.thumbnail((520, 520))
            img.save(out)
    return FileResponse(out, media_type="image/png")


@app.get("/api/runs/{run_id}/pages/{n}.png")
def page_image(run_id: str, n: int) -> FileResponse:
    run = _get(run_id)
    pages = run["artwork"].pages
    if n < 0 or n >= len(pages):
        raise HTTPException(404, "No such page")
    return FileResponse(Path(run["run_dir"]) / pages[n].image, media_type="image/png")


class RerunBody(BaseModel):
    disabled: list[str] = []
    as_of: Optional[str] = None


@app.post("/api/runs/{run_id}/rerun")
def rerun(run_id: str, body: RerunBody) -> JSONResponse:
    run = _get(run_id)
    set_rules_enabled(run, set(body.disabled))
    try:
        day = dt.date.fromisoformat(body.as_of) if body.as_of else dt.date.today()
    except ValueError:
        day = dt.date.today()
    evaluate(run, day)
    return JSONResponse(run_to_json(run, run_id))


class AskBody(BaseModel):
    rule_id: str
    question: str


@app.post("/api/runs/{run_id}/ask")
def ask(run_id: str, body: AskBody) -> dict:
    run = _get(run_id)
    finding = next((f for f in run["findings"] + run["future"] if f.rule_id == body.rule_id), None)
    if finding is None:
        raise HTTPException(404, "Finding not found")
    q = body.question.strip()[:500]
    if not q:
        raise HTTPException(400, "Please type a question.")
    return answer_question(finding, q, run, llm)


@app.get("/api/runs/{run_id}/report.html", response_class=HTMLResponse)
def report(run_id: str) -> HTMLResponse:
    return HTMLResponse(build_report(_get(run_id)))


@app.get("/api/runs/{run_id}/report.json")
def report_json(run_id: str) -> JSONResponse:
    data = run_to_json(_get(run_id), run_id)
    return JSONResponse(data, headers={"Content-Disposition": f"attachment; filename=launchready_{run_id}.json"})


@app.get("/api/samples/{sample_id}/{filename}")
def sample_file(sample_id: str, filename: str) -> FileResponse:
    p = (SAMPLES / sample_id / filename).resolve()
    if not str(p).startswith(str(SAMPLES.resolve())) or not p.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(p)


@app.get("/api/rulebooks/{name}", response_class=PlainTextResponse)
def rulebook_text(name: str) -> PlainTextResponse:
    p = (RULEBOOKS / Path(name).name)
    if not p.exists():
        raise HTTPException(404, "Not found")
    if p.suffix.lower() in (".md", ".txt", ".yaml", ".yml", ".json"):
        return PlainTextResponse(p.read_text(encoding="utf-8"))
    return PlainTextResponse(f"{p.name} (binary document)")
