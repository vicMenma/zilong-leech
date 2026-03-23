# @title 🖥️ Zilong Code
API_ID    = 0                              # @param {type: "integer"}
API_HASH  = ""                             # @param {type: "string"}
BOT_TOKEN = ""                             # @param {type: "string"}
USER_ID   = 0                              # @param {type: "integer"}
DUMP_ID   = 0                              # @param {type: "integer"} — unused, keep as 0

# ── CloudConvert webhook (optional) ──────────────────────────────────────────
# Get a free ngrok token at: https://dashboard.ngrok.com/get-started/your-authtoken
NGROK_TOKEN       = ""  # @param {type: "string"}
# Signing secret from CloudConvert → Dashboard → Webhooks → Signing Secret
CC_WEBHOOK_SECRET = ""  # @param {type: "string"}
# API key from CloudConvert → Dashboard → API → API Keys (for /hardsub future use)
CC_API_KEY        = ""  # @param {type: "string"}

import subprocess, time, json, shutil, os
from IPython.display import clear_output
from threading import Thread

Working = True

banner = '''
 ███████╗██╗██╗██╗      ██████╗ ███╗   ██╗ ██████╗
 ╚══███╔╝██║██║██║     ██╔═══██╗████╗  ██║██╔════╝
   ███╔╝ ██║██║██║     ██║   ██║██╔██╗ ██║██║  ███╗
  ███╔╝  ██║██║██║     ██║   ██║██║╚██╗██║██║   ██║
 ███████╗██║██║███████╗╚██████╔╝██║ ╚████║╚██████╔╝
 ╚══════╝╚═╝╚═╝╚══════╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝

  ██████╗ ██████╗ ██████╗ ███████╗
 ██╔════╝██╔═══██╗██╔══██╗██╔════╝
 ██║     ██║   ██║██║  ██║█████╗
 ██║     ██║   ██║██║  ██║██╔══╝
 ╚██████╗╚██████╔╝██████╔╝███████╗
  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
'''
print(banner)

def Loading():
    white = 37
    black = 0
    while Working:
        print("\r" + "░"*white + "▒▒"+ "▓"*black + "▒▒" + "░"*white, end="")
        black = (black + 2) % 75
        white = (white - 1) if white != 0 else 37
        time.sleep(2)
    clear_output()

_Thread = Thread(target=Loading, name="Prepare", args=())
_Thread.start()

if os.path.exists("/content/sample_data"):
    shutil.rmtree("/content/sample_data")

subprocess.run("git clone https://github.com/vicMenma/zilong-leech.git", shell=True)
subprocess.run("apt update && apt install -y ffmpeg aria2", shell=True)
subprocess.run("pip3 install -r /content/zilong-leech/requirements.txt", shell=True)

credentials = {
    "API_ID":    API_ID,
    "API_HASH":  API_HASH,
    "BOT_TOKEN": BOT_TOKEN,
    "USER_ID":   USER_ID,
    "DUMP_ID":   DUMP_ID,
    # CloudConvert — only written when non-empty so credentials.json stays clean
    **({"NGROK_TOKEN":       NGROK_TOKEN}       if NGROK_TOKEN       else {}),
    **({"CC_WEBHOOK_SECRET": CC_WEBHOOK_SECRET} if CC_WEBHOOK_SECRET else {}),
    **({"CC_API_KEY":        CC_API_KEY}        if CC_API_KEY        else {}),
}

with open('/content/zilong-leech/credentials.json', 'w') as f:
    json.dump(credentials, f)

Working = False

if os.path.exists("/content/zilong-leech/my_bot.session"):
    os.remove("/content/zilong-leech/my_bot.session")

print("\rStarting Bot....")

!cd /content/zilong-leech && python3 -m colab_leecher
