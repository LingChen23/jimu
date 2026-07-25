from __future__ import annotations

import struct
import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motion_model import MotionStore
from motion_protocol import decode_motion_packet, parse_serial_line


class MotionProtocolTests(unittest.TestCase):
    def test_decode_fixed_packet(self) -> None:
        data = bytearray(100)
        data[0:4] = bytes((1, 0x10, 7, 0x41))
        struct.pack_into("<IIQH", data, 4, 0x12345678, 1000, 50000, 9)
        data[22:24] = bytes((4, 1))
        for index in range(4):
            struct.pack_into(
                "<Hhhhhhh",
                data,
                24 + index * 14,
                index * 328,
                100 + index,
                200 + index,
                300 + index,
                -10 - index,
                -20 - index,
                -30 - index,
            )
        struct.pack_into("<IiiiH", data, 80, 997, 10, -20, 30, 0x0002)

        frame = decode_motion_packet(bytes(data))

        self.assertEqual(frame.device_id, 7)
        self.assertEqual(frame.packet_sequence, 9)
        self.assertEqual(len(frame.samples), 4)
        self.assertEqual(frame.samples[3].sequence, 1003)
        self.assertEqual(frame.samples[3].tick_32k, 50984)
        self.assertEqual(frame.position_mm, (10, -20, 30))

    def test_parse_current_esp32_diagnostic_line(self) -> None:
        line = (
            "I motion_data: [addr=X conn=1 id=1 pkt=512 sample=2048 "
            "tick=671581 flags=0x41] s0 a=2721,-279,8051 "
            "g=-11,51,3 s3dt=984 coord_seq=2017 xyz=1,2,3 "
            "cflags=0x0002"
        )

        frame = parse_serial_line(line)

        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.device_id, 1)
        self.assertEqual(frame.samples[0].gyro_raw, (-11, 51, 3))
        self.assertTrue(frame.coordinate_valid)

    def test_store_reserves_twelve_devices(self) -> None:
        store = MotionStore(12)
        snapshot = store.snapshot(12)
        self.assertEqual(len(snapshot["devices"]), 12)
        self.assertEqual(snapshot["device_id"], 12)

    def test_sparse_diagnostic_output_does_not_report_packet_loss(self) -> None:
        store = MotionStore()
        line_template = (
            "I motion_data: [addr=X conn=1 id=1 pkt={packet} sample={sample} "
            "tick={tick} flags=0x41] s0 a=1,2,8192 g=4,5,6 "
            "s3dt=983 coord_seq=9 xyz=10,20,30 cflags=0x0002"
        )
        first = parse_serial_line(
            line_template.format(packet=100, sample=400, tick=1000)
        )
        second = parse_serial_line(
            line_template.format(packet=125, sample=500, tick=33768)
        )
        assert first is not None
        assert second is not None

        store.process(first)
        store.process(second)
        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["lost_packets"], 0)


if __name__ == "__main__":
    unittest.main()
