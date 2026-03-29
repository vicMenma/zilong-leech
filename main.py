# @title ⚡ Zilong Leech — Colab Launcher
# @markdown ### How to set your credentials
# @markdown 1. Click the **🔑 key icon** in the left sidebar → "Secrets"
# @markdown 2. Add each secret below (toggle "Notebook access" ON for each):
# @markdown
# @markdown | Secret name | Required | Example |
# @markdown |---|---|---|
# @markdown | `API_ID` | ✅ | `12345678` |
# @markdown | `API_HASH` | ✅ | `abcdef1234567890abcdef1234567890` |
# @markdown | `BOT_TOKEN` | ✅ | `7873326341:AAHsLo5w...` |
# @markdown | `OWNER_ID` | ✅ | `7156656832` |
# @markdown | `DUMP_ID` | ✅ | `-1002831551404` |
# @markdown | `CC_API_KEY` | ☁️ optional | CloudConvert API key |
# @markdown | `NGROK_TOKEN` | ☁️ optional | ngrok authtoken |
# @markdown | `CC_WEBHOOK_SECRET` | ☁️ optional | CloudConvert webhook signing secret |

import subprocess
import time
import json
import shutil
import os
from IPython.display import clear_output
from threading import Thread

# ── Read all credentials from Colab Secrets ────────────────────
def _secret(name: str, default: str = "") -> str:
    """Read a Colab secret. Returns default if not found or not accessible."""
    try:
        from google.colab import userdata
        val = userdata.get(name)
        if val is not None:
            return str(val).strip()
    except Exception:
        pass
    # Fallback: environment variable (useful on EC2 / local)
    return os.environ.get(name, default).strip()


def _secret_int(name: str, default: int = 0) -> int:
    try:
        return int(_secret(name, str(default)))
    except (ValueError, TypeError):
        return default


print("🔑 Reading credentials from Colab Secrets…")

API_ID            = _secret_int("API_ID")
API_HASH          = _secret("API_HASH")
BOT_TOKEN         = _secret("BOT_TOKEN")
OWNER_ID          = _secret_int("OWNER_ID")
DUMP_ID           = _secret_int("DUMP_ID")

# Optional CloudConvert / ngrok
CC_API_KEY        = _secret("CC_API_KEY")
NGROK_TOKEN       = _secret("NGROK_TOKEN")
CC_WEBHOOK_SECRET = _secret("CC_WEBHOOK_SECRET")

# ── Validate required secrets ──────────────────────────────────
errors = []
if not API_ID:    errors.append("❌  API_ID is missing")
if not API_HASH:  errors.append("❌  API_HASH is missing")
if not BOT_TOKEN: errors.append("❌  BOT_TOKEN is missing")
if not OWNER_ID:  errors.append("❌  OWNER_ID is missing")
if not DUMP_ID:   errors.append("❌  DUMP_ID is missing")

if errors:
    print()
    for e in errors:
        print(e)
    print()
    print("👆 Go to the 🔑 Secrets panel (left sidebar) and add the missing secrets.")
    print("   Make sure 'Notebook access' is toggled ON for each secret.")
    raise SystemExit("Missing required secrets. Bot not started.")

print(f"  ✅  API_ID    = {API_ID}")
print(f"  ✅  API_HASH  = {API_HASH[:6]}…")
print(f"  ✅  BOT_TOKEN = {BOT_TOKEN[:8]}…")
print(f"  ✅  OWNER_ID  = {OWNER_ID}")
print(f"  ✅  DUMP_ID   = {DUMP_ID}")
if CC_API_KEY:
    print(f"  ✅  CC_API_KEY configured")
if NGROK_TOKEN:
    print(f"  ✅  NGROK_TOKEN configured")
if CC_WEBHOOK_SECRET:
    print(f"  ✅  CC_WEBHOOK_SECRET configured")

# ── Loading animation ──────────────────────────────────────────
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
        print("\r" + "░" * white + "▒▒" + "▓" * black + "▒▒" + "░" * white, end="")
        black = (black + 2) % 75
        white = (white - 1) if white != 0 else 37
        time.sleep(2)
    clear_output()


_Thread = Thread(target=Loading, name="Prepare", args=())
_Thread.start()

# ── System packages ────────────────────────────────────────────
if os.path.exists("/content/sample_data"):
    shutil.rmtree("/content/sample_data")

subprocess.run("git clone https://github.com/vicMenma/zilong-leech.git", shell=True)
subprocess.run("apt update -qq && apt install -y -qq ffmpeg aria2", shell=True)
subprocess.run("pip3 install -r /content/zilong-leech/requirements.txt -q", shell=True)

# ── Write credentials.json ─────────────────────────────────────
credentials = {
    "API_ID":    API_ID,
    "API_HASH":  API_HASH,
    "BOT_TOKEN": BOT_TOKEN,
    "OWNER_ID":  OWNER_ID,
    "DUMP_ID":   DUMP_ID,
    **({"CC_API_KEY":        CC_API_KEY}        if CC_API_KEY        else {}),
    **({"NGROK_TOKEN":       NGROK_TOKEN}       if NGROK_TOKEN       else {}),
    **({"CC_WEBHOOK_SECRET": CC_WEBHOOK_SECRET} if CC_WEBHOOK_SECRET else {}),
}

with open("/content/zilong-leech/credentials.json", "w") as f:
    json.dump(credentials, f)

Working = False

# Clean stale session
if os.path.exists("/content/zilong-leech/my_bot.session"):
    os.remove("/content/zilong-leech/my_bot.session")

print("\rStarting Bot…")

get_ipython().system("cd /content/zilong-leech && python3 -m colab_leecher")
