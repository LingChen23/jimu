"""Read archived tasks without importing PC collection services."""
from contextlib import contextmanager, closing
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import threading
import zipfile

from .archive import TASK_ID, CHUNK
from .batch_export import write_batch
from .errors import ArchiveError


class History:
    def __init__(self, settings, repository, storage):
        self.settings, self.repository, self.storage = settings, repository, storage
        self.gate = threading.Lock()
        self.root = settings.temp_dir / 'exports'

    def cleanup(self):
        if not self.root.exists():
            return
        for path in self.root.glob('job-*'):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        try:
            self.root.rmdir()
        except OSError:
            pass

    def row(self, task_id):
        if not TASK_ID.fullmatch(task_id):
            raise ArchiveError('invalid_task_id', 400)
        row = self.repository.get(task_id)
        if row is None or row['archive_status'] != 'archived':
            raise ArchiveError('task_not_found', 404)
        return row

    def document(self, key, limit):
        data = bytearray()
        with closing(self.storage.stream(key)) as stream:
            for chunk in stream:
                data.extend(chunk)
                if len(data) > limit:
                    raise ArchiveError('json_too_large', 413)
        try:
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, UnicodeDecodeError):
            raise ArchiveError('invalid_archive_json', 503) from None

    def manifest(self, task_id):
        row = self.row(task_id)
        manifest = self.document(row['oss_prefix'] + 'task.json', 4 * CHUNK)
        if manifest.get('task', {}).get('task_id') != task_id:
            raise ArchiveError('archive_task_mismatch', 503)
        return row, manifest

    def page(self, **filters):
        page = self.repository.history(**filters)
        for item in page['items']:
            try:
                _, manifest = self.manifest(item['task_id'])
                item['task'] = manifest['task']
            except ArchiveError as error:
                item['metadata_error'] = error.code
        page['statistics'] = self.repository.statistics()
        return page

    def result(self, task_id):
        _, manifest = self.manifest(task_id)
        task = manifest['task']
        references = task.get('movement_references', manifest.get('movements', []))
        score = task.get('total_score')
        return dict(task=task, movement_reference=references[-1] if references else None,
            score=dict(total_score=score, score_status='completed' if score is not None else 'pending',
                model_version=task.get('score_model_version') if score is not None else None,
                explanation=[f'评分基于本次任务中 {len(references)} 次完整移动，在任务结束后统一生成。']
                    if score is not None else ['尚未检测到本任务中的完整移动。'], demo_only=True))

    def member(self, task_id, device_id, movement_id, analysis_mode, filename):
        row, manifest = self.manifest(task_id)
        for movement in manifest['movements']:
            if (movement['device_id'], movement['movement_id'], movement['analysis_mode']) == (
                    device_id, movement_id, analysis_mode):
                for name in movement['included_files']:
                    if name.endswith('/' + filename):
                        if not name.startswith('movements/') or any(p in ('', '.', '..') for p in name.split('/')):
                            raise ArchiveError('unsafe_zip_path', 503)
                        return row, name
        raise ArchiveError('movement_not_found', 404)

    def detail(self, task_id, device_id, movement_id, analysis_mode):
        row, name = self.member(task_id, device_id, movement_id, analysis_mode, 'detail.json')
        detail = self.document(row['oss_prefix'] + name, 64 * CHUNK)
        if not detail.get('gyroscope_series'):
            with self.export_movement(task_id, device_id, movement_id, analysis_mode, 'json') as path:
                samples = json.loads(path.read_text(encoding='utf-8-sig'))
            samples = samples.get('samples', []) if isinstance(samples, dict) else []
            detail['gyroscope_series'] = [[sample.get('elapsed_s', 0), *sample['gyro_dps']]
                for sample in samples if isinstance(sample, dict)
                and isinstance(sample.get('gyro_dps'), list) and len(sample['gyro_dps']) == 3]
        return detail

    @contextmanager
    def job(self):
        if not self.gate.acquire(blocking=False):
            raise ArchiveError('export_busy', 503, True)
        try:
            reserve = self.settings.max_expanded + 3 * self.settings.max_zip
            used = sum(p.stat().st_size for p in self.settings.temp_dir.rglob('*') if p.is_file())
            if used + reserve > self.settings.temp_budget:
                raise ArchiveError('temporary_storage_full', 503, True)
            if shutil.disk_usage(self.settings.temp_dir).free < self.settings.min_free + reserve:
                raise ArchiveError('insufficient_disk_space', 503, True)
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='job-', dir=self.root) as directory:
                yield Path(directory)
            try:
                self.root.rmdir()
            except OSError:
                pass
        finally:
            self.gate.release()

    @contextmanager
    def export_movement(self, task_id, device_id, movement_id, analysis_mode, format):
        if format not in ('csv', 'json'):
            raise ArchiveError('invalid_format', 400)
        row, name = self.member(task_id, device_id, movement_id, analysis_mode, 'samples.' + format)
        with self.job() as directory:
            source = directory / 'source.zip'
            self.storage.download(row['oss_prefix'] + 'task.zip', source, self.settings.max_zip)
            target = directory / ('movement.' + format)
            with zipfile.ZipFile(source) as archive:
                if archive.getinfo(name).file_size > 64 * CHUNK:
                    raise ArchiveError('movement_export_too_large', 413)
                with archive.open(name) as reader, target.open('wb') as writer:
                    shutil.copyfileobj(reader, writer, CHUNK)
            yield target

    @contextmanager
    def batch(self, task_ids):
        if not isinstance(task_ids, list) or not all(isinstance(i, str) for i in task_ids):
            raise ArchiveError('invalid_task_ids', 400)
        identifiers = list(dict.fromkeys(task_ids))
        if not 1 <= len(identifiers) <= 50:
            raise ArchiveError('invalid_batch_size', 400)
        rows = {identifier: self.row(identifier) for identifier in identifiers}
        if sum(row['zip_size'] for row in rows.values()) > 512 * CHUNK:
            raise ArchiveError('batch_too_large', 413)
        with self.job() as directory:
            @contextmanager
            def source(identifier):
                path = directory / 'source.zip'
                self.storage.download(rows[identifier]['oss_prefix'] + 'task.zip', path, self.settings.max_zip)
                with zipfile.ZipFile(path) as archive:
                    yield archive
                path.unlink()
            target = directory / 'tasks_batch.zip'
            try:
                write_batch(target, identifiers, source, max_expanded=self.settings.max_expanded)
            except ValueError as error:
                raise ArchiveError(str(error), 413) from None
            yield target
