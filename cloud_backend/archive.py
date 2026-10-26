"""The archive module owns validation, first-write semantics and recovery."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import io
import json
import logging
import math
import re
import shutil
import sqlite3
import stat
import threading
import uuid
import zipfile
import zlib

from .errors import ArchiveError

LOG = logging.getLogger('jimu.cloud')
TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z')
BEIJING = timezone(timedelta(hours=8))
CHUNK = 1024 * 1024


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _text(value, limit=256):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('invalid text')
    return value


def _time(value):
    parsed = datetime.fromisoformat(_text(value, 64))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed.astimezone(BEIJING)


def _validate(path, settings):
    """Read every entry, including samples, so CRC and actual limits are checked."""
    if path.stat().st_size > settings.max_zip:
        raise ArchiveError('zip_too_large', 413)
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > settings.max_entries:
                raise ArchiveError('too_many_entries', 413)
            names = set()
            expanded = 0
            documents = {}
            for entry in entries:
                name = entry.filename
                parts = name.split('/')
                if (not name or '\\' in name or '\x00' in entry.orig_filename
                        or name.startswith('/') or any(p in ('', '.', '..') for p in parts)
                        or any(':' in p for p in parts) or len(name) > 512
                        or any(ord(c) < 32 for c in name)):
                    raise ArchiveError('unsafe_zip_path')
                if name in names:
                    raise ArchiveError('duplicate_zip_entry')
                names.add(name)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise ArchiveError('unsupported_zip_entry')
                if entry.flag_bits & 1:
                    raise ArchiveError('encrypted_zip')
                if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ArchiveError('unsupported_compression')
                if expanded + entry.file_size > settings.max_expanded:
                    raise ArchiveError('expanded_zip_too_large', 413)
                document = name == 'task.json' or name.endswith('/detail.json')
                limit = 4 * CHUNK if name == 'task.json' else 64 * CHUNK
                data = bytearray()
                actual = 0
                with archive.open(entry) as source:
                    while chunk := source.read(CHUNK):
                        actual += len(chunk)
                        expanded += len(chunk)
                        if expanded > settings.max_expanded:
                            raise ArchiveError('expanded_zip_too_large', 413)
                        if document:
                            if actual > limit:
                                raise ArchiveError('json_too_large', 413)
                            data.extend(chunk)
                if actual != entry.file_size:
                    raise ArchiveError('invalid_zip')
                if document:
                    value = _json(data)
                    if not isinstance(value, dict):
                        raise ValueError('object required')
                    # Keep only the small manifest; detail is validated then discarded.
                    documents[name] = value if name == 'task.json' else value.get('movement_id')
            if 'task.json' not in documents or 'batch.json' in names:
                raise ArchiveError('single_task_required')
            manifest = documents['task.json']
            if type(manifest.get('export_schema_version')) is not int or manifest['export_schema_version'] != 1:
                raise ArchiveError('unsupported_schema')
            task = manifest['task']
            task_id = task['task_id']
            if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
                raise ArchiveError('invalid_task_id')
            if task['status'] not in ('completed', 'abnormal'):
                raise ArchiveError('task_not_finished')
            start, end = _time(task['start_time']), _time(task['end_time'])
            if end < start:
                raise ValueError('invalid time range')
            movements, references = manifest['movements'], task['movement_references']
            count = manifest['movement_count']
            if (not isinstance(movements, list) or not isinstance(references, list)
                    or type(count) is not int or count != len(movements) or count != len(references)):
                raise ArchiveError('manifest_mismatch')
            expected = {'task.json'}
            details = []
            identities = set()
            for movement, reference in zip(movements, references):
                if not isinstance(movement, dict) or not isinstance(reference, dict):
                    raise ValueError('invalid movement')
                if {k: v for k, v in movement.items() if k not in ('included_files', 'missing_files')} != reference:
                    raise ArchiveError('manifest_mismatch')
                if movement.get('missing_files') != []:
                    raise ArchiveError('missing_files')
                index, device, mid, mode = (movement[k] for k in
                                          ('task_movement_index', 'device_id', 'movement_id', 'analysis_mode'))
                if (any(type(v) is not int or v < 0 for v in (index, device, mid))
                        or index < 1 or mode not in ('pc', 'device')):
                    raise ValueError('invalid movement identity')
                directory = f'movements/{index:04d}_device_{device}_movement_{mid}_{mode}'
                identity = (device, mid, mode)
                if identity in identities or directory + '/detail.json' in expected:
                    raise ArchiveError('manifest_mismatch')
                identities.add(identity)
                files = [directory + '/' + suffix for suffix in ('detail.json', 'samples.json', 'samples.csv')]
                if sorted(movement['included_files']) != sorted(files):
                    raise ArchiveError('missing_files')
                expected.update(files)
                details.append(files[0])
                if documents.get(files[0]) != mid:
                    raise ArchiveError('manifest_mismatch')
            if names != expected:
                raise ArchiveError('manifest_mismatch')
            participant = task.get('participant_snapshot') or {}
            if manifest.get('participant_snapshot') != task.get('participant_snapshot'):
                raise ArchiveError('manifest_mismatch')
            score = task.get('total_score')
            if score is not None and (type(score) not in (float, int) or not math.isfinite(score)):
                raise ValueError('invalid score')
            version = task.get('score_model_version')
            if version is not None:
                _text(version)
            summary = dict(task_id=task_id,
                           participant_id=_text(task.get('participant_id') or ''),
                           participant_name=_text(participant.get('name') or ''),
                           start_time=start.isoformat(), end_time=end.isoformat(),
                           task_status=task['status'], movement_count=count,
                           total_score=score, score_model_version=version,
                           oss_prefix=f'tasks/{start:%Y/%m}/{task_id}/', zip_size=path.stat().st_size)
            return summary, ['task.json', *details]
    except ArchiveError:
        raise
    except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError, ValueError,
            KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        raise ArchiveError('invalid_zip_or_manifest') from None


class Archive:
    def __init__(self, settings, repository, storage):
        self.settings, self.repository, self.storage = settings, repository, storage
        self.gate = threading.Lock()
        settings.temp_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def admission(self, *, require_space=True):
        if not self.gate.acquire(blocking=False):
            raise ArchiveError('archive_busy', 503, True)
        try:
            self.cleanup()
            if require_space:
                self.check_space()
            yield
        finally:
            self.gate.release()

    def check_space(self):
        used = sum(p.stat().st_size for p in self.settings.temp_dir.glob('jimu-*.zip'))
        # Budget for incoming ZIP plus downloading a previous authoritative ZIP.
        if used + 2 * self.settings.max_zip > self.settings.temp_budget:
            raise ArchiveError('temporary_storage_full', 503, True)
        if shutil.disk_usage(self.settings.temp_dir).free < self.settings.min_free + 2 * self.settings.max_zip:
            raise ArchiveError('insufficient_disk_space', 503, True)

    def temporary_path(self):
        return self.settings.temp_dir / f'jimu-{uuid.uuid4().hex}.zip'

    def cleanup(self):
        for path in self.settings.temp_dir.glob('jimu-*.zip'):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                LOG.warning('temporary_cleanup_failed')

    def status(self, task_id):
        if not TASK_ID.fullmatch(task_id):
            raise ArchiveError('invalid_task_id')
        row = self.repository.get(task_id)
        if row is None:
            raise ArchiveError('task_not_found', 404)
        return dict(task_id=task_id, status=row['archive_status'],
                    retryable=row['archive_status'] != 'archived', error_code=row['error_code'])

    def submit(self, path):
        """Called under admission; no caller needs to know OSS object layout."""
        summary, documents = _validate(path, self.settings)
        row = self.repository.reserve(summary)
        if row['archive_status'] == 'deleting':
            raise ArchiveError('task_deleting', 409, True)
        if row['archive_status'] == 'archived':
            return dict(task_id=row['task_id'], status='archived', already_exists=True)
        return self._finish(row, path, summary, documents)

    def delete(self, task_id):
        if not TASK_ID.fullmatch(task_id):
            raise ArchiveError('invalid_task_id')
        if not self.gate.acquire(blocking=False):
            raise ArchiveError('archive_busy', 503, True)
        try:
            row = self.repository.get(task_id)
            if row is not None:
                self._delete(row)
            return dict(task_id=task_id, deleted=True)
        finally:
            self.gate.release()

    def _delete(self, row):
        task_id, prefix = row['task_id'], row['oss_prefix']
        if not re.fullmatch(r'tasks/\d{4}/(?:0[1-9]|1[0-2])/' + re.escape(task_id) + '/', prefix):
            raise ArchiveError('invalid_archive_prefix', 503)
        # Persist intent before touching OSS. Recovery must finish deletion,
        # never recreate already removed objects from the original ZIP.
        self.repository.mark_deleting(task_id)
        self.storage.delete_prefix(prefix)
        self.repository.delete(task_id)

    def _finish(self, row, path=None, summary=None, documents=None):
        task_id, prefix = row['task_id'], row['oss_prefix']
        key = prefix + 'task.zip'
        try:
            exists = self.storage.size(key) is not None
            if not exists and path is None:
                self.repository.fail(task_id, 'awaiting_upload')
                return None
            if not exists:
                if summary['oss_prefix'] != prefix:
                    raise ArchiveError('task_metadata_conflict', 409)
                with path.open('rb') as source:
                    exists = not self.storage.put(key, source, summary['zip_size'],
                                                 'application/zip', create_only=True)
            if exists:
                path = self.temporary_path()
                self.storage.download(key, path, self.settings.max_zip)
                summary, documents = _validate(path, self.settings)
                if summary['task_id'] != task_id or summary['oss_prefix'] != prefix:
                    raise ArchiveError('stored_archive_conflict', 409)
            with zipfile.ZipFile(path) as archive:
                for name in documents:
                    # BytesIO is rewindable for official SDK retry and CRC checks.
                    data = archive.read(name)
                    self.storage.put(prefix + name, io.BytesIO(data), len(data), 'application/json')
            if self.storage.size(key) != summary['zip_size']:
                raise ArchiveError('oss_size_mismatch', 503, True)
            self.repository.complete(summary)
            return dict(task_id=task_id, status='archived', already_exists=False)
        except (ArchiveError, sqlite3.Error, OSError) as error:
            code = error.code if isinstance(error, ArchiveError) else 'index_or_disk_unavailable'
            try:
                self.repository.fail(task_id, code)
            except sqlite3.Error:
                LOG.error('index_update_failed')
            if isinstance(error, ArchiveError):
                raise
            raise ArchiveError(code, 503, True) from None

    def recover(self):
        """Startup/periodic recovery shares the same single-request gate."""
        try:
            with self.admission(require_space=False):
                try:
                    for row in self.repository.pending():
                        try:
                            if row['archive_status'] == 'deleting':
                                self._delete(row)
                            else:
                                self.check_space()
                                self._finish(row)
                        except (ArchiveError, sqlite3.Error, OSError):
                            LOG.warning('archive_recovery_pending')
                        finally:
                            self.cleanup()
                finally:
                    self.cleanup()
        except (ArchiveError, sqlite3.Error, OSError):
            LOG.info('archive_recovery_deferred')
