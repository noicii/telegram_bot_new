"""Adaptive Telegram API gate for Bot V2.

Telegram API pressure is kept separate from download/upload workers.
Dashboard updates are coalesced by chat so only the newest state is sent.
There is no fixed three-second dashboard refresh rule.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pyrogram.errors import FloodWait

logger = logging.getLogger(__name__)
T = TypeVar("T")


class TelegramFloodGate:
    """Adaptive, cancellation-safe gate for Telegram API operations."""

    def __init__(self, *, upload_max: int = 4) -> None:
        self.upload_min = 1
        self.upload_max = max(1, int(upload_max))
        self.upload_limit = self.upload_max
        self.upload_in_flight = 0
        self.upload_cooldown_until = 0.0
        self.upload_condition = asyncio.Condition()
        self.upload_success_streak = 0

        # Dashboard is deliberately independent from upload concurrency.
        # It starts responsive and only backs off when Telegram asks us to.
        self.dashboard_min_interval = 0.75
        self.dashboard_cooldown_until = 0.0
        self.dashboard_last_sent: dict[str, float] = {}
        self.dashboard_pending: dict[str, Callable[[], Awaitable[Any]]] = {}
        self.dashboard_tasks: dict[str, asyncio.Task] = {}
        self.dashboard_lock = asyncio.Lock()

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    async def acquire_upload(self) -> None:
        async with self.upload_condition:
            while True:
                now = self._now()
                cooldown = self.upload_cooldown_until - now
                if cooldown > 0:
                    self.upload_condition.release()
                    try:
                        await asyncio.sleep(cooldown)
                    finally:
                        await self.upload_condition.acquire()
                    continue
                if self.upload_in_flight < self.upload_limit:
                    self.upload_in_flight += 1
                    return
                await self.upload_condition.wait()

    async def release_upload(self) -> None:
        async with self.upload_condition:
            self.upload_in_flight = max(0, self.upload_in_flight - 1)
            self.upload_condition.notify_all()

    async def note_upload_success(self) -> None:
        async with self.upload_condition:
            self.upload_success_streak += 1
            if self.upload_success_streak >= 4 and self.upload_limit < self.upload_max:
                self.upload_limit += 1
                self.upload_success_streak = 0
                logger.info("telegram gate: upload concurrency increased to %s", self.upload_limit)
            self.upload_condition.notify_all()

    async def note_upload_flood(self, seconds: int | float) -> None:
        wait = max(0.0, float(seconds or 0))
        async with self.upload_condition:
            self.upload_success_streak = 0
            self.upload_limit = max(self.upload_min, min(self.upload_limit - 1, self.upload_max))
            self.upload_cooldown_until = max(self.upload_cooldown_until, self._now() + wait)
            logger.warning(
                "telegram gate: upload FloodWait=%ss; pausing new upload sends; concurrency=%s",
                wait, self.upload_limit,
            )
            self.upload_condition.notify_all()

    async def run_upload(self, operation: Callable[[], Awaitable[T]]) -> T:
        while True:
            await self.acquire_upload()
            try:
                result = await operation()
            except FloodWait as exc:
                await self.note_upload_flood(exc.value)
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                raise
            else:
                await self.note_upload_success()
                return result
            finally:
                await self.release_upload()

    async def publish_dashboard(
        self,
        key: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> None:
        """Queue the newest dashboard render for a chat/message key."""
        async with self.dashboard_lock:
            self.dashboard_pending[key] = operation
            task = self.dashboard_tasks.get(key)
            if task and not task.done():
                return
            self.dashboard_tasks[key] = asyncio.create_task(
                self._dashboard_worker(key), name=f"telegram-dashboard-{key}"
            )

    async def _dashboard_worker(self, key: str) -> None:
        try:
            while True:
                async with self.dashboard_lock:
                    operation = self.dashboard_pending.pop(key, None)
                    if operation is None:
                        return
                    last_sent = self.dashboard_last_sent.get(key, 0.0)
                    cooldown = max(
                        self.dashboard_cooldown_until - self._now(),
                        self.dashboard_min_interval - (self._now() - last_sent),
                    )
                if cooldown > 0:
                    await asyncio.sleep(cooldown)

                try:
                    await operation()
                except FloodWait as exc:
                    wait = max(0.0, float(exc.value or 0))
                    async with self.dashboard_lock:
                        self.dashboard_cooldown_until = max(
                            self.dashboard_cooldown_until, self._now() + wait
                        )
                        self.dashboard_min_interval = min(
                            5.0, max(0.75, self.dashboard_min_interval * 1.35)
                        )
                        # Requeue the same latest operation. If a newer state
                        # arrived meanwhile, publish_dashboard already replaced it.
                        self.dashboard_pending.setdefault(key, operation)
                    logger.warning(
                        "telegram gate: dashboard FloodWait=%ss; adaptive interval=%.2fs",
                        wait, self.dashboard_min_interval,
                    )
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.debug("dashboard Telegram update failed: %s", exc)
                    # Do not spin on a failed API call. A later state change
                    # will schedule the next render.
                    return
                else:
                    async with self.dashboard_lock:
                        self.dashboard_last_sent[key] = self._now()
                        self.dashboard_min_interval = max(
                            0.75, self.dashboard_min_interval * 0.95
                        )
        finally:
            async with self.dashboard_lock:
                self.dashboard_tasks.pop(key, None)
                if key in self.dashboard_pending:
                    self.dashboard_tasks[key] = asyncio.create_task(
                        self._dashboard_worker(key), name=f"telegram-dashboard-{key}"
                    )

    async def close(self) -> None:
        async with self.dashboard_lock:
            tasks = list(self.dashboard_tasks.values())
            self.dashboard_tasks.clear()
            self.dashboard_pending.clear()
            self.dashboard_last_sent.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self.upload_condition:
            self.upload_condition.notify_all()
