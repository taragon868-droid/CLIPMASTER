"""Vertical layout plans.

Layouts are pure numbers so they can be checked without a video file.
Coordinates are pixels in the source frame. aspect values are width / height.
"""

from __future__ import annotations


def even(value: float) -> int:
    """Nearest even integer at or below a rounded value. Zero stays zero."""
    number = int(round(value))
    if number <= 0:
        return 0
    return number - (number % 2)


def even_size(value: float) -> int:
    number = even(value)
    return number if number >= 2 else 2


def crop_to_aspect(src_w: int, src_h: int, aspect: float, cx: float, cy: float) -> tuple[int, int, int, int]:
    """Largest crop of the given aspect that stays inside the frame, biased toward (cx, cy)."""
    src_w = even_size(src_w)
    src_h = even_size(src_h)
    if src_w / src_h > aspect:
        crop_h = src_h
        crop_w = even_size(crop_h * aspect)
    else:
        crop_w = src_w
        crop_h = even_size(crop_w / aspect)
    crop_w = min(crop_w, src_w)
    crop_h = min(crop_h, src_h)
    if crop_w < 2 or crop_h < 2:
        return src_w, src_h, 0, 0
    crop_x = even(max(0, min(src_w - crop_w, cx - crop_w / 2)))
    crop_y = even(max(0, min(src_h - crop_h, cy - crop_h / 2)))
    if crop_x + crop_w > src_w:
        crop_x = even(src_w - crop_w)
    if crop_y + crop_h > src_h:
        crop_y = even(src_h - crop_h)
    return crop_w, crop_h, max(0, crop_x), max(0, crop_y)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _box_median(boxes: list[tuple[int, int, int, int]]) -> tuple[float, float, float, float]:
    xs, ys, ws, hs = [], [], [], []
    for x, y, w, h in boxes:
        xs.append(x + w / 2)
        ys.append(y + h / 2)
        ws.append(w)
        hs.append(h)
    return _median(xs), _median(ys), _median(ws), _median(hs)


def plan_layout(src_w: int, src_h: int, frames: list[list[tuple[int, int, int, int]]]) -> dict:
    """Pick track, split, or facecam from sampled face boxes.

    Each frame is a list of (x, y, w, h) boxes in source pixels.
    """
    src_w = even_size(src_w)
    src_h = even_size(src_h)
    populated = [boxes for boxes in frames if boxes]
    if len(populated) < 3:
        cw, ch, cx, cy = crop_to_aspect(src_w, src_h, 1080 / 1920, src_w / 2, src_h / 2)
        return {"mode": "center", "caption": "Bottom", "crop": (cw, ch, cx, cy)}

    largest = []
    pairs = []
    for boxes in populated:
        ordered = sorted(boxes, key=lambda box: box[2] * box[3], reverse=True)
        largest.append(ordered[0])
        if len(ordered) >= 2:
            a, b = ordered[0], ordered[1]
            # Second face has to be a real subject, not a tiny background face.
            if a[2] * a[3] > 0 and (b[2] * b[3]) / (a[2] * a[3]) >= 0.35:
                left, right = sorted((a, b), key=lambda box: box[0])
                gap = (right[0] + right[2] / 2) - (left[0] + left[2] / 2)
                if gap > src_w * 0.22:
                    pairs.append((left, right))

    if len(pairs) >= max(3, int(len(populated) * 0.4)):
        left_m = _box_median([pair[0] for pair in pairs])
        right_m = _box_median([pair[1] for pair in pairs])
        return {
            "mode": "split",
            "caption": "Middle",
            "faces": (left_m, right_m),
        }

    cx, cy, fw, fh = _box_median(largest)
    face_area = fw * fh
    frame_area = src_w * src_h
    in_corner = cx < src_w * 0.38 or cx > src_w * 0.62 or cy > src_h * 0.68
    small = face_area < frame_area * 0.08 and fw < src_w * 0.34
    if small and in_corner and len(populated) >= 4:
        return {
            "mode": "facecam",
            "caption": "Bottom",
            "face": (cx, cy, fw, fh),
        }

    cw, ch, crop_x, crop_y = crop_to_aspect(src_w, src_h, 1080 / 1920, cx, src_h / 2)
    return {"mode": "track", "caption": "Bottom", "crop": (cw, ch, crop_x, crop_y)}


def filtergraph(layout: dict, src_w: int, src_h: int, with_subs: bool) -> tuple[str, bool]:
    """Return (filter, uses_filter_complex)."""
    subs = ",ass=subs.ass" if with_subs else ""
    mode = layout["mode"]

    if mode in {"center", "track"}:
        cw, ch, cx, cy = layout["crop"]
        chain = f"crop={cw}:{ch}:{cx}:{cy},scale=1080:1920:flags=bicubic,setsar=1{subs},format=yuv420p"
        return chain, False

    if mode == "split":
        left, right = layout["faces"]
        parts = []
        labels = []
        for index, (cx, cy, fw, fh) in enumerate((left, right)):
            cw, ch, x, y = crop_to_aspect(src_w, src_h, 1080 / 960, cx, cy - fh * 0.15)
            label = "L" if index == 0 else "R"
            parts.append(f"[0:v]crop={cw}:{ch}:{x}:{y},scale=1080:960:flags=bicubic,setsar=1[{label}]")
            labels.append(f"[{label}]")
        graph = ";".join(parts)
        graph += f";{labels[0]}{labels[1]}vstack=inputs=2{subs},format=yuv420p[v]"
        return graph, True

    cx, cy, fw, fh = layout["face"]
    cw, ch, x, y = crop_to_aspect(src_w, src_h, 1080 / 1312, cx, cy)
    graph = (
        f"[0:v]scale=1080:608:force_original_aspect_ratio=decrease,"
        f"pad=1080:608:(ow-iw)/2:(oh-ih)/2:black,setsar=1[top];"
        f"[0:v]crop={cw}:{ch}:{x}:{y},scale=1080:1312:flags=bicubic,setsar=1[bot];"
        f"[top][bot]vstack=inputs=2{subs},format=yuv420p[v]"
    )
    return graph, True
