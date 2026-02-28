import logging
import os
import platform
import psutil
from datetime import datetime
from asyncio import sleep, get_event_loop
from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from colab_leecher import colab_bot, OWNER
from colab_leecher.utility.handler import cancelTask
from colab_leecher.utility.variables import BOT, MSG, BotTimes, Paths
from colab_leecher.utility.task_manager import taskScheduler
from colab_leecher.utility.helper import (
    isLink, setThumbnail, message_deleter, send_settings,
    sizeUnit, getTime, is_ytdl_link, _pct_bar,
)

def _owner(m): return m.chat.id == OWNER
def _ring(p):  return "🟢" if p < 40 else ("🟡" if p < 70 else "🔴")

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
        "  /cancel · /stop\n\n"
        "──────────────────\n"
        "🎛 <b>Options (après le lien)</b>\n"
        "  <code>[nom.ext]</code>  — nom personnalisé\n\n"
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
#  Réception du lien — leech direct (no mode selection)
# ──────────────────────────────────────────────
@colab_bot.on_message(filters.create(isLink) & ~filters.photo & filters.private)
async def handle_url(client, message):
    if not _owner(message): return
    BOT.Options.custom_name = ""

    if BOT.State.task_going:
        msg = await message.reply_text("⚠️ Tâche en cours — /cancel d'abord.", quote=True)
        await sleep(8); await msg.delete()
        return

    src = message.text.splitlines()
    # Parse optional custom name: last line in [brackets]
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

    MSG.status_msg = await message.reply_text(
        f"⏳ <i>Démarrage du leech...</i>\n{label}  ·  <code>{n}</code> source(s)",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Annuler", callback_data="cancel")
        ]]),
        quote=True,
    )

    BOT.State.task_going = True
    BOT.State.started    = False
    BotTimes.start_time  = datetime.now()
    BOT.TASK = get_event_loop().create_task(taskScheduler())
    await BOT.TASK
    BOT.State.task_going = False

# ──────────────────────────────────────────────
#  Callbacks
# ──────────────────────────────────────────────
@colab_bot.on_callback_query()
async def callbacks(client, cq):
    data    = cq.data
    chat_id = cq.message.chat.id

    # ── Stats ──────────────────────────────────
    if data == "stats_refresh":
        try: await cq.message.edit_text(_stats_text(), reply_markup=_STATS_KB)
        except Exception: pass
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

logging.info("⚡ Zilong démarré.")
colab_bot.run()
