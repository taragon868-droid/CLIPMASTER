"""Download a VOD or record a slice of a live stream with yt-dlp."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from yt_dlp import YoutubeDL

from .ffmpeg_util import probe

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".ts"}


def _opts(outtmpl: str, hook, extra: dict | None = None) -> dict:
    opts = {
        "format": "bv*[height<=720][vcodec!=none]+ba/b[height<=720]/b",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
    }
    if extra:
        opts.update(extra)
    return opts


def _throttle(callback, stage_message: str):
    last = {"t": 0.0, "pct": -1.0}

    def hook(event: dict) -> None:
        if event.get("status") != "downloading":
            return
        now = time.time()
        total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
        done = event.get("downloaded_bytes") or 0
        pct = (done / total * 100) if total else 0
        if now - last["t"] < 0.7 and abs(pct - last["pct"]) < 3:
            return
        last["t"] = now
        last["pct"] = pct
        callback(min(24, pct * 0.24), f"{stage_message} {pct:.0f}%" if total else stage_message)

    return hook


def describe(url: str) -> dict:
    with YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 30}) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError("That link is a playlist with no videos.")
        info = entries[0]
    live_status = str(info.get("live_status") or "")
    return {
        "title": info.get("title") or "Untitled",
        "duration": float(info.get("duration") or 0),
        "is_live": bool(info.get("is_live") or live_status == "is_live"),
        "was_live": live_status in {"is_live", "post_live", "was_live"},
        "platform": info.get("extractor_key") or info.get("extractor") or "video",
        "webpage": info.get("webpage_url") or url,
    }


def _find_source(folder: Path) -> Path:
    files = [p for p in folder.iterdir() if p.stem.startswith("source") and p.suffix.lower() in VIDEO_EXTS]
    if not files:
        raise RuntimeError("The download finished but no video file was saved.")
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0]


def fetch(url: str, folder: Path, minutes: int, live_from_start: bool, progress) -> dict:
    meta = describe(url)
    seconds = max(60, int(minutes) * 60)
    progress(1, f"Found {meta['platform']}: {meta['title']}")
    outtmpl = str(folder / "source.%(ext)s")

    if meta["is_live"]:
        _record_live(url, outtmpl, seconds, live_from_start, progress)
        mode = "from the start" if live_from_start else "from right now"
        progress(24, f"Recorded the live {mode}")
    else:
        extra = {}
        duration = meta["duration"]
        if duration and duration > seconds + 5:
            from yt_dlp.utils import download_range_func

            extra["download_ranges"] = download_range_func(None, [(0.0, float(seconds))])
            progress(2, f"Video is {duration / 60:.0f} min. Using the first {minutes} min.")
        else:
            progress(2, "Downloading")
        hook = _throttle(progress, "Downloading")
        with YoutubeDL(_opts(outtmpl, hook, extra)) as ydl:
            ydl.download([url])

    source = _find_source(folder)
    # Keep a stable name for the rest of the pipeline.
    stable = folder / f"source{source.suffix.lower()}"
    if source.resolve() != stable.resolve():
        if stable.exists():
            stable.unlink()
        shutil.move(str(source), str(stable))
        source = stable
    info = probe(source)
    meta["duration"] = info["duration"]
    meta["path"] = str(source)
    meta["width"] = info["width"]
    meta["height"] = info["height"]
    meta["has_audio"] = info["has_audio"]
    return meta


def _record_live(url: str, outtmpl: str, seconds: int, live_from_start: bool, progress) -> None:
    """Record `seconds` of a live stream. ffmpeg stops the input with -t.

    YouTube can rewind a live. Twitch and Kick usually cannot, so a failed
    rewind falls back to recording from the current moment.
    """
    try:
        _record_live_once(url, outtmpl, seconds, live_from_start, progress)
    except Exception:
        if not live_from_start:
            raise
        progress(2, "This live cannot rewind. Recording from now instead.")
        _record_live_once(url, outtmpl, seconds, False, progress)


def _record_live_once(url: str, outtmpl: str, seconds: int, live_from_start: bool, progress) -> None:
    hook = _throttle(progress, "Recording live")
    extra = {
        "live_from_start": bool(live_from_start),
        "external_downloader": "ffmpeg",
        "external_downloader_args": {"ffmpeg_i": ["-t", str(int(seconds))]},
    }
    label = "Recording from the start of the live" if live_from_start else "Recording the live from now"
    progress(2, f"{label} ({seconds // 60} min)")
    with YoutubeDL(_opts(outtmpl, hook, extra)) as ydl:
        ydl.download([url])


def import_upload(upload: Path, folder: Path) -> dict:
    if not upload.exists():
        raise RuntimeError("The uploaded file is missing. Upload it again.")
    dest = folder / f"source{upload.suffix.lower() or '.mp4'}"
    if upload.resolve() != dest.resolve():
        shutil.copy2(upload, dest)
    info = probe(dest)
    return {
        "title": upload.stem,
        "duration": info["duration"],
        "is_live": False,
        "was_live": False,
        "platform": "upload",
        "path": str(dest),
        "width": info["width"],
        "height": info["height"],
        "has_audio": info["has_audio"],
    }
