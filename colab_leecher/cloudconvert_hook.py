"""
colab_leecher/cloudconvert_hook.py
Receives CloudConvert webhooks and auto-downloads + uploads
finished files through zilong-leech's native pipeline.

Fixes vs v1:
- Creates Paths.WORK_PATH before calling upload_file so thumbMaintainer
  can write VIDEO_FRAME and Hero.jpg fallback never gets hit.
- Real download progress: edits MSG.status_msg every second while
  streaming the CC export URL.
- Transfer / BotTimes set up properly so upload_file progress_bar works.
- upload_file errors are now re-raised (not swallowed) so the hook can
  send a proper failure message to the owner.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import shutil
import tempfile
import time
from datetime import datetime

import aiohttp
from aiohttp import web

log = logging.getLogger(__name__)

WEBHOOK_SECRET: str = ""

_runner = None
_site   = None

LISTEN_PORT = 8765


# ─────────────────────────────────────────────────────────────
# Signature verification
# ─────────────────────────────────────────────────────────────

def _verify_signature(payload: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        return True
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


# ─────────────────────────────────────────────────────────────
# Payload parsing
# ─────────────────────────────────────────────────────────────

def _extract_urls(data: dict) -> list[dict]:
    results = []
    job   = data.get("job", {})
    tasks = job.get("tasks", [])
    for task in tasks:
        if task.get("operation") != "export/url":
            continue
        if task.get("status") != "finished":
            continue
        for f in (task.get("result") or {}).get("files", []):
            url = f.get("url")
            if url:
                results.append({
                    "url":      url,
                    "filename": f.get("filename", "cloudconvert_output.mp4"),
                })
    return results


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _size_str(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GiB"


def _time_str(seconds: int) -> str:
    if seconds <= 0:
        return "—"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:   return f"{h}h {m}m {s}s"
    if m:   return f"{m}m {s}s"
    return f"{s}s"


def _bar(pct: float, cells: int = 12) -> str:
    filled = int(min(max(pct, 0), 100) / 100 * cells)
    return "█" * filled + "░" * (cells - filled)


# ─────────────────────────────────────────────────────────────
# Download → upload pipeline
# ─────────────────────────────────────────────────────────────

async def _process_file(url: str, filename: str) -> None:
    from colab_leecher import colab_bot, OWNER
    from colab_leecher.uploader.telegram import upload_file
    from colab_leecher.utility.variables import Transfer, BotTimes, MSG, Messages, Paths

    tmp  = tempfile.mkdtemp(prefix="cc_hook_")
    dest = os.path.join(tmp, filename)

    # ── FIX: ensure WORK_PATH exists so thumbMaintainer can write frames ──
    # Without this, thumbMaintainer falls back to Hero.jpg which doesn't
    # exist, Image.open() raises FileNotFoundError, upload_file swallows
    # it silently and the file is never sent.
    os.makedirs(Paths.WORK_PATH, exist_ok=True)

    try:
        # ── Phase 1: Download from CloudConvert ───────────────────────────
        status_msg = await colab_bot.send_message(
            OWNER,
            f"☁️ <b>CloudConvert</b>\n"
            f"──────────────────\n"
            f"📥 <b>Downloading</b>\n"
            f"📄 <code>{filename[:50]}</code>",
        )

        start     = time.time()
        last_edit = [start]

        async with aiohttp.ClientSession() as sess:
            async with sess.get(url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0))
                done  = 0

                with open(dest, "wb") as fh:
                    async for chunk in resp.content.iter_chunked(512 * 1024):
                        fh.write(chunk)
                        done += len(chunk)

                        now = time.time()
                        if now - last_edit[0] >= 2.0:
                            last_edit[0] = now
                            elapsed = now - start
                            speed   = done / elapsed if elapsed else 0
                            pct     = (done / total * 100) if total else 0
                            eta     = int((total - done) / speed) if (speed and total) else 0
                            try:
                                await status_msg.edit(
                                    f"☁️ <b>CloudConvert</b>\n"
                                    f"──────────────────\n"
                                    f"📥 <b>Downloading</b>\n"
                                    f"📄 <code>{filename[:45]}</code>\n\n"
                                    f"<code>[{_bar(pct)}]</code>  <b>{pct:.1f}%</b>\n"
                                    f"──────────────────\n"
                                    f"⚡ Speed   <code>{_size_str(speed)}/s</code>\n"
                                    f"✅ Done    <code>{_size_str(done)}</code>"
                                    + (f" / <code>{_size_str(total)}</code>" if total else "") + "\n"
                                    f"⏳ ETA     <code>{_time_str(eta)}</code>",
                                )
                            except Exception:
                                pass

        fsize = os.path.getsize(dest)
        log.info("[CC-Hook] Downloaded %s (%.1f MiB)", filename, fsize / (1024 * 1024))

        # ── Phase 2: Upload to Telegram ───────────────────────────────────
        # Set up the state variables upload_file / progress_bar depend on
        Transfer.total_down_size = fsize
        Transfer.up_bytes        = [0, 0]
        Transfer.sent_file       = []
        Transfer.sent_file_names = []
        BotTimes.start_time      = datetime.now()
        Messages.status_head     = (
            f"☁️ <b>CloudConvert</b>\n"
            f"──────────────────\n"
            f"📤 <b>Uploading</b>\n"
            f"📄 <code>{filename[:45]}</code>\n"
        )
        Messages.task_msg = ""

        # Swap the download message for an upload status message.
        # upload_file's progress_bar will edit this, and is_last=True
        # will delete it once the file has been sent.
        try:
            await status_msg.delete()
        except Exception:
            pass

        MSG.status_msg = await colab_bot.send_message(
            OWNER,
            f"☁️ <b>CloudConvert</b>\n"
            f"──────────────────\n"
            f"📤 <b>Uploading</b>\n"
            f"📄 <code>{filename[:45]}</code>\n\n"
            f"⏳ <i>Starting upload…</i>",
        )

        # upload_file deletes MSG.status_msg on is_last=True after
        # a successful send.  Any exception here propagates to the
        # outer except block so the owner gets a proper error message.
        await upload_file(dest, filename, is_last=True)
        log.info("[CC-Hook] Upload complete: %s", filename)

    except Exception as exc:
        log.error("[CC-Hook] Pipeline failed for %s: %s", filename, exc, exc_info=True)
        # Clean up the stuck status message if it's still alive
        try:
            await MSG.status_msg.delete()
        except Exception:
            pass
        try:
            await colab_bot.send_message(
                OWNER,
                f"❌ <b>CloudConvert auto-upload failed</b>\n\n"
                f"📄 <code>{filename}</code>\n"
                f"<code>{str(exc)[:250]}</code>",
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────
# HTTP handlers
# ─────────────────────────────────────────────────────────────

async def handle_cloudconvert(request: web.Request) -> web.Response:
    try:
        body = await request.read()

        sig = request.headers.get("CloudConvert-Signature", "")
        if WEBHOOK_SECRET and not _verify_signature(body, sig):
            log.warning("[CC-Hook] Invalid signature — rejected")
            return web.json_response({"error": "invalid signature"}, status=403)

        data  = await request.json()
        event = data.get("event", "")
        log.info("[CC-Hook] Event received: %s", event)

        if event != "job.finished":
            return web.json_response({"status": "ignored", "event": event})

        files = _extract_urls(data)
        if not files:
            log.warning("[CC-Hook] job.finished with no export URLs")
            return web.json_response({"status": "no_export_urls"})

        for f in files:
            asyncio.create_task(_process_file(f["url"], f["filename"]))

        log.info("[CC-Hook] Enqueued %d file(s)", len(files))
        return web.json_response({
            "status":   "ok",
            "enqueued": [f["filename"] for f in files],
        })

    except Exception as exc:
        log.error("[CC-Hook] Handler error: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status":  "online",
        "service": "zilong-leech-cloudconvert-webhook",
    })


def _build_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/webhook/cloudconvert", handle_cloudconvert)
    app.router.add_get("/health",                handle_health)
    app.router.add_get("/",                      handle_health)
    return app


# ─────────────────────────────────────────────────────────────
# Server lifecycle
# ─────────────────────────────────────────────────────────────

async def start_webhook_server(port: int = LISTEN_PORT, ngrok_token: str = "") -> str:
    global _runner, _site

    app     = _build_app()
    _runner = web.AppRunner(app)
    await _runner.setup()
    _site = web.TCPSite(_runner, "0.0.0.0", port)
    await _site.start()
    log.info("[CC-Hook] Webhook server listening on port %d", port)

    if not WEBHOOK_SECRET:
        log.warning(
            "[CC-Hook] ⚠️  CC_WEBHOOK_SECRET not set — ANY POST to the ngrok URL "
            "will trigger downloads/uploads. Set it in credentials.json for security."
        )

    if not ngrok_token:
        log.info("[CC-Hook] No NGROK_TOKEN — running on localhost:%d only", port)
        return ""

    try:
        from pyngrok import ngrok, conf
        conf.get_default().auth_token = ngrok_token
        tunnel      = ngrok.connect(port, "http")
        public_url  = tunnel.public_url
        webhook_url = f"{public_url}/webhook/cloudconvert"
        log.info("[CC-Hook] ngrok tunnel:   %s", public_url)
        log.info("[CC-Hook] Webhook URL:    %s", webhook_url)
        return webhook_url
    except ImportError:
        log.error("[CC-Hook] pyngrok not installed — pip install pyngrok")
    except Exception as exc:
        log.error("[CC-Hook] ngrok error: %s", exc)

    return ""


async def stop_webhook_server() -> None:
    try:
        from pyngrok import ngrok
        ngrok.kill()
    except Exception:
        pass
    if _site:
        await _site.stop()
    if _runner:
        await _runner.cleanup()
