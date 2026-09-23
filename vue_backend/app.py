"""Small standard-library HTTP backend for the Motion visualizer."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Callable
from urllib.parse import parse_qs, quote, urlencode, urlsplit
import webbrowser

from motion_model import ANALYSIS_MODE_PC, MotionStore
from serial_service import SerialService
from system_config import DEVICE_COUNT, DEVICE_IDS
from task_service import TaskService
from cloud_upload import CloudUploader
from cloud_upload.transport import HttpTransport, TransportError
from diagnostics import Diagnostics


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
RUNTIME_ROOT = Path(os.environ.get("MOTION_DATA_DIR", ROOT / "runtime")).resolve()
DEFAULTS_ROOT = ROOT / "defaults"


def _runtime_file(name: str) -> Path:
    path = RUNTIME_ROOT / name
    if not path.exists():
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        default = DEFAULTS_ROOT / name
        if default.exists():
            shutil.copyfile(default, path)
    return path


SETTINGS_PATH = _runtime_file("motion_settings.json")
BLOCK_MAPPING_PATH = _runtime_file("block_device_mapping.json")
TASK_RECORDS_PATH = _runtime_file("task_records.json")
DATABASE_PATH = Path(
    os.environ.get("MOTION_DATABASE_PATH", RUNTIME_ROOT / "motion_system.sqlite3")
).resolve()
MOVEMENT_ARCHIVE_ROOT = Path(
    os.environ.get("MOTION_MOVEMENT_ARCHIVE_DIR", RUNTIME_ROOT / "task_movements")
).resolve()
STORE = MotionStore(
    device_count=DEVICE_COUNT,
    default_analysis_mode=ANALYSIS_MODE_PC,
    settings_path=SETTINGS_PATH,
    mapping_path=BLOCK_MAPPING_PATH,
)
DIAGNOSTICS = Diagnostics(RUNTIME_ROOT)
SERIAL = SerialService(
    STORE, device_ids=DEVICE_IDS, diagnostic_sink=DIAGNOSTICS.record,
    task_collecting=lambda: next((str(task.get("task_id", "")) for task in TASKS.list()
                                  if task.get("status") == "collecting"), None),
)
TASKS = TaskService(
    TASK_RECORDS_PATH,
    STORE.snapshot,
    device_ids=DEVICE_IDS,
    database_path=DATABASE_PATH,
    movement_root=MOVEMENT_ARCHIVE_ROOT,
    require_participant_profile=True,
    movement_archived_callback=STORE.mark_movement_archived,
    diagnostic_sink=DIAGNOSTICS.record,
)


def _cloud_transport():
    try:
        return HttpTransport.from_file(RUNTIME_ROOT / "cloud_upload.json")
    except ValueError as error:
        print(f"Cloud upload disabled: {error}")
        return None


CLOUD_TRANSPORT = _cloud_transport()
DIAGNOSTICS.transport = CLOUD_TRANSPORT
CLOUD_UPLOADER = CloudUploader(
    list_tasks=TASKS.list,
    export_zip=TASKS.task_export_archive,
    runtime_root=RUNTIME_ROOT,
    transport=CLOUD_TRANSPORT,
    diagnostic_sink=DIAGNOSTICS.record,
)


def _record_api_error(path: str, error: BaseException, task_id: str = "") -> None:
    parsed = urlsplit(path)
    path = parsed.path
    query = parse_qs(parsed.query)
    task_id = task_id or (query.get("task") or query.get("task_id") or [""])[0]
    if path == "/api/serial/open" and isinstance(error, OSError):
        DIAGNOSTICS.record("serial_open_failed", "error", "serial_open", error=error)
    elif path == "/api/tasks/start" and not isinstance(error, ValueError):
        code = "task_save_failed" if isinstance(error, (OSError, sqlite3.Error)) else "task_start_failed"
        DIAGNOSTICS.record(code, "error", "task_start", error=error)
    elif path == "/api/tasks/finish" and not isinstance(error, ValueError):
        code = "task_save_failed" if isinstance(error, (OSError, sqlite3.Error)) else "task_finish_failed"
        DIAGNOSTICS.record(code, "error", "task_finish", error=error, task_id=task_id)
    elif path.startswith("/api/tasks/export"):
        DIAGNOSTICS.record("zip_export_failed", "error", "task_export",
                           error=error, task_id=task_id)
    elif isinstance(error, TransportError):
        DIAGNOSTICS.record("cloud_read_failed", "warning", "cloud_read",
                           error=error, task_id=task_id)
    elif not isinstance(error, ValueError):
        DIAGNOSTICS.record("unexpected_backend_error", "error", "http_request",
                           error=error, task_id=task_id)


def _cloud_json(path: str) -> dict:
    if CLOUD_TRANSPORT is None:
        raise TransportError("cloud_upload_disabled", False)
    return CLOUD_TRANSPORT.json("GET", path)


def _cloud_bytes(method: str, path: str, body: dict | None = None) -> tuple[bytes, str]:
    if CLOUD_TRANSPORT is None:
        raise TransportError("cloud_upload_disabled", False)
    return CLOUD_TRANSPORT.bytes(method, path, body)


class ApiHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".svg": "image/svg+xml",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_ROOT), **kwargs)

    def log_message(self, format_string: str, *args) -> None:
        return

    def _json_response(
        self, payload: object, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _download_response(
        self,
        content: str | bytes,
        content_type: str,
        filename: str,
    ) -> None:
        encoded = (
            content
            if isinstance(content, bytes)
            else content.encode(
                "utf-8-sig" if content_type.startswith("text/csv") else "utf-8"
            )
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 65536:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    @staticmethod
    def _integer(query: dict[str, list[str]], name: str, default: int) -> int:
        try:
            return int(query.get(name, [str(default)])[0])
        except (TypeError, ValueError):
            return default

    def do_GET(self) -> None:
        try:
            self._do_GET()
        except Exception as error:
            _record_api_error(self.path, error)
            raise

    def _do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/ports":
            self._json_response(
                {"ports": SERIAL.ports(), "serial": SERIAL.status()}
            )
            return
        if path == "/api/status":
            self._json_response(SERIAL.status())
            return
        if path == "/api/settings":
            self._json_response(STORE.settings_snapshot())
            return
        if path == "/api/block-mapping":
            self._json_response(STORE.block_mapping_snapshot())
            return
        if path == "/api/devices":
            self._json_response(
                {
                    "devices": STORE.devices_snapshot(),
                    "serial": SERIAL.status(),
                    "controls": {
                        str(device_id): SERIAL.device_control_snapshot(device_id)
                        for device_id in DEVICE_IDS
                    },
                }
            )
            return
        if path == "/api/tasks":
            self._json_response({"tasks": TASKS.list()})
            return
        if path == "/api/cloud-history/tasks":
            try:
                page = max(1, self._integer(query, "page", 1))
                page_size = max(1, min(10, self._integer(query, "page_size", 10)))
                payload = _cloud_json(
                    f"/api/history/tasks?page={page}&page_size={page_size}"
                )
                items = [
                    item.get("task", item) for item in payload.get("items", [])
                ]
            except TransportError as error:
                self._json_response({"error": error.code}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self._json_response(
                {
                    "tasks": items,
                    "total": int(payload.get("total", len(items))),
                    "page": page,
                    "page_size": page_size,
                }
            )
            return
        if path == "/api/cloud-upload/status":
            task_ids = [str(task.get("task_id", "")) for task in TASKS.list()]
            self._json_response(CLOUD_UPLOADER.statuses(task_ids))
            return
        if path == "/api/participants":
            search = query.get("search", [""])[0]
            self._json_response({"participants": TASKS.participants(search)})
            return
        if path == "/api/participants/detail":
            participant_id = query.get("participant_id", [""])[0]
            profile = TASKS.participant(participant_id)
            if profile is None:
                self._json_response(
                    {"error": "未找到体验者档案"}, HTTPStatus.NOT_FOUND
                )
                return
            self._json_response({"participant": profile})
            return
        if path == "/api/tasks/current":
            device_id = max(1, min(DEVICE_COUNT, self._integer(query, "device", 1)))
            self._json_response({"task": TASKS.current(device_id)})
            return
        if path == "/api/tasks/result":
            task_id = query.get("task", [""])[0]
            result = TASKS.result(task_id)
            if result is None:
                try:
                    result = _cloud_json(f"/api/tasks/result?task={quote(task_id)}")
                except TransportError as error:
                    self._json_response({"error": error.code}, HTTPStatus.SERVICE_UNAVAILABLE)
                    return
            self._json_response(result)
            return
        if path == "/api/tasks/movement-detail":
            task_id = query.get("task", [""])[0]
            device_id = max(1, min(DEVICE_COUNT, self._integer(query, "device", 1)))
            movement_id = self._integer(query, "movement", 0)
            mode = query.get("mode", ["pc"])[0]
            detail = TASKS.movement_detail(
                task_id, device_id, movement_id, mode
            )
            if detail is None:
                try:
                    detail = _cloud_json("/api/tasks/movement-detail?" + urlencode({
                        "task": task_id, "device": device_id,
                        "movement": movement_id, "mode": mode,
                    }))
                except TransportError as error:
                    self._json_response({"error": error.code}, HTTPStatus.SERVICE_UNAVAILABLE)
                    return
            self._json_response(detail)
            return
        if path in {
            "/api/tasks/movement-export.csv",
            "/api/tasks/movement-export.json",
        }:
            task_id = query.get("task", [""])[0]
            device_id = max(1, min(DEVICE_COUNT, self._integer(query, "device", 1)))
            movement_id = self._integer(query, "movement", 0)
            mode = query.get("mode", ["pc"])[0]
            export_format = "csv" if path.endswith(".csv") else "json"
            content = TASKS.movement_export(
                task_id, device_id, movement_id, mode, export_format
            )
            if content is None:
                try:
                    content, _ = _cloud_bytes("GET", f"/api/tasks/movement-export.{export_format}?" + urlencode({
                        "task": task_id, "device": device_id,
                        "movement": movement_id, "mode": mode,
                    }))
                except TransportError as error:
                    self._json_response({"error": error.code}, HTTPStatus.SERVICE_UNAVAILABLE)
                    return
            filename = (
                f"task_{task_id}_device_{device_id}_movement_{movement_id}."
                f"{export_format}"
            )
            content_type = (
                "text/csv; charset=utf-8"
                if export_format == "csv"
                else "application/json; charset=utf-8"
            )
            self._download_response(content, content_type, filename)
            return
        if path == "/api/tasks/export.zip":
            task_id = query.get("task", [""])[0]
            content = TASKS.task_export_archive(task_id)
            if content is None:
                try:
                    content, _ = _cloud_bytes("GET", f"/api/tasks/export.zip?task={quote(task_id)}")
                except TransportError as error:
                    self._json_response({"error": error.code}, HTTPStatus.SERVICE_UNAVAILABLE)
                    return
            self._download_response(
                content,
                "application/zip",
                f"task_{task_id}.zip",
            )
            return
        if path == "/api/snapshot":
            device_id = max(1, min(DEVICE_COUNT, self._integer(query, "device", 1)))
            payload = STORE.snapshot(device_id)
            payload["serial"] = SERIAL.status()
            payload["control"] = SERIAL.device_control_snapshot(device_id)
            self._json_response(payload)
            return
        if path == "/api/live":
            device_id = max(1, min(DEVICE_COUNT, self._integer(query, "device", 1)))
            revision = self._integer(query, "trajectory_revision", -1)
            received_after = self._integer(query, "received_after", -1)
            calculated_after = self._integer(query, "calculated_after", -1)
            self._json_response(STORE.live_snapshot(
                device_id,
                revision if revision >= 0 else None,
                received_after if received_after >= 0 else None,
                calculated_after if calculated_after >= 0 else None,
            ))
            return
        if path in {
            "/api/movement/detail",
            "/api/movement/export.csv",
            "/api/movement/export.json",
        }:
            device_id = max(1, min(DEVICE_COUNT, self._integer(query, "device", 1)))
            movement_id = self._integer(query, "movement", 0)
            mode = query.get("mode", [None])[0]
            if movement_id <= 0:
                self._json_response(
                    {"error": "movement 参数无效"}, HTTPStatus.BAD_REQUEST
                )
                return
            if path == "/api/movement/detail":
                detail = STORE.movement_detail(
                    device_id, movement_id, mode
                )
                if detail is None:
                    self._json_response(
                        {"error": "未找到该移动记录"}, HTTPStatus.NOT_FOUND
                    )
                    return
                self._json_response(detail)
                return
            export_format = "csv" if path.endswith(".csv") else "json"
            content = STORE.movement_export(
                device_id, movement_id, export_format, mode
            )
            if content is None:
                self._json_response(
                    {"error": "未找到该移动记录"}, HTTPStatus.NOT_FOUND
                )
                return
            filename = (
                f"device_{device_id}_movement_{movement_id}_{mode or 'active'}."
                f"{export_format}"
            )
            content_type = (
                "text/csv; charset=utf-8"
                if export_format == "csv"
                else "application/json; charset=utf-8"
            )
            self._download_response(content, content_type, filename)
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        body = {}
        try:
            body = self._read_json()
            if self.path == "/api/diagnostics/event":
                code = body.get("code")
                operation = body.get("operation", "browser_page")
                if operation not in ("browser_page", "realtime", "history", "results", "devices"):
                    operation = "browser_page"
                if code in ("page_render_failed", "page_request_timeout"):
                    DIAGNOSTICS.record(
                        code, "error" if code == "page_render_failed" else "warning",
                        operation, task_id=body.get("task_id", ""),
                    )
                    self._json_response({"ok": True})
                else:
                    self._json_response({"error": "invalid_diagnostic_event"}, HTTPStatus.BAD_REQUEST)
                return
            if self.path == "/api/serial/open":
                result = SERIAL.open(
                    str(body.get("port", "")),
                    int(body.get("baud", 921600)),
                )
                self._json_response(result)
                return
            if self.path == "/api/serial/close":
                self._json_response(SERIAL.close())
                return
            if self.path == "/api/serial/reset-counters":
                self._json_response(SERIAL.reset_transport_counters())
                return
            if self.path == "/api/device/reset":
                device_id = max(1, min(DEVICE_COUNT, int(body.get("device", 1))))
                if not STORE.reset_analysis(device_id):
                    raise ValueError("设备不存在")
                self._json_response(
                    {
                        "ok": True,
                        "device_id": device_id,
                        "snapshot": STORE.snapshot(device_id),
                    }
                )
                return
            if self.path == "/api/device/reset-packet-counters":
                device_id = max(1, min(DEVICE_COUNT, int(body.get("device", 1))))
                if not STORE.reset_packet_counters(device_id):
                    raise ValueError("设备不存在")
                self._json_response(
                    {
                        "ok": True,
                        "device_id": device_id,
                        "snapshot": STORE.snapshot(device_id),
                    }
                )
                return
            if self.path == "/api/device/reset-data-error-counters":
                device_id = max(1, min(DEVICE_COUNT, int(body.get("device", 1))))
                result = SERIAL.reset_data_and_error_counters(device_id)
                if not result.get("ok"):
                    raise ValueError(str(result.get("message", "计数清零失败")))
                self._json_response(
                    {
                        "ok": True,
                        "device_id": device_id,
                        "snapshot": STORE.snapshot(device_id),
                        "control": SERIAL.device_control_snapshot(device_id),
                    }
                )
                return
            if self.path == "/api/device/command":
                device_id = max(1, min(DEVICE_COUNT, int(body.get("device", 1))))
                command = str(body.get("command", "")).upper()
                result = SERIAL.command(device_id, command)
                result["control"] = SERIAL.device_control_snapshot(device_id)
                self._json_response(result)
                return
            if self.path == "/api/devices/battery-refresh":
                self._json_response(SERIAL.refresh_batteries())
                return
            if self.path == "/api/device/mode":
                device_id = max(1, min(DEVICE_COUNT, int(body.get("device", 1))))
                mode = str(body.get("mode", ""))
                if not STORE.set_analysis_mode(device_id, mode):
                    raise ValueError("设备不存在")
                self._json_response(
                    {
                        "ok": True,
                        "device_id": device_id,
                        "snapshot": STORE.snapshot(device_id),
                    }
                )
                return
            if self.path == "/api/settings":
                result = STORE.set_minimum_movement_seconds(
                    float(body.get("minimum_movement_seconds", 0.3))
                )
                self._json_response({"ok": True, **result})
                return
            if self.path == "/api/block-mapping":
                devices = body.get("devices")
                if not isinstance(devices, list):
                    raise ValueError("devices 必须是数组")
                version = str(body.get("mapping_version", "")).strip()
                self._json_response(
                    {
                        "ok": True,
                        **STORE.update_block_mapping(devices, version),
                    }
                )
                return
            if self.path == "/api/tasks/start":
                device_id = int(body.get("device_id", 0))
                task = TASKS.start(
                    device_id,
                    str(body.get("participant_id", "")),
                    str(body.get("note", "")),
                    body.get("participant_profile")
                    if isinstance(body.get("participant_profile"), dict)
                    else None,
                )
                STORE.set_task_collection_active(True)
                self._json_response(
                    {
                        "ok": True,
                        "task": task,
                    }
                )
                return
            if self.path == "/api/tasks/finish":
                task_id = str(body.get("task_id", ""))
                current_task = next(
                    (task for task in TASKS.list() if task.get("task_id") == task_id),
                    None,
                )
                if current_task is not None:
                    deadline = time.monotonic() + 3.5
                    while time.monotonic() < deadline:
                        active = any(
                            STORE.snapshot(int(device_id)).get("current_movement")
                            is not None
                            for device_id in current_task.get(
                                "device_ids", [current_task.get("device_id", 1)]
                            )
                        )
                        if not active:
                            break
                        time.sleep(0.1)
                STORE.wait_for_finalizations(timeout_seconds=5.0)
                task = TASKS.finish(task_id)
                STORE.set_task_collection_active(False)
                try:
                    CLOUD_UPLOADER.enqueue([task_id])
                except (OSError, sqlite3.Error) as error:
                    # Cloud queue availability must never undo a completed task.
                    DIAGNOSTICS.record("cloud_archive_failed", "error", "queue_task",
                                       error=error, task_id=task_id)
                self._json_response(
                    {
                        "ok": True,
                        "task": task,
                    }
                )
                return
            if self.path == "/api/cloud-upload/enqueue":
                task_ids = body.get("task_ids")
                if not isinstance(task_ids, list):
                    raise ValueError("task_ids 必须是数组")
                self._json_response(
                    {"ok": True, "tasks": CLOUD_UPLOADER.enqueue(task_ids)}
                )
                return
            if self.path == "/api/tasks/export-batch.zip":
                task_ids = body.get("task_ids")
                if not isinstance(task_ids, list):
                    raise ValueError("task_ids 必须是数组")
                def cloud_archive(identifier: str) -> bytes:
                    return _cloud_bytes("GET", f"/api/tasks/export.zip?task={quote(identifier)}")[0]
                content = TASKS.tasks_export_archive(task_ids, fallback=cloud_archive)
                self._download_response(
                    content,
                    "application/zip",
                    "tasks_batch.zip",
                )
                return
            if self.path == "/api/tasks/participant":
                profile = body.get("participant_profile")
                if not isinstance(profile, dict):
                    raise ValueError("participant_profile 必须是对象")
                self._json_response(
                    {
                        "ok": True,
                        "task": TASKS.update_task_participant(
                            str(body.get("task_id", "")),
                            profile,
                        ),
                    }
                )
                return
            if self.path == "/api/tasks/delete":
                task_id = str(body.get("task_id", ""))
                CLOUD_UPLOADER.prepare_delete(task_id)
                self._json_response(
                    {
                        "ok": True,
                        "task": TASKS.delete(task_id),
                    }
                )
                return
            self._json_response(
                {"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND
            )
        except TransportError as error:
            _record_api_error(self.path, error,
                              str(body.get("task_id", "")) if isinstance(body, dict) else "")
            self._json_response({"error": error.code}, HTTPStatus.SERVICE_UNAVAILABLE)
        except (ValueError, OSError, TypeError) as error:
            _record_api_error(self.path, error,
                              str(body.get("task_id", "")) if isinstance(body, dict) else "")
            self._json_response(
                {"error": str(error)}, HTTPStatus.BAD_REQUEST
            )
        except Exception as error:
            _record_api_error(self.path, error,
                              str(body.get("task_id", "")) if isinstance(body, dict) else "")
            raise


def main(
    on_started: Callable[[str, ThreadingHTTPServer], None] | None = None,
) -> None:
    parser = argparse.ArgumentParser(description="Motion V1 PC visualizer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    arguments = parser.parse_args()

    server = ThreadingHTTPServer((arguments.host, arguments.port), ApiHandler)
    try:
        if shutil.disk_usage(RUNTIME_ROOT).free < 2 * 1024 * 1024 * 1024:
            DIAGNOSTICS.record("disk_space_low", "warning", "app_start")
    except OSError:
        pass
    DIAGNOSTICS.start()
    CLOUD_UPLOADER.start()
    url = f"http://{arguments.host}:{arguments.port}"
    print(f"Motion visualizer: {url}")
    if on_started is not None:
        on_started(url, server)
    if not arguments.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        CLOUD_UPLOADER.close()
        SERIAL.close()
        STORE.wait_for_finalizations(timeout_seconds=2.0)
        DIAGNOSTICS.close()
        server.server_close()


if __name__ == "__main__":
    main()
