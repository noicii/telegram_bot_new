"""Core task state and cancellation primitives for Bot V2."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class TaskCancelled(Exception):
    """Raised when a task is intentionally cancelled by its owner."""


@dataclass
class TaskContext:
    task_id: int | str
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    processes: set[asyncio.subprocess.Process] = field(default_factory=set)
    temp_paths: set[Path] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)
    _closed: bool = False

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.is_cancelled():
            raise TaskCancelled(f"Task {self.task_id} cancelled")

    def register_process(self, process: asyncio.subprocess.Process) -> None:
        self.processes.add(process)

    def unregister_process(self, process: asyncio.subprocess.Process) -> None:
        self.processes.discard(process)

    def register_temp(self, path: str | Path) -> Path:
        p = Path(path)
        self.temp_paths.add(p)
        return p

    async def cancel(self) -> None:
        """Signal cancellation and terminate all child processes for this task."""
        self.cancel_event.set()
        await self.terminate_processes()

    async def terminate_processes(self) -> None:
        processes = list(self.processes)
        for process in processes:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        if processes:
            await asyncio.gather(
                *(self._wait_or_kill(p) for p in processes),
                return_exceptions=True,
            )

    @staticmethod
    async def _wait_or_kill(process: asyncio.subprocess.Process) -> None:
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                await process.wait()
            except Exception:
                pass

    async def cleanup(self) -> None:
        """Remove registered temporary artifacts after the task has stopped."""
        if self._closed:
            return
        self._closed = True
        await self.terminate_processes()
        for path in list(self.temp_paths):
            try:
                if path.is_file() or path.is_symlink():
                    path.unlink(missing_ok=True)
            except OSError:
                pass
        self.temp_paths.clear()
        self.processes.clear()
