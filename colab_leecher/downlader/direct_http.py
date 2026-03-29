"""
colab_leecher/downlader/direct_http.py
Direct HTTP download using aiohttp — used as fallback when aria2c
fails on direct links (e.g. aria2c not running on AWS/EC2).

Provides a progress callback compatible with the rest of the downloader stack.
"""
from __future__ import annotations

import logging
import os
import time
import urllib.parse as _up
from typing import Optional, Callable, Awaitable

import aiohttp

log = logging.getLogger(__name__)

ProgressCB = Optional[Callable[[int, int, float, int], Awaitable[None]]]


def _size_str(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GiB"


async def download_direct(
    url: str,
    dest_dir: str,
    progress: ProgressCB = None,
    filename: Optional[str] = None,
) -> str:
    """
    Stream-download `url` into `dest_dir` with real-time progress.
    Returns the local file path.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ZilongBot/1.0)",
        "Accept":     "*/*",
    }

    timeout = aiohttp.ClientTimeout(total=6 * 3600, connect=30)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as sess:
        async with sess.get(url, allow_redirects=True) as resp:
            resp.raise_for_status()

            # Determine filename from Content-Disposition or URL
            if not filename:
                cd = resp.headers.get("Content-Disposition", "")
                if "filename=" in cd:
                    fn_raw = cd.split("filename=")[-1].strip().strip('"').strip("'")
                    filename = _up.unquote_plus(fn_raw)
                if not filename:
                    filename = _up.unquote_plus(url.split("/")[-1].split("?")[0]) or "download"

            # Sanitise
            import re
            filename = re.sub(r'[\\/:*?"<>|]', "_", filename)

            total = int(resp.headers.get("Content-Length", 0))
            fpath = os.path.join(dest_dir, filename)
            done  = 0
            start = time.time()
            last  = [start]

            with open(fpath, "wb") as fh:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    fh.write(chunk)
                    done += len(chunk)

                    now = time.time()
                    if progress and now - last[0] >= 1.5:
                        last[0] = now
                        elapsed = now - start
                        speed   = done / elapsed if elapsed else 0
                        eta     = int((total - done) / speed) if (speed and total) else 0
                        await progress(done, total, speed, eta)

    log.info("[DirectHTTP] Downloaded %s → %s (%.1f MiB)",
             url[:60], filename, os.path.getsize(fpath) / (1024 * 1024))
    return fpath
