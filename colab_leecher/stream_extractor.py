"""
colab_leecher/stream_extractor.py
──────────────────────────────────────────────────────────────
Stream Extractor for zilong-leech.

Analyses a URL or direct link and lists all tracks:
  - ffprobe : direct HTTP links, local files, seedr, DDL
  - yt-dlp  : YouTube, streaming platforms

Two sources, ffprobe wins on direct links.
Sessions are keyed by chat_id and stored in memory.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

# ── Per-chat session store ─────────────────────────────────────
_sessions: dict[int, dict] = {}
_pool = ThreadPoolExecutor(max_workers=2)


# ─────────────────────────────────────────────────────────────
# Language helpers
# ─────────────────────────────────────────────────────────────

_FLAGS: dict[str, str] = {
    "en": "🇬🇧", "fr": "🇫🇷", "de": "🇩🇪", "es": "🇪🇸", "pt": "🇵🇹",
    "it": "🇮🇹", "ru": "🇷🇺", "ja": "🇯🇵", "ko": "🇰🇷", "zh": "🇨🇳",
    "ar": "🇸🇦", "hi": "🇮🇳", "tr": "🇹🇷", "nl": "🇳🇱", "pl": "🇵🇱",
    "sv": "🇸🇪", "da": "🇩🇰", "fi": "🇫🇮", "cs": "🇨🇿", "uk": "🇺🇦",
    "ro": "🇷🇴", "hu": "🇭🇺", "el": "🇬🇷", "he": "🇮🇱", "th": "🇹🇭",
    "vi": "🇻🇳", "id": "🇮🇩", "ms": "🇲🇾", "no": "🇳🇴", "und": "🌐",
}

_LANG_NAMES: dict[str, str] = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "pt": "Portuguese", "it": "Italian", "ru": "Russian", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "hi": "Hindi",
    "tr": "Turkish", "nl": "Dutch", "pl": "Polish", "sv": "Swedish",
    "da": "Danish", "fi": "Finnish", "cs": "Czech", "uk": "Ukrainian",
    "ro": "Romanian", "hu": "Hungarian", "el": "Greek", "he": "Hebrew",
    "th": "Thai", "vi": "Vietnamese", "id": "Indonesian", "ms": "Malay",
    "no": "Norwegian", "und": "Unknown",
    # 3-letter codes
    "eng": "English", "fra": "French", "fre": "French", "deu": "German",
    "ger": "German", "spa": "Spanish", "por": "Portuguese", "ita": "Italian",
    "rus": "Russian", "jpn": "Japanese", "kor": "Korean", "chi": "Chinese",
    "zho": "Chinese", "ara": "Arabic", "hin": "Hindi", "tur": "Turkish",
    "nld": "Dutch", "pol": "Polish", "swe": "Swedish", "dan": "Danish",
    "fin": "Finnish", "ces": "Czech", "cze": "Czech", "ukr": "Ukrainian",
    "ron": "Romanian", "rum": "Romanian", "hun": "Hungarian", "bul": "Bulgarian",
    "ind": "Indonesian", "msa": "Malay", "nor": "Norwegian",
}


def _flag(code: str) -> str:
    if not code:
        return "🌐"
    key = code.split("-")[0].lower()
    # 3-letter → 2-letter fallback
    if len(key) == 3:
        mapping = {
            "eng": "en", "fra": "fr", "fre": "fr", "deu": "de", "ger": "de",
            "spa": "es", "por": "pt", "ita": "it", "rus": "ru", "jpn": "ja",
            "kor": "ko", "chi": "zh", "zho": "zh", "ara": "ar", "hin": "hi",
            "tur": "tr", "nld": "nl", "pol": "pl", "swe": "sv", "dan": "da",
            "fin": "fi", "ces": "cs", "cze": "cs", "ukr": "uk", "ron": "ro",
            "rum": "ro", "hun": "hu", "ind": "id", "msa": "ms", "nor": "no",
            "bul": "bg",
        }
        key = mapping.get(key, key)
    return _FLAGS.get(key, "🌐")


def _lang_name(code: str) -> str:
    if not code:
        return "Unknown"
    return _LANG_NAMES.get(code.lower(), code.upper())


def _sz(b) -> str:
    if not b or b <= 0:
        return "?"
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f} {u}"
        b /= 1024
    return f"{b:.1f} GB"


def _fmt_dur(secs: float) -> str:
    s = int(max(0, secs))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ─────────────────────────────────────────────────────────────
# ffprobe — direct HTTP links & local files
# ─────────────────────────────────────────────────────────────

def _ffprobe_sync(url: str) -> dict | None:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-allowed_extensions", "ALL",
        "-analyzeduration", "20000000",
        "-probesize", "50000000",
        "-print_format", "json",
        "-show_format", "-show_streams",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if any(s.get("codec_type") in ("video", "audio", "subtitle") for s in streams):
            return data
        return None
    except Exception as exc:
        log.debug("[ffprobe] error: %s", exc)
        return None


def _parse_ffprobe(info: dict, url: str) -> dict:
    streams  = info.get("streams", [])
    fmt      = info.get("format", {})
    duration = float(fmt.get("duration") or 0)
    total_sz = int(fmt.get("size") or 0)
    title    = (fmt.get("tags") or {}).get("title") or url.split("/")[-1].split("?")[0][:60]

    videos, audios, subs = [], [], []

    for s in streams:
        codec_type = s.get("codec_type", "")
        codec_name = s.get("codec_name", "unknown")
        lang       = ((s.get("tags") or {}).get("language") or "und").lower()
        idx        = s.get("index", 0)
        title_tag  = (s.get("tags") or {}).get("title", "")

        if codec_type == "video":
            w   = s.get("width") or 0
            h   = s.get("height") or 0
            fps_raw = s.get("r_frame_rate", "0/1")
            try:
                num, den = fps_raw.split("/")
                fps = round(float(num) / max(float(den), 1))
            except Exception:
                fps = 0
            br  = int(s.get("bit_rate") or 0)
            sz  = int(br * duration / 8) if br and duration else 0
            res = f"{h}p" if h else f"{w}×{h}"
            fps_s = f" {fps}fps" if fps > 30 else ""
            label = f"🎬  {res}{fps_s}  [{codec_name.upper()}]  {_sz(sz or total_sz)}"
            if title_tag:
                label += f"  {title_tag}"
            videos.append({
                "id": str(idx), "label": label,
                "h": h, "fps": fps, "sz": sz,
                "lang": lang, "codec": codec_name,
                "map": f"0:{idx}", "ext": "mkv",
            })

        elif codec_type == "audio":
            channels = s.get("channels") or 0
            br       = int(s.get("bit_rate") or 0)
            sz       = int(br * duration / 8) if br and duration else 0
            ch_s     = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}.get(channels, f"{channels}ch") if channels else ""
            br_s     = f"{br // 1000}kbps" if br else ""
            flag     = _flag(lang)
            lname    = _lang_name(lang)
            label    = f"{flag}  {lname}  [{codec_name.upper()}]  {ch_s}  {br_s}  {_sz(sz)}"
            if title_tag:
                label += f"  {title_tag}"
            audios.append({
                "id": str(idx), "label": label.strip(),
                "abr": br // 1000 if br else 0,
                "sz": sz, "lang": lang,
                "map": f"0:{idx}", "ext": "mka",
            })

        elif codec_type == "subtitle":
            flag  = _flag(lang)
            lname = _lang_name(lang)
            forced = " ⚡Forced" if (s.get("tags") or {}).get("forced") else ""
            label = f"{flag}  {lname}  [{codec_name.upper()}]{forced}"
            if title_tag:
                label += f"  ({title_tag})"
            subs.append({
                "id": str(idx), "label": label,
                "lang": lang, "map": f"0:{idx}",
                "ext": "srt" if codec_name in ("subrip", "mov_text") else "ass",
                "url": None,
            })

    return {
        "url": url, "title": title,
        "video": videos, "audio": audios, "subs": subs,
        "source": "ffprobe", "duration": duration,
    }


# ─────────────────────────────────────────────────────────────
# yt-dlp — streaming platforms
# ─────────────────────────────────────────────────────────────

_QUALITY_ORDER = ["4K", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p", "Audio"]
_QUALITY_ICON  = {
    "4K": "🔷", "1440p": "🟣", "1080p": "🔵", "720p": "🟢",
    "480p": "🟡", "360p": "🟠", "240p": "🔴", "144p": "⚫", "Audio": "🎵",
}


def _quality_bucket(h: int, w: int = 0) -> str:
    if h >= 2160 or w >= 3840: return "4K"
    if h >= 1440: return "1440p"
    if h >= 1080: return "1080p"
    if h >= 720:  return "720p"
    if h >= 480:  return "480p"
    if h >= 360:  return "360p"
    if h >= 240:  return "240p"
    if h > 0:     return "144p"
    return "Audio"


def _ytdlp_sync(url: str) -> dict | None:
    opts = {
        "quiet": True, "no_warnings": True,
        "skip_download": True, "noplaylist": True,
        "ignoreerrors": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        if not info.get("formats"):
            return None
        return info
    except Exception as exc:
        log.debug("[yt-dlp] %s", exc)
        return None


def _parse_ytdlp(info: dict, url: str) -> dict:
    """Parse yt-dlp info into the standard session format."""
    formats   = info.get("formats") or []
    subtitles = {**( info.get("subtitles") or {}), **(info.get("automatic_captions") or {})}

    # Group video formats by quality bucket
    groups: dict[str, list] = {b: [] for b in _QUALITY_ORDER}
    seen: set = set()

    for f in reversed(formats):
        fid    = f.get("format_id", "")
        vcodec = f.get("vcodec", "none") or "none"
        acodec = f.get("acodec", "none") or "none"
        h      = int(f.get("height") or 0)
        w      = int(f.get("width")  or 0)
        fps    = float(f.get("fps") or 0)
        tbr    = float(f.get("tbr") or 0)
        abr    = float(f.get("abr") or 0)
        fsz    = int(f.get("filesize") or f.get("filesize_approx") or 0)
        lang   = f.get("language") or ""

        is_audio_only = vcodec == "none"
        has_audio     = acodec != "none"

        if is_audio_only:
            ac_s  = acodec.split(".")[0].upper()
            br_s  = f"{int(abr)}kbps" if abr else (f"{int(tbr)}kbps" if tbr else "")
            dedup = f"audio_{ac_s}_{br_s}"
            if dedup in seen:
                continue
            seen.add(dedup)
            label  = f"🎵 {ac_s} {br_s}"
            groups["Audio"].append({
                "id": fid, "label": label,
                "abr": int(abr or tbr), "sz": fsz,
                "lang": lang, "ext": f.get("ext", "m4a"),
                "source": "ytdlp",
            })
            continue

        bucket  = _quality_bucket(h, w)
        vc_s    = vcodec.split(".")[0].upper()
        fps_s   = f"{int(fps)}fps" if fps and fps not in (24, 25, 30) else ""
        sz_s    = _sz(fsz) if fsz else ""
        audio_s = "" if has_audio else " 🔇"
        res_s   = f"{h}p" if h else "?"
        icon    = _QUALITY_ICON.get(bucket, "📦")
        dedup   = f"{bucket}_{vc_s}_{has_audio}_{fps_s}"
        if dedup in seen:
            continue
        seen.add(dedup)
        label = f"{icon} {res_s}{fps_s} {vc_s}{audio_s}"
        if sz_s:
            label += f" [{sz_s}]"
        groups[bucket].append({
            "id": fid, "label": label,
            "h": h, "fps": fps, "sz": fsz,
            "lang": lang, "ext": f.get("ext", "mp4"),
            "has_audio": has_audio,
            "source": "ytdlp",
        })

    # Flatten non-empty groups into a simple video list for our session format
    videos = []
    for b in _QUALITY_ORDER:
        for v in groups[b]:
            if not v.get("source") == "ytdlp":
                continue
            videos.append(v)

    # Audio-only tracks
    audios = groups["Audio"]

    # Subtitles
    subs = []
    for lang_code, tracks in subtitles.items():
        best = next((t for t in tracks if t.get("ext") in ("vtt", "srt")), tracks[0] if tracks else None)
        if not best:
            continue
        flag  = _flag(lang_code)
        lname = _lang_name(lang_code)
        subs.append({
            "lang": lang_code,
            "label": f"{flag}  {lname}  [{best.get('ext', '?')}]",
            "url": best.get("url", ""),
            "ext": best.get("ext", "srt"),
        })
    subs.sort(key=lambda x: x["lang"])

    return {
        "url":    url,
        "title":  (info.get("title") or "Unknown")[:60],
        "video":  videos,
        "audio":  audios,
        "subs":   subs,
        "source": "ytdlp",
        "duration": float(info.get("duration") or 0),
    }


# ─────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────

async def analyse(url: str, chat_id: int) -> dict | None:
    """
    Try ffprobe first (fast, works on any direct HTTP link),
    then yt-dlp for streaming platforms.
    """
    loop = asyncio.get_event_loop()

    # 1. ffprobe — direct links, seedr, DDL, local files
    raw = await loop.run_in_executor(_pool, _ffprobe_sync, url)
    if raw:
        session = _parse_ffprobe(raw, url)
        if session["video"] or session["audio"] or session["subs"]:
            _sessions[chat_id] = session
            return session

    # 2. yt-dlp — YouTube, streaming platforms
    info = await loop.run_in_executor(_pool, _ytdlp_sync, url)
    if info:
        session = _parse_ytdlp(info, url)
        if session["video"] or session["audio"] or session["subs"]:
            _sessions[chat_id] = session
            return session

    return None


def get_session(chat_id: int) -> dict | None:
    return _sessions.get(chat_id)


def clear_session(chat_id: int) -> None:
    _sessions.pop(chat_id, None)


# ─────────────────────────────────────────────────────────────
# Keyboards
# ─────────────────────────────────────────────────────────────

def kb_type(v: int, a: int, s: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎬 Vidéo  ({v})",       callback_data="sx_video"),
         InlineKeyboardButton(f"🎵 Audio  ({a})",       callback_data="sx_audio")],
        [InlineKeyboardButton(f"💬 Sous-titres  ({s})", callback_data="sx_subs")],
        [InlineKeyboardButton("⏎ Retour",               callback_data="sx_back")],
    ])


def kb_video(session: dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(v["label"][:58], callback_data=f"sx_dl_video_{i}")]
        for i, v in enumerate(session["video"])
    ]
    rows.append([InlineKeyboardButton("⏎ Retour", callback_data="sx_type")])
    return InlineKeyboardMarkup(rows)


def kb_audio(session: dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(a["label"][:58], callback_data=f"sx_dl_audio_{i}")]
        for i, a in enumerate(session["audio"])
    ]
    rows.append([InlineKeyboardButton("⏎ Retour", callback_data="sx_type")])
    return InlineKeyboardMarkup(rows)


def kb_subs(session: dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(s["label"][:58], callback_data=f"sx_dl_sub_{i}")]
        for i, s in enumerate(session["subs"])
    ]
    rows.append([InlineKeyboardButton("⏎ Retour", callback_data="sx_type")])
    return InlineKeyboardMarkup(rows)


# ─────────────────────────────────────────────────────────────
# Download helpers
# ─────────────────────────────────────────────────────────────

def _dl_ytdlp(url: str, fmt_id: str, out_dir: str) -> str:
    opts = {
        "quiet": True, "no_warnings": True,
        "format": fmt_id,
        "outtmpl": f"{out_dir}/%(title)s.%(ext)s",
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url)
        return ydl.prepare_filename(info)


def _dl_ffmpeg(url: str, stream_map: str, out_file: str) -> str:
    cmd = [
        "ffmpeg", "-y",
        "-allowed_extensions", "ALL",
        "-i", url,
        "-map", stream_map,
        "-c", "copy",
        out_file,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode()[-400:])
    return out_file


def _dl_sub_url(sub_url: str, out_dir: str, lang: str, ext: str) -> str:
    dest = os.path.join(out_dir, f"subtitle_{lang}.{ext}")
    urllib.request.urlretrieve(sub_url, dest)
    return dest


async def dl_video(session: dict, idx: int, out_dir: str) -> str:
    v    = session["video"][idx]
    loop = asyncio.get_event_loop()
    if session["source"] == "ytdlp":
        return await loop.run_in_executor(_pool, _dl_ytdlp, session["url"], v["id"], out_dir)
    fname = os.path.join(out_dir, f"video_stream_{idx}.{v['ext']}")
    return await loop.run_in_executor(_pool, _dl_ffmpeg, session["url"], v["map"], fname)


async def dl_audio(session: dict, idx: int, out_dir: str) -> str:
    a    = session["audio"][idx]
    loop = asyncio.get_event_loop()
    if session["source"] == "ytdlp":
        return await loop.run_in_executor(_pool, _dl_ytdlp, session["url"], a["id"], out_dir)
    fname = os.path.join(out_dir, f"audio_stream_{idx}.{a['ext']}")
    return await loop.run_in_executor(_pool, _dl_ffmpeg, session["url"], a["map"], fname)


async def dl_sub(session: dict, idx: int, out_dir: str) -> str:
    s    = session["subs"][idx]
    loop = asyncio.get_event_loop()
    if s.get("url"):
        return await loop.run_in_executor(_pool, _dl_sub_url, s["url"], out_dir, s["lang"], s["ext"])
    fname = os.path.join(out_dir, f"subtitle_{s['lang']}_{idx}.{s['ext']}")
    return await loop.run_in_executor(_pool, _dl_ffmpeg, session["url"], s["map"], fname)
