"""Serial-port ownership and binary gateway-frame ingestion."""

from __future__ import annotations

import threading
from typing import Any

import serial
from serial.tools import list_ports

from motion_model import MotionStore
from motion_protocol import GatewayStreamDecoder


class SerialService:
    def __init__(self, store: MotionStore) -> None:
        self._store = store
        self._serial: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._port = ""
        self._baud = 921600
        self._error = ""
        self._byte_count = 0
        self._frame_count = 0
        self._gateway_lost_frames = 0
        self._last_gateway_sequence: dict[int, int] = {}
        self._decoder = GatewayStreamDecoder()

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

        connection = serial.Serial()
        connection.port = port
        connection.baudrate = baud
        connection.timeout = 0.2
        connection.dtr = False
        connection.rts = False
        connection.open()
        connection.reset_input_buffer()
        with self._lock:
            self._serial = connection
            self._port = port
            self._baud = baud
            self._error = ""
            self._byte_count = 0
            self._frame_count = 0
            self._gateway_lost_frames = 0
            self._last_gateway_sequence = {}
            self._decoder = GatewayStreamDecoder()
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
                "byte_count": self._byte_count,
                "frame_count": self._frame_count,
                "encoded_frame_count": self._decoder.encoded_frame_count,
                "gateway_lost_frames": self._gateway_lost_frames,
                "cobs_error_count": self._decoder.cobs_error_count,
                "crc_error_count": self._decoder.crc_error_count,
                "frame_error_count": self._decoder.frame_error_count,
                "overflow_count": self._decoder.overflow_count,
            }

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                connection = self._serial
            if connection is None:
                return
            try:
                raw = connection.read(4096)
                if not raw:
                    continue
                with self._lock:
                    self._byte_count += len(raw)
                for gateway_frame in self._decoder.feed(raw):
                    previous = self._last_gateway_sequence.get(
                        gateway_frame.gateway_id
                    )
                    if previous is not None:
                        expected = (previous + 1) & 0xFFFFFFFF
                        gap = (
                            gateway_frame.gateway_sequence - expected
                        ) & 0xFFFFFFFF
                        if 0 < gap < 0x80000000:
                            with self._lock:
                                self._gateway_lost_frames += gap
                    self._last_gateway_sequence[gateway_frame.gateway_id] = (
                        gateway_frame.gateway_sequence
                    )
                    if not self._store.process(gateway_frame.motion):
                        continue
                    with self._lock:
                        self._frame_count += 1
            except (serial.SerialException, OSError) as error:
                if self._stop.is_set():
                    return
                with self._lock:
                    self._error = str(error)
                self._stop.set()
                return
