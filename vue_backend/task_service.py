"""Persistent collection-task records for the Vue application."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import uuid
import zipfile
from typing import Any, Callable

from data_repository import DataRepository, normalize_participant_profile
from scoring_engine import (
    SCORING_PROFILES,
    SCORING_PROFILE_CALIBRATED,
    SCORING_PROFILE_LABELS,
    SCORING_PROFILE_TRIAL,
    TRIAL_CONFIG_PATH,
    ScoringConfig,
    attach_task_profile_metadata,
    evaluate_movement_profiles,
    evaluate_task_profiles,
    normalize_scoring_profile,
)


class TaskService:
    TEMPORARY_SCORE_MODEL_VERSION = "temporary-random-task-v2"
    CUSTOM_SCORE_MODEL_VERSION = "custom-score-provider"
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
        diagnostic_sink: Callable[..., None] | None = None,
        scoring_config_path: str | Path | None = None,
        trial_scoring_config_path: str | Path | None = None,
        active_scoring_profile: str | None = None,
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
        self._custom_score_provider = score_provider
        self._score_provider = score_provider or (lambda: 60 + secrets.randbelow(36))
        self._scoring_configs: dict[str, ScoringConfig] = {
            SCORING_PROFILE_CALIBRATED: ScoringConfig.from_file(scoring_config_path),
            SCORING_PROFILE_TRIAL: ScoringConfig.from_file(
                trial_scoring_config_path or TRIAL_CONFIG_PATH
            ),
        }
        self._profile_state_path = self._path.parent / "scoring_profile.json"
        self._active_scoring_profile = self._load_initial_scoring_profile(
            active_scoring_profile
        )
        self._require_participant_profile = bool(require_participant_profile)
        self._movement_archived_callback = movement_archived_callback
        self._diagnostic_sink = diagnostic_sink
        if not self._device_ids:
            raise ValueError("采集任务至少需要一个设备")
        self._lock = threading.Lock()
        self._tasks = self._load()
        self._backfill_missing_scores()
        self._recover_interrupted_tasks()
        self._cleanup_temporary_files()

    @property
    def _scoring_config(self) -> ScoringConfig:
        return self._scoring_configs[self._active_scoring_profile]

    @property
    def active_scoring_profile(self) -> str:
        with self._lock:
            return self._active_scoring_profile

    def _load_initial_scoring_profile(self, explicit_profile: str | None) -> str:
        if explicit_profile is not None:
            return normalize_scoring_profile(explicit_profile)
        env_profile = os.environ.get("MOTION_SCORING_PROFILE")
        if env_profile:
            return normalize_scoring_profile(env_profile)
        if self._profile_state_path.exists():
            try:
                raw = json.loads(self._profile_state_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("active_profile"):
                    return normalize_scoring_profile(str(raw["active_profile"]))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        return SCORING_PROFILE_CALIBRATED

    def _persist_scoring_profile_locked(self) -> None:
        self._profile_state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._profile_state_path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps({"active_profile": self._active_scoring_profile}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self._profile_state_path)

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
            status = task.get("status")
            if status == "collecting":
                for reference in references:
                    for field in ("score", "score_raw", "score_model_version", "score_quality_status", "score_invalid_reason"):
                        if field in reference:
                            reference.pop(field, None)
                            changed = True
                if task.get("total_score") is not None or "score_summary" in task or "score_profiles" in task:
                    task["total_score"] = None
                    task.pop("score_model_version", None)
                    task.pop("score_summary", None)
                    task.pop("score_profiles", None)
                    changed = True
                continue

            if self._custom_score_provider is not None:
                for reference in references:
                    if "score" in reference:
                        reference.pop("score", None)
                        changed = True
                    if "score_model_version" in reference:
                        reference.pop("score_model_version", None)
                        changed = True
                if status == "completed" and references and task.get("total_score") is None:
                    task["total_score"] = self._new_task_score(str(task.get("task_id", "")))
                    task["score_model_version"] = self.CUSTOM_SCORE_MODEL_VERSION
                    changed = True
                continue

            version = task.get("score_model_version")
            stored_profiles = task.get("score_profiles")
            has_both_profiles = isinstance(stored_profiles, dict) and all(
                pid in stored_profiles for pid in SCORING_PROFILES
            )
            needs_v14_rescore = (
                status == "completed"
                and version != self.CUSTOM_SCORE_MODEL_VERSION
                and (
                    version == self.TEMPORARY_SCORE_MODEL_VERSION
                    or (
                        references
                        and (
                            not has_both_profiles
                            or (task.get("score_summary") is None and task.get("total_score") is None)
                        )
                    )
                )
            )
            if needs_v14_rescore:
                self._apply_v14_task_score(
                    task,
                    references,
                    task.get("movement_files", {}),
                )
                changed = True
            elif status == "completed" and has_both_profiles:
                summary = task.get("score_summary") or {}
                if summary.get("active_profile") != self._active_scoring_profile:
                    self._activate_task_profile_locked(task, self._active_scoring_profile)
                    changed = True
        if changed:
            self._save_locked()

    def _scoring_config_snapshot_locked(self) -> dict[str, Any]:
        active_cfg = self._scoring_config
        return {
            "active_profile": self._active_scoring_profile,
            "active_profile_label": SCORING_PROFILE_LABELS.get(
                self._active_scoring_profile, self._active_scoring_profile
            ),
            "available_profiles": [
                {
                    "profile": pid,
                    "label": SCORING_PROFILE_LABELS.get(pid, pid),
                    "model_version": self._scoring_configs[pid].model_version,
                    "calibration_version": self._scoring_configs[pid].calibration_version,
                    "calibrated": self._scoring_configs[pid].calibrated,
                    "provisional": self._scoring_configs[pid].provisional,
                }
                for pid in SCORING_PROFILES
            ],
            "scoring_version": active_cfg.scoring_version,
            "weights_version": active_cfg.weights_version,
            "calibration_version": active_cfg.calibration_version,
            "model_version": active_cfg.model_version,
            "calibrated": active_cfg.calibrated,
            "provisional": active_cfg.provisional,
            "missing_ratio_threshold": active_cfg.missing_ratio_threshold,
            "drop_penalty": deepcopy(active_cfg.drop_penalty),
            "intra_group_weights": deepcopy(active_cfg.intra_group_weights),
            "blocks": deepcopy(active_cfg.blocks),
        }

    def scoring_config_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._scoring_config_snapshot_locked()

    def set_scoring_profile(self, profile: str) -> dict[str, Any]:
        selected = normalize_scoring_profile(profile)
        with self._lock:
            self._active_scoring_profile = selected
            self._persist_scoring_profile_locked()
            tasks_changed = False
            for task in self._tasks:
                if task.get("status") != "completed":
                    continue
                if task.get("score_model_version") == self.CUSTOM_SCORE_MODEL_VERSION:
                    continue
                references = task.get("movement_references", [])
                if not isinstance(references, list) or not references:
                    continue
                stored_profiles = task.get("score_profiles")
                if isinstance(stored_profiles, dict) and all(
                    pid in stored_profiles for pid in SCORING_PROFILES
                ):
                    self._activate_task_profile_locked(task, selected)
                else:
                    self._apply_v14_task_score(
                        task,
                        references,
                        task.get("movement_files", {}),
                    )
                tasks_changed = True
            if tasks_changed:
                self._save_locked()
            return self._scoring_config_snapshot_locked()

    def score_live_movement(
        self,
        device_id: int,
        movement_id: int,
        analysis_mode: str | None,
        detail: dict[str, Any],
    ) -> dict[str, Any]:
        mode = str(analysis_mode or detail.get("analysis_mode") or "pc")
        scoring_input = self._live_movement_scoring_input(device_id, movement_id, mode)
        samples_rows: list[dict[str, Any]] | None = None
        if scoring_input is None:
            exported = self._live_movement_export(device_id, movement_id, "json", mode)
            if exported is not None:
                try:
                    parsed = json.loads(exported)
                    if isinstance(parsed, dict) and isinstance(parsed.get("samples"), list):
                        samples_rows = parsed["samples"]
                except (TypeError, ValueError, json.JSONDecodeError):
                    samples_rows = None
        with self._lock:
            active_profile = self._active_scoring_profile
        return evaluate_movement_profiles(
            detail,
            self._scoring_configs,
            active_profile=active_profile,
            samples_rows=samples_rows,
            scoring_input=scoring_input,
        )

    def _evaluate_task_v14(
        self,
        task: dict[str, Any],
        references: list[dict[str, Any]],
        movement_files: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        bindings = {
            int(item["device_id"]): str(item["block_id"])
            for item in task.get("block_bindings", [])
            if isinstance(item, dict)
            and "device_id" in item
            and "block_id" in item
        }
        movements_input: list[dict[str, Any]] = []
        for index, reference in enumerate(references, start=1):
            if "task_movement_index" not in reference:
                reference["task_movement_index"] = index
            key = self._movement_key(reference)
            files = movement_files.get(key, {}) if isinstance(movement_files, dict) else {}

            detail: dict[str, Any] | None = None
            detail_raw = self._read_movement_file(files.get("detail"))
            if detail_raw is not None:
                try:
                    parsed_detail = json.loads(detail_raw)
                    if isinstance(parsed_detail, dict):
                        detail = parsed_detail
                except (TypeError, ValueError, json.JSONDecodeError):
                    detail = None

            scoring_input: dict[str, Any] | None = None
            scoring_raw = self._read_movement_file(files.get("scoring_input"))
            if scoring_raw is not None:
                try:
                    parsed_scoring = json.loads(scoring_raw)
                    if isinstance(parsed_scoring, dict):
                        scoring_input = parsed_scoring
                except (TypeError, ValueError, json.JSONDecodeError):
                    scoring_input = None

            samples_rows: list[dict[str, Any]] | None = None
            if scoring_input is None:
                samples_raw = self._read_movement_file(files.get("json"))
                if samples_raw is not None:
                    try:
                        parsed_samples = json.loads(samples_raw)
                        if isinstance(parsed_samples, dict) and isinstance(parsed_samples.get("samples"), list):
                            samples_rows = parsed_samples["samples"]
                    except (TypeError, ValueError, json.JSONDecodeError):
                        samples_rows = None

            device_id = int(reference.get("device_id", 0))
            fallback_block = str(
                reference.get("block_id") or bindings.get(device_id, "")
            ).strip()
            movements_input.append({
                "reference": reference,
                "detail": detail,
                "samples_rows": samples_rows,
                "scoring_input": scoring_input,
                "fallback_block_id": fallback_block,
                "hole_id": reference.get("hole_id"),
            })

        return evaluate_task_profiles(
            movements_input,
            self._scoring_configs,
            active_profile=self._active_scoring_profile,
            task_status=str(task.get("status", "completed")),
            include_synthetic_samples=False,
        )

    def _activate_task_profile_locked(
        self,
        task: dict[str, Any],
        profile: str,
    ) -> dict[str, Any]:
        selected = normalize_scoring_profile(profile)
        stored_profiles = task.get("score_profiles") or {}
        refreshed = attach_task_profile_metadata(stored_profiles, selected)
        task["score_profiles"] = refreshed
        evaluated = deepcopy(refreshed[selected])
        references = task.get("movement_references", [])
        evaluated_movements = evaluated.get("movements", [])
        model_version = str(
            evaluated.get("model_version") or self._scoring_configs[selected].model_version
        )
        for reference, mov in zip(references, evaluated_movements):
            if mov.get("valid") and mov.get("score_display") is not None:
                reference["score"] = float(mov["score_display"])
                reference["score_raw"] = float(mov["score"])
                reference["score_model_version"] = model_version
                reference["score_quality_status"] = str(
                    (mov.get("quality") or {}).get("status") or "observed"
                )
                reference.pop("score_invalid_reason", None)
            else:
                reference["score"] = None
                reference.pop("score_raw", None)
                reference["score_model_version"] = model_version
                reference["score_quality_status"] = "invalid"
                if mov.get("invalid_reason"):
                    reference["score_invalid_reason"] = str(mov["invalid_reason"])

        completeness = str(evaluated.get("completeness") or "empty")
        if not references or completeness == "empty":
            task["data_quality"] = "normal"
            task["total_score"] = None
            task["score_model_version"] = None
        else:
            if completeness == "complete":
                task["data_quality"] = "complete"
            elif completeness == "partial":
                task["data_quality"] = "good"
            else:
                task["data_quality"] = "poor"
            task["total_score"] = evaluated.get("total_score")
            task["score_model_version"] = model_version
        task["score_summary"] = evaluated
        return evaluated

    def _apply_v14_task_score(
        self,
        task: dict[str, Any],
        references: list[dict[str, Any]],
        movement_files: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        bundle = self._evaluate_task_v14(task, references, movement_files)
        task["score_profiles"] = bundle["profiles"]
        return self._activate_task_profile_locked(task, bundle["active_profile"])

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
        result.pop("score_profiles", None)
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
                if (self._diagnostic_sink is not None and any(
                    self._movement_key(reference) not in movement_files
                    for reference in references
                )):
                    self._diagnostic_sink("task_result_incomplete", "warning", "task_finish",
                                          task_id=task_id)
                ended = datetime.now().astimezone()
                started = datetime.fromisoformat(str(task["start_time"]))
                task["end_time"] = ended.isoformat(timespec="seconds")
                task["duration_seconds"] = max(
                    0, int((ended - started).total_seconds())
                )
                task["status"] = "completed"
                task["movement_references"] = references
                task["movement_files"] = movement_files
                if self._custom_score_provider is not None:
                    task["data_quality"] = "complete" if references else "normal"
                    task["total_score"] = self._new_task_score(task_id) if references else None
                    task["score_model_version"] = (
                        self.CUSTOM_SCORE_MODEL_VERSION if references else None
                    )
                    task.pop("score_summary", None)
                else:
                    self._apply_v14_task_score(task, references, movement_files)
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
                    task.pop("score_summary", None)
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
            summary = task.get("score_summary")
            if isinstance(summary, dict):
                score_payload = {
                    **deepcopy(summary),
                    "total_score": total_score,
                    "score_status": str(
                        summary.get("score_status")
                        or ("completed" if total_score is not None else "pending")
                    ),
                    "model_version": task.get("score_model_version") or summary.get("model_version"),
                    "explanation": list(summary.get("explanation") or []),
                    "demo_only": bool(summary.get("demo_only", False)),
                }
            else:
                version = task.get("score_model_version")
                demo_only = version in (
                    None,
                    self.TEMPORARY_SCORE_MODEL_VERSION,
                    self.CUSTOM_SCORE_MODEL_VERSION,
                )
                score_payload = {
                    "total_score": total_score,
                    "score_status": "completed" if total_score is not None else "pending",
                    "model_version": version if total_score is not None else None,
                    "explanation": (
                        [
                            f"评分基于本次任务中 {len(references)} 次完整移动，在任务结束后统一生成。",
                        ]
                        if total_score is not None
                        else ["尚未检测到本任务中的完整移动。"]
                    ),
                    "demo_only": demo_only,
                }
            return {
                "task": self._public(task),
                "movement_reference": reference,
                "score": score_payload,
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
            for field in ("score", "score_raw", "score_model_version", "score_quality_status", "score_invalid_reason"):
                reference.pop(field, None)
            reference["task_movement_index"] = index
            target_id = int(reference["device_id"])
            per_device_indexes[target_id] = per_device_indexes.get(target_id, 0) + 1
            reference["device_movement_index"] = per_device_indexes[target_id]
        return references

    def _new_task_score(self, task_id: str = '') -> int:
        try:
            score = int(self._score_provider())
        except Exception as error:
            if self._diagnostic_sink is not None:
                self._diagnostic_sink("task_result_failed", "error", "task_scoring",
                                      error=error, task_id=task_id)
            raise
        if not 60 <= score <= 95:
            if self._diagnostic_sink is not None:
                self._diagnostic_sink("task_result_failed", "error", "task_scoring",
                                      task_id=task_id)
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
            scoring_input = self._live_movement_scoring_input(
                int(reference["device_id"]),
                int(reference["movement_id"]),
                str(reference["analysis_mode"]),
            )
            if scoring_input is not None:
                scoring_file = self._write_movement_file(
                    task_id,
                    reference,
                    "scoring_input.json",
                    json.dumps(scoring_input, ensure_ascii=False, separators=(",", ":")) + "\n",
                )
                created_files.append(self._movement_root / scoring_file)
                files["scoring_input"] = scoring_file
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
            summary = task.get("score_summary")
            if isinstance(summary, dict):
                matched_score = next(
                    (
                        deepcopy(item)
                        for item in summary.get("movements", [])
                        if isinstance(item, dict)
                        and int(item.get("device_id") or 0) == int(device_id)
                        and int(item.get("movement_id") or 0) == int(movement_id)
                        and str(item.get("analysis_mode") or "pc") == str(analysis_mode)
                    ),
                    None,
                )
                if matched_score is not None:
                    payload["v14_score"] = matched_score
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

    def tasks_export_archive(self, task_ids: list[str], fallback=None) -> bytes:
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
        if missing and fallback is None:
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
                if task_archive is None and fallback is not None:
                    task_archive = fallback(identifier)
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

    def _live_movement_scoring_input(
        self,
        device_id: int,
        movement_id: int,
        analysis_mode: str,
    ) -> dict[str, Any] | None:
        provider_owner = getattr(self._snapshot_provider, "__self__", None)
        provider = getattr(provider_owner, "movement_scoring_input", None)
        if not callable(provider):
            return None
        return provider(device_id, movement_id, analysis_mode)

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
