import os
import math
import psutil
import logging
from time import time
from PIL import Image
from os import path as ospath
from datetime import datetime
from urllib.parse import urlparse
from asyncio import get_event_loop
from colab_leecher import colab_bot
from pyrogram.errors import BadRequest
from moviepy.video.io.VideoFileClip import VideoFileClip
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from colab_leecher.utility.variables import BOT, MSG, BotTimes, Messages, Paths


# ──────────────────────────────────────────────
#  Shared visual helpers
# ──────────────────────────────────────────────

def _pct_bar(percentage: float, length: int = 12) -> str:
    filled = int(min(percentage, 100) / 100 * length)
    return "█" * filled + "░" * (length - filled)

def _speed_emoji(speed_str: str) -> str:
    if "GiB" in speed_str or "TiB" in speed_str: return "🚀"
    if "MiB" in speed_str:
        try:
            if float(speed_str.split()[0]) >= 50: return "⚡"
            if float(speed_str.split()[0]) >= 10: return "🔥"
        except Exception: pass
        return "🏃"
    return "🐢"


# ──────────────────────────────────────────────
#  Link / type detectors
# ──────────────────────────────────────────────

def isLink(_, __, update):
    if update.text:
        if "/content/" in str(update.text) or "/home" in str(update.text):
            return True
        if update.text.startswith("magnet:?xt=urn:btih:"):
            return True
        parsed = urlparse(update.text)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return True
    return False

def is_google_drive(link): return "drive.google.com" in link
def is_mega(link):         return "mega.nz" in link
def is_terabox(link):      return "terabox" in link or "1024tera" in link
def is_ytdl_link(link):    return "youtube.com" in link or "youtu.be" in link
def is_telegram(link):     return "t.me" in link
def is_torrent(link):      return "magnet" in link or "torrent" in link


# ──────────────────────────────────────────────
#  Time / size
# ──────────────────────────────────────────────

def getTime(seconds):
    seconds = int(seconds)
    d = seconds // 86400; seconds %= 86400
    h = seconds // 3600;  seconds %= 3600
    m = seconds // 60;    seconds %= 60
    if d: return f"{d}d {h}h {m}m {seconds}s"
    if h: return f"{h}h {m}m {seconds}s"
    if m: return f"{m}m {seconds}s"
    return f"{seconds}s"

def sizeUnit(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PiB"


# ──────────────────────────────────────────────
#  File helpers
# ──────────────────────────────────────────────

def fileType(file_path: str):
    ext_map = {
        ".mp4":"video",".avi":"video",".mkv":"video",".m2ts":"video",
        ".mov":"video",".ts":"video",".m3u8":"video",".webm":"video",
        ".mpg":"video",".mpeg":"video",".mpeg4":"video",".vob":"video",".m4v":"video",
        ".mp3":"audio",".wav":"audio",".flac":"audio",".aac":"audio",".ogg":"audio",
        ".jpg":"photo",".jpeg":"photo",".png":"photo",".bmp":"photo",".gif":"photo",
    }
    _, ext = ospath.splitext(file_path)
    return ext_map.get(ext.lower(), "document")

def shortFileName(path):
    if ospath.isfile(path):
        d, f = ospath.split(path)
        if len(f) > 60:
            b, e = ospath.splitext(f)
            f = b[:60 - len(e)] + e
            path = ospath.join(d, f)
    elif ospath.isdir(path):
        d, dn = ospath.split(path)
        if len(dn) > 60: path = ospath.join(d, dn[:60])
    else:
        if len(path) > 60: path = path[:60]
    return path

def getSize(path):
    if ospath.isfile(path): return ospath.getsize(path)
    total = 0
    for dp, _, fns in os.walk(path):
        for f in fns: total += ospath.getsize(ospath.join(dp, f))
    return total

def videoExtFix(file_path: str):
    if file_path.endswith(".mp4") or file_path.endswith(".mkv"): return file_path
    new = file_path + ".mp4"
    os.rename(file_path, new)
    return new

def thumbMaintainer(file_path):
    if ospath.exists(Paths.VIDEO_FRAME): os.remove(Paths.VIDEO_FRAME)
    try:
        fname, _ = ospath.splitext(ospath.basename(file_path))
        ytdl_thmb = f"{Paths.WORK_PATH}/ytdl_thumbnails/{fname}.webp"
        with VideoFileClip(file_path) as video:
            if ospath.exists(Paths.THMB_PATH): return Paths.THMB_PATH, video.duration
            elif ospath.exists(ytdl_thmb):     return convertIMG(ytdl_thmb), video.duration
            else:
                video.save_frame(Paths.VIDEO_FRAME, t=math.floor(video.duration / 2))
                return Paths.VIDEO_FRAME, video.duration
    except Exception as e:
        logging.warning(f"Thumb error: {e}")
        return (Paths.THMB_PATH if ospath.exists(Paths.THMB_PATH) else Paths.HERO_IMAGE), 0

async def setThumbnail(message):
    try:
        if ospath.exists(Paths.THMB_PATH): os.remove(Paths.THMB_PATH)
        loop = get_event_loop()
        await loop.create_task(message.download(file_name=Paths.THMB_PATH))
        BOT.Setting.thumbnail = True
        if BOT.State.task_going and MSG.status_msg:
            await MSG.status_msg.edit_media(InputMediaPhoto(Paths.THMB_PATH), reply_markup=keyboard())
        return True
    except Exception as e:
        BOT.Setting.thumbnail = False
        logging.warning(f"Thumbnail error: {e}")
        return False

def isYtdlComplete():
    for _d, _, filenames in os.walk(Paths.down_path):
        for f in filenames:
            _, ext = ospath.splitext(f)
            if ext in [".part", ".ytdl"]: return False
    return True

def convertIMG(image_path):
    img = Image.open(image_path)
    if img.mode != "RGB": img = img.convert("RGB")
    out = ospath.splitext(image_path)[0] + ".jpg"
    img.save(out, "JPEG")
    os.remove(image_path)
    return out

def applyCustomName():
    if len(BOT.Options.custom_name) != 0 and BOT.Mode.type not in ["zip", "undzip"]:
        for file_ in os.listdir(Paths.down_path):
            os.rename(
                ospath.join(Paths.down_path, file_),
                ospath.join(Paths.down_path, BOT.Options.custom_name),
            )

def speedETA(start, done, total):
    percentage = min((done / total) * 100, 100) if total else 0
    elapsed    = (datetime.now() - start).seconds
    if done > 0 and elapsed:
        raw_speed = done / elapsed
        speed = f"{sizeUnit(raw_speed)}/s"
        eta   = (total - done) / raw_speed
    else:
        speed, eta = "N/A", 0
    return speed, eta, percentage

def isTimeOver():
    passed = time() - BotTimes.current_time >= 3
    if passed: BotTimes.current_time = time()
    return passed

async def message_deleter(m1, m2):
    for m in (m1, m2):
        try: await m.delete()
        except Exception as e: logging.debug(f"Delete failed: {e}")

def multipartArchive(path: str, type: str, remove: bool):
    dirname, filename = ospath.split(path)
    name, _ = ospath.splitext(filename)
    c, size, rname = 1, 0, name
    if type == "rar":
        name_, _ = ospath.splitext(name); rname = name_
        na_p = name_ + ".part" + str(c) + ".rar"
        p_ap = ospath.join(dirname, na_p)
        while ospath.exists(p_ap):
            if remove: os.remove(p_ap)
            size += getSize(p_ap); c += 1
            na_p = name_ + ".part" + str(c) + ".rar"
            p_ap = ospath.join(dirname, na_p)
    elif type == "7z":
        na_p = name + "." + str(c).zfill(3)
        p_ap = ospath.join(dirname, na_p)
        while ospath.exists(p_ap):
            if remove: os.remove(p_ap)
            size += getSize(p_ap); c += 1
            na_p = name + "." + str(c).zfill(3)
            p_ap = ospath.join(dirname, na_p)
    elif type == "zip":
        na_p = name + ".zip"; p_ap = ospath.join(dirname, na_p)
        if ospath.exists(p_ap):
            if remove: os.remove(p_ap)
            size += getSize(p_ap)
        na_p = name + ".z" + str(c).zfill(2)
        p_ap = ospath.join(dirname, na_p)
        while ospath.exists(p_ap):
            if remove: os.remove(p_ap)
            size += getSize(p_ap); c += 1
            na_p = name + ".z" + str(c).zfill(2)
            p_ap = ospath.join(dirname, na_p)
        if rname.endswith(".zip"): rname, _ = ospath.splitext(rname)
    return rname, size


# ──────────────────────────────────────────────
#  sysINFO  — compact inline strip
# ──────────────────────────────────────────────

def sysINFO():
    ram  = psutil.Process(os.getpid()).memory_info().rss
    disk = psutil.disk_usage("/")
    cpu  = psutil.cpu_percent()
    return (
        "\n\n──────────────────\n"
        f"🖥  CPU  <code>[{_pct_bar(cpu, 8)}]</code> <b>{cpu:.0f}%</b>\n"
        f"💾  RAM  <code>{sizeUnit(ram)}</code>\n"
        f"💿  Disk Free  <code>{sizeUnit(disk.free)}</code>"
        f"{Messages.caution_msg}"
    )


# ──────────────────────────────────────────────
#  status_bar  — progress display
# ──────────────────────────────────────────────

async def status_bar(down_msg, speed, percentage, eta, done, left, engine):
    bar      = _pct_bar(float(percentage), 12)
    s_ico    = _speed_emoji(str(speed))
    pct_f    = float(percentage)
    pct_str  = f"<b>{pct_f:.1f}%</b>"
    elapsed  = getTime((datetime.now() - BotTimes.start_time).seconds)

    text = (
        f"\n<code>[{bar}]</code>  {pct_str}\n"
        "──────────────────\n"
        f"{s_ico}  <b>Speed</b>    <code>{speed}</code>\n"
        f"⚙️  <b>Engine</b>   <code>{engine}</code>\n"
        f"⏳  <b>ETA</b>      <code>{eta}</code>\n"
        f"🕰  <b>Elapsed</b>  <code>{elapsed}</code>\n"
        f"✅  <b>Done</b>     <code>{done}</code>\n"
        f"📦  <b>Total</b>    <code>{left}</code>"
    )
    try:
        if isTimeOver():
            await MSG.status_msg.edit_text(
                text=Messages.task_msg + down_msg + text + sysINFO(),
                disable_web_page_preview=True,
                reply_markup=keyboard(),
            )
    except BadRequest as e:
        logging.debug(f"Status not modified: {e}")
    except Exception as e:
        logging.warning(f"Status bar error: {e}")


# ──────────────────────────────────────────────
#  send_settings  — settings panel
# ──────────────────────────────────────────────

async def send_settings(client, message, msg_id, command: bool):
    up_mode   = "document" if BOT.Options.stream_upload else "media"
    up_toggle = "📄 → Media" if not BOT.Options.stream_upload else "🎞 → Document"
    pr   = "—" if BOT.Setting.prefix == "" else f"«{BOT.Setting.prefix}»"
    su   = "—" if BOT.Setting.suffix == "" else f"«{BOT.Setting.suffix}»"
    thmb = "✅ Set" if BOT.Setting.thumbnail else "❌ None"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(up_toggle,        callback_data=up_mode),
         InlineKeyboardButton("🎥 Video",        callback_data="video")],
        [InlineKeyboardButton("✏️ Caption Font", callback_data="caption"),
         InlineKeyboardButton("🖼 Thumbnail",    callback_data="thumb")],
        [InlineKeyboardButton("⬅️ Prefix",       callback_data="set-prefix"),
         InlineKeyboardButton("Suffix ➡️",       callback_data="set-suffix")],
        [InlineKeyboardButton("✖ Close",         callback_data="close")],
    ])

    text = (
        "⚙️ <b>BOT SETTINGS</b>\n"
        "──────────────────\n\n"
        f"📤  Upload    <code>{BOT.Setting.stream_upload}</code>\n"
        f"✂️   Split     <code>{BOT.Setting.split_video}</code>\n"
        f"🔄  Convert   <code>{BOT.Setting.convert_video}</code>\n"
        f"✏️   Caption   <code>{BOT.Setting.caption}</code>\n"
        f"⬅️   Prefix    <code>{pr}</code>\n"
        f"➡️   Suffix    <code>{su}</code>\n"
        f"🖼  Thumbnail {thmb}"
    )
    try:
        if command:
            await message.reply_text(text=text, reply_markup=kb)
        else:
            await colab_bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_id,
                text=text,
                reply_markup=kb,
            )
    except BadRequest as e:
        logging.debug(f"Settings not modified: {e}")
    except Exception as e:
        logging.warning(f"Settings error: {e}")


# ──────────────────────────────────────────────
#  Keyboard shortcut
# ──────────────────────────────────────────────

def keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel Task", callback_data="cancel"),
    ]])
