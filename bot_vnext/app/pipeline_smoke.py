"""Small offline smoke test for V2 pipeline wiring."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

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
