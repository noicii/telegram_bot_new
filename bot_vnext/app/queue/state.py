"""Small in-memory state registry used by the V2 runtime.

The production Telegram layer will persist the same state in SQLite; this
module keeps queue orchestration independent from the Telegram implementation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TaskState:
    task_id: int | str
    status: str = "queued"
    progress: float | None = None
    current_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: int | None = None
    engine: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RuntimeState:
    def __init__(self):
        self.tasks: dict[str, TaskState] = {}
        self.lock = asyncio.Lock()

    async def create(self, task_id: int | str, **metadata: Any) -> TaskState:
        state = TaskState(task_id=task_id, metadata=metadata)
        async with self.lock:
            self.tasks[str(task_id)] = state
        return state

    async def update(self, task_id: int | str, **changes: Any) -> TaskState | None:
        async with self.lock:
            state = self.tasks.get(str(task_id))
            if not state:
                return None
            for key, value in changes.items():
                if hasattr(state, key):
                    setattr(state, key, value)
            state.updated_at = datetime.now(timezone.utc)
            return state

    async def remove(self, task_id: int | str) -> None:
        async with self.lock:
            self.tasks.pop(str(task_id), None)

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for state in self.tasks.values():
            result[state.status] = result.get(state.status, 0) + 1
        return result
