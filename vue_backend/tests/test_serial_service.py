from __future__ import annotations

from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from serial_service import SerialService


class FakeStore:
    def __init__(self) -> None:
        self.discontinuities: list[str] = []
        self.packet_counter_resets: list[int] = []

    def transport_discontinuity(self, reason: str) -> None:
        self.discontinuities.append(reason)

    def reset_packet_counters(self, device_id: int) -> bool:
        self.packet_counter_resets.append(device_id)
        return True


class FailingConnection:
    def __init__(self) -> None:
        self.is_open = True
        self.closed = False
        self.writes: list[bytes] = []

    @property
    def in_waiting(self) -> int:
        raise serial.SerialException("device disconnected")

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    def flush(self) -> None:
        return

    def close(self) -> None:
        self.closed = True
        self.is_open = False


class SerialServiceLifecycleTests(unittest.TestCase):
    def test_reader_failure_closes_active_connection_and_clears_session(self) -> None:
        store = FakeStore()
        service = SerialService(store)  # type: ignore[arg-type]
        connection = FailingConnection()
        stop = threading.Event()
        service._serial = connection  # type: ignore[assignment]
        service._stop = stop
        service._host_session_ready = True

        service._reader_loop(connection, stop, 123)  # type: ignore[arg-type]

        self.assertIsNone(service._serial)
        self.assertFalse(service._host_session_ready)
        self.assertTrue(connection.closed)
        self.assertEqual(store.discontinuities, ["serial_error"])

    def test_failure_from_old_reader_does_not_close_new_connection(self) -> None:
        store = FakeStore()
        service = SerialService(store)  # type: ignore[arg-type]
        old_connection = FailingConnection()
        new_connection = FailingConnection()
        service._serial = new_connection  # type: ignore[assignment]

        service._reader_loop(old_connection, threading.Event(), 1)  # type: ignore[arg-type]

        self.assertIs(service._serial, new_connection)
        self.assertFalse(old_connection.closed)
        self.assertEqual(store.discontinuities, [])

    def test_timed_out_response_is_removed_before_request_id_reuse(self) -> None:
        store = FakeStore()
        service = SerialService(store)  # type: ignore[arg-type]
        connection = FailingConnection()
        service._serial = connection  # type: ignore[assignment]
        service._host_session_ready = True
        service._request_id = 0
        service._responses[(1, 1)] = object()  # type: ignore[assignment]

        with self.assertRaises(TimeoutError):
            service.command(1, "GET_STATUS", timeout_seconds=0.001)

        self.assertNotIn((1, 1), service._responses)
        self.assertNotIn((1, 1), service._pending_response_keys)

    def test_counter_reset_does_not_request_status_or_change_battery(self) -> None:
        store = FakeStore()
        service = SerialService(store)  # type: ignore[arg-type]
        service._device_control[3] = {
            "status": {
                "battery_mV": 4038,
                "sample_gap_count": 7,
                "sensor_error_count": 6,
                "queue_drop_count": 5,
                "ble_resource_wait_count": 4,
            }
        }
        response = {
            "ok": True,
            "device_id": 3,
            "command": "RESET_STATS",
            "message": "成功",
        }

        with patch.object(
            service, "_command_locked", return_value=response
        ) as command:
            result = service.reset_data_and_error_counters(3)

        command.assert_called_once_with(3, "RESET_STATS", 3.0)
        self.assertIs(result, response)
        self.assertEqual(store.packet_counter_resets, [3])
        status = service.device_control_snapshot(3)["status"]
        self.assertEqual(status["battery_mV"], 4038)
        self.assertEqual(status["sample_gap_count"], 0)
        self.assertEqual(status["sensor_error_count"], 0)
        self.assertEqual(status["queue_drop_count"], 0)
        self.assertEqual(status["ble_resource_wait_count"], 0)

    def test_rejected_counter_reset_preserves_all_cached_counters(self) -> None:
        store = FakeStore()
        service = SerialService(store)  # type: ignore[arg-type]
        original_status = {
            "battery_mV": 4012,
            "sample_gap_count": 7,
            "sensor_error_count": 6,
            "queue_drop_count": 5,
            "ble_resource_wait_count": 4,
        }
        service._device_control[3] = {"status": dict(original_status)}

        with patch.object(
            service,
            "_command_locked",
            return_value={"ok": False, "message": "设备忙"},
        ) as command:
            result = service.reset_data_and_error_counters(3)

        command.assert_called_once_with(3, "RESET_STATS", 3.0)
        self.assertFalse(result["ok"])
        self.assertEqual(store.packet_counter_resets, [])
        self.assertEqual(
            service.device_control_snapshot(3)["status"], original_status
        )


if __name__ == "__main__":
    unittest.main()
