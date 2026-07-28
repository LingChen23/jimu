from __future__ import annotations

import math
import struct
import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motion_model import InertialEstimate, MotionStore, MovementStats
from motion_protocol import (
    GatewayStreamDecoder,
    ImuSample,
    MotionCommand,
    MotionFrame,
    cobs_decode,
    cobs_encode,
    decode_motion_packet,
    encode_gateway_command,
    encode_gateway_frame,
    encode_gateway_payload,
    parse_serial_line,
)

TERMINAL_STATIONARY_FRAMES = 90
PAUSE_STATIONARY_FRAMES = 21


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
    accel_raw: tuple[int, int, int] = (0, 0, 4096),
    gyro_raw: tuple[int, int, int] = (0, 0, 0),
    coordinate_source_sequence: int | None = None,
) -> MotionFrame:
    samples = tuple(
        ImuSample(
            sequence=packet_sequence * 4 + index,
            tick_32k=tick_start + index * 328,
            accel_raw=accel_raw,
            gyro_raw=gyro_raw,
        )
        for index in range(4)
    )
    return MotionFrame(
        device_id=1,
        packet_sequence=packet_sequence,
        session_id=1,
        flags=0x41,
        samples=samples,
        coordinate_source_sequence=(
            samples[-1].sequence
            if coordinate_source_sequence is None
            else coordinate_source_sequence
        ),
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

    def test_gateway_command_encoding(self) -> None:
        encoded = encode_gateway_command(
            1,
            MotionCommand.START_STREAM,
            9,
            struct.pack("<HBB", 100, 50, 7),
        )
        raw = cobs_decode(encoded[:-1])

        self.assertEqual(raw[2], 0x20)
        self.assertEqual(struct.unpack_from("<H", raw, 8)[0], 9)
        self.assertEqual(raw[10:19], bytes((1, 1, 1, 9, 4, 100, 0, 50, 7)))

    def test_gateway_control_response_decoding(self) -> None:
        status_payload = bytes(range(29))
        response = bytes((1, 1, 0x84, 11, 0, 29)) + status_payload
        encoded = encode_gateway_payload(0x21, response)
        frames = GatewayStreamDecoder().feed(encoded)

        self.assertEqual(len(frames), 1)
        control = frames[0].control_response
        self.assertIsNotNone(control)
        assert control is not None
        self.assertEqual(control.device_id, 1)
        self.assertEqual(control.command, MotionCommand.GET_STATUS)
        self.assertEqual(control.request_id, 11)
        self.assertEqual(control.payload, status_payload)

    def test_gateway_link_diagnostic_decoding(self) -> None:
        payload = struct.pack(
            "<BBBbHHHHIIIIIIII",
            1,
            3,
            0x07,
            -57,
            16,
            0x0208,
            2498,
            2501,
            12345,
            67,
            2,
            4,
            3,
            1,
            98765,
            5,
        )
        encoded = encode_gateway_payload(0x30, payload)
        frames = GatewayStreamDecoder().feed(encoded)

        self.assertEqual(len(frames), 1)
        link = frames[0].link_diagnostic
        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual(link.device_id, 3)
        self.assertTrue(link.connected)
        self.assertTrue(link.ready)
        self.assertEqual(link.rssi_dbm, -57)
        self.assertEqual(link.connection_interval_ms, 20.0)
        self.assertEqual(link.receive_rate_1s_pps, 24.98)
        self.assertEqual(link.receive_rate_10s_pps, 25.01)
        self.assertEqual(link.sequence_gap_frames, 67)
        self.assertEqual(link.disconnect_count, 3)
        self.assertEqual(link.gateway_queue_drops, 5)
        self.assertIsNone(link.att_mtu)
        self.assertFalse(link.dle_valid)
        self.assertIsNone(link.tx_phy)

    def test_gateway_link_diagnostic_v2_decoding(self) -> None:
        payload = struct.pack(
            "<BBBbHHHHIIIIIIIIHHHHHBBhhI",
            2,
            1,
            0x7F,
            -61,
            16,
            0,
            2500,
            2499,
            20000,
            12,
            0,
            2,
            1,
            0,
            456789,
            0,
            185,
            251,
            2120,
            251,
            2120,
            1,
            1,
            0,
            0,
            1,
        )
        encoded = encode_gateway_payload(0x30, payload)
        frames = GatewayStreamDecoder().feed(encoded)

        self.assertEqual(len(frames), 1)
        link = frames[0].link_diagnostic
        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual(link.device_id, 1)
        self.assertEqual(link.att_mtu, 185)
        self.assertTrue(link.dle_requested)
        self.assertTrue(link.dle_valid)
        self.assertEqual(link.max_tx_octets, 251)
        self.assertEqual(link.max_tx_time_us, 2120)
        self.assertEqual(link.max_rx_octets, 251)
        self.assertEqual(link.max_rx_time_us, 2120)
        self.assertEqual(link.tx_phy, 1)
        self.assertEqual(link.rx_phy, 1)
        self.assertEqual(link.dle_request_result, 0)
        self.assertEqual(link.phy_read_result, 0)
        self.assertEqual(link.dle_update_count, 1)

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
            "tick={tick} flags=0x41] s0 a=1,2,4096 g=4,5,6 "
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
        for _ in range(TERMINAL_STATIONARY_FRAMES):
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

    def test_short_stop_below_threshold_is_not_counted_as_pause(self) -> None:
        store = MotionStore()
        packet = 0
        tick = 0

        for flags, count in (
            (0x0001, 5),
            (0x0002, 10),
            (0x0001, 5),
            (0x0002, 10),
            (0x0001, TERMINAL_STATIONARY_FRAMES),
        ):
            for _ in range(count):
                store.process(make_frame(packet, flags, tick))
                packet += 1
                tick += 4 * 328

        movement = store.snapshot(1)["completed_movements"][0]
        self.assertEqual(movement["pause_count"], 0)
        self.assertEqual(
            movement["pause_definition"],
            "stationary_at_least_0.8s_then_resume",
        )

    def test_clear_analysis_preserves_link_counters(self) -> None:
        store = MotionStore()
        store.process(make_frame(0, 0x0002, 0))
        before = store.snapshot(1)

        self.assertTrue(store.reset_analysis(1))
        after = store.snapshot(1)

        self.assertEqual(after["packet_count"], before["packet_count"])
        self.assertEqual(after["completed_movement_count"], 0)
        self.assertEqual(after["received_path"], [])

    def test_reset_packet_counters_preserves_analysis_and_rebases_loss(self) -> None:
        store = MotionStore()
        store.process(make_frame(10, 0x0002, 0, x_mm=100))
        store.process(make_frame(12, 0x0002, 4 * 328, x_mm=200))
        before = store.snapshot(1)

        self.assertEqual(before["packet_count"], 2)
        self.assertEqual(before["lost_packets"], 1)
        self.assertTrue(store.reset_packet_counters(1))

        after = store.snapshot(1)
        self.assertEqual(after["packet_count"], 0)
        self.assertEqual(after["lost_packets"], 0)
        self.assertEqual(after["sample_count"], before["sample_count"])
        self.assertEqual(after["received_path"], before["received_path"])

        store.process(make_frame(100, 0x0002, 8 * 328, x_mm=300))
        rebased = store.snapshot(1)
        self.assertEqual(rebased["packet_count"], 1)
        self.assertEqual(rebased["lost_packets"], 0)

    def test_shake_and_flip_are_counted_independently(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])
        estimate = InertialEstimate(
            accel=[0.0, 0.0, 9.80665],
            gyro_dps=[0.0, 0.0, 0.0],
            corrected_gyro_dps=[0.0, 0.0, 0.0],
            linear_world=[16.0, 0.0, 0.0],
            velocity=[0.0, 0.0, 0.0],
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
            gyro_dps=[0.0, 0.0, 165.0],
            corrected_gyro_dps=[0.0, 0.0, 165.0],
            linear_world=[0.0, 0.0, 0.0],
            velocity=[0.0, 0.0, 0.0],
            position=[0.0, 0.0, 0.0],
            up_world=[0.0, 0.0, 1.0],
            dt=0.01,
            stationary=False,
        )

        for index in range(200):
            movement.add_sample(estimate, index * 328)

        self.assertEqual(movement.rotation_count, 1)
        self.assertEqual(movement.flip_count, 0)

    def test_held_still_in_air_for_0_8_seconds_then_resumed_counts_pause(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        store.set_analysis_mode(1, "device")
        packet = 0
        tick = 0

        # Hand-held stillness still measures about 1 g. It is recognized from
        # the stationary state, not from low-g/freefall.
        for flags, count in (
            (0x0001, 5),
            (0x0002, 10),
            (0x0001, PAUSE_STATIONARY_FRAMES),
            (0x0002, 10),
            (0x0001, TERMINAL_STATIONARY_FRAMES),
        ):
            for _ in range(count):
                store.process(make_frame(packet, flags, tick))
                packet += 1
                tick += 4 * 328

        movement = store.snapshot(1)["completed_movements"][0]
        self.assertEqual(movement["pause_count"], 1)
        self.assertGreaterEqual(movement["pause_total_seconds"], 0.8)
        detail = store.movement_detail(
            1, movement["movement_id"], mode="device"
        )
        assert detail is not None
        self.assertEqual(len(detail["pause_events"]), 1)

    def test_pc_mode_counts_0_8_second_stop_when_motion_resumes(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        store.set_analysis_mode(1, "pc")
        packet = 0
        tick = 0

        for accel, gyro, count in (
            ((0, 0, 4096), (0, 0, 0), 25),
            ((0, 0, 4096), (0, 0, 1200), 5),
            ((0, 0, 4096), (0, 0, 0), PAUSE_STATIONARY_FRAMES),
            ((0, 0, 4096), (0, 0, 1200), 5),
            (
                (0, 0, 4096),
                (0, 0, 0),
                TERMINAL_STATIONARY_FRAMES,
            ),
        ):
            for _ in range(count):
                store.process(
                    make_frame(
                        packet,
                        0x0001,
                        tick,
                        accel_raw=accel,
                        gyro_raw=gyro,
                    )
                )
                packet += 1
                tick += 4 * 328

        movement = store.snapshot(1)["completed_movements"][0]
        self.assertEqual(movement["pause_count"], 1)
        self.assertGreaterEqual(movement["pause_total_seconds"], 0.8)

    def test_flip_back_counts_as_a_second_flip(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])

        def estimate(up_world: list[float]) -> InertialEstimate:
            return InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=[0.0, 0.0, 0.0],
                corrected_gyro_dps=[0.0, 0.0, 0.0],
                linear_world=[0.0, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0],
                position=[0.0, 0.0, 0.0],
                up_world=up_world,
                dt=0.01,
                stationary=False,
            )

        for index in range(30):
            movement.add_sample(estimate([0.0, 0.0, -1.0]), index * 328)
        self.assertEqual(movement.flip_count, 1)

        for index in range(30, 60):
            movement.add_sample(estimate([0.0, 0.0, -1.0]), index * 328)
        self.assertEqual(movement.flip_count, 1)

        for index in range(60, 90):
            movement.add_sample(estimate([0.0, 0.0, 1.0]), index * 328)
        self.assertEqual(movement.flip_count, 2)

    def test_back_and_forth_gyro_does_not_count_complete_rotation(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])

        def estimate(rate: float) -> InertialEstimate:
            return InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=[0.0, 0.0, rate],
                corrected_gyro_dps=[0.0, 0.0, rate],
                linear_world=[0.0, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0],
                position=[0.0, 0.0, 0.0],
                up_world=[0.0, 0.0, 1.0],
                dt=0.01,
                stationary=False,
            )

        for index in range(150):
            movement.add_sample(estimate(180.0), index * 328)
        for index in range(150, 300):
            movement.add_sample(estimate(-180.0), index * 328)

        self.assertEqual(movement.rotation_count, 0)
        self.assertAlmostEqual(movement.rotation_progress_degrees, 0.0, places=5)

    def test_diagonal_rotation_uses_total_angular_travel(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])
        component = 180.0 / (2.0 ** 0.5)
        estimate = InertialEstimate(
            accel=[0.0, 0.0, 9.80665],
            gyro_dps=[component, component, 0.0],
            corrected_gyro_dps=[component, component, 0.0],
            linear_world=[0.0, 0.0, 0.0],
            velocity=[0.0, 0.0, 0.0],
            position=[0.0, 0.0, 0.0],
            up_world=[0.0, 0.0, 1.0],
            dt=0.01,
            stationary=False,
        )

        for index in range(200):
            movement.add_sample(estimate, index * 328)

        self.assertEqual(movement.rotation_count, 1)
        self.assertAlmostEqual(
            movement.snapshot("移动中")["rotation_turns"], 1.0, places=5
        )

    def test_rest_to_rest_correction_removes_constant_bias(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])
        biased = InertialEstimate(
            accel=[0.0, 0.0, 9.80665],
            gyro_dps=[0.0, 0.0, 0.0],
            corrected_gyro_dps=[0.0, 0.0, 0.0],
            linear_world=[0.25, 0.0, 0.0],
            velocity=[0.0, 0.0, 0.0],
            position=[0.0, 0.0, 0.0],
            up_world=[0.0, 0.0, 1.0],
            dt=0.01,
            stationary=False,
        )
        for index in range(100):
            movement.add_sample(biased, index * 328)
        corrected = movement.rest_to_rest_corrected_path()
        self.assertAlmostEqual(corrected[-1][0], 0.0, places=8)
        self.assertAlmostEqual(corrected[-1][1], 0.0, places=8)
        self.assertAlmostEqual(corrected[-1][2], 0.0, places=8)

    def test_completed_movement_speed_statistics_uses_core_p05(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])
        speeds = [0.0] * 10 + [1.0 + index / 100.0 for index in range(80)] + [0.0] * 10
        for index, speed in enumerate(speeds):
            estimate = InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=[0.0, 0.0, 0.0],
                corrected_gyro_dps=[0.0, 0.0, 0.0],
                linear_world=[0.0, 0.0, 0.0],
                velocity=[speed, 0.0, 0.0],
                position=[speed * 0.01, 0.0, 0.0],
                up_world=[0.0, 0.0, 1.0],
                dt=0.01,
                stationary=False,
            )
            movement.add_sample(estimate, index * 328, index, 1000.0 + index * 0.01)
        movement.finalize(False, [[0.0, 0.0, 0.0]], [[0.0, 0.0, 0.0]])
        stats = movement.snapshot("已完成")["speed"]
        self.assertEqual(stats["max"], max(speeds))
        self.assertEqual(stats["min"], 0.0)  # retained internally for completeness
        self.assertGreater(stats["p05"], 1.0)
        self.assertEqual(stats["p05_percentile"], 5.0)
        self.assertEqual(stats["p05_sample_count"], 80)

    def test_tremor_fft_finds_six_hz_component(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])
        sample_rate = 100.0
        for index in range(300):
            t = index / sample_rate
            rate = 18.0 * math.sin(2.0 * math.pi * 6.0 * t)
            rate += 4.0 * math.sin(2.0 * math.pi * 0.8 * t)
            estimate = InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=[rate, 0.0, 0.0],
                corrected_gyro_dps=[rate, 0.0, 0.0],
                linear_world=[0.0, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0],
                position=[0.0, 0.0, 0.0],
                up_world=[0.0, 0.0, 1.0],
                dt=1.0 / sample_rate,
                stationary=False,
            )
            movement.add_sample(estimate, index * 328, index, 1000.0 + t)
        movement.finalize(False, [[0.0, 0.0, 0.0]], [[0.0, 0.0, 0.0]])
        self.assertTrue(movement.tremor["valid"])
        self.assertAlmostEqual(
            movement.tremor["dominant_frequency_hz"], 6.0, delta=0.15
        )
        self.assertGreater(movement.tremor["intensity_dps"], 10.0)

    def test_drop_requires_freefall_then_impact(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])
        freefall = InertialEstimate(
            accel=[0.0, 0.0, 0.0], gyro_dps=[0.0, 0.0, 0.0],
            corrected_gyro_dps=[0.0, 0.0, 0.0], linear_world=[0.0, 0.0, -9.8],
            velocity=[0.0, 0.0, 0.0], position=[0.0, 0.0, 0.0],
            up_world=[0.0, 0.0, 1.0], dt=0.01, stationary=False,
        )
        impact = InertialEstimate(
            accel=[0.0, 0.0, 3.2 * 9.80665], gyro_dps=[0.0, 0.0, 0.0],
            corrected_gyro_dps=[0.0, 0.0, 0.0], linear_world=[0.0, 0.0, 20.0],
            velocity=[0.0, 0.0, 0.0], position=[0.0, 0.0, 0.0],
            up_world=[0.0, 0.0, 1.0], dt=0.01, stationary=False,
        )
        for index in range(12):
            movement.add_sample(freefall, index * 328, index, 1000.0 + index * 0.01)
        movement.add_sample(impact, 12 * 328, 12, 1000.12)
        self.assertEqual(movement.drop_count, 1)
        self.assertEqual(movement.pause_count, 0)
        self.assertGreater(movement.drop_events[0]["impact_g"], 3.0)
        self.assertNotIn("airborne_seconds", movement.snapshot("已完成"))

    def test_history_detail_and_exports_are_available(self) -> None:
        store = MotionStore()
        packet = 0
        tick = 0
        for flags, count in (
            (0x0001, 5),
            (0x0002, 30),
            (0x0001, TERMINAL_STATIONARY_FRAMES),
        ):
            for _ in range(count):
                store.process(make_frame(packet, flags, tick))
                packet += 1
                tick += 4 * 328
        movement_id = store.snapshot(1)["completed_movements"][0]["movement_id"]
        detail = store.movement_detail(1, movement_id, "device")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertIn("acceleration_series", detail)
        self.assertIn("session_tremor_average", detail)
        self.assertIsNotNone(detail["time"]["start"]["beijing"])
        self.assertAlmostEqual(
            detail["time"]["end_epoch"] - detail["time"]["start_epoch"],
            detail["segment_seconds"],
            places=5,
        )
        csv_data = store.movement_export(1, movement_id, "csv", "device")
        json_data = store.movement_export(1, movement_id, "json", "device")
        self.assertIn("record_type", csv_data or "")
        self.assertIn("speed_p05_mps", csv_data or "")
        self.assertIn("flip_count", csv_data or "")
        self.assertNotIn("airborne_seconds", csv_data or "")
        self.assertIn('"samples"', json_data or "")
        self.assertIn('"pause_events"', json_data or "")
        self.assertNotIn('"airborne_seconds"', json_data or "")
        self.assertNotIn('"airborne_intervals"', json_data or "")

    def test_pc_mode_ignores_delayed_device_state(self) -> None:
        # This test exercises state independence, not the short-segment filter.
        store = MotionStore(minimum_movement_seconds=0.0)
        self.assertTrue(store.set_analysis_mode(1, "pc"))
        packet = 0
        tick = 0

        # PC performs its own one-second stationary calibration.
        for _ in range(25):
            store.process(make_frame(packet, 0x0001, tick))
            packet += 1
            tick += 4 * 328

        # Device state still says stationary, but current gyro data is moving.
        store.process(
            make_frame(
                packet,
                0x0001,
                tick,
                gyro_raw=(0, 0, 1200),
            )
        )
        packet += 1
        tick += 4 * 328
        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["analysis_mode"], "pc")
        self.assertEqual(snapshot["device_reference_state"], "静止")
        self.assertEqual(snapshot["motion_state"], "移动中")
        self.assertIsNotNone(snapshot["current_movement"])

        # Device state now says moving, but PC sees a stable IMU and ends the
        # segment independently after the configured hold interval.
        for _ in range(TERMINAL_STATIONARY_FRAMES):
            store.process(make_frame(packet, 0x0002, tick))
            packet += 1
            tick += 4 * 328
        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["device_reference_state"], "移动中")
        self.assertEqual(snapshot["motion_state"], "静止")
        self.assertEqual(snapshot["completed_movement_count"], 1)

    def test_pc_mode_fast_rotation_is_not_forced_into_calibration(self) -> None:
        store = MotionStore()
        store.set_analysis_mode(1, "pc")
        packet = 0
        tick = 0

        for _ in range(25):
            store.process(make_frame(packet, 0x0001, tick))
            packet += 1
            tick += 4 * 328

        # Transmitter reports CALIBRATING, while the current IMU clearly shows
        # a fast rotation. PC mode must continue as moving, not zero the samples.
        for _ in range(35):
            store.process(
                make_frame(
                    packet,
                    0x0040,
                    tick,
                    gyro_raw=(20000, 0, 0),
                )
            )
            packet += 1
            tick += 4 * 328
        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["device_reference_state"], "校准中")
        self.assertEqual(snapshot["motion_state"], "移动中")
        self.assertGreater(
            snapshot["current_movement"]["rotation_count"], 0
        )

    def test_pc_coordinate_points_are_filtered_by_source_sequence(self) -> None:
        store = MotionStore()
        store.set_analysis_mode(1, "pc")
        packet = 0
        tick = 0

        for _ in range(25):
            store.process(make_frame(packet, 0x0001, tick))
            packet += 1
            tick += 4 * 328

        stale_sequence = 10
        store.process(
            make_frame(
                packet,
                0x0001,
                tick,
                x_mm=500,
                gyro_raw=(0, 0, 1200),
                coordinate_source_sequence=stale_sequence,
            )
        )
        packet += 1
        tick += 4 * 328
        movement_start_sequence = packet * 4 - 3

        # A coordinate tagged before the PC segment start is ignored.
        store.process(
            make_frame(
                packet,
                0x0002,
                tick,
                x_mm=600,
                gyro_raw=(0, 0, 1200),
                coordinate_source_sequence=movement_start_sequence - 20,
            )
        )
        packet += 1
        tick += 4 * 328
        self.assertEqual(store.snapshot(1)["received_path"], [[0.0, 0.0, 0.0]])

        # First aligned coordinate establishes the received origin; the next
        # aligned point produces a relative device-path point.
        store.process(
            make_frame(
                packet,
                0x0002,
                tick,
                x_mm=700,
                gyro_raw=(0, 0, 1200),
                coordinate_source_sequence=movement_start_sequence + 1,
            )
        )
        packet += 1
        tick += 4 * 328
        store.process(
            make_frame(
                packet,
                0x0002,
                tick,
                x_mm=750,
                gyro_raw=(0, 0, 1200),
                coordinate_source_sequence=movement_start_sequence + 5,
            )
        )
        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["received_path"][0], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(snapshot["received_path"][-1][0], 0.05, places=6)

    def test_mode_switch_keeps_both_parallel_results(self) -> None:
        store = MotionStore()
        packet = 0
        tick = 0
        for _ in range(25):
            store.process(make_frame(packet, 0x0001, tick))
            packet += 1
            tick += 4 * 328
        store.process(
            make_frame(packet, 0x0001, tick, gyro_raw=(0, 0, 1200))
        )

        device_snapshot = store.snapshot(1)
        self.assertEqual(device_snapshot["analysis_mode"], "device")
        self.assertEqual(device_snapshot["motion_state"], "静止")

        store.set_analysis_mode(1, "pc")
        pc_snapshot = store.snapshot(1)
        self.assertEqual(pc_snapshot["motion_state"], "移动中")
        self.assertIsNotNone(pc_snapshot["current_movement"])

        store.set_analysis_mode(1, "device")
        restored = store.snapshot(1)
        self.assertEqual(restored["motion_state"], "静止")

    def test_snapshot_exposes_realtime_orientation(self) -> None:
        store = MotionStore()
        store.process(make_frame(0, 0x0001, 0))

        snapshot = store.snapshot(1)
        orientation = snapshot["orientation"]

        self.assertEqual(len(orientation["quaternion_wxyz"]), 4)
        self.assertEqual(len(orientation["euler_degrees"]), 3)
        self.assertEqual(len(orientation["body_x_world"]), 3)
        self.assertEqual(orientation["yaw_reference"], "relative")
        self.assertAlmostEqual(
            sum(value * value for value in orientation["body_z_world"]),
            1.0,
            places=5,
        )

    def test_live_snapshot_is_lightweight_and_contains_latest_imu(self) -> None:
        store = MotionStore()
        store.process(
            make_frame(
                0,
                0x0001,
                0,
                accel_raw=(50, -100, 4096),
                gyro_raw=(33, -66, 131),
            )
        )

        live = store.live_snapshot(1)

        self.assertEqual(live["device_id"], 1)
        self.assertEqual(len(live["accel_mps2"]), 3)
        self.assertEqual(len(live["gyro_dps"]), 3)
        self.assertIn("orientation", live)
        self.assertIn("calculated_position_m", live)
        self.assertFalse(live["position_semantics"]["pc_metric_reliable"])
        self.assertEqual(
            live["position_semantics"]["default_display"], "relative_shape"
        )
        self.assertNotIn("calculated_path", live)
        self.assertNotIn("accel", live)


    def test_short_movement_is_ignored_without_consuming_number(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.30)
        packet = 0
        tick = 0
        for _ in range(5):
            store.process(make_frame(packet, 0x0001, tick))
            packet += 1
            tick += 4 * 328
        for _ in range(2):
            store.process(make_frame(packet, 0x0002, tick))
            packet += 1
            tick += 4 * 328
        for _ in range(TERMINAL_STATIONARY_FRAMES):
            store.process(make_frame(packet, 0x0001, tick))
            packet += 1
            tick += 4 * 328

        snapshot = store.snapshot(1)
        self.assertEqual(snapshot["completed_movement_count"], 0)
        self.assertEqual(snapshot["movement_filter"]["ignored_short_count"], 1)

        for _ in range(10):
            store.process(make_frame(packet, 0x0002, tick))
            packet += 1
            tick += 4 * 328
        for _ in range(TERMINAL_STATIONARY_FRAMES):
            store.process(make_frame(packet, 0x0001, tick))
            packet += 1
            tick += 4 * 328
        snapshot = store.snapshot(1)
        self.assertEqual(snapshot["completed_movement_count"], 1)
        self.assertEqual(snapshot["completed_movements"][0]["movement_id"], 1)

    def test_setting_removes_existing_short_records(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        packet = 0
        tick = 0
        for _ in range(5):
            store.process(make_frame(packet, 0x0001, tick))
            packet += 1
            tick += 4 * 328
        for _ in range(2):
            store.process(make_frame(packet, 0x0002, tick))
            packet += 1
            tick += 4 * 328
        for _ in range(TERMINAL_STATIONARY_FRAMES):
            store.process(make_frame(packet, 0x0001, tick))
            packet += 1
            tick += 4 * 328
        self.assertEqual(store.snapshot(1)["completed_movement_count"], 1)

        result = store.set_minimum_movement_seconds(0.30)
        self.assertGreaterEqual(result["removed_existing_records_across_pipelines"], 1)
        snapshot = store.snapshot(1)
        self.assertEqual(snapshot["completed_movement_count"], 0)
        self.assertEqual(snapshot["movement_filter"]["minimum_seconds"], 0.30)

if __name__ == "__main__":
    unittest.main()
