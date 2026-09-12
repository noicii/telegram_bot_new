"""Small offline smoke test for V2 pipeline wiring."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

# Direct execution puts bot_vnext/app first on sys.path. That would make
# app/queue shadow Python's standard-library queue module. Remove the script
# directory and put bot_vnext itself first instead.
ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != SCRIPT_DIR]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline import Pipeline
from app.storage.database import Database


class DummyClient:
    async def send_video(self, *args, **kwargs):
        return None

    async def send_document(self, *args, **kwargs):
        return None

    async def send_audio(self, *args, **kwargs):
        return None


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        pipe = Pipeline(DummyClient(), Path(tmp) / "downloads", database=db)
        await pipe.start()
        assert pipe.download.pool.worker_count == 2
        assert pipe.upload.pool.worker_count == 4
        await pipe.stop()
        print("V2 PIPELINE SMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())
