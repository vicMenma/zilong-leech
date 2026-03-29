"""
colab_leecher/media_info.py
MediaInfo helpers — rich inline display matching zilong_multiusage quality.

Functions:
  get_inline_summary(path) → rich HTML for Telegram inline display
  get_mediainfo(path)      → full text report (mediainfo CLI or ffprobe fallback)
  post_to_telegraph(filename, text) → Telegra.ph URL

FIXES vs previous version:
  - Removed -allowed_extensions ALL (not supported on all ffprobe versions, caused
    "Option allowed_extensions not found" on Colab)
  - get_inline_summary now shows codec+profile, fps, HDR, bitrate per track,
    channel layout, sample rate, forced subtitle markers, overall bitrate —
    matching the detail level of the attached Zilong_v2 repo
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


def _fps_str(r_frame_rate: str) -> str:
    """Convert '24000/1001' → '23.976fps', '25/1' → '25fps'."""
    try:
        n, d = r_frame_rate.split("/")
        val  = float(n) / max(float(d), 1)
        # Pretty-print common rates
        for known, label in ((23.976, "23.976"), (24.0, "24"), (25.0, "25"),
                             (29.970, "29.97"), (30.0, "30"), (47.952, "47.95"),
                             (50.0, "50"), (59.940, "59.94"), (60.0, "60")):
            if abs(val - known) < 0.02:
                return f"{label}fps"
        return f"{val:.3f}fps"
    except Exception:
        return ""


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
# MediaInfo extraction (full text — for Telegraph)
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
    """ffprobe fallback for full text mediainfo report (no -allowed_extensions)."""
    cmd = [
        "ffprobe", "-v", "quiet",
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
            profile  = s.get("profile", "")
            level    = s.get("level")
            codec_ln = s.get("codec_long_name") or s.get("codec_name", "?")
            w, h     = s.get("width", 0), s.get("height", 0)
            fps      = _fps_str(s.get("r_frame_rate", "0/1"))
            pix      = s.get("pix_fmt", "")
            hdr      = "HDR" if "10" in pix else ""
            br       = s.get("bit_rate", "")
            lines.append(f"Video #{idx}")
            prof_s = f" @ {profile}" if profile else ""
            lv_s   = f" L{level/10:.1f}" if level else ""
            lines.append(f"Format         : {codec_ln}{prof_s}{lv_s}")
            if w and h:
                lines.append(f"Size           : {w}x{h}")
            if fps:
                lines.append(f"Frame rate     : {fps}")
            if pix:
                lines.append(f"Chroma         : {pix}" + (f"  [{hdr}]" if hdr else ""))
            if br:
                try:
                    lines.append(f"Bit rate       : {int(br)//1000} kb/s")
                except Exception:
                    pass
        elif stype == "audio":
            codec_ln = s.get("codec_long_name") or s.get("codec_name", "?")
            ch       = s.get("channels", 0)
            ch_lyt   = s.get("channel_layout", "")
            ch_s     = ch_lyt or {1:"Mono",2:"Stereo",6:"5.1",8:"7.1"}.get(ch, f"{ch}ch") if ch else ""
            sr       = s.get("sample_rate", "")
            br       = s.get("bit_rate", "")
            flag     = _fl(lang)
            lname    = _ln(lang)
            title    = tags.get("title", "")
            lines.append(f"Audio #{idx}  {flag} {lname}")
            lines.append(f"Format         : {codec_ln}")
            if ch_s:
                lines.append(f"Channels       : {ch_s}")
            if sr:
                lines.append(f"Sampling rate  : {sr} Hz")
            if br:
                try:
                    lines.append(f"Bit rate       : {int(br)//1000} kb/s")
                except Exception:
                    pass
            if title:
                lines.append(f"Title          : {title}")
        elif stype == "subtitle":
            codec_ln = s.get("codec_long_name") or s.get("codec_name", "?")
            flag     = _fl(lang)
            lname    = _ln(lang)
            title    = tags.get("title", "")
            forced   = tags.get("forced", "0")
            lines.append(f"Text #{idx}  {flag} {lname}")
            lines.append(f"Format         : {codec_ln}")
            if title:
                lines.append(f"Title          : {title}")
            if forced not in ("0", "", "false"):
                lines.append("Forced         : Yes")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Inline summary — rich display in Telegram message
# ─────────────────────────────────────────────────────────────

async def get_inline_summary(path: str) -> str:
    """
    Rich HTML summary for inline display in Telegram.
    Shows codec+profile, resolution, fps, HDR, bitrate per track,
    language flags, channel layout, sample rate, forced subtitle markers,
    and overall bitrate — matching the detail level of Zilong_v2.

    FIX: -allowed_extensions ALL removed (caused 'Option not found' on Colab's ffprobe).
    FIX: probe is run directly on the URL — no partial download needed.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
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
    except asyncio.TimeoutError:
        return "❌ <i>ffprobe timeout (30s).</i>"
    except Exception as exc:
        return f"❌ <i>Error: {exc}</i>"

    fmt     = data.get("format", {})
    streams = data.get("streams", [])

    # ── General info ─────────────────────────────────────────
    dur = 0.0
    try:
        dur = float(fmt.get("duration") or 0)
    except Exception:
        pass

    fsize   = int(fmt.get("size") or 0)
    fname   = os.path.basename(path)
    dur_s   = _fmt_hms(dur)  if dur   else "?"
    size_s  = _human_size(fsize) if fsize else "?"

    overall_br = ""
    try:
        br_val = int(fmt.get("bit_rate") or 0)
        if br_val:
            overall_br = f"   📡 <code>~{br_val // 1000} kbps</code>"
    except Exception:
        pass

    fmt_name = fmt.get("format_long_name") or fmt.get("format_name", "")
    # Shorten e.g. "Matroska / WebM" → "Matroska"
    fmt_short = fmt_name.split("/")[0].strip()[:20] if fmt_name else ""

    lines = [
        "📊 <b>MEDIA INFO</b>",
        "──────────────────────",
        f"📄 <code>{fname[:55]}</code>",
    ]
    if fmt_short:
        lines.append(f"📦 <i>{fmt_short}</i>   💾 <code>{size_s}</code>   ⏱ <code>{dur_s}</code>{overall_br}")
    else:
        lines.append(f"💾 <code>{size_s}</code>   ⏱ <code>{dur_s}</code>{overall_br}")

    # ── Collect per-type streams ──────────────────────────────
    v_streams, a_streams, s_streams = [], [], []
    for s in streams:
        ct = s.get("codec_type", "")
        if   ct == "video":    v_streams.append(s)
        elif ct == "audio":    a_streams.append(s)
        elif ct == "subtitle": s_streams.append(s)

    # ── Video tracks ──────────────────────────────────────────
    if v_streams:
        n = len(v_streams)
        lines.append("")
        lines.append(f"🎬 <b>Vidéo</b>  ·  {n} piste{'s' if n > 1 else ''}")
        for s in v_streams:
            codec    = (s.get("codec_name") or "?").upper()
            profile  = s.get("profile", "")
            level    = s.get("level")
            w, h     = s.get("width", 0), s.get("height", 0)
            fps      = _fps_str(s.get("r_frame_rate", "0/1"))
            pix      = s.get("pix_fmt", "")
            hdr_s    = "  HDR" if ("10" in pix or "hlg" in pix.lower()) else ""
            br       = s.get("bit_rate", "")
            br_s     = f"  ~{int(br)//1000} kbps" if br and str(br).isdigit() else ""

            # Codec + profile line
            prof_s = ""
            if profile:
                lv_s = f" L{level/10:.1f}" if isinstance(level, int) and level > 0 else ""
                prof_s = f" @ {profile}{lv_s}"
            lines.append(f"   <code>{codec}{prof_s}</code>")

            # Resolution + fps + HDR line
            res_s = f"{w}×{h}" if (w and h) else ""
            detail_parts = [p for p in [res_s, fps, pix] if p]
            detail = "  ".join(detail_parts) + hdr_s + br_s
            if detail.strip():
                lines.append(f"   <code>{detail.strip()}</code>")

    # ── Audio tracks ──────────────────────────────────────────
    if a_streams:
        n = len(a_streams)
        lines.append("")
        lines.append(f"🎵 <b>Audio</b>  ·  {n} piste{'s' if n > 1 else ''}")
        for s in a_streams:
            codec    = (s.get("codec_name") or "?").upper()
            tags     = s.get("tags") or {}
            lang     = (tags.get("language") or "und").lower()
            title_t  = (tags.get("title") or "").strip()
            ch       = s.get("channels", 0)
            ch_lyt   = s.get("channel_layout", "")
            ch_s     = (ch_lyt
                        or {1:"Mono",2:"Stereo",6:"5.1 ch",8:"7.1 ch"}.get(ch, f"{ch}ch")
                        if ch else "")
            sr       = s.get("sample_rate", "")
            sr_s     = f"{int(sr)//1000}kHz" if sr else ""
            br       = s.get("bit_rate", "")
            br_s     = f"~{int(br)//1000}kbps" if br and str(br).isdigit() else ""

            detail_parts = [p for p in [codec, ch_s, sr_s, br_s] if p]
            detail = "  ".join(detail_parts)
            flag   = _fl(lang)
            lname  = _ln(lang)
            title_s = f"  <i>{title_t}</i>" if title_t and title_t.lower() != lname.lower() else ""
            lines.append(f"   {flag} <b>{lname}</b>  <code>{detail}</code>{title_s}")

    # ── Subtitle tracks ───────────────────────────────────────
    if s_streams:
        n = len(s_streams)
        lines.append("")
        lines.append(f"💬 <b>Sous-titres</b>  ·  {n} piste{'s' if n > 1 else ''}")
        for s in s_streams:
            codec   = (s.get("codec_name") or "?").upper()
            tags    = s.get("tags") or {}
            lang    = (tags.get("language") or "und").lower()
            title_t = (tags.get("title") or "").strip()
            forced  = tags.get("forced", "0") not in ("0", "", "false")
            flag    = _fl(lang)
            lname   = _ln(lang)
            forced_s  = "  ⚡ Forced" if forced else ""
            title_s   = f"  <i>({title_t})</i>" if title_t else ""
            lines.append(f"   {flag} <b>{lname}</b>  <code>{codec}</code>{forced_s}{title_s}")

    if not v_streams and not a_streams and not s_streams:
        lines.append("")
        lines.append("⚠️ <i>No media streams detected.</i>")

    lines.append("──────────────────────")
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
