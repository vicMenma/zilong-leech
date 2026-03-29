"""
colab_leecher/stream_extractor.py
Stream Extractor — two-source strategy adapted from zilong_multiusage.

Strategy:
  1. ffprobe directly on the URL — fastest for direct HTTP links (DDL, seedr)
  2. yt-dlp — for YouTube, streaming platforms
  ffprobe wins on direct links; yt-dlp wins on platforms.

Public API used by __main__.py:
  analyse(url, chat_id)       → session dict or None
  get_session(chat_id)        → session dict or None
  clear_session(chat_id)
  kb_type(v, a, s)            → InlineKeyboardMarkup
  kb_video(session)           → InlineKeyboardMarkup
  kb_audio(session)           → InlineKeyboardMarkup
  kb_subs(session)            → InlineKeyboardMarkup
  dl_video(session, idx, dir) → path
  dl_audio(session, idx, dir) → path
  dl_sub(session,   idx, dir) → path

Session format:
  {
    "url":      str,
    "title":    str,
    "source":   "ffprobe" | "ytdlp",
    "duration": float,
    "video": [{"label", "map"/"id", "ext", "lang", "h", "fps", "sz"}],
    "audio": [{"label", "map"/"id", "ext", "lang", "abr", "sz"}],
    "subs":  [{"label", "map"/"id"/"url", "ext", "lang"}],
  }
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import urllib.request as _urlreq
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import yt_dlp
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

# ── Per-chat session store ────────────────────────────────────
_sessions: dict[int, dict] = {}
_pool = ThreadPoolExecutor(max_workers=3)

# ─────────────────────────────────────────────────────────────
# Language / flag helpers
# ─────────────────────────────────────────────────────────────

_FLAGS: dict[str, str] = {
    "en": "🇬🇧", "eng": "🇬🇧",
    "fr": "🇫🇷", "fra": "🇫🇷", "fre": "🇫🇷",
    "de": "🇩🇪", "deu": "🇩🇪", "ger": "🇩🇪",
    "es": "🇪🇸", "spa": "🇪🇸",
    "pt": "🇧🇷", "por": "🇧🇷",
    "it": "🇮🇹", "ita": "🇮🇹",
    "ja": "🇯🇵", "jpn": "🇯🇵",
    "ko": "🇰🇷", "kor": "🇰🇷",
    "zh": "🇨🇳", "chi": "🇨🇳", "zho": "🇨🇳",
    "ru": "🇷🇺", "rus": "🇷🇺",
    "ar": "🇸🇦", "ara": "🇸🇦",
    "hi": "🇮🇳", "hin": "🇮🇳",
    "tr": "🇹🇷", "tur": "🇹🇷",
    "nl": "🇳🇱", "nld": "🇳🇱",
    "pl": "🇵🇱", "pol": "🇵🇱",
    "sv": "🇸🇪", "swe": "🇸🇪",
    "da": "🇩🇰", "dan": "🇩🇰",
    "fi": "🇫🇮", "fin": "🇫🇮",
    "cs": "🇨🇿", "ces": "🇨🇿",
    "uk": "🇺🇦", "ukr": "🇺🇦",
    "ro": "🇷🇴", "ron": "🇷🇴",
    "hu": "🇭🇺", "hun": "🇭🇺",
    "id": "🇮🇩", "ind": "🇮🇩",
    "th": "🇹🇭", "tha": "🇹🇭",
    "vi": "🇻🇳", "vie": "🇻🇳",
    "und": "🌐",
}

_LANG_NAMES: dict[str, str] = {
    "en": "English", "eng": "English",
    "fr": "French",  "fra": "French",  "fre": "French",
    "de": "German",  "deu": "German",  "ger": "German",
    "es": "Spanish", "spa": "Spanish",
    "pt": "Portuguese", "por": "Portuguese",
    "it": "Italian", "ita": "Italian",
    "ja": "Japanese", "jpn": "Japanese",
    "ko": "Korean",  "kor": "Korean",
    "zh": "Chinese", "chi": "Chinese", "zho": "Chinese",
    "ru": "Russian", "rus": "Russian",
    "ar": "Arabic",  "ara": "Arabic",
    "hi": "Hindi",   "hin": "Hindi",
    "tr": "Turkish", "tur": "Turkish",
    "nl": "Dutch",   "nld": "Dutch",
    "pl": "Polish",  "pol": "Polish",
    "sv": "Swedish", "swe": "Swedish",
    "da": "Danish",  "dan": "Danish",
    "fi": "Finnish", "fin": "Finnish",
    "cs": "Czech",   "ces": "Czech",
    "uk": "Ukrainian", "ukr": "Ukrainian",
    "ro": "Romanian",  "ron": "Romanian",
    "hu": "Hungarian", "hun": "Hungarian",
    "id": "Indonesian","ind": "Indonesian",
    "th": "Thai",    "tha": "Thai",
    "vi": "Vietnamese","vie": "Vietnamese",
    "und": "Unknown",
}


def _flag(code: str) -> str:
    if not code:
        return "🌐"
    key = code.split("-")[0].lower()
    return _FLAGS.get(key, "🌐")


def _lname(code: str) -> str:
    if not code:
        return "Unknown"
    key = code.split("-")[0].lower()
    return _LANG_NAMES.get(key, key.upper())


def _sz(b) -> str:
    if not b or b <= 0:
        return ""
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
# Source 1 — ffprobe on URL (direct links, DDL, seedr)
# ─────────────────────────────────────────────────────────────

async def _ffprobe_url(url: str) -> Optional[dict]:
    """Run ffprobe async on a URL. Returns JSON dict or None."""
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
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0 or not out.strip():
            return None
        data = json.loads(out.decode(errors="replace"))
        streams = data.get("streams", [])
        if any(s.get("codec_type") in ("video", "audio", "subtitle") for s in streams):
            return data
        return None
    except Exception as exc:
        log.debug("[SX ffprobe] %s : %s", url[:60], exc)
        return None


def _parse_ffprobe(info: dict, url: str) -> dict:
    """Convert raw ffprobe JSON into the unified session dict."""
    streams  = info.get("streams", [])
    fmt      = info.get("format", {})
    duration = float(fmt.get("duration") or 0)
    title    = (
        (fmt.get("tags") or {}).get("title")
        or url.split("/")[-1].split("?")[0][:60]
        or "Media"
    )

    videos, audios, subs = [], [], []

    for s in streams:
        ctype  = s.get("codec_type", "")
        codec  = s.get("codec_name", "?")
        idx    = s.get("index", 0)
        tags   = s.get("tags") or {}
        lang   = (tags.get("language") or "und").lower()
        title_tag = tags.get("title", "")

        if ctype == "video":
            w   = s.get("width") or 0
            h   = s.get("height") or 0
            fr  = s.get("r_frame_rate", "0/1")
            try:
                fn, fd = fr.split("/")
                fps = round(float(fn) / max(float(fd), 1))
            except Exception:
                fps = 0
            br  = int(s.get("bit_rate") or 0)
            sz  = int(br * duration / 8) if br and duration else 0
            res = f"{h}p" if h else f"{w}×{h}"
            fps_s = f" {fps}fps" if fps > 30 else ""
            pix   = s.get("pix_fmt", "")
            hdr_s = " HDR" if "10" in pix else ""
            br_s  = f"  {br//1000}kbps" if br else ""
            label = f"🎬 {res}{fps_s}{hdr_s}  [{codec.upper()}]{br_s}"
            if sz: label += f"  ~{_sz(sz)}"
            if title_tag: label += f"  {title_tag}"
            videos.append({
                "idx": idx, "label": label,
                "map": f"0:{idx}", "ext": "mkv",
                "lang": lang, "h": h, "fps": fps, "sz": sz,
                "source": "ffprobe",
            })

        elif ctype == "audio":
            ch  = s.get("channels") or 0
            br  = int(s.get("bit_rate") or 0)
            sz  = int(br * duration / 8) if br and duration else 0
            ch_s = {1:"Mono",2:"Stereo",6:"5.1",8:"7.1"}.get(ch, f"{ch}ch") if ch else ""
            br_s = f"  {br//1000}kbps" if br else ""
            ext  = _audio_ext(codec)
            label = f"{_flag(lang)} {_lname(lang)}  [{codec.upper()}]  {ch_s}{br_s}"
            if title_tag: label += f"  {title_tag}"
            audios.append({
                "idx": idx, "label": label.strip(),
                "map": f"0:{idx}", "ext": ext,
                "lang": lang, "abr": br//1000 if br else 0, "sz": sz,
                "source": "ffprobe",
            })

        elif ctype == "subtitle":
            ext   = _sub_ext(codec)
            forced = " ⚡" if tags.get("forced") else ""
            label  = f"{_flag(lang)} {_lname(lang)}  [{codec.upper()}]{forced}"
            if title_tag: label += f"  ({title_tag})"
            subs.append({
                "idx": idx, "label": label,
                "map": f"0:{idx}", "ext": ext,
                "lang": lang, "url": None,
                "source": "ffprobe",
            })

    return {
        "url": url, "title": title,
        "video": videos, "audio": audios, "subs": subs,
        "source": "ffprobe", "duration": duration,
    }


def _sub_ext(codec: str) -> str:
    return {
        "subrip": "srt", "ass": "ass", "ssa": "ass",
        "webvtt": "vtt", "mov_text": "srt", "dvd_subtitle": "sub",
        "hdmv_pgs_subtitle": "sup", "text": "srt",
    }.get(codec.lower(), "srt")


def _audio_ext(codec: str) -> str:
    return {
        "aac": "aac", "mp3": "mp3", "ac3": "ac3", "eac3": "eac3",
        "dts": "dts", "flac": "flac", "vorbis": "ogg", "opus": "opus",
        "truehd": "thd", "pcm_s16le": "wav", "pcm_s24le": "wav",
    }.get(codec.lower(), "mka")


# ─────────────────────────────────────────────────────────────
# Source 2 — yt-dlp (YouTube, streaming platforms)
# ─────────────────────────────────────────────────────────────

_QUALITY_ORDER = ["4K","1440p","1080p","720p","480p","360p","240p","144p"]
_QUALITY_ICON  = {
    "4K":"🔷","1440p":"🟣","1080p":"🔵","720p":"🟢",
    "480p":"🟡","360p":"🟠","240p":"🔴","144p":"⚫",
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
    return "144p"


def _ytdlp_sync(url: str) -> Optional[dict]:
    opts = {
        "quiet": True, "no_warnings": True,
        "skip_download": True, "noplaylist": True,
        "ignoreerrors": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info and info.get("formats"):
            return info
        return None
    except Exception as exc:
        log.debug("[SX ytdlp] %s : %s", url[:60], exc)
        return None


def _parse_ytdlp(info: dict, url: str) -> dict:
    """Parse yt-dlp format list into the unified session dict.

    Key fix vs old version: audio-only formats go ONLY into audios list,
    never into videos. Quality buckets are properly deduplicated.
    """
    formats   = info.get("formats") or []
    subtitles = {**(info.get("subtitles") or {}), **(info.get("automatic_captions") or {})}

    # ── Video formats grouped by quality bucket ───────────────
    groups: dict[str, list] = {b: [] for b in _QUALITY_ORDER}
    audio_fmts: list = []
    seen_video: set  = set()
    seen_audio: set  = set()

    for f in reversed(formats):
        fid    = f.get("format_id", "")
        vcodec = (f.get("vcodec") or "none")
        acodec = (f.get("acodec") or "none")
        h      = int(f.get("height") or 0)
        w      = int(f.get("width")  or 0)
        fps    = float(f.get("fps") or 0)
        tbr    = float(f.get("tbr") or 0)
        abr    = float(f.get("abr") or 0)
        fsz    = int(f.get("filesize") or f.get("filesize_approx") or 0)
        ext    = f.get("ext", "mp4")
        lang   = (f.get("language") or "").lower()

        is_audio_only = (vcodec == "none")
        has_audio     = (acodec != "none")

        # ── Audio-only ────────────────────────────────────────
        if is_audio_only:
            ac    = acodec.split(".")[0].upper()
            br_s  = f"{int(abr)}kbps" if abr else (f"{int(tbr)}kbps" if tbr else "?")
            dedup = f"aud_{ac}_{br_s}"
            if dedup in seen_audio:
                continue
            seen_audio.add(dedup)
            label = f"🎵 {ac}  {br_s}"
            if fsz: label += f"  {_sz(fsz)}"
            audio_fmts.append({
                "id": fid, "label": label,
                "abr": int(abr or tbr), "sz": fsz,
                "lang": lang, "ext": ext,
                "source": "ytdlp",
            })
            continue

        # ── Video ─────────────────────────────────────────────
        if not h:
            continue
        bucket  = _quality_bucket(h, w)
        vc      = vcodec.split(".")[0].upper()
        fps_s   = f" {int(fps)}fps" if fps and fps not in (24, 25, 30) else ""
        hdr_s   = " HDR" if "hdr" in (f.get("dynamic_range") or "").lower() else ""
        no_aud  = "" if has_audio else " 🔇"
        sz_s    = f"  {_sz(fsz)}" if fsz else ""
        dedup   = f"vid_{bucket}_{vc}_{has_audio}_{fps_s}"
        if dedup in seen_video:
            continue
        seen_video.add(dedup)
        icon  = _QUALITY_ICON.get(bucket, "📦")
        label = f"{icon} {h}p{fps_s}{hdr_s}{no_aud}  [{vc}]{sz_s}"
        groups[bucket].append({
            "id": fid, "label": label,
            "h": h, "fps": fps, "sz": fsz,
            "lang": lang, "ext": ext,
            "has_audio": has_audio,
            "source": "ytdlp",
        })

    # Flatten video groups in quality order
    videos = [v for b in _QUALITY_ORDER for v in groups[b]]

    # ── Subtitles ─────────────────────────────────────────────
    subs = []
    for lang_code, tracks in subtitles.items():
        # Prefer vtt > srt > anything
        best = (
            next((t for t in tracks if t.get("ext") == "vtt"), None)
            or next((t for t in tracks if t.get("ext") == "srt"), None)
            or (tracks[0] if tracks else None)
        )
        if not best or not best.get("url"):
            continue
        sub_ext = best.get("ext", "vtt")
        is_auto = any(
            lang_code in (info.get("automatic_captions") or {})
            for _ in [None]
        )
        auto_s = " (auto)" if is_auto else ""
        label  = f"{_flag(lang_code)} {_lname(lang_code)}  [{sub_ext}]{auto_s}"
        subs.append({
            "lang": lang_code, "label": label,
            "url": best["url"], "ext": sub_ext,
            "source": "ytdlp",
        })
    subs.sort(key=lambda x: x["lang"])

    return {
        "url":    url,
        "title":  (info.get("title") or "")[:60],
        "video":  videos,
        "audio":  audio_fmts,
        "subs":   subs,
        "source": "ytdlp",
        "duration": float(info.get("duration") or 0),
    }


# ─────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────

async def analyse(url: str, chat_id: int) -> Optional[dict]:
    """
    Try ffprobe first (fast, works on any direct HTTP link),
    then fall back to yt-dlp for streaming platforms.
    """
    loop = asyncio.get_event_loop()

    # 1. ffprobe — direct links, DDL, seedr
    raw = await _ffprobe_url(url)
    if raw:
        session = _parse_ffprobe(raw, url)
        if session["video"] or session["audio"] or session["subs"]:
            log.info("[SX] ffprobe found %dV/%dA/%dS for %s",
                     len(session["video"]), len(session["audio"]),
                     len(session["subs"]), url[:60])
            _sessions[chat_id] = session
            return session

    # 2. yt-dlp — YouTube and streaming platforms
    info = await loop.run_in_executor(_pool, _ytdlp_sync, url)
    if info:
        session = _parse_ytdlp(info, url)
        if session["video"] or session["audio"] or session["subs"]:
            log.info("[SX] yt-dlp found %dV/%dA/%dS for %s",
                     len(session["video"]), len(session["audio"]),
                     len(session["subs"]), url[:60])
            _sessions[chat_id] = session
            return session

    log.warning("[SX] No streams found for %s", url[:60])
    return None


def get_session(chat_id: int) -> Optional[dict]:
    return _sessions.get(chat_id)


def clear_session(chat_id: int) -> None:
    _sessions.pop(chat_id, None)


# ─────────────────────────────────────────────────────────────
# Keyboards — identical callback_data format that __main__ expects
# ─────────────────────────────────────────────────────────────

def kb_type(v: int, a: int, s: int) -> InlineKeyboardMarkup:
    # Video and Audio side by side on the same row (compact, matches multiusage)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎬 Vidéo  ({v})",       callback_data="sx_video"),
         InlineKeyboardButton(f"🎵 Audio  ({a})",       callback_data="sx_audio")],
        [InlineKeyboardButton(f"💬 Sous-titres  ({s})", callback_data="sx_subs")],
        [InlineKeyboardButton("⏎ Retour",               callback_data="sx_back")],
    ])


def kb_video(session: dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(v["label"][:56], callback_data=f"sx_dl_video_{i}")]
        for i, v in enumerate(session["video"])
    ]
    rows.append([InlineKeyboardButton("⏎ Retour", callback_data="sx_type")])
    return InlineKeyboardMarkup(rows)


def kb_audio(session: dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(a["label"][:56], callback_data=f"sx_dl_audio_{i}")]
        for i, a in enumerate(session["audio"])
    ]
    rows.append([InlineKeyboardButton("⏎ Retour", callback_data="sx_type")])
    return InlineKeyboardMarkup(rows)


def kb_subs(session: dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(s["label"][:56], callback_data=f"sx_dl_sub_{i}")]
        for i, s in enumerate(session["subs"])
    ]
    rows.append([InlineKeyboardButton("⏎ Retour", callback_data="sx_type")])
    return InlineKeyboardMarkup(rows)


# ─────────────────────────────────────────────────────────────
# Download helpers
# ─────────────────────────────────────────────────────────────

def _ffmpeg_extract(url: str, stream_map: str, out_path: str) -> str:
    """Extract one stream from `url` via ffmpeg (synchronous, run in executor)."""
    cmd = [
        "ffmpeg", "-y",
        "-allowed_extensions", "ALL",
        "-analyzeduration", "20000000",
        "-probesize", "50000000",
        "-i", url,
        "-map", stream_map,
        "-c", "copy",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace")[-500:])
    return out_path


def _ytdlp_download(url: str, fmt_id: str, out_dir: str, audio_only: bool = False) -> str:
    """Download a single yt-dlp format (synchronous, run in executor)."""
    opts: dict = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": fmt_id if fmt_id else ("bestaudio/best" if audio_only else "bestvideo+bestaudio/best"),
        "outtmpl": os.path.join(out_dir, "%(title).60s.%(ext)s"),
        "merge_output_format": "mkv",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info  = ydl.extract_info(url, download=True)
        fpath = ydl.prepare_filename(info)
    if not os.path.exists(fpath):
        # yt-dlp might have merged into .mkv
        base = os.path.splitext(fpath)[0]
        for ext in (".mkv", ".mp4", ".webm", ".mp3", ".m4a", ".ogg", ".opus"):
            if os.path.exists(base + ext):
                return base + ext
        # last resort: largest file in out_dir
        files = [os.path.join(out_dir, f) for f in os.listdir(out_dir)]
        if files:
            return max(files, key=os.path.getsize)
        raise FileNotFoundError(f"yt-dlp output not found: {fpath}")
    return fpath


def _fetch_sub_url(sub_url: str, out_dir: str, lang: str, ext: str) -> str:
    """Download a subtitle from a direct URL (synchronous, run in executor)."""
    dest = os.path.join(out_dir, f"subtitle_{lang}.{ext}")
    _urlreq.urlretrieve(sub_url, dest)
    return dest


async def dl_video(session: dict, idx: int, out_dir: str) -> str:
    v    = session["video"][idx]
    loop = asyncio.get_event_loop()
    os.makedirs(out_dir, exist_ok=True)

    if session["source"] == "ytdlp":
        return await loop.run_in_executor(
            _pool, _ytdlp_download, session["url"], v["id"], out_dir, False
        )
    # ffprobe source — use ffmpeg stream copy
    ext   = v.get("ext", "mkv")
    fname = os.path.join(out_dir, f"video_track_{idx}.{ext}")
    return await loop.run_in_executor(
        _pool, _ffmpeg_extract, session["url"], v["map"], fname
    )


async def dl_audio(session: dict, idx: int, out_dir: str) -> str:
    a    = session["audio"][idx]
    loop = asyncio.get_event_loop()
    os.makedirs(out_dir, exist_ok=True)

    if session["source"] == "ytdlp":
        return await loop.run_in_executor(
            _pool, _ytdlp_download, session["url"], a["id"], out_dir, True
        )
    ext   = a.get("ext", "mka")
    fname = os.path.join(out_dir, f"audio_track_{idx}.{ext}")
    return await loop.run_in_executor(
        _pool, _ffmpeg_extract, session["url"], a["map"], fname
    )


async def dl_sub(session: dict, idx: int, out_dir: str) -> str:
    s    = session["subs"][idx]
    loop = asyncio.get_event_loop()
    os.makedirs(out_dir, exist_ok=True)

    if s.get("url"):
        # yt-dlp subtitle: has a direct URL
        ext  = s.get("ext", "vtt")
        lang = s.get("lang", "und")
        return await loop.run_in_executor(
            _pool, _fetch_sub_url, s["url"], out_dir, lang, ext
        )
    # ffprobe subtitle: extract via ffmpeg
    ext   = s.get("ext", "srt")
    fname = os.path.join(out_dir, f"subtitle_{s.get('lang','und')}_{idx}.{ext}")
    return await loop.run_in_executor(
        _pool, _ffmpeg_extract, session["url"], s["map"], fname
    )
