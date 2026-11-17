"""Pick short moments from a transcript with the local Ollama model."""

from __future__ import annotations

import json
import re

import httpx

from .config import MODEL, NUM_CTX, OLLAMA_URL

PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "score": {"type": "integer"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["start", "end", "score", "title", "hook", "reason"],
            },
        }
    },
    "required": ["clips"],
}

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["score", "reason"],
}


def ollama_ready() -> tuple[bool, str]:
    try:
        response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        response.raise_for_status()
    except Exception as exc:
        return False, f"Ollama is not running ({exc})"
    names = [item.get("name", "") for item in response.json().get("models", [])]
    base = MODEL.split(":")[0]
    if any(name == MODEL or name.split(":")[0] == base for name in names):
        return True, ""
    return False, f"Model {MODEL} is not pulled. Installed: {', '.join(names) or 'none'}"


def _chat(system: str, user: str, schema: dict, predict: int) -> dict:
    payload = {
        "model": MODEL,
        "stream": False,
        "format": schema,
        "keep_alive": "10m",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {
            "temperature": 0.2,
            "num_ctx": NUM_CTX,
            "num_predict": predict,
        },
    }
    response = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=600)
    response.raise_for_status()
    content = response.json().get("message", {}).get("content", "")
    return _parse_json(content)


def _parse_json(content: str) -> dict:
    text = content.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise RuntimeError("The local model did not return JSON.")
        data = json.loads(match.group(0))
    if isinstance(data, list):
        return {"clips": data}
    return data


def _lines(segments: list[dict], start: float, end: float, limit: int = 6000) -> str:
    rows = []
    for seg in segments:
        if seg["end"] < start or seg["start"] > end:
            continue
        text = seg["text"].strip()
        if text:
            rows.append(f"[{seg['start']:.1f}-{seg['end']:.1f}] {text}")
    blob = "\n".join(rows)
    if len(blob) > limit:
        blob = blob[:limit]
    return blob


def _windows(segments: list[dict], window_s: float = 150, overlap: float = 20) -> list[tuple[float, float]]:
    if not segments:
        return []
    t0 = segments[0]["start"]
    t1 = segments[-1]["end"]
    if t1 - t0 <= window_s * 1.25:
        return [(t0, t1)]
    spans = []
    cursor = t0
    while cursor < t1 - 8:
        end = min(t1, cursor + window_s)
        spans.append((cursor, end))
        if end >= t1 - 1:
            break
        cursor = end - overlap
    return spans


def _snap(bound: float, words: list[dict], side: str) -> float:
    for word in words:
        if word["start"] - 0.08 <= bound <= word["end"] + 0.08:
            return float(word["start"] if side == "start" else word["end"])
    return bound


def _normalize(raw_clips: list, words: list[dict], duration: float, min_sec: float, max_sec: float, count: int) -> list[dict]:
    cleaned = []
    for item in raw_clips:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if end < start:
            start, end = end, start
        start = max(0.0, _snap(start, words, "start"))
        end = min(duration, _snap(end, words, "end"))
        length = end - start
        if length < max(8.0, min_sec - 3) or length > max_sec + 12:
            continue
        if length > max_sec:
            end = start + max_sec
        title = str(item.get("title") or "Clip").strip()[:90]
        hook = str(item.get("hook") or title).strip()[:80]
        try:
            score = int(float(item.get("score") or 0))
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(100, score))
        cleaned.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "score": score,
                "title": title,
                "hook": hook,
                "reason": str(item.get("reason") or "").strip()[:240],
            }
        )
    cleaned.sort(key=lambda clip: clip["score"], reverse=True)
    chosen = []
    for clip in cleaned:
        if any(not (clip["end"] <= other["start"] + 0.4 or clip["start"] >= other["end"] - 0.4) for other in chosen):
            continue
        chosen.append(clip)
        if len(chosen) >= count:
            break
    chosen.sort(key=lambda clip: clip["start"])
    return chosen


def _fallback(segments: list[dict], duration: float, min_sec: float, max_sec: float, count: int) -> list[dict]:
    if not segments:
        return []
    target = min(max_sec, max(min_sec, 28))
    scored = []
    for index, seg in enumerate(segments):
        start = seg["start"]
        end = start
        texts = []
        j = index
        while j < len(segments) and segments[j]["end"] - start < target:
            texts.append(segments[j]["text"])
            end = segments[j]["end"]
            j += 1
        if end - start < min_sec * 0.7:
            continue
        blob = " ".join(texts)
        score = 40
        score += min(20, blob.count("?") * 8 + blob.count("!") * 5)
        words = blob.split()
        if 35 <= len(words) <= 130:
            score += 15
        if duration > 180 and (start < duration * 0.04 or end > duration * 0.96):
            score -= 15
        scored.append((score, start, end, blob))
    scored.sort(key=lambda item: item[0], reverse=True)
    chosen = []
    for score, start, end, blob in scored:
        if any(not (end <= other["start"] + 1 or start >= other["end"] - 1) for other in chosen):
            continue
        title = " ".join(blob.split()[:10]).strip(" .")
        chosen.append(
            {
                "start": round(start, 2),
                "end": round(min(duration, end), 2),
                "score": int(max(1, min(100, score))),
                "title": (title[:70] or "Clip"),
                "hook": " ".join(blob.split()[:7])[:80],
                "reason": "Picked from the transcript because the model returned nothing usable.",
            }
        )
        if len(chosen) >= count:
            break
    chosen.sort(key=lambda clip: clip["start"])
    return chosen


def pick(transcript: dict, title: str, duration: float, count: int, min_sec: float, max_sec: float, progress) -> tuple[list[dict], str]:
    segments = transcript.get("segments") or []
    words = transcript.get("words") or []
    if duration <= max(12, min_sec):
        text = " ".join(seg["text"] for seg in segments).strip()
        hook = " ".join(text.split()[:7])[:80] or title[:80]
        return (
            [
                {
                    "start": 0,
                    "end": round(duration, 2),
                    "score": 70,
                    "title": title[:90] or "Clip",
                    "hook": hook or "Watch this",
                    "reason": "The source is already short, so the whole video is the clip.",
                }
            ],
            "short",
        )

    windows = _windows(segments)
    chosen_spans = windows
    note = "model"
    if len(windows) > 1:
        ranked = []
        for index, (start, end) in enumerate(windows, start=1):
            progress(60 + int(12 * index / len(windows)), f"Scoring section {index}/{len(windows)}")
            body = _lines(segments, start, end)
            if len(body) < 40:
                ranked.append((0, start, end))
                continue
            try:
                data = _chat(
                    "You score transcript windows for short-form video potential. Reply with the schema only.",
                    (
                        f"Score this section from 0 to 100 for a TikTok, Short, or Reel. "
                        f"High scores need a complete idea, a strong opinion, a story, a joke, or a useful tip. "
                        f"Low scores are intros, outros, ads, and filler.\n\n{body}"
                    ),
                    SCORE_SCHEMA,
                    120,
                )
                score = int(float(data.get("score") or 0))
            except Exception:
                score = 0
            ranked.append((score, start, end))
        ranked.sort(key=lambda item: item[0], reverse=True)
        chosen_spans = [(start, end) for score, start, end in ranked[:5] if score > 0] or [
            (start, end) for _, start, end in ranked[:3]
        ]

    chunks = []
    for start, end in chosen_spans:
        text = _lines(segments, start, end, limit=3500)
        if text:
            chunks.append(text)
    transcript_text = "\n".join(chunks)[:12000]
    if not transcript_text.strip():
        return _fallback(segments, duration, min_sec, max_sec, count), "fallback"

    progress(74, "Choosing the clips")
    system = (
        "You are a short-form video editor. Pick the moments worth posting. "
        "Times are seconds on the original video clock. Output only the JSON schema."
    )
    user = (
        f"Title: {title or 'Untitled'}\n"
        f"Language: {transcript.get('language') or 'unknown'}\n"
        f"Video length: {duration:.1f} seconds\n"
        f"Return up to {count} clips. Each clip must be between {min_sec:.0f} and {max_sec:.0f} seconds.\n"
        "Rules:\n"
        "- Do not overlap clips.\n"
        "- Start and end on a complete thought.\n"
        "- Skip greetings, outros, sponsor reads, and dead air.\n"
        "- title: a posting title, under 70 characters, no hashtags.\n"
        "- hook: 4 to 8 words shown on screen at the start.\n"
        "- score: integer 0-100.\n"
        "- reason: one short sentence.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
    )
    try:
        data = _chat(system, user, PICK_SCHEMA, 1400)
        clips = _normalize(data.get("clips") or [], words, duration, min_sec, max_sec, count)
    except Exception:
        clips = []
        note = "fallback"
    if not clips:
        clips = _fallback(segments, duration, min_sec, max_sec, count)
        note = "fallback"
    return clips, note
