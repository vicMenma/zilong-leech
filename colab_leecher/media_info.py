"""
colab_leecher/media_info.py
────────────────────────────────────────────────────────────────
MediaInfo helpers for zilong-leech.

Provides:
  - get_mediainfo(path)  : runs `mediainfo` CLI or falls back to ffprobe
  - post_to_telegraph(filename, text) : posts the report to Telegra.ph
                                        and returns the public URL
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Telegra.ph token store (in-memory + simple file cache)
# ─────────────────────────────────────────────────────────────

_TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".telegraph_token")
_TELEGRAPH_BASE = "https://api.telegra.ph"
_token: str = ""


async def _get_telegraph_token() -> str:
    global _token
    if _token:
        return _token
    # Try reading from cache file
    try:
        with open(_TOKEN_FILE) as f:
            _token = f.read().strip()
        if _token:
            return _token
    except FileNotFoundError:
        pass
    # Create a new account
    async with aiohttp.ClientSession() as sess:
        async with sess.post(f"{_TELEGRAPH_BASE}/createAccount", json={
            "short_name":  "ZilongLeech",
            "author_name": "Zilong MediaInfo",
        }) as r:
            data = await r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegraph createAccount failed: {data}")
    _token = data["result"]["access_token"]
    try:
        with open(_TOKEN_FILE, "w") as f:
            f.write(_token)
    except Exception:
        pass
    return _token


async def post_to_telegraph(filename: str, text: str) -> str:
    """
    Post a MediaInfo text report to Telegra.ph.
    Returns the public page URL (https://telegra.ph/...).
    """
    token = await _get_telegraph_token()
    title = f"MediaInfo — {filename[:55]}"

    # Strip local path prefixes for privacy
    clean = re.sub(r"(Complete name\s*:\s*)/[^\n]*/", r"\1", text)
    clean = re.sub(r"/(?:tmp|content|home)/[^\s]*/([^\s/\n]+)", r"\1", clean)
    if len(clean) > 60_000:
        clean = clean[:60_000] + "\n\n...(truncated)"

    _SECTION_RE = re.compile(r"^[A-Z][a-zA-Z\s#0-9]+$")
    nodes: list = [
        {"tag": "p", "children": [{"tag": "em", "children": [filename]}]},
    ]
    for line in clean.splitlines():
        stripped = line.rstrip()
        if not stripped:
            nodes.append({"tag": "p", "children": [{"tag": "br", "children": []}]})
            continue
        if _SECTION_RE.match(stripped) and len(stripped) < 40 and ":" not in stripped:
            nodes.append({"tag": "p", "children": [
                {"tag": "strong", "children": [stripped]}
            ]})
            continue
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            children: list = [key.rstrip() + " : "]
            if val.strip():
                children.append({"tag": "code", "children": [val.strip()]})
            nodes.append({"tag": "p", "children": children})
        else:
            nodes.append({"tag": "p", "children": [
                {"tag": "code", "children": [stripped]}
            ]})

    async with aiohttp.ClientSession() as sess:
        async with sess.post(f"{_TELEGRAPH_BASE}/createPage", json={
            "access_token":   token,
            "title":          title,
            "author_name":    "Zilong Leech",
            "content":        nodes,
            "return_content": False,
        }) as r:
            data = await r.json()

    if data.get("ok"):
        return "https://telegra.ph/" + data["result"]["path"]
    raise RuntimeError(f"Telegraph createPage failed: {data.get('error', 'unknown')}")


# ─────────────────────────────────────────────────────────────
# MediaInfo extraction
# ─────────────────────────────────────────────────────────────

def _human_size(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024.0:
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} PiB"


def _fmt_hms(secs: float) -> str:
    s = int(max(0, secs))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def _ffprobe_mediainfo_text(path: str) -> str:
    """Fallback when `mediainfo` CLI is not installed."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0 or not out.strip():
            return "MediaInfo unavailable"
        data = json.loads(out.decode(errors="replace"))
    except Exception as exc:
        log.warning("ffprobe mediainfo fallback error: %s", exc)
        return "MediaInfo unavailable"

    lines = ["General"]
    fmt = data.get("format", {})
    lines.append(f"Complete name  : {os.path.basename(path)}")
    lines.append(f"Format         : {fmt.get('format_long_name', fmt.get('format_name', '?'))}")

    dur_sec = 0.0
    try:
        dur_sec = float(fmt.get("duration") or 0)
    except Exception:
        pass
    if dur_sec:
        lines.append(f"Duration       : {_fmt_hms(dur_sec)}")
    if fmt.get("bit_rate"):
        try:
            lines.append(f"Overall bit rate : {int(fmt['bit_rate']) // 1000} kb/s")
        except Exception:
            pass
    if fmt.get("size"):
        try:
            lines.append(f"File size      : {_human_size(int(fmt['size']))}")
        except Exception:
            pass

    for s in data.get("streams", []):
        stype = s.get("codec_type", "?")
        idx   = s.get("index", "?")
        lines.append("")
        if stype == "video":
            lines.append(f"Video #{idx}")
            lines.append(f"Format  : {s.get('codec_long_name', s.get('codec_name', '?'))}")
            w, h = s.get("width", 0), s.get("height", 0)
            if w and h:
                lines.append(f"Size    : {w}x{h}")
            fr = s.get("r_frame_rate", "0/1")
            try:
                fn2, fd2 = fr.split("/")
                fps = float(fn2) / max(float(fd2), 1)
                lines.append(f"Frame rate : {fps:.3f} FPS")
            except Exception:
                pass
            if s.get("bit_rate"):
                try:
                    lines.append(f"Bit rate : {int(s['bit_rate']) // 1000} kb/s")
                except Exception:
                    pass
            pix = s.get("pix_fmt", "")
            if pix:
                lines.append(f"Pixel format : {pix}")
        elif stype == "audio":
            lines.append(f"Audio #{idx}")
            lines.append(f"Format     : {s.get('codec_long_name', s.get('codec_name', '?'))}")
            tags = s.get("tags") or {}
            lang = tags.get("language", "")
            if lang:
                lines.append(f"Language   : {lang}")
            ch = s.get("channels", 0)
            if ch:
                ch_s = {1: "1 channel (Mono)", 2: "2 channels (Stereo)",
                        6: "6 channels (5.1)", 8: "8 channels (7.1)"}.get(ch, f"{ch} channels")
                lines.append(f"Channels   : {ch_s}")
            if s.get("sample_rate"):
                lines.append(f"Sample rate : {s['sample_rate']} Hz")
            if s.get("bit_rate"):
                try:
                    lines.append(f"Bit rate   : {int(s['bit_rate']) // 1000} kb/s")
                except Exception:
                    pass
        elif stype == "subtitle":
            lines.append(f"Text #{idx}")
            lines.append(f"Format : {s.get('codec_long_name', s.get('codec_name', '?'))}")
            tags = s.get("tags") or {}
            lang = tags.get("language", "")
            if lang:
                lines.append(f"Language : {lang}")

    return "\n".join(lines)


async def get_mediainfo(path: str) -> str:
    """
    Run `mediainfo` CLI on a file.
    Falls back to an ffprobe-based report if mediainfo is not installed.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "mediainfo", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        if proc.returncode == 0:
            txt = out.decode(errors="replace").strip()
            if len(txt) > 80:
                return txt[:8000]
    except FileNotFoundError:
        log.info("mediainfo CLI not found — using ffprobe fallback")
    except Exception as exc:
        log.warning("mediainfo error: %s", exc)

    return await _ffprobe_mediainfo_text(path)


# ─────────────────────────────────────────────────────────────
# Quick inline summary (used in chat without Telegraph)
# ─────────────────────────────────────────────────────────────

async def get_inline_summary(path: str) -> str:
    """
    Return a short HTML-formatted summary of the file's streams,
    suitable for inline display in a Telegram message.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0 or not out.strip():
            return "❌ <i>ffprobe failed — cannot read file.</i>"
        data = json.loads(out.decode(errors="replace"))
    except Exception as exc:
        return f"❌ <i>Error: {exc}</i>"

    fmt      = data.get("format", {})
    streams  = data.get("streams", [])
    dur_sec  = 0.0
    try:
        dur_sec = float(fmt.get("duration") or 0)
    except Exception:
        pass

    fname    = os.path.basename(path)
    fsize    = int(fmt.get("size") or 0)
    dur_s    = _fmt_hms(dur_sec) if dur_sec else "?"
    size_s   = _human_size(fsize) if fsize else "?"

    lines = [
        "📊 <b>Media Info</b>",
        f"📄 <code>{fname[:50]}</code>",
        f"💾 <code>{size_s}</code>  ⏱ <code>{dur_s}</code>",
        "──────────────────────",
    ]

    _FLAG_MAP = {
        "eng": "🇬🇧", "en": "🇬🇧", "fra": "🇫🇷", "fr": "🇫🇷",
        "jpn": "🇯🇵", "ja": "🇯🇵", "deu": "🇩🇪", "de": "🇩🇪",
        "spa": "🇪🇸", "es": "🇪🇸", "por": "🇵🇹", "pt": "🇵🇹",
        "ita": "🇮🇹", "it": "🇮🇹", "rus": "🇷🇺", "ru": "🇷🇺",
        "chi": "🇨🇳", "zho": "🇨🇳", "zh": "🇨🇳", "kor": "🇰🇷", "ko": "🇰🇷",
        "ara": "🇸🇦", "ar": "🇸🇦", "hin": "🇮🇳", "hi": "🇮🇳",
        "tur": "🇹🇷", "tr": "🇹🇷", "und": "🌐",
    }

    def _fl(lang: str) -> str:
        return _FLAG_MAP.get(lang.lower() if lang else "und", "🌐")

    vid_count = aud_count = sub_count = 0
    for s in streams:
        ct    = s.get("codec_type", "")
        codec = (s.get("codec_name") or "?").upper()
        tags  = s.get("tags") or {}
        lang  = (tags.get("language") or "und").lower()

        if ct == "video":
            vid_count += 1
            w, h = s.get("width", 0), s.get("height", 0)
            fr = s.get("r_frame_rate", "0/1")
            try:
                fn2, fd2 = fr.split("/")
                fps = f"{float(fn2) / max(float(fd2), 1):.2f}fps"
            except Exception:
                fps = ""
            pix = s.get("pix_fmt", "")
            hdr = " HDR" if "10" in pix else ""
            br  = s.get("bit_rate", "")
            br_s = f"  {int(int(br)) // 1000}kbps" if br and str(br).isdigit() else ""
            lines.append(f"🎬 <code>{codec}  {w}x{h}  {fps}{hdr}{br_s}</code>")

        elif ct == "audio":
            aud_count += 1
            ch  = s.get("channels", 0)
            ch_s = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}.get(ch, f"{ch}ch") if ch else ""
            sr  = s.get("sample_rate", "")
            sr_s = f"  {int(sr) // 1000}kHz" if sr else ""
            br  = s.get("bit_rate", "")
            br_s = f"  {int(int(br)) // 1000}kbps" if br and str(br).isdigit() else ""
            title_tag = tags.get("title", "")
            title_s   = f" — {title_tag}" if title_tag else ""
            lines.append(f"{_fl(lang)} <code>{codec}  {ch_s}{sr_s}{br_s}</code>{title_s}")

        elif ct == "subtitle":
            sub_count += 1
            title_tag = tags.get("title", "")
            title_s   = f" ({title_tag})" if title_tag else ""
            forced    = " ⚡" if tags.get("forced") else ""
            lines.append(f"{_fl(lang)} <code>{codec}</code>{title_s}{forced}")

    if vid_count == 0 and aud_count == 0 and sub_count == 0:
        lines.append("⚠️ <i>No media streams detected.</i>")

    return "\n".join(lines)
