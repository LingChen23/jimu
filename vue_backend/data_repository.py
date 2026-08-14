"""Transactional persistence for participant profiles and collection tasks.

SQLite is the default deployment database.  The location is configurable so a
cloud deployment can place the database and movement archives on a persistent
volume.  The repository keeps SQL and JSON payloads separated: indexed columns
support history queries while the payload preserves forward-compatible fields.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Iterator


PROFILE_FIELDS = (
    "participant_id",
    "name",
    "gender",
    "age",
    "birth_date",
    "education_level",
    "dominant_hand",
    "phone",
    "vision_status",
    "hearing_status",
    "upper_limb_status",
    "cognitive_status",
    "profile_note",
)


def normalize_participant_profile(
    value: dict[str, Any],
    participant_id: str | None = None,
    *,
    require_identity: bool = True,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    profile = {
        field: str(raw.get(field, "")).strip()
        for field in PROFILE_FIELDS
        if field != "age"
    }
    profile["participant_id"] = str(
        participant_id if participant_id is not None else raw.get("participant_id", "")
    ).strip()
    age_value = raw.get("age")
    try:
        age = int(age_value) if age_value not in (None, "") else None
    except (TypeError, ValueError) as error:
        raise ValueError("年龄必须是整数") from error
    if age is not None and not 0 <= age <= 130:
        raise ValueError("年龄必须在 0～130 岁之间")
    profile["age"] = age

    if not profile["participant_id"]:
        raise ValueError("体验者编号不能为空")
    if require_identity and not profile["name"]:
        raise ValueError("姓名不能为空")
    return profile


class DataRepository:
    SCHEMA_VERSION = 2

    def __init__(self, database_path: str | Path, legacy_task_path: str | Path | None = None) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_task_path = Path(legacy_task_path) if legacy_task_path else None
        self._initialize()
        self._migrate_legacy_tasks_if_empty()
        self._migrate_terminology()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS participants (
                    participant_id TEXT PRIMARY KEY,
                    name TEXT,
                    gender TEXT,
                    age INTEGER,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_participants_name
                    ON participants(name);
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    participant_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_participant_time
                    ON tasks(participant_id, start_time DESC);
                CREATE INDEX IF NOT EXISTS idx_tasks_status_time
                    ON tasks(status, start_time DESC);
                CREATE TABLE IF NOT EXISTS task_movements (
                    task_id TEXT NOT NULL,
                    device_id INTEGER NOT NULL,
                    analysis_mode TEXT NOT NULL,
                    movement_id INTEGER NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    score REAL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(task_id, device_id, analysis_mode, movement_id),
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_task_movements_time
                    ON task_movements(started_at);
                """
            )
            connection.commit()

    def _migrate_terminology(self) -> None:
        """Remove the deprecated label from all structured historical payloads."""
        json_columns = (
            ("participants", "profile_json"),
            ("tasks", "payload_json"),
            ("task_movements", "payload_json"),
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for table, column in json_columns:
                    connection.execute(
                        f'UPDATE "{table}" SET "{column}" = '
                        f'replace("{column}", ?, ?) '
                        f'WHERE instr("{column}", ?) > 0',
                        ("老人", "体验者", "老人"),
                    )
                metadata = {
                    "schema_version": str(self.SCHEMA_VERSION),
                    "profile_entity": "participant",
                    "profile_label": "体验者",
                }
                connection.executemany(
                    "INSERT INTO schema_metadata(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    metadata.items(),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _migrate_legacy_tasks_if_empty(self) -> None:
        if self.legacy_task_path is None:
            return
        with self._connection() as connection:
            existing = int(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        if existing:
            return
        tasks = self._read_legacy_tasks()
        if not tasks:
            return
        self.replace_tasks(tasks)
        migrated = self.legacy_task_path.with_suffix(
            self.legacy_task_path.suffix + ".migrated"
        )
        if not migrated.exists():
            shutil.copyfile(self.legacy_task_path, migrated)

    def _read_legacy_tasks(self) -> list[dict[str, Any]]:
        if self.legacy_task_path is None:
            return []
        backup = self.legacy_task_path.with_suffix(self.legacy_task_path.suffix + ".bak")
        failures: list[str] = []
        for candidate in (self.legacy_task_path, backup):
            if not candidate.exists():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                tasks = payload.get("tasks", [])
                if not isinstance(tasks, list):
                    raise ValueError("tasks 必须是数组")
                return tasks
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
                failures.append(f"{candidate.name}: {error}")
        if failures:
            raise ValueError(f"旧任务记录损坏且无法迁移：{'；'.join(failures)}")
        return []

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM tasks ORDER BY start_time DESC, task_id DESC"
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def replace_tasks(self, tasks: list[dict[str, Any]]) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        task_ids = [str(task["task_id"]) for task in tasks]
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if task_ids:
                    placeholders = ",".join("?" for _ in task_ids)
                    connection.execute(
                        f"DELETE FROM tasks WHERE task_id NOT IN ({placeholders})",
                        task_ids,
                    )
                else:
                    connection.execute("DELETE FROM tasks")
                for task in tasks:
                    task_id = str(task["task_id"])
                    connection.execute(
                        """
                        INSERT INTO tasks(
                            task_id, participant_id, status, start_time, end_time,
                            payload_json, created_at, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(task_id) DO UPDATE SET
                            participant_id=excluded.participant_id,
                            status=excluded.status,
                            start_time=excluded.start_time,
                            end_time=excluded.end_time,
                            payload_json=excluded.payload_json,
                            updated_at=excluded.updated_at
                        """,
                        (
                            task_id,
                            str(task.get("participant_id", "")),
                            str(task.get("status", "completed")),
                            str(task.get("start_time", now)),
                            task.get("end_time"),
                            json.dumps(task, ensure_ascii=False, separators=(",", ":")),
                            now,
                            now,
                        ),
                    )
                    connection.execute("DELETE FROM task_movements WHERE task_id=?", (task_id,))
                    for reference in task.get("movement_references", []):
                        connection.execute(
                            """
                            INSERT INTO task_movements(
                                task_id, device_id, analysis_mode, movement_id,
                                started_at, ended_at, score, payload_json
                            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                task_id,
                                int(reference["device_id"]),
                                str(reference.get("analysis_mode", "pc")),
                                int(reference["movement_id"]),
                                reference.get("started_at"),
                                reference.get("ended_at"),
                                reference.get("score"),
                                json.dumps(reference, ensure_ascii=False, separators=(",", ":")),
                            ),
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def upsert_participant(self, profile: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_participant_profile(profile)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO participants(
                    participant_id, name, gender, age, profile_json, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(participant_id) DO UPDATE SET
                    name=excluded.name,
                    gender=excluded.gender,
                    age=excluded.age,
                    profile_json=excluded.profile_json,
                    updated_at=excluded.updated_at
                """,
                (
                    normalized["participant_id"],
                    normalized["name"],
                    normalized["gender"],
                    normalized["age"],
                    json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            connection.commit()
        return deepcopy(normalized)

    def participant(self, participant_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT profile_json FROM participants WHERE participant_id=?",
                (str(participant_id).strip(),),
            ).fetchone()
        return json.loads(row["profile_json"]) if row is not None else None

    def list_participants(self, query: str = "") -> list[dict[str, Any]]:
        keyword = str(query).strip()
        with self._connection() as connection:
            if keyword:
                pattern = f"%{keyword}%"
                rows = connection.execute(
                    """
                    SELECT profile_json FROM participants
                    WHERE participant_id LIKE ? OR name LIKE ?
                    ORDER BY updated_at DESC LIMIT 100
                    """,
                    (pattern, pattern),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT profile_json FROM participants ORDER BY updated_at DESC LIMIT 100"
                ).fetchall()
        return [json.loads(row["profile_json"]) for row in rows]
