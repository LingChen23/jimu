from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_service import TaskService
from task_deletion import TaskDeletion


class TaskDeletionTests(unittest.TestCase):
    def test_local_delete_reuses_task_service_and_never_deletes_cloud(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks = TaskService(Path(directory) / 'tasks.json', lambda _: {
                'block_id': 'A', 'analysis_mode': 'pc', 'completed_movements': [],
            }, device_ids=(1,))
            task_id = tasks.start(1, 'P-TEST', '')['task_id']
            tasks.finish(task_id)
            uploader = Mock()
            result = TaskDeletion(tasks, uploader).delete(task_id, 'local')
            self.assertTrue(result['ok'])
            self.assertEqual(tasks.list(), [])
            uploader.prepare_delete.assert_called_once_with(task_id)
            uploader.delete_cloud.assert_not_called()

    def test_cloud_only_delete_and_protection_of_collecting_task(self):
        tasks, uploader = Mock(), Mock()
        tasks.list.return_value = [{'task_id': 'T-LIVE', 'status': 'collecting'}]
        deletion = TaskDeletion(tasks, uploader)
        for scope in ('local', 'cloud'):
            with self.assertRaisesRegex(ValueError, '进行中'):
                deletion.delete('T-LIVE', scope)
        uploader.delete_cloud.assert_not_called()
        uploader.prepare_delete.assert_not_called()
        self.assertTrue(deletion.delete('T-REMOTE', 'cloud')['ok'])
        uploader.delete_cloud.assert_called_once_with('T-REMOTE')
        tasks.delete.assert_not_called()
