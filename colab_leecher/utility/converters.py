import os
import json
import GPUtil
import shutil
import logging
import subprocess
from asyncio import sleep
from threading import Thread
from datetime import datetime
from os import makedirs, path as ospath
from moviepy.editor import VideoFileClip as VideoClip
from colab_leecher.utility.variables import BOT, MSG, BotTimes, Paths, Messages
from colab_leecher.utility.helper import (
    getSize,
    fileType,
    keyboard,
    sizeUnit,
    speedETA,
    status_bar,
    sysINFO,
    getTime,
)


async def videoConverter(file: str):
    global BOT, MSG, BotTimes

    def convert_to_mp4(input_file, out_file):
        clip = VideoClip(input_file)
        clip.write_videofile(
            out_file,
            codec="libx264",
            audio_codec="aac",
            ffmpeg_params=["-strict", "-2"],
        )

    async def msg_updater(c: int, tr, engine: str):
        global Messages
        messg = f"╭「" + "░" * c + "█" + "░" * (11 - c) + "」"
        messg += f"\n├⏳ **Status »** __Running 🏃🏼‍♂️__\n├🕹 **Attempt »** __{tr}__"
        messg += f"\n├⚙️ **Engine »** __{engine}__\n├💪🏼 **Handler »** __{core}__"
        messg += f"\n╰🍃 **Time Spent »** __{getTime((datetime.now() - BotTimes.start_time).seconds)}__"
        try:
            await MSG.status_msg.edit_text(
                text=Messages.task_msg + mtext + messg + sysINFO(),
                reply_markup=keyboard(),
            )
        except Exception:
            pass

    name, ext = ospath.splitext(file)

    if ext.lower() in [".mkv", ".mp4"]:
        return file  # Already mp4/mkv

    c, out_file, Err = 0, f"{name}.{BOT.Options.video_out}", False
    gpu = len(GPUtil.getAvailable())

    quality = "-preset slow -qp 0" if BOT.Options.convert_quality else ""

    if gpu == 1:
        cmd = f"ffmpeg -y -i '{file}' {quality} -c:v h264_nvenc -c:a copy '{out_file}'"
        core = "GPU"
    else:
        cmd = f"ffmpeg -y -i '{file}' {quality} -c:v libx264 -c:a copy '{out_file}'"
        core = "CPU"

    mtext = f"<b>🎥 Converting Video »</b>\n\n{ospath.basename(file)}\n\n"

    proc = subprocess.Popen(cmd, shell=True)

    while proc.poll() is None:
        await msg_updater(c, "1st", "FFmpeg 🏍")
        c = (c + 1) % 12
        await sleep(3)

    if ospath.exists(out_file) and getSize(out_file) == 0:
        os.remove(out_file)
        Err = True
    elif not ospath.exists(out_file):
        Err = True

    if Err:
        proc = Thread(target=convert_to_mp4, name="Moviepy", args=(file, out_file))
        proc.start()
        core = "CPU"
        while proc.is_alive():
            await msg_updater(c, "2nd", "Moviepy 🛵")
            c = (c + 1) % 12
            await sleep(3)

    if ospath.exists(out_file) and getSize(out_file) == 0:
        os.remove(out_file)
        Err = True
    elif not ospath.exists(out_file):
        Err = True
    else:
        Err = False

    if Err:
        logging.error("This Video Can't Be Converted !")
        return file
    else:
        os.remove(file)
        return out_file


async def sizeChecker(file_path, remove: bool):
    """Split files larger than 2 GB into parts for Telegram upload."""
    max_size = 2097152000  # 2 GB
    file_size = os.stat(file_path).st_size

    if file_size > max_size:
        if not ospath.exists(Paths.temp_zpath):
            makedirs(Paths.temp_zpath)
        f_type = fileType(file_path)
        if f_type == "video" and BOT.Options.is_split:
            await splitVideo(file_path, 2000, remove)
        else:
            await splitFile(file_path, max_size, remove)
        await sleep(2)
        return True
    else:
        return False


async def splitFile(file_path, max_size, remove: bool):
    """Split any file into binary chunks."""
    _, filename = ospath.split(file_path)
    new_path = f"{Paths.temp_zpath}/{filename}"
    Messages.status_head = f"<b>✂️ SPLITTING » </b>\n\n<code>{filename}</code>\n"
    total_size = ospath.getsize(file_path)
    BotTimes.task_start = datetime.now()

    with open(file_path, "rb") as f:
        chunk = f.read(max_size)
        i = 1
        bytes_written = 0
        while chunk:
            ext = str(i).zfill(3)
            output_filename = "{}.{}".format(new_path, ext)
            with open(output_filename, "wb") as out:
                out.write(chunk)
            bytes_written += len(chunk)
            speed_string, eta, percentage = speedETA(
                BotTimes.task_start, bytes_written, total_size
            )
            await status_bar(
                Messages.status_head,
                speed_string,
                percentage,
                getTime(eta),
                sizeUnit(bytes_written),
                sizeUnit(total_size),
                "Xr-Split ✂️",
            )
            chunk = f.read(max_size)
            i += 1

    if remove and ospath.exists(file_path):
        os.remove(file_path)


async def splitVideo(file_path, max_size, remove: bool):
    global Paths, BOT, MSG, Messages
    _, filename = ospath.split(file_path)
    just_name, extension = ospath.splitext(filename)

    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", file_path]
    bitrate = None
    try:
        output = subprocess.check_output(cmd)
        video_info = json.loads(output)
        bitrate = float(video_info["format"]["bit_rate"])
    except subprocess.CalledProcessError:
        logging.error("Error: Could not get video bitrate")
        bitrate = 1000

    target_size_bits = max_size * 8 * 1024 * 1024
    duration = int(target_size_bits / bitrate)

    cmd = f'ffmpeg -i {file_path} -c copy -f segment -segment_time {duration} -reset_timestamps 1 "{Paths.temp_zpath}/{just_name}.part%03d{extension}"'

    Messages.status_head = f"<b>✂️ SPLITTING » </b>\n\n<code>{filename}</code>\n"
    BotTimes.task_start = datetime.now()

    proc = subprocess.Popen(cmd, shell=True)
    total_size = getSize(file_path)
    total_in_unit = sizeUnit(total_size)
    while proc.poll() is None:
        speed_string, eta, percentage = speedETA(
            BotTimes.task_start, getSize(Paths.temp_zpath), total_size
        )
        await status_bar(
            Messages.status_head,
            speed_string,
            percentage,
            getTime(eta),
            sizeUnit(getSize(Paths.temp_zpath)),
            total_in_unit,
            "Xr-Split ✂️",
        )
        await sleep(1)

    if remove:
        os.remove(file_path)
