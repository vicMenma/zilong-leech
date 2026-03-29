"""
colab_leecher/bot_name.py
Bot name management — ask once on first run, persist to data/bot_name.txt.

Priority:
  1. BOT_NAME env var
  2. data/bot_name.txt (persisted from interactive setup)
  3. "Zilong" (hardcoded fallback)
"""
from __future__ import annotations

import os

_DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
_NAME_FILE = os.path.join(_DATA_DIR, "bot_name.txt")
_cached: str = ""


def get_bot_name() -> str:
    global _cached
    if _cached:
        return _cached

    env = os.environ.get("BOT_NAME", "").strip()
    if env:
        _cached = env
        return _cached

    try:
        with open(_NAME_FILE, encoding="utf-8") as fh:
            name = fh.read().strip()
        if name:
            _cached = name
            return _cached
    except FileNotFoundError:
        pass

    return "Zilong"


def set_bot_name(name: str) -> None:
    global _cached
    _cached = name.strip()
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_NAME_FILE, "w", encoding="utf-8") as fh:
        fh.write(_cached)


def is_name_configured() -> bool:
    if os.environ.get("BOT_NAME", "").strip():
        return True
    return os.path.exists(_NAME_FILE)
