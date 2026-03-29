"""
colab_leecher/media_info.py
MediaInfo helpers — improved version matching zilong_multiusage quality.

Functions:
  get_inline_summary(path) → short HTML for Telegram inline display
  get_mediainfo(path)      → full text report (mediainfo CLI or ffprobe fallback)
  post_to_telegraph(filename, text) → Telegra.ph URL
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import aiohttp

log = logging.getLogger(__name__)

# ── Telegra.ph token (persisted in data/ so it survives reboots) ─
_DATA_DIR   = os.path.join(os.path.dirname(__file__), "..", "data")
_TOKEN_FILE = os.path.join(_DATA_DIR, "telegraph.token")
_BASE       = "https://api.telegra.ph"
_token: str = ""

# ── Language flags ────────────────────────────────────────────
_FLAGS: dict[str, str] = {
    "eng":"🇬🇧","en":"🇬🇧","jpn":"🇯🇵","ja":"🇯🇵",
    "fra":"🇫🇷","fre":"🇫🇷","fr":"🇫🇷","deu":"🇩🇪","ger":"🇩🇪","de":"🇩🇪",
    "spa":"🇪🇸","es":"🇪🇸","por":"🇧🇷","pt":"🇧🇷","ita":"🇮🇹","it":"🇮🇹",
    "kor":"🇰🇷","ko":"🇰🇷","chi":"🇨🇳","zho":"🇨🇳","zh":"🇨🇳",
    "rus":"🇷🇺","ru":"🇷🇺","ara":"🇸🇦","ar":"🇸🇦","hin":"🇮🇳","hi":"🇮🇳",
    "tha":"🇹🇭","th":"🇹🇭","vie":"🇻🇳","vi":"🇻🇳","ind":"🇮🇩","id":"🇮🇩",
    "tur":"🇹🇷","tr":"🇹🇷","pol":"🇵🇱","pl":"🇵🇱","nld":"🇳🇱","nl":"🇳🇱",
    "swe":"🇸🇪","sv":"🇸🇪","dan":"🇩🇰","da":"🇩🇰","fin":"🇫🇮","fi":"🇫🇮",
    "ces":"🇨🇿","cze":"🇨🇿","ron":"🇷🇴","rum":"🇷🇴","hun":"🇭🇺","hu":"🇭🇺",
    "ukr":"🇺🇦","uk":"🇺🇦","bul":"🇧🇬","bg":"🇧🇬","und":"🌐",
}

_LANG_NAMES: dict[str, str] = {
    "eng":"English","en":"English","jpn":"Japanese","ja":"Japanese",
    "fra":"French","fre":"French","fr":"French","deu":"German","ger":"German","de":"German",
    "spa":"Spanish","es":"Spanish","por":"Portuguese","pt":"Portuguese",
    "ita":"Italian","it":"Italian","kor":"Korean","ko":"Korean",
    "chi":"Chinese","zho":"Chinese","zh":"Chinese","rus":"Russian","ru":"Russian",
    "ara":"Arabic","ar":"Arabic","hin":"Hindi","hi":"Hindi","tha":"Thai","th":"Thai",
    "vie":"Vietnamese","vi":"Vietnamese","ind":"Indonesian","id":"Indonesian",
    "tur":"Turkish","tr":"Turkish","pol":"Polish","pl":"Polish","nld":"Dutch","nl":"Dutch",
    "swe":"Swedish","sv":"Swedish","dan":"Danish","da":"Danish","fin":"Finnish","fi":"Finnish",
    "ces":"Czech","cze":"Czech","ron":"Romanian","rum":"Romanian",
    "hun":"Hungarian","hu":"Hungarian","ukr":"Ukrainian","uk":"Ukrainian",
    "bul":"Bulgarian","bg":"Bulgarian","und":"Unknown",
}


def _fl(lang: str) -> str:
    return _FLAGS.get((lang or "und").lower(), "🌐")


def _ln(lang: str) -> str:
    return _LANG_NAMES.get((lang or "und").lower(), (lang or "und").upper())


# ─────────────────────────────────────────────────────────────
# Formatters
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


# ─────────────────────────────────────────────────────────────
# Telegra.ph token
# ─────────────────────────────────────────────────────────────

async def _get_token() -> str:
    global _token
    if _token:
        return _token
    try:
        with open(_TOKEN_FILE, encoding="utf-8") as fh:
            _token = fh.read().strip()
        if _token:
            return _token
    except FileNotFoundError:
        pass

    async with aiohttp.ClientSession() as sess:
        async with sess.post(f"{_BASE}/createAccount", json={
            "short_name":  "ZilongLeech",
            "author_name": "Zilong MediaInfo",
        }) as r:
            data = await r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegraph createAccount failed: {data}")
    _token = data["result"]["access_token"]
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_TOKEN_FILE, "w", encoding="utf-8") as fh:
            fh.write(_token)
    except Exception:
        pass
    return _token


# ─────────────────────────────────────────────────────────────
# MediaInfo extraction
# ─────────────────────────────────────────────────────────────

async def get_mediainfo(path: str) -> str:
    """Run mediainfo CLI, fall back to ffprobe if not installed."""
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


async def _ffprobe_mediainfo_text(path: str) -> str:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-allowed_extensions", "ALL",
        "-analyzeduration", "20000000",
        "-probesize", "50000000",
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
        log.warning("ffprobe fallback error: %s", exc)
        return "MediaInfo unavailable"

    lines = ["General"]
    fmt   = data.get("format", {})
    lines.append(f"Complete name  : {os.path.basename(path)}")
    lines.append(f"Format         : {fmt.get('format_long_name', fmt.get('format_name', '?'))}")
    dur = 0.0
    try:
        dur = float(fmt.get("duration") or 0)
    except Exception:
        pass
    if dur:
        lines.append(f"Duration       : {_fmt_hms(dur)}")
    if fmt.get("bit_rate"):
        try:
            lines.append(f"Overall bit rate : {int(fmt['bit_rate'])//1000} kb/s")
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
        tags  = s.get("tags") or {}
        lang  = tags.get("language", "und")
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
                lines.append(f"FPS     : {fps:.3f}")
            except Exception:
                pass
            if s.get("bit_rate"):
                try:
                    lines.append(f"Bit rate : {int(s['bit_rate'])//1000} kb/s")
                except Exception:
                    pass
        elif stype == "audio":
            lines.append(f"Audio #{idx} [{lang}]")
            lines.append(f"Format  : {s.get('codec_long_name', s.get('codec_name', '?'))}")
            ch = s.get("channels", 0)
            if ch:
                ch_s = {1:"Mono",2:"Stereo",6:"5.1",8:"7.1"}.get(ch, f"{ch}ch")
                lines.append(f"Channels: {ch_s}")
            if s.get("sample_rate"):
                lines.append(f"Sampling: {s['sample_rate']} Hz")
            if s.get("bit_rate"):
                try:
                    lines.append(f"Bit rate : {int(s['bit_rate'])//1000} kb/s")
                except Exception:
                    pass
        elif stype == "subtitle":
            lines.append(f"Text #{idx} [{lang}]")
            lines.append(f"Format  : {s.get('codec_long_name', s.get('codec_name', '?'))}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Inline summary (displayed in the Telegram message)
# ─────────────────────────────────────────────────────────────

async def get_inline_summary(path: str) -> str:
    """Short HTML summary for inline display in Telegram."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-allowed_extensions", "ALL",
        "-analyzeduration", "20000000",
        "-probesize", "50000000",
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
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0 or not out.strip():
            return "❌ <i>ffprobe failed — cannot read file.</i>"
        data = json.loads(out.decode(errors="replace"))
    except Exception as exc:
        return f"❌ <i>Error: {exc}</i>"

    fmt     = data.get("format", {})
    streams = data.get("streams", [])

    dur = 0.0
    try:
        dur = float(fmt.get("duration") or 0)
    except Exception:
        pass

    fsize  = int(fmt.get("size") or 0)
    fname  = os.path.basename(path)
    dur_s  = _fmt_hms(dur) if dur else "?"
    size_s = _human_size(fsize) if fsize else "?"

    lines = [
        "📊 <b>Media Info</b>",
        f"📄 <code>{fname[:50]}</code>",
        f"💾 <code>{size_s}</code>   ⏱ <code>{dur_s}</code>",
        "──────────────────────",
    ]

    v_count = a_count = s_count = 0

    for s in streams:
        ct    = s.get("codec_type", "")
        codec = (s.get("codec_name") or "?").upper()
        tags  = s.get("tags") or {}
        lang  = (tags.get("language") or "und").lower()
        title_tag = (tags.get("title") or "").strip()

        if ct == "video":
            v_count += 1
            w, h   = s.get("width", 0), s.get("height", 0)
            fr     = s.get("r_frame_rate", "0/1")
            fps_s  = ""
            try:
                fn2, fd2 = fr.split("/")
                fps = float(fn2) / max(float(fd2), 1)
                fps_s = f"  {fps:.2f}fps"
            except Exception:
                pass
            pix   = s.get("pix_fmt", "")
            hdr_s = " HDR" if "10" in pix else ""
            br    = s.get("bit_rate", "")
            br_s  = f"  {int(int(br))//1000}kbps" if br and str(br).isdigit() else ""
            lines.append(f"🎬 <code>{codec}  {w}x{h}{fps_s}{hdr_s}{br_s}</code>")

        elif ct == "audio":
            a_count += 1
            ch   = s.get("channels", 0)
            ch_s = {1:"Mono",2:"Stereo",6:"5.1",8:"7.1"}.get(ch, f"{ch}ch") if ch else ""
            sr   = s.get("sample_rate", "")
            sr_s = f"  {int(sr)//1000}kHz" if sr else ""
            br   = s.get("bit_rate", "")
            br_s = f"  {int(int(br))//1000}kbps" if br and str(br).isdigit() else ""
            title_s = f" — {title_tag}" if title_tag else ""
            lines.append(f"{_fl(lang)} <code>{codec}  {ch_s}{sr_s}{br_s}</code>{title_s}")

        elif ct == "subtitle":
            s_count += 1
            title_s  = f" ({title_tag})" if title_tag else ""
            forced_s = " ⚡" if (tags.get("forced") or "0") not in ("0", "", "false") else ""
            lines.append(f"{_fl(lang)} <code>{codec}</code>  {_ln(lang)}{title_s}{forced_s}")

    if not v_count and not a_count and not s_count:
        lines.append("⚠️ <i>No media streams detected.</i>")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Telegra.ph posting
# ─────────────────────────────────────────────────────────────

async def post_to_telegraph(filename: str, text: str) -> str:
    """Post a MediaInfo text report to Telegra.ph, return public URL."""
    token = await _get_token()
    title = f"MediaInfo — {filename[:55]}"

    # Sanitise path leakage
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
        async with sess.post(f"{_BASE}/createPage", json={
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
