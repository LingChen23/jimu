import io
import gzip
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from cloud_backend.app import create_app
from cloud_backend.config import Settings
from cloud_backend.errors import ArchiveError
from cloud_backend.storage.repository import Repository
from cloud_backend.storage.oss import OssStorage, _service_error


class MemoryStorage:
    """Fault-injectable adapter; database remains real SQLite in every test."""
    def __init__(self):
        self.objects = {}
        self.fail_suffix = None
        self.crash_suffix = None

    def size(self, key):
        return len(self.objects[key]) if key in self.objects else None

    def put(self, key, source, size, content_type, *, create_only=False):
        if self.fail_suffix and key.endswith(self.fail_suffix):
            raise ArchiveError('oss_unavailable', 503, True)
        if self.crash_suffix and key.endswith(self.crash_suffix):
            raise SystemExit('simulated process exit')
        if create_only and key in self.objects:
            return False
        data = source.read()
        assert len(data) == size
        self.objects[key] = data
        return True

    def download(self, key, target, limit):
        data = self.objects[key]
        assert len(data) <= limit
        target.write_bytes(data)

    def stream(self, key, chunk_size=1024 * 1024):
        if key not in self.objects:
            raise ArchiveError('archive_file_missing', 503, True)
        data = self.objects[key]
        return (data[index:index + chunk_size] for index in range(0, len(data), chunk_size))


class OssAdapterTests(unittest.TestCase):
    def test_unwraps_service_error_from_operation_error(self):
        import alibabacloud_oss_v2 as oss
        service = oss.exceptions.ServiceError(
            status_code=404, code='NoSuchKey', request_id='test',
            message='missing', ec='0026-00000001', timestamp='now',
            request_target='HEAD https://example.invalid/object')
        operation = oss.exceptions.OperationError(name='HeadObject', error=service)
        self.assertIs(_service_error(operation, oss), service)

    def test_download_uses_v2_stream_iterator(self):
        class Body:
            closed = False
            def iter_bytes(self, **kwargs):
                self.chunk_size = kwargs['chunk_size']
                yield b'abc'
                yield b'def'
            def close(self):
                self.closed = True

        class Request:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        body = Body()
        storage = object.__new__(OssStorage)
        storage.bucket = 'test'
        storage.sdk = SimpleNamespace(GetObjectRequest=Request)
        storage.client = SimpleNamespace(get_object=lambda request: SimpleNamespace(body=body))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'task.zip'
            storage.download('key', target, 10)
            self.assertEqual(target.read_bytes(), b'abcdef')
        self.assertEqual(body.chunk_size, 1024 * 1024)
        self.assertTrue(body.closed)

    def test_stream_closes_oss_body_when_consumer_stops(self):
        class Body:
            closed = False
            def iter_bytes(self, **kwargs):
                yield b'abc'
                yield b'def'
            def close(self):
                self.closed = True

        class Request:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        body = Body()
        storage = object.__new__(OssStorage)
        storage.bucket = 'test'
        storage.sdk = SimpleNamespace(GetObjectRequest=Request)
        storage.client = SimpleNamespace(get_object=lambda request: SimpleNamespace(body=body))
        stream = storage.stream('key', chunk_size=3)
        self.assertEqual(next(stream), b'abc')
        stream.close()
        self.assertTrue(body.closed)


def fixture(*, change=None, extra=None, compression=zipfile.ZIP_DEFLATED, zero=False):
    reference = dict(task_movement_index=1, device_id=5, movement_id=2, analysis_mode='pc')
    directory = 'movements/0001_device_5_movement_2_pc/'
    entries = {directory + 'detail.json': b'{"movement_id":2}',
               directory + 'samples.json': b'[]', directory + 'samples.csv': b'time,value\n'}
    participant = dict(name='Test')
    task = dict(task_id='T20260921-TEST', status='completed',
                start_time='2026-09-20T20:00:00+00:00', end_time='2026-09-20T20:01:00+00:00',
                participant_id='P-TEST', participant_snapshot=participant,
                movement_references=[] if zero else [reference], total_score=64,
                score_model_version='test-v1')
    manifest = dict(export_schema_version=1, task=task, participant_snapshot=participant,
                    movement_count=0 if zero else 1,
                    movements=[] if zero else [{**reference, 'included_files': list(entries), 'missing_files': []}])
    if zero:
        entries = {}
    if change:
        change(manifest, entries)
    entries['task.json'] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as archive:
        for key, value in entries.items():
            archive.writestr(key, value)
        if extra:
            archive.writestr(*extra)
    return output.getvalue()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(token='test-token-' * 4, data_dir=root/'data',
                                 temp_dir=root/'temp', log_dir=root/'log', min_free=0)
        self.repository = Repository(root/'data'/'tasks.sqlite3')
        self.storage = MemoryStorage()
        self.app = create_app(self.settings, self.storage, self.repository)
        self.client = TestClient(self.app)
        self.headers = {'Authorization': 'Bearer ' + self.settings.token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def upload(self, data=None, **kwargs):
        return self.client.post('/api/tasks/archive', files={'file': ('ignored.zip', fixture() if data is None else data)},
                                headers=kwargs.pop('headers', self.headers), **kwargs)

    def assert_empty(self):
        self.assertEqual(list(self.settings.temp_dir.iterdir()), [])

    def test_diagnostics_batch_is_authenticated_idempotent_and_saved_to_oss(self):
        event = dict(code='serial_open_failed', severity='error', operation='serial_open',
                     task_id='T20260921-TEST',
                     device_id=None, error_type='PermissionError', reason_code='', count=3,
                     first_seen='2026-09-23T01:00:00+00:00',
                     last_seen='2026-09-23T01:01:00+00:00')
        payload = dict(client_id='a' * 32, batch_id='b' * 32, events=[event])
        self.assertEqual(self.client.post('/api/diagnostics/events', json=payload).status_code, 401)
        first = self.client.post('/api/diagnostics/events', json=payload, headers=self.headers)
        second = self.client.post('/api/diagnostics/events', json=payload, headers=self.headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(len(self.storage.objects), 1)
        key, value = next(iter(self.storage.objects.items()))
        self.assertEqual(key, 'logs/' + 'a' * 32 + '/2026-09-23/' + 'b' * 32 + '.json.gz')
        self.assertEqual(json.loads(gzip.decompress(value))['events'], [event])

    def test_success_duplicate_and_status_after_lost_response(self):
        first = fixture()
        self.assertEqual(self.upload(first).status_code, 201)
        saved = self.storage.objects.copy()
        changed = fixture(change=lambda m,e: m['task'].update(total_score=99))
        response = self.upload(changed)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['already_exists'])
        self.assertEqual(self.storage.objects, saved)
        self.assertEqual(len(saved), 3)
        row = self.repository.get('T20260921-TEST')
        self.assertEqual(row['total_score'], 64)
        self.assertEqual(row['oss_prefix'], 'tasks/2026/09/T20260921-TEST/')
        response = self.client.get('/api/tasks/T20260921-TEST/archive-status', headers=self.headers)
        self.assertEqual(response.json()['status'], 'archived')
        self.assert_empty()

    def test_public_history_filters_paginates_and_streams_zip(self):
        original = fixture()
        self.assertEqual(self.upload(original).status_code, 201)
        other = fixture(change=lambda manifest, entries: (
            manifest['task'].update(
                task_id='T20260922-OTHER', participant_id='P-OTHER',
                start_time='2026-09-22T08:00:00+08:00',
                end_time='2026-09-22T08:01:00+08:00',
            ),
            manifest['participant_snapshot'].update(name='Other'),
        ))
        self.assertEqual(self.upload(other).status_code, 201)

        page = self.client.get('/api/history/tasks', params={'page_size': 1}).json()
        self.assertEqual(page['total'], 2)
        self.assertEqual(page['items'][0]['task_id'], 'T20260922-OTHER')
        filtered = self.client.get('/api/history/tasks', params={
            'participant_id': 'P-TEST', 'participant_name': 'Test', 'date': '2026-09-21',
        }).json()
        self.assertEqual([item['task_id'] for item in filtered['items']], ['T20260921-TEST'])

        response = self.client.get('/api/history/tasks/T20260921-TEST/download')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, original)
        self.assertEqual(response.headers['content-disposition'],
                         'attachment; filename="task_T20260921-TEST.zip"')
        self.assertEqual(self.client.get('/api/history/tasks/unknown/download').status_code, 404)

    def test_history_result_detail_and_exports_reuse_archived_objects(self):
        original = fixture()
        self.assertEqual(self.upload(original).status_code, 201)

        page = self.client.get('/api/history/tasks').json()
        self.assertEqual(page['statistics']['total'], 1)
        self.assertEqual(page['items'][0]['task']['task_id'], 'T20260921-TEST')

        result = self.client.get('/api/tasks/result', params={'task': 'T20260921-TEST'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['task']['total_score'], 64)

        detail = self.client.get('/api/tasks/movement-detail', params={
            'task': 'T20260921-TEST', 'device': 5, 'movement': 2, 'mode': 'pc',
        })
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()['movement_id'], 2)

        exported = self.client.get('/api/tasks/movement-export.csv', params={
            'task': 'T20260921-TEST', 'device': 5, 'movement': 2, 'mode': 'pc',
        })
        self.assertEqual(exported.content, b'time,value\n')

        batch = self.client.post('/api/history/tasks/export-batch', json={
            'task_ids': ['T20260921-TEST'],
        })
        self.assertEqual(batch.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(batch.content)) as archive:
            self.assertIn('batch.json', archive.namelist())
            self.assertIn('tasks/001_T20260921-TEST/task.json', archive.namelist())
        self.assert_empty()

    def test_zero_movements(self):
        self.assertEqual(self.upload(fixture(zero=True)).status_code, 201)
        self.assertEqual(len(self.storage.objects), 2)

    def test_authentication_busy_and_unknown(self):
        self.assertEqual(self.upload(headers={}).status_code, 401)
        with self.app.state.archive.admission():
            self.assertEqual(self.upload().json()['error']['code'], 'archive_busy')
        self.assertEqual(self.client.get('/api/tasks/unknown/archive-status', headers=self.headers).status_code, 404)
        self.assertFalse(self.storage.objects)
        self.assert_empty()

    def test_reject_invalid_archives_without_index(self):
        cases = [b'not zip', fixture(extra=('../evil', b'x')),
                 fixture(extra=('task.json', b'{}')),
                 fixture(change=lambda m,e: e.pop(next(iter(e)))),
                 fixture(change=lambda m,e: m.update(export_schema_version=2)),
                 fixture(change=lambda m,e: m['task'].update(status='collecting')),
                 fixture(change=lambda m,e: m.update(movement_count=2)),
                 fixture(change=lambda m,e: m['movements'][0].update(missing_files=['detail.json'])),
                 fixture(change=lambda m,e: m['task'].update(task_id='../bad')),
                 fixture(change=lambda m,e: m['task'].update(total_score=float('nan')))]
        symlink = zipfile.ZipInfo('link')
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases.append(fixture(extra=(symlink, b'target')))
        corrupt = bytearray(fixture(compression=zipfile.ZIP_STORED))
        offset = corrupt.find(b'{"movement_id":2}')
        corrupt[offset + 15] ^= 1
        cases.append(bytes(corrupt))
        encrypted = bytearray(fixture())
        for signature, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
            position = encrypted.find(signature)
            encrypted[position + offset] |= 1
        cases.append(bytes(encrypted))
        for data in cases:
            with self.subTest(length=len(data)):
                self.assertEqual(self.upload(data).status_code, 422)
                self.assertFalse(self.storage.objects)
                self.assertIsNone(self.repository.get('T20260921-TEST'))
                self.assert_empty()

    def test_limits_and_incomplete_multipart(self):
        for name, value in [('max_zip', 50), ('max_expanded', 10), ('max_entries', 2),
                            ('temp_budget', 1), ('min_free', 10**18)]:
            settings = replace(self.settings, **{name: value})
            with TestClient(create_app(settings, self.storage, self.repository)) as client:
                response = client.post('/api/tasks/archive', files={'file': ('test.zip', fixture())}, headers=self.headers)
                self.assertIn(response.status_code, (413, 503))
                self.assert_empty()
        response = self.client.post('/api/tasks/archive', content=b'--abc\r\n',
                                    headers={**self.headers, 'Content-Type': 'multipart/form-data; boundary=abc'})
        self.assertEqual(response.status_code, 400)
        self.assert_empty()

    def test_oss_failure_recovers_from_original_not_new_upload(self):
        original = fixture()
        self.storage.fail_suffix = 'task.json'
        self.assertEqual(self.upload(original).status_code, 503)
        self.assertEqual(self.repository.get('T20260921-TEST')['archive_status'], 'retryable')
        self.assert_empty()
        self.storage.fail_suffix = None
        changed = fixture(change=lambda m,e: m['task'].update(total_score=99))
        self.assertEqual(self.upload(changed).status_code, 201)
        self.assertEqual(self.repository.get('T20260921-TEST')['total_score'], 64)
        self.assertEqual(self.storage.objects['tasks/2026/09/T20260921-TEST/task.zip'], original)
        self.assert_empty()

    def test_database_commit_failure_then_startup_recovery(self):
        with patch.object(self.repository, 'complete', side_effect=sqlite3.OperationalError('disk full')):
            self.assertEqual(self.upload().status_code, 503)
        fresh = create_app(self.settings, self.storage, Repository(self.repository.path))
        with TestClient(fresh) as client:
            result = client.get('/api/tasks/T20260921-TEST/archive-status', headers=self.headers)
            self.assertEqual(result.json()['status'], 'archived')
        self.assert_empty()

    def test_crash_after_zip_and_wait_for_retransmission(self):
        archive = self.app.state.archive
        self.storage.crash_suffix = 'task.json'
        with archive.admission():
            path = archive.temporary_path()
            path.write_bytes(fixture())
            with self.assertRaises(SystemExit):
                archive.submit(path)
        self.storage.crash_suffix = None
        with TestClient(create_app(self.settings, self.storage, Repository(self.repository.path))):
            self.assertEqual(self.repository.get('T20260921-TEST')['archive_status'], 'archived')
        self.assert_empty()

    def test_failure_before_zip_waits_for_retransmission(self):
        self.storage.fail_suffix = 'task.zip'
        self.assertEqual(self.upload().status_code, 503)
        self.app.state.archive.recover()
        self.assertEqual(self.repository.get('T20260921-TEST')['error_code'], 'awaiting_upload')
        self.storage.fail_suffix = None
        self.assertEqual(self.upload().status_code, 201)

    def test_cleanup_failure_does_not_undo_archive(self):
        original = Path.unlink
        def denied(path, *args, **kwargs):
            if path.name.startswith('jimu-'):
                raise PermissionError()
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', denied):
            self.assertEqual(self.upload().status_code, 201)
        self.app.state.archive.recover()
        self.assert_empty()


if __name__ == '__main__':
    unittest.main()
