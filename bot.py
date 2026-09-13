import asyncio
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
import handlers
from handlers import register_handlers
from queue_worker import start_queue_worker, stop_queue_worker
import upload_manager


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


if not API_ID:
    raise RuntimeError("API_ID is missing")

if not API_HASH:
    raise RuntimeError("API_HASH is missing")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")


init_db()

logger.info("Bot configuration loaded")
logger.info("Default channel: %s", DEFAULT_CHANNEL_ID)


app = Client(
    "telegram_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=8,
)


register_handlers(app)

# The download worker must never own an upload slot.  Replace the old
# fire-and-forget upload coroutine with a real bounded upload queue.
_original_upload_pipeline = handlers._run_upload_pipeline


async def _queued_upload_pipeline(*args):
    task_id = args[-1]
    await upload_manager.enqueue(task_id, args)


handlers._run_upload_pipeline = _queued_upload_pipeline


if __name__ == "__main__":

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

        await upload_manager.start(_original_upload_pipeline)
        start_queue_worker(app)
        logger.info("Queue worker and 4-slot upload pool started")

        try:
            await idle()
        finally:
            logger.info("Stopping queue worker...")
            await stop_queue_worker()

            logger.info("Stopping upload pool...")
            await upload_manager.stop()

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
