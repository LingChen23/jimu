"""Small standard-library HTTP backend for the Motion visualizer."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

from motion_model import ANALYSIS_MODE_PC, MotionStore
from serial_service import SerialService


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
SETTINGS_PATH = ROOT / "motion_settings.json"
STORE = MotionStore(
    device_count=12,
    default_analysis_mode=ANALYSIS_MODE_PC,
    settings_path=SETTINGS_PATH,
)
SERIAL = SerialService(STORE)


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
            if self.path == "/api/device/command":
                device_id = max(1, min(12, int(body.get("device", 1))))
                command = str(body.get("command", "")).upper()
                result = SERIAL.command(device_id, command)
                result["control"] = SERIAL.device_control_snapshot(device_id)
                self._json_response(result)
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
        server.server_close()


if __name__ == "__main__":
    main()
