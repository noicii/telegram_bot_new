import sqlite3
from datetime import datetime
from config import DB_PATH


def get_connection():
    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                custom_name TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_task_status
            ON task_queue(status)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crawl_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                episode TEXT NOT NULL,
                url TEXT NOT NULL,
                selected INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_crawl_items_chat
            ON crawl_items(chat_id)
            """
        )

        conn.commit()


def add_task_db(url, chat_id, custom_name=None):
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO task_queue
            (url, chat_id, custom_name, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (
                url,
                chat_id,
                custom_name,
                datetime.utcnow().isoformat(),
            ),
        )

        conn.commit()
        return cursor.lastrowid


def get_pending_tasks_db():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM task_queue
            WHERE status IN ('pending', 'processing')
            ORDER BY id ASC
            """
        ).fetchall()

        return [dict(row) for row in rows]


def update_task_status_db(task_id, status):
    allowed_statuses = {
        "pending",
        "processing",
        "completed",
        "failed",
        "cancelled",
    }

    if status not in allowed_statuses:
        raise ValueError(f"Invalid task status: {status}")

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE task_queue
            SET status = ?
            WHERE id = ?
            """,
            (status, task_id),
        )

        conn.commit()


def clear_pending_tasks_db():
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM task_queue
            WHERE status IN ('pending', 'processing')
            """
        )

        conn.commit()


def get_task_db(task_id):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM task_queue
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

        return dict(row) if row else None


init_db()
def clear_crawl_items_db(chat_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM crawl_items WHERE chat_id = ?", (chat_id,))
        conn.commit()

def add_crawl_item_db(chat_id, title, episode, url):
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO crawl_items (chat_id, title, episode, url, selected, created_at) VALUES (?, ?, ?, ?, 0, ?)",
            (chat_id, title, episode, url, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return cursor.lastrowid
def get_crawl_items_db(chat_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM crawl_items WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
        return [dict(row) for row in rows]

def get_crawl_item_db(item_id, chat_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM crawl_items WHERE id = ? AND chat_id = ?",
            (item_id, chat_id),
        ).fetchone()
        return dict(row) if row else None
def toggle_crawl_item_db(item_id, chat_id):
    with get_connection() as conn:
        conn.execute(
            "UPDATE crawl_items SET selected = CASE selected WHEN 1 THEN 0 ELSE 1 END WHERE id = ? AND chat_id = ?",
            (item_id, chat_id),
        )
        conn.commit()

def select_all_crawl_items_db(chat_id):
    with get_connection() as conn:
        conn.execute(
            "UPDATE crawl_items SET selected = 1 WHERE chat_id = ?",
            (chat_id,),
        )
        conn.commit()

def clear_selected_crawl_items_db(chat_id):
    with get_connection() as conn:
        conn.execute(
            "UPDATE crawl_items SET selected = 0 WHERE chat_id = ?",
            (chat_id,),
        )
        conn.commit()
def get_selected_crawl_items_db(chat_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM crawl_items WHERE chat_id = ? AND selected = 1 ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
        return [dict(row) for row in rows]
