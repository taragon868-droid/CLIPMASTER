"""Local UniPost key. Stored under data/, which is gitignored."""

from __future__ import annotations

import json

from .config import DATA

PATH = DATA / "unipost.json"


def load() -> dict:
    if not PATH.exists():
        return {}
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def api_key() -> str:
    return str(load().get("api_key") or "").strip()


def save_key(key: str) -> None:
    PATH.write_text(json.dumps({"api_key": key.strip()}), encoding="utf-8")
