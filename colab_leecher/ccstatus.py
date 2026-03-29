"""
colab_leecher/ccstatus.py
/ccstatus — live CloudConvert progress panel + background poller + /convert.

The poller polls /v2/jobs/{id} every 5 s.
Per-task `percent` is read from the API and converted to a weighted
overall progress:
  import-video  →  weight 15 %
  import-sub    →  weight 5 %
  hardsub/convert (the ffmpeg command task) → weight 75 %
  export        →  weight 5 %
This gives a real 0-100 bar that moves live.
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

# ── task weight table ─────────────────────────────────────────
# Keys are substrings matched against task["name"] or task["operation"].
# Weights must sum to 100 for a clean 0-100 progress.
_TASK_WEIGHTS: list[tuple[str, float]] = [
    ("import-video",  15.0),
    ("import-sub",     5.0),
    ("hardsub",       75.0),   # ffmpeg encoding task — the slow one
    ("convert",       75.0),   # same for /convert jobs
    ("export",         5.0),
]
# Fallback when a task name doesn't match any entry
_DEFAULT_WEIGHT = 20.0

# Human-readable labels for the active task shown in the panel
_TASK_LABELS: dict[str, str] = {
    "import-video": "📥 Downloading video",
    "import-sub":   "📥 Uploading subtitle",
    "hardsub":      "🎬 Encoding (FFmpeg)",
    "convert":      "🔄 Converting (FFmpeg)",
    "export":       "📦 Exporting result",
}


def _task_weight(name: str) -> float:
    nl = name.lower()
    for key, w in _TASK_WEIGHTS:
        if key in nl:
            return w
    return _DEFAULT_WEIGHT


def _task_label(name: str) -> str:
    nl = name.lower()
    for key, lbl in _TASK_LABELS.items():
        if key in nl:
            return lbl
    return f"⚙️ {name}"


def _progress_bar(pct: float, cells: int = 14) -> str:
    filled = int(min(max(pct, 0.0), 100.0) / 100.0 * cells)
    return "█" * filled + "░" * (cells - filled)


def _fmt_elapsed(s: int) -> str:
    if s < 60:   return f"{s}s"
    if s < 3600: return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"


def _compute_weighted_pct(tasks: list[dict]) -> tuple[float, str, str]:
    """
    Returns (overall_pct, active_task_name, active_task_label).

    CC task status values: waiting | processing | finished | error
    CC task `percent` field: 0-100 integer, present during processing/finished.

    Approach:
      1. For each task, determine its "contribution percent":
         - finished → 100%
         - processing → task["percent"] (0-100 from CC API)
         - waiting / error → 0%
      2. Weighted average across all tasks using _TASK_WEIGHTS.
      3. active_task = the first task currently in "processing" state.
    """
    if not tasks:
        return 0.0, "", ""

    total_weight    = 0.0
    weighted_done   = 0.0
    active_name     = ""
    active_label    = ""

    for t in tasks:
        tname  = t.get("name") or t.get("operation") or "unknown"
        status = t.get("status", "waiting")
        pct_raw = t.get("percent") or 0

        try:
            task_pct = float(pct_raw)
        except (TypeError, ValueError):
            task_pct = 0.0

        if status == "finished":
            task_pct = 100.0
        elif status == "processing":
            task_pct = max(0.0, min(100.0, task_pct))
            if not active_name:
                active_name  = tname
                active_label = _task_label(tname)
        else:
            task_pct = 0.0

        w              = _task_weight(tname)
        weighted_done += task_pct * w
        total_weight  += w

    overall = (weighted_done / total_weight) if total_weight else 0.0
    return min(overall, 99.9), active_name, active_label


# ─────────────────────────────────────────────────────────────
# Panel renderer
# ─────────────────────────────────────────────────────────────

def _render(uid: int) -> str:
    jobs = cc_job_store.jobs_for_user(uid)
    if not jobs:
        return (
            "☁️ <b>CloudConvert Jobs</b>\n"
            "──────────────────────\n\n"
            "<i>Aucun job trouvé.</i>\n\n"
            "• /hardsub — graver les sous-titres\n"
            "• /convert — convertir la résolution"
        )

    now = time.time()
    lines = ["☁️ <b>CloudConvert Jobs</b>", "──────────────────────", ""]

    for j in jobs[:8]:
        fname = (j.fname[:36] + "…") if len(j.fname) > 36 else j.fname
        elapsed = _fmt_elapsed(int(now - j.created_at))

        if j.status == "processing":
            pct = j.progress_pct
            bar = _progress_bar(pct)
            active_lbl = j.active_task or "En attente…"

            lines += [
                f"⏳ <b>{fname}</b>",
                f"   <code>[{bar}]</code>  <b>{pct:.1f}%</b>",
                f"   {active_lbl}",
                f"   ⏱ <code>{elapsed}</code>  ·  🆔 <code>{j.job_id[:18]}</code>",
                "",
            ]

        elif j.status == "finished":
            total_elapsed = _fmt_elapsed(j.elapsed_s) if j.elapsed_s else elapsed
            state = "📤 <i>Envoyé sur Telegram</i>" if j.notified else "⬆️ <i>Upload en cours…</i>"
            lines += [
                f"✅ <b>{fname}</b>",
                f"   <code>[{_progress_bar(100)}]</code>  <b>100%</b>",
                f"   {state}",
                f"   ⏱ Terminé en <code>{total_elapsed}</code>",
                "",
            ]

        elif j.status == "error":
            err = (j.error_msg or "Erreur inconnue")[:55]
            lines += [
                f"❌ <b>{fname}</b>",
                f"   <i>{err}</i>",
                f"   ⏱ <code>{elapsed}</code>",
                "",
            ]

    lines += [
        "──────────────────────",
        f"<i>🔄 Actualisé {time.strftime('%H:%M:%S')}</i>",
    ]
    return "\n".join(lines)


_PANEL_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("🔄 Refresh", callback_data="ccs|refresh"),
    InlineKeyboardButton("✖ Fermer",   callback_data="ccs|close"),
]])

_open_panels: dict[int, Message] = {}   # uid → live panel message


# ─────────────────────────────────────────────────────────────
# /ccstatus command
# ─────────────────────────────────────────────────────────────

@colab_bot.on_message(filters.command("ccstatus") & filters.private)
async def cmd_ccstatus(client, msg: Message):
    if msg.from_user.id != OWNER:
        return
    await msg.delete()
    st = await colab_bot.send_message(OWNER, _render(OWNER), reply_markup=_PANEL_KB)
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

    # refresh
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
     InlineKeyboardButton("❌ Annuler",  callback_data="ccv2|cancel")],
])

_pending_convert: dict[int, str] = {}


@colab_bot.on_message(filters.command("convert") & filters.private)
async def cmd_convert(client, msg: Message):
    if msg.from_user.id != OWNER:
        return
    api_key = CC_API_KEY.strip()
    if not api_key:
        return await msg.reply(
            "❌ <b>CC_API_KEY non configurée</b>\n\n"
            "Ajoute <code>CC_API_KEY</code> dans <code>credentials.json</code>.",
        )
    await msg.delete()
    await colab_bot.send_message(
        OWNER,
        "🔄 <b>CloudConvert Convert</b>\n"
        "──────────────────────\n\n"
        "Envoie l'<b>URL directe</b> de la vidéo :\n"
        "<code>http://…/video.mkv</code>\n\n"
        "<i>Envoie /cancel pour annuler.</i>",
    )
    _pending_convert[OWNER] = "waiting_url"


@colab_bot.on_message(
    filters.private & filters.text
    & ~filters.command(["start", "help", "settings", "stats", "ping", "cancel", "stop",
                        "setname", "hardsub", "ccstatus", "convert", "botname"]),
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
    uid = cb.from_user.id
    h   = cb.data.split("|")[1]
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

    import re, urllib.parse as _up
    raw_name    = url.split("/")[-1].split("?")[0]
    fname       = _up.unquote_plus(raw_name)[:50] or "video.mkv"
    name_base   = os.path.splitext(fname)[0]
    res_tag     = f" [{scale_height}p]" if scale_height else ""
    output_name = re.sub(r'[^\w\s\-\[\]()]', '_', name_base).strip() + f"{res_tag}.mp4"

    await cb.message.edit(
        f"☁️ <b>Soumission convert…</b>\n\n"
        f"🎬 <code>{fname[:40]}</code>  →  <b>{res_label}</b>\n\n"
        "<i>Vérification crédits API…</i>",
    )

    api_key = CC_API_KEY.strip()
    try:
        from colab_leecher.cloudconvert_api import parse_api_keys, pick_best_key, submit_convert
        keys              = parse_api_keys(api_key)
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
            f"🎬 <code>{fname[:38]}</code>  →  <b>{res_label}</b>\n\n"
            "⏳ <i>Utilise /ccstatus pour suivre la progression.</i>",
        )
    except Exception as exc:
        log.error("[Convert] Failed: %s", exc)
        await cb.message.edit(
            f"❌ <b>Échec</b>\n\n<code>{str(exc)[:200]}</code>",
        )


# ─────────────────────────────────────────────────────────────
# Export download → Telegram upload pipeline
# ─────────────────────────────────────────────────────────────

async def _deliver_job(job: CCJob) -> None:
    from colab_leecher.uploader.telegram import upload_file
    from colab_leecher.utility.variables import Transfer, BotTimes, MSG, Messages
    from datetime import datetime

    tmp   = tempfile.mkdtemp(prefix="cc_deliver_", dir=getattr(Paths, "WORK_PATH", "/tmp"))
    fname = job.output_name or job.fname or "output.mp4"
    dest  = os.path.join(tmp, fname)

    try:
        os.makedirs(Paths.WORK_PATH, exist_ok=True)

        status_msg = await colab_bot.send_message(
            OWNER,
            f"☁️ <b>CloudConvert — Livraison</b>\n"
            f"──────────────────────\n"
            f"📥 <b>Downloading</b>  <code>{fname[:45]}</code>",
        )

        start      = time.time()
        last_edit  = [start]

        async with aiohttp.ClientSession() as sess:
            async with sess.get(job.export_url, allow_redirects=True) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0))
                done  = 0
                with open(dest, "wb") as fh:
                    async for chunk in resp.content.iter_chunked(512 * 1024):
                        fh.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if now - last_edit[0] >= 2.0 and total:
                            last_edit[0] = now
                            pct  = done / total * 100
                            spd  = done / (now - start) if now > start else 0
                            eta  = int((total - done) / spd) if spd else 0
                            bar  = _progress_bar(pct)
                            try:
                                await status_msg.edit(
                                    f"☁️ <b>CloudConvert — Livraison</b>\n"
                                    f"──────────────────────\n"
                                    f"📥 <b>Downloading</b>  <code>{fname[:40]}</code>\n\n"
                                    f"<code>[{bar}]</code>  <b>{pct:.1f}%</b>\n"
                                    f"⚡ <code>{spd / (1024*1024):.1f} MiB/s</code>"
                                    f"  ⏳ ETA <code>{eta}s</code>",
                                )
                            except Exception:
                                pass

        fsize = os.path.getsize(dest)
        log.info("[CCStatus] Downloaded %s (%.1f MiB)", fname, fsize / (1024 * 1024))

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
        log.info("[CCStatus] Delivered %s to uid=%d", fname, job.uid)

    except Exception as exc:
        log.error("[CCStatus] Delivery failed %s: %s", job.job_id, exc, exc_info=True)
        try:
            await MSG.status_msg.delete()
        except Exception:
            pass
        try:
            await colab_bot.send_message(
                OWNER,
                f"❌ <b>Delivery failed</b>\n\n"
                f"📄 <code>{fname}</code>\n"
                f"<code>{str(exc)[:250]}</code>",
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────
# Background poller — real live progress
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

                # ── Compute real weighted progress ───────────────────────
                overall_pct, active_name, active_label = _compute_weighted_pct(tasks)

                # Task message = message from the actively processing task
                task_msg = ""
                for t in tasks:
                    if t.get("status") == "processing":
                        task_msg = t.get("message") or active_label or "Processing…"
                        break

                await cc_job_store.update(
                    job.job_id,
                    progress_pct=overall_pct,
                    active_task=active_label or task_msg or "Processing…",
                    task_message=task_msg,
                )

                log.debug(
                    "[CCStatus] job=%s status=%s pct=%.1f active=%s",
                    job.job_id[:12], status, overall_pct, active_name,
                )

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

        # ── Auto-edit all open /ccstatus panels ──────────────────────────
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

        # Stop when nothing left to do
        if not cc_job_store.active_jobs() and not _open_panels:
            consecutive_idle += 1
            if consecutive_idle >= 3:
                log.info("[CCStatus] Poller idle — stopping")
                return
        else:
            consecutive_idle = 0

        await asyncio.sleep(interval)
