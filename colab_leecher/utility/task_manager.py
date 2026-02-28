import shutil
import logging
from time import time
from datetime import datetime
from asyncio import sleep
from os import makedirs, path as ospath
from colab_leecher import OWNER, colab_bot
from colab_leecher.downlader.manager import calDownSize, get_d_name, downloadManager
from colab_leecher.utility.helper import (
    getSize, applyCustomName, keyboard, sysINFO,
)
from colab_leecher.utility.handler import (
    Leech, SendLogs, cancelTask,
)
from colab_leecher.utility.variables import (
    BOT, MSG, BotTimes, Messages, Paths, Aria2c, Transfer, TaskError,
)


async def taskScheduler():
    global BOT, MSG, BotTimes, Messages, Paths, Transfer, TaskError

    # Reset
    Messages.download_name   = ""
    Messages.task_msg        = ""
    Messages.status_head     = "<b>📥 DOWNLOADING</b>\n"
    Transfer.sent_file       = []
    Transfer.sent_file_names = []
    Transfer.down_bytes      = [0, 0]
    Transfer.up_bytes        = [0, 0]

    # Prepare work directory
    if ospath.exists(Paths.WORK_PATH):
        shutil.rmtree(Paths.WORK_PATH)
    makedirs(Paths.WORK_PATH)
    makedirs(Paths.down_path)

    await calDownSize(BOT.SOURCE)
    await get_d_name(BOT.SOURCE[0])

    BotTimes.current_time = time()

    await Do_Leech(BOT.SOURCE, BOT.Mode.ytdl)


async def Do_Leech(source, is_ytdl):
    await downloadManager(source, is_ytdl)
    Transfer.total_down_size = getSize(Paths.down_path)
    applyCustomName()
    await Leech(Paths.down_path, True)
    await SendLogs(True)
