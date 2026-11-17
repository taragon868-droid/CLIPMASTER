"""Local checks that do not need a network video."""

import subprocess
import sys
import tempfile
from pathlib import Path

from app.ffmpeg_util import probe
from app.publish import build_post_body, fallback_caption
from app.layout import crop_to_aspect, plan_layout
from app.render import render_clip


def test_crop_stays_inside() -> None:
    for w, h, aspect, cx, cy in (
        (1920, 1080, 1080 / 1920, 400, 540),
        (1280, 720, 1080 / 960, 200, 500),
        (720, 1280, 1080 / 1920, 360, 640),
        (640, 360, 1080 / 1312, 80, 300),
    ):
        cw, ch, x, y = crop_to_aspect(w, h, aspect, cx, cy)
        assert cw % 2 == 0 and ch % 2 == 0
        assert x >= 0 and y >= 0
        assert x + cw <= w
        assert y + ch <= h
        assert cw >= 2 and ch >= 2


def test_layout_modes() -> None:
    center = plan_layout(1920, 1080, [])
    assert center["mode"] == "center"
    face = (200, 700, 180, 180)
    frames = [[face] for _ in range(6)]
    facecam = plan_layout(1920, 1080, frames)
    assert facecam["mode"] == "facecam"
    left = (300, 200, 280, 280)
    right = (1300, 180, 260, 260)
    split = plan_layout(1920, 1080, [[left, right] for _ in range(6)])
    assert split["mode"] == "split"


def test_tiktok_post_has_no_music() -> None:
    body = build_post_body("sa_tiktok_1", "Hello\n\n#shorts", "media_1", "PUBLIC_TO_EVERYONE")
    post = body["platform_posts"][0]
    options = post["platform_options"]
    assert options["auto_add_music"] is False
    assert options["upload_mode"] == "file_upload"
    assert "music_sound_id" not in options
    assert "music" not in options
    caption = fallback_caption("Model Quality")
    assert caption.startswith("Model Quality")
    assert "#shorts" in caption
    assert "#fyp" in caption


def test_render() -> None:
    folder = Path(tempfile.mkdtemp(prefix="clipmaster-"))
    source = folder / "source.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "4",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(source),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    words = [
        {"start": 0.2, "end": 0.8, "word": "Hello"},
        {"start": 0.8, "end": 1.4, "word": "from"},
        {"start": 1.4, "end": 2.2, "word": "Clipmaster."},
    ]
    dest = folder / "clips" / "clip_00.mp4"
    render_clip(source, dest, 0.2, 3.4, words, "Watch this")
    info = probe(dest)
    assert info["width"] == 1080 and info["height"] == 1920, info
    assert dest.stat().st_size > 10_000
    print(f"render ok {dest} {info['width']}x{info['height']}")


if __name__ == "__main__":
    test_crop_stays_inside()
    test_layout_modes()
    test_tiktok_post_has_no_music()
    test_render()
    print("selftest passed")
    sys.exit(0)
