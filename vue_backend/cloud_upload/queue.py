"""Durable upload state. This is an implementation detail of CloudUploader."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time


class UploadQueue:
    STATUSES = ("pending", "uploading", "failed", "uploaded")

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS uploads (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN ('pending','uploading','failed','uploaded')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    retry_at REAL,
                    cache_path TEXT,
                    uploaded_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS uploads_due
                    ON uploads(status, retry_at, updated_at);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            database.execute(
                "INSERT OR IGNORE INTO metadata(key,value) VALUES('enabled_at',?)",
                (str(time.time()),),
            )
            database.execute(
                "UPDATE uploads SET status='pending',error_code=NULL,retry_at=NULL,updated_at=? "
                "WHERE status='uploading'",
                (self._now(),),
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @contextmanager
    def _connection(self):
        database = sqlite3.connect(self.path, timeout=10)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA synchronous=FULL")
        try:
            with database:
                yield database
        finally:
            database.close()

    def enabled_at(self) -> float:
        with self._connection() as database:
            row = database.execute(
                "SELECT value FROM metadata WHERE key='enabled_at'"
            ).fetchone()
        return float(row["value"])

    def contains(self, task_id: str) -> bool:
        with self._connection() as database:
            return database.execute(
                "SELECT 1 FROM uploads WHERE task_id=?", (task_id,)
            ).fetchone() is not None

    def insert_if_missing(self, task_id: str) -> bool:
        with self._connection() as database:
            cursor = database.execute(
                "INSERT OR IGNORE INTO uploads(task_id,status,updated_at) VALUES(?,'pending',?)",
                (task_id, self._now()),
            )
        return cursor.rowcount == 1

    def enqueue(self, task_id: str) -> str | None:
        """Queue or manually retry a task; return stale cache to remove."""
        with self._connection() as database:
            row = database.execute(
                "SELECT status,cache_path FROM uploads WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                database.execute(
                    "INSERT INTO uploads(task_id,status,updated_at) VALUES(?,'pending',?)",
                    (task_id, self._now()),
                )
                return None
            if row["status"] in ("uploaded", "uploading"):
                return None
            stale_cache = row["cache_path"] if row["status"] == "failed" else None
            database.execute(
                "UPDATE uploads SET status='pending',attempts=0,error_code=NULL,retry_at=NULL,"
                "cache_path=NULL,updated_at=? WHERE task_id=?",
                (self._now(), task_id),
            )
            return stale_cache

    def next_due(self, now: float) -> dict | None:
        with self._connection() as database:
            row = database.execute(
                """
                SELECT * FROM uploads
                WHERE status='pending' OR (status='failed' AND retry_at IS NOT NULL AND retry_at<=?)
                ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, updated_at
                LIMIT 1
                """,
                (now,),
            ).fetchone()
        return dict(row) if row else None

    def mark_uploading(self, task_id: str) -> dict | None:
        with self._connection() as database:
            cursor = database.execute(
                "UPDATE uploads SET status='uploading',attempts=attempts+1,error_code=NULL,"
                "retry_at=NULL,updated_at=? WHERE task_id=? AND status IN ('pending','failed')",
                (self._now(), task_id),
            )
            if cursor.rowcount != 1:
                return None
            row = database.execute(
                "SELECT * FROM uploads WHERE task_id=?", (task_id,)
            ).fetchone()
        return dict(row)

    def set_cache(self, task_id: str, path: Path) -> None:
        with self._connection() as database:
            database.execute(
                "UPDATE uploads SET cache_path=?,updated_at=? WHERE task_id=?",
                (str(path), self._now(), task_id),
            )

    def mark_uploaded(self, task_id: str) -> None:
        now = self._now()
        with self._connection() as database:
            database.execute(
                "UPDATE uploads SET status='uploaded',error_code=NULL,retry_at=NULL,"
                "cache_path=NULL,uploaded_at=?,updated_at=? WHERE task_id=?",
                (now, now, task_id),
            )

    def mark_failed(self, task_id: str, code: str, retry_at: float | None) -> None:
        with self._connection() as database:
            database.execute(
                "UPDATE uploads SET status='failed',error_code=?,retry_at=?,updated_at=? "
                "WHERE task_id=?",
                (code[:80], retry_at, self._now(), task_id),
            )

    def rows(self, task_ids: list[str]) -> dict[str, dict]:
        if not task_ids:
            return {}
        placeholders = ",".join("?" for _ in task_ids)
        with self._connection() as database:
            rows = database.execute(
                f"SELECT * FROM uploads WHERE task_id IN ({placeholders})", task_ids
            ).fetchall()
        return {row["task_id"]: dict(row) for row in rows}

    def remove_for_delete(self, task_id: str) -> str | None:
        with self._connection() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT status,cache_path FROM uploads WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is not None and row["status"] == "uploading":
                raise ValueError("任务正在上传，请上传结束后再删除")
            database.execute("DELETE FROM uploads WHERE task_id=?", (task_id,))
        return row["cache_path"] if row else None
