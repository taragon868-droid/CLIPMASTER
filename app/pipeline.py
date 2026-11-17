from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path
from queue import Queue

from . import ingest, jobs, moments, publish, render
from .config import JOBS
from .ffmpeg_util import probe
from .transcribe import transcribe

_queue: Queue = Queue()
_started = False
_lock = threading.Lock()


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_loop, name="clipmaster-worker", daemon=True).start()


def enqueue(kind: str, job_id: str, clip_index: int | None = None) -> None:
    start()
    _queue.put((kind, job_id, clip_index))


def _loop() -> None:
    while True:
        kind, job_id, clip_index = _queue.get()
        try:
            if kind == "process":
                _process(job_id)
            elif kind == "rerender":
                _rerender(job_id, int(clip_index))
            elif kind == "captions":
                _captions(job_id)
            elif kind == "publish":
                _publish(job_id)
        except Exception as exc:
            tb = traceback.format_exc(limit=6)
            try:
                jobs.log(job_id, str(exc), status="failed", error=str(exc), stage="failed")
                jobs.mutate(job_id, lambda job: job.setdefault("logs", []).append(tb))
            except Exception:
                pass
        finally:
            _queue.task_done()


def _settings(raw: dict) -> dict:
    minutes = int(raw.get("minutes") or 30)
    clips = int(raw.get("clips") or 5)
    min_sec = int(raw.get("min_sec") or 15)
    max_sec = int(raw.get("max_sec") or 60)
    minutes = max(1, min(180, minutes))
    clips = max(1, min(10, clips))
    min_sec = max(8, min(90, min_sec))
    max_sec = max(min_sec + 5, min(180, max_sec))
    return {
        "minutes": minutes,
        "clips": clips,
        "min_sec": min_sec,
        "max_sec": max_sec,
        "live_from_start": bool(raw.get("live_from_start")),
    }


def create_job(payload: dict) -> dict:
    settings = _settings(payload)
    url = str(payload.get("url") or "").strip()
    upload_id = str(payload.get("upload_id") or "").strip()
    upload_path = ""
    if upload_id:
        folder = JOBS.parent / "uploads"
        matches = list(folder.glob(f"{upload_id}.*"))
        if not matches:
            raise FileNotFoundError("Upload not found")
        upload_path = str(matches[0])
    if not url and not upload_path:
        raise ValueError("Paste a link or upload a video.")
    job = jobs.create(settings, source_url=url, upload_path=upload_path)
    enqueue("process", job["id"])
    return job


def _process(job_id: str) -> None:
    job = jobs.get(job_id)
    settings = job["settings"]
    jobs.log(job_id, "Starting", status="running", stage="download", progress=1)

    def progress(pct: float, message: str) -> None:
        jobs.log(job_id, message, progress=int(pct), stage=job_stage(pct))

    folder = jobs.job_dir(job_id)
    if job.get("upload_path"):
        progress(5, "Reading the uploaded file")
        meta = ingest.import_upload(Path(job["upload_path"]), folder)
    else:
        meta = ingest.fetch(
            job["source_url"],
            folder,
            minutes=settings["minutes"],
            live_from_start=settings["live_from_start"],
            progress=progress,
        )

    source = Path(meta["path"])
    duration = float(meta["duration"] or 0)
    if duration < 3:
        raise RuntimeError("The video is shorter than 3 seconds.")
    jobs.log(
        job_id,
        f"Ready: {meta['title']}",
        title=meta["title"],
        platform=meta["platform"],
        duration=round(duration, 2),
        was_live=bool(meta.get("is_live") or meta.get("was_live")),
        stage="transcribe",
        progress=25,
    )

    def tprogress(pct: float, message: str) -> None:
        jobs.log(job_id, message, progress=int(pct), stage="transcribe")

    transcript = transcribe(source, duration, tprogress)
    (folder / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    if not transcript["words"]:
        raise RuntimeError(
            "No speech was detected. Gameplay with no commentary has nothing for the model to clip."
        )
    jobs.log(
        job_id,
        f"Transcript ready ({transcript['language'] or 'unknown'}, {len(transcript['words'])} words)",
        language=transcript["language"],
        stage="analyze",
        progress=60,
    )

    def aprogress(pct: float, message: str) -> None:
        jobs.log(job_id, message, progress=int(pct), stage="analyze")

    clips, how = moments.pick(
        transcript,
        meta["title"],
        duration,
        settings["clips"],
        settings["min_sec"],
        settings["max_sec"],
        aprogress,
    )
    if not clips:
        raise RuntimeError("No clip-length moments were found in the transcript.")
    for index, clip in enumerate(clips):
        clip["index"] = index
        clip["ready"] = False
        clip["rev"] = 0
        clip["file"] = f"clip_{index:02d}.mp4"
    jobs.mutate(job_id, lambda current: current.__setitem__("clips", clips))
    jobs.log(job_id, f"Picked {len(clips)} moment{'s' if len(clips) != 1 else ''} ({how})", stage="render", progress=80)
    _render_all(job_id, source, transcript["words"], clips)
    jobs.log(job_id, "Writing TikTok titles and hashtags", stage="caption", progress=96)
    publish.write_captions(job_id)

    def ready(current: dict) -> None:
        current["tiktok"] = {"state": "review", "error": "", "account": ""}

    jobs.mutate(job_id, ready)
    jobs.log(
        job_id,
        "Clips are ready. Approve them before they go to TikTok.",
        status="completed",
        stage="done",
        progress=100,
    )


def job_stage(pct: float) -> str:
    if pct < 25:
        return "download"
    if pct < 60:
        return "transcribe"
    if pct < 80:
        return "analyze"
    return "render"


def _render_all(job_id: str, source: Path, words: list[dict], clips: list[dict]) -> None:
    info = probe(source)
    total = len(clips)
    for index, clip in enumerate(clips):
        jobs.log(
            job_id,
            f"Rendering {index + 1}/{total}: {clip['title']}",
            stage="render",
            progress=80 + int(18 * index / max(1, total)),
        )
        dest = jobs.job_dir(job_id) / "clips" / clip["file"]
        render.render_clip(source, dest, clip["start"], clip["end"], words, clip.get("hook") or "", info)

        def mark(current, idx=index):
            current["clips"][idx]["ready"] = True
            current["clips"][idx]["rev"] = current["clips"][idx].get("rev", 0) + 1

        jobs.mutate(job_id, mark)


def request_captions(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job.get("status") != "completed":
        raise ValueError("Clips are not ready.")
    queue_it = {"yes": False}

    def edit(current: dict) -> None:
        tiktok = current.setdefault("tiktok", {})
        if tiktok.get("state") in {"writing", "uploading"}:
            return
        missing = any(clip.get("ready") and not str(clip.get("caption") or "").strip() for clip in current.get("clips") or [])
        if not missing:
            if tiktok.get("state") != "dismissed":
                tiktok["state"] = "review"
            return
        tiktok["state"] = "writing"
        tiktok["error"] = ""
        queue_it["yes"] = True

    updated = jobs.mutate(job_id, edit)
    if queue_it["yes"]:
        enqueue("captions", job_id)
    return updated


def request_publish(job_id: str, edits: list) -> dict:
    jobs.get(job_id)
    by_index = {}
    for item in edits:
        if not isinstance(item, dict) or "index" not in item:
            continue
        try:
            by_index[int(item["index"])] = str(item.get("caption") or "").strip()[:2200]
        except (TypeError, ValueError):
            continue

    def edit(current: dict) -> None:
        if current.get("status") != "completed":
            raise ValueError("Clips are not ready.")
        tiktok = current.setdefault("tiktok", {})
        if tiktok.get("state") == "uploading":
            raise RuntimeError("An upload is already running.")
        ready = [clip for clip in current.get("clips") or [] if clip.get("ready")]
        if not ready:
            raise ValueError("There are no clips to upload.")
        for clip in ready:
            if clip["index"] in by_index and by_index[clip["index"]]:
                clip["caption"] = by_index[clip["index"]]
                clip["caption_auto"] = False
            if not str(clip.get("caption") or "").strip():
                raise ValueError("Each clip needs a caption before upload.")
        if all(clip.get("post_status") == "posted" for clip in ready):
            tiktok["state"] = "done"
            tiktok["error"] = ""
            return
        tiktok["state"] = "uploading"
        tiktok["error"] = ""

    updated = jobs.mutate(job_id, edit)
    if updated.get("tiktok", {}).get("state") == "uploading":
        enqueue("publish", job_id)
    return updated


def dismiss_tiktok(job_id: str) -> dict:
    def edit(current: dict) -> None:
        if current.get("tiktok", {}).get("state") == "uploading":
            raise RuntimeError("An upload is already running.")
        current.setdefault("tiktok", {})["state"] = "dismissed"

    return jobs.mutate(job_id, edit)


def _captions(job_id: str) -> None:
    publish.write_captions(job_id)

    def done(current: dict) -> None:
        tiktok = current.setdefault("tiktok", {})
        if tiktok.get("state") != "dismissed":
            tiktok["state"] = "review"
            tiktok["error"] = ""
        current["message"] = "Review the TikTok captions, then approve the upload."

    jobs.mutate(job_id, done)
    jobs.log(job_id, "TikTok captions are ready")


def _publish(job_id: str) -> None:
    try:
        publish.upload_job(job_id)
    except Exception as exc:
        message = str(exc) or "Upload failed"

        def fail(current: dict) -> None:
            tiktok = current.setdefault("tiktok", {})
            tiktok["state"] = "failed"
            tiktok["error"] = message[:300]
            current["message"] = message[:300]

        jobs.mutate(job_id, fail)
        jobs.log(job_id, message)


def request_rerender(job_id: str, index: int, start: float, end: float, title: str, hook: str) -> dict:
    job = jobs.get(job_id)
    if index < 0 or index >= len(job.get("clips") or []):
        raise IndexError("Clip not found")
    if end <= start:
        raise ValueError("The end time has to be after the start.")
    duration = float(job.get("duration") or 0)
    start = max(0.0, start)
    if duration:
        end = min(duration, end)

    def edit(current: dict) -> None:
        clip = current["clips"][index]
        clip["start"] = round(start, 2)
        clip["end"] = round(end, 2)
        clip["title"] = (title or clip["title"])[:90]
        clip["hook"] = (hook or clip["hook"])[:80]
        clip["ready"] = False

    updated = jobs.mutate(job_id, edit)
    enqueue("rerender", job_id, index)
    return updated


def _rerender(job_id: str, index: int) -> None:
    job = jobs.get(job_id)
    clip = job["clips"][index]
    folder = jobs.job_dir(job_id)
    transcript = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    source = next(folder.glob("source.*"))
    jobs.log(job_id, f"Re-rendering clip {index + 1}", status="running", stage="render")
    dest = folder / "clips" / clip["file"]
    render.render_clip(
        source,
        dest,
        float(clip["start"]),
        float(clip["end"]),
        transcript.get("words") or [],
        clip.get("hook") or "",
        probe(source),
    )

    def mark(current: dict) -> None:
        current["clips"][index]["ready"] = True
        current["clips"][index]["rev"] = current["clips"][index].get("rev", 0) + 1
        if current["status"] != "failed":
            current["status"] = "completed"
            current["stage"] = "done"
            current["progress"] = 100
            current["message"] = "Clip updated"

    jobs.mutate(job_id, mark)
