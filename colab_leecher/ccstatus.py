"""
colab_leecher/ccstatus.py
/ccstatus command  +  background CC poller  +  /convert command.

The poller polls CloudConvert API every 5 s when jobs are active and
delivers finished files even when the webhook never fires (AWS, Koyeb).
/convert submits a resolution/format conversion job (no subtitle).
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from typing import Optional

import aiohttp
from pyrogram import filters
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from colab_leecher import colab_bot, OWNER, CC_API_KEY
from colab_leecher.cc_job_store import cc_job_store, CCJob
from colab_leecher.utility.variables import Paths

log = logging.getLogger(__name__)

_poller_task: Optional[asyncio.Task] = None


# ─────────────────────────────────────────────────────────────
# /ccstatus — live panel
# ─────────────────────────────────────────────────────────────

def _icon(status: str) -> str:
    return {"processing": "⏳", "finished": "✅", "error": "❌"}.get(status, "❓")


def _render(uid: int) -> str:
    jobs = cc_job_store.jobs_for_user(uid)
    if not jobs:
        return (
            "☁️ <b>CloudConvert Jobs</b>\n"
            "──────────────────────\n\n"
            "<i>No jobs found.</i>\n\n"
            "Use /hardsub to start a hardsub job\n"
            "or /convert to convert a video."
        )
    lines = ["☁️ <b>CloudConvert Jobs</b>", "──────────────────────", ""]
    for j in jobs[:10]:
        icon  = _icon(j.status)
        fname = (j.fname[:38] + "…") if len(j.fname) > 38 else j.fname
        lines.append(f"{icon} <code>{fname}</code>")
        lines.append(f"   🆔 <code>{j.job_id[:22]}</code>")
        if j.status == "processing":
            lines.append(f"   🔄 <i>{j.task_message or 'Processing…'}</i>")
        elif j.status == "finished":
            lines.append("   📤 <i>Uploaded to Telegram</i>" if j.notified else "   ⬆️ <i>Uploading…</i>")
        elif j.status == "error":
            lines.append(f"   ❌ <i>{(j.error_msg or 'Unknown error')[:60]}</i>")
        lines.append("")
    lines += ["──────────────────────", f"<i>Updated {time.strftime('%H:%M:%S')}</i>"]
    return "\n".join(lines)


_PANEL_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("🔄 Refresh", callback_data="ccs|refresh"),
    InlineKeyboardButton("✖ Close",   callback_data="ccs|close"),
]])

_open_panels: dict[int, Message] = {}


@colab_bot.on_message(filters.command("ccstatus") & filters.private)
async def cmd_ccstatus(client, msg: Message):
    if msg.from_user.id != OWNER:
        return
    st = await msg.reply(_render(OWNER), reply_markup=_PANEL_KB)
    _open_panels[OWNER] = st
    ensure_poller()


@colab_bot.on_callback_query(filters.regex(r"^ccs\|"))
async def ccstatus_cb(client, cb: CallbackQuery):
    uid    = cb.from_user.id
    action = cb.data.split("|")[1]
    await cb.answer()
    if action == "close":
        _open_panels.pop(uid, None)
        try:
            await cb.message.delete()
        except Exception:
            pass
        return
    text = _render(uid)
    try:
        await cb.message.edit(text, reply_markup=_PANEL_KB)
        _open_panels[uid] = cb.message
    except Exception as exc:
        if "MESSAGE_NOT_MODIFIED" not in str(exc):
            log.debug("ccstatus refresh: %s", exc)


# ─────────────────────────────────────────────────────────────
# /convert — resolution/format conversion (no subtitle)
# ─────────────────────────────────────────────────────────────

_CONVERT_RES_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🎬 Original", callback_data="ccv2|0"),
     InlineKeyboardButton("🔵 1080p",    callback_data="ccv2|1080")],
    [InlineKeyboardButton("🟢 720p",     callback_data="ccv2|720"),
     InlineKeyboardButton("🟡 480p",     callback_data="ccv2|480")],
    [InlineKeyboardButton("🟠 360p",     callback_data="ccv2|360"),
     InlineKeyboardButton("❌ Cancel",   callback_data="ccv2|cancel")],
])

_pending_convert: dict[int, str] = {}  # uid → video_url


@colab_bot.on_message(filters.command("convert") & filters.private)
async def cmd_convert(client, msg: Message):
    if msg.from_user.id != OWNER:
        return
    api_key = CC_API_KEY.strip()
    if not api_key:
        return await msg.reply(
            "❌ <b>CC_API_KEY non configurée</b>\n\n"
            "Ajoute <code>CC_API_KEY</code> dans <code>credentials.json</code>.\n"
            "Obtiens une clé sur cloudconvert.com → API Keys.",
        )
    await msg.reply(
        "🔄 <b>CloudConvert Convert</b>\n"
        "──────────────────────\n\n"
        "Envoie l'<b>URL directe</b> de la vidéo à convertir\n"
        "(<code>http://…mkv</code> ou <code>…mp4</code>)\n\n"
        "<i>Envoie /cancel pour annuler.</i>",
    )
    _pending_convert[OWNER] = "waiting_url"


@colab_bot.on_message(
    filters.private & filters.text
    & ~filters.command(["start","help","settings","stats","ping","cancel","stop",
                        "setname","hardsub","ccstatus","convert"]),
    group=2,
)
async def convert_url_receiver(client, msg: Message):
    uid = msg.from_user.id
    if uid != OWNER or _pending_convert.get(uid) != "waiting_url":
        return
    url = msg.text.strip()
    if not url.startswith("http"):
        return
    raw_name = url.split("/")[-1].split("?")[0]
    import urllib.parse as _up
    fname = _up.unquote_plus(raw_name)[:50] or "video.mkv"
    _pending_convert[uid] = url
    await msg.reply(
        f"🎬 <code>{fname[:45]}</code>\n\nChoisis la résolution :",
        reply_markup=_CONVERT_RES_KB,
    )
    msg.stop_propagation()


@colab_bot.on_callback_query(filters.regex(r"^ccv2\|"))
async def ccv2_cb(client, cb: CallbackQuery):
    uid   = cb.from_user.id
    parts = cb.data.split("|")
    h     = parts[1]
    await cb.answer()

    if h == "cancel":
        _pending_convert.pop(uid, None)
        await cb.message.delete()
        return

    url = _pending_convert.pop(uid, None)
    if not url or url == "waiting_url":
        await cb.message.edit("❌ Session expirée. Relance /convert.")
        return

    scale_height = int(h) if h.isdigit() and h != "0" else 0
    res_label    = f"{scale_height}p" if scale_height else "Original"
    import re
    raw_name = url.split("/")[-1].split("?")[0]
    import urllib.parse as _up
    fname      = _up.unquote_plus(raw_name)[:50] or "video.mkv"
    name_base  = os.path.splitext(fname)[0]
    res_tag    = f" [{scale_height}p]" if scale_height else ""
    output_name = re.sub(r'[^\w\s\-\[\]()]', '_', name_base).strip() + f"{res_tag}.mp4"

    api_key = CC_API_KEY.strip()
    await cb.message.edit(
        f"☁️ <b>Soumission job convert…</b>\n\n"
        f"🎬 <code>{fname[:40]}</code>\n"
        f"📐 → <b>{res_label}</b>\n\n"
        "<i>Vérification crédits API…</i>",
    )

    try:
        from colab_leecher.cloudconvert_api import parse_api_keys, pick_best_key, submit_convert
        keys = parse_api_keys(api_key)
        selected, credits = await pick_best_key(keys)

        job_id = await submit_convert(
            api_key,
            video_url=url,
            output_name=output_name,
            scale_height=scale_height,
        )

        job = CCJob(
            job_id=job_id, uid=uid, fname=fname,
            output_name=output_name, status="processing",
        )
        await cc_job_store.add(job)
        ensure_poller()

        await cb.message.edit(
            f"✅ <b>Convert soumis !</b>\n"
            "──────────────────────\n\n"
            f"🆔 <code>{job_id}</code>\n"
            f"🎬 <code>{fname[:38]}</code>\n"
            f"📐 → <b>{res_label}</b>\n"
            f"📦 → <code>{output_name[:40]}</code>\n\n"
            "⏳ <i>CloudConvert traite…\n"
            "Le webhook ou /ccstatus livrera le résultat.</i>",
        )
    except Exception as exc:
        log.error("[Convert] Failed: %s", exc)
        await cb.message.edit(
            f"❌ <b>Convert échoué</b>\n\n<code>{str(exc)[:200]}</code>",
        )


# ─────────────────────────────────────────────────────────────
# Export download → Telegram upload
# ─────────────────────────────────────────────────────────────

async def _deliver_job(job: CCJob) -> None:
    from colab_leecher.uploader.telegram import upload_file
    from colab_leecher.utility.variables import Transfer, BotTimes, MSG, Messages, Paths
    from datetime import datetime

    tmp  = tempfile.mkdtemp(prefix="cc_deliver_", dir=getattr(Paths, "WORK_PATH", "/tmp"))
    dest = os.path.join(tmp, job.output_name or job.fname or "output.mp4")
    fname = os.path.basename(dest)

    try:
        os.makedirs(Paths.WORK_PATH, exist_ok=True)

        status_msg = await colab_bot.send_message(
            OWNER,
            f"☁️ <b>CloudConvert</b> — Livraison\n"
            f"──────────────────────\n"
            f"📥 <b>Downloading</b>  <code>{fname[:45]}</code>",
        )

        start = time.time()
        async with aiohttp.ClientSession() as sess:
            async with sess.get(job.export_url, allow_redirects=True) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0))
                done  = 0
                with open(dest, "wb") as fh:
                    async for chunk in resp.content.iter_chunked(512 * 1024):
                        fh.write(chunk)
                        done += len(chunk)

        fsize = os.path.getsize(dest)
        log.info("[CCStatus] Downloaded %s (%.1f MiB)", fname, fsize / (1024 * 1024))

        # Set up state variables upload_file depends on
        Transfer.total_down_size = fsize
        Transfer.up_bytes        = [0, 0]
        Transfer.sent_file       = []
        Transfer.sent_file_names = []
        BotTimes.start_time      = datetime.now()
        Messages.status_head     = (
            f"☁️ <b>CloudConvert</b>\n"
            f"──────────────────────\n"
            f"📤 <b>Uploading</b>  <code>{fname[:45]}</code>\n"
        )
        Messages.task_msg = ""

        try:
            await status_msg.delete()
        except Exception:
            pass

        MSG.status_msg = await colab_bot.send_message(
            OWNER,
            f"☁️ <b>CloudConvert</b>\n"
            f"──────────────────────\n"
            f"📤 <b>Uploading</b>  <code>{fname[:45]}</code>\n\n"
            f"⏳ <i>Starting upload…</i>",
        )

        await upload_file(dest, fname, is_last=True)
        await cc_job_store.mark_notified(job.job_id)
        log.info("[CCStatus] Delivered job %s to uid=%d", job.job_id, job.uid)

    except Exception as exc:
        log.error("[CCStatus] Delivery failed for job %s: %s", job.job_id, exc, exc_info=True)
        try:
            await MSG.status_msg.delete()
        except Exception:
            pass
        try:
            await colab_bot.send_message(
                OWNER,
                f"❌ <b>CloudConvert delivery failed</b>\n\n"
                f"📄 <code>{fname}</code>\n"
                f"<code>{str(exc)[:250]}</code>",
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────
# Poller
# ─────────────────────────────────────────────────────────────

def ensure_poller() -> None:
    global _poller_task
    if _poller_task and not _poller_task.done():
        return
    _poller_task = asyncio.create_task(_poll_loop())
    log.info("[CCStatus] Poller started")


async def _poll_loop() -> None:
    api_key = CC_API_KEY.strip()
    if not api_key:
        log.warning("[CCStatus] No CC_API_KEY — poller disabled")
        return

    from colab_leecher.cloudconvert_api import check_job_status
    consecutive_idle = 0

    while True:
        active   = cc_job_store.active_jobs()
        interval = 5 if active else 60

        for job in active:
            try:
                data   = await check_job_status(api_key, job.job_id)
                status = data.get("status", "")
                tasks  = data.get("tasks", [])

                # Collect task progress messages
                msgs = [
                    t.get("message") or t.get("status") or ""
                    for t in tasks
                    if t.get("status") not in ("finished", "waiting", "pending")
                ]
                await cc_job_store.update(job.job_id, task_message=(msgs[0] if msgs else "Processing…"))

                if status == "finished":
                    export_url = ""
                    for t in tasks:
                        if t.get("operation") == "export/url" and t.get("status") == "finished":
                            files = (t.get("result") or {}).get("files", [])
                            if files:
                                export_url = files[0].get("url", "")
                                break
                    if export_url:
                        await cc_job_store.finish(job.job_id, export_url=export_url)
                        fresh = cc_job_store.get(job.job_id)
                        if fresh:
                            asyncio.create_task(_deliver_job(fresh))
                    else:
                        await cc_job_store.finish(
                            job.job_id,
                            error_msg="Job finished but no export URL found",
                        )
                elif status == "error":
                    err = data.get("message") or "CloudConvert error"
                    await cc_job_store.finish(job.job_id, error_msg=err)

            except Exception as exc:
                log.warning("[CCStatus] Poll error job %s: %s", job.job_id, exc)

        # Edit open panels
        for uid, panel_msg in list(_open_panels.items()):
            try:
                text = _render(uid)
                await panel_msg.edit(text, reply_markup=_PANEL_KB)
            except Exception as exc:
                err = str(exc)
                if "MESSAGE_NOT_MODIFIED" in err or "message was not modified" in err.lower():
                    pass
                elif "MESSAGE_ID_INVALID" in err or "not found" in err.lower():
                    _open_panels.pop(uid, None)
                else:
                    log.debug("[CCStatus] Panel edit uid=%d: %s", uid, err)

        # Stop when nothing to do
        if not cc_job_store.active_jobs() and not _open_panels:
            consecutive_idle += 1
            if consecutive_idle >= 3:
                log.info("[CCStatus] Poller stopping — no active jobs, no open panels")
                return
        else:
            consecutive_idle = 0

        await asyncio.sleep(interval)
