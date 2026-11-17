"""TikTok captions and UniPost uploads.

Video posts send the rendered file only. No music id is attached, and
auto_add_music is false so TikTok is not asked to lay a sound on top.
"""

from __future__ import annotations

import json
import re
import time

import httpx

from . import jobs, moments

API = "https://api.unipost.dev"

CAPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["index", "caption", "hashtags"],
            },
        }
    },
    "required": ["clips"],
}


class PublishError(RuntimeError):
    pass


def fallback_caption(title: str) -> str:
    title = (title or "Clip").strip()[:90] or "Clip"
    words = re.findall(r"[A-Za-z0-9]{4,}", title)[:4]
    tags = ["#fyp", "#shorts"] + [f"#{word.lower()}" for word in words]
    unique = list(dict.fromkeys(tags))
    return f"{title}\n\n{' '.join(unique)}"


def clean_caption(text: str, hashtags: list | None) -> str:
    lines = [line.strip() for line in str(text or "").replace("\r", "").split("\n")]
    body = "\n".join(lines).strip()
    tags = []
    for tag in hashtags or []:
        raw = re.sub(r"[^\w]", "", str(tag).lstrip("#"), flags=re.UNICODE)
        if raw:
            tags.append("#" + raw[:40])
    if tags and "#" not in body:
        body = f"{body}\n\n{' '.join(tags[:8])}".strip()
    return body[:2200]


def build_post_body(account_id: str, caption: str, media_id: str, privacy: str, *, allow_music_flag: bool = True) -> dict:
    options = {
        "privacy_level": privacy,
        "upload_mode": "file_upload",
    }
    if allow_music_flag:
        options["auto_add_music"] = False
    return {
        "platform_posts": [
            {
                "account_id": account_id,
                "caption": caption[:2200],
                "media_ids": [media_id],
                "platform_options": options,
            }
        ]
    }


def write_captions(job_id: str) -> None:
    job = jobs.get(job_id)
    folder = jobs.job_dir(job_id)
    transcript_path = folder / "transcript.json"
    segments = []
    language = job.get("language") or ""
    if transcript_path.exists():
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        segments = transcript.get("segments") or []
        language = language or transcript.get("language") or ""

    blocks = []
    for clip in job.get("clips") or []:
        if not clip.get("ready"):
            continue
        spoken = moments._lines(segments, clip["start"], clip["end"], limit=800) if segments else ""
        blocks.append(
            f"CLIP {clip['index']}\nWorking title: {clip.get('title') or 'Clip'}\nSpoken:\n{spoken or '(no transcript)'}"
        )

    by_index: dict[int, str] = {}
    if blocks:
        system = (
            "You write TikTok captions. The video already has burned-in subtitles and its original audio. "
            "Do not mention music, sounds, or a soundtrack. Output only the JSON schema."
        )
        user = (
            f"Language: {language or 'the language of the spoken words'}\n"
            "For each clip write one caption in that language.\n"
            "- First line: a hook title under 90 characters, no hashtags.\n"
            "- Then one short sentence about what is said.\n"
            "- Then 5 to 8 hashtags on one line. Each tag starts with # and has no spaces inside it.\n"
            "- Stay under 400 characters.\n\n"
            + "\n\n".join(blocks)
        )
        try:
            data = moments._chat(system, user, CAPTION_SCHEMA, 900)
            for item in data.get("clips") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    index = int(item.get("index"))
                except (TypeError, ValueError):
                    continue
                by_index[index] = clean_caption(str(item.get("caption") or ""), item.get("hashtags") or [])
        except Exception:
            by_index = {}

    def apply(current: dict) -> None:
        for clip in current.get("clips") or []:
            if clip.get("caption") and clip.get("caption_auto") is False:
                continue
            caption = by_index.get(clip["index"]) or fallback_caption(clip.get("title") or "")
            if not caption.strip():
                caption = fallback_caption(clip.get("title") or "")
            clip["caption"] = caption
            clip["caption_auto"] = True
            clip.setdefault("post_status", "")
            clip.setdefault("post_error", "")

    jobs.mutate(job_id, apply)


def _key_headers() -> dict:
    from .secrets import api_key

    key = api_key()
    if not key:
        raise PublishError("Add your UniPost API key in the side panel.")
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        return (response.text or f"UniPost returned {response.status_code}")[:400]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:400]
        if isinstance(err, str):
            return err[:400]
        if body.get("message"):
            return str(body["message"])[:400]
    return str(body)[:400]


def _request(method: str, path: str, **kwargs) -> dict | list:
    headers = dict(_key_headers())
    headers.update(kwargs.pop("headers", {}) or {})
    if "json" in kwargs:
        headers["Content-Type"] = "application/json"
    try:
        response = httpx.request(method, f"{API}{path}", headers=headers, timeout=kwargs.pop("timeout", 60), **kwargs)
    except httpx.HTTPError as exc:
        raise PublishError(f"Could not reach UniPost ({exc.__class__.__name__}).") from None
    if response.status_code >= 400:
        raise PublishError(_error_text(response))
    if not response.content:
        return {}
    body = response.json()
    return body


def _object(body: dict | list) -> dict:
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    if isinstance(body, dict):
        return body
    raise PublishError("UniPost returned an unexpected response.")


def tiktok_account() -> dict:
    body = _request("GET", "/v1/accounts", params={"platform": "tiktok"})
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        rows = []
    active = [row for row in rows if isinstance(row, dict) and row.get("status") == "active"]
    chosen = (active or [row for row in rows if isinstance(row, dict)])
    if not chosen:
        raise PublishError("No TikTok account is connected in UniPost. Connect it at app.unipost.dev, then try again.")
    return chosen[0]


def _privacy(account_id: str) -> str:
    try:
        body = _object(_request("GET", f"/v1/accounts/{account_id}/tiktok/creator-info"))
    except PublishError:
        return "PUBLIC_TO_EVERYONE"
    options = body.get("privacy_level_options") or []
    if "PUBLIC_TO_EVERYONE" in options or not options:
        return "PUBLIC_TO_EVERYONE"
    return str(options[0])


def _put_video(upload_url: str, path) -> None:
    data = path.read_bytes()
    try:
        response = httpx.put(upload_url, content=data, headers={"Content-Type": "video/mp4"}, timeout=600)
    except httpx.HTTPError as exc:
        raise PublishError(f"Could not upload the video ({exc.__class__.__name__}).") from None
    if response.status_code in {400, 403}:
        try:
            response = httpx.put(upload_url, content=data, timeout=600)
        except httpx.HTTPError as exc:
            raise PublishError(f"Could not upload the video ({exc.__class__.__name__}).") from None
    if response.status_code >= 300:
        raise PublishError(f"UniPost storage rejected the video ({response.status_code}).")


def _wait_uploaded(media_id: str) -> None:
    for _ in range(40):
        body = _object(_request("GET", f"/v1/media/{media_id}"))
        status = str(body.get("status") or "")
        if status in {"uploaded", "attached"}:
            return
        if status == "deleted":
            raise PublishError("UniPost deleted the video before it could be posted.")
        time.sleep(1.5)
    raise PublishError("UniPost did not finish receiving the video.")


def _reserve(path, filename: str) -> tuple[str, str]:
    body = _object(
        _request(
            "POST",
            "/v1/media",
            json={"filename": filename, "content_type": "video/mp4", "size_bytes": path.stat().st_size},
        )
    )
    media_id = str(body.get("id") or "")
    upload_url = str(body.get("upload_url") or "")
    if not media_id or not upload_url:
        raise PublishError("UniPost did not return an upload URL.")
    return media_id, upload_url


def _publish(account_id: str, caption: str, media_id: str, privacy: str, idempotency: str) -> str:
    body = build_post_body(account_id, caption, media_id, privacy, allow_music_flag=True)
    body["idempotency_key"] = idempotency[:64]
    try:
        _request("POST", "/v1/posts/validate", json=body)
    except PublishError as exc:
        if "auto_add_music" not in str(exc).lower():
            raise
        body = build_post_body(account_id, caption, media_id, privacy, allow_music_flag=False)
        body["idempotency_key"] = idempotency[:64]
        _request("POST", "/v1/posts/validate", json=body)
    created = _object(_request("POST", "/v1/posts", json=body))
    return str(created.get("id") or created.get("post_id") or "")


def upload_job(job_id: str) -> None:
    account = tiktok_account()
    account_id = str(account.get("id") or "")
    if not account_id:
        raise PublishError("UniPost did not return a TikTok account id.")
    name = account.get("account_name") or account_id
    privacy = _privacy(account_id)
    jobs.mutate(
        job_id,
        lambda current: current.setdefault("tiktok", {}).update(
            {"account": name, "privacy": privacy, "state": "uploading", "error": ""}
        ),
    )
    job = jobs.get(job_id)
    folder = jobs.job_dir(job_id)
    pending = [clip for clip in job.get("clips") or [] if clip.get("ready") and clip.get("post_status") != "posted"]
    total = len(pending)
    failures = []
    for offset, clip in enumerate(pending, start=1):
        index = clip["index"]
        jobs.log(job_id, f"Uploading {offset}/{total} to TikTok: {clip.get('title') or 'Clip'}")

        def mark_uploading(current, idx=index):
            current["clips"][idx]["post_status"] = "uploading"
            current["clips"][idx]["post_error"] = ""

        jobs.mutate(job_id, mark_uploading)
        try:
            path = folder / "clips" / clip["file"]
            if not path.exists():
                raise PublishError("Clip file is missing.")
            media_id, upload_url = _reserve(path, f"clip-{index:02d}.mp4")
            _put_video(upload_url, path)
            _wait_uploaded(media_id)
            post_id = _publish(
                account_id,
                clip.get("caption") or fallback_caption(clip.get("title") or ""),
                media_id,
                privacy,
                f"cm-{job_id}-{index}",
            )

            def mark_posted(current, idx=index, pid=post_id):
                current["clips"][idx]["post_status"] = "posted"
                current["clips"][idx]["post_id"] = pid
                current["clips"][idx]["post_error"] = ""

            jobs.mutate(job_id, mark_posted)
            jobs.log(job_id, f"TikTok accepted clip {offset}/{total}")
        except Exception as exc:
            message = str(exc) or "Upload failed"
            failures.append(message)

            def mark_failed(current, idx=index, text=message):
                current["clips"][idx]["post_status"] = "failed"
                current["clips"][idx]["post_error"] = text[:300]

            jobs.mutate(job_id, mark_failed)
            jobs.log(job_id, f"Clip {offset} was not posted: {message}")

    def finish(current: dict) -> None:
        tiktok = current.setdefault("tiktok", {})
        if failures:
            tiktok["state"] = "failed"
            tiktok["error"] = failures[0]
            current["message"] = f"{total - len(failures)} of {total} posted. {failures[0]}"
        else:
            tiktok["state"] = "done"
            tiktok["error"] = ""
            current["message"] = f"Uploaded {total} clip{'s' if total != 1 else ''} to TikTok"

    jobs.mutate(job_id, finish)
