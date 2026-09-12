from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).resolve().parents[3] / "bot_vnext.db"


class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self._lock = asyncio.Lock()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def init(self):
        async with self._lock:
            await asyncio.to_thread(self._init_sync)

    def _init_sync(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                url TEXT,
                file_path TEXT,
                title TEXT,
                caption TEXT,
                thumbnail TEXT,
                chat_id INTEGER,
                message_id INTEGER,
                preset TEXT,
                mode TEXT,
                source TEXT,
                provider TEXT,
                resolution TEXT,
                batch_id TEXT,
                batch_total INTEGER DEFAULT 0,
                priority INTEGER DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                progress REAL DEFAULT 0,
                speed REAL DEFAULT 0,
                eta INTEGER DEFAULT 0,
                error TEXT,
                metadata TEXT,
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority DESC, created_at ASC);
            CREATE INDEX IF NOT EXISTS idx_tasks_batch ON tasks(batch_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_type_status ON tasks(task_type, status);
            """)
            conn.commit()
        finally:
            conn.close()

    async def create_task(self, task_id: str, task_type: str, **kwargs):
        await asyncio.to_thread(self._create_task_sync, task_id, task_type, kwargs)

    def _create_task_sync(self, task_id, task_type, data):
        now = time.time()
        metadata = data.get("metadata") or {}
        if isinstance(metadata, dict):
            metadata = json.dumps(metadata, ensure_ascii=False)
        conn = self._connect()
        try:
            conn.execute("""
                INSERT INTO tasks (
                    id, task_type, status, url, file_path, title, caption, thumbnail,
                    chat_id, message_id, preset, mode, source, provider, resolution,
                    batch_id, batch_total, priority, retry_count, max_retries, metadata,
                    created_at, updated_at
                ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """, (
                task_id, task_type, data.get("url"), data.get("file_path"),
                data.get("title"), data.get("caption"), data.get("thumbnail"),
                data.get("chat_id"), data.get("message_id"), data.get("preset"),
                data.get("mode"), data.get("source"), data.get("provider"),
                data.get("resolution"), data.get("batch_id"), data.get("batch_total", 0),
                data.get("priority", 0), data.get("max_retries", 3), metadata, now, now
            ))
            conn.commit()
        finally:
            conn.close()

    async def update_task(self, task_id: str, **fields):
        allowed = {
            "status", "url", "file_path", "title", "caption", "thumbnail",
            "chat_id", "message_id", "preset", "mode", "source", "provider",
            "resolution", "batch_id", "batch_total", "priority", "retry_count",
            "max_retries", "progress", "speed", "eta", "error", "metadata",
            "started_at", "completed_at"
        }
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        if isinstance(fields.get("metadata"), dict):
            fields["metadata"] = json.dumps(fields["metadata"], ensure_ascii=False)
        fields["updated_at"] = time.time()
        await asyncio.to_thread(self._update_sync, task_id, fields)

    def _update_sync(self, task_id, fields):
        columns = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [task_id]
        conn = self._connect()
        try:
            conn.execute(f"UPDATE tasks SET {columns} WHERE id = ?", values)
            conn.commit()
        finally:
            conn.close()

    async def get_task(self, task_id: str):
        return await asyncio.to_thread(self._get_sync, task_id)

    def _get_sync(self, task_id):
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self._row(row) if row else None
        finally:
            conn.close()

    async def get_recoverable_tasks(self):
        return await asyncio.to_thread(self._get_status_sync, ("queued", "downloading", "uploading"))

    async def get_queued_tasks(self):
        return await asyncio.to_thread(self._get_status_sync, ("queued",))

    def _get_status_sync(self, statuses):
        marks = ",".join("?" for _ in statuses)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE status IN ({marks}) ORDER BY priority DESC, created_at ASC",
                statuses).fetchall()
            return [self._row(r) for r in rows]
        finally:
            conn.close()

    async def recover_after_crash(self):
        await asyncio.to_thread(self._recover_sync)

    def _recover_sync(self):
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("""
                UPDATE tasks
                SET status = 'queued', error = NULL, updated_at = ?
                WHERE status IN ('downloading', 'uploading')
            """, (now,))
            conn.commit()
        finally:
            conn.close()

    async def mark_downloading(self, task_id):
        await self.update_task(task_id, status="downloading", started_at=time.time(), error=None)

    async def mark_uploading(self, task_id):
        await self.update_task(task_id, status="uploading", error=None)

    async def mark_completed(self, task_id):
        await self.update_task(task_id, status="completed", progress=100, completed_at=time.time(), error=None)

    async def mark_failed(self, task_id, error):
        await self.update_task(task_id, status="failed", error=str(error))

    async def mark_cancelled(self, task_id):
        await self.update_task(task_id, status="cancelled")

    async def increment_retry(self, task_id):
        task = await self.get_task(task_id)
        if not task:
            return False
        count = int(task.get("retry_count") or 0) + 1
        maximum = int(task.get("max_retries") or 3)
        if count > maximum:
            await self.mark_failed(task_id, f"Maximum retries exceeded ({maximum})")
            return False
        await self.update_task(task_id, retry_count=count, status="queued", error=None)
        return True

    async def update_progress(self, task_id, *, progress, speed=0, eta=0):
        await self.update_task(
            task_id,
            progress=max(0.0, min(100.0, float(progress))),
            speed=max(0.0, float(speed)),
            eta=max(0, int(eta)),
        )

    async def counts(self):
        return await asyncio.to_thread(self._counts_sync)

    def _counts_sync(self):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT task_type, status, COUNT(*) AS count FROM tasks GROUP BY task_type, status"
            ).fetchall()
            result = {}
            for row in rows:
                result.setdefault(row["task_type"], {})[row["status"]] = row["count"]
            return result
        finally:
            conn.close()

    @staticmethod
    def _row(row):
        data = dict(row)
        try:
            data["metadata"] = json.loads(data.get("metadata") or "{}")
        except Exception:
            data["metadata"] = {}
        return data
