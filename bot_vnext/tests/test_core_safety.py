from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.task import TaskContext
from app.storage.database import Database


def test_retry_counter_is_preserved(tmp_path: Path) -> None:
    async def run() -> None:
        db = Database(tmp_path / "tasks.db")
        await db.init()
        await db.create_task("1", "download", url="https://example.invalid/video")
        await db.mark_failed("1", "first failure")
        assert await db.reset_for_retry("1") is True
        row = await db.get_task("1")
        assert row["status"] == "queued"
        assert row["retry_count"] == 1
        assert row["file_path"] is None

        await db.mark_failed("1", "second failure")
        assert await db.reset_for_retry("1") is True
        row = await db.get_task("1")
        assert row["retry_count"] == 2

        await db.mark_failed("1", "third failure")
        assert await db.reset_for_retry("1") is True
        row = await db.get_task("1")
        assert row["retry_count"] == 3

        await db.mark_failed("1", "fourth failure")
        assert await db.reset_for_retry("1") is False
        row = await db.get_task("1")
        assert row["status"] == "failed"
        assert row["retry_count"] == 3

    asyncio.run(run())


def test_task_context_removes_registered_directories(tmp_path: Path) -> None:
    async def run() -> None:
        work = tmp_path / ".browser_hls_1"
        work.mkdir()
        (work / "segment.ts").write_bytes(b"x")
        ctx = TaskContext("1")
        ctx.register_temp(work)
        await ctx.cleanup()
        assert not work.exists()

    asyncio.run(run())
