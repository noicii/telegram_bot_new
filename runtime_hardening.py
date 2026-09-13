"""Runtime hardening for bot-vnext."""
import asyncio
import os
import re
import tempfile
from pathlib import Path


def _clean_full_video_title(title, source_url="", idx=None):
    if not title:
        return ""
    title = str(title).strip()
    title = re.sub(r"\s*[|\-–—]\s*(?:https?://)?(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}\s*$", "", title, flags=re.I).strip()
    low = title.lower().rstrip("/")
    source_low = str(source_url or "").strip().rstrip("/").lower()
    if source_low and low == source_low:
        return ""
    if re.match(r"^(?:https?://|www\.)", low) or re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(?:/.*)?$", low):
        return ""
    from utils import sanitize_filename
    return sanitize_filename(title).strip()


async def _strip_media_metadata(path):
    if not path:
        return path
    src = Path(path)
    if not src.exists() or src.stat().st_size < 1000:
        return path
    fd, tmp_name = tempfile.mkstemp(prefix=f".meta_clean_{src.stem}_", suffix=src.suffix, dir=str(src.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    command = ["ffmpeg", "-y", "-i", str(src), "-map", "0", "-map_metadata", "-1", "-map_chapters", "-1", "-c", "copy", str(tmp)]
    try:
        proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1000:
            raise RuntimeError(stderr.decode("utf-8", errors="ignore")[-500:] if stderr else "metadata cleanup failed")
        os.replace(tmp, src)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    return path


async def _fetch_segment_with_backoff(session, index, url, sem, max_retries=4):
    import aiohttp
    async with sem:
        for attempt in range(max_retries):
            try:
                timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=30)
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status == 200:
                        return index, await resp.read()
                    if resp.status not in (408, 425, 429, 500, 502, 503, 504):
                        return index, None
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        delay = min(8.0, max(0.25, float(retry_after))) if retry_after else 0.5 * (2 ** attempt)
                    except ValueError:
                        delay = 0.5 * (2 ** attempt)
                    await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt + 1 < max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
        return index, None


def _claim_next_queued_task_db():
    from database import get_connection
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM task_queue WHERE status='pending' ORDER BY priority DESC, id ASC LIMIT 1").fetchone()
        if not row:
            conn.commit()
            return None
        task_id = row["id"]
        updated = conn.execute("UPDATE task_queue SET status='processing' WHERE id=? AND status='pending'", (task_id,)).rowcount
        conn.commit()
        return dict(row) if updated == 1 else None


def _patch_cancel_handler():
    import handlers
    import queue_worker
    original_cancel = handlers.cancel_task_db
    if getattr(original_cancel, "_hardened", False):
        return
    def cancel_task_and_runtime(task_id):
        ok = original_cancel(task_id)
        if ok:
            try: handlers.CANCELLED_TASKS.add(str(task_id))
            except Exception: pass
            try: queue_worker.cancel_running_task(task_id)
            except Exception: pass
            try: handlers.cancel_upload_task(task_id)
            except Exception: pass
        return ok
    cancel_task_and_runtime._hardened = True
    handlers.cancel_task_db = cancel_task_and_runtime


def apply():
    import downloader
    import queue_worker
    downloader.PER_TASK_SEGMENT_CONCURRENCY = 16
    downloader.fetch_segment_with_backoff = _fetch_segment_with_backoff
    downloader.clean_full_video_title = _clean_full_video_title
    queue_worker.get_next_queued_task_db = _claim_next_queued_task_db
    original_download = downloader.download_single_item
    if not getattr(original_download, "_hardened", False):
        async def hardened_download(*args, **kwargs):
            result = await original_download(*args, **kwargs)
            if result:
                file_path, title = result
                if file_path and os.path.exists(file_path):
                    await _strip_media_metadata(file_path)
                return file_path, title
            return result
        hardened_download._hardened = True
        downloader.download_single_item = hardened_download
    _patch_cancel_handler()
