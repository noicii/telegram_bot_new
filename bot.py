import logging

from pyrogram import Client, idle

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


app = Client(
    "telegram_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=8,
)


register_handlers(app)


async def startup():
    init_db()

    if not API_ID:
        raise RuntimeError("API_ID is missing")

    if not API_HASH:
        raise RuntimeError("API_HASH is missing")

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    logger.info("Bot configuration loaded")
    logger.info("Default channel: %s", DEFAULT_CHANNEL_ID)


if __name__ == "__main__":
    import asyncio

    async def main():
        await startup()

        await app.start()

        me = await app.get_me()

        logger.info(
            "Bot started: @%s (%s)",
            me.username,
            me.id,
        )

        await idle()

        await app.stop()

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("Bot stopped by user")

    except Exception:
        logger.exception("Bot crashed")
        raise
