"""Small offline smoke test for V2 pipeline wiring."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

# Allow this file to be executed directly from the repository root:
#   python bot_vnext/app/pipeline_smoke.py
ROOT = Path(__file__).resolve().parents[1]
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
