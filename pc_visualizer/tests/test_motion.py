from __future__ import annotations

import struct
import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motion_model import InertialEstimate, MotionStore, MovementStats
from motion_protocol import (
    GatewayStreamDecoder,
    ImuSample,
    MotionFrame,
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


def make_frame(
    packet_sequence: int,
    coordinate_flags: int,
    tick_start: int,
    x_mm: int = 0,
) -> MotionFrame:
    samples = tuple(
        ImuSample(
            sequence=packet_sequence * 4 + index,
            tick_32k=tick_start + index * 328,
            accel_raw=(0, 0, 8192),
            gyro_raw=(0, 0, 0),
        )
        for index in range(4)
    )
    return MotionFrame(
        device_id=1,
        packet_sequence=packet_sequence,
        session_id=1,
        flags=0x41,
        samples=samples,
        coordinate_source_sequence=samples[-1].sequence,
        position_mm=(x_mm, 0, 0),
        coordinate_flags=coordinate_flags,
        source="motion-v1-100b",
    )


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

    def test_complete_movement_is_segmented_and_rebased(self) -> None:
        store = MotionStore()
        packet = 0
        tick = 0

        for _ in range(5):
            store.process(make_frame(packet, 0x0001, tick))
            packet += 1
            tick += 4 * 328
        for index in range(10):
            store.process(
                make_frame(packet, 0x0002, tick, x_mm=100 + index * 10)
            )
            packet += 1
            tick += 4 * 328
        for _ in range(25):
            store.process(make_frame(packet, 0x0001, tick, x_mm=200))
            packet += 1
            tick += 4 * 328

        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["motion_state"], "静止")
        self.assertEqual(snapshot["completed_movement_count"], 1)
        movement = snapshot["completed_movements"][0]
        self.assertGreater(movement["moving_seconds"], 0.35)
        self.assertEqual(snapshot["received_path"][0], [0.0, 0.0, 0.0])
        self.assertEqual(snapshot["calculated_path"][0], [0.0, 0.0, 0.0])

    def test_short_stop_inside_movement_counts_as_pause(self) -> None:
        store = MotionStore()
        packet = 0
        tick = 0

        for flags, count in (
            (0x0001, 5),
            (0x0002, 10),
            (0x0001, 5),
            (0x0002, 10),
            (0x0001, 25),
        ):
            for _ in range(count):
                store.process(make_frame(packet, flags, tick))
                packet += 1
                tick += 4 * 328

        movement = store.snapshot(1)["completed_movements"][0]
        self.assertEqual(movement["pause_count"], 1)

    def test_clear_analysis_preserves_link_counters(self) -> None:
        store = MotionStore()
        store.process(make_frame(0, 0x0002, 0))
        before = store.snapshot(1)

        self.assertTrue(store.reset_analysis(1))
        after = store.snapshot(1)

        self.assertEqual(after["packet_count"], before["packet_count"])
        self.assertEqual(after["completed_movement_count"], 0)
        self.assertEqual(after["received_path"], [])

    def test_shake_and_flip_events_are_debounced(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])
        estimate = InertialEstimate(
            accel=[0.0, 0.0, 9.80665],
            gyro_dps=[0.0, 0.0, 0.0],
            corrected_gyro_dps=[0.0, 0.0, 0.0],
            linear_world=[16.0, 0.0, 0.0],
            position=[0.0, 0.0, 0.0],
            up_world=[0.0, 0.0, -1.0],
            dt=0.01,
            stationary=False,
        )

        for index in range(30):
            movement.add_sample(estimate, index * 328)

        self.assertEqual(movement.shake_count, 1)
        self.assertEqual(movement.flip_count, 1)

    def test_full_gyro_rotation_counts_once(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])
        estimate = InertialEstimate(
            accel=[0.0, 0.0, 9.80665],
            gyro_dps=[0.0, 0.0, 180.0],
            corrected_gyro_dps=[0.0, 0.0, 180.0],
            linear_world=[0.0, 0.0, 0.0],
            position=[0.0, 0.0, 0.0],
            up_world=[0.0, 0.0, 1.0],
            dt=0.01,
            stationary=False,
        )

        for index in range(200):
            movement.add_sample(estimate, index * 328)

        self.assertEqual(movement.rotation_count, 1)


if __name__ == "__main__":
    unittest.main()
