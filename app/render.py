from __future__ import annotations

import re
from pathlib import Path

from . import faces, layout
from .ffmpeg_util import probe, run


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def ass_escape(text: str) -> str:
    text = " ".join(text.replace("\n", " ").split())
    text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return text


def caption_cues(words: list[dict], clip_start: float, clip_end: float, limit: int = 42) -> list[tuple[float, float, str]]:
    cues = []
    buf: list[str] = []
    start = None
    last_end = None
    for word in words:
        if word["end"] < clip_start + 0.05 or word["start"] > clip_end - 0.02:
            continue
        token = str(word.get("word", "")).strip()
        if not token:
            continue
        rel_start = max(0.0, word["start"] - clip_start)
        rel_end = max(rel_start + 0.05, word["end"] - clip_start)
        if start is None:
            start = rel_start
        buf.append(token)
        last_end = rel_end
        text = " ".join(buf)
        boundary = token[-1:] in ".?!"
        if len(text) >= limit or boundary or (last_end - start) >= 3.0:
            cues.append((start, last_end, text))
            buf = []
            start = None
    if buf and start is not None and last_end is not None:
        cues.append((start, last_end, " ".join(buf)))
    return cues


def write_ass(path: Path, cues: list[tuple[float, float, str]], hook: str, caption_style: str, duration: float) -> None:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bottom,Arial,64,&H00FFFFFF,&H000000FF,&H00101010,&H96000000,-1,0,0,0,100,100,0,0,1,5,0,2,70,70,210,1
Style: Middle,Arial,60,&H00FFFFFF,&H000000FF,&H00101010,&H96000000,-1,0,0,0,100,100,0,0,1,5,0,5,80,80,0,1
Style: Hook,Arial,72,&H0048C8FF,&H000000FF,&H00101010,&H96000000,-1,0,0,0,100,100,0,0,1,5,0,8,70,70,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    hook = ass_escape(hook.strip())
    if hook:
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(min(3.2, duration))},Hook,,0,0,0,,{hook}")
    for start, end, text in cues:
        if end - start < 0.08:
            end = start + 0.08
        lines.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{caption_style},,0,0,0,,{ass_escape(text)}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def render_clip(
    source: Path,
    dest: Path,
    start: float,
    end: float,
    words: list[dict],
    hook: str,
    info: dict | None = None,
) -> None:
    info = info or probe(source)
    duration = max(0.4, end - start)
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent
    subs = work / "subs.ass"
    frames = []
    try:
        frames = faces.sample_faces(source, start, duration, info["width"], info["height"])
    except Exception:
        frames = []
    plan = layout.plan_layout(info["width"], info["height"], frames)
    cues = caption_cues(words, start, end)
    write_ass(subs, cues, hook, plan["caption"], duration)
    graph, complex_filter = layout.filtergraph(plan, info["width"], info["height"], with_subs=True)
    cmd = [
        "-ss",
        f"{max(0, start):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
    ]
    if complex_filter:
        cmd += ["-filter_complex", graph, "-map", "[v]"]
    else:
        cmd += ["-vf", graph, "-map", "0:v:0"]
    if info.get("has_audio"):
        cmd += ["-map", "0:a:0"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
    ]
    if info.get("has_audio"):
        cmd += ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]
    cmd += ["-movflags", "+faststart", str(dest)]
    try:
        run(cmd, timeout=max(180, duration * 20), cwd=work)
    except RuntimeError:
        # A missing font or a bad ass path should not throw the clip away.
        graph, complex_filter = layout.filtergraph(plan, info["width"], info["height"], with_subs=False)
        cmd = [
            "-ss",
            f"{max(0, start):.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
        ]
        if complex_filter:
            cmd += ["-filter_complex", graph, "-map", "[v]"]
        else:
            cmd += ["-vf", graph, "-map", "0:v:0"]
        if info.get("has_audio"):
            cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k"]
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(dest),
        ]
        run(cmd, timeout=max(180, duration * 20), cwd=work)


def safe_filename(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    return (cleaned[:60] or "clip") + ".mp4"
