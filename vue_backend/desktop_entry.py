"""Desktop entry point used by the Windows and macOS applications."""

from __future__ import annotations

import threading
import webbrowser

from PIL import Image, ImageDraw
import pystray
from desktop_runtime import SingleInstance, initialize_runtime


RUNTIME_ROOT = initialize_runtime()

from diagnostics import Diagnostics  # noqa: E402

APP_URL = "http://127.0.0.1:8765/"


def _tray_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), "#2563eb")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((10, 10, 54, 54), radius=8, fill="#ffffff")
    draw.rectangle((18, 18, 31, 31), fill="#2563eb")
    draw.rectangle((34, 18, 47, 31), fill="#60a5fa")
    draw.rectangle((18, 34, 31, 47), fill="#60a5fa")
    draw.rectangle((34, 34, 47, 47), fill="#2563eb")
    return image


class DesktopApplication:
    """Own the tray interface and the lifecycle of the local HTTP backend."""

    def __init__(self) -> None:
        self._url = APP_URL
        self._server = None
        self._exit_requested = False
        self._icon = pystray.Icon(
            "JimuMotion",
            _tray_image(),
            "积木运动采集",
            menu=pystray.Menu(
                pystray.MenuItem("打开界面", self.open_interface, default=True),
                pystray.MenuItem("退出程序", self.exit_application),
            ),
        )

    def run(self) -> None:
        backend = threading.Thread(
            target=self._run_backend,
            name="jimu-http-backend",
            daemon=True,
        )
        backend.start()
        self._icon.run()
        backend.join()

    def _run_backend(self) -> None:
        try:
            main(on_started=self._on_started)
        except Exception as error:
            DIAGNOSTICS.record("app_start_failed", "error", "backend_start", error=error)
        finally:
            self._icon.stop()

    def _on_started(self, url, server) -> None:
        self._url = f"{url}/"
        self._server = server
        if self._exit_requested:
            threading.Thread(target=server.shutdown, daemon=True).start()

    def open_interface(self, _icon=None, _item=None) -> None:
        webbrowser.open(self._url)

    def exit_application(self, icon, _item) -> None:
        self._exit_requested = True
        server = self._server
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()
        icon.stop()


if __name__ == "__main__":
    instance = SingleInstance(RUNTIME_ROOT)
    try:
        if instance.already_running:
            webbrowser.open(APP_URL)
        else:
            try:
                from app import DIAGNOSTICS, main
            except Exception as error:
                Diagnostics(RUNTIME_ROOT).record(
                    "app_start_failed", "error", "app_import", error=error)
                raise
            DesktopApplication().run()
    finally:
        instance.close()
