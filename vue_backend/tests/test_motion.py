from __future__ import annotations

import math
import json
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motion_model import AnalysisPipeline, InertialEstimate, MotionStore, MovementStats
from motion_protocol import (
    AGGREGATOR_HOST_CONTROL_START,
    GatewayStreamDecoder,
    ImuSample,
    MotionCommand,
    MotionFrame,
    cobs_decode,
    cobs_encode,
    decode_motion_packet,
    encode_aggregator_host_control,
    encode_gateway_command,
    encode_gateway_frame,
    encode_gateway_payload,
    parse_serial_line,
)

TERMINAL_STATIONARY_FRAMES = 90
PAUSE_STATIONARY_FRAMES = 21


def completed_snapshot(store: MotionStore, device_id: int = 1) -> dict:
    if not store.wait_for_finalizations(timeout_seconds=3.0):
        raise AssertionError("后台移动特征计算超时")
    return store.snapshot(device_id)


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


def make_motion_v2_packet(
    device_id: int = 7, packet_sequence: int = 10
) -> bytes:
    data = bytearray(100)
    data[0:4] = bytes((2, 0x10, device_id, 0x41))
    struct.pack_into(
        "<IIIHBB",
        data,
        4,
        0x12345678,
        2000,
        0xFFFFFE00,
        packet_sequence,
        6,
        1,
    )
    for index in range(6):
        struct.pack_into(
            "<hhhhhh",
            data,
            20 + index * 12,
            100 + index,
            200 + index,
            300 + index,
            -10 - index,
            -20 - index,
            -30 - index,
        )
    struct.pack_into("<hhhBB", data, 92, 10, -20, 30, 2, 0x02)
    return bytes(data)


def make_frame(
    packet_sequence: int,
    coordinate_flags: int,
    tick_start: int,
    x_mm: int = 0,
    accel_raw: tuple[int, int, int] = (0, 0, 4096),
    gyro_raw: tuple[int, int, int] = (0, 0, 0),
    coordinate_source_sequence: int | None = None,
    session_id: int = 1,
    device_id: int = 1,
    source: str = "motion-v1-100b",
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
        device_id=device_id,
        packet_sequence=packet_sequence,
        session_id=session_id,
        flags=0x41,
        samples=samples,
        coordinate_source_sequence=(
            samples[-1].sequence
            if coordinate_source_sequence is None
            else coordinate_source_sequence
        ),
        position_mm=(x_mm, 0, 0),
        coordinate_flags=coordinate_flags,
        source=source,
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

    def test_decode_v2_six_sample_packet(self) -> None:
        frame = decode_motion_packet(make_motion_v2_packet())

        self.assertEqual(frame.device_id, 7)
        self.assertEqual(frame.packet_sequence, 10)
        self.assertEqual(len(frame.samples), 6)
        self.assertEqual(frame.samples[5].sequence, 2005)
        self.assertEqual(frame.samples[5].tick_32k, 0x00000466)
        self.assertEqual(frame.coordinate_source_sequence, 2003)
        self.assertEqual(frame.position_mm, (10, -20, 30))
        self.assertEqual(frame.coordinate_flags, 0x02)
        self.assertEqual(frame.source, "motion-v2-100b")

    def test_gateway_decoder_accepts_mixed_v1_v2_stream(self) -> None:
        decoder = GatewayStreamDecoder()
        stream = encode_gateway_frame(
            make_motion_packet(device_id=1), gateway_id=0
        ) + encode_gateway_frame(
            make_motion_v2_packet(device_id=5), gateway_id=1
        )

        frames = decoder.feed(stream)

        self.assertEqual([frame.motion.device_id for frame in frames], [1, 5])
        self.assertEqual([len(frame.motion.samples) for frame in frames], [4, 6])

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

    def test_aggregator_host_session_round_trip(self) -> None:
        session_id = 0x12345678
        request = encode_aggregator_host_control(
            AGGREGATOR_HOST_CONTROL_START,
            session_id,
        )
        request_raw = cobs_decode(request[:-1])

        self.assertEqual(request_raw[1], 0xFF)
        self.assertEqual(request_raw[2], 0x32)
        self.assertEqual(
            struct.unpack_from("<BBHI", request_raw, 10),
            (1, 1, 0, session_id),
        )

        response = encode_gateway_payload(
            0x33,
            struct.pack("<BBBBI", 1, 1, 0, 0, session_id),
            gateway_id=0xFF,
            gateway_sequence=session_id,
        )
        frames = GatewayStreamDecoder().feed(response)

        self.assertEqual(len(frames), 1)
        host_control = frames[0].aggregator_host_control
        self.assertIsNotNone(host_control)
        assert host_control is not None
        self.assertEqual(host_control.command, 1)
        self.assertEqual(host_control.result, 0)
        self.assertEqual(host_control.session_id, session_id)

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

    def test_aggregator_diagnostic_decoding(self) -> None:
        payload = bytearray(100)
        struct.pack_into(
            "<BBBBIIIIII",
            payload,
            0,
            1,
            3,
            0x01,
            17,
            4567,
            48,
            123456,
            7,
            2,
            3,
        )
        for gateway_id in range(3):
            struct.pack_into(
                "<BBBBIIHHHHHH",
                payload,
                28 + gateway_id * 24,
                gateway_id,
                0x01 if gateway_id != 2 else 0x00,
                (1, 2, 0)[gateway_id],
                5 + gateway_id,
                1000 * (gateway_id + 1),
                100 * (gateway_id + 1),
                gateway_id,
                gateway_id + 1,
                gateway_id + 2,
                gateway_id + 3,
                gateway_id + 4,
                15 if gateway_id != 2 else 0xFFFF,
            )
        encoded = encode_gateway_payload(
            0x31,
            bytes(payload),
            gateway_id=0xFF,
            gateway_sequence=9,
        )
        frames = GatewayStreamDecoder().feed(encoded)

        self.assertEqual(len(frames), 1)
        diagnostic = frames[0].aggregator_diagnostic
        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertTrue(diagnostic.usb_ready)
        self.assertEqual(diagnostic.usb_tx_bytes, 123456)
        self.assertEqual(diagnostic.usb_queue_high_watermark, 17)
        self.assertEqual(len(diagnostic.links), 3)
        self.assertTrue(diagnostic.links[0].active)
        self.assertEqual(diagnostic.links[1].uart_port, 2)
        self.assertEqual(diagnostic.links[1].valid_frames, 200)
        self.assertEqual(diagnostic.links[0].last_frame_age_ms, 1500.0)
        self.assertIsNone(diagnostic.links[2].last_frame_age_ms)

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

    def test_store_reserves_ten_devices(self) -> None:
        store = MotionStore(10)
        snapshot = store.snapshot(10)
        self.assertEqual(len(snapshot["devices"]), 10)
        self.assertEqual(snapshot["device_id"], 10)

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

        snapshot = completed_snapshot(store)

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

        movement = completed_snapshot(store)["completed_movements"][0]
        self.assertEqual(movement["pause_count"], 0)
        self.assertGreater(movement["stationary_seconds"], 0)
        self.assertLess(movement["stationary_seconds"], 0.8)
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

    def test_session_change_preserves_active_movement_as_new_subsegment(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        store.set_analysis_mode(1, "device")
        self.assertTrue(
            store.process(
                make_frame(
                    10,
                    0x0002,
                    10000,
                    gyro_raw=(0, 0, 1200),
                    session_id=10,
                )
            )
        )
        self.assertIsNotNone(store.snapshot(1)["current_movement"])

        self.assertTrue(
            store.process(
                make_frame(
                    0,
                    0x0001,
                    0,
                    session_id=11,
                )
            )
        )
        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["session_id"], 11)
        self.assertEqual(snapshot["discontinuity_count"], 1)
        self.assertEqual(snapshot["last_discontinuity_reason"], "session_changed")
        self.assertEqual(snapshot["interrupted_movement_count"], 0)
        self.assertIsNotNone(snapshot["current_movement"])
        self.assertEqual(snapshot["current_movement"]["segment_count"], 2)
        self.assertEqual(snapshot["current_movement"]["gap_count"], 1)
        self.assertGreater(len(snapshot["calculated_path"]), 0)
        signal_times = [row[0] for row in snapshot["accel"]]
        self.assertEqual(signal_times, sorted(signal_times))
        self.assertLess(signal_times[-1], 1.0)

    def test_duplicate_and_out_of_order_packets_are_rejected(self) -> None:
        store = MotionStore()
        first = make_frame(10, 0x0001, 0)

        self.assertTrue(store.process(first))
        self.assertFalse(store.process(first))
        self.assertFalse(store.process(make_frame(9, 0x0001, 0)))
        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["packet_count"], 1)
        self.assertEqual(snapshot["sample_count"], 4)
        self.assertEqual(snapshot["duplicate_packet_count"], 1)
        self.assertEqual(snapshot["out_of_order_packet_count"], 1)

    def test_short_packet_gap_is_interpolated_without_breaking_movement(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        store.set_analysis_mode(1, "device")
        self.assertTrue(store.process(make_frame(10, 0x0002, 0)))
        before = store.snapshot(1)
        self.assertEqual(before["current_movement"]["sample_count"], 4)

        self.assertTrue(store.process(make_frame(12, 0x0002, 8 * 328)))
        after = store.snapshot(1)

        self.assertEqual(after["packet_count"], 2)
        self.assertEqual(after["lost_packets"], 1)
        self.assertEqual(after["continuity_gap_count"], 1)
        self.assertEqual(after["last_discontinuity_reason"], "sample_gap")
        self.assertEqual(after["interrupted_movement_count"], 0)
        self.assertEqual(after["interpolated_sample_count"], 4)
        self.assertEqual(after["current_movement"]["sample_count"], 12)
        self.assertEqual(after["current_movement"]["observed_sample_count"], 8)
        self.assertEqual(
            after["current_movement"]["interpolated_sample_count"], 4
        )
        self.assertEqual(after["current_movement"]["segment_count"], 1)
        records = store._devices[1].analyses["device"].current_movement.sample_records
        self.assertEqual(
            [row["sequence"] for row in records if row["interpolated"]],
            [44, 45, 46, 47],
        )

    def test_transport_disconnect_preserves_path_and_resumes_new_subsegment(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        store.set_analysis_mode(1, "device")
        self.assertTrue(store.process(make_frame(0, 0x0002, 0)))

        store.transport_discontinuity("serial_closed")
        disconnected = store.snapshot(1)

        self.assertFalse(disconnected["online"])
        self.assertIsNone(disconnected["session_id"])
        self.assertIsNotNone(disconnected["current_movement"])
        self.assertGreater(len(disconnected["calculated_path"]), 0)
        self.assertEqual(disconnected["last_discontinuity_reason"], "serial_closed")
        self.assertEqual(disconnected["interrupted_movement_count"], 0)
        self.assertEqual(disconnected["current_movement"]["segment_count"], 2)

        self.assertTrue(store.process(make_frame(1, 0x0002, 100000)))
        resumed = store.snapshot(1)
        self.assertEqual(resumed["session_id"], 1)
        self.assertEqual(resumed["current_movement"]["sample_count"], 8)
        self.assertEqual(resumed["current_movement"]["segment_count"], 2)

    def test_long_time_gap_resumes_as_new_subsegment_without_interpolation(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        store.set_analysis_mode(1, "device")
        self.assertTrue(store.process(make_frame(0, 0x0002, 0)))
        self.assertTrue(store.process(make_frame(1, 0x0002, 100000)))
        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["lost_packets"], 0)
        self.assertEqual(snapshot["continuity_gap_count"], 1)
        self.assertEqual(snapshot["last_discontinuity_reason"], "sample_time_gap")
        self.assertEqual(snapshot["interrupted_movement_count"], 0)
        self.assertEqual(snapshot["interpolated_sample_count"], 0)
        self.assertEqual(snapshot["current_movement"]["sample_count"], 8)
        self.assertEqual(snapshot["current_movement"]["segment_count"], 2)
        self.assertEqual(snapshot["current_movement"]["gap_count"], 1)

    def test_long_gap_preserves_pc_calibration_baseline(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        store.set_analysis_mode(1, "pc")
        packet = 0
        tick = 0
        for _ in range(25):
            self.assertTrue(store.process(make_frame(packet, 0x0001, tick)))
            packet += 1
            tick += 4 * 328
        calibrated = store.snapshot(1)
        self.assertEqual(calibrated["pc_mode_state"], "静止")

        self.assertTrue(
            store.process(make_frame(packet, 0x0001, tick + 100000))
        )
        after = store.snapshot(1)

        self.assertEqual(after["last_discontinuity_reason"], "sample_time_gap")
        self.assertEqual(after["pc_mode_state"], "静止")
        self.assertEqual(after["pc_detector"]["state"], "stationary")

    def test_host_arrival_gap_resumes_without_clearing_visible_movement(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        store.set_analysis_mode(1, "device")
        self.assertTrue(store.process(make_frame(0, 0x0002, 0)))
        store._devices[1].last_arrival_monotonic -= 2.0

        self.assertTrue(store.process(make_frame(1, 0x0002, 4 * 328)))
        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["lost_packets"], 0)
        self.assertEqual(snapshot["continuity_gap_count"], 1)
        self.assertEqual(snapshot["last_discontinuity_reason"], "host_arrival_gap")
        self.assertEqual(snapshot["interrupted_movement_count"], 0)
        self.assertEqual(snapshot["current_movement"]["sample_count"], 8)
        self.assertEqual(snapshot["current_movement"]["segment_count"], 2)
        self.assertGreater(len(snapshot["calculated_path"]), 0)

    def test_host_arrival_gap_accepts_same_session_sequence_restart(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        store.set_analysis_mode(1, "device")
        self.assertTrue(store.process(make_frame(50000, 0x0002, 100000)))
        store._devices[1].last_arrival_monotonic -= 2.0

        self.assertTrue(store.process(make_frame(0, 0x0002, 0)))
        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["packet_count"], 2)
        self.assertEqual(snapshot["lost_packets"], 0)
        self.assertEqual(snapshot["out_of_order_packet_count"], 0)
        self.assertEqual(snapshot["last_packet_sequence"], 0)
        self.assertEqual(snapshot["last_discontinuity_reason"], "host_arrival_gap")

    def test_stream_format_change_accepts_same_session_sequence_restart(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        self.assertTrue(store.process(make_frame(50000, 0x0001, 100000)))

        self.assertTrue(
            store.process(
                make_frame(
                    0,
                    0x0001,
                    0,
                    source="motion-v2-100b",
                )
            )
        )
        snapshot = store.snapshot(1)

        self.assertEqual(snapshot["packet_count"], 2)
        self.assertEqual(snapshot["lost_packets"], 0)
        self.assertEqual(snapshot["out_of_order_packet_count"], 0)
        self.assertEqual(snapshot["source"], "motion-v2-100b")
        self.assertEqual(
            snapshot["last_discontinuity_reason"], "stream_format_changed"
        )

    def test_shake_and_flip_are_counted_independently(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])
        for index in range(200):
            direction = 16.0 if (index // 5) % 2 == 0 else -16.0
            movement.add_sample(InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=[0.0, 0.0, 0.0],
                corrected_gyro_dps=[0.0, 0.0, 0.0],
                linear_world=[direction, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0],
                position=[0.0, 0.0, 0.0],
                up_world=[0.0, 0.0, -1.0],
                dt=0.01,
                stationary=False,
            ), index * 328)

        self.assertEqual(movement.shake_count, 1)
        self.assertEqual(movement.flip_count, 1)

    def test_continuous_shake_peaks_are_one_episode(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])

        def estimate(linear_x: float, gyro_z: float = 0.0) -> InertialEstimate:
            return InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=[0.0, 0.0, gyro_z],
                corrected_gyro_dps=[0.0, 0.0, gyro_z],
                linear_world=[linear_x, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0],
                position=[0.0, 0.0, 0.0],
                up_world=[0.0, 0.0, 1.0],
                dt=0.01,
                stationary=False,
            )

        tick = 0
        for index in range(300):
            direction = 16.0 if (index // 5) % 2 == 0 else -16.0
            movement.add_sample(estimate(direction), tick, index, 1000.0 + index * 0.01)
            tick += 328
        for index in range(130):
            movement.add_sample(estimate(0.0), tick, 300 + index, 1003.0 + index * 0.01)
            tick += 328

        self.assertEqual(movement.shake_count, 1)
        self.assertFalse(movement.shake_active)
        event = movement.shake_events[0]
        self.assertGreater(event["duration_seconds"], 2.5)
        self.assertGreaterEqual(event["direction_reversals"], 2)

    def test_shakes_separated_by_merge_gap_are_two_episodes(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])

        def estimate(linear_x: float) -> InertialEstimate:
            return InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=[0.0, 0.0, 0.0],
                corrected_gyro_dps=[0.0, 0.0, 0.0],
                linear_world=[linear_x, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0],
                position=[0.0, 0.0, 0.0],
                up_world=[0.0, 0.0, 1.0],
                dt=0.01,
                stationary=False,
            )

        tick = 0
        sequence = 0
        for sample_count, shaking in ((150, True), (150, False), (150, True)):
            for local_index in range(sample_count):
                direction = (
                    16.0 if (local_index // 5) % 2 == 0 else -16.0
                ) if shaking else 0.0
                movement.add_sample(estimate(direction), tick, sequence)
                tick += 328
                sequence += 1

        self.assertEqual(movement.shake_count, 2)

    def test_isolated_impact_and_pure_rotation_are_not_shakes(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])

        def estimate(linear_x: float, gyro_z: float = 0.0) -> InertialEstimate:
            return InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=[0.0, 0.0, gyro_z],
                corrected_gyro_dps=[0.0, 0.0, gyro_z],
                linear_world=[linear_x, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0],
                position=[0.0, 0.0, 0.0],
                up_world=[0.0, 0.0, 1.0],
                dt=0.01,
                stationary=False,
            )

        movement.add_sample(estimate(30.0), 0)
        for index in range(1, 100):
            movement.add_sample(estimate(0.0, 500.0), index * 328)

        self.assertEqual(movement.shake_count, 0)

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

        movement = completed_snapshot(store)["completed_movements"][0]
        self.assertEqual(movement["pause_count"], 1)
        self.assertGreaterEqual(movement["pause_total_seconds"], 0.8)
        self.assertGreaterEqual(movement["stationary_seconds"], 0.8)
        self.assertAlmostEqual(movement["stationary_seconds"], movement["pause_total_seconds"])
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

        movement = completed_snapshot(store)["completed_movements"][0]
        self.assertEqual(movement["pause_count"], 1)
        self.assertGreaterEqual(movement["pause_total_seconds"], 0.8)
        self.assertAlmostEqual(movement["stationary_seconds"], movement["pause_total_seconds"])

    def test_pc_isolated_motion_candidate_does_not_strand_stopped_movement(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        store.set_analysis_mode(1, "pc")
        packet = 0
        tick = 0

        for accel, gyro, count in (
            ((0, 0, 4096), (0, 0, 0), 25),
            ((0, 0, 4096), (0, 0, 1200), 5),
            ((0, 0, 4096), (0, 0, 0), PAUSE_STATIONARY_FRAMES),
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

        # One noisy sample crosses the start threshold, but the following
        # samples are quiet.  It is only a candidate, not a confirmed restart.
        candidate_frame = make_frame(packet, 0x0001, tick)
        candidate_samples = list(candidate_frame.samples)
        first = candidate_samples[0]
        candidate_samples[0] = ImuSample(
            sequence=first.sequence,
            tick_32k=first.tick_32k,
            accel_raw=first.accel_raw,
            gyro_raw=(0, 0, 1200),
        )
        store.process(
            MotionFrame(
                device_id=candidate_frame.device_id,
                packet_sequence=candidate_frame.packet_sequence,
                session_id=candidate_frame.session_id,
                flags=candidate_frame.flags,
                samples=tuple(candidate_samples),
                coordinate_source_sequence=candidate_frame.coordinate_source_sequence,
                position_mm=candidate_frame.position_mm,
                coordinate_flags=candidate_frame.coordinate_flags,
                source=candidate_frame.source,
            )
        )
        packet += 1
        tick += 4 * 328

        during_hold = store.snapshot(1)
        self.assertIsNotNone(during_hold["current_movement"])
        self.assertEqual(during_hold["current_movement"]["pause_count"], 0)
        self.assertIn(during_hold["motion_state"], {"停止确认", "停顿中"})

        for _ in range(TERMINAL_STATIONARY_FRAMES):
            store.process(make_frame(packet, 0x0001, tick))
            packet += 1
            tick += 4 * 328

        movement = completed_snapshot(store)["completed_movements"][0]
        self.assertEqual(movement["pause_count"], 0)
        self.assertIsNone(store.snapshot(1)["current_movement"])

    def test_pc_live_path_excludes_stop_confirmation_window(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        store.set_analysis_mode(1, "pc")
        packet = 0
        tick = 0

        # Calibrate, then create a movement long enough to expose a live path.
        for accel, gyro, count in (
            ((0, 0, 4096), (0, 0, 0), 25),
            ((0, 0, 4096), (0, 0, 1200), 5),
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

        before_quiet = store.snapshot(1)
        path_before_quiet = list(before_quiet["calculated_path"])
        self.assertGreater(len(path_before_quiet), 1)

        # Five quiet frames accept the backdated stop; additional quiet frames
        # are the three-second end hold and must not extend the visible path.
        for _ in range(15):
            store.process(
                make_frame(
                    packet,
                    0x0001,
                    tick,
                    accel_raw=(0, 0, 4096),
                    gyro_raw=(0, 0, 0),
                )
            )
            packet += 1
            tick += 4 * 328

        during_hold = store.snapshot(1)
        self.assertIsNotNone(during_hold["current_movement"])
        self.assertIn(during_hold["motion_state"], {"停止确认", "停顿中"})
        self.assertEqual(during_hold["calculated_path"], path_before_quiet)

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
        # Reversal starts an independent one-axis segment after confirmation;
        # it no longer cancels the unfinished forward segment.
        self.assertAlmostEqual(
            movement.rotation_progress_degrees, 270.0, places=5
        )
        snapshot = movement.snapshot("moving")
        self.assertAlmostEqual(
            snapshot["spatial_rotation_degrees"], 540.0, places=5
        )
        self.assertEqual(snapshot["spatial_rotation_count"], 1)
        self.assertAlmostEqual(
            snapshot["spatial_rotation_turns"], 1.5, places=5
        )

    def test_axis_switch_discards_old_progress_and_replays_new_axis_buffer(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])

        def estimate(rate: list[float]) -> InertialEstimate:
            return InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=rate,
                corrected_gyro_dps=rate,
                linear_world=[0.0, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0],
                position=[0.0, 0.0, 0.0],
                up_world=[0.0, 0.0, 1.0],
                dt=0.01,
                stationary=False,
                gyro_world_dps=rate,
            )

        for index in range(150):
            movement.add_sample(estimate([100.0, 0.0, 0.0]), index * 328)
        for index in range(150, 490):
            movement.add_sample(estimate([0.0, 100.0, 0.0]), index * 328)

        self.assertEqual(movement.rotation_count, 1)
        self.assertAlmostEqual(
            movement.rotation_progress_degrees, 10.0, places=5
        )

    def test_short_axis_excursion_does_not_replace_current_axis(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])

        def estimate(rate: list[float]) -> InertialEstimate:
            return InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=rate,
                corrected_gyro_dps=rate,
                linear_world=[0.0, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0],
                position=[0.0, 0.0, 0.0],
                up_world=[0.0, 0.0, 1.0],
                dt=0.01,
                stationary=False,
                gyro_world_dps=rate,
            )

        tick = 0
        for _ in range(160):
            movement.add_sample(estimate([100.0, 0.0, 0.0]), tick)
            tick += 328
        for _ in range(8):
            movement.add_sample(estimate([0.0, 100.0, 0.0]), tick)
            tick += 328
        for _ in range(170):
            movement.add_sample(estimate([100.0, 0.0, 0.0]), tick)
            tick += 328

        self.assertEqual(movement.rotation_count, 1)
        self.assertAlmostEqual(
            movement.rotation_progress_degrees, 0.0, places=5
        )

    def test_one_axis_rotation_uses_world_space_angular_direction(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])
        component = 180.0 / (2.0 ** 0.5)
        changing_body_rates = (
            [180.0, 0.0, 0.0],
            [0.0, 180.0, 0.0],
        )
        for index in range(200):
            body_rate = changing_body_rates[(index // 10) % 2]
            estimate = InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=body_rate,
                corrected_gyro_dps=body_rate,
                linear_world=[0.0, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0],
                position=[0.0, 0.0, 0.0],
                up_world=[0.0, 0.0, 1.0],
                dt=0.01,
                stationary=False,
                gyro_world_dps=[component, component, 0.0],
            )
            movement.add_sample(estimate, index * 328)

        self.assertEqual(movement.rotation_count, 1)

    def test_spatial_rotation_accumulates_across_orthogonal_axes(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])
        rates = ([120.0, 0.0, 0.0], [0.0, 120.0, 0.0], [0.0, 0.0, 120.0])
        tick = 0
        for rate in rates:
            for _ in range(100):
                estimate = InertialEstimate(
                    accel=[0.0, 0.0, 9.80665],
                    gyro_dps=rate,
                    corrected_gyro_dps=rate,
                    linear_world=[0.0, 0.0, 0.0],
                    velocity=[0.0, 0.0, 0.0],
                    position=[0.0, 0.0, 0.0],
                    up_world=[0.0, 0.0, 1.0],
                    dt=0.01,
                    stationary=False,
                    gyro_world_dps=rate,
                )
                movement.add_sample(estimate, tick)
                tick += 328

        snapshot = movement.snapshot("moving")
        self.assertAlmostEqual(
            snapshot["spatial_rotation_degrees"], 360.0, places=5
        )
        self.assertEqual(snapshot["spatial_rotation_count"], 1)
        self.assertEqual(movement.rotation_count, 0)

    def test_spatial_rotation_discards_noise_and_short_segments(self) -> None:
        movement = MovementStats(1, 0, [0.0, 0.0, 1.0])

        def estimate(rate: float) -> InertialEstimate:
            return InertialEstimate(
                accel=[0.0, 0.0, 9.80665],
                gyro_dps=[rate, 0.0, 0.0],
                corrected_gyro_dps=[rate, 0.0, 0.0],
                linear_world=[0.0, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0],
                position=[0.0, 0.0, 0.0],
                up_world=[0.0, 0.0, 1.0],
                dt=0.01,
                stationary=False,
            )

        tick = 0
        for _ in range(1000):
            movement.add_sample(estimate(9.0), tick)
            tick += 328
        for _ in range(100):
            movement.add_sample(estimate(12.0), tick)
            tick += 328
        for _ in range(40):
            movement.add_sample(estimate(0.0), tick)
            tick += 328

        snapshot = movement.snapshot("moving")
        self.assertEqual(snapshot["spatial_rotation_degrees"], 0.0)
        self.assertEqual(snapshot["spatial_rotation_count"], 0)

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

    def test_archived_movement_cache_keeps_summaries_and_two_recent_details(self) -> None:
        pipeline = AnalysisPipeline("pc")
        movements: list[MovementStats] = []
        for movement_id in range(1, 5):
            movement = MovementStats(movement_id, 0, [0.0, 0.0, 1.0])
            movement.finalized = True
            movement.sample_records = [
                {"sample_index": index} for index in range(10)
            ]
            movement.device_coordinate_records = [[0.0, 0.0, 0.0]]
            movement.calculated_path_final = [[0.0, 0.0, 0.0]]
            movement.received_path_final = [[0.0, 0.0, 0.0]]
            movements.append(movement)
        pipeline.completed_movements.extend(reversed(movements))

        for movement_id in range(1, 5):
            pipeline.mark_movement_archived(movement_id)

        by_id = {
            movement.movement_id: movement
            for movement in pipeline.completed_movements
        }
        self.assertTrue(by_id[1].high_frequency_released)
        self.assertTrue(by_id[2].high_frequency_released)
        self.assertEqual(by_id[1].sample_records, [])
        self.assertFalse(by_id[3].high_frequency_released)
        self.assertFalse(by_id[4].high_frequency_released)
        self.assertEqual(len(pipeline.completed_movements), 4)
        self.assertEqual(pipeline.finalization_snapshot()["detail_cache_count"], 2)

    def test_task_external_cache_releases_old_unarchived_details(self) -> None:
        pipeline = AnalysisPipeline("pc")
        pipeline.set_task_collection_active(True)
        movements: list[MovementStats] = []
        for movement_id in range(1, 5):
            movement = MovementStats(movement_id, 0, [0.0, 0.0, 1.0])
            movement.finalized = True
            movement.sample_records = [
                {"sample_index": index} for index in range(10)
            ]
            movement.calculated_path_final = [[0.0, 0.0, 0.0]]
            movements.append(movement)
        pipeline.completed_movements.extend(reversed(movements))

        self.assertTrue(all(not item.high_frequency_released for item in movements))
        released = pipeline.set_task_collection_active(False)

        self.assertEqual(released, 2)
        self.assertTrue(movements[0].high_frequency_released)
        self.assertTrue(movements[1].high_frequency_released)
        self.assertFalse(movements[2].high_frequency_released)
        self.assertFalse(movements[3].high_frequency_released)
        self.assertEqual(len(pipeline.completed_movements), 4)

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
        movement_id = completed_snapshot(store)["completed_movements"][0]["movement_id"]
        detail = store.movement_detail(1, movement_id, "device")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertIn("acceleration_series", detail)
        self.assertIn("gyroscope_series", detail)
        self.assertEqual(len(detail["gyroscope_series"]), len(detail["acceleration_series"]))
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
        snapshot = completed_snapshot(store)

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
        self.assertEqual(len(live["accel_sample"]), 4)
        self.assertEqual(len(live["gyro_sample"]), 4)
        self.assertIn("orientation", live)
        self.assertIn("calculated_position_m", live)
        self.assertFalse(live["position_semantics"]["pc_metric_reliable"])
        self.assertEqual(
            live["position_semantics"]["default_display"], "relative_shape"
        )
        self.assertNotIn("calculated_path", live)
        self.assertNotIn("accel", live)

    def test_live_snapshot_returns_trajectory_points_after_cursor(self) -> None:
        store = MotionStore()
        state = store._devices[1]
        pipeline = state.analyses[state.analysis_mode]
        pipeline.trajectory_revision = 4
        pipeline.calculated_path.extend([
            [0.0, 0.0, 0.0],
            [0.1, 0.0, 0.0],
            [0.2, 0.1, 0.0],
        ])
        pipeline.calculated_path_cursor = 3

        initial = store.live_snapshot(1)
        delta = store.live_snapshot(1, 4, 0, 1)

        self.assertTrue(initial["trajectory_delta"]["reset"])
        self.assertEqual(
            initial["trajectory_delta"]["calculated"]["points"],
            list(pipeline.calculated_path),
        )
        self.assertFalse(delta["trajectory_delta"]["reset"])
        self.assertEqual(
            delta["trajectory_delta"]["calculated"]["points"],
            [[0.1, 0.0, 0.0], [0.2, 0.1, 0.0]],
        )
        self.assertEqual(delta["trajectory_delta"]["calculated"]["cursor"], 3)

        stale = store.live_snapshot(1, 4, 0, 99)
        self.assertTrue(stale["trajectory_delta"]["calculated"]["truncated"])
        self.assertEqual(
            stale["trajectory_delta"]["calculated"]["points"],
            list(pipeline.calculated_path),
        )


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
        snapshot = completed_snapshot(store)
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
        self.assertEqual(completed_snapshot(store)["completed_movement_count"], 1)

        result = store.set_minimum_movement_seconds(0.30)
        self.assertGreaterEqual(result["removed_existing_records_across_pipelines"], 1)
        snapshot = store.snapshot(1)
        self.assertEqual(snapshot["completed_movement_count"], 0)
        self.assertEqual(snapshot["movement_filter"]["minimum_seconds"], 0.30)

    def test_movement_finalization_does_not_block_frame_ingestion(self) -> None:
        store = MotionStore(minimum_movement_seconds=0.0)
        original_finalize = MovementStats.finalize
        worker_started = threading.Event()
        release_worker = threading.Event()
        worker_names: list[str] = []

        def blocked_finalize(movement: MovementStats, *args, **kwargs) -> None:
            worker_names.append(threading.current_thread().name)
            worker_started.set()
            if not release_worker.wait(timeout=3.0):
                raise TimeoutError("test finalization worker was not released")
            original_finalize(movement, *args, **kwargs)

        try:
            with patch.object(MovementStats, "finalize", blocked_finalize):
                packet = 0
                tick = 0
                for flags, count in (
                    (0x0001, 5),
                    (0x0002, 10),
                    (0x0001, TERMINAL_STATIONARY_FRAMES),
                ):
                    for _ in range(count):
                        store.process(make_frame(packet, flags, tick))
                        packet += 1
                        tick += 4 * 328

                self.assertTrue(worker_started.wait(timeout=1.0))
                pending = store.snapshot(1)
                self.assertEqual(pending["finalization"]["pending_count"], 1)
                self.assertEqual(pending["completed_movement_count"], 0)

                started = time.perf_counter()
                self.assertTrue(
                    store.process(
                        make_frame(
                            0,
                            0x0001,
                            0,
                            device_id=2,
                            session_id=2,
                        )
                    )
                )
                self.assertLess(time.perf_counter() - started, 0.25)
        finally:
            release_worker.set()

        self.assertTrue(store.wait_for_finalizations(timeout_seconds=3.0))
        snapshot = store.snapshot(1)
        self.assertEqual(snapshot["completed_movement_count"], 1)
        self.assertEqual(snapshot["finalization"]["pending_count"], 0)
        self.assertTrue(
            all(name.startswith("motion-finalize") for name in worker_names)
        )

    def test_block_mapping_is_loaded_and_frozen_into_movement(self) -> None:
        mapping_path = Path(__file__).resolve().parents[1] / "defaults" / "block_device_mapping.json"
        store = MotionStore(mapping_path=mapping_path)
        configured_payload = json.loads(mapping_path.read_text(encoding="utf-8"))
        configured_version = configured_payload["mapping_version"]
        configured_blocks = {
            entry["device_id"]: entry["block_id"]
            for entry in configured_payload["devices"]
        }

        mapping = store.block_mapping_snapshot()
        self.assertEqual(mapping["mapping_version"], configured_version)
        self.assertEqual(mapping["devices"][0], {"device_id": 1, "block_id": configured_blocks[1]})
        self.assertEqual(mapping["devices"][-1], {"device_id": 10, "block_id": configured_blocks[10]})
        self.assertEqual(store.snapshot(1)["block_id"], configured_blocks[1])

        packet = 0
        tick = 0
        for flags, count in (
            (0x0001, 5),
            (0x0002, 10),
            (0x0001, TERMINAL_STATIONARY_FRAMES),
        ):
            for _ in range(count):
                store.process(make_frame(packet, flags, tick))
                packet += 1
                tick += 4 * 328

        snapshot = completed_snapshot(store)
        movement = snapshot["completed_movements"][0]
        self.assertEqual(movement["block_id"], configured_blocks[1])
        self.assertEqual(movement["mapping_version"], configured_version)
        exported = store.movement_export(
            1,
            movement["movement_id"],
            "csv",
            "device",
        )
        self.assertIn(f"metadata,block_id,{configured_blocks[1]}", exported or "")
        self.assertIn(
            f"metadata,mapping_version,{configured_version}",
            exported or "",
        )

    def test_block_mapping_can_be_updated_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mapping_path = Path(directory) / "block_device_mapping.json"
            mapping_path.write_text(
                json.dumps(
                    {
                        "mapping_version": "test-v1",
                        "devices": [
                            {"device_id": device_id, "block_id": chr(64 + device_id)}
                            for device_id in range(1, 11)
                        ],
                    }
                ),
                encoding="utf-8",
            )
            store = MotionStore(mapping_path=mapping_path)
            reversed_mapping = [
                {"device_id": device_id, "block_id": chr(75 - device_id)}
                for device_id in range(1, 11)
            ]

            updated = store.update_block_mapping(reversed_mapping, "test-v2")

            self.assertEqual(updated["mapping_version"], "test-v2")
            self.assertEqual(store.snapshot(1)["block_id"], "J")
            persisted = json.loads(mapping_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["devices"][0], {"device_id": 1, "block_id": "J"})
            with self.assertRaisesRegex(ValueError, "A～J"):
                store.update_block_mapping(
                    [{"device_id": device_id, "block_id": "A"} for device_id in range(1, 11)],
                    "invalid",
                )

if __name__ == "__main__":
    unittest.main()
