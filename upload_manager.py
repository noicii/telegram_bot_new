import asyncio
import logging

from config import MAX_CONCURRENT_UPLOADS, MAX_CONCURRENT_DOWNLOADS

logger = logging.getLogger(__name__)

_queue = None
_workers = []
_pipeline = None
_running = False
_jobs = {}


def _compact_status_text():
    from database import get_queue_counts_db, get_queue_tasks_db
    from downloader import LIVE_TASKS

    counts = get_queue_counts_db()
    downloading = 0
    uploading = 0

    for task in get_queue_tasks_db():
        if task.get("status") != "processing":
            continue
        live = LIVE_TASKS.get(str(task.get("id")), {})
        operation = str(live.get("operation") or "").lower()
        if "upload" in operation:
            uploading += 1
        else:
            downloading += 1

    return "\n".join([
        "📊 **QUEUE STATUS**",
        "",
        f"⬇️ Downloads: {downloading}/{MAX_CONCURRENT_DOWNLOADS}",
        f"⬆️ Uploads: {uploading}/{MAX_CONCURRENT_UPLOADS}",
        "",
        f"⏳ Queue: {int(counts.get('pending', 0))}",
        f"✅ Done: {int(counts.get('completed', 0))}",
        f"❌ Failed: {int(counts.get('failed', 0))}",
    ])


# Handlers still contains the previous verbose status builder. Install the
# compact dashboard centrally so /status and the status button cannot regress.
try:
    import handlers as _handlers
    _handlers.build_status_text = _compact_status_text
except Exception:
    logger.exception("Could not install compact status dashboard")


async def start(pipeline):
    global _queue, _pipeline, _running, _workers
    if _running:
        return
    _pipeline = pipeline
    _queue = asyncio.Queue()
    _running = True
    _workers = [asyncio.create_task(_worker(i + 1)) for i in range(MAX_CONCURRENT_UPLOADS)]
    logger.info("Upload pool started with %s workers", MAX_CONCURRENT_UPLOADS)


async def _worker(worker_id):
    while _running:
        try:
            job = await _queue.get()
        except asyncio.CancelledError:
            break

        task_id = job["task_id"]
        future = job["future"]
        client = job["args"][0]
        try:
            if future.cancelled():
                continue

            _jobs[task_id] = asyncio.current_task()

            try:
                from handlers import update_batch_summary_message
                from database import get_task_db
                from downloader import set_live_task

                task = get_task_db(task_id)
                if task and task.get("status") == "processing":
                    set_live_task(
                        task_id,
                        operation="Telegram upload",
                        percent=0,
                        current_mb=0,
                        speed_mb=0,
                        eta="—",
                    )
                    await update_batch_summary_message(client, task)
            except Exception:
                logger.exception("Could not refresh upload dashboard for task %s", task_id)

            await _pipeline(*job["args"])
            if not future.done():
                future.set_result(True)
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        except Exception as exc:
            logger.exception("Upload worker %s failed task %s", worker_id, task_id)
            if not future.done():
                future.set_exception(exc)
        finally:
            _jobs.pop(task_id, None)
            _queue.task_done()


async def enqueue(task_id, args):
    if not _running or _queue is None:
        raise RuntimeError("Upload pool is not running")
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await _queue.put({"task_id": int(task_id), "args": args, "future": future})
    try:
        return await future
    except asyncio.CancelledError:
        if not future.done():
            future.cancel()
        raise


async def stop():
    global _running, _queue, _workers, _pipeline
    _running = False
    workers = list(_workers)
    for worker in workers:
        worker.cancel()
    if workers:
        await asyncio.gather(*workers, return_exceptions=True)
    _workers.clear()
    _jobs.clear()
    _queue = None
    _pipeline = None
    logger.info("Upload pool stopped")


def active_count():
    return len(_jobs)
