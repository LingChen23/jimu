"""Serial-port ownership and background Motion line ingestion."""

from __future__ import annotations

import threading
from typing import Any

import serial
from serial.tools import list_ports

from motion_model import MotionStore
from motion_protocol import parse_serial_line


class SerialService:
    def __init__(self, store: MotionStore) -> None:
        self._store = store
        self._serial: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._port = ""
        self._baud = 460800
        self._error = ""
        self._line_count = 0
        self._frame_count = 0

    @staticmethod
    def ports() -> list[dict[str, str]]:
        return [
            {
                "device": port.device,
                "description": port.description or port.device,
            }
            for port in sorted(list_ports.comports(), key=lambda item: item.device)
        ]

    def open(self, port: str, baud: int) -> dict[str, Any]:
        self.close()
        if not port:
            raise ValueError("请选择串口")
        if baud <= 0:
            raise ValueError("波特率必须大于 0")

        connection = serial.Serial(port=port, baudrate=baud, timeout=0.2)
        connection.dtr = False
        connection.rts = False
        with self._lock:
            self._serial = connection
            self._port = port
            self._baud = baud
            self._error = ""
            self._line_count = 0
            self._frame_count = 0
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._reader_loop,
            name="motion-serial-reader",
            daemon=True,
        )
        self._thread.start()
        return self.status()

    def close(self) -> dict[str, Any]:
        self._stop.set()
        with self._lock:
            connection = self._serial
            self._serial = None
        if connection is not None:
            try:
                connection.close()
            except serial.SerialException:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            connection = self._serial
            return {
                "open": bool(connection is not None and connection.is_open),
                "port": self._port,
                "baud": self._baud,
                "error": self._error,
                "line_count": self._line_count,
                "frame_count": self._frame_count,
            }

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                connection = self._serial
            if connection is None:
                return
            try:
                raw = connection.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore")
                frame = parse_serial_line(line)
                with self._lock:
                    self._line_count += 1
                if frame is not None and self._store.process(frame):
                    with self._lock:
                        self._frame_count += 1
            except (serial.SerialException, OSError) as error:
                with self._lock:
                    self._error = str(error)
                self._stop.set()
                return
