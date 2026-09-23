"""Windows desktop entry point used by the packaged PC application."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import threading
import webbrowser

from PIL import Image, ImageDraw
import pystray


def _application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


# Keep databases, exports and upload state beside the executable. PyInstaller's
# bundled source directory is internal implementation data and must stay read-only.
os.environ.setdefault("MOTION_DATA_DIR", str(_application_root() / "runtime"))

from diagnostics import Diagnostics  # noqa: E402

try:
    from app import DIAGNOSTICS, main  # noqa: E402
except Exception as error:
    Diagnostics(_application_root() / "runtime").record(
        "app_start_failed", "error", "app_import", error=error)
    raise


APP_URL = "http://127.0.0.1:8765/"
MUTEX_NAME = "Local\\JimuMotionPC"
ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Keep exactly one packaged desktop backend alive per Windows session."""

    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = (ctypes.c_void_p,)
        self._close_handle.restype = ctypes.c_bool
        self._handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self._handle:
            self._close_handle(self._handle)
            self._handle = None


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
    instance = SingleInstance()
    try:
        if instance.already_running:
            webbrowser.open(APP_URL)
        else:
            DesktopApplication().run()
    finally:
        instance.close()
