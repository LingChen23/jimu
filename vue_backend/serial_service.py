"""Serial-port ownership and binary gateway-frame ingestion."""

from __future__ import annotations

from dataclasses import asdict
import secrets
import struct
import threading
import time
from typing import Any

import serial
from serial.tools import list_ports

from motion_model import MotionStore
from motion_protocol import (
    AGGREGATOR_HOST_CONTROL_START,
    AGGREGATOR_HOST_CONTROL_STOP,
    AggregatorDiagnostic,
    ControlResponse,
    GatewayStreamDecoder,
    LinkDiagnostic,
    MotionCommand,
    encode_aggregator_host_control,
    encode_gateway_command,
)


COMMAND_NAMES = {
    "START_STREAM": MotionCommand.START_STREAM,
    "STOP_STREAM": MotionCommand.STOP_STREAM,
    "GET_INFO": MotionCommand.GET_INFO,
    "GET_STATUS": MotionCommand.GET_STATUS,
    "RESET_STATS": MotionCommand.RESET_STATS,
    "RECALIBRATE": MotionCommand.RECALIBRATE,
    "REBOOT": MotionCommand.REBOOT,
}

PHY_NAMES = {
    1: "1M",
    2: "2M",
    3: "Coded",
}
RESULT_NAMES = {
    0: "成功",
    1: "命令不支持",
    2: "参数错误",
    3: "设备忙",
    4: "当前状态不允许",
    5: "设备离线",
    6: "网关转发失败",
}
RESET_REASON_BITS = {
    0: "复位引脚",
    1: "看门狗",
    2: "软件重启",
    3: "CPU 锁死",
    16: "低功耗唤醒",
    17: "比较器唤醒",
    18: "调试接口唤醒",
    19: "NFC 唤醒",
}

DISCONNECT_REASON_NAMES = {
    0x08: "连接监督超时",
    0x13: "nRF 主动断开",
    0x16: "ESP32 主动断开",
    0x22: "链路层响应超时",
    0x3B: "连接参数不可接受",
}


class SerialService:
    def __init__(
        self,
        store: MotionStore,
        device_ids: tuple[int, ...] | list[int] = tuple(range(1, 11)),
    ) -> None:
        self._store = store
        self._device_ids = tuple(int(device_id) for device_id in device_ids)
        self._serial: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._lock = threading.Lock()
        self._response_condition = threading.Condition(self._lock)
        # Only one control transaction may be in flight.  The reader thread
        # deliberately does not use this lock, so motion frames keep flowing
        # while a command waits for its response.
        self._command_lock = threading.Lock()
        self._port = ""
        self._baud = 921600
        self._error = ""
        self._byte_count = 0
        self._frame_count = 0
        self._gateway_lost_frames = 0
        self._last_gateway_sequence: dict[int, int] = {}
        self._decoder = GatewayStreamDecoder()
        self._decoder_counter_baseline = self._decoder_counters(self._decoder)
        self._request_id = 0
        self._responses: dict[tuple[int, int], ControlResponse] = {}
        self._pending_response_keys: set[tuple[int, int]] = set()
        self._device_control: dict[int, dict[str, Any]] = {}
        self._aggregator_diagnostic: dict[str, Any] | None = None
        self._aggregator_counter_baseline: dict[str, Any] | None = None
        self._host_session_id = 0
        self._host_session_ready = False

    @staticmethod
    def ports() -> list[dict[str, Any]]:
        return [
            {
                "device": port.device,
                "description": port.description or port.device,
                "native_usb": port.vid == 0x303A and port.pid == 0x1001,
            }
            for port in sorted(
                list_ports.comports(), key=lambda item: item.device
            )
        ]

    def open(self, port: str, baud: int) -> dict[str, Any]:
        with self._lifecycle_lock:
            self._close_locked()
            if not port:
                raise ValueError("请选择串口")
            if baud <= 0:
                raise ValueError("波特率必须大于 0")

            connection = serial.Serial()
            connection.port = port
            connection.baudrate = baud
            # Return bytes promptly instead of waiting for a 4096-byte batch.
            connection.timeout = 0.02
            connection.write_timeout = 1.0
            connection.dtr = False
            connection.rts = False
            connection.open()
            connection.reset_input_buffer()
            host_session_id = secrets.randbits(32) or 1
            stop_event = threading.Event()
            with self._lock:
                self._serial = connection
                self._port = port
                self._baud = baud
                self._error = ""
                self._byte_count = 0
                self._frame_count = 0
                self._gateway_lost_frames = 0
                self._last_gateway_sequence = {}
                decoder = GatewayStreamDecoder()
                self._decoder = decoder
                self._decoder_counter_baseline = self._decoder_counters(decoder)
                self._responses = {}
                self._pending_response_keys = set()
                self._device_control = {}
                self._aggregator_diagnostic = None
                self._aggregator_counter_baseline = None
                self._host_session_id = host_session_id
                self._host_session_ready = False
                self._stop = stop_event
            thread = threading.Thread(
                target=self._reader_loop,
                args=(connection, stop_event, host_session_id, decoder),
                name="motion-serial-reader",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            try:
                connection.write(
                    encode_aggregator_host_control(
                        AGGREGATOR_HOST_CONTROL_START,
                        host_session_id,
                    )
                )
                connection.flush()
            except (serial.SerialException, OSError) as error:
                self._close_locked()
                raise OSError(str(error)) from error
            return self.status()

    def close(self) -> dict[str, Any]:
        with self._lifecycle_lock:
            return self._close_locked()

    def _close_locked(self) -> dict[str, Any]:
        with self._lock:
            connection = self._serial
            host_session_id = self._host_session_id
            stop_event = self._stop
            thread = self._thread
        stop_event.set()
        if connection is not None and connection.is_open:
            try:
                connection.write(
                    encode_aggregator_host_control(
                        AGGREGATOR_HOST_CONTROL_STOP,
                        host_session_id,
                    )
                )
                connection.flush()
            except (serial.SerialException, OSError):
                pass
        with self._response_condition:
            if self._serial is connection:
                self._serial = None
                self._host_session_ready = False
                self._responses.clear()
            self._response_condition.notify_all()
        if connection is not None:
            try:
                connection.close()
            except serial.SerialException:
                pass
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=1.0)
        with self._lock:
            if self._thread is thread:
                self._thread = None
        if connection is not None:
            self._store.transport_discontinuity("serial_closed")
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            connection = self._serial
            decoder_counters = self._decoder_counters(self._decoder)
            return {
                "open": bool(connection is not None and connection.is_open),
                "port": self._port,
                "baud": self._baud,
                "error": self._error,
                "byte_count": self._byte_count,
                "frame_count": self._frame_count,
                "encoded_frame_count": max(
                    0,
                    decoder_counters["encoded_frame_count"]
                    - self._decoder_counter_baseline["encoded_frame_count"],
                ),
                "gateway_lost_frames": self._gateway_lost_frames,
                "cobs_error_count": max(
                    0,
                    decoder_counters["cobs_error_count"]
                    - self._decoder_counter_baseline["cobs_error_count"],
                ),
                "crc_error_count": max(
                    0,
                    decoder_counters["crc_error_count"]
                    - self._decoder_counter_baseline["crc_error_count"],
                ),
                "frame_error_count": max(
                    0,
                    decoder_counters["frame_error_count"]
                    - self._decoder_counter_baseline["frame_error_count"],
                ),
                "overflow_count": max(
                    0,
                    decoder_counters["overflow_count"]
                    - self._decoder_counter_baseline["overflow_count"],
                ),
                "host_session_ready": self._host_session_ready,
                "host_session_id": self._host_session_id,
                "aggregator": self._aggregator_snapshot_locked(),
            }

    def _aggregator_snapshot_locked(self) -> dict[str, Any] | None:
        if self._aggregator_diagnostic is None:
            return None
        snapshot = dict(self._aggregator_diagnostic)
        snapshot["links"] = [
            dict(link) for link in snapshot.get("links", [])
        ]
        self._apply_aggregator_counter_baseline_locked(snapshot)
        updated = snapshot.pop("_updated_monotonic", None)
        snapshot["age_seconds"] = (
            max(0.0, time.monotonic() - updated)
            if updated is not None
            else None
        )
        return snapshot

    @staticmethod
    def _decoder_counters(decoder: GatewayStreamDecoder) -> dict[str, int]:
        return {
            "encoded_frame_count": decoder.encoded_frame_count,
            "cobs_error_count": decoder.cobs_error_count,
            "crc_error_count": decoder.crc_error_count,
            "frame_error_count": decoder.frame_error_count,
            "overflow_count": decoder.overflow_count,
        }

    @staticmethod
    def _uint32_delta(current: Any, baseline: Any) -> int:
        return (int(current or 0) - int(baseline or 0)) & 0xFFFFFFFF

    @staticmethod
    def _saturating_delta(current: Any, baseline: Any) -> int:
        return max(0, int(current or 0) - int(baseline or 0))

    def _apply_aggregator_counter_baseline_locked(
        self,
        snapshot: dict[str, Any],
    ) -> None:
        baseline = self._aggregator_counter_baseline
        if baseline is None:
            return
        if int(snapshot.get("uptime_ms", 0)) < int(baseline.get("uptime_ms", 0)):
            # The aggregator restarted after the logical reset.  Its hardware
            # counters already have a fresh origin, so discard the old base.
            self._aggregator_counter_baseline = None
            return

        for key in (
            "usb_rx_bytes",
            "usb_tx_bytes",
            "usb_valid_commands",
            "usb_input_errors",
            "usb_output_drops",
        ):
            snapshot[key] = self._uint32_delta(
                snapshot.get(key), baseline.get(key)
            )
        snapshot["output_drop_seen"] = snapshot["usb_output_drops"] != 0

        baseline_links = {
            int(link.get("gateway_id", -1)): link
            for link in baseline.get("links", [])
        }
        for link in snapshot.get("links", []):
            base = baseline_links.get(int(link.get("gateway_id", -1)))
            if base is None:
                continue
            for key in ("uart_rx_bytes", "valid_frames"):
                link[key] = self._uint32_delta(link.get(key), base.get(key))
            for key in (
                "cobs_errors",
                "crc_errors",
                "frame_errors",
                "frame_drops",
                "uart_output_drops",
            ):
                link[key] = self._saturating_delta(
                    link.get(key), base.get(key)
                )

    def reset_transport_counters(self) -> dict[str, Any]:
        """Reset PC/aggregator display counters without touching the stream."""
        with self._lock:
            self._byte_count = 0
            self._frame_count = 0
            self._gateway_lost_frames = 0
            self._decoder_counter_baseline = self._decoder_counters(self._decoder)
            if self._aggregator_diagnostic is None:
                self._aggregator_counter_baseline = None
            else:
                self._aggregator_counter_baseline = dict(
                    self._aggregator_diagnostic
                )
                self._aggregator_counter_baseline["links"] = [
                    dict(link)
                    for link in self._aggregator_diagnostic.get("links", [])
                ]
        return self.status()

    def device_control_snapshot(self, device_id: int) -> dict[str, Any]:
        with self._lock:
            snapshot = dict(self._device_control.get(device_id, {}))
            link = snapshot.get("link")
            if isinstance(link, dict):
                link_snapshot = dict(link)
                updated = link_snapshot.pop("_updated_monotonic", None)
                link_snapshot["age_seconds"] = (
                    max(0.0, time.monotonic() - updated)
                    if updated is not None
                    else None
                )
                snapshot["link"] = link_snapshot
            return snapshot

    @staticmethod
    def _link_snapshot(diagnostic: LinkDiagnostic) -> dict[str, Any]:
        link = asdict(diagnostic)
        reason = diagnostic.last_disconnect_reason
        hci_reason = reason - 0x200 if 0x200 <= reason <= 0x2FF else reason
        if reason == 0:
            reason_text = "无"
        else:
            reason_text = DISCONNECT_REASON_NAMES.get(
                hci_reason, "未知原因"
            )
            reason_text = f"{reason_text} (0x{reason:04X})"
        link["last_disconnect_reason_text"] = reason_text
        link["link_layer_retransmissions"] = None
        link["link_layer_retransmissions_note"] = (
            "NimBLE 公开接口不提供链路层真实重传次数"
        )
        link["tx_phy_text"] = (
            PHY_NAMES.get(diagnostic.tx_phy, f"未知({diagnostic.tx_phy})")
            if diagnostic.tx_phy is not None
            else None
        )
        link["rx_phy_text"] = (
            PHY_NAMES.get(diagnostic.rx_phy, f"未知({diagnostic.rx_phy})")
            if diagnostic.rx_phy is not None
            else None
        )
        link["dle_request_result_text"] = (
            "成功"
            if diagnostic.dle_request_result == 0
            else (
                f"失败({diagnostic.dle_request_result})"
                if diagnostic.dle_request_result is not None
                else "未请求"
            )
        )
        link["_updated_monotonic"] = time.monotonic()
        return link

    @staticmethod
    def _command_payload(command: MotionCommand) -> bytes:
        if command == MotionCommand.START_STREAM:
            return struct.pack("<HBB", 100, 50, 0x07)
        if command == MotionCommand.REBOOT:
            return b"\xA5"
        return b""

    @staticmethod
    def _parse_response(response: ControlResponse) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        payload = response.payload
        if response.result == 0 and response.command == MotionCommand.GET_INFO:
            if len(payload) != 14:
                raise ValueError("GET_INFO 响应长度错误")
            parsed = {
                "device_id": payload[0],
                "firmware": f"{payload[1]}.{payload[2]}.{payload[3]}",
                "motion_protocol_version": payload[4],
                "accel_range_g": payload[5],
                "gyro_range_dps": struct.unpack_from("<H", payload, 6)[0],
                "sample_rate_hz": struct.unpack_from("<H", payload, 8)[0],
                "coordinate_rate_hz": payload[10],
                "capabilities": struct.unpack_from("<H", payload, 11)[0],
            }
        elif (
            response.result == 0
            and response.command == MotionCommand.GET_STATUS
        ):
            if len(payload) != 29:
                raise ValueError("GET_STATUS 响应长度错误")
            flags = payload[0]
            battery_mv = struct.unpack_from("<H", payload, 10)[0]
            reset_reason = struct.unpack_from("<I", payload, 2)[0]
            reset_labels = [
                label
                for bit, label in RESET_REASON_BITS.items()
                if reset_reason & (1 << bit)
            ]
            parsed = {
                "streaming": bool(flags & 0x01),
                "calibrated": bool(flags & 0x02),
                "sensor_ok": bool(flags & 0x04),
                "battery_low": bool(flags & 0x08),
                "reset_reason": reset_reason,
                "reset_reason_text": " / ".join(reset_labels)
                if reset_labels
                else "上电或未知",
                "uptime_seconds": struct.unpack_from("<I", payload, 6)[0],
                "battery_mV": None if battery_mv == 0xFFFF else battery_mv,
                "session_id": struct.unpack_from("<I", payload, 12)[0],
                "latest_sample_sequence": struct.unpack_from(
                    "<I", payload, 16
                )[0],
                "sample_gap_count": struct.unpack_from("<H", payload, 20)[0],
                "sensor_error_count": struct.unpack_from("<H", payload, 22)[0],
                "queue_drop_count": struct.unpack_from("<H", payload, 24)[0],
                "ble_resource_wait_count": struct.unpack_from(
                    "<H", payload, 26
                )[0],
                "queue_depth": payload[28],
            }
        return parsed

    def command(
        self,
        device_id: int,
        command_name: str,
        timeout_seconds: float = 3.0,
    ) -> dict[str, Any]:
        with self._command_lock:
            return self._command_locked(
                device_id, command_name, timeout_seconds
            )

    def reset_data_and_error_counters(
        self,
        device_id: int,
        timeout_seconds: float = 3.0,
    ) -> dict[str, Any]:
        """Reset device and receiver counters without requesting status data."""
        with self._command_lock:
            result = self._command_locked(
                device_id, "RESET_STATS", timeout_seconds
            )
            if not result.get("ok"):
                return result

            if not self._store.reset_packet_counters(device_id):
                raise ValueError("设备不存在")

            # RESET_STATS has an empty response payload.  Update only the
            # cached counters it owns so the UI reflects the acknowledged
            # reset without issuing GET_STATUS (which reads battery voltage).
            with self._lock:
                control = self._device_control.get(device_id)
                status = control.get("status") if control else None
                if isinstance(status, dict):
                    updated_status = dict(status)
                    for field in (
                        "sample_gap_count",
                        "sensor_error_count",
                        "queue_drop_count",
                        "ble_resource_wait_count",
                    ):
                        updated_status[field] = 0
                    control["status"] = updated_status
            return result

    def _command_locked(
        self,
        device_id: int,
        command_name: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        command = COMMAND_NAMES.get(command_name.upper())
        if command is None:
            raise ValueError("未知设备命令")

        with self._response_condition:
            connection = self._serial
            if connection is None or not connection.is_open:
                raise ValueError("串口未打开")
            if not self._host_session_ready:
                raise ValueError("聚合器会话尚未就绪")
            self._request_id = (self._request_id % 255) + 1
            request_id = self._request_id
            key = (device_id, request_id)
            # A response that arrived after an earlier timeout must never be
            # consumed when the 8-bit request identifier wraps around.
            self._responses.pop(key, None)
            self._pending_response_keys.add(key)
            frame = encode_gateway_command(
                device_id,
                command,
                request_id,
                self._command_payload(command),
            )
            try:
                try:
                    connection.write(frame)
                    connection.flush()
                except (serial.SerialException, OSError) as error:
                    raise OSError(str(error)) from error

                deadline = time.monotonic() + timeout_seconds
                while key not in self._responses:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"{command.name} 等待响应超时")
                    self._response_condition.wait(remaining)
                    if self._serial is not connection:
                        raise OSError("等待响应时串口已关闭或重新连接")
                response = self._responses.pop(key)
            finally:
                self._pending_response_keys.discard(key)
                self._responses.pop(key, None)

        parsed = self._parse_response(response)
        result = {
            "ok": response.result == 0,
            "device_id": response.device_id,
            "command": command.name,
            "request_id": response.request_id,
            "result": response.result,
            "message": RESULT_NAMES.get(response.result, "未知结果"),
            "data": parsed,
        }
        with self._lock:
            control = self._device_control.setdefault(device_id, {})
            control["last_command"] = command.name
            control["last_result"] = result["message"]
            if command == MotionCommand.GET_INFO and parsed:
                control["info"] = parsed
            elif command == MotionCommand.GET_STATUS and parsed:
                control["status"] = parsed
        return result

    def refresh_batteries(
        self,
        device_ids: list[int] | None = None,
        timeout_seconds: float = 1.5,
        interval_seconds: float = 0.1,
    ) -> dict[str, Any]:
        """Read battery voltage sequentially without pausing motion input.

        A failed or missing device is reported and skipped.  Holding the
        command lock across the whole scan prevents another UI command from
        being interleaved with the deliberately paced GET_STATUS requests.
        """
        targets = device_ids if device_ids is not None else list(self._device_ids)
        results: list[dict[str, Any]] = []
        with self._command_lock:
            for index, device_id in enumerate(targets):
                try:
                    response = self._command_locked(
                        device_id, "GET_STATUS", timeout_seconds
                    )
                    status = response.get("data", {})
                    battery_mv = status.get("battery_mV")
                    if not response.get("ok") or battery_mv is None:
                        results.append(
                            {
                                "device_id": device_id,
                                "ok": False,
                                "battery_mV": None,
                                "message": response.get("message")
                                or "未返回电池电压",
                            }
                        )
                    else:
                        results.append(
                            {
                                "device_id": device_id,
                                "ok": True,
                                "battery_mV": battery_mv,
                                "battery_low": status.get("battery_low"),
                                "message": "获取成功",
                            }
                        )
                except (TimeoutError, ValueError, OSError) as error:
                    results.append(
                        {
                            "device_id": device_id,
                            "ok": False,
                            "battery_mV": None,
                            "message": str(error),
                        }
                    )
                if index + 1 < len(targets):
                    # Leave the transport a quiet window after every response
                    # or timeout before issuing the next device command.
                    # This never blocks the independent serial reader thread.
                    time.sleep(interval_seconds)

        succeeded = sum(1 for item in results if item["ok"])
        return {
            "ok": True,
            "requested": len(targets),
            "succeeded": succeeded,
            "skipped": len(targets) - succeeded,
            "results": results,
        }

    def _reader_loop(
        self,
        connection: serial.Serial,
        stop_event: threading.Event,
        host_session_id: int,
        decoder: GatewayStreamDecoder | None = None,
    ) -> None:
        stream_decoder = decoder or GatewayStreamDecoder()
        while not stop_event.is_set():
            with self._lock:
                if self._serial is not connection:
                    return
            try:
                waiting = min(max(connection.in_waiting, 1), 4096)
                raw = connection.read(waiting)
                if not raw:
                    continue
                with self._lock:
                    self._byte_count += len(raw)
                for gateway_frame in stream_decoder.feed(raw):
                    host_control = gateway_frame.aggregator_host_control
                    if host_control is not None:
                        with self._response_condition:
                            if (
                                host_control.command
                                == AGGREGATOR_HOST_CONTROL_START
                                and host_control.session_id
                                == host_session_id
                            ):
                                self._host_session_ready = (
                                    host_control.result == 0
                                )
                                self._last_gateway_sequence = {}
                                if host_control.result != 0:
                                    self._error = (
                                        "聚合器拒绝启动 USB 数据会话"
                                    )
                                self._response_condition.notify_all()
                        if self._host_session_ready:
                            self._store.transport_discontinuity(
                                "aggregator_session_started"
                            )
                        continue
                    with self._lock:
                        host_session_ready = self._host_session_ready
                    if not host_session_ready:
                        continue
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
                    if gateway_frame.aggregator_diagnostic is not None:
                        diagnostic: AggregatorDiagnostic = (
                            gateway_frame.aggregator_diagnostic
                        )
                        snapshot = asdict(diagnostic)
                        snapshot["_updated_monotonic"] = time.monotonic()
                        with self._lock:
                            self._aggregator_diagnostic = snapshot
                        continue
                    if gateway_frame.control_response is not None:
                        response = gateway_frame.control_response
                        with self._response_condition:
                            key = (response.device_id, response.request_id)
                            if key in self._pending_response_keys:
                                self._responses[key] = response
                                self._response_condition.notify_all()
                        continue
                    if gateway_frame.link_diagnostic is not None:
                        diagnostic = gateway_frame.link_diagnostic
                        with self._lock:
                            control = self._device_control.setdefault(
                                diagnostic.device_id, {}
                            )
                            control["link"] = self._link_snapshot(diagnostic)
                        continue
                    if gateway_frame.motion is None:
                        continue
                    if not self._store.process(gateway_frame.motion):
                        continue
                    with self._lock:
                        self._frame_count += 1
            except (serial.SerialException, OSError) as error:
                if stop_event.is_set():
                    return
                active_connection = False
                with self._response_condition:
                    if self._serial is connection:
                        active_connection = True
                        self._error = str(error)
                        self._serial = None
                        self._host_session_ready = False
                        self._responses.clear()
                        self._response_condition.notify_all()
                stop_event.set()
                try:
                    connection.close()
                except (serial.SerialException, OSError):
                    pass
                if active_connection:
                    self._store.transport_discontinuity("serial_error")
                return
