"""Small standard-library HTTP backend for the Motion visualizer."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import webbrowser

from motion_model import MotionStore
from serial_service import SerialService


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
STORE = MotionStore(device_count=12)
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

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 4096:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        if path == "/api/ports":
            self._json_response(
                {"ports": SERIAL.ports(), "serial": SERIAL.status()}
            )
            return
        if path == "/api/status":
            self._json_response(SERIAL.status())
            return
        if path == "/api/snapshot":
            device_id = 1
            for item in query.split("&"):
                key, _, value = item.partition("=")
                if key == "device":
                    try:
                        device_id = max(1, min(12, int(value)))
                    except ValueError:
                        device_id = 1
            payload = STORE.snapshot(device_id)
            payload["serial"] = SERIAL.status()
            self._json_response(payload)
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
                    int(body.get("baud", 460800)),
                )
                self._json_response(result)
                return
            if self.path == "/api/serial/close":
                self._json_response(SERIAL.close())
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
