"""
colab_leecher/hardsub.py
────────────────────────────────────────────────────────────────
/hardsub command — CloudConvert-powered subtitle burning.

FIX: _make_tmp now calls os.makedirs(base, exist_ok=True) before
     tempfile.mkdtemp so it works even when WORK_PATH has not been
     created yet (i.e. no leech task has run in this session).
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import urllib.parse as _urlparse

import aiohttp
from pyrogram import filters
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from colab_leecher import colab_bot, OWNER, CC_API_KEY
from colab_leecher.utility.variables import Paths

log = logging.getLogger(__name__)

_SUB_EXTS = {".ass", ".srt", ".vtt", ".ssa", ".sub", ".txt"}

# ── Per-user flow state ───────────────────────────────────────
_STATE: dict[int, dict] = {}


def _user_state(uid: int) -> dict | None:
    return _STATE.get(uid)


def _clear(uid: int) -> None:
    s = _STATE.pop(uid, None)
    if s and s.get("tmp"):
        import shutil
        shutil.rmtree(s["tmp"], ignore_errors=True)


def _make_tmp(uid: int) -> str:
    base = getattr(Paths, "WORK_PATH", "/tmp")
    # FIX: ensure the parent directory exists before calling mkdtemp
    os.makedirs(base, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=f"hardsub_{uid}_", dir=base)
    return tmp


# ─────────────────────────────────────────────────────────────
# Keyboards
# ─────────────────────────────────────────────────────────────

def _more_or_done_kb(uid: int, count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Ajouter une vidéo",       callback_data=f"hs_more|{uid}"),
         InlineKeyboardButton(f"✅ Terminer ({count}) → Sub", callback_data=f"hs_done|{uid}")],
        [InlineKeyboardButton("❌ Annuler",                   callback_data=f"hs_cancel|{uid}")],
    ])


# ─────────────────────────────────────────────────────────────
# Public entry-point (called from __main__ / stream extractor)
# ─────────────────────────────────────────────────────────────

async def start_hardsub_for_url(
    client, st, uid: int, url: str, fname: str,
) -> None:
    """
    Begin a hardsub flow with a pre-resolved direct video URL.
    The video is NOT downloaded locally — CloudConvert fetches it.
    """
    _clear(uid)
    tmp = _make_tmp(uid)
    _STATE[uid] = {
        "step":      "waiting_subtitle",
        "tmp":       tmp,
        "videos":    [{"path": None, "url": url, "fname": fname}],
        "sub_path":  None,
        "sub_fname": None,
    }
    await st.edit_text(
        "🔥 <b>Hardsub</b>\n"
        "──────────────────────\n\n"
        f"🎬 <code>{fname[:45]}</code>\n"
        "☁️ <i>CloudConvert récupèrera la vidéo directement</i>\n\n"
        "Envoie le <b>sous-titre</b> :\n"
        "• Un <b>fichier</b> (.ass / .srt / .vtt / .txt)\n"
        "• Une <b>URL</b> vers un fichier de sous-titres\n\n"
        "<i>Envoie /cancel pour annuler.</i>",
    )


# ─────────────────────────────────────────────────────────────
# Job submission helpers
# ─────────────────────────────────────────────────────────────

async def _submit_one_job(
    api_key: str, video: dict, sub_path: str, sub_fname: str, uid: int,
) -> tuple[str, str, bool]:
    from colab_leecher.cloudconvert_api import submit_hardsub

    video_fname = video.get("fname", "video.mkv")
    name_base   = os.path.splitext(video_fname)[0]
    output_name = re.sub(r"[^\w\s\-\[\]()]", "_", name_base).strip() + " [VOSTFR].mp4"

    try:
        job_id = await submit_hardsub(
            api_key,
            video_path=video.get("path"),
            video_url=video.get("url"),
            subtitle_path=sub_path,
            output_name=output_name,
            scale_height=0,
        )
        # Register job in CC job store for /ccstatus tracking
        try:
            from colab_leecher.cc_job_store import cc_job_store, CCJob
            from colab_leecher.ccstatus import ensure_poller
            job = CCJob(
                job_id=job_id, uid=uid, fname=video_fname,
                sub_fname=sub_fname, output_name=output_name, status="processing",
            )
            await cc_job_store.add(job)
            ensure_poller()
        except Exception as e:
            log.warning("[Hardsub] Could not register job in store: %s", e)
        return video_fname, job_id, True
    except Exception as exc:
        log.error("[Hardsub] Job failed for %s: %s", video_fname, exc)
        return video_fname, str(exc)[:80], False


async def _submit_batch(st, state: dict, uid: int) -> None:
    videos    = state.get("videos", [])
    sub_path  = state["sub_path"]
    sub_fname = state.get("sub_fname", "subtitle.ass")
    count     = len(videos)

    vid_list = "\n".join(
        f"  {i+1}. <code>{v['fname'][:40]}</code>"
        for i, v in enumerate(videos)
    )
    await st.edit_text(
        f"☁️ <b>Soumission {count} job{'s' if count > 1 else ''}…</b>\n"
        "──────────────────────\n\n"
        f"{vid_list}\n\n"
        f"💬 <code>{sub_fname[:42]}</code>\n\n"
        "<i>Vérification de la clé API et création des jobs…</i>",
    )

    api_key = CC_API_KEY.strip()
    if not api_key:
        await st.edit_text(
            "❌ <b>CC_API_KEY non configurée</b>\n\n"
            "Ajoute <code>CC_API_KEY</code> dans <code>credentials.json</code>.\n"
            "Obtiens une clé sur cloudconvert.com → API Keys."
        )
        _clear(uid)
        return

    results: list[str] = []
    ok_count = 0

    for i, video in enumerate(videos):
        vname, result, success = await _submit_one_job(
            api_key, video, sub_path, sub_fname, uid,
        )
        if success:
            results.append(f"✅ {i+1}. <code>{vname[:35]}</code> → <code>{result}</code>")
            ok_count += 1
        else:
            results.append(f"❌ {i+1}. <code>{vname[:35]}</code> — {result}")

    result_text = "\n".join(results)
    await st.edit_text(
        f"{'✅' if ok_count == count else '⚠️'} <b>Hardsub — {ok_count}/{count} soumis</b>\n"
        "──────────────────────\n\n"
        f"{result_text}\n\n"
        f"💬 <code>{sub_fname[:38]}</code>\n\n"
        "⏳ <i>CloudConvert traite…\n"
        "Utilise /ccstatus pour suivre la progression.\n"
        "Le résultat sera livré automatiquement.</i>",
    )

    log.info("[Hardsub] Batch: %d/%d jobs submitted for uid=%d", ok_count, count, uid)
    _clear(uid)


async def _video_added(st, state: dict, uid: int) -> None:
    videos   = state.get("videos", [])
    count    = len(videos)
    vid_list = "\n".join(
        f"  {i+1}. <code>{v['fname'][:40]}</code>"
        for i, v in enumerate(videos)
    )
    await st.edit_text(
        f"✅ <b>Vidéo {count} ajoutée !</b>\n"
        "──────────────────────\n\n"
        f"{vid_list}\n\n"
        "Envoie une <b>autre vidéo</b> ou tape <b>Terminer</b>.",
        reply_markup=_more_or_done_kb(uid, count),
    )


# ─────────────────────────────────────────────────────────────
# /hardsub command
# ─────────────────────────────────────────────────────────────

@colab_bot.on_message(filters.command("hardsub") & filters.private)
async def cmd_hardsub(client, msg: Message):
    uid = msg.from_user.id
    if uid != OWNER:
        return

    api_key = CC_API_KEY.strip()
    if not api_key:
        return await msg.reply(
            "❌ <b>CC_API_KEY non configurée</b>\n\n"
            "Ajoute <code>\"CC_API_KEY\": \"ta_clé\"</code> dans <code>credentials.json</code>.\n\n"
            "Obtiens une clé sur : cloudconvert.com → Dashboard → API → API Keys\n"
            "⚠️ Utilise une clé <b>Live</b>, pas Sandbox.",
        )

    _clear(uid)
    tmp = _make_tmp(uid)
    _STATE[uid] = {
        "step":      "waiting_video",
        "tmp":       tmp,
        "videos":    [],
        "sub_path":  None,
        "sub_fname": None,
    }

    await msg.reply(
        "🔥 <b>CloudConvert Hardsub</b>\n"
        "──────────────────────\n\n"
        "Envoie la <b>vidéo</b> :\n"
        "• Un <b>fichier vidéo</b> (upload Telegram)\n"
        "• Une <b>URL directe</b> (lien HTTP vers .mkv/.mp4)\n\n"
        "📦 <i>Tu peux envoyer plusieurs vidéos — elles auront\n"
        "toutes le même sous-titre gravé.</i>\n\n"
        "<i>Envoie /cancel pour annuler.</i>",
    )


# ─────────────────────────────────────────────────────────────
# /cancel — hardsub-specific override
# ─────────────────────────────────────────────────────────────

@colab_bot.on_message(filters.command("cancel") & filters.private, group=4)
async def hardsub_cancel(client, msg: Message):
    uid = msg.from_user.id
    if uid in _STATE:
        _clear(uid)
        await msg.reply("❌ Hardsub annulé.")
        msg.stop_propagation()


# ─────────────────────────────────────────────────────────────
# Inline buttons: more / done / cancel
# ─────────────────────────────────────────────────────────────

@colab_bot.on_callback_query(filters.regex(r"^hs_(more|done|cancel)\|"))
async def hardsub_flow_cb(client, cb: CallbackQuery):
    parts  = cb.data.split("|")
    action = parts[0].split("_")[1]
    uid    = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else cb.from_user.id
    state  = _user_state(uid)

    if not state:
        return await cb.answer("Session expirée.", show_alert=True)
    await cb.answer()

    if action == "cancel":
        _clear(uid)
        await cb.message.delete()
        return

    if action == "more":
        state["step"] = "waiting_video"
        await cb.message.edit_text(
            f"📦 <b>{len(state['videos'])} vidéo(s) en file</b>\n\n"
            "Envoie la prochaine <b>vidéo</b> (fichier / URL) :",
        )
        return

    if action == "done":
        if not state["videos"]:
            return await cb.answer("Aucune vidéo ajoutée !", show_alert=True)
        state["step"] = "waiting_subtitle"
        count = len(state["videos"])
        await cb.message.edit_text(
            f"✅ <b>{count} vidéo{'s' if count > 1 else ''} en file</b>\n\n"
            "Envoie le <b>sous-titre</b> (un pour toutes) :\n"
            "• Un <b>fichier</b> (.ass / .srt / .vtt / .txt)\n"
            "• Une <b>URL</b> vers un fichier de sous-titres",
        )


# ─────────────────────────────────────────────────────────────
# Step 1 — receive video FILE
# ─────────────────────────────────────────────────────────────

@colab_bot.on_message(
    filters.private & (filters.video | filters.document),
    group=1,
)
async def hardsub_video_file(client, msg: Message):
    uid   = msg.from_user.id
    state = _user_state(uid)
    if not state or state["step"] != "waiting_video":
        return

    media = msg.video or msg.document
    if not media:
        return

    fname = getattr(media, "file_name", None) or "video.mkv"
    ext   = os.path.splitext(fname)[1].lower()
    _VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv",
                   ".ts", ".m2ts", ".wmv", ".m4v"}
    if ext not in _VIDEO_EXTS and not msg.video:
        return

    fsize = getattr(media, "file_size", 0) or 0
    st    = await msg.reply(f"⬇️ Téléchargement <code>{fname[:40]}</code>…")

    try:
        path = await client.download_media(
            media,
            file_name=os.path.join(state["tmp"], fname),
            progress=lambda cur, tot: None,
        )
        state["videos"].append({
            "path":  path,
            "url":   None,
            "fname": os.path.basename(path),
        })
        await _video_added(st, state, uid)
    except Exception as exc:
        await st.edit_text(f"❌ Échec du téléchargement : <code>{exc}</code>")

    msg.stop_propagation()


# ─────────────────────────────────────────────────────────────
# Step 1 — receive video URL / Step 2b — receive subtitle URL
# ─────────────────────────────────────────────────────────────

@colab_bot.on_message(
    filters.private & filters.text & ~filters.command(
        ["start", "help", "settings", "stats", "ping",
         "cancel", "stop", "setname", "hardsub", "ccstatus", "convert", "botname"]
    ),
    group=1,
)
async def hardsub_url_or_sub_url(client, msg: Message):
    uid   = msg.from_user.id
    state = _user_state(uid)
    if not state:
        return
    if state["step"] not in ("waiting_video", "waiting_subtitle"):
        return

    text   = msg.text.strip()
    url_re = re.compile(r"^(https?://\S+)$", re.I)
    if not url_re.match(text):
        return

    if state["step"] == "waiting_subtitle":
        await _handle_subtitle_url(msg, state, text, uid)
        msg.stop_propagation()
        return

    raw_name = text.split("/")[-1].split("?")[0]
    fname    = _urlparse.unquote_plus(raw_name)[:50] or "video.mkv"
    state["videos"].append({"path": None, "url": text, "fname": fname})
    st = await msg.reply(
        f"✅ URL vidéo ajoutée : <code>{fname[:40]}</code>\n"
        "☁️ <i>CloudConvert récupèrera la vidéo directement</i>",
    )
    await _video_added(st, state, uid)
    msg.stop_propagation()


# ─────────────────────────────────────────────────────────────
# Step 2a — receive subtitle FILE
# ─────────────────────────────────────────────────────────────

@colab_bot.on_message(
    filters.private & filters.document,
    group=0,
)
async def hardsub_subtitle_file(client, msg: Message):
    uid   = msg.from_user.id
    state = _user_state(uid)
    if not state or state["step"] != "waiting_subtitle":
        return

    media = msg.document
    if not media:
        return

    fname = getattr(media, "file_name", None) or "subtitle.ass"
    ext   = os.path.splitext(fname)[1].lower()
    if ext not in _SUB_EXTS:
        await msg.reply(
            f"❌ <b>Type non supporté</b> : <code>{ext or 'inconnu'}</code>\n\n"
            "Envoie un fichier de sous-titres :\n"
            "<code>.ass  .srt  .vtt  .ssa  .sub  .txt</code>",
        )
        msg.stop_propagation()
        return

    st = await msg.reply("⬇️ Téléchargement du sous-titre…")
    try:
        sub_path = await client.download_media(
            media,
            file_name=os.path.join(state["tmp"], fname),
        )
        state["sub_path"]  = sub_path
        state["sub_fname"] = os.path.basename(sub_path)
    except Exception as exc:
        await st.edit_text(f"❌ Échec du téléchargement : <code>{exc}</code>")
        _clear(uid)
        msg.stop_propagation()
        return

    await _submit_batch(st, state, uid)
    msg.stop_propagation()


# ─────────────────────────────────────────────────────────────
# Step 2b helper — subtitle from URL
# ─────────────────────────────────────────────────────────────

async def _handle_subtitle_url(msg: Message, state: dict, url: str, uid: int) -> None:
    parsed_path = _urlparse.urlparse(url).path
    raw_fname   = os.path.basename(parsed_path)
    fname       = _urlparse.unquote_plus(raw_fname) if raw_fname else "subtitle.ass"
    ext         = os.path.splitext(fname)[1].lower()
    if ext not in _SUB_EXTS:
        fname += ".ass"
    fname = re.sub(r'[\\/:*?"<>|]', "_", fname)

    st = await msg.reply(
        f"⬇️ Téléchargement du sous-titre…\n<code>{url[:60]}</code>",
    )
    try:
        sub_path = os.path.join(state["tmp"], fname)
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, headers={"User-Agent": "Mozilla/5.0"},
                                allow_redirects=True) as resp:
                resp.raise_for_status()
                cd = resp.headers.get("Content-Disposition", "")
                if "filename=" in cd:
                    cd_fname = cd.split("filename=")[-1].strip().strip('"').strip("'")
                    cd_fname = _urlparse.unquote_plus(cd_fname)
                    cd_ext   = os.path.splitext(cd_fname)[1].lower()
                    if cd_ext in _SUB_EXTS:
                        fname    = re.sub(r'[\\/:*?"<>|]', "_", cd_fname)
                        sub_path = os.path.join(state["tmp"], fname)
                content = await resp.read()

        if len(content) > 10_000_000:
            await st.edit_text("❌ Fichier trop volumineux — ce n'est pas un sous-titre.")
            _clear(uid)
            return

        with open(sub_path, "wb") as f:
            f.write(content)

        state["sub_path"]  = sub_path
        state["sub_fname"] = fname

    except Exception as exc:
        log.error("[Hardsub] Subtitle URL failed: %s", exc)
        await st.edit_text(
            f"❌ Échec du téléchargement :\n<code>{str(exc)[:200]}</code>",
        )
        _clear(uid)
        return

    await _submit_batch(st, state, uid)
