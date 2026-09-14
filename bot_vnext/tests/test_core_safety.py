from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.core.task import TaskContext
from app.storage.database import Database


class CoreSafetyTests(unittest.TestCase):
    def test_retry_counter_is_preserved(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                db = Database(Path(directory) / "tasks.db")
                await db.init()
                await db.create_task("1", "download", url="https://example.invalid/video")
                await db.mark_failed("1", "first failure")
                self.assertTrue(await db.reset_for_retry("1"))
                row = await db.get_task("1")
                self.assertEqual(row["status"], "queued")
                self.assertEqual(row["retry_count"], 1)
                self.assertIsNone(row["file_path"])

                await db.mark_failed("1", "second failure")
                self.assertTrue(await db.reset_for_retry("1"))
                row = await db.get_task("1")
                self.assertEqual(row["retry_count"], 2)

                await db.mark_failed("1", "third failure")
                self.assertTrue(await db.reset_for_retry("1"))
                row = await db.get_task("1")
                self.assertEqual(row["retry_count"], 3)

                await db.mark_failed("1", "fourth failure")
                self.assertFalse(await db.reset_for_retry("1"))
                row = await db.get_task("1")
                self.assertEqual(row["status"], "failed")
                self.assertEqual(row["retry_count"], 3)

        asyncio.run(run())

    def test_task_context_removes_registered_directories(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / ".browser_hls_1"
                work.mkdir()
                (work / "segment.ts").write_bytes(b"x")
                ctx = TaskContext("1")
                ctx.register_temp(work)
                await ctx.cleanup()
                self.assertFalse(work.exists())

        asyncio.run(run())

    def test_state_transition_is_atomic(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                db = Database(Path(directory) / "tasks.db")
                await db.init()
                await db.create_task("race", "download", url="https://example.invalid/video")
                results = await asyncio.gather(
                    db.transition("race", ("queued",), "downloading"),
                    db.transition("race", ("queued",), "downloading"),
                )
                self.assertEqual(sum(bool(x) for x in results), 1)
                row = await db.get_task("race")
                self.assertEqual(row["status"], "downloading")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
