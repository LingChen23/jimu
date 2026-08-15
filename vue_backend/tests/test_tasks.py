from __future__ import annotations

import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from task_service import TaskService


class TaskServiceTests(unittest.TestCase):
    def test_delete_completed_task_removes_database_snapshot_and_archives(self) -> None:
        class SnapshotProvider:
            def __init__(self) -> None:
                self.movement_ids: list[int] = []

            def snapshot(self, device_id: int) -> dict:
                return {
                    "device_id": device_id,
                    "block_id": chr(64 + device_id),
                    "analysis_mode": "pc",
                    "completed_movements": ([{"movement_id": movement_id} for movement_id in self.movement_ids] if device_id == 1 else []),
                }

            def movement_detail(self, device_id: int, movement_id: int, mode: str) -> dict:
                return {"device_id": device_id, "movement_id": movement_id, "analysis_mode": mode}

            def movement_export(self, *_args) -> str:
                return "archived"

        provider = SnapshotProvider()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            movement_root = Path(directory) / "movements"
            service = TaskService(
                path,
                provider.snapshot,
                device_ids=(1, 2, 3),
                score_provider=lambda: 80,
                movement_root=movement_root,
            )
            started = service.start(1, "P001", "可删除任务")
            provider.movement_ids.append(1)
            finished = service.finish(started["task_id"])
            archive_directories = [item for item in movement_root.iterdir() if item.is_dir()]
            self.assertEqual(len(archive_directories), 1)

            deleted = service.delete(finished["task_id"])

            self.assertEqual(deleted["task_id"], finished["task_id"])
            self.assertEqual(service.list(), [])
            self.assertFalse(archive_directories[0].exists())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["tasks"], [])
            reloaded = TaskService(path, provider.snapshot, device_ids=(1, 2, 3), movement_root=movement_root)
            self.assertEqual(reloaded.list(), [])

    def test_delete_rejects_collecting_task(self) -> None:
        snapshot = lambda device_id: {
            "device_id": device_id,
            "block_id": "A",
            "analysis_mode": "pc",
            "completed_movements": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            service = TaskService(Path(directory) / "tasks.json", snapshot, device_ids=(1,))
            started = service.start(1, "P001", "进行中")
            with self.assertRaisesRegex(ValueError, "进行中的采集任务不能删除"):
                service.delete(started["task_id"])
            self.assertEqual(len(service.list()), 1)

    def test_delete_restores_task_when_snapshot_write_fails(self) -> None:
        snapshot = lambda device_id: {
            "device_id": device_id,
            "block_id": "A",
            "analysis_mode": "pc",
            "completed_movements": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            service = TaskService(path, snapshot, device_ids=(1,))
            started = service.start(1, "P001", "回滚任务")
            service.finish(started["task_id"])

            original_save = service._save_locked
            service._save_locked = lambda: (_ for _ in ()).throw(OSError("snapshot failed"))
            with self.assertRaisesRegex(OSError, "snapshot failed"):
                service.delete(started["task_id"])
            service._save_locked = original_save

            self.assertEqual(service.list()[0]["task_id"], started["task_id"])
            reloaded = TaskService(path, snapshot, device_ids=(1,))
            self.assertEqual(reloaded.list()[0]["task_id"], started["task_id"])

    def test_scores_task_once_only_after_collection_finishes(self) -> None:
        movements = {1: [], 2: [], 3: []}
        score_calls: list[None] = []

        def snapshot(device_id: int) -> dict:
            return {
                "device_id": device_id,
                "block_id": chr(64 + device_id),
                "analysis_mode": "pc",
                "completed_movements": movements[device_id],
            }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            service = TaskService(
                path,
                snapshot,
                device_ids=(1, 2, 3),
                score_provider=lambda: score_calls.append(None) or 87,
            )
            started = service.start(1, "P001", "评分闭环")
            self.assertIsNone(service.current(1)["total_score"])

            movements[2].append({"movement_id": 1})
            first_refresh = service.current(1)
            second_refresh = service.current(3)

            self.assertNotIn("score", first_refresh["movement_references"][0])
            self.assertNotIn("score", second_refresh["movement_references"][0])
            self.assertIsNone(first_refresh["total_score"])
            self.assertEqual(score_calls, [])

            movements[1].append({"movement_id": 1})
            refreshed = service.current(2)
            self.assertTrue(all("score" not in reference for reference in refreshed["movement_references"]))
            self.assertIsNone(refreshed["total_score"])
            self.assertEqual(score_calls, [])

            finished = service.finish(started["task_id"])
            self.assertTrue(all("score" not in reference for reference in finished["movement_references"]))
            self.assertEqual(finished["total_score"], 87)
            self.assertEqual(len(score_calls), 1)
            result = service.result(started["task_id"])
            self.assertEqual(result["score"]["score_status"], "completed")
            self.assertTrue(result["score"]["demo_only"])

            reloaded = TaskService(path, snapshot, device_ids=(1, 2, 3))
            reloaded_task = reloaded.result(started["task_id"])["task"]
            self.assertEqual(reloaded_task["total_score"], 87)
            self.assertTrue(all("score" not in reference for reference in reloaded_task["movement_references"]))

    def test_completed_movement_detail_is_archived_before_task_finishes(self) -> None:
        class SnapshotProvider:
            def __init__(self) -> None:
                self.movement_ids: list[int] = []

            def snapshot(self, device_id: int) -> dict:
                return {
                    "device_id": device_id,
                    "block_id": chr(64 + device_id),
                    "analysis_mode": "pc",
                    "completed_movements": [
                        {"movement_id": movement_id}
                        for movement_id in self.movement_ids
                    ] if device_id == 1 else [],
                }

            def movement_detail(self, device_id: int, movement_id: int, mode: str) -> dict:
                return {
                    "device_id": device_id,
                    "movement_id": movement_id,
                    "analysis_mode": mode,
                    "acceleration_series": [[0.0, 1.0, 2.0, 3.0]],
                }

            def movement_export(self, *_args) -> str:
                return "archived"

        provider = SnapshotProvider()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            archive_notifications: list[tuple[int, int, str]] = []
            service = TaskService(
                path,
                provider.snapshot,
                device_ids=(1, 2, 3),
                score_provider=lambda: 80,
                movement_archived_callback=lambda device_id, movement_id, mode: (
                    archive_notifications.append((device_id, movement_id, mode))
                ),
            )
            started = service.start(1, "P001", "即时归档")
            provider.movement_ids.append(1)

            collecting = service.current(1)
            detail = service.movement_detail(started["task_id"], 1, 1, "pc")

            self.assertEqual(collecting["status"], "collecting")
            self.assertNotIn("score", collecting["movement_references"][0])
            self.assertIsNone(collecting["total_score"])
            self.assertEqual(detail["acceleration_series"][0][1:], [1.0, 2.0, 3.0])
            self.assertEqual(archive_notifications, [(1, 1, "pc")])

    def test_reload_backfills_one_task_score_for_legacy_completed_task(self) -> None:
        snapshot = lambda device_id: {
            "device_id": device_id,
            "block_id": "A",
            "analysis_mode": "pc",
            "completed_movements": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            path.write_text(json.dumps({"tasks": [{
                "task_id": "T-LEGACY",
                "device_id": 1,
                "status": "completed",
                "total_score": None,
                "movement_references": [{
                    "device_id": 1,
                    "movement_id": 7,
                    "analysis_mode": "pc",
                }],
            }]}), encoding="utf-8")

            service = TaskService(
                path,
                snapshot,
                device_ids=(1, 2, 3),
                score_provider=lambda: 88,
            )
            result = service.result("T-LEGACY")

            self.assertNotIn("score", result["task"]["movement_references"][0])
            self.assertEqual(result["task"]["total_score"], 88)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["tasks"][0]["total_score"],
                88,
            )

    def test_task_collects_movements_from_all_configured_devices(self) -> None:
        movements = {1: [], 2: [], 3: []}

        def snapshot(device_id: int) -> dict:
            return {
                "device_id": device_id,
                "block_id": chr(64 + device_id),
                "analysis_mode": "pc",
                "completed_movements": movements[device_id],
            }

        with tempfile.TemporaryDirectory() as directory:
            service = TaskService(
                Path(directory) / "tasks.json",
                snapshot,
                device_ids=(1, 2, 3),
            )
            started = service.start(2, "P001", "多积木")
            movements[1].extend([{"movement_id": 1}, {"movement_id": 2}])
            movements[3].append({"movement_id": 1})

            finished = service.finish(started["task_id"])

            self.assertEqual(finished["device_ids"], [1, 2, 3])
            self.assertEqual(
                [(item["device_id"], item["movement_id"]) for item in finished["movement_references"]],
                [(1, 1), (1, 2), (3, 1)],
            )
            self.assertEqual(
                [item["task_movement_index"] for item in finished["movement_references"]],
                [1, 2, 3],
            )
            self.assertEqual(
                [item["device_movement_index"] for item in finished["movement_references"]],
                [1, 2, 1],
            )
            self.assertEqual(
                finished["block_bindings"],
                [
                    {"device_id": 1, "block_id": "A"},
                    {"device_id": 2, "block_id": "B"},
                    {"device_id": 3, "block_id": "C"},
                ],
            )

    def test_start_finish_and_reload_task(self) -> None:
        movement_ids: list[int] = []

        def snapshot(device_id: int) -> dict:
            return {
                "device_id": device_id,
                "block_id": "A",
                "analysis_mode": "pc",
                "completed_movements": [
                    {"movement_id": movement_id} for movement_id in movement_ids
                ],
            }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            service = TaskService(path, snapshot, device_ids=(1, 2, 3))
            started = service.start(1, "P001", "首次采集")
            self.assertEqual(started["participant_id"], "P001")
            self.assertEqual(service.current(1)["task_id"], started["task_id"])
            movement_ids.append(1)

            finished = service.finish(started["task_id"])

            self.assertEqual(finished["status"], "completed")
            self.assertEqual(finished["movement_references"][0]["movement_id"], 1)
            self.assertIsNone(service.current(1))
            reloaded = TaskService(path, snapshot, device_ids=(1, 2, 3))
            self.assertEqual(reloaded.result(started["task_id"])["task"]["note"], "首次采集")
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["tasks"]), 1)

    def test_rejects_duplicate_collecting_task(self) -> None:
        snapshot = lambda device_id: {
            "block_id": "A", "analysis_mode": "pc", "completed_movements": []
        }
        with tempfile.TemporaryDirectory() as directory:
            service = TaskService(Path(directory) / "tasks.json", snapshot, device_ids=(1, 2, 3))
            service.start(1, "P001", "")
            with self.assertRaisesRegex(ValueError, "进行中"):
                service.start(1, "P002", "")

    def test_start_rolls_back_when_persistence_fails(self) -> None:
        snapshot = lambda device_id: {
            "block_id": "A", "analysis_mode": "pc", "completed_movements": []
        }
        with tempfile.TemporaryDirectory() as directory:
            service = TaskService(Path(directory) / "tasks.json", snapshot, device_ids=(1, 2, 3))
            with patch.object(service, "_save_locked", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    service.start(1, "P001", "")
            self.assertIsNone(service.current(1))
            self.assertEqual(service.list(), [])

    def test_finish_rolls_back_when_persistence_fails(self) -> None:
        movement_ids: list[int] = []
        snapshot = lambda device_id: {
            "block_id": "A",
            "analysis_mode": "pc",
            "completed_movements": [
                {"movement_id": movement_id} for movement_id in movement_ids
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            service = TaskService(Path(directory) / "tasks.json", snapshot, device_ids=(1, 2, 3))
            started = service.start(1, "P001", "")
            movement_ids.append(1)
            with patch.object(service, "_save_locked", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    service.finish(started["task_id"])
            self.assertEqual(service.current(1)["status"], "collecting")
            self.assertIsNone(service.current(1)["end_time"])

    def test_finish_removes_new_movement_files_when_index_write_fails(self) -> None:
        class SnapshotProvider:
            def __init__(self) -> None:
                self.movement_ids: list[int] = []

            def snapshot(self, device_id: int) -> dict:
                return {
                    "device_id": device_id,
                    "block_id": "A",
                    "analysis_mode": "pc",
                    "completed_movements": [
                        {"movement_id": movement_id}
                        for movement_id in self.movement_ids
                    ],
                }

            def movement_detail(self, *_args) -> dict:
                return {"calculated_path": [[0, 0, 0]]}

            def movement_export(self, *_args) -> str:
                return "saved"

        provider = SnapshotProvider()
        with tempfile.TemporaryDirectory() as directory:
            archive_notifications: list[tuple[int, int, str]] = []
            service = TaskService(
                Path(directory) / "tasks.json",
                provider.snapshot,
                device_ids=(1, 2, 3),
                movement_archived_callback=lambda device_id, movement_id, mode: (
                    archive_notifications.append((device_id, movement_id, mode))
                ),
            )
            started = service.start(1, "P001", "")
            provider.movement_ids.append(1)

            with patch.object(service, "_save_locked", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    service.finish(started["task_id"])

            movement_root = Path(directory) / "task_movements"
            self.assertFalse(any(movement_root.rglob("*.*")))
            self.assertEqual(archive_notifications, [])

    def test_sqlite_remains_authoritative_when_json_snapshot_is_damaged(self) -> None:
        snapshot = lambda device_id: {
            "block_id": "A", "analysis_mode": "pc", "completed_movements": []
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            service = TaskService(path, snapshot, device_ids=(1, 2, 3))
            first = service.start(1, "P001", "")
            service.finish(first["task_id"])
            service.start(2, "P002", "")
            path.write_text("{broken", encoding="utf-8")

            recovered = TaskService(path, snapshot, device_ids=(1, 2, 3))

            self.assertEqual(len(recovered.list()), 2)
            self.assertIn(first["task_id"], {item["task_id"] for item in recovered.list()})

    def test_participant_profile_is_saved_and_frozen_into_task(self) -> None:
        snapshot = lambda device_id: {
            "device_id": device_id,
            "block_id": "A",
            "analysis_mode": "pc",
            "completed_movements": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            service = TaskService(
                Path(directory) / "tasks.json",
                snapshot,
                device_ids=(1,),
                require_participant_profile=True,
            )
            profile = {
                "participant_id": "P001",
                "name": "张某",
                "gender": "男",
                "age": 78,
                "education_level": "小学",
                "dominant_hand": "右手",
            }
            started = service.start(1, "P001", "首次评估", profile)
            profile["name"] = "已修改"

            self.assertEqual(started["participant_snapshot"]["name"], "张某")
            self.assertEqual(service.participant("P001")["age"], 78)
            self.assertEqual(service.participants()[0]["participant_id"], "P001")

            reloaded = TaskService(
                Path(directory) / "tasks.json",
                snapshot,
                device_ids=(1,),
                require_participant_profile=True,
            )
            stored = reloaded.result(started["task_id"])["task"]
            self.assertEqual(stored["participant_snapshot"]["name"], "张某")

    def test_participant_profile_only_requires_identifier_and_name(self) -> None:
        snapshot = lambda device_id: {
            "device_id": device_id,
            "block_id": "A",
            "analysis_mode": "pc",
            "completed_movements": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            service = TaskService(
                Path(directory) / "tasks.json",
                snapshot,
                device_ids=(1,),
                require_participant_profile=True,
            )
            started = service.start(1, "P002", "仅必填字段", {
                "participant_id": "P002",
                "name": "王某",
            })

            self.assertEqual(started["participant_snapshot"]["name"], "王某")
            self.assertEqual(started["participant_snapshot"]["gender"], "")
            self.assertIsNone(started["participant_snapshot"]["age"])

            with self.assertRaisesRegex(ValueError, "姓名不能为空"):
                service.start(1, "P003", "缺少姓名", {
                    "participant_id": "P003",
                    "name": "",
                })

    def test_task_exports_include_profile_task_and_full_movement_context(self) -> None:
        class SnapshotProvider:
            def __init__(self) -> None:
                self.movement_ids: list[int] = []

            def snapshot(self, device_id: int) -> dict:
                return {
                    "device_id": device_id,
                    "block_id": "A",
                    "analysis_mode": "pc",
                    "completed_movements": [
                        {"movement_id": movement_id}
                        for movement_id in self.movement_ids
                    ],
                }

            def movement_detail(self, device_id: int, movement_id: int, mode: str) -> dict:
                return {"device_id": device_id, "movement_id": movement_id}

            def movement_export(self, *_args) -> str:
                if _args[2] == "csv":
                    return "record_type,sample_index,accel_x_mps2,gyro_x_dps\nimu_pc,0,1.2,3.4\n"
                return json.dumps({
                    "samples": [{"accel_mps2": [1, 2, 3], "gyro_dps": [4, 5, 6]}],
                    "calculated_path": [[0, 0, 0], [1, 0, 0]],
                })

        provider = SnapshotProvider()
        with tempfile.TemporaryDirectory() as directory:
            service = TaskService(
                Path(directory) / "tasks.json",
                provider.snapshot,
                device_ids=(1,),
                score_provider=lambda: 80,
                require_participant_profile=True,
            )
            started = service.start(1, "P001", "导出测试", {
                "participant_id": "P001", "name": "李某", "gender": "女", "age": 82,
            })
            provider.movement_ids.append(1)
            service.finish(started["task_id"])

            exported_json = json.loads(service.movement_export(
                started["task_id"], 1, 1, "pc", "json"
            ))
            exported_csv = service.movement_export(
                started["task_id"], 1, 1, "pc", "csv"
            )

            self.assertEqual(
                exported_json["collection_context"]["participant_snapshot"]["name"],
                "李某",
            )
            self.assertEqual(exported_json["samples"][0]["gyro_dps"], [4, 5, 6])
            self.assertIn("participant_snapshot.name,李某", exported_csv)
            self.assertIn("imu_pc,0,1.2,3.4", exported_csv)

            task_archive = service.task_export_archive(started["task_id"])
            self.assertIsNotNone(task_archive)
            with zipfile.ZipFile(io.BytesIO(task_archive)) as archive:
                names = archive.namelist()
                manifest = json.loads(archive.read("task.json"))
                sample_json_name = next(
                    name for name in names if name.endswith("/samples.json")
                )
                sample_csv_name = next(
                    name for name in names if name.endswith("/samples.csv")
                )
                detail_name = next(
                    name for name in names if name.endswith("/detail.json")
                )
                sample_json = json.loads(archive.read(sample_json_name))

                self.assertEqual(manifest["movement_count"], 1)
                self.assertEqual(manifest["task"]["task_id"], started["task_id"])
                self.assertEqual(
                    manifest["participant_snapshot"]["name"], "李某"
                )
                self.assertEqual(sample_json["samples"][0]["gyro_dps"], [4, 5, 6])
                self.assertIn(b"imu_pc,0,1.2,3.4", archive.read(sample_csv_name))
                self.assertEqual(
                    json.loads(archive.read(detail_name))["movement_id"], 1
                )

    def test_finished_task_persists_movement_detail(self) -> None:
        class SnapshotProvider:
            def __init__(self) -> None:
                self.movement_ids: list[int] = []

            def snapshot(self, device_id: int) -> dict:
                return {
                    "device_id": device_id,
                    "block_id": "A",
                    "analysis_mode": "pc",
                    "completed_movements": [
                        {"movement_id": movement_id}
                        for movement_id in self.movement_ids
                    ],
                }

            def movement_detail(
                self, device_id: int, movement_id: int, mode: str
            ) -> dict:
                return {
                    "device_id": device_id,
                    "movement_id": movement_id,
                    "analysis_mode": mode,
                    "calculated_path": [[0, 0, 0], [1, 0, 0]],
                }

            def movement_export(
                self,
                device_id: int,
                movement_id: int,
                export_format: str,
                mode: str,
            ) -> str:
                return (
                    "movement_id,value\n1,ok\n"
                    if export_format == "csv"
                    else json.dumps({"movement_id": movement_id, "mode": mode})
                )

        provider = SnapshotProvider()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            service = TaskService(path, provider.snapshot, device_ids=(1, 2, 3))
            started = service.start(1, "P001", "")
            provider.movement_ids.append(1)
            service.finish(started["task_id"])

            reloaded = TaskService(path, provider.snapshot, device_ids=(1, 2, 3))
            detail = reloaded.movement_detail(started["task_id"], 1, 1, "pc")
            csv_export = reloaded.movement_export(
                started["task_id"], 1, 1, "pc", "csv"
            )

            self.assertEqual(detail["calculated_path"][-1], [1, 0, 0])
            self.assertIn("1,ok", csv_export)
            self.assertNotIn("movement_details", reloaded.list()[0])
            self.assertNotIn("calculated_path", path.read_text(encoding="utf-8"))
            self.assertTrue(any((Path(directory) / "task_movements").rglob("*.json")))

    def test_reload_marks_collecting_task_as_interrupted(self) -> None:
        snapshot = lambda device_id: {
            "block_id": "A", "analysis_mode": "pc", "completed_movements": []
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            service = TaskService(path, snapshot, device_ids=(1, 2, 3))
            started = service.start(1, "P001", "")

            reloaded = TaskService(path, snapshot, device_ids=(1, 2, 3))

            self.assertIsNone(reloaded.current(1))
            result = reloaded.result(started["task_id"])["task"]
            self.assertEqual(result["status"], "abnormal")
            self.assertEqual(result["data_quality"], "poor")


if __name__ == "__main__":
    unittest.main()
