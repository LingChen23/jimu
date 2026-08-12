from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from task_service import TaskService


class TaskServiceTests(unittest.TestCase):
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
            service = TaskService(path, snapshot)
            started = service.start(1, "P001", "首次采集")
            self.assertEqual(started["participant_id"], "P001")
            self.assertEqual(service.current(1)["task_id"], started["task_id"])
            movement_ids.append(1)

            finished = service.finish(started["task_id"])

            self.assertEqual(finished["status"], "completed")
            self.assertEqual(finished["movement_references"][0]["movement_id"], 1)
            self.assertIsNone(service.current(1))
            reloaded = TaskService(path, snapshot)
            self.assertEqual(reloaded.result(started["task_id"])["task"]["note"], "首次采集")
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["tasks"]), 1)

    def test_rejects_duplicate_collecting_task(self) -> None:
        snapshot = lambda device_id: {
            "block_id": "A", "analysis_mode": "pc", "completed_movements": []
        }
        with tempfile.TemporaryDirectory() as directory:
            service = TaskService(Path(directory) / "tasks.json", snapshot)
            service.start(1, "P001", "")
            with self.assertRaisesRegex(ValueError, "进行中"):
                service.start(1, "P002", "")


if __name__ == "__main__":
    unittest.main()
