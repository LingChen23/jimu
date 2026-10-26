import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_runtime import SingleInstance, initialize_runtime


class DesktopRuntimeTests(unittest.TestCase):
    def test_mac_data_is_outside_app_bundle(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), \
                patch("desktop_runtime.sys.platform", "darwin"), \
                patch("desktop_runtime.Path.home", return_value=Path(folder)):
            root = initialize_runtime()
            self.assertEqual(root, Path(folder).resolve() / "Library" / "Application Support" / "JimuMotion")
            self.assertTrue(root.is_dir())

    def test_existing_data_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, {"MOTION_DATA_DIR": folder}):
            config = Path(folder) / "cloud_upload.json"
            config.write_text('{"enabled": false}', encoding="utf-8")
            self.assertEqual(initialize_runtime(), Path(folder).resolve())
            self.assertEqual(config.read_text(encoding="utf-8"), '{"enabled": false}')

    def test_only_one_instance_and_lock_is_released_on_exit(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("desktop_runtime.MUTEX_NAME", "Local\\JimuMotionTest-" + uuid.uuid4().hex):
            first = SingleInstance(Path(folder))
            try:
                self.assertFalse(first.already_running)
                second = SingleInstance(Path(folder))
                try:
                    self.assertTrue(second.already_running)
                finally:
                    second.close()
            finally:
                first.close()
            reopened = SingleInstance(Path(folder))
            try:
                self.assertFalse(reopened.already_running)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
