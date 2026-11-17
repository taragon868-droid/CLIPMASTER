import json
import os
import shutil
import subprocess
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("FFmpeg is not installed or not on PATH.")
    return path


def ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise RuntimeError("FFprobe is not installed or not on PATH.")
    return path


def run(args: list[str], timeout: float | None = None, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        [ffmpeg(), "-hide_banner", "-y", *args],
        capture_output=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
        creationflags=NO_WINDOW,
    )
    stderr = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        tail = stderr.strip().splitlines()[-12:]
        raise RuntimeError("FFmpeg failed: " + " | ".join(tail))
    return stderr


def probe(path: Path) -> dict:
    proc = subprocess.run(
        [
            ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=index,codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        creationflags=NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError("Could not read the video: " + proc.stderr.decode("utf-8", "replace")[-400:])
    data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("This file has no video stream.")
    duration = float(data.get("format", {}).get("duration") or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "duration": duration,
        "has_audio": has_audio,
    }
