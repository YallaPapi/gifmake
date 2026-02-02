"""SQLite database for scheduler queue, history, and errors."""

import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Optional


class Database:
    def __init__(self, db_path: str = "scheduler.db"):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY,
                account_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY,
                account_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                redgifs_url TEXT,
                status TEXT NOT NULL,
                error_message TEXT,
                completed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY,
                queue_id INTEGER,
                account_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                error_type TEXT NOT NULL,
                error_message TEXT NOT NULL,
                occurred_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status, scheduled_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_account ON queue(account_name, status)")

        self.conn.commit()

    # Queue operations
    def add_to_queue(self, account_name: str, file_path: str, scheduled_at: datetime) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO queue (account_name, file_path, scheduled_at) VALUES (?, ?, ?)",
            (account_name, str(file_path), scheduled_at.isoformat())
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_next_pending(self, account_name: str) -> Optional[dict]:
        cursor = self.conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            """SELECT * FROM queue
               WHERE account_name = ? AND status = 'pending' AND scheduled_at <= ?
               ORDER BY scheduled_at ASC LIMIT 1""",
            (account_name, now)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_status(self, queue_id: int, status: str):
        cursor = self.conn.cursor()
        cursor.execute("UPDATE queue SET status = ? WHERE id = ?", (status, queue_id))
        self.conn.commit()

    def increment_retry(self, queue_id: int, next_scheduled_at: datetime):
        cursor = self.conn.cursor()
        cursor.execute(
            """UPDATE queue SET retry_count = retry_count + 1,
               scheduled_at = ?, status = 'pending' WHERE id = ?""",
            (next_scheduled_at.isoformat(), queue_id)
        )
        self.conn.commit()

    def get_retry_count(self, queue_id: int) -> int:
        cursor = self.conn.cursor()
        cursor.execute("SELECT retry_count FROM queue WHERE id = ?", (queue_id,))
        row = cursor.fetchone()
        return row["retry_count"] if row else 0

    def get_pending_count(self, account_name: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM queue WHERE account_name = ? AND status = 'pending'",
            (account_name,)
        )
        return cursor.fetchone()["cnt"]

    def file_in_queue(self, account_name: str, file_path: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT 1 FROM queue WHERE account_name = ? AND file_path = ? AND status IN ('pending', 'processing')",
            (account_name, str(file_path))
        )
        return cursor.fetchone() is not None

    # History operations
    def add_to_history(self, account_name: str, file_path: str, status: str,
                       redgifs_url: str = None, error_message: str = None):
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO history (account_name, file_path, redgifs_url, status, error_message)
               VALUES (?, ?, ?, ?, ?)""",
            (account_name, str(file_path), redgifs_url, status, error_message)
        )
        self.conn.commit()

    def get_uploads_today(self, account_name: str) -> int:
        cursor = self.conn.cursor()
        today = date.today().isoformat()
        cursor.execute(
            """SELECT COUNT(*) as cnt FROM history
               WHERE account_name = ? AND status = 'success' AND date(completed_at) = ?""",
            (account_name, today)
        )
        return cursor.fetchone()["cnt"]

    def get_history(self, account_name: str = None, limit: int = 50) -> list:
        cursor = self.conn.cursor()
        if account_name:
            cursor.execute(
                "SELECT * FROM history WHERE account_name = ? ORDER BY completed_at DESC LIMIT ?",
                (account_name, limit)
            )
        else:
            cursor.execute("SELECT * FROM history ORDER BY completed_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]

    # Error operations
    def log_error(self, queue_id: int, account_name: str, file_path: str,
                  error_type: str, error_message: str):
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO errors (queue_id, account_name, file_path, error_type, error_message)
               VALUES (?, ?, ?, ?, ?)""",
            (queue_id, account_name, str(file_path), error_type, error_message)
        )
        self.conn.commit()

    def get_errors(self, limit: int = 20) -> list:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM errors ORDER BY occurred_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        self.conn.close()
