from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import jobs, moments, pipeline, publish
from .secrets import api_key, save_key
from .config import HOST, PORT, STATIC, UPLOADS
from .render import safe_filename

_ID = re.compile(r"^[a-f0-9]{32}$")


def _job_or_404(job_id: str) -> dict:
    if not _ID.fullmatch(job_id):
        raise HTTPException(404, "Job not found")
    try:
        return jobs.get(job_id)
    except KeyError:
        raise HTTPException(404, "Job not found") from None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    jobs.mark_interrupted()
    pipeline.start()
    print(f"Clipmaster  http://{HOST}:{PORT}")
    yield


app = FastAPI(title="Clipmaster", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
def health():
    ready, detail = moments.ollama_ready()
    return {
        "ok": ready,
        "model": moments.MODEL,
        "detail": detail,
        "whisper": "small on CPU",
        "encoder": "libx264",
    }


@app.get("/api/settings")
def get_settings():
    key = api_key()
    return {"unipost": bool(key), "hint": f"…{key[-4:]}" if len(key) >= 4 else ""}


@app.put("/api/settings")
def put_settings(payload: dict):
    key = str(payload.get("unipost_api_key") or "").strip()
    if len(key) < 8:
        raise HTTPException(400, "Paste the UniPost API key from app.unipost.dev.")
    save_key(key)
    account = ""
    warning = ""
    try:
        row = publish.tiktok_account()
        account = str(row.get("account_name") or row.get("id") or "")
    except publish.PublishError as exc:
        warning = str(exc)
    return {"unipost": True, "hint": f"…{key[-4:]}", "account": account, "warning": warning}


@app.post("/api/jobs/{job_id}/captions")
def write_captions(job_id: str):
    _job_or_404(job_id)
    try:
        updated = pipeline.request_captions(job_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return jobs.public_view(updated)


@app.post("/api/jobs/{job_id}/tiktok")
def post_tiktok(job_id: str, payload: dict):
    _job_or_404(job_id)
    if not payload.get("confirm"):
        raise HTTPException(400, "Confirm the upload before anything is sent to TikTok.")
    try:
        updated = pipeline.request_publish(job_id, payload.get("clips") or [])
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return jobs.public_view(updated)


@app.post("/api/jobs/{job_id}/tiktok/dismiss")
def dismiss_tiktok(job_id: str):
    _job_or_404(job_id)
    try:
        updated = pipeline.dismiss_tiktok(job_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return jobs.public_view(updated)


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": [jobs.public_view(job) for job in jobs.list_jobs()]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return jobs.public_view(_job_or_404(job_id))


@app.post("/api/jobs")
def create_job(payload: dict):
    ready, detail = moments.ollama_ready()
    if not ready:
        raise HTTPException(400, detail)
    try:
        job = pipeline.create_job(payload)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return jobs.public_view(job)


@app.post("/api/jobs/{job_id}/clips/{index}/rerender")
def rerender(job_id: str, index: int, payload: dict):
    _job_or_404(job_id)
    try:
        start = float(payload.get("start"))
        end = float(payload.get("end"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Start and end must be seconds.") from None
    try:
        updated = pipeline.request_rerender(
            job_id,
            index,
            start,
            end,
            str(payload.get("title") or ""),
            str(payload.get("hook") or ""),
        )
    except (IndexError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return jobs.public_view(updated)


@app.get("/api/jobs/{job_id}/transcript")
def transcript(job_id: str):
    _job_or_404(job_id)
    path = jobs.job_dir(job_id) / "transcript.json"
    if not path.exists():
        raise HTTPException(404, "Transcript is not ready")
    return FileResponse(path, media_type="application/json")


@app.get("/api/jobs/{job_id}/clips/{index}/file")
def clip_file(job_id: str, index: int, download: int = 0):
    job = _job_or_404(job_id)
    clips = job.get("clips") or []
    if index < 0 or index >= len(clips):
        raise HTTPException(404, "Clip not found")
    clip = clips[index]
    path = jobs.job_dir(job_id) / "clips" / clip["file"]
    if not path.exists():
        raise HTTPException(404, "Clip file is not ready")
    if download:
        return FileResponse(path, media_type="video/mp4", filename=safe_filename(clip.get("title") or "clip"))
    return FileResponse(path, media_type="video/mp4")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "video.mp4").suffix.lower()
    if suffix not in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}:
        raise HTTPException(400, "Upload an mp4, mkv, webm, or mov file.")
    upload_id = jobs.new_id()
    dest = UPLOADS / f"{upload_id}{suffix}"
    size = 0
    with dest.open("wb") as handle:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 4 * 1024 * 1024 * 1024:
                dest.unlink(missing_ok=True)
                raise HTTPException(400, "File is larger than 4 GB.")
            handle.write(chunk)
    return {"upload_id": upload_id, "name": file.filename, "bytes": size}
