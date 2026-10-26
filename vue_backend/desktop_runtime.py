"""Desktop data location and single-instance ownership on Windows and macOS."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


MUTEX_NAME = "Local\\JimuMotionPC"


def initialize_runtime() -> Path:
    if sys.platform == "darwin":
        default = Path.home() / "Library" / "Application Support" / "JimuMotion"
    else:
        root = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
        default = root / "runtime"
    runtime = Path(os.environ.setdefault("MOTION_DATA_DIR", str(default))).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    bundled_config = Path(__file__).resolve().parent / "defaults" / "cloud_upload.json"
    config = runtime / "cloud_upload.json"
    if bundled_config.is_file() and not config.exists():
        shutil.copyfile(bundled_config, config)
        config.chmod(0o600)
    return runtime


class SingleInstance:
    def __init__(self, runtime: Path) -> None:
        self._handle = None
        self._lock_file = None
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            self._close_handle = kernel32.CloseHandle
            self._close_handle.argtypes = (ctypes.c_void_p,)
            self._close_handle.restype = ctypes.c_bool
            self._handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
            if not self._handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self.already_running = ctypes.get_last_error() == 183
        else:
            import fcntl

            self._lock_file = (runtime / ".instance.lock").open("a+b")
            try:
                fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.already_running = False
            except BlockingIOError:
                self.already_running = True
                self._lock_file.close()
                self._lock_file = None

    def close(self) -> None:
        if self._handle:
            self._close_handle(self._handle)
            self._handle = None
        if self._lock_file:
            self._lock_file.close()
            self._lock_file = None
