"""
colab_leecher/forward_channels.py
Persistent store for forward channels.

Data stored in data/forward_channels.json as a list of:
  {"id": int, "name": str}

The bot copies every delivered file (hardsub / leech) to all configured
channels automatically, without re-downloading — the local file is still
present in tmp when forwarding occurs inside _deliver_job.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

_DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
_STORE_PATH = os.path.join(_DATA_DIR, "forward_channels.json")


class ForwardChannelStore:
    def __init__(self) -> None:
        self._channels: list[dict] = []
        self._lock = asyncio.Lock()
        self._load()

    # ── Persistence ───────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(_STORE_PATH, encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, list):
                self._channels = raw
            log.info("[FwdChannels] Loaded %d channel(s)", len(self._channels))
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning("[FwdChannels] Load error: %s", exc)

    def _save(self) -> None:
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with open(_STORE_PATH, "w", encoding="utf-8") as fh:
                json.dump(self._channels, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            log.warning("[FwdChannels] Save error: %s", exc)

    # ── Write API ─────────────────────────────────────────────

    async def add(self, channel_id: int, name: str) -> bool:
        """Add a channel. Returns False if it was already present."""
        async with self._lock:
            if any(c["id"] == channel_id for c in self._channels):
                return False
            self._channels.append({"id": channel_id, "name": name[:60]})
            self._save()
            return True

    async def remove(self, channel_id: int) -> bool:
        """Remove a channel by ID. Returns True if removed."""
        async with self._lock:
            before = len(self._channels)
            self._channels = [c for c in self._channels if c["id"] != channel_id]
            if len(self._channels) < before:
                self._save()
                return True
            return False

    # ── Read API ──────────────────────────────────────────────

    def all(self) -> list[dict]:
        return list(self._channels)

    def count(self) -> int:
        return len(self._channels)

    def get(self, channel_id: int) -> Optional[dict]:
        return next((c for c in self._channels if c["id"] == channel_id), None)


# Singleton shared across modules
fwd_channels = ForwardChannelStore()
