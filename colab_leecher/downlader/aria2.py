"""
colab_leecher/downlader/aria2.py
Faster aria2c settings:
  - x16 connections  + --split=16 + --min-split-size=1M
  - --file-allocation=none  (skip pre-allocation, faster start)
  - aiohttp fallback when aria2c fails on a direct HTTP link
"""
import re
import logging
import subprocess
from datetime import datetime

from colab_leecher.utility.helper import sizeUnit, status_bar
from colab_leecher.utility.variables import BOT, Aria2c, Paths, Messages, BotTimes

_MAGNET_RE = re.compile(r"^magnet:\?", re.I)


async def aria2_Download(link: str, num: int):
    global BotTimes, Messages

    name_d = get_Aria2c_Name(link)
    BotTimes.task_start = datetime.now()
    Messages.status_head = (
        f"<b>📥 DOWNLOADING FROM » </b>"
        f"<i>🔗Link {str(num).zfill(2)}</i>\n\n"
        f"<b>🏷️ Name » </b><code>{name_d}</code>\n"
    )

    command = [
        "aria2c",
        "-x16",                       # 16 connections per server
        "--split=16",                  # 16 segments per file
        "--min-split-size=1M",         # split chunks from 1 MB
        "--file-allocation=none",      # skip pre-allocation (faster cold start)
        "--seed-time=0",
        "--summary-interval=1",
        "--max-tries=3",
        "--console-log-level=notice",
        "-d", Paths.down_path,
        link,
    ]

    proc = subprocess.Popen(
        command, bufsize=0, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    while True:
        output = proc.stdout.readline()
        if output == b"" and proc.poll() is not None:
            break
        if output:
            await on_output(output.decode("utf-8"))

    exit_code = proc.wait()
    error_output = proc.stderr.read()

    if exit_code != 0:
        # ── Fallback: aiohttp direct download when aria2c fails ──────────
        # This handles cases where aria2c is not running (AWS EC2, Koyeb)
        # and the link is a direct HTTP(S) URL (not magnet/torrent).
        if not _MAGNET_RE.match(link) and link.startswith("http"):
            logging.warning(
                "[aria2] aria2c exited %d for %s — trying aiohttp fallback",
                exit_code, link[:60],
            )
            try:
                from colab_leecher.downlader.direct_http import download_direct
                import asyncio as _asyncio

                async def _progress(done, total, speed, eta):
                    speed_str = f"{sizeUnit(speed)}/s"
                    pct = (done / total * 100) if total else 0
                    from colab_leecher.utility.helper import getTime
                    await status_bar(
                        Messages.status_head,
                        speed_str,
                        int(pct),
                        getTime(int(eta)),
                        sizeUnit(done),
                        sizeUnit(total) if total else "?",
                        "aiohttp 🌐",
                    )

                await download_direct(link, Paths.down_path, progress=_progress)
                return  # success via fallback

            except Exception as fallback_exc:
                logging.error("[aria2] aiohttp fallback also failed: %s", fallback_exc)

        # Log original aria2 error
        if exit_code == 3:
            logging.error("Resource not found: %s", link)
        elif exit_code == 9:
            logging.error("Not enough disk space")
        elif exit_code == 24:
            logging.error("HTTP authorization failed")
        else:
            logging.error(
                "aria2c exit %d for %s: %s",
                exit_code, link, error_output.decode(errors="replace")[:200],
            )


def get_Aria2c_Name(link):
    if len(BOT.Options.custom_name) != 0:
        return BOT.Options.custom_name
    cmd = f'aria2c -x10 --dry-run --file-allocation=none "{link}"'
    result = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True)
    stdout_str = result.stdout.decode("utf-8")
    filename = stdout_str.split("complete: ")[-1].split("\n")[0]
    name = filename.split("/")[-1]
    if len(name) == 0:
        name = "UNKNOWN DOWNLOAD NAME"
    return name


async def on_output(output: str):
    global link_info
    total_size = "0B"
    progress_percentage = "0B"
    downloaded_bytes = "0B"
    eta = "0S"
    try:
        if "ETA:" in output:
            parts = output.split()
            total_size = parts[1].split("/")[1]
            total_size = total_size.split("(")[0]
            progress_percentage = parts[1][parts[1].find("(") + 1: parts[1].find(")")]
            downloaded_bytes = parts[1].split("/")[0]
            eta = parts[4].split(":")[1][:-1]
    except Exception as do:
        logging.error(f"Couldn't Get Info Due to: {do}")

    percentage = re.findall(r"\d+\.\d+|\d+", progress_percentage)[0]
    down = re.findall(r"\d+\.\d+|\d+", downloaded_bytes)[0]
    down_unit = re.findall(r"[a-zA-Z]+", downloaded_bytes)[0]
    if "G" in down_unit:
        spd = 3
    elif "M" in down_unit:
        spd = 2
    elif "K" in down_unit:
        spd = 1
    else:
        spd = 0

    elapsed_time_seconds = (datetime.now() - BotTimes.task_start).seconds

    if elapsed_time_seconds >= 270 and not Aria2c.link_info:
        logging.error("Failed to get download information ! Probably dead link 💀")

    if total_size != "0B":
        Aria2c.link_info = True
        current_speed = (float(down) * 1024 ** spd) / elapsed_time_seconds
        speed_string = f"{sizeUnit(current_speed)}/s"

        await status_bar(
            Messages.status_head,
            speed_string,
            int(percentage),
            eta,
            downloaded_bytes,
            total_size,
            "Aria2c 🧨",
        )
