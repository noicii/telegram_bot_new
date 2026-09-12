import asyncio
import logging

from database import (
    get_next_queued_task_db,
    update_task_status_db,
    reset_processing_tasks_db,
)
from config import MAX_CONCURRENT_DOWNLOADS, SETTINGS

logger = logging.getLogger(__name__)

_queue_worker_task = None
_queue_worker_running = False
_worker_tasks = set()
RUNNING_TASKS = {}
MAX_WORKERS = MAX_CONCURRENT_DOWNLOADS


async def get_status_message(client, task):
    chat_id = task.get("status_chat_id")
    message_id = task.get("status_message_id")

    if not chat_id or not message_id:
        return None

    try:
        return await client.get_messages(chat_id, message_id)
    except Exception as exc:
        logger.warning(
            "Could not fetch status message for task %s: %s",
            task.get("id"),
            exc,
        )
        return None


async def process_one_queue_task(client, task):
    task_id = task["id"]

    try:
        from handlers import process_queue_task

        status_message = await get_status_message(client, task)

        logger.info("Queue worker started task %s", task_id)

        result = await process_queue_task(
            client,
            task,
            status_message,
        )

        logger.info("Queue worker finished task %s", task_id)

    except asyncio.CancelledError:
        # Explicitly cancelled tasks are already marked "cancelled".
        # If cancellation came from worker shutdown, return the task to
        # pending so it can resume safely after the next bot restart.
        from database import get_task_db

        current_task = get_task_db(task_id)
        if current_task and current_task.get("status") == "processing":
            update_task_status_db(task_id, "pending")
        raise

    except Exception:
        logger.exception("Queue task %s failed", task_id)

        from database import get_task_db

        current_task = get_task_db(task_id)
        if not current_task or current_task.get("status") != "cancelled":
            update_task_status_db(task_id, "failed")


async def queue_worker(client):
    global _queue_worker_running

    _queue_worker_running = True

    # Tasks left in "processing" after a crash/restart must be resumed.
    reset_processing_tasks_db()

    logger.info("Queue worker started with %s workers", MAX_WORKERS)

    try:
        while _queue_worker_running:
            if SETTINGS.get("queue_paused", False):
                await asyncio.sleep(0.5)
                continue

            while len(_worker_tasks) < MAX_WORKERS:
                task = get_next_queued_task_db()

                if not task:
                    break

                task_id = task["id"]

                # "Move to Next" priority is one-time.
                # Once the task is picked, return it to normal priority.
                if task.get("priority", 0):
                    from database import update_task_priority_db
                    update_task_priority_db(task_id, 0)

                update_task_status_db(task_id, "processing")

                worker_task = asyncio.create_task(
                    process_one_queue_task(client, task)
                )

                _worker_tasks.add(worker_task)
                RUNNING_TASKS[task_id] = worker_task

                def _task_done(done_task, tid=task_id):
                    _worker_tasks.discard(done_task)
                    RUNNING_TASKS.pop(tid, None)

                worker_task.add_done_callback(_task_done)

            if not _worker_tasks:
                await asyncio.sleep(2)
            else:
                await asyncio.sleep(0.5)

    except asyncio.CancelledError:
        raise

    finally:
        _queue_worker_running = False

        if _worker_tasks:
            logger.info(
                "Stopping %s active queue task(s)",
                len(_worker_tasks),
            )

            for task in list(_worker_tasks):
                task.cancel()

            await asyncio.gather(
                *_worker_tasks,
                return_exceptions=True,
            )

            _worker_tasks.clear()

        try:
            from handlers import cancel_all_upload_tasks
            n = cancel_all_upload_tasks()
            if n:
                logger.info("Stopping %s upload task(s)", n)
        except Exception:
            logger.exception("Could not stop upload tasks")

        logger.info("Queue worker stopped")



def cancel_running_task(task_id):
    task = RUNNING_TASKS.get(int(task_id))
    if task and not task.done():
        task.cancel()
        return True

    try:
        from handlers import cancel_upload_task
        return bool(cancel_upload_task(task_id))
    except Exception:
        logger.exception("Could not cancel upload task %s", task_id)
        return False

def start_queue_worker(client):
    global _queue_worker_task

    if _queue_worker_task and not _queue_worker_task.done():
        return _queue_worker_task

    _queue_worker_task = asyncio.create_task(
        queue_worker(client)
    )

    return _queue_worker_task


async def stop_queue_worker():
    global _queue_worker_task, _queue_worker_running

    _queue_worker_running = False

    if _queue_worker_task and not _queue_worker_task.done():
        _queue_worker_task.cancel()

        try:
            await _queue_worker_task
        except asyncio.CancelledError:
            pass

    _queue_worker_task = None
