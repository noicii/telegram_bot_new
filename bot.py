cd ~/telegram_bot_new
source venv/bin/activate

cat > bot.py <<'PY'
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


if __name__ == "__main__":
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception:
        logger.exception("Bot crashed")
        raise
PY

python -m py_compile bot.py
echo "BOT.PY FIXED AND COMPILES OK"
