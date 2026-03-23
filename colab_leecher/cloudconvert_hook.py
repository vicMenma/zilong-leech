"""
colab_leecher/cloudconvert_hook.py
Receives CloudConvert webhooks and auto-downloads + uploads
finished files through zilong-leech's native pipeline.

How it works:
  1. CloudConvert finishes a job → sends POST to /webhook/cloudconvert
  2. We verify the optional HMAC signature
  3. We extract the export/url task's download links
  4. We download each file directly (they are temporary S3 URLs)
  5. We upload via zilong-leech's upload_file() to the owner's chat
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import shutil
import tempfile

import aiohttp
from aiohttp import web

log = logging.getLogger(__name__)

# Set by __init__.py at startup from credentials.json
WEBHOOK_SECRET: str = ""

_runner = None
_site   = None

LISTEN_PORT = 8765


# ─────────────────────────────────────────────────────────────
# Signature verification
# ─────────────────────────────────────────────────────────────

def _verify_signature(payload: bytes, signature: str) -> bool:
    """Return True when signature matches, or when no secret is configured."""
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
    """Pull all finished export/url file entries out of a CC webhook payload."""
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
# Download → upload pipeline
# ─────────────────────────────────────────────────────────────

async def _process_file(url: str, filename: str) -> None:
    """Download a CC export URL and upload it to the owner via colab_bot."""
    # Import here to avoid circular import at module load time
    from colab_leecher import colab_bot, OWNER
    from colab_leecher.uploader.telegram import upload_file
    from colab_leecher.utility.variables import Transfer, BotTimes, MSG, Messages
    from datetime import datetime

    tmp  = tempfile.mkdtemp(prefix="cc_hook_")
    dest = os.path.join(tmp, filename)

    try:
        # Notify owner that we are starting
        notify_msg = await colab_bot.send_message(
            OWNER,
            f"☁️ <b>CloudConvert Auto-Upload</b>\n\n"
            f"📁 <code>{filename[:60]}</code>\n\n"
            f"⬇️ <i>Downloading from CloudConvert…</i>",
        )

        # ── Download from CloudConvert's temporary S3 URL ──────────────
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        fh.write(chunk)

        fsize = os.path.getsize(dest)
        log.info("[CC-Hook] Downloaded %s (%.1f MiB)", filename, fsize / (1024 * 1024))

        # ── Prepare upload state variables ─────────────────────────────
        # We set up just enough state so upload_file() works standalone.
        Transfer.total_down_size = fsize
        Transfer.up_bytes        = [0, 0]
        Transfer.sent_file       = []
        Transfer.sent_file_names = []
        BotTimes.start_time      = datetime.now()
        Messages.status_head     = f"☁️ CC Upload: <code>{filename[:50]}</code>\n"
        Messages.task_msg        = ""

        # Give upload_file a status message to update
        # (it will delete this message when is_last=True)
        MSG.status_msg = await colab_bot.send_message(
            OWNER,
            f"📤 <b>Uploading</b>  <code>{filename[:50]}</code>…",
        )

        # Delete the earlier notify message to keep chat clean
        try:
            await notify_msg.delete()
        except Exception:
            pass

        # ── Upload ────────────────────────────────────────────────────
        await upload_file(dest, filename, is_last=True)
        log.info("[CC-Hook] Uploaded %s successfully", filename)

    except Exception as exc:
        log.error("[CC-Hook] Pipeline failed for %s: %s", filename, exc)
        try:
            from colab_leecher import colab_bot, OWNER
            await colab_bot.send_message(
                OWNER,
                f"❌ <b>CloudConvert auto-upload failed</b>\n\n"
                f"📁 <code>{filename}</code>\n"
                f"<code>{str(exc)[:250]}</code>",
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────
# HTTP request handlers
# ─────────────────────────────────────────────────────────────

async def handle_cloudconvert(request: web.Request) -> web.Response:
    try:
        body = await request.read()

        sig = request.headers.get("CloudConvert-Signature", "")
        if WEBHOOK_SECRET and not _verify_signature(body, sig):
            log.warning("[CC-Hook] Invalid signature — request rejected")
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

        # Fire-and-forget: do not block the HTTP response
        for f in files:
            asyncio.create_task(_process_file(f["url"], f["filename"]))

        log.info("[CC-Hook] Enqueued %d file(s) for upload", len(files))
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
    """
    Start the aiohttp webhook server and optionally open an ngrok tunnel.
    Returns the public webhook URL (empty string if ngrok is not configured).
    """
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
        log.error("[CC-Hook] pyngrok not installed — run: pip install pyngrok")
    except Exception as exc:
        log.error("[CC-Hook] ngrok error: %s", exc)

    return ""


async def stop_webhook_server() -> None:
    """Tear down the ngrok tunnel and aiohttp server cleanly."""
    try:
        from pyngrok import ngrok
        ngrok.kill()
    except Exception:
        pass
    if _site:
        await _site.stop()
    if _runner:
        await _runner.cleanup()
