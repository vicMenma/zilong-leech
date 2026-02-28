# colab_leecher/__init__.py
import json
import logging
import asyncio
from pathlib import Path

from uvloop import install
from pyrogram.client import Client

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)

CREDENTIALS_PATH = Path("/content/zilong/credentials.json")


def load_credentials(path: Path = CREDENTIALS_PATH) -> dict:
    """Load and validate credentials from JSON file."""
    if not path.exists():
        raise FileNotFoundError(f"Credentials file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        creds = json.load(f)

    required_keys = ["API_ID", "API_HASH", "BOT_TOKEN", "USER_ID", "DUMP_ID"]
    missing = [k for k in required_keys if k not in creds]
    if missing:
        raise KeyError(f"Missing keys in credentials.json: {missing}")

    return creds


# Load credentials
credentials = load_credentials()

API_ID = int(credentials["API_ID"])
API_HASH = str(credentials["API_HASH"])
BOT_TOKEN = str(credentials["BOT_TOKEN"])
OWNER = int(credentials["USER_ID"])
DUMP_ID = str(credentials["DUMP_ID"])

log.info("Credentials loaded successfully")

# Use uvloop as event loop policy
install()

# Explicitly create and set an event loop for the main thread
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# Create Pyrogram client using the current loop
colab_bot = Client(
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

log.info("Pyrogram Client initialized")
