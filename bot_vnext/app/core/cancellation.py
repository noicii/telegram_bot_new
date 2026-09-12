"""Task-scoped cancellation primitives for vNext."""
from __future__ import annotations

import asyncio
from collections import defaultdict


class CancellationRegistry:
    """Owns cancellation state and running asyncio tasks/process handles per task id."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self._tasks: dict[str, set[asyncio.Task]] = defaultdict(set)
        self._processes: dict[str, set[object]] = defaultdict(set)

    def event(self, task_id: str) -> asyncio.Event:
        return self._events[str(task_id)]

    def is_cancelled(self, task_id: str) -> bool:
        return self.event(task_id).is_set()

    def register_task(self, task_id: str, task: asyncio.Task) -> None:
        self._tasks[str(task_id)].add(task)

    def unregister_task(self, task_id: str, task: asyncio.Task) -> None:
        self._tasks[str(task_id)].discard(task)

    def register_process(self, task_id: str, process: object) -> None:
        self._processes[str(task_id)].add(process)

    def unregister_process(self, task_id: str, process: object) -> None:
        self._processes[str(task_id)].discard(process)

    async def cancel(self, task_id: str) -> None:
        key = str(task_id)
        self.event(key).set()

        for task in list(self._tasks.get(key, ())):
            if task is not asyncio.current_task() and not task.done():
                task.cancel()

        for process in list(self._processes.get(key, ())):
            terminate = getattr(process, "terminate", None)
            if terminate:
                try:
                    terminate()
                except Exception:
                    pass

    def clear(self, task_id: str) -> None:
        key = str(task_id)
        self._events.pop(key, None)
        self._tasks.pop(key, None)
        self._processes.pop(key, None)


CANCELLATIONS = CancellationRegistry()
