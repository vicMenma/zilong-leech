"""
colab_leecher/cloudconvert_api.py
CloudConvert API v2 client for zilong-leech.

Supports multi-key rotation via comma-separated CC_API_KEY:
    CC_API_KEY=eyJ...key1,eyJ...key2

Flows:
  1. submit_hardsub  — burn subtitles into video
  2. submit_convert  — resolution/format conversion (no subtitles)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

CC_API = "https://api.cloudconvert.com/v2"


# ─────────────────────────────────────────────────────────────
# Multi-key credit checking & rotation
# ─────────────────────────────────────────────────────────────

def parse_api_keys(raw: str) -> list[str]:
    """Parse comma-separated API keys from the env / credentials value."""
    return [k.strip() for k in raw.split(",") if k.strip()]


async def check_credits(api_key: str) -> int:
    """Return remaining conversion credits, or -1 on error."""
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"{CC_API}/users/me", headers=headers) as resp:
                if resp.status != 200:
                    log.warning("[CC-API] Credit check HTTP %d", resp.status)
                    return -1
                data = await resp.json()
                return int(data.get("data", {}).get("credits", 0))
    except Exception as exc:
        log.warning("[CC-API] Credit check error: %s", exc)
        return -1


async def pick_best_key(api_keys: list[str]) -> tuple[str, int]:
    """
    Check credits on all keys concurrently, return (best_key, credits).
    Raises RuntimeError if all keys are exhausted.
    """
    if len(api_keys) == 1:
        credits = await check_credits(api_keys[0])
        if credits == 0:
            raise RuntimeError(
                "CloudConvert: 0 credits on your only API key.\n"
                "Wait for daily reset or add more keys (comma-separated in CC_API_KEY)."
            )
        return api_keys[0], max(credits, 0)

    results = await asyncio.gather(*[check_credits(k) for k in api_keys])
    best_key, best_credits = "", -1
    for key, credits in zip(api_keys, results):
        log.info("[CC-API]  ...%s: %d credits", key[-8:], credits)
        if credits > best_credits:
            best_credits, best_key = credits, key

    if best_credits <= 0:
        raise RuntimeError(
            f"CloudConvert: all {len(api_keys)} API keys exhausted (0 credits)."
        )
    log.info("[CC-API] Selected key ...%s (%d credits)", best_key[-8:], best_credits)
    return best_key, best_credits


# ─────────────────────────────────────────────────────────────
# Job creation helpers
# ─────────────────────────────────────────────────────────────

def _find_task(job: dict, name: str) -> Optional[dict]:
    for task in job.get("tasks", []):
        if task.get("name") == name:
            return task
    return None


async def _post_job(api_key: str, payload: dict) -> dict:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as sess:
        async with sess.post(f"{CC_API}/jobs", json=payload, headers=headers) as resp:
            data = await resp.json()
            if resp.status not in (200, 201):
                raise RuntimeError(
                    f"CC job creation failed ({resp.status}): {data.get('message', data)}"
                )
    return data.get("data", data)


async def upload_file_to_task(task: dict, file_path: str, filename: Optional[str] = None) -> None:
    """Upload a local file to a CloudConvert import/upload task."""
    result = task.get("result") or {}
    form   = result.get("form") or {}
    url    = form.get("url")
    params = form.get("parameters") or {}

    if not url:
        raise RuntimeError("No upload URL — task may not be in 'waiting' state")

    fname = filename or os.path.basename(file_path)
    data  = aiohttp.FormData()
    for k, v in params.items():
        data.add_field(k, str(v))
    data.add_field("file", open(file_path, "rb"), filename=fname.replace(" ", "_"))

    async with aiohttp.ClientSession() as sess:
        async with sess.post(url, data=data, allow_redirects=True) as resp:
            if resp.status not in (200, 201, 204, 301, 302):
                body = await resp.text()
                raise RuntimeError(f"Upload failed ({resp.status}): {body[:300]}")

    log.info("[CC-API] Uploaded %s", fname)


# ─────────────────────────────────────────────────────────────
# Hardsub job
# ─────────────────────────────────────────────────────────────

async def create_hardsub_job(
    api_key: str, *,
    video_url: Optional[str] = None,
    video_filename: str = "video.mkv",
    subtitle_filename: str = "subtitle.ass",
    output_filename: str = "output.mp4",
    crf: int = 20,
    preset: str = "medium",
    scale_height: int = 0,
) -> dict:
    v_safe = video_filename.replace("'", "\\'").replace(" ", "_")
    s_safe = subtitle_filename.replace("'", "\\'").replace(" ", "_")
    o_safe = output_filename.replace("'", "\\'").replace(" ", "_")
    sub_path = f"/input/import-sub/{s_safe}"
    sub_esc  = sub_path.replace("\\", "\\\\").replace(":", "\\:")
    vf = f"scale=-2:{scale_height},subtitles='{sub_esc}'" if scale_height > 0 \
         else f"subtitles='{sub_esc}'"
    abr = "128k" if scale_height and scale_height <= 480 else "192k"

    tasks: dict = {
        "import-video": {"operation": "import/url", "url": video_url, "filename": v_safe}
                        if video_url else {"operation": "import/upload"},
        "import-sub":   {"operation": "import/upload"},
        "hardsub": {
            "operation": "command",
            "input":     ["import-video", "import-sub"],
            "engine":    "ffmpeg",
            "command":   "ffmpeg",
            "arguments": (
                f"-i /input/import-video/{v_safe} "
                f"-vf {vf} "
                f"-c:v libx264 -crf {crf} -preset {preset} "
                f"-c:a aac -b:a {abr} -movflags +faststart "
                f"/output/{o_safe}"
            ),
        },
        "export": {"operation": "export/url", "input": ["hardsub"]},
    }

    job = await _post_job(api_key, {"tasks": tasks, "tag": "zilong-leech-hardsub"})
    log.info("[CC-API] Hardsub job created: %s", job.get("id"))
    return job


async def submit_hardsub(
    api_key: str,
    video_path: Optional[str] = None,
    video_url: Optional[str] = None,
    subtitle_path: str = "",
    output_name: str = "hardsub.mp4",
    crf: int = 20,
    scale_height: int = 0,
) -> str:
    """Submit a hardsub job. Returns the job ID."""
    if not video_path and not video_url:
        raise ValueError("Provide either video_path or video_url")
    if not subtitle_path or not os.path.isfile(subtitle_path):
        raise ValueError(f"Subtitle file not found: {subtitle_path}")

    keys          = parse_api_keys(api_key)
    selected, _   = await pick_best_key(keys)
    video_fname   = os.path.basename(video_path) if video_path else \
                    video_url.split("/")[-1].split("?")[0]
    sub_fname     = os.path.basename(subtitle_path)

    job    = await create_hardsub_job(
        selected,
        video_url=video_url if not video_path else None,
        video_filename=video_fname, subtitle_filename=sub_fname,
        output_filename=output_name, crf=crf, scale_height=scale_height,
    )
    job_id = job.get("id", "?")

    sub_task = _find_task(job, "import-sub")
    if not sub_task:
        raise RuntimeError("No import-sub task in job")
    await upload_file_to_task(sub_task, subtitle_path, sub_fname)

    if video_path:
        vid_task = _find_task(job, "import-video")
        if not vid_task:
            raise RuntimeError("No import-video task in job")
        await upload_file_to_task(vid_task, video_path, video_fname)

    log.info("[CC-API] Hardsub submitted: %s → %s", job_id, output_name)
    return job_id


# ─────────────────────────────────────────────────────────────
# Convert job (resolution / format, no subtitles)
# ─────────────────────────────────────────────────────────────

async def create_convert_job(
    api_key: str, *,
    video_url: Optional[str] = None,
    video_filename: str = "video.mkv",
    output_filename: str = "output.mp4",
    crf: int = 20,
    preset: str = "medium",
    scale_height: int = 0,
) -> dict:
    v_safe  = video_filename.replace("'", "\\'").replace(" ", "_")
    o_safe  = output_filename.replace("'", "\\'").replace(" ", "_")
    vf      = f"-vf scale=-2:{scale_height}" if scale_height > 0 else ""
    abr     = "128k" if scale_height and scale_height <= 480 else "192k"

    tasks: dict = {
        "import-video": {"operation": "import/url", "url": video_url, "filename": v_safe}
                        if video_url else {"operation": "import/upload"},
        "convert": {
            "operation": "command",
            "input":     ["import-video"],
            "engine":    "ffmpeg",
            "command":   "ffmpeg",
            "arguments": (
                f"-i /input/import-video/{v_safe} {vf} "
                f"-c:v libx264 -crf {crf} -preset {preset} "
                f"-c:a aac -b:a {abr} -movflags +faststart "
                f"/output/{o_safe}"
            ).strip(),
        },
        "export": {"operation": "export/url", "input": ["convert"]},
    }

    job = await _post_job(api_key, {"tasks": tasks, "tag": "zilong-leech-convert"})
    log.info("[CC-API] Convert job created: %s", job.get("id"))
    return job


async def submit_convert(
    api_key: str,
    video_path: Optional[str] = None,
    video_url: Optional[str] = None,
    output_name: str = "converted.mp4",
    crf: int = 20,
    scale_height: int = 0,
) -> str:
    """Submit a convert job. Returns the job ID."""
    if not video_path and not video_url:
        raise ValueError("Provide either video_path or video_url")

    keys         = parse_api_keys(api_key)
    selected, _  = await pick_best_key(keys)
    video_fname  = os.path.basename(video_path) if video_path else \
                   video_url.split("/")[-1].split("?")[0]

    job    = await create_convert_job(
        selected,
        video_url=video_url if not video_path else None,
        video_filename=video_fname, output_filename=output_name,
        crf=crf, scale_height=scale_height,
    )
    job_id = job.get("id", "?")

    if video_path:
        vid_task = _find_task(job, "import-video")
        if not vid_task:
            raise RuntimeError("No import-video task in job")
        await upload_file_to_task(vid_task, video_path, video_fname)

    log.info("[CC-API] Convert submitted: %s → %s", job_id, output_name)
    return job_id


async def check_job_status(api_key: str, job_id: str) -> dict:
    """Poll a job's current status."""
    keys = parse_api_keys(api_key)
    headers = {"Authorization": f"Bearer {keys[0]}"}
    async with aiohttp.ClientSession() as sess:
        async with sess.get(f"{CC_API}/jobs/{job_id}", headers=headers) as resp:
            data = await resp.json()
    return data.get("data", data)
