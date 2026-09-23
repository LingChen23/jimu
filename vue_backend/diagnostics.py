"""Bounded, durable PC warning/error summaries and OSS delivery via ECS."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from uuid import uuid4


MAX_EVENTS = 1000
MAX_BYTES = 20 * 1024 * 1024
BATCH_COUNT = 100
BATCH_WAIT = 300
RETRY_WAIT = 60


class Diagnostics:
    def __init__(self, root: Path, transport=None) -> None:
        self.root = root / "logs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "events.sqlite3"
        self.transport = transport
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    code TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    reason_code TEXT NOT NULL DEFAULT '',
                    device_id INTEGER,
                    error_type TEXT,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    batch_id TEXT,
                    sent_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            if 'reason_code' not in {row[1] for row in db.execute('PRAGMA table_info(events)')}:
                db.execute("ALTER TABLE events ADD COLUMN reason_code TEXT NOT NULL DEFAULT ''")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('client_id', ?)",
                       (uuid4().hex,))
            self.client_id = db.execute(
                "SELECT value FROM metadata WHERE key='client_id'"
            ).fetchone()[0]

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            db.execute(f"PRAGMA max_page_count={MAX_BYTES // 4096}")
            with db:
                yield db
        finally:
            db.close()

    def record(self, code: str, severity: str, operation: str,
               *, device_id: int | None = None, error: BaseException | None = None,
               occurrences: int = 1, reason_code: str = '') -> None:
        if severity not in ("warning", "error"):
            return
        # Deliberately avoid exception messages, URLs, task contents and personal data.
        code = str(code)[:64]
        operation = str(operation)[:64]
        reason_code = str(reason_code)[:64]
        if reason_code and not re.fullmatch(r'[A-Za-z0-9_-]+', reason_code):
            reason_code = 'invalid_reason_code'
        error_type = type(error).__name__ if error is not None else None
        occurrences = max(1, min(int(occurrences), 1_000_000))
        fingerprint = json.dumps([code, operation, reason_code, device_id, error_type],
                                 separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._lock, self._connect() as db:
                db.execute("""
                    INSERT INTO events (fingerprint,code,severity,operation,reason_code,device_id,error_type,
                                        first_seen,last_seen,count)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(fingerprint) DO UPDATE SET
                        last_seen=excluded.last_seen,
                        count=count+excluded.count,
                        severity=CASE WHEN severity='error' THEN 'error' ELSE excluded.severity END
                """, (fingerprint, code, severity, operation, reason_code, device_id,
                      error_type, now, now, occurrences))
                self._trim(db)
            self._wake.set()
        except (OSError, sqlite3.Error):
            # Diagnostics must never break collection or recurse into its own logger.
            pass

    @staticmethod
    def _trim(db) -> None:
        count = db.execute("SELECT count(*) FROM events").fetchone()[0]
        if count <= MAX_EVENTS:
            return
        excess = count - MAX_EVENTS
        # Preserve in-flight batches; sacrifice oldest warning summaries first.
        for severity in ("warning", "error"):
            if excess <= 0:
                break
            ids = [row[0] for row in db.execute(
                "SELECT id FROM events WHERE batch_id IS NULL AND severity=? "
                "ORDER BY last_seen LIMIT ?", (severity, excess))]
            db.executemany("DELETE FROM events WHERE id=?", ((item,) for item in ids))
            excess -= len(ids)

    def start(self) -> None:
        if self.transport is None or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="jimu-diagnostics", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _prepare(self, now: float) -> dict | None:
        with self._lock, self._connect() as db:
            prior = db.execute("SELECT id,payload FROM batches LIMIT 1").fetchone()
            if prior:
                return json.loads(prior["payload"])
            rows = db.execute("SELECT * FROM events WHERE batch_id IS NULL ORDER BY first_seen LIMIT ?",
                              (BATCH_COUNT,)).fetchall()
            if not rows:
                return None
            total = sum(row["count"] for row in rows)
            oldest = datetime.fromisoformat(rows[0]["first_seen"]).timestamp()
            if total < BATCH_COUNT and now - oldest < BATCH_WAIT:
                return None
            batch_id = uuid4().hex
            payload = {"client_id": self.client_id, "batch_id": batch_id,
                       "events": [{key: row[key] for key in (
                           "code", "severity", "operation", "reason_code", "device_id", "error_type",
                           "first_seen", "last_seen", "count")}
                          for row in rows]}
            db.execute("INSERT INTO batches VALUES (?,?)",
                       (batch_id, json.dumps(payload, separators=(",", ":"))))
            db.executemany("UPDATE events SET batch_id=?,sent_count=count WHERE id=?",
                           ((batch_id, row["id"]) for row in rows))
            return payload

    def _acknowledge(self, batch_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM events WHERE batch_id=? AND count<=sent_count", (batch_id,))
            db.execute("UPDATE events SET count=count-sent_count,batch_id=NULL,sent_count=0,"
                       "first_seen=last_seen WHERE batch_id=?", (batch_id,))
            db.execute("DELETE FROM batches WHERE id=?", (batch_id,))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                payload = self._prepare(time.time())
                if payload is not None:
                    result = self.transport.json("POST", "/api/diagnostics/events", payload)
                    if result.get("stored") is True and result.get("batch_id") == payload["batch_id"]:
                        self._acknowledge(payload["batch_id"])
                        continue
            except Exception:
                # Keep the exact batch for retry, including a lost-success response.
                self._wake.wait(RETRY_WAIT)
                self._wake.clear()
                continue
            self._wake.wait(1 if payload is not None else 5)
            self._wake.clear()
