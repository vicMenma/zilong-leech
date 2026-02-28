import logging
from PIL import Image
from asyncio import sleep
from os import path as ospath
from datetime import datetime
from pyrogram.errors import FloodWait
from colab_leecher import colab_bot, OWNER
from colab_leecher.utility.variables import BOT, Transfer, BotTimes, Messages, MSG, Paths
from colab_leecher.utility.helper import (
    sizeUnit, fileType, getTime, status_bar, thumbMaintainer, videoExtFix,
)


async def progress_bar(current, total):
    upload_speed = 4 * 1024 * 1024
    elapsed = (datetime.now() - BotTimes.task_start).seconds
    if current > 0 and elapsed > 0:
        upload_speed = current / elapsed
    eta        = (Transfer.total_down_size - current - sum(Transfer.up_bytes)) / max(upload_speed, 1)
    percentage = (current + sum(Transfer.up_bytes)) / max(Transfer.total_down_size, 1) * 100
    await status_bar(
        down_msg=Messages.status_head,
        speed=f"{sizeUnit(upload_speed)}/s",
        percentage=percentage,
        eta=getTime(eta),
        done=sizeUnit(current + sum(Transfer.up_bytes)),
        left=sizeUnit(Transfer.total_down_size),
        engine="Pyrofork 💥",
    )


async def upload_file(file_path, real_name, is_last: bool = False):
    """
    Upload one file directly to the owner's private chat.

    is_last  — when True the caption shows ✅ Done and the
               progress status message is deleted afterwards.
    """
    global Transfer, MSG
    BotTimes.task_start = datetime.now()

    # Caption: clean name, or "✅ Done · name" on the final file
    name_part = f"{BOT.Setting.prefix} {real_name} {BOT.Setting.suffix}".strip()
    if is_last:
        caption = f"<{BOT.Options.caption}>✅ Done · {name_part}</{BOT.Options.caption}>"
    else:
        caption = f"<{BOT.Options.caption}>{name_part}</{BOT.Options.caption}>"

    type_  = fileType(file_path)
    f_type = type_ if BOT.Options.stream_upload else "document"

    try:
        if f_type == "video":
            if not BOT.Options.stream_upload:
                file_path = videoExtFix(file_path)
            thmb_path, seconds = thumbMaintainer(file_path)
            with Image.open(thmb_path) as img:
                width, height = img.size
            sent = await colab_bot.send_video(
                chat_id=OWNER,
                video=file_path,
                supports_streaming=True,
                width=width, height=height,
                caption=caption,
                thumb=thmb_path,
                duration=int(seconds),
                progress=progress_bar,
            )

        elif f_type == "audio":
            thmb_path = Paths.THMB_PATH if ospath.exists(Paths.THMB_PATH) else None
            sent = await colab_bot.send_audio(
                chat_id=OWNER,
                audio=file_path,
                caption=caption,
                thumb=thmb_path,
                progress=progress_bar,
            )

        elif f_type == "photo":
            sent = await colab_bot.send_photo(
                chat_id=OWNER,
                photo=file_path,
                caption=caption,
                progress=progress_bar,
            )

        else:  # document
            if ospath.exists(Paths.THMB_PATH):
                thmb_path = Paths.THMB_PATH
            elif type_ == "video":
                thmb_path, _ = thumbMaintainer(file_path)
            else:
                thmb_path = None
            sent = await colab_bot.send_document(
                chat_id=OWNER,
                document=file_path,
                caption=caption,
                thumb=thmb_path,
                progress=progress_bar,
            )

        MSG.sent_msg = sent
        Transfer.sent_file.append(sent)
        Transfer.sent_file_names.append(real_name)

        # Delete the progress status message once the last file lands
        if is_last:
            try:
                await MSG.status_msg.delete()
            except Exception:
                pass

    except FloodWait as e:
        logging.warning(f"FloodWait {e.value}s")
        await sleep(e.value)
        await upload_file(file_path, real_name, is_last)

    except Exception as e:
        logging.error(f"Upload error: {e}")
