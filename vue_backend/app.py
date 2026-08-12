"""Small standard-library HTTP backend for the Motion visualizer."""

from __future__ import annotations

import argparse
import shutil
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from motion_model import ANALYSIS_MODE_PC, MotionStore
from serial_service import SerialService
from task_service import TaskService


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
RUNTIME_ROOT = ROOT / "runtime"
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
STORE = MotionStore(
    device_count=12,
    default_analysis_mode=ANALYSIS_MODE_PC,
    settings_path=SETTINGS_PATH,
    mapping_path=BLOCK_MAPPING_PATH,
)
SERIAL = SerialService(STORE)
TASKS = TaskService(TASK_RECORDS_PATH, STORE.snapshot)


class ApiHandler(SimpleHTTPRequestHandler):
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
        content: str,
        content_type: str,
        filename: str,
    ) -> None:
        encoded = content.encode("utf-8-sig" if content_type.startswith("text/csv") else "utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 4096:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    @staticmethod
    def _integer(query: dict[str, list[str]], name: str, default: int) -> int:
        try:
            return int(query.get(name, [str(default)])[0])
        except (TypeError, ValueError):
            return default

    def do_GET(self) -> None:
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
                        for device_id in range(1, 13)
                    },
                }
            )
            return
        if path == "/api/tasks":
            self._json_response({"tasks": TASKS.list()})
            return
        if path == "/api/tasks/current":
            device_id = max(1, min(12, self._integer(query, "device", 1)))
            self._json_response({"task": TASKS.current(device_id)})
            return
        if path == "/api/tasks/result":
            task_id = query.get("task", [""])[0]
            result = TASKS.result(task_id)
            if result is None:
                self._json_response(
                    {"error": "未找到采集任务"}, HTTPStatus.NOT_FOUND
                )
                return
            self._json_response(result)
            return
        if path == "/api/snapshot":
            device_id = max(1, min(12, self._integer(query, "device", 1)))
            payload = STORE.snapshot(device_id)
            payload["serial"] = SERIAL.status()
            payload["control"] = SERIAL.device_control_snapshot(device_id)
            self._json_response(payload)
            return
        if path == "/api/live":
            device_id = max(1, min(12, self._integer(query, "device", 1)))
            self._json_response(STORE.live_snapshot(device_id))
            return
        if path in {
            "/api/movement/detail",
            "/api/movement/export.csv",
            "/api/movement/export.json",
        }:
            device_id = max(1, min(12, self._integer(query, "device", 1)))
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
        try:
            body = self._read_json()
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
            if self.path == "/api/device/reset":
                device_id = max(1, min(12, int(body.get("device", 1))))
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
                device_id = max(1, min(12, int(body.get("device", 1))))
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
                device_id = max(1, min(12, int(body.get("device", 1))))
                # Clear the four counters owned by the nRF first, verify them
                # with a fresh status response, then rebase the two counters
                # maintained by the PC receiver.
                SERIAL.command(device_id, "RESET_STATS")
                status_result = SERIAL.command(device_id, "GET_STATUS")
                if not status_result.get("ok"):
                    raise ValueError(str(status_result.get("message", "设备状态读取失败")))
                if not STORE.reset_packet_counters(device_id):
                    raise ValueError("设备不存在")
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
                device_id = max(1, min(12, int(body.get("device", 1))))
                command = str(body.get("command", "")).upper()
                result = SERIAL.command(device_id, command)
                result["control"] = SERIAL.device_control_snapshot(device_id)
                self._json_response(result)
                return
            if self.path == "/api/devices/battery-refresh":
                self._json_response(SERIAL.refresh_batteries())
                return
            if self.path == "/api/device/mode":
                device_id = max(1, min(12, int(body.get("device", 1))))
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
                self._json_response(
                    {
                        "ok": True,
                        "task": TASKS.start(
                            device_id,
                            str(body.get("participant_id", "")),
                            str(body.get("note", "")),
                        ),
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
                        snapshot = STORE.snapshot(int(current_task["device_id"]))
                        if snapshot.get("current_movement") is None:
                            break
                        time.sleep(0.1)
                STORE.wait_for_finalizations(timeout_seconds=1.0)
                self._json_response(
                    {
                        "ok": True,
                        "task": TASKS.finish(task_id),
                    }
                )
                return
            self._json_response(
                {"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND
            )
        except (ValueError, OSError) as error:
            self._json_response(
                {"error": str(error)}, HTTPStatus.BAD_REQUEST
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Motion V1 PC visualizer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    arguments = parser.parse_args()

    server = ThreadingHTTPServer((arguments.host, arguments.port), ApiHandler)
    url = f"http://{arguments.host}:{arguments.port}"
    print(f"Motion visualizer: {url}")
    if not arguments.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SERIAL.close()
        STORE.wait_for_finalizations(timeout_seconds=2.0)
        server.server_close()


if __name__ == "__main__":
    main()
