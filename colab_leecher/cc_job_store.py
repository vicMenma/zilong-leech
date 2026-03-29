"""
colab_leecher/cc_job_store.py
Persistent JSON store for CloudConvert jobs.

New fields vs v1:
  progress_pct  — 0-100, weighted from per-task percent
  active_task   — human name of the currently running CC task
  elapsed_s     — seconds since job created (updated on every poll)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict, fields as dc_fields
from typing import Optional

log = logging.getLogger(__name__)

_STORE_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
_STORE_PATH = os.path.join(_STORE_DIR, "cc_jobs.json")
JOB_LINGER  = 6 * 3600


@dataclass
class CCJob:
    job_id:       str
    uid:          int
    fname:        str
    sub_fname:    str   = ""
    output_name:  str   = ""
    status:       str   = "processing"    # processing | finished | error
    error_msg:    str   = ""
    export_url:   str   = ""
    finished_at:  float = 0.0
    notified:     bool  = False
    task_message: str   = ""
    progress_pct: float = 0.0        # 0-100, weighted from per-task pct
    active_task:  str   = ""         # e.g. "hardsub", "convert", "import-video"
    elapsed_s:    int   = 0          # seconds elapsed since created_at
    created_at:   float = field(default_factory=time.time)


_KNOWN_FIELDS = {f.name for f in dc_fields(CCJob)}


class CCJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, CCJob] = {}
        self._lock = asyncio.Lock()
        self._load()

    # ── Persistence ──────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(_STORE_PATH, encoding="utf-8") as fh:
                raw = json.load(fh)
            for jid, d in raw.items():
                try:
                    filtered = {k: v for k, v in d.items() if k in _KNOWN_FIELDS}
                    self._jobs[jid] = CCJob(**filtered)
                except Exception:
                    pass
            log.info("[CCJobStore] Loaded %d jobs", len(self._jobs))
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning("[CCJobStore] Load error: %s", exc)

    def _save(self) -> None:
        try:
            os.makedirs(_STORE_DIR, exist_ok=True)
            with open(_STORE_PATH, "w", encoding="utf-8") as fh:
                json.dump({jid: asdict(j) for jid, j in self._jobs.items()}, fh, indent=2)
        except Exception as exc:
            log.warning("[CCJobStore] Save error: %s", exc)

    def _evict(self) -> None:
        now  = time.time()
        dead = [
            jid for jid, j in self._jobs.items()
            if j.status in ("finished", "error")
            and j.finished_at > 0
            and now - j.finished_at > JOB_LINGER
        ]
        for jid in dead:
            self._jobs.pop(jid, None)

    # ── Write API ────────────────────────────────────────────

    async def add(self, job: CCJob) -> None:
        async with self._lock:
            self._evict()
            self._jobs[job.job_id] = job
            self._save()
        log.info("[CCJobStore] Added job %s uid=%d fname=%s",
                 job.job_id, job.uid, job.fname)

    async def update(self, job_id: str, **kw) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for k, v in kw.items():
                if hasattr(job, k):
                    setattr(job, k, v)
            job.elapsed_s = int(time.time() - job.created_at)
            self._save()

    async def finish(self, job_id: str, export_url: str = "", error_msg: str = "") -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if error_msg:
                job.status       = "error"
                job.error_msg    = error_msg
                job.progress_pct = 0.0
            else:
                job.status       = "finished"
                job.export_url   = export_url
                job.progress_pct = 100.0
            job.finished_at = time.time()
            job.elapsed_s   = int(job.finished_at - job.created_at)
            self._save()
        log.info("[CCJobStore] Job %s → %s", job_id, "error" if error_msg else "finished")

    async def mark_notified(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.notified = True
                self._save()

    # ── Read API ─────────────────────────────────────────────

    def get(self, job_id: str) -> Optional[CCJob]:
        return self._jobs.get(job_id)

    def jobs_for_user(self, uid: int) -> list[CCJob]:
        self._evict()
        return sorted(
            [j for j in self._jobs.values() if j.uid == uid],
            key=lambda j: j.created_at, reverse=True,
        )

    def active_jobs(self) -> list[CCJob]:
        return [j for j in self._jobs.values() if j.status == "processing"]

    def all_jobs(self) -> list[CCJob]:
        self._evict()
        return list(self._jobs.values())


# Singleton
cc_job_store = CCJobStore()
