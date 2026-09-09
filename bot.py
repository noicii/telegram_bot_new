import logging

from pyrogram import Client

from config import (
    API_HASH,
    API_ID,
    BOT_TOKEN,
    DEFAULT_CHANNEL_ID,
    LOG_DIR,
)
from database import init_db
from handlers import register_handlers


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / "bot.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger("telegram_bot")


# Validate required configuration before starting.
if not API_ID:
    raise RuntimeError("API_ID is missing")

if not API_HASH:
    raise RuntimeError("API_HASH is missing")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")


# Initialize database.
init_db()

logger.info("Bot configuration loaded")
logger.info("Default channel: %s", DEFAULT_CHANNEL_ID)


# Create the Pyrogram client.
app = Client(
    "telegram_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=8,
)


# Register all bot handlers.
register_handlers(app)


if __name__ == "__main__":
    try:
        logger.info("Starting Telegram bot...")
        app.run()

    except KeyboardInterrupt:
        logger.info("Bot stopped by user")

    except Exception:
        logger.exception("Bot crashed")
        raise
