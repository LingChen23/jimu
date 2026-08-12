from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from serial_service import SerialService


class BatteryRefreshTests(unittest.TestCase):
    def test_queries_devices_in_order_and_skips_failures(self) -> None:
        service = SerialService(None)  # type: ignore[arg-type]
        calls: list[tuple[int, str, float]] = []

        def command(device_id: int, name: str, timeout: float) -> dict:
            calls.append((device_id, name, timeout))
            if device_id == 2:
                raise TimeoutError("GET_STATUS 等待响应超时")
            return {
                "ok": True,
                "message": "成功",
                "data": {
                    "battery_mV": 4000 + device_id,
                    "battery_low": False,
                },
            }

        service._command_locked = command  # type: ignore[method-assign]
        with patch("serial_service.time.sleep") as sleep:
            result = service.refresh_batteries(
                [1, 2, 3], timeout_seconds=0.75, interval_seconds=0.1
            )

        self.assertEqual(
            calls,
            [
                (1, "GET_STATUS", 0.75),
                (2, "GET_STATUS", 0.75),
                (3, "GET_STATUS", 0.75),
            ],
        )
        self.assertEqual(result["succeeded"], 2)
        self.assertEqual(result["skipped"], 1)
        self.assertFalse(result["results"][1]["ok"])
        self.assertEqual(sleep.call_count, 2)

    def test_missing_voltage_is_skipped_without_stopping_scan(self) -> None:
        service = SerialService(None)  # type: ignore[arg-type]
        responses = iter(
            [
                {"ok": True, "message": "成功", "data": {"battery_mV": None}},
                {"ok": True, "message": "成功", "data": {"battery_mV": 3890}},
            ]
        )
        service._command_locked = lambda *_: next(responses)  # type: ignore[method-assign]

        result = service.refresh_batteries(
            [4, 5], timeout_seconds=0.1, interval_seconds=0
        )

        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["results"][1]["battery_mV"], 3890)


if __name__ == "__main__":
    unittest.main()
