import logging
import os
import platform
import psutil
from datetime import datetime
from asyncio import sleep, get_event_loop
from pyrogram import filters, idle
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from colab_leecher import colab_bot, OWNER, NGROK_TOKEN, CC_WEBHOOK_SECRET
from colab_leecher.utility.handler import cancelTask
from colab_leecher.utility.variables import BOT, MSG, BotTimes, Paths
from colab_leecher.utility.task_manager import taskScheduler
from colab_leecher.utility.helper import (
    isLink, setThumbnail, message_deleter, send_settings,
    sizeUnit, getTime, is_ytdl_link, _pct_bar,
)
from colab_leecher.stream_extractor import (
    analyse, get_session, clear_session,
    kb_type, kb_video, kb_audio, kb_subs,
    dl_video, dl_audio, dl_sub,
)
import colab_leecher.hardsub as _hardsub_module  # registers /hardsub handlers

def _owner(m): return m.chat.id == OWNER
def _ring(p):  return "🟢" if p < 40 else ("🟡" if p < 70 else "🔴")


def _fmt_dur(secs: float) -> str:
    s = int(max(0, secs))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def _show_sx_type_menu(msg, session: dict) -> None:
    """Edit msg to show the stream type selection menu."""
    v = len(session["video"])
    a = len(session["audio"])
    s = len(session["subs"])
    dur_s = f"  ⏱ <code>{_fmt_dur(session.get('duration', 0))}</code>" if session.get("duration") else ""
    await msg.edit_text(
        "🎞 <b>STREAM EXTRACTOR</b>\n"
        "──────────────────\n\n"
        f"<b>{session['title'][:55]}</b>{dur_s}\n\n"
        f"🎬 Vidéo  <code>{v}</code>   🎵 Audio  <code>{a}</code>   💬 Sous-titres  <code>{s}</code>\n\n"
        "Choisir le type de piste :",
        reply_markup=kb_type(v, a, s),
    )

# ──────────────────────────────────────────────
#  /start
# ──────────────────────────────────────────────
@colab_bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await message.delete()
    await message.reply_text(
        "⚡ <b>ZILONG BOT</b>\n"
        "──────────────────\n"
        "🟢 Online &amp; Ready\n\n"
        "Envoie un <b>lien</b>, <b>magnet</b> ou <b>chemin</b>.\n"
        "💡 /help pour les commandes",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📣 Support", url="https://t.me/New_Animes_2025"),
        ]])
    )

# ──────────────────────────────────────────────
#  /help
# ──────────────────────────────────────────────
@colab_bot.on_message(filters.command("help") & filters.private)
async def help_cmd(client, message):
    text = (
        "📖 <b>AIDE</b>\n"
        "──────────────────\n\n"
        "🔗 <b>Sources supportées</b>\n"
        "  · HTTP/HTTPS  · Magnet\n"
        "  · Google Drive  · Mega.nz\n"
        "  · YouTube / YTDL\n"
        "  · Liens Telegram  · Chemins locaux\n\n"
        "──────────────────\n"
        "⚙️ <b>Commandes</b>\n"
        "  /settings · /stats · /ping\n"
        "  /cancel · /stop\n"
        "  /hardsub — Graver sous-titres via CloudConvert\n\n"
        "──────────────────\n"
        "🎛 <b>Options (après le lien)</b>\n"
        "  <code>[nom.ext]</code>  — nom personnalisé\n\n"
        "──────────────────\n"
        "🎞 <b>Stream Extractor</b>\n"
        "  Bouton <b>🎞 Stream Extractor</b> sur chaque lien.\n"
        "  Choisir vidéo / audio / sous-titres\n"
        "  avec langue, codec, résolution, taille.\n\n"
        "📊 <b>Media Info</b>\n"
        "  Bouton <b>📊 Media Info</b> sur chaque lien.\n"
        "  Rapport complet publié sur Telegra.ph.\n\n"
        "🔥 <b>Hardsub</b>\n"
        "  Bouton <b>🔥 Hardsub (CC)</b> sur chaque lien,\n"
        "  ou commande /hardsub.\n"
        "  Grave le sous-titre dans la vidéo via CloudConvert.\n\n"
        "🖼 Envoie une <b>image</b> pour définir la miniature"
    )
    msg = await message.reply_text(text)
    await sleep(90)
    await message_deleter(message, msg)

# ──────────────────────────────────────────────
#  /stats
# ──────────────────────────────────────────────
def _stats_text():
    cpu  = psutil.cpu_percent(interval=1)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net  = psutil.net_io_counters()
    up_s = int((datetime.now() - datetime.fromtimestamp(psutil.boot_time())).total_seconds())
    return (
        "📊 <b>STATS SERVEUR</b>\n"
        "──────────────────\n\n"
        f"🖥  <b>OS</b>      <code>{platform.system()} {platform.release()}</code>\n"
        f"🐍  <b>Python</b>  <code>v{platform.python_version()}</code>\n"
        f"⏱  <b>Uptime</b>  <code>{getTime(up_s)}</code>\n"
        f"🤖  <b>Tâche</b>   {'🟠 En cours' if BOT.State.task_going else '⚪ Inactif'}\n\n"
        f"── CPU ───────────────\n"
        f"{_ring(cpu)}  <code>[{_pct_bar(cpu,12)}]</code>  <b>{cpu:.1f}%</b>\n\n"
        f"── RAM ───────────────\n"
        f"{_ring(ram.percent)}  <code>[{_pct_bar(ram.percent,12)}]</code>  <b>{ram.percent:.1f}%</b>\n"
        f"    Utilisé <code>{sizeUnit(ram.used)}</code>  ·  Libre <code>{sizeUnit(ram.available)}</code>\n\n"
        f"── Disque ────────────\n"
        f"{_ring(disk.percent)}  <code>[{_pct_bar(disk.percent,12)}]</code>  <b>{disk.percent:.1f}%</b>\n"
        f"    Utilisé <code>{sizeUnit(disk.used)}</code>  ·  Libre <code>{sizeUnit(disk.free)}</code>\n\n"
        f"── Réseau ────────────\n"
        f"    ⬆️  <code>{sizeUnit(net.bytes_sent)}</code>\n"
        f"    ⬇️  <code>{sizeUnit(net.bytes_recv)}</code>"
    )

_STATS_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("🔄 Actualiser", callback_data="stats_refresh"),
    InlineKeyboardButton("✖ Fermer",      callback_data="close"),
]])

@colab_bot.on_message(filters.command("stats") & filters.private)
async def stats(client, message):
    if not _owner(message): return
    await message.delete()
    await message.reply_text(_stats_text(), reply_markup=_STATS_KB)

# ──────────────────────────────────────────────
#  /ping
# ──────────────────────────────────────────────
@colab_bot.on_message(filters.command("ping") & filters.private)
async def ping(client, message):
    t0  = datetime.now()
    msg = await message.reply_text("⏳")
    ms  = (datetime.now() - t0).microseconds // 1000
    if ms < 100:   q, fill = "🟢 Excellent", 12
    elif ms < 300: q, fill = "🟡 Bon",        8
    elif ms < 700: q, fill = "🟠 Moyen",       4
    else:          q, fill = "🔴 Mauvais",      1
    bar = "█" * fill + "░" * (12 - fill)
    await msg.edit_text(
        "🏓 <b>PONG</b>\n"
        "──────────────────\n\n"
        f"<code>[{bar}]</code>\n\n"
        f"⚡ <b>Latence</b>  <code>{ms} ms</code>\n"
        f"📶 <b>Qualité</b>  {q}"
    )
    await sleep(20)
    await message_deleter(message, msg)

# ──────────────────────────────────────────────
#  Commandes diverses
# ──────────────────────────────────────────────
@colab_bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message):
    if not _owner(message): return
    await message.delete()
    if BOT.State.task_going:
        await cancelTask("Annulé via /cancel")
    else:
        msg = await message.reply_text("⚠️ Aucune tâche en cours.")
        await sleep(8); await msg.delete()

@colab_bot.on_message(filters.command("stop") & filters.private)
async def stop_bot(client, message):
    if not _owner(message): return
    await message.delete()
    if BOT.State.task_going:
        await cancelTask("Arrêt du bot")
    await message.reply_text("🛑 <b>Arrêt en cours...</b> 👋")
    await sleep(2); await client.stop(); os._exit(0)

@colab_bot.on_message(filters.command("settings") & filters.private)
async def settings(client, message):
    if _owner(message):
        await message.delete()
        await send_settings(client, message, message.id, True)

@colab_bot.on_message(filters.command("setname") & filters.private)
async def custom_name(client, message):
    if len(message.command) != 2:
        msg = await message.reply_text("Usage : <code>/setname fichier.ext</code>", quote=True)
    else:
        BOT.Options.custom_name = message.command[1]
        msg = await message.reply_text(f"✅ Nom → <code>{BOT.Options.custom_name}</code>", quote=True)
    await sleep(15); await message_deleter(message, msg)

@colab_bot.on_message(filters.reply & filters.private)
async def setFix(client, message):
    if BOT.State.prefix:
        BOT.Setting.prefix = message.text; BOT.State.prefix = False
        await send_settings(client, message, message.reply_to_message_id, False)
        await message.delete()
    elif BOT.State.suffix:
        BOT.Setting.suffix = message.text; BOT.State.suffix = False
        await send_settings(client, message, message.reply_to_message_id, False)
        await message.delete()

# ──────────────────────────────────────────────
#  Réception du lien — choix du mode
# ──────────────────────────────────────────────

def _mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Leech Normal",  callback_data="mode_normal")],
        [InlineKeyboardButton("🎞 Stream Extractor", callback_data="sx_open"),
         InlineKeyboardButton("📊 Media Info",       callback_data="mi_open")],
        [InlineKeyboardButton("🔥 Hardsub (CC)",  callback_data="hs_from_url")],
    ])

@colab_bot.on_message(filters.create(isLink) & ~filters.photo & filters.private)
async def handle_url(client, message):
    if not _owner(message): return
    BOT.Options.custom_name = ""

    if BOT.State.task_going:
        msg = await message.reply_text("⚠️ Tâche en cours — /cancel d'abord.", quote=True)
        await sleep(8); await msg.delete()
        return

    src = message.text.splitlines()
    for _ in range(1):
        if not src: break
        last = src[-1].strip()
        if last.startswith("[") and last.endswith("]"):
            BOT.Options.custom_name = last[1:-1]; src.pop()

    BOT.SOURCE    = src
    BOT.Mode.ytdl = all(is_ytdl_link(l) for l in src if l.strip())
    BOT.Mode.mode = "leech"
    BOT.Mode.type = "normal"
    BOT.State.started = True

    n     = len([l for l in src if l.strip()])
    label = "🏮 YTDL" if BOT.Mode.ytdl else "🔗 Lien"

    await message.reply_text(
        f"{label}  ·  <code>{n}</code> source(s)\n<b>Choisir le mode :</b>",
        reply_markup=_mode_keyboard(), quote=True,
    )

# ──────────────────────────────────────────────
#  Callbacks
# ──────────────────────────────────────────────
@colab_bot.on_callback_query()
async def callbacks(client, cq):
    data    = cq.data
    chat_id = cq.message.chat.id

    if data == "stats_refresh":
        try: await cq.message.edit_text(_stats_text(), reply_markup=_STATS_KB)
        except Exception: pass
        return

    # ── Leech normal ───────────────────────────
    if data == "mode_normal":
        await cq.message.delete()
        MSG.status_msg = await colab_bot.send_message(
            chat_id=OWNER, text="⏳ <i>Démarrage du leech...</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Annuler", callback_data="cancel")
            ]]),
        )
        BOT.State.task_going = True
        BOT.State.started    = False
        BotTimes.start_time  = datetime.now()
        BOT.TASK = get_event_loop().create_task(taskScheduler())
        await BOT.TASK
        BOT.State.task_going = False
        return

    # ════════════════════════════════════════════
    #  STREAM EXTRACTOR
    # ════════════════════════════════════════════

    if data == "sx_open":
        url = (BOT.SOURCE or [None])[0]
        if not url:
            await cq.answer("Aucun URL trouvé.", show_alert=True); return

        await cq.message.edit_text(
            "🎞 <b>STREAM EXTRACTOR</b>\n"
            "──────────────────\n\n"
            f"⏳ <i>Analyse des pistes...</i>\n"
            f"<code>{url[:70]}{'…' if len(url) > 70 else ''}</code>"
        )

        session = await analyse(url, chat_id)

        if not session or (not session["video"] and not session["audio"] and not session["subs"]):
            await cq.message.edit_text(
                "🎞 <b>STREAM EXTRACTOR</b>\n"
                "──────────────────\n\n"
                "❌ Impossible d'extraire les pistes.\n"
                "<i>Vérifiez que le lien est accessible et compatible.</i>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏎ Retour", callback_data="sx_back")
                ]])
            )
            return

        await _show_sx_type_menu(cq.message, session)
        return

    if data == "sx_type":
        session = get_session(chat_id)
        if not session:
            await cq.answer("Session expirée. Renvoie le lien.", show_alert=True); return
        await _show_sx_type_menu(cq.message, session)
        return

    if data == "sx_video":
        session = get_session(chat_id)
        if not session: await cq.answer("Session expirée.", show_alert=True); return
        if not session["video"]: await cq.answer("Aucune piste vidéo.", show_alert=True); return
        dur_s = f"  ⏱ <code>{_fmt_dur(session.get('duration', 0))}</code>" if session.get("duration") else ""
        await cq.message.edit_text(
            f"🎬 <b>PISTES VIDÉO</b>\n"
            f"──────────────────\n"
            f"<i>{session['title'][:50]}</i>{dur_s}\n\n"
            "Appuie pour télécharger :",
            reply_markup=kb_video(session)
        )
        return

    if data == "sx_audio":
        session = get_session(chat_id)
        if not session: await cq.answer("Session expirée.", show_alert=True); return
        if not session["audio"]: await cq.answer("Aucune piste audio.", show_alert=True); return
        await cq.message.edit_text(
            "🎵 <b>PISTES AUDIO</b>\n"
            "──────────────────\n"
            "<i>drapeau  langue  [codec]  débit  taille</i>\n\n"
            "Appuie pour télécharger :",
            reply_markup=kb_audio(session)
        )
        return

    if data == "sx_subs":
        session = get_session(chat_id)
        if not session: await cq.answer("Session expirée.", show_alert=True); return
        if not session["subs"]: await cq.answer("Aucun sous-titre.", show_alert=True); return
        await cq.message.edit_text(
            "💬 <b>SOUS-TITRES</b>\n"
            "──────────────────\n"
            "<i>drapeau  langue  [format]</i>\n\n"
            "Appuie pour télécharger :",
            reply_markup=kb_subs(session)
        )
        return

    if data == "sx_back":
        clear_session(chat_id)
        n     = len([l for l in (BOT.SOURCE or []) if l.strip()])
        label = "🏮 YTDL" if BOT.Mode.ytdl else "🔗 Lien"
        await cq.message.edit_text(
            f"{label}  ·  <code>{n}</code> source(s)\n<b>Choisir le mode :</b>",
            reply_markup=_mode_keyboard()
        )
        return

    # ── Téléchargement d'un stream ─────────────
    if data.startswith("sx_dl_"):
        session = get_session(chat_id)
        if not session: await cq.answer("Session expirée.", show_alert=True); return

        parts = data.split("_")   # ["sx","dl","video","0"]
        kind  = parts[2]
        idx   = int(parts[3])

        stream = (session["video"] if kind == "video"
                  else session["audio"] if kind == "audio"
                  else session["subs"])[idx]

        kind_fr = {"video": "Vidéo", "audio": "Audio", "sub": "Sous-titre"}.get(kind, kind)
        await cq.message.edit_text(
            "🎞 <b>STREAM EXTRACTOR</b>\n"
            "──────────────────\n\n"
            f"⬇️ <i>Téléchargement {kind_fr}...</i>\n\n"
            f"<code>{stream['label'][:60]}</code>\n\n"
            "⏳ <i>Patiente...</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Annuler", callback_data="cancel")
            ]])
        )
        MSG.status_msg = cq.message

        os.makedirs(Paths.down_path, exist_ok=True)
        try:
            if kind == "video":
                fp = await dl_video(session, idx, Paths.down_path)
            elif kind == "audio":
                fp = await dl_audio(session, idx, Paths.down_path)
            else:
                fp = await dl_sub(session, idx, Paths.down_path)

            from colab_leecher.uploader.telegram import upload_file
            await upload_file(fp, os.path.basename(fp), is_last=True)
            clear_session(chat_id)

        except Exception as e:
            logging.error(f"[StreamDL] {e}")
            try:
                await cq.message.edit_text(
                    "🎞 <b>STREAM EXTRACTOR</b>\n"
                    "──────────────────\n\n"
                    f"❌ <b>Erreur :</b> <code>{e}</code>"
                )
            except Exception:
                pass
        return

    # ════════════════════════════════════════════
    #  MEDIA INFO
    # ════════════════════════════════════════════

    if data == "mi_open":
        url = (BOT.SOURCE or [None])[0]
        if not url:
            await cq.answer("Aucun URL trouvé.", show_alert=True); return

        await cq.message.edit_text(
            "📊 <b>MEDIA INFO</b>\n"
            "──────────────────\n\n"
            "⏳ <i>Téléchargement et analyse en cours...</i>\n"
            f"<code>{url[:70]}{'…' if len(url) > 70 else ''}</code>"
        )

        try:
            from colab_leecher.media_info import get_inline_summary, get_mediainfo, post_to_telegraph
            import tempfile as _tf

            # Download to a temp file
            tmp_dir  = _tf.mkdtemp(prefix="mi_", dir=getattr(Paths, "WORK_PATH", "/tmp"))
            fname    = url.split("/")[-1].split("?")[0][:60] or "media"
            tmp_path = os.path.join(tmp_dir, fname)

            import aiohttp as _aio
            async with _aio.ClientSession() as sess:
                async with sess.get(url, allow_redirects=True) as resp:
                    resp.raise_for_status()
                    # Only read up to 64 MB for probing
                    content = await resp.content.read(67_108_864)
            with open(tmp_path, "wb") as fh:
                fh.write(content)

            summary = await get_inline_summary(tmp_path)
            raw     = await get_mediainfo(tmp_path)

            kb_rows: list = []
            try:
                tph_url = await post_to_telegraph(fname, raw)
                kb_rows.append([InlineKeyboardButton("📋 MediaInfo complet →", url=tph_url)])
            except Exception:
                pass
            kb_rows.append([InlineKeyboardButton("⏎ Retour", callback_data="sx_back")])

            import shutil as _sh
            _sh.rmtree(tmp_dir, ignore_errors=True)

            await cq.message.edit_text(
                summary,
                reply_markup=InlineKeyboardMarkup(kb_rows)
            )
        except Exception as exc:
            logging.error(f"[MediaInfo] {exc}")
            await cq.message.edit_text(
                "📊 <b>MEDIA INFO</b>\n"
                "──────────────────\n\n"
                f"❌ <b>Erreur :</b> <code>{exc}</code>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏎ Retour", callback_data="sx_back")
                ]])
            )
        return

    # ── Hardsub from URL ───────────────────────
    if data == "hs_from_url":
        url = (BOT.SOURCE or [None])[0]
        if not url:
            await cq.answer("Aucun URL trouvé.", show_alert=True); return
        raw_name = url.split("/")[-1].split("?")[0]
        import urllib.parse as _up
        fname    = _up.unquote_plus(raw_name)[:50] or "video.mkv"
        uid      = cq.from_user.id
        await _hardsub_module.start_hardsub_for_url(client, cq.message, uid, url, fname)
        return

    # ── Settings ───────────────────────────────
    if data == "video":
        await cq.message.edit_text(
            "🎥 <b>PARAMÈTRES VIDÉO</b>\n"
            "──────────────────\n\n"
            f"Convertir  <code>{BOT.Setting.convert_video}</code>\n"
            f"Découper   <code>{BOT.Setting.split_video}</code>\n"
            f"Format     <code>{BOT.Options.video_out.upper()}</code>\n"
            f"Qualité    <code>{BOT.Setting.convert_quality}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✂️ Découper",  callback_data="split-true"),
                 InlineKeyboardButton("🗜 Zipper",    callback_data="split-false")],
                [InlineKeyboardButton("🔄 Convertir", callback_data="convert-true"),
                 InlineKeyboardButton("🚫 Non",       callback_data="convert-false")],
                [InlineKeyboardButton("🎬 MP4",       callback_data="mp4"),
                 InlineKeyboardButton("📦 MKV",       callback_data="mkv")],
                [InlineKeyboardButton("🔝 Haute",     callback_data="q-High"),
                 InlineKeyboardButton("📉 Basse",     callback_data="q-Low")],
                [InlineKeyboardButton("⏎ Retour",     callback_data="back")],
            ]))
    elif data == "caption":
        await cq.message.edit_text(
            "✏️ <b>STYLE CAPTION</b>\n"
            "──────────────────\n\n"
            f"Actuel : <code>{BOT.Setting.caption}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Monospace", callback_data="code-Monospace"),
                 InlineKeyboardButton("Gras",      callback_data="b-Bold")],
                [InlineKeyboardButton("Italique",  callback_data="i-Italic"),
                 InlineKeyboardButton("Souligné",  callback_data="u-Underlined")],
                [InlineKeyboardButton("Normal",    callback_data="p-Regular")],
                [InlineKeyboardButton("⏎ Retour",  callback_data="back")],
            ]))
    elif data == "thumb":
        await cq.message.edit_text(
            "🖼 <b>MINIATURE</b>\n"
            "──────────────────\n\n"
            f"Statut : {'✅ Définie' if BOT.Setting.thumbnail else '❌ Aucune'}\n\n"
            "Envoie une image pour mettre à jour.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Supprimer", callback_data="del-thumb")],
                [InlineKeyboardButton("⏎ Retour",    callback_data="back")],
            ]))
    elif data == "del-thumb":
        if BOT.Setting.thumbnail:
            try: os.remove(Paths.THMB_PATH)
            except Exception: pass
        BOT.Setting.thumbnail = False
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "set-prefix":
        await cq.message.edit_text("Réponds avec ton texte de <b>préfixe</b> :")
        BOT.State.prefix = True
    elif data == "set-suffix":
        await cq.message.edit_text("Réponds avec ton texte de <b>suffixe</b> :")
        BOT.State.suffix = True
    elif data in ["code-Monospace","p-Regular","b-Bold","i-Italic","u-Underlined"]:
        r = data.split("-"); BOT.Options.caption = r[0]; BOT.Setting.caption = r[1]
        await send_settings(client, cq.message, cq.message.id, False)
    elif data in ["split-true","split-false"]:
        BOT.Options.is_split    = data == "split-true"
        BOT.Setting.split_video = "Découpé" if data == "split-true" else "Zippé"
        await send_settings(client, cq.message, cq.message.id, False)
    elif data in ["convert-true","convert-false","mp4","mkv","q-High","q-Low"]:
        if   data == "convert-true":  BOT.Options.convert_video = True;  BOT.Setting.convert_video = "Oui"
        elif data == "convert-false": BOT.Options.convert_video = False; BOT.Setting.convert_video = "Non"
        elif data == "q-High": BOT.Setting.convert_quality = "Haute"; BOT.Options.convert_quality = True
        elif data == "q-Low":  BOT.Setting.convert_quality = "Basse"; BOT.Options.convert_quality = False
        else: BOT.Options.video_out = data
        await send_settings(client, cq.message, cq.message.id, False)
    elif data in ["media","document"]:
        BOT.Options.stream_upload = data == "media"
        BOT.Setting.stream_upload = "Média" if data == "media" else "Document"
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "close":
        await cq.message.delete()
    elif data == "back":
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "cancel":
        await cancelTask("Annulé par l'utilisateur")

# ──────────────────────────────────────────────
#  Photo → miniature
# ──────────────────────────────────────────────
@colab_bot.on_message(filters.photo & filters.private)
async def handle_photo(client, message):
    msg = await message.reply_text("⏳ <i>Sauvegarde de la miniature...</i>")
    if await setThumbnail(message):
        await msg.edit_text("✅ Miniature mise à jour.")
        await message.delete()
    else:
        await msg.edit_text("❌ Impossible de définir la miniature.")
    await sleep(10)
    await message_deleter(message, msg)


# ──────────────────────────────────────────────
#  Async main — start bot + optional CC webhook
# ──────────────────────────────────────────────
async def _main():
    await colab_bot.start()
    logging.info("⚡ Zilong démarré.")

    # ── CloudConvert webhook (only when NGROK_TOKEN or local testing) ──
    if NGROK_TOKEN or CC_WEBHOOK_SECRET:
        try:
            import colab_leecher.cloudconvert_hook as _cc_hook
            _cc_hook.WEBHOOK_SECRET = CC_WEBHOOK_SECRET

            webhook_url = await _cc_hook.start_webhook_server(ngrok_token=NGROK_TOKEN)

            if webhook_url:
                logging.info("☁️  CC webhook URL: %s", webhook_url)
                await colab_bot.send_message(
                    OWNER,
                    f"☁️ <b>CloudConvert Webhook Active</b>\n\n"
                    f"<i>Event to subscribe: <b>job.finished</b></i>",
                )
            else:
                logging.info("☁️  CC webhook server running on localhost only (no ngrok URL).")
        except Exception as exc:
            logging.error("Failed to start CC webhook server: %s", exc)
    else:
        logging.info("ℹ️  CloudConvert webhook disabled "
                     "(set NGROK_TOKEN in credentials.json to enable).")

    await idle()

    # Graceful shutdown
    try:
        import colab_leecher.cloudconvert_hook as _cc_hook
        await _cc_hook.stop_webhook_server()
    except Exception:
        pass

    await colab_bot.stop()


# Entry point — use the pre-created event loop from __init__.py
from colab_leecher import loop
loop.run_until_complete(_main())
