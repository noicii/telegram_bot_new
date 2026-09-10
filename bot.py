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
from queue_worker import start_queue_worker, stop_queue_worker


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
    import asyncio
    from pyrogram import idle

    async def run_bot():
        logger.info("Starting Telegram bot...")
        app.start()

        logger.info("Telegram client started")

        start_queue_worker(app)
        logger.info("Queue worker started")

        try:
            await idle()
        finally:
            logger.info("Stopping queue worker...")
            await stop_queue_worker()

            logger.info("Stopping Telegram bot...")
            app.stop()
            logger.info("Telegram bot stopped")

    try:
        asyncio.get_event_loop().run_until_complete(run_bot())

    except KeyboardInterrupt:
        logger.info("Bot stopped by user")

    except Exception:
        logger.exception("Bot crashed")
        raise
