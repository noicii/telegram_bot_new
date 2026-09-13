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

# Apply runtime hardening before handlers are imported.
from runtime_hardening import apply as apply_runtime_hardening
apply_runtime_hardening()

from handlers import register_handlers
from queue_worker import start_queue_worker, stop_queue_worker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger("telegram_bot")

if not API_ID:
    raise RuntimeError("API_ID is missing")
if not API_HASH:
    raise RuntimeError("API_HASH is missing")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

init_db()
logger.info("Bot configuration loaded")
logger.info("Default channel: %s", DEFAULT_CHANNEL_ID)
logger.info("HLS segment concurrency: 16 per video")

app = Client(
    "telegram_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=8,
)

register_handlers(app)


if __name__ == "__main__":
    import asyncio
    from pyrogram import idle

    async def run_bot():
        logger.info("Starting Telegram bot...")
        await app.start()
        logger.info("Telegram client started")

        try:
            channel = await app.get_chat(DEFAULT_CHANNEL_ID)
            me = await app.get_chat_member(DEFAULT_CHANNEL_ID, "me")
            logger.info(
                "Default channel check OK: %s | bot status: %s",
                getattr(channel, "title", DEFAULT_CHANNEL_ID),
                getattr(me, "status", "unknown"),
            )
        except Exception as channel_exc:
            logger.exception(
                "Default channel check failed for %s: %s",
                DEFAULT_CHANNEL_ID,
                channel_exc,
            )

        start_queue_worker(app)
        logger.info("Queue worker started")

        try:
            await idle()
        finally:
            logger.info("Stopping queue worker...")
            await stop_queue_worker()
            logger.info("Stopping Telegram bot...")
            await app.stop()
            logger.info("Telegram bot stopped")

    try:
        asyncio.get_event_loop().run_until_complete(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception:
        logger.exception("Bot crashed")
        raise
