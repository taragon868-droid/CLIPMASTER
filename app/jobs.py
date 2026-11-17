import json
import threading
import time
import uuid
from pathlib import Path

from .config import JOBS

_lock = threading.Lock()


def new_id() -> str:
    return uuid.uuid4().hex


def job_dir(job_id: str) -> Path:
    path = JOBS / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(job_id: str) -> Path:
    return JOBS / job_id / "job.json"


def _read(job_id: str) -> dict:
    path = _path(job_id)
    if not path.exists():
        raise KeyError(job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _write(job: dict) -> None:
    path = _path(job["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, indent=2), encoding="utf-8")
    tmp.replace(path)


def create(settings: dict, source_url: str = "", upload_path: str = "") -> dict:
    job = {
        "id": new_id(),
        "created": time.time(),
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Queued",
        "error": "",
        "title": "",
        "platform": "",
        "source_url": source_url,
        "upload_path": upload_path,
        "duration": 0,
        "language": "",
        "was_live": False,
        "settings": settings,
        "clips": [],
        "logs": [],
    }
    with _lock:
        _write(job)
    return job


def get(job_id: str) -> dict:
    with _lock:
        return _read(job_id)


def mutate(job_id: str, fn) -> dict:
    with _lock:
        job = _read(job_id)
        fn(job)
        _write(job)
        return job


def log(job_id: str, text: str, **fields) -> None:
    stamp = time.strftime("%H:%M:%S")

    def apply(job: dict) -> None:
        job.setdefault("logs", []).append(f"{stamp} {text}")
        job["logs"] = job["logs"][-300:]
        job["message"] = text
        job.update(fields)

    mutate(job_id, apply)


def list_jobs() -> list[dict]:
    found = []
    if not JOBS.exists():
        return found
    for folder in JOBS.iterdir():
        path = folder / "job.json"
        if path.exists():
            try:
                found.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    found.sort(key=lambda job: job.get("created", 0), reverse=True)
    return found


def public_view(job: dict) -> dict:
    """Drop fields the UI does not need on every poll."""
    data = dict(job)
    data.pop("upload_path", None)
    data.pop("_caption_queued", None)
    return data


def mark_interrupted() -> None:
    for job in list_jobs():
        if job.get("status") in {"queued", "running"}:
            log(
                job["id"],
                "Stopped because Clipmaster was restarted. Start this one again.",
                status="failed",
                error="Interrupted by restart",
                stage="failed",
            )
