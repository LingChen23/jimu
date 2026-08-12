"""Persistent collection-task records for the Vue application."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import threading
import uuid
from typing import Any, Callable


class TaskService:
    def __init__(
        self,
        path: str | Path,
        snapshot_provider: Callable[[int], dict[str, Any]],
    ) -> None:
        self._path = Path(path)
        self._snapshot_provider = snapshot_provider
        self._lock = threading.Lock()
        self._tasks = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            tasks = payload.get("tasks", [])
            return tasks if isinstance(tasks, list) else []
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"tasks": self._tasks}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._path)

    @staticmethod
    def _public(task: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(task)
        result.pop("baseline_movement_id", None)
        return result

    def start(self, device_id: int, participant_id: str, note: str) -> dict[str, Any]:
        participant = str(participant_id).strip()
        if not participant:
            raise ValueError("体验者编号不能为空")
        if device_id < 1 or device_id > 12:
            raise ValueError("设备编号必须为 1～12")
        snapshot = self._snapshot_provider(device_id)
        completed = snapshot.get("completed_movements", [])
        baseline = max(
            (int(item.get("movement_id", 0)) for item in completed),
            default=0,
        )
        now = datetime.now().astimezone()
        with self._lock:
            if any(
                task.get("status") == "collecting"
                and int(task.get("device_id", 0)) == device_id
                for task in self._tasks
            ):
                raise ValueError("该设备已有进行中的采集任务")
            task = {
                "task_id": f"T{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4].upper()}",
                "participant_id": participant,
                "note": str(note).strip(),
                "block_id": str(snapshot.get("block_id", "")),
                "device_id": device_id,
                "device_ids": [device_id],
                "analysis_mode": str(snapshot.get("analysis_mode", "pc")),
                "start_time": now.isoformat(timespec="seconds"),
                "end_time": None,
                "duration_seconds": 0,
                "total_score": None,
                "data_quality": "normal",
                "status": "collecting",
                "baseline_movement_id": baseline,
                "movement_references": [],
            }
            self._tasks.insert(0, task)
            self._save_locked()
            return self._public(task)

    def finish(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = next(
                (item for item in self._tasks if item.get("task_id") == task_id),
                None,
            )
            if task is None:
                raise ValueError("未找到采集任务")
            if task.get("status") != "collecting":
                raise ValueError("采集任务已经结束")
            snapshot = self._snapshot_provider(int(task["device_id"]))
            baseline = int(task.get("baseline_movement_id", 0))
            mode = str(task.get("analysis_mode", snapshot.get("analysis_mode", "pc")))
            references = [
                {
                    "device_id": int(task["device_id"]),
                    "movement_id": int(item["movement_id"]),
                    "analysis_mode": mode,
                }
                for item in snapshot.get("completed_movements", [])
                if int(item.get("movement_id", 0)) > baseline
            ]
            references.sort(key=lambda item: item["movement_id"])
            ended = datetime.now().astimezone()
            started = datetime.fromisoformat(str(task["start_time"]))
            task["end_time"] = ended.isoformat(timespec="seconds")
            task["duration_seconds"] = max(0, int((ended - started).total_seconds()))
            task["status"] = "completed"
            task["data_quality"] = "complete" if references else "normal"
            task["movement_references"] = references
            self._save_locked()
            return self._public(task)

    def current(self, device_id: int) -> dict[str, Any] | None:
        with self._lock:
            task = next(
                (
                    item for item in self._tasks
                    if item.get("status") == "collecting"
                    and int(item.get("device_id", 0)) == device_id
                ),
                None,
            )
            return self._public(task) if task else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public(task) for task in self._tasks]

    def result(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = next(
                (item for item in self._tasks if item.get("task_id") == task_id),
                None,
            )
            if task is None:
                return None
            references = task.get("movement_references", [])
            return {
                "task": self._public(task),
                "movement_reference": deepcopy(references[-1]) if references else None,
            }
