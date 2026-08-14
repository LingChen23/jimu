from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_repository import DataRepository


class DataRepositoryTests(unittest.TestCase):
    def test_terminology_migration_updates_all_historical_json_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.sqlite3"
            DataRepository(path)
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "INSERT INTO participants VALUES(?, ?, ?, ?, ?, ?, ?)",
                    ("P1", "张某", "", 70, '{"profile_note":"老人档案"}', "now", "now"),
                )
                connection.execute(
                    "INSERT INTO tasks VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    ("T1", "P1", "completed", "now", "now", '{"note":"老人任务"}', "now", "now"),
                )
                connection.execute(
                    "INSERT INTO task_movements VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    ("T1", 1, "pc", 1, "now", "now", None, '{"label":"老人移动"}'),
                )
                connection.commit()
            finally:
                connection.close()

            DataRepository(path)

            connection = sqlite3.connect(path)
            try:
                payloads = [
                    connection.execute("SELECT profile_json FROM participants").fetchone()[0],
                    connection.execute("SELECT payload_json FROM tasks").fetchone()[0],
                    connection.execute("SELECT payload_json FROM task_movements").fetchone()[0],
                ]
                metadata = dict(connection.execute("SELECT key, value FROM schema_metadata"))
            finally:
                connection.close()

            self.assertTrue(all("老人" not in payload for payload in payloads))
            self.assertTrue(all("体验者" in payload for payload in payloads))
            self.assertEqual(metadata["schema_version"], "2")
            self.assertEqual(metadata["profile_entity"], "participant")
            self.assertEqual(metadata["profile_label"], "体验者")


if __name__ == "__main__":
    unittest.main()
