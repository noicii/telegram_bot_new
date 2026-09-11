import os
import json
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

ADMIN_IDS = [OWNER_ID] if OWNER_ID else []


DEFAULT_CHANNEL_ID = -1002067488300


BASE_DIR = Path(__file__).resolve().parent


DOWNLOAD_DIR = BASE_DIR / "downloads"
ASSETS_DIR = BASE_DIR / "assets"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"


DB_PATH = DATA_DIR / "bot_queue.db"


THUMB_PATH = ASSETS_DIR / "thumb.jpg"
CUSTOM_THUMB_PATH = THUMB_PATH

WATERMARK_PATH = ASSETS_DIR / "watermark.png"
CAPTION_PATH = ASSETS_DIR / "caption.txt"

SETTINGS_PATH = DATA_DIR / "settings.json"
STATS_PATH = DATA_DIR / "stats.json"
COOKIES_PATH = DATA_DIR / "cookies.txt"


for directory in (
    DOWNLOAD_DIR,
    ASSETS_DIR,
    DATA_DIR,
    LOG_DIR,
):
    directory.mkdir(
        parents=True,
        exist_ok=True,
    )


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)


MAX_RETRIES = 2
TASK_TIMEOUT = 1200

MAX_CONCURRENT_DOWNLOADS = 2

MAX_SPLIT_SIZE = 1950 * 1024 * 1024

MIN_DISK_FREE_GB = 2.0


PROTECTED_DOMAINS = [
    "luluvdo",
    "lulustream",
    "playmogo",
    "ponstream",
    "streamhide",
    "vidhide",
    "luluvid",
]


DEFAULT_SETTINGS = {
    "auto_delete": False,
    "compress": False,
    "custom_rename": True,
    "sample_video": True,
    "screenshots": False,
    "speed_limit": "0",
    "extract_subtitles": False,
    "target_resolution": "original",
    "audio_track_mode": "all",
    "download_preset": "custom",
    "queue_paused": False,
    "extra_channels": [],
}


SETTINGS = DEFAULT_SETTINGS.copy()


DEFAULT_STATS = {
    "total_tasks": 0,
    "successful": 0,
    "failed": 0,
    "total_downloaded": 0,

    # Compatibility keys used by handlers.py
    "total_downloaded_items": 0,
    "failed_items": 0,
    "total_downloaded_mb": 0.0,
}


STATS = DEFAULT_STATS.copy()


def load_json_settings():
    global SETTINGS

    try:
        if SETTINGS_PATH.exists():
            data = json.loads(
                SETTINGS_PATH.read_text(
                    encoding="utf-8"
                )
            )

            if isinstance(data, dict):
                SETTINGS.update(data)

    except Exception:
        SETTINGS = DEFAULT_SETTINGS.copy()


def save_settings():
    SETTINGS_PATH.write_text(
        json.dumps(
            SETTINGS,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_stats():
    global STATS

    try:
        if STATS_PATH.exists():
            data = json.loads(
                STATS_PATH.read_text(
                    encoding="utf-8"
                )
            )

            if isinstance(data, dict):
                STATS.update(data)

    except Exception:
        STATS = DEFAULT_STATS.copy()


def save_stats():
    STATS_PATH.write_text(
        json.dumps(
            STATS,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


load_json_settings()
load_stats()
