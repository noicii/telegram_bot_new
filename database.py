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
                created_at TEXT NOT NULL,
                status_chat_id INTEGER,
                status_message_id INTEGER
            )
            """
        )

        # Migrate older task_queue databases without deleting existing data.
        task_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(task_queue)").fetchall()
        }
        if "preset" not in task_columns:
            conn.execute(
                "ALTER TABLE task_queue ADD COLUMN preset TEXT NOT NULL DEFAULT 'custom'"
            )

        if "title" not in task_columns:
            conn.execute(
                "ALTER TABLE task_queue ADD COLUMN title TEXT"
            )

        if "retry_of" not in task_columns:
            conn.execute(
                "ALTER TABLE task_queue ADD COLUMN retry_of INTEGER"
            )

        if "priority" not in task_columns:
            conn.execute(
                "ALTER TABLE task_queue ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"
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

        # Migrate older crawl_items databases without deleting existing data.
        existing_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(crawl_items)").fetchall()
        }

        for column, definition in (
            ("source", "TEXT"),
            ("resolution", "TEXT"),
            ("source_url", "TEXT"),
        ):
            if column not in existing_columns:
                conn.execute(
                    f"ALTER TABLE crawl_items ADD COLUMN {column} {definition}"
                )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_crawl_items_chat
            ON crawl_items(chat_id)
            """
        )

        conn.commit()


def add_task_db(
    url,
    chat_id,
    custom_name=None,
    status_chat_id=None,
    status_message_id=None,
    preset="custom",
    title=None,
):
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO task_queue
            (
                url,
                chat_id,
                custom_name,
                title,
                preset,
                status,
                created_at,
                status_chat_id,
                status_message_id
            )
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                url,
                chat_id,
                custom_name,
                title,
                preset,
                datetime.utcnow().isoformat(),
                status_chat_id,
                status_message_id,
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


def get_next_queued_task_db():
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM task_queue
            WHERE status = 'pending'
            ORDER BY priority DESC, id ASC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None


def update_task_priority_db(task_id, priority=0):
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE task_queue
            SET priority = ?
            WHERE id = ?
            """,
            (int(priority), task_id),
        )
        return True


def move_task_next_db(task_id):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, status
            FROM task_queue
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

        if not row:
            return False

        if row["status"] != "pending":
            return False

        conn.execute(
            """
            UPDATE task_queue
            SET priority = 1
            WHERE id = ?
            """,
            (task_id,),
        )
        return True


def get_queue_counts_db():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM task_queue
            WHERE status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')
            GROUP BY status
            """
        ).fetchall()

    counts = {
        "pending": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }

    for row in rows:
        counts[row["status"]] = row["count"]

    return counts


def reset_processing_tasks_db():
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE task_queue
            SET status = 'pending'
            WHERE status = 'processing'
            """
        )
        conn.commit()


def cancel_pending_tasks_db():
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE task_queue
            SET status = 'cancelled'
            WHERE status = 'pending'
            """
        )
        conn.commit()


def cancel_tasks_by_status_message_db(status_chat_id, status_message_id):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM task_queue
            WHERE status_chat_id = ?
              AND status_message_id = ?
              AND status IN ('pending', 'processing')
            """,
            (status_chat_id, status_message_id),
        ).fetchall()

        task_ids = [row["id"] for row in rows]

        conn.execute(
            """
            UPDATE task_queue
            SET status = 'cancelled'
            WHERE status_chat_id = ?
              AND status_message_id = ?
              AND status IN ('pending', 'processing')
            """,
            (status_chat_id, status_message_id),
        )
        conn.commit()

        return task_ids



def get_pending_only_tasks_db():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM task_queue
            WHERE status = 'pending'
            ORDER BY id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def cancel_task_db(task_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM task_queue WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row or row["status"] not in ("pending", "processing"):
            return False
        conn.execute(
            "UPDATE task_queue SET status = 'cancelled' WHERE id = ?",
            (task_id,),
        )
        conn.commit()
        return True



def retry_failed_task_db(task_id):
    """Create a fresh pending task from one failed/cancelled task."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT url, chat_id, custom_name, title, status_chat_id, status_message_id, preset
            FROM task_queue
            WHERE id = ?
              AND status IN ('failed', 'cancelled')
              AND NOT EXISTS (
                  SELECT 1
                  FROM task_queue AS retry_task
                  WHERE retry_task.retry_of = task_queue.id
                    AND retry_task.status IN ('pending', 'processing')
              )
            """,
            (task_id,),
        ).fetchone()

        if not row:
            return None

        cursor = conn.execute(
            """
            INSERT INTO task_queue
                (url, chat_id, custom_name, title, preset, status,
                 created_at, status_chat_id, status_message_id, retry_of)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                row["url"],
                row["chat_id"],
                row["custom_name"],
                row["title"],
                row["preset"] or "custom",
                datetime.utcnow().isoformat(),
                row["status_chat_id"],
                row["status_message_id"],
                task_id,
            ),
        )
        conn.commit()
        return cursor.lastrowid


def retry_all_failed_tasks_db():
    """Create fresh pending tasks for every failed/cancelled task."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, url, chat_id, custom_name, title, status_chat_id,
                   status_message_id, preset
            FROM task_queue
            WHERE status IN ('failed', 'cancelled')
              AND NOT EXISTS (
                  SELECT 1
                  FROM task_queue AS retry_task
                  WHERE retry_task.retry_of = task_queue.id
                    AND retry_task.status IN ('pending', 'processing')
              )
            ORDER BY id ASC
            """
        ).fetchall()

        new_ids = []

        for row in rows:
            cursor = conn.execute(
                """
                INSERT INTO task_queue
                    (url, chat_id, custom_name, title, preset, status,
                     created_at, status_chat_id, status_message_id, retry_of)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    row["url"],
                    row["chat_id"],
                    row["custom_name"],
                    row["title"],
                    row["preset"] or "custom",
                    datetime.utcnow().isoformat(),
                    row["status_chat_id"],
                    row["status_message_id"],
                    row["id"],
                ),
            )
            new_ids.append(cursor.lastrowid)

        conn.commit()
        return new_ids

def clear_summary_history_db():
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM task_queue WHERE status IN ('completed', 'failed', 'cancelled')"
        )
        conn.commit()
        return cursor.rowcount

def get_queue_tasks_db(limit=None):
    query = """
        SELECT * FROM task_queue
        ORDER BY id ASC
    """
    params = ()

    if limit is not None:
        query += " LIMIT ?"
        params = (int(limit),)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
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


def update_task_title_db(task_id, title):
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE task_queue
            SET title = ?
            WHERE id = ?
            """,
            (title, task_id),
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


def update_task_status_message_db(task_id, status_chat_id, status_message_id):
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE task_queue
            SET status_chat_id = ?, status_message_id = ?
            WHERE id = ?
            """,
            (
                status_chat_id,
                status_message_id,
                task_id,
            ),
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

def add_crawl_item_db(
    chat_id,
    title,
    episode,
    url,
    source=None,
    resolution=None,
    source_url=None,
):
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO crawl_items
            (chat_id, title, episode, url, selected, created_at, source, resolution, source_url)
            VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                chat_id,
                title,
                episode,
                url,
                datetime.utcnow().isoformat(),
                source or "Unknown",
                resolution or "Unknown",
                source_url or url,
            ),
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
