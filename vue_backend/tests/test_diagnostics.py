from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from diagnostics import Diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_repeated_warning_counts_once_and_waits_for_five_minutes(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = Diagnostics(Path(directory))
            for _ in range(3):
                logs.record('device_command_timeout', 'warning', 'GET_STATUS', device_id=1)
            self.assertIsNone(logs._prepare(time.time()))
            payload = logs._prepare(time.time() + 301)
            self.assertEqual(len(payload['events']), 1)
            self.assertEqual(payload['events'][0]['count'], 3)

            # A lost response must retry the exact same batch after restart.
            restarted = Diagnostics(Path(directory))
            self.assertEqual(restarted._prepare(time.time() + 302), payload)
            restarted._acknowledge(payload['batch_id'])
            with closing(sqlite3.connect(restarted.path)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 0)

    def test_hundred_occurrences_send_immediately_and_keep_new_occurrences(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = Diagnostics(Path(directory))
            for _ in range(100):
                logs.record('serial_open_failed', 'error', 'serial_open')
            payload = logs._prepare(time.time())
            self.assertEqual(payload['events'][0]['count'], 100)
            logs.record('serial_open_failed', 'error', 'serial_open')
            logs._acknowledge(payload['batch_id'])
            with closing(sqlite3.connect(logs.path)) as db:
                self.assertEqual(db.execute('SELECT count FROM events').fetchone()[0], 1)

    def test_upload_failure_reasons_are_separate_summaries(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = Diagnostics(Path(directory))
            logs.record('cloud_request_failed', 'warning', 'archive_task',
                        reason_code='archive_busy')
            logs.record('cloud_request_failed', 'warning', 'archive_task',
                        reason_code='archive_busy')
            logs.record('cloud_request_failed', 'warning', 'archive_task',
                        reason_code='cloud_connection_failed')
            with closing(sqlite3.connect(logs.path)) as db:
                rows = db.execute('SELECT reason_code,count FROM events ORDER BY reason_code').fetchall()
            self.assertEqual(rows, [('archive_busy', 2), ('cloud_connection_failed', 1)])

    def test_task_errors_keep_task_ids_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = Diagnostics(Path(directory))
            for task_id in ('T-ONE', 'T-ONE', 'T-TWO'):
                logs.record('cloud_request_failed', 'warning', 'archive_task',
                            reason_code='archive_busy', task_id=task_id)
            payload = logs._prepare(time.time() + 301)
            self.assertEqual(
                {(row['task_id'], row['count']) for row in payload['events']},
                {('T-ONE', 2), ('T-TWO', 1)},
            )
