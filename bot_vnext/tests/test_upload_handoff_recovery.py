import asyncio
import tempfile
import unittest
from pathlib import Path

from app.storage.database import Database


class UploadHandoffRecoveryTests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_download_handoff_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "tasks.db")

            async def scenario():
                await db.init()
                await db.create_task("download-1", "download", url="https://example.test/video")
                self.assertTrue(await db.mark_downloading("download-1"))
                self.assertTrue(
                    await db.mark_download_handed_to_upload(
                        "download-1", "/tmp/completed-video.mp4"
                    )
                )

                task = await db.get_task("download-1")
                self.assertEqual(task["task_type"], "upload")
                self.assertEqual(task["status"], "queued")
                self.assertEqual(task["file_path"], "/tmp/completed-video.mp4")

                await db.recover_after_crash()
                recovered = await db.get_task("download-1")
                self.assertEqual(recovered["task_type"], "upload")
                self.assertEqual(recovered["status"], "queued")

            self.run_async(scenario())

    def test_active_download_is_not_converted_to_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "tasks.db")

            async def scenario():
                await db.init()
                await db.create_task("download-2", "download", url="https://example.test/video")
                self.assertTrue(await db.mark_downloading("download-2"))

                await db.recover_after_crash()
                task = await db.get_task("download-2")
                self.assertEqual(task["task_type"], "download")
                self.assertEqual(task["status"], "failed")
                self.assertIn("Interrupted by bot restart", task["error"])

            self.run_async(scenario())

    def test_upload_in_progress_is_failed_for_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "tasks.db")

            async def scenario():
                await db.init()
                await db.create_task("upload-1", "upload", file_path="/tmp/video.mp4")
                self.assertTrue(await db.mark_uploading("upload-1"))

                await db.recover_after_crash()
                task = await db.get_task("upload-1")
                self.assertEqual(task["task_type"], "upload")
                self.assertEqual(task["status"], "failed")
                self.assertIn("Interrupted by bot restart", task["error"])

            self.run_async(scenario())


if __name__ == "__main__":
    unittest.main()
