"""Persistent collection-task records for the Vue application."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import secrets
import shutil
import threading
import uuid
import zipfile
from typing import Any, Callable

from data_repository import DataRepository, normalize_participant_profile


class TaskService:
    TEMPORARY_SCORE_MODEL_VERSION = "temporary-random-task-v2"
    MAX_BATCH_EXPORT_TASKS = 50

    def __init__(
        self,
        path: str | Path,
        snapshot_provider: Callable[[int], dict[str, Any]],
        device_ids: tuple[int, ...] | list[int] = tuple(range(1, 11)),
        score_provider: Callable[[], int] | None = None,
        database_path: str | Path | None = None,
        movement_root: str | Path | None = None,
        require_participant_profile: bool = False,
        movement_archived_callback: Callable[[int, int, str], Any] | None = None,
    ) -> None:
        self._path = Path(path)
        self._snapshot_provider = snapshot_provider
        self._movement_root = (
            Path(movement_root)
            if movement_root is not None
            else self._path.parent / "task_movements"
        )
        self._repository = DataRepository(
            database_path or self._path.parent / "motion_system.sqlite3",
            legacy_task_path=self._path,
        )
        self._device_ids = tuple(sorted({int(device_id) for device_id in device_ids}))
        self._score_provider = score_provider or (lambda: 60 + secrets.randbelow(36))
        self._require_participant_profile = bool(require_participant_profile)
        self._movement_archived_callback = movement_archived_callback
        if not self._device_ids:
            raise ValueError("采集任务至少需要一个设备")
        self._lock = threading.Lock()
        self._tasks = self._load()
        self._backfill_missing_scores()
        self._recover_interrupted_tasks()
        self._cleanup_temporary_files()

    def _cleanup_temporary_files(self) -> None:
        if not self._movement_root.exists():
            return
        for temporary in self._movement_root.rglob("*.tmp"):
            try:
                temporary.unlink()
            except OSError:
                pass
        for staged_directory in self._movement_root.glob("*.deleting-*"):
            if staged_directory.is_dir():
                try:
                    shutil.rmtree(staged_directory)
                except OSError:
                    pass

    def _backfill_missing_scores(self) -> None:
        changed = False
        for task in self._tasks:
            references = task.get("movement_references", [])
            if not isinstance(references, list):
                continue
            for reference in references:
                if "score" in reference:
                    reference.pop("score", None)
                    changed = True
                if "score_model_version" in reference:
                    reference.pop("score_model_version", None)
                    changed = True
            if task.get("status") == "collecting" and task.get("total_score") is not None:
                task["total_score"] = None
                task.pop("score_model_version", None)
                changed = True
            elif (
                task.get("status") == "completed"
                and references
                and task.get("total_score") is None
            ):
                task["total_score"] = self._new_task_score()
                task["score_model_version"] = self.TEMPORARY_SCORE_MODEL_VERSION
                changed = True
            elif (
                task.get("status") == "completed"
                and task.get("total_score") is not None
                and task.get("score_model_version") != self.TEMPORARY_SCORE_MODEL_VERSION
            ):
                task["score_model_version"] = self.TEMPORARY_SCORE_MODEL_VERSION
                changed = True
        if changed:
            self._save_locked()

    def _recover_interrupted_tasks(self) -> None:
        interrupted = [
            task for task in self._tasks if task.get("status") == "collecting"
        ]
        if not interrupted:
            return
        ended = datetime.now().astimezone()
        for task in interrupted:
            try:
                started = datetime.fromisoformat(str(task["start_time"]))
                duration = max(0, int((ended - started).total_seconds()))
            except (KeyError, TypeError, ValueError):
                duration = 0
            task["end_time"] = ended.isoformat(timespec="seconds")
            task["duration_seconds"] = duration
            task["status"] = "abnormal"
            task["data_quality"] = "poor"
            task["interruption_reason"] = "后端重启，采集中断"
        self._save_locked()

    def _load(self) -> list[dict[str, Any]]:
        return self._repository.list_tasks()

    def _save_locked(self) -> None:
        # SQLite is authoritative.  A readable JSON snapshot remains as a
        # disaster-recovery/export aid for existing installations.
        self._repository.replace_tasks(self._tasks)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"tasks": self._tasks}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if self._path.exists():
            backup = self._path.with_suffix(self._path.suffix + ".bak")
            backup_temporary = backup.with_suffix(backup.suffix + ".tmp")
            shutil.copyfile(self._path, backup_temporary)
            backup_temporary.replace(backup)
        temporary.replace(self._path)

    @staticmethod
    def _public(task: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(task)
        result.pop("baseline_movement_id", None)
        result.pop("baseline_movement_ids", None)
        result.pop("analysis_modes", None)
        result.pop("movement_files", None)
        return result

    def start(
        self,
        device_id: int,
        participant_id: str,
        note: str,
        participant_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        participant = str(participant_id).strip()
        if not participant:
            raise ValueError("体验者编号不能为空")
        existing_profile = self._repository.participant(participant)
        if (
            self._require_participant_profile
            and participant_profile is None
            and existing_profile is None
        ):
            raise ValueError("请填写体验者详细信息")
        profile = normalize_participant_profile(
            participant_profile
            or existing_profile
            or {
                "name": participant,
                "gender": "",
                "age": None,
            },
            participant,
            require_identity=self._require_participant_profile,
        )
        if self._require_participant_profile or participant_profile or existing_profile:
            profile = self._repository.upsert_participant(profile)
        if device_id not in self._device_ids:
            raise ValueError(
                f"设备编号必须为 {self._device_ids[0]}～{self._device_ids[-1]}"
            )
        snapshots = {
            target_id: self._snapshot_provider(target_id)
            for target_id in self._device_ids
        }
        baselines = {
            str(target_id): max(
                (
                    int(item.get("movement_id", 0))
                    for item in snapshots[target_id].get("completed_movements", [])
                ),
                default=0,
            )
            for target_id in self._device_ids
        }
        analysis_modes = {
            str(target_id): str(snapshots[target_id].get("analysis_mode", "pc"))
            for target_id in self._device_ids
        }
        snapshot = snapshots[device_id]
        now = datetime.now().astimezone()
        with self._lock:
            if any(
                task.get("status") == "collecting"
                for task in self._tasks
            ):
                raise ValueError("已有进行中的采集任务")
            task = {
                "task_id": f"T{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4].upper()}",
                "participant_id": participant,
                "participant_snapshot": deepcopy(profile),
                "note": str(note).strip(),
                "block_id": str(snapshot.get("block_id", "")),
                "device_id": device_id,
                "device_ids": list(self._device_ids),
                "block_bindings": [
                    {
                        "device_id": target_id,
                        "block_id": str(snapshots[target_id].get("block_id", "")),
                    }
                    for target_id in self._device_ids
                ],
                "analysis_mode": str(snapshot.get("analysis_mode", "pc")),
                "start_time": now.isoformat(timespec="seconds"),
                "end_time": None,
                "duration_seconds": 0,
                "total_score": None,
                "data_quality": "normal",
                "status": "collecting",
                "baseline_movement_id": int(baselines[str(device_id)]),
                "baseline_movement_ids": baselines,
                "analysis_modes": analysis_modes,
                "movement_references": [],
            }
            self._tasks.insert(0, task)
            try:
                self._save_locked()
            except Exception:
                self._tasks.remove(task)
                raise
            return self._public(task)

    def participants(self, query: str = "") -> list[dict[str, Any]]:
        with self._lock:
            return self._repository.list_participants(query)

    def participant(self, participant_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._repository.participant(participant_id)

    def update_task_participant(
        self,
        task_id: str,
        participant_profile: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a task snapshot and the participant's current master profile.

        The participant identifier remains immutable so task ownership and
        movement archives cannot be accidentally reassigned while correcting
        profile details from the result page.
        """
        identifier = str(task_id).strip()
        with self._lock:
            task = next(
                (item for item in self._tasks if item.get("task_id") == identifier),
                None,
            )
            if task is None:
                raise ValueError("未找到采集任务")
            participant_id = str(task.get("participant_id", "")).strip()
            profile = normalize_participant_profile(
                participant_profile,
                participant_id,
                require_identity=True,
            )
            original_snapshot = deepcopy(task.get("participant_snapshot"))
            original_profile = self._repository.participant(participant_id)
            task["participant_snapshot"] = deepcopy(profile)
            try:
                saved_profile = self._repository.upsert_participant(profile)
                task["participant_snapshot"] = deepcopy(saved_profile)
                self._save_locked()
            except Exception:
                task["participant_snapshot"] = original_snapshot
                if original_profile is not None:
                    self._repository.upsert_participant(original_profile)
                raise
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
            original = deepcopy(task)
            created_files: list[Path] = []
            try:
                references = self._collect_movement_references(task)
                movement_files = self._ensure_movement_files(
                    task,
                    references,
                    created_files,
                )
                ended = datetime.now().astimezone()
                started = datetime.fromisoformat(str(task["start_time"]))
                task["end_time"] = ended.isoformat(timespec="seconds")
                task["duration_seconds"] = max(
                    0, int((ended - started).total_seconds())
                )
                task["status"] = "completed"
                task["data_quality"] = "complete" if references else "normal"
                task["movement_references"] = references
                task["total_score"] = self._new_task_score() if references else None
                task["score_model_version"] = (
                    self.TEMPORARY_SCORE_MODEL_VERSION if references else None
                )
                task["movement_files"] = movement_files
                self._save_locked()
            except Exception:
                task.clear()
                task.update(original)
                for created_file in created_files:
                    try:
                        created_file.unlink()
                    except OSError:
                        pass
                raise
            self._notify_archived_movements(references, movement_files)
            return self._public(task)

    def current(self, device_id: int) -> dict[str, Any] | None:
        with self._lock:
            task = next(
                (
                    item for item in self._tasks
                    if item.get("status") == "collecting"
                    and device_id in item.get("device_ids", [item.get("device_id", 0)])
                ),
                None,
            )
            if task is None:
                return None
            original = deepcopy(task)
            created_files: list[Path] = []
            try:
                references = self._collect_movement_references(task)
                movement_files = self._ensure_movement_files(
                    task,
                    references,
                    created_files,
                )
                if (
                    references != task.get("movement_references", [])
                    or movement_files != task.get("movement_files", {})
                ):
                    task["movement_references"] = references
                    task["total_score"] = None
                    task.pop("score_model_version", None)
                    task["movement_files"] = movement_files
                    self._save_locked()
            except Exception:
                task.clear()
                task.update(original)
                for created_file in created_files:
                    try:
                        created_file.unlink()
                    except OSError:
                        pass
                raise
            self._notify_archived_movements(references, movement_files)
            return self._public(task)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public(task) for task in self._tasks]

    def delete(self, task_id: str) -> dict[str, Any]:
        identifier = str(task_id).strip()
        if not identifier:
            raise ValueError("任务编号不能为空")
        with self._lock:
            index = next(
                (
                    position
                    for position, item in enumerate(self._tasks)
                    if item.get("task_id") == identifier
                ),
                None,
            )
            if index is None:
                raise ValueError("未找到采集任务")
            task = self._tasks[index]
            if task.get("status") == "collecting":
                raise ValueError("进行中的采集任务不能删除，请先结束采集")

            root = self._movement_root.resolve()
            archive = (root / self._task_archive_token(identifier)).resolve()
            if not archive.is_relative_to(root):
                raise ValueError("任务归档路径无效")
            staged = root / f"{archive.name}.deleting-{uuid.uuid4().hex}"
            archive_staged = False
            if archive.exists():
                if not archive.is_dir():
                    raise ValueError("任务归档结构无效")
                root.mkdir(parents=True, exist_ok=True)
                archive.replace(staged)
                archive_staged = True

            removed = self._tasks.pop(index)
            try:
                self._save_locked()
            except Exception:
                self._tasks.insert(index, removed)
                # `_save_locked` persists SQLite before updating the readable
                # JSON snapshot. Restore the authoritative database as well as
                # in-memory state if a later snapshot write fails.
                self._repository.replace_tasks(self._tasks)
                if archive_staged and staged.exists():
                    staged.replace(archive)
                raise

            if archive_staged:
                try:
                    shutil.rmtree(staged)
                except OSError:
                    # The task is already inaccessible and the staged directory
                    # is retried during the next service startup.
                    pass
            return self._public(task)

    def result(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = next(
                (item for item in self._tasks if item.get("task_id") == task_id),
                None,
            )
            if task is None:
                return None
            references = task.get("movement_references", [])
            reference = deepcopy(references[-1]) if references else None
            total_score = task.get("total_score")
            return {
                "task": self._public(task),
                "movement_reference": reference,
                "score": {
                    "total_score": total_score,
                    "score_status": "completed" if total_score is not None else "pending",
                    "model_version": task.get("score_model_version") if total_score is not None else None,
                    "explanation": (
                        [
                            f"评分基于本次任务中 {len(references)} 次完整移动，在任务结束后统一生成。",
                        ]
                        if total_score is not None
                        else ["尚未检测到本任务中的完整移动。"]
                    ),
                    "demo_only": True,
                },
            }

    def _collect_movement_references(
        self,
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        existing = {
            self._movement_key(reference): reference
            for reference in task.get("movement_references", [])
        }
        device_ids = [
            int(device_id)
            for device_id in task.get("device_ids", [task["device_id"]])
            if int(device_id) in self._device_ids
        ]
        legacy_baseline = int(task.get("baseline_movement_id", 0))
        baselines = task.get("baseline_movement_ids", {})
        analysis_modes = task.get("analysis_modes", {})
        references_by_key = deepcopy(existing)
        for target_id in device_ids:
            snapshot = self._snapshot_provider(target_id)
            baseline = int(
                baselines.get(str(target_id), legacy_baseline)
                if isinstance(baselines, dict)
                else legacy_baseline
            )
            mode = str(
                analysis_modes.get(str(target_id), snapshot.get("analysis_mode", "pc"))
                if isinstance(analysis_modes, dict)
                else snapshot.get("analysis_mode", "pc")
            )
            for item in snapshot.get("completed_movements", []):
                if (
                    int(item.get("movement_id", 0)) <= baseline
                    or not self._movement_belongs_to_task(item, task)
                ):
                    continue
                reference = {
                    "device_id": target_id,
                    "block_id": str(item.get("block_id", snapshot.get("block_id", ""))),
                    "movement_id": int(item["movement_id"]),
                    "analysis_mode": mode,
                    "started_at": self._movement_time(item, "start"),
                    "ended_at": self._movement_time(item, "end"),
                    "duration_seconds": float(
                        item.get("segment_seconds", item.get("moving_seconds", 0.0))
                    ),
                }
                references_by_key[self._movement_key(reference)] = reference

        references = list(references_by_key.values())
        references.sort(
            key=lambda item: (
                str(item.get("started_at") or item.get("ended_at") or ""),
                int(item["device_id"]),
                int(item["movement_id"]),
            )
        )
        per_device_indexes: dict[int, int] = {}
        for index, reference in enumerate(references, start=1):
            reference.pop("score", None)
            reference.pop("score_model_version", None)
            reference["task_movement_index"] = index
            target_id = int(reference["device_id"])
            per_device_indexes[target_id] = per_device_indexes.get(target_id, 0) + 1
            reference["device_movement_index"] = per_device_indexes[target_id]
        return references

    def _new_task_score(self) -> int:
        score = int(self._score_provider())
        if not 60 <= score <= 95:
            raise ValueError("任务评分必须在 60～95 分之间")
        return score

    def _ensure_movement_files(
        self,
        task: dict[str, Any],
        references: list[dict[str, Any]],
        created_files: list[Path],
    ) -> dict[str, dict[str, str]]:
        movement_files = deepcopy(task.get("movement_files", {}))
        task_id = str(task["task_id"])
        for reference in references:
            key = self._movement_key(reference)
            if key in movement_files:
                continue
            detail = self._live_movement_detail(
                int(reference["device_id"]),
                int(reference["movement_id"]),
                str(reference["analysis_mode"]),
            )
            if detail is None:
                continue
            detail_file = self._write_movement_file(
                task_id,
                reference,
                "detail.json",
                json.dumps(detail, ensure_ascii=False, indent=2) + "\n",
            )
            created_files.append(self._movement_root / detail_file)
            files = {"detail": detail_file}
            for export_format in ("csv", "json"):
                exported = self._live_movement_export(
                    int(reference["device_id"]),
                    int(reference["movement_id"]),
                    export_format,
                    str(reference["analysis_mode"]),
                )
                if exported is not None:
                    export_file = self._write_movement_file(
                        task_id,
                        reference,
                        f"export.{export_format}",
                        exported,
                    )
                    created_files.append(self._movement_root / export_file)
                    files[export_format] = export_file
            movement_files[key] = files
        return movement_files

    @staticmethod
    def _movement_time(movement: dict[str, Any], boundary: str) -> str | None:
        time_data = movement.get("time", {})
        if not isinstance(time_data, dict):
            return None
        value = time_data.get(boundary, {})
        if not isinstance(value, dict):
            return None
        timestamp = value.get("beijing") or value.get("computer")
        return str(timestamp) if timestamp else None

    @classmethod
    def _movement_belongs_to_task(
        cls,
        movement: dict[str, Any],
        task: dict[str, Any],
    ) -> bool:
        started_at = cls._movement_time(movement, "start")
        if not started_at:
            return True
        try:
            return datetime.fromisoformat(started_at) >= datetime.fromisoformat(
                str(task["start_time"])
            )
        except (KeyError, TypeError, ValueError):
            return True

    def movement_detail(
        self,
        task_id: str,
        device_id: int,
        movement_id: int,
        analysis_mode: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            task = next(
                (item for item in self._tasks if item.get("task_id") == task_id),
                None,
            )
            if task is None:
                return None
            key = self._movement_key({
                "device_id": device_id,
                "movement_id": movement_id,
                "analysis_mode": analysis_mode,
            })
            file_name = task.get("movement_files", {}).get(key, {}).get("detail")
            content = self._read_movement_file(file_name)
            if content is None:
                return None
            try:
                payload = json.loads(content)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            if not isinstance(payload, dict):
                return None
            if not payload.get("gyroscope_series"):
                export_file = task.get("movement_files", {}).get(key, {}).get("json")
                export_content = self._read_movement_file(export_file)
                if export_content is not None:
                    try:
                        exported = json.loads(export_content)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        exported = None
                    samples = exported.get("samples", []) if isinstance(exported, dict) else []
                    payload["gyroscope_series"] = [
                        [sample.get("elapsed_s", 0.0), *sample.get("gyro_dps", [])]
                        for sample in samples
                        if isinstance(sample, dict)
                        and isinstance(sample.get("gyro_dps"), list)
                        and len(sample["gyro_dps"]) == 3
                    ]
            return payload

    def movement_export(
        self,
        task_id: str,
        device_id: int,
        movement_id: int,
        analysis_mode: str,
        export_format: str,
    ) -> str | None:
        if export_format not in {"csv", "json"}:
            raise ValueError("不支持的导出格式")
        with self._lock:
            task = next(
                (item for item in self._tasks if item.get("task_id") == task_id),
                None,
            )
            if task is None:
                return None
            key = self._movement_key({
                "device_id": device_id,
                "movement_id": movement_id,
                "analysis_mode": analysis_mode,
            })
            file_name = task.get("movement_files", {}).get(key, {}).get(
                export_format
            )
            content = self._read_movement_file(file_name)
            if content is None:
                return None
            reference = next(
                (
                    deepcopy(item)
                    for item in task.get("movement_references", [])
                    if self._movement_key(item) == key
                ),
                None,
            )
            return self._enrich_export(
                content,
                export_format,
                task,
                reference,
            )

    def task_export_archive(self, task_id: str) -> bytes | None:
        """Build a self-contained ZIP export for every movement in one task.

        The task snapshot is copied while holding the service lock, then the
        potentially expensive compression work happens without blocking live
        task updates or serial-data requests.
        """
        identifier = str(task_id).strip()
        if not identifier:
            raise ValueError("任务编号不能为空")
        with self._lock:
            stored_task = next(
                (item for item in self._tasks if item.get("task_id") == identifier),
                None,
            )
            if stored_task is None:
                return None
            task = deepcopy(stored_task)

        references = task.get("movement_references", [])
        movement_files = task.get("movement_files", {})
        manifest_movements: list[dict[str, Any]] = []
        buffer = io.BytesIO()
        with zipfile.ZipFile(
            buffer,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for fallback_index, reference in enumerate(references, start=1):
                if not isinstance(reference, dict):
                    continue
                key = self._movement_key(reference)
                files = movement_files.get(key, {})
                movement_index = int(
                    reference.get("task_movement_index", fallback_index)
                )
                device_id = int(reference["device_id"])
                movement_id = int(reference["movement_id"])
                mode = str(reference["analysis_mode"])
                directory = (
                    f"movements/{movement_index:04d}_device_{device_id}_"
                    f"movement_{movement_id}_{mode}"
                )
                included: list[str] = []
                missing: list[str] = []

                detail = self._read_movement_file(files.get("detail"))
                if detail is None:
                    missing.append("detail.json")
                else:
                    name = f"{directory}/detail.json"
                    archive.writestr(name, detail.encode("utf-8"))
                    included.append(name)

                for export_format in ("json", "csv"):
                    content = self._read_movement_file(files.get(export_format))
                    export_name = f"samples.{export_format}"
                    if content is None:
                        missing.append(export_name)
                        continue
                    enriched = self._enrich_export(
                        content,
                        export_format,
                        task,
                        reference,
                    )
                    name = f"{directory}/{export_name}"
                    encoding = "utf-8-sig" if export_format == "csv" else "utf-8"
                    archive.writestr(name, enriched.encode(encoding))
                    included.append(name)

                manifest_movements.append({
                    **deepcopy(reference),
                    "included_files": included,
                    "missing_files": missing,
                })

            manifest = {
                "export_schema_version": 1,
                "exported_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "task": self._public(task),
                "participant_snapshot": deepcopy(
                    task.get("participant_snapshot")
                ),
                "block_bindings": deepcopy(task.get("block_bindings", [])),
                "movement_count": len(manifest_movements),
                "movements": manifest_movements,
                "storage": {
                    "schema_version": DataRepository.SCHEMA_VERSION,
                    "task_database": "sqlite",
                    "sample_archive": "file",
                },
            }
            archive.writestr(
                "task.json",
                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
                    "utf-8"
                ),
            )
        return buffer.getvalue()

    def tasks_export_archive(self, task_ids: list[str]) -> bytes:
        """Build one ZIP containing the full export of multiple tasks.

        Every task directory contains exactly the files produced by
        ``task_export_archive``.  Keeping the single-task exporter as the only
        source of task contents prevents the history-page bulk action from
        silently omitting fields added to the result-page export later.
        """
        if not isinstance(task_ids, list):
            raise ValueError("task_ids 必须是数组")
        identifiers: list[str] = []
        seen: set[str] = set()
        for task_id in task_ids:
            identifier = str(task_id).strip()
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            identifiers.append(identifier)
        if not identifiers:
            raise ValueError("请至少选择一个历史任务")
        if len(identifiers) > self.MAX_BATCH_EXPORT_TASKS:
            raise ValueError(
                f"一次最多导出 {self.MAX_BATCH_EXPORT_TASKS} 个任务"
            )

        with self._lock:
            existing = {
                str(task.get("task_id")): str(task.get("status", ""))
                for task in self._tasks
                if str(task.get("task_id")) in seen
            }
        missing = [identifier for identifier in identifiers if identifier not in existing]
        if missing:
            raise ValueError(f"未找到任务：{'、'.join(missing)}")
        collecting = [
            identifier
            for identifier in identifiers
            if existing.get(identifier) == "collecting"
        ]
        if collecting:
            raise ValueError(
                f"采集中的任务不能导出：{'、'.join(collecting)}"
            )

        batch_manifest: list[dict[str, Any]] = []
        buffer = io.BytesIO()
        with zipfile.ZipFile(
            buffer,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for index, identifier in enumerate(identifiers, start=1):
                task_archive = self.task_export_archive(identifier)
                if task_archive is None:
                    raise ValueError(f"导出任务失败：{identifier}")
                directory = f"tasks/{index:03d}_{identifier}"
                included_files: list[str] = []
                with zipfile.ZipFile(io.BytesIO(task_archive), mode="r") as source:
                    for member in source.infolist():
                        if member.is_dir():
                            continue
                        name = f"{directory}/{member.filename}"
                        archive.writestr(name, source.read(member))
                        included_files.append(name)
                batch_manifest.append({
                    "task_id": identifier,
                    "directory": directory,
                    "included_files": included_files,
                })

            archive.writestr(
                "batch.json",
                (
                    json.dumps(
                        {
                            "export_schema_version": 1,
                            "exported_at": datetime.now().astimezone().isoformat(
                                timespec="seconds"
                            ),
                            "task_count": len(batch_manifest),
                            "tasks": batch_manifest,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
        return buffer.getvalue()

    def _enrich_export(
        self,
        content: str,
        export_format: str,
        task: dict[str, Any],
        reference: dict[str, Any] | None,
    ) -> str:
        task_context = self._public(task)
        context = {
            "task": task_context,
            "participant_snapshot": deepcopy(task.get("participant_snapshot")),
            "selected_movement": deepcopy(reference),
            "all_task_movements": deepcopy(task.get("movement_references", [])),
            "block_bindings": deepcopy(task.get("block_bindings", [])),
            "storage": {
                "schema_version": DataRepository.SCHEMA_VERSION,
                "task_database": "sqlite",
                "sample_archive": "file",
            },
        }
        if export_format == "json":
            try:
                payload = json.loads(content)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {"raw_export": content}
            if not isinstance(payload, dict):
                payload = {"movement_data": payload}
            return json.dumps(
                {"collection_context": context, **payload},
                ensure_ascii=False,
                indent=2,
            ) + "\n"

        import csv
        import io

        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["section", "field", "value"])
        for field, value in self._flatten_export_context(context):
            writer.writerow(["collection_context", field, value])
        writer.writerow([])
        output.write(content.lstrip("\ufeff"))
        return output.getvalue()

    @classmethod
    def _flatten_export_context(
        cls,
        value: Any,
        prefix: str = "",
    ) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if isinstance(value, dict):
            for key, item in value.items():
                name = f"{prefix}.{key}" if prefix else str(key)
                rows.extend(cls._flatten_export_context(item, name))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                rows.extend(cls._flatten_export_context(item, f"{prefix}[{index}]"))
        else:
            rows.append((prefix, "" if value is None else str(value)))
        return rows

    def _live_movement_detail(
        self,
        device_id: int,
        movement_id: int,
        analysis_mode: str,
    ) -> dict[str, Any] | None:
        provider_owner = getattr(self._snapshot_provider, "__self__", None)
        provider = getattr(provider_owner, "movement_detail", None)
        if not callable(provider):
            return None
        return provider(device_id, movement_id, analysis_mode)

    def _live_movement_export(
        self,
        device_id: int,
        movement_id: int,
        export_format: str,
        analysis_mode: str,
    ) -> str | None:
        provider_owner = getattr(self._snapshot_provider, "__self__", None)
        provider = getattr(provider_owner, "movement_export", None)
        if not callable(provider):
            return None
        return provider(device_id, movement_id, export_format, analysis_mode)

    def _notify_archived_movements(
        self,
        references: list[dict[str, Any]],
        movement_files: dict[str, dict[str, str]],
    ) -> None:
        callback = self._movement_archived_callback
        if not callable(callback):
            return
        for reference in references:
            files = movement_files.get(self._movement_key(reference), {})
            if not {"detail", "csv", "json"}.issubset(files):
                continue
            try:
                callback(
                    int(reference["device_id"]),
                    int(reference["movement_id"]),
                    str(reference["analysis_mode"]),
                )
            except Exception:
                # Archiving is already durable; cache cleanup must never turn
                # a successful task save into a user-visible task failure.
                continue

    def _write_movement_file(
        self,
        task_id: str,
        reference: dict[str, Any],
        suffix: str,
        content: str,
    ) -> str:
        task_token = self._task_archive_token(task_id)
        target_directory = self._movement_root / task_token
        target_directory.mkdir(parents=True, exist_ok=True)
        stem = (
            f'device_{int(reference["device_id"])}_'
            f'{str(reference["analysis_mode"])}_'
            f'movement_{int(reference["movement_id"])}'
        )
        target = target_directory / f"{stem}.{suffix}"
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
        return target.relative_to(self._movement_root).as_posix()

    @staticmethod
    def _task_archive_token(task_id: str) -> str:
        return hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:24]

    def _read_movement_file(self, relative_name: object) -> str | None:
        if not isinstance(relative_name, str) or not relative_name:
            return None
        try:
            root = self._movement_root.resolve()
            target = (root / relative_name).resolve()
            if not target.is_relative_to(root):
                return None
            return target.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None

    @staticmethod
    def _movement_key(reference: dict[str, Any]) -> str:
        return (
            f'{int(reference["device_id"])}:'
            f'{str(reference["analysis_mode"])}:'
            f'{int(reference["movement_id"])}'
        )
