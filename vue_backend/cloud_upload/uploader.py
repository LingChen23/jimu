"""Deep module for durable background upload of existing task ZIP exports."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Callable

from .queue import UploadQueue
from .transport import TransportError

TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
MAX_ZIP = 100 * 1024 * 1024
CACHE_BUDGET = 2 * 1024 * 1024 * 1024


class UploadFailure(Exception):
    def __init__(self, code: str, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class CloudUploader:
    """Owns queueing, packaging, retry and transport behind five methods."""

    def __init__(
        self,
        *,
        list_tasks: Callable[[], list[dict]],
        export_zip: Callable[[str], bytes | None],
        runtime_root: Path,
        transport=None,
        diagnostic_sink: Callable[..., None] | None = None,
    ) -> None:
        self._list_tasks = list_tasks
        self._export_zip = export_zip
        self._runtime_root = runtime_root
        self._cache_root = runtime_root / "cloud_upload_cache"
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self._queue = UploadQueue(runtime_root / "cloud_upload.sqlite3")
        self._transport = transport
        self._diagnostic_sink = diagnostic_sink
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._cleanup_temporary_files()
        self._recover_missed_tasks()
        self._stop.clear()
        if self._transport is not None:
            self._thread = threading.Thread(
                target=self._run, name="jimu-cloud-upload", daemon=True
            )
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=10)
        if self._transport is not None:
            self._transport.close()

    def enqueue(self, task_ids: list[str]) -> dict[str, dict]:
        tasks = {str(task.get("task_id", "")): task for task in self._list_tasks()}
        result = {}
        for raw_id in task_ids:
            task_id = str(raw_id).strip()
            task = tasks.get(task_id)
            if not TASK_ID.fullmatch(task_id) or task is None:
                result[task_id] = {"ok": False, "error_code": "task_not_found"}
                continue
            if task.get("status") not in ("completed", "abnormal"):
                result[task_id] = {"ok": False, "error_code": "task_not_finished"}
                continue
            stale_cache = self._queue.enqueue(task_id)
            self._unlink(stale_cache)
            current = self._queue.rows([task_id])[task_id]["status"]
            result[task_id] = {"ok": True, "status": current}
        self._wake.set()
        return result

    def statuses(self, task_ids: list[str]) -> dict:
        identifiers = [task_id for task_id in dict.fromkeys(map(str, task_ids)) if TASK_ID.fullmatch(task_id)]
        rows = self._queue.rows(identifiers)
        tasks = {}
        for task_id in identifiers:
            row = rows.get(task_id)
            tasks[task_id] = {
                "status": row["status"] if row else "pending",
                "error_code": row["error_code"] if row else None,
                "retry_at": row["retry_at"] if row else None,
            }
        connection = "disabled" if self._transport is None else self._transport.state()
        return {"connection": connection, "tasks": tasks}

    def prepare_delete(self, task_id: str) -> None:
        stale_cache = self._queue.remove_for_delete(str(task_id))
        self._unlink(stale_cache)

    def _recover_missed_tasks(self) -> None:
        enabled_at = self._queue.enabled_at()
        for task in self._list_tasks():
            task_id = str(task.get("task_id", ""))
            if (not TASK_ID.fullmatch(task_id) or task.get("status") not in ("completed", "abnormal")
                    or self._queue.contains(task_id)):
                continue
            try:
                ended_at = datetime.fromisoformat(str(task.get("end_time", ""))).timestamp()
            except (TypeError, ValueError):
                continue
            if ended_at >= enabled_at:
                self._queue.insert_if_missing(task_id)

    def _run(self) -> None:
        while not self._stop.is_set():
            row = self._queue.next_due(time.time())
            if row is None:
                self._wake.wait(timeout=1)
                self._wake.clear()
                continue
            active = self._queue.mark_uploading(row["task_id"])
            if active is not None:
                self._process(active)

    def _process(self, row: dict) -> None:
        task_id = row["task_id"]
        cache_path = Path(row["cache_path"]) if row.get("cache_path") else self._cache_root / f"{task_id}.zip"
        try:
            if not cache_path.is_file():
                data = self._export_zip(task_id)
                if data is None:
                    raise UploadFailure("task_not_found", False)
                if len(data) > MAX_ZIP:
                    raise UploadFailure("zip_too_large", False)
                current = sum(path.stat().st_size for path in self._cache_root.glob("*.zip"))
                if current + len(data) > CACHE_BUDGET:
                    raise UploadFailure("upload_cache_full", True)
                temporary = cache_path.with_suffix(".zip.tmp")
                temporary.write_bytes(data)
                temporary.replace(cache_path)
                self._queue.set_cache(task_id, cache_path)
            if cache_path.stat().st_size > MAX_ZIP:
                raise UploadFailure("zip_too_large", False)
            self._transport.archive(task_id, cache_path)
            self._queue.mark_uploaded(task_id)
            self._unlink(str(cache_path))
        except (UploadFailure, TransportError) as error:
            if self._diagnostic_sink is not None:
                code = "cloud_auth_failed" if error.code == "unauthorized" else (
                    "cloud_request_failed" if error.retryable else "cloud_archive_failed")
                self._diagnostic_sink(code, "warning" if error.retryable else "error",
                                      "archive_task", error=error, reason_code=error.code)
            delay = (15, 60, 300)[min(max(int(row["attempts"]) - 1, 0), 2)]
            self._queue.mark_failed(
                task_id, error.code, time.time() + delay if error.retryable else None
            )
        except (OSError, sqlite3.Error) as error:
            if self._diagnostic_sink is not None:
                self._diagnostic_sink("task_save_failed", "error", "upload_cache",
                                      error=error, reason_code="local_storage_error")
            self._queue.mark_failed(task_id, "local_storage_error", time.time() + 60)
        except Exception as error:
            if self._diagnostic_sink is not None:
                self._diagnostic_sink("unexpected_backend_error", "error", "archive_task",
                                      error=error, reason_code="upload_internal_error")
            self._queue.mark_failed(task_id, "upload_internal_error", time.time() + 60)

    def _cleanup_temporary_files(self) -> None:
        for path in self._cache_root.glob("*.tmp"):
            self._unlink(str(path))

    @staticmethod
    def _unlink(path: str | None) -> None:
        if not path:
            return
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
