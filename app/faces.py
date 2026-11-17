"""Sample faces with a Haar cascade. Used only to aim the crop, not to understand the scene."""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from .ffmpeg_util import NO_WINDOW, ffmpeg

_cascade = None


def _detector():
    global _cascade
    if _cascade is None:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _cascade = cv2.CascadeClassifier(path)
    return _cascade


def sample_faces(source: Path, start: float, duration: float, src_w: int, src_h: int) -> list[list[tuple[int, int, int, int]]]:
    if duration <= 0.2:
        return []
    scaled_w = 320
    scaled_h = max(2, int(round(src_h * scaled_w / src_w)))
    scaled_h -= scaled_h % 2
    fps = 40 / duration
    fps = min(2.0, max(0.4, fps))
    frame_bytes = scaled_w * scaled_h * 3
    cmd = [
        ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0, start):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
        "-an",
        "-vf",
        f"fps={fps:.3f},scale={scaled_w}:{scaled_h}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    frames: list[list[tuple[int, int, int, int]]] = []
    scale_x = src_w / scaled_w
    scale_y = src_h / scaled_h
    detector = _detector()
    assert proc.stdout is not None
    try:
        while len(frames) < 48:
            raw = proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            image = np.frombuffer(raw, dtype=np.uint8).reshape((scaled_h, scaled_w, 3)).copy()
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            found = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=4, minSize=(28, 28))
            boxes = []
            for x, y, w, h in found:
                boxes.append(
                    (
                        int(x * scale_x),
                        int(y * scale_y),
                        int(w * scale_x),
                        int(h * scale_y),
                    )
                )
            frames.append(boxes)
    finally:
        proc.kill()
        proc.wait(timeout=5)
    return frames
