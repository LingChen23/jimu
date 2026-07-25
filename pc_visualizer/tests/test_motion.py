from __future__ import annotations

import struct
import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motion_model import MotionStore
from motion_protocol import (
    GatewayStreamDecoder,
    cobs_decode,
    cobs_encode,
    decode_motion_packet,
    encode_gateway_frame,
    parse_serial_line,
)


def make_motion_packet(device_id: int = 7, packet_sequence: int = 9) -> bytes:
    data = bytearray(100)
    data[0:4] = bytes((1, 0x10, device_id, 0x41))
    struct.pack_into(
        "<IIQH", data, 4, 0x12345678, 1000, 50000, packet_sequence
    )
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
    return bytes(data)


class MotionProtocolTests(unittest.TestCase):
    def test_decode_fixed_packet(self) -> None:
        frame = decode_motion_packet(make_motion_packet())

        self.assertEqual(frame.device_id, 7)
        self.assertEqual(frame.packet_sequence, 9)
        self.assertEqual(len(frame.samples), 4)
        self.assertEqual(frame.samples[3].sequence, 1003)
        self.assertEqual(frame.samples[3].tick_32k, 50984)
        self.assertEqual(frame.position_mm, (10, -20, 30))

    def test_cobs_round_trip(self) -> None:
        source = bytes(range(256)) + b"\x00\x00hello"
        self.assertEqual(cobs_decode(cobs_encode(source)), source)

    def test_chunked_gateway_frame_decoding(self) -> None:
        encoded = encode_gateway_frame(
            make_motion_packet(device_id=1, packet_sequence=77),
            gateway_id=0,
            gateway_sequence=123456,
        )
        decoder = GatewayStreamDecoder()

        frames = []
        for chunk in (encoded[:3], encoded[3:51], encoded[51:]):
            frames.extend(decoder.feed(chunk))

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].gateway_sequence, 123456)
        self.assertEqual(frames[0].motion.device_id, 1)
        self.assertEqual(frames[0].motion.packet_sequence, 77)
        self.assertEqual(len(frames[0].motion.samples), 4)
        self.assertEqual(decoder.valid_frame_count, 1)

    def test_crc_error_is_rejected_and_next_frame_recovers(self) -> None:
        valid = encode_gateway_frame(make_motion_packet())
        damaged_raw = bytearray(cobs_decode(valid[:-1]))
        damaged_raw[30] ^= 0x55
        damaged = cobs_encode(bytes(damaged_raw)) + b"\x00"
        decoder = GatewayStreamDecoder()

        frames = decoder.feed(damaged + valid)

        self.assertEqual(len(frames), 1)
        self.assertEqual(decoder.crc_error_count, 1)

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
