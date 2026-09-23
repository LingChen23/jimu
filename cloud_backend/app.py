"""Thin HTTP entry; authenticate and reserve capacity before reading multipart."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import gzip
import hmac
from io import BytesIO
import json
import logging
from logging.handlers import RotatingFileHandler
import re
import sqlite3

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from python_multipart import MultipartParser
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool

from .archive import Archive
from .config import Settings
from .errors import ArchiveError
from .history import History
from .storage.repository import Repository


async def _receive(request, path, limit):
    media, options = parse_options_header(request.headers.get('content-type', ''))
    boundary = options.get(b'boundary')
    if media != b'multipart/form-data' or not boundary or len(boundary) > 200:
        raise ArchiveError('multipart_required', 400)
    received = 0
    file_size = 0
    parts = 0
    finished = False
    headers = {}
    header_name = bytearray()
    header_value = bytearray()
    header_size = 0

    with path.open('xb') as output:
        def part_begin():
            nonlocal parts
            parts += 1
            if parts != 1:
                raise ArchiveError('single_file_required', 400)

        def header_field(data, start, end):
            nonlocal header_size
            header_size += end - start
            if header_size > 8192:
                raise ArchiveError('multipart_headers_too_large', 413)
            header_name.extend(data[start:end])

        def header_data(data, start, end):
            nonlocal header_size
            header_size += end - start
            if header_size > 8192:
                raise ArchiveError('multipart_headers_too_large', 413)
            header_value.extend(data[start:end])

        def header_end():
            key = bytes(header_name).lower()
            if key in headers:
                raise ArchiveError('invalid_multipart', 400)
            headers[key] = bytes(header_value)
            header_name.clear()
            header_value.clear()

        def headers_finished():
            disposition, fields = parse_options_header(headers.get(b'content-disposition', b''))
            if disposition != b'form-data' or fields.get(b'name') != b'file' or b'filename' not in fields:
                raise ArchiveError('file_field_required', 400)

        def part_data(data, start, end):
            nonlocal file_size
            file_size += end - start
            if file_size > limit:
                raise ArchiveError('zip_too_large', 413)
            output.write(data[start:end])

        def end():
            nonlocal finished
            finished = True

        parser = MultipartParser(boundary, dict(on_part_begin=part_begin,
            on_header_field=header_field, on_header_value=header_data,
            on_header_end=header_end, on_headers_finished=headers_finished,
            on_part_data=part_data, on_end=end))
        try:
            async for chunk in request.stream():
                received += len(chunk)
                if received > limit + 16384:
                    raise ArchiveError('request_too_large', 413)
                parser.write(chunk)
            parser.finalize()
        except ArchiveError:
            raise
        except Exception:
            raise ArchiveError('invalid_multipart', 400) from None
        if not finished or parts != 1 or not file_size:
            raise ArchiveError('incomplete_upload', 400, True)


def create_app(settings=None, storage=None, repository=None):
    settings = settings or Settings.from_env()
    repository = repository or Repository(settings.data_dir / 'tasks.sqlite3')
    if storage is None:
        from .storage.oss import OssStorage
        storage = OssStorage(settings)
    archiver = Archive(settings, repository, storage)
    history_reader = History(settings, repository, storage)

    @asynccontextmanager
    async def lifespan(app):
        # Prevent another worker/process from sweeping an active upload's temp file.
        lock = None
        try:
            import fcntl
            lock = (settings.data_dir / 'service.lock').open('a')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass  # Windows is used only for isolated local tests.
        await run_in_threadpool(archiver.recover)
        await run_in_threadpool(history_reader.cleanup)
        stop = asyncio.Event()

        async def recover_periodically():
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), settings.recovery_seconds)
                except TimeoutError:
                    await run_in_threadpool(archiver.recover)

        recovery = asyncio.create_task(recover_periodically())
        try:
            yield
        finally:
            stop.set()
            await recovery
            archiver.cleanup()
            history_reader.cleanup()
            if lock is not None:
                lock.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.archive = archiver

    @app.exception_handler(ArchiveError)
    async def archive_error(request, error):
        return JSONResponse({'error': {'code': error.code, 'retryable': error.retryable}},
                            status_code=error.status)

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    async def unavailable(request, error):
        return JSONResponse({'error': {'code': 'index_or_disk_unavailable', 'retryable': True}},
                            status_code=503)

    def authenticate(request):
        expected = 'Bearer ' + settings.token
        if not hmac.compare_digest(request.headers.get('authorization', '').encode(), expected.encode()):
            raise ArchiveError('unauthorized', 401)

    @app.get('/health')
    def health():
        repository.healthy()
        return {'status': 'ok'}

    @app.post('/api/diagnostics/events')
    async def diagnostic_events(request: Request):
        authenticate(request)
        try:
            declared_length = int(request.headers.get('content-length', '0') or '0')
        except ValueError:
            raise ArchiveError('invalid_content_length', 400) from None
        if declared_length > 256 * 1024:
            raise ArchiveError('diagnostic_batch_too_large', 413)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 256 * 1024:
                raise ArchiveError('diagnostic_batch_too_large', 413)
        try:
            payload = json.loads(data)
            client_id = payload['client_id']
            batch_id = payload['batch_id']
            events = payload['events']
            if (not isinstance(client_id, str) or not re.fullmatch('[0-9a-f]{32}', client_id)
                    or not isinstance(batch_id, str) or not re.fullmatch('[0-9a-f]{32}', batch_id)
                    or not isinstance(events, list) or not 1 <= len(events) <= 100):
                raise ValueError
            for event in events:
                if (not isinstance(event, dict)
                        or not {'code', 'severity', 'operation', 'device_id', 'error_type',
                                'reason_code', 'first_seen', 'last_seen', 'count'} <= set(event)
                        or set(event) - {'code', 'severity', 'operation', 'device_id',
                                         'error_type', 'reason_code', 'task_id',
                                         'first_seen', 'last_seen', 'count'}
                        or event.get('severity') not in ('warning', 'error')
                        or not isinstance(event.get('task_id', ''), str)
                        or bool(event.get('task_id')) and not re.fullmatch(
                            '[A-Za-z0-9_-]{1,96}', event['task_id'])
                        or not isinstance(event.get('reason_code'), str)
                        or len(event['reason_code']) > 64
                        or bool(event['reason_code']) and not re.fullmatch('[A-Za-z0-9_-]+', event['reason_code'])
                        or not isinstance(event.get('count'), int)
                        or not 1 <= event['count'] <= 2_147_483_647
                        or any(not isinstance(event.get(field), str)
                               or not 1 <= len(event[field]) <= limit
                               for field, limit in (('code', 64), ('operation', 64),
                                                    ('first_seen', 40), ('last_seen', 40)))
                        or event.get('device_id') is not None and
                        (not isinstance(event['device_id'], int) or not 1 <= event['device_id'] <= 1000)
                        or event.get('error_type') is not None and
                        (not isinstance(event['error_type'], str) or len(event['error_type']) > 64)):
                    raise ValueError
                datetime.fromisoformat(event['first_seen'])
                datetime.fromisoformat(event['last_seen'])
        except (ValueError, TypeError, KeyError):
            raise ArchiveError('invalid_diagnostic_batch', 400) from None
        compressed = gzip.compress(bytes(data), mtime=0)
        date = datetime.fromisoformat(events[0]['first_seen']).astimezone(timezone.utc).strftime('%Y-%m-%d')
        key = f'logs/{client_id}/{date}/{batch_id}.json.gz'
        created = await run_in_threadpool(
            storage.put, key, BytesIO(compressed), len(compressed),
            'application/gzip', create_only=True)
        if not created and await run_in_threadpool(storage.size, key) != len(compressed):
            raise ArchiveError('diagnostic_batch_conflict', 409)
        return {'stored': True, 'batch_id': batch_id}

    @app.get('/api/tasks/{task_id}/archive-status')
    def status(task_id: str, request: Request):
        authenticate(request)
        return archiver.status(task_id)

    @app.get('/api/history/tasks')
    def history(
        task_id: str = Query('', max_length=96),
        participant_id: str = Query('', max_length=128),
        participant_name: str = Query('', max_length=128),
        date: str = Query('', pattern=r'^$|^\d{4}-\d{2}-\d{2}$'),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        return history_reader.page(
            task_id=task_id.strip(), participant_id=participant_id.strip(),
            participant_name=participant_name.strip(), date=date,
            page=page, page_size=page_size,
        )

    @app.get('/api/history/tasks/{task_id}/download')
    def history_download(task_id: str):
        row = repository.get(task_id)
        if row is None or row['archive_status'] != 'archived':
            raise ArchiveError('task_not_found', 404)
        stream = storage.stream(row['oss_prefix'] + 'task.zip')
        return StreamingResponse(
            stream,
            media_type='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename="task_{task_id}.zip"',
                'Content-Length': str(row['zip_size']),
                'Cache-Control': 'no-store',
            },
        )

    @app.get('/api/history/tasks/{task_id}/result')
    def history_result(task_id: str):
        return history_reader.result(task_id)

    @app.get('/api/tasks/result')
    def compatible_task_result(task: str = Query(..., max_length=96)):
        return history_reader.result(task)

    @app.get('/api/history/tasks/{task_id}/movement-detail')
    def history_movement_detail(task_id: str, device_id: int = Query(..., ge=1),
                                movement_id: int = Query(..., ge=1),
                                analysis_mode: str = Query('pc', max_length=32)):
        return history_reader.detail(task_id, device_id, movement_id, analysis_mode)

    @app.get('/api/tasks/movement-detail')
    def compatible_movement_detail(task: str = Query(..., max_length=96),
                                   device: int = Query(..., ge=1),
                                   movement: int = Query(..., ge=1),
                                   mode: str = Query('pc', max_length=32)):
        return history_reader.detail(task, device, movement, mode)

    @app.get('/api/history/tasks/{task_id}/movement-export')
    def history_movement_export(task_id: str, device_id: int = Query(..., ge=1),
                                movement_id: int = Query(..., ge=1),
                                analysis_mode: str = Query('pc', max_length=32),
                                format: str = Query(..., pattern='^(csv|json)$')):
        def content():
            with history_reader.export_movement(
                    task_id, device_id, movement_id, analysis_mode, format) as path:
                with path.open('rb') as source:
                    while chunk := source.read(1024 * 1024):
                        yield chunk
        return StreamingResponse(content(), media_type='text/csv; charset=utf-8'
            if format == 'csv' else 'application/json; charset=utf-8', headers={
                'Content-Disposition': f'attachment; filename="task_{task_id}_device_{device_id}_movement_{movement_id}.{format}"',
                'Cache-Control': 'no-store'})

    @app.get('/api/tasks/movement-export.{format}')
    def compatible_movement_export(format: str, task: str = Query(..., max_length=96),
                                   device: int = Query(..., ge=1),
                                   movement: int = Query(..., ge=1),
                                   mode: str = Query('pc', max_length=32)):
        return history_movement_export(task, device, movement, mode, format)

    @app.get('/api/tasks/export.zip')
    def compatible_task_download(task: str = Query(..., max_length=96)):
        return history_download(task)

    @app.post('/api/history/tasks/export-batch')
    async def history_batch(request: Request):
        try:
            body = await request.json()
        except ValueError:
            raise ArchiveError('invalid_json', 400) from None
        task_ids = body.get('task_ids') if isinstance(body, dict) else None

        def content():
            with history_reader.batch(task_ids) as path:
                with path.open('rb') as source:
                    while chunk := source.read(1024 * 1024):
                        yield chunk
        return StreamingResponse(content(), media_type='application/zip', headers={
            'Content-Disposition': 'attachment; filename="tasks_batch.zip"',
            'Cache-Control': 'no-store'})

    @app.post('/api/tasks/export-batch.zip')
    async def compatible_batch(request: Request):
        return await history_batch(request)

    @app.post('/api/tasks/archive')
    async def upload(request: Request):
        authenticate(request)
        length = request.headers.get('content-length')
        if length is not None:
            try:
                length = int(length)
            except ValueError:
                raise ArchiveError('invalid_content_length', 400) from None
            if length < 0:
                raise ArchiveError('invalid_content_length', 400)
            if length > settings.max_zip + 16384:
                raise ArchiveError('request_too_large', 413)
        with archiver.admission():
            try:
                async with asyncio.timeout(600):
                    path = archiver.temporary_path()
                    await _receive(request, path, settings.max_zip)
                result = await run_in_threadpool(archiver.submit, path)
                return JSONResponse(result, status_code=200 if result['already_exists'] else 201)
            except TimeoutError:
                raise ArchiveError('upload_timeout', 408, True) from None
            finally:
                archiver.cleanup()

    return app


def main():
    import uvicorn
    settings = Settings.from_env()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(settings.log_dir / 'service.log',
                                  maxBytes=10 * 1024 * 1024, backupCount=9, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger = logging.getLogger('jimu.cloud')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    for name in ('uvicorn', 'uvicorn.error'):
        server_logger = logging.getLogger(name)
        server_logger.handlers = [handler]
        server_logger.propagate = False
    # SDK/debug payloads and request headers must never enter application logs.
    logging.getLogger('alibabacloud_oss_v2').setLevel(logging.CRITICAL)
    uvicorn.run(create_app(settings), host='127.0.0.1', port=8767, workers=1,
                access_log=False, log_level='warning', log_config=None)


if __name__ == '__main__':
    main()
