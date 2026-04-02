"""SQLite state tracker for processed S3 objects."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class StateDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_objects (
                s3_key TEXT NOT NULL,
                etag TEXT NOT NULL,
                size INTEGER,
                discovered_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                vm_name TEXT,
                started_at TEXT,
                completed_at TEXT,
                result_path TEXT,
                error_message TEXT,
                PRIMARY KEY (s3_key, etag)
            )
        """)
        self._conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def is_processed(self, s3_key: str, etag: str) -> bool:
        row = self._conn.execute(
            "SELECT status FROM processed_objects WHERE s3_key = ? AND etag = ?",
            (s3_key, etag),
        ).fetchone()
        return row is not None

    def mark_pending(self, s3_key: str, etag: str, size: int | None = None):
        self._conn.execute(
            """INSERT OR IGNORE INTO processed_objects
               (s3_key, etag, size, discovered_at, status)
               VALUES (?, ?, ?, ?, 'pending')""",
            (s3_key, etag, size, self._now()),
        )
        self._conn.commit()

    def mark_running(self, s3_key: str, etag: str, vm_name: str):
        self._conn.execute(
            """UPDATE processed_objects
               SET status = 'running', vm_name = ?, started_at = ?
               WHERE s3_key = ? AND etag = ?""",
            (vm_name, self._now(), s3_key, etag),
        )
        self._conn.commit()

    def mark_completed(self, s3_key: str, etag: str, result_path: str):
        self._conn.execute(
            """UPDATE processed_objects
               SET status = 'completed', completed_at = ?, result_path = ?
               WHERE s3_key = ? AND etag = ?""",
            (self._now(), result_path, s3_key, etag),
        )
        self._conn.commit()

    def mark_failed(self, s3_key: str, etag: str, error_message: str):
        self._conn.execute(
            """UPDATE processed_objects
               SET status = 'failed', completed_at = ?, error_message = ?
               WHERE s3_key = ? AND etag = ?""",
            (self._now(), error_message, s3_key, etag),
        )
        self._conn.commit()

    def get_running(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM processed_objects WHERE status = 'running'"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_orphaned_vms(self, timeout_seconds: int) -> list[dict]:
        """Find VMs that have been running longer than timeout."""
        rows = self._conn.execute(
            """SELECT * FROM processed_objects
               WHERE status = 'running'
               AND started_at IS NOT NULL
               AND julianday('now') - julianday(started_at) > ? / 86400.0""",
            (timeout_seconds * 2,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM processed_objects GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def delete_by_status(self, status: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM processed_objects WHERE status = ?", (status,),
        )
        self._conn.commit()
        return cur.rowcount

    def delete_all(self) -> int:
        cur = self._conn.execute("DELETE FROM processed_objects")
        self._conn.commit()
        return cur.rowcount

    def close(self):
        self._conn.close()
