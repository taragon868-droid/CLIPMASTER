from __future__ import annotations

from pathlib import Path

from faster_whisper import WhisperModel

from .config import WHISPER_COMPUTE, WHISPER_DEVICE, WHISPER_MODEL

_model: WhisperModel | None = None


def _load() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
    return _model


def transcribe(path: Path, duration: float, progress) -> dict:
    progress(26, f"Loading speech model ({WHISPER_MODEL}, {WHISPER_DEVICE}). First run downloads it.")
    model = _load()
    progress(30, "Transcribing")
    try:
        segments_iter, info = model.transcribe(
            str(path),
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
        )
    except Exception:
        segments_iter, info = model.transcribe(str(path), word_timestamps=True, vad_filter=False)

    segments = []
    words = []
    last_pct = 30
    for seg in segments_iter:
        segments.append(
            {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": (seg.text or "").strip(),
            }
        )
        for word in seg.words or []:
            token = (word.word or "").strip()
            if not token:
                continue
            words.append(
                {
                    "start": round(word.start, 2),
                    "end": round(word.end, 2),
                    "word": token,
                }
            )
        if duration > 0:
            pct = 30 + int(28 * min(1.0, seg.end / duration))
            if pct >= last_pct + 2:
                last_pct = pct
                progress(pct, f"Transcribing {seg.end / 60:.1f} min")

    return {
        "language": info.language or "",
        "duration": duration,
        "segments": segments,
        "words": words,
    }
