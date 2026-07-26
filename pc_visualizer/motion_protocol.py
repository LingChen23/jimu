"""Motion V1 packet and ESP32 diagnostic-line decoding."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import re
import struct
from typing import Optional
import zlib


MOTION_PACKET_SIZE = 100
MOTION_PROTOCOL_VERSION = 1
MOTION_PACKET_TYPE_DATA = 0x10
MOTION_SAMPLE_COUNT = 4
ACCEL_LSB_PER_G = 4096.0
GYRO_LSB_PER_DPS = 32.8
TICK_HZ = 32768.0
GATEWAY_FRAME_VERSION = 1
GATEWAY_FRAME_MOTION_DATA = 0x10
GATEWAY_FRAME_CONTROL_REQUEST = 0x20
GATEWAY_FRAME_CONTROL_RESPONSE = 0x21
GATEWAY_FRAME_LINK_DIAGNOSTIC = 0x30
GATEWAY_HEADER_SIZE = 10
GATEWAY_CRC_SIZE = 4
GATEWAY_MAX_RAW_FRAME_SIZE = 114
GATEWAY_MAX_ENCODED_SIZE = 115
MOTION_CONTROL_VERSION = 1
LINK_DIAGNOSTIC_VERSION = 2
LINK_DIAGNOSTIC_V1_PAYLOAD_SIZE = 44
LINK_DIAGNOSTIC_PAYLOAD_SIZE = 64


class MotionCommand(IntEnum):
    START_STREAM = 0x01
    STOP_STREAM = 0x02
    GET_INFO = 0x03
    GET_STATUS = 0x04
    RESET_STATS = 0x05
    RECALIBRATE = 0x06
    REBOOT = 0x07

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_HEX_RE = re.compile(r"MOTION_V1_HEX\s*[:=,]\s*([0-9a-fA-F]{200})")
_DIAGNOSTIC_RE = re.compile(
    r"id=(?P<device>\d+)\s+"
    r"pkt=(?P<packet>\d+)\s+"
    r"sample=(?P<sample>\d+)\s+"
    r"tick=(?P<tick>\d+)\s+"
    r"flags=0x(?P<flags>[0-9a-fA-F]+)\]\s+"
    r"s0\s+a=(?P<ax>-?\d+),(?P<ay>-?\d+),(?P<az>-?\d+)\s+"
    r"g=(?P<gx>-?\d+),(?P<gy>-?\d+),(?P<gz>-?\d+)\s+"
    r"s3dt=(?P<s3dt>\d+)\s+"
    r"coord_seq=(?P<coord_seq>\d+)\s+"
    r"xyz=(?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\s+"
    r"cflags=0x(?P<cflags>[0-9a-fA-F]+)"
)


@dataclass(frozen=True)
class ImuSample:
    sequence: int
    tick_32k: int
    accel_raw: tuple[int, int, int]
    gyro_raw: tuple[int, int, int]


@dataclass(frozen=True)
class MotionFrame:
    device_id: int
    packet_sequence: int
    session_id: int
    flags: int
    samples: tuple[ImuSample, ...]
    coordinate_source_sequence: int
    position_mm: tuple[int, int, int]
    coordinate_flags: int
    source: str

    @property
    def coordinate_valid(self) -> bool:
        return bool(self.flags & 0x40)


@dataclass(frozen=True)
class GatewayFrame:
    gateway_id: int
    gateway_sequence: int
    frame_type: int
    motion: MotionFrame | None = None
    control_response: "ControlResponse | None" = None
    link_diagnostic: "LinkDiagnostic | None" = None


@dataclass(frozen=True)
class ControlResponse:
    device_id: int
    command: int
    request_id: int
    result: int
    payload: bytes


@dataclass(frozen=True)
class LinkDiagnostic:
    device_id: int
    connected: bool
    ready: bool
    rssi_valid: bool
    rssi_dbm: int | None
    connection_interval_units: int
    connection_interval_ms: float | None
    last_disconnect_reason: int
    receive_rate_1s_pps: float
    receive_rate_10s_pps: float
    received_frames: int
    sequence_gap_frames: int
    invalid_frames: int
    connect_count: int
    disconnect_count: int
    connection_update_count: int
    gateway_uptime_ms: int
    gateway_queue_drops: int
    att_mtu: int | None
    dle_requested: bool
    dle_valid: bool
    max_tx_octets: int | None
    max_tx_time_us: int | None
    max_rx_octets: int | None
    max_rx_time_us: int | None
    phy_valid: bool
    tx_phy: int | None
    rx_phy: int | None
    dle_request_result: int | None
    phy_read_result: int | None
    dle_update_count: int


class CobsDecodeError(ValueError):
    pass


class GatewayFrameError(ValueError):
    pass


class GatewayCrcError(GatewayFrameError):
    pass


def cobs_encode(data: bytes) -> bytes:
    """Encode bytes with Consistent Overhead Byte Stuffing."""
    output = bytearray((0,))
    code_index = 0
    code = 1

    for value in data:
        if value == 0:
            output[code_index] = code
            code_index = len(output)
            output.append(0)
            code = 1
            continue

        output.append(value)
        code += 1
        if code == 0xFF:
            output[code_index] = code
            code_index = len(output)
            output.append(0)
            code = 1

    output[code_index] = code
    return bytes(output)


def cobs_decode(data: bytes) -> bytes:
    """Decode one COBS frame without its trailing zero delimiter."""
    if not data:
        raise CobsDecodeError("empty COBS frame")

    output = bytearray()
    index = 0
    while index < len(data):
        code = data[index]
        if code == 0:
            raise CobsDecodeError("zero byte inside COBS frame")
        index += 1
        end = index + code - 1
        if end > len(data):
            raise CobsDecodeError("COBS code exceeds frame length")
        output.extend(data[index:end])
        index = end
        if code != 0xFF and index < len(data):
            output.append(0)
    return bytes(output)


def encode_gateway_payload(
    frame_type: int,
    payload: bytes,
    gateway_id: int = 0,
    gateway_sequence: int = 0,
) -> bytes:
    """Build one generic COBS/CRC gateway frame."""
    if len(payload) > MOTION_PACKET_SIZE:
        raise ValueError("gateway payload exceeds 100 bytes")
    raw = bytearray(GATEWAY_HEADER_SIZE + len(payload) + GATEWAY_CRC_SIZE)
    struct.pack_into(
        "<BBBBIH",
        raw,
        0,
        GATEWAY_FRAME_VERSION,
        gateway_id,
        frame_type,
        0,
        gateway_sequence,
        len(payload),
    )
    raw[GATEWAY_HEADER_SIZE : GATEWAY_HEADER_SIZE + len(payload)] = payload
    crc = zlib.crc32(raw[:-4]) & 0xFFFFFFFF
    struct.pack_into("<I", raw, len(raw) - 4, crc)
    return cobs_encode(bytes(raw)) + b"\x00"


def encode_gateway_frame(
    motion_payload: bytes,
    gateway_id: int = 0,
    gateway_sequence: int = 0,
) -> bytes:
    """Build a Motion gateway frame, mainly for tests and capture tools."""
    if len(motion_payload) != MOTION_PACKET_SIZE:
        raise ValueError("gateway motion payload must be 100 bytes")
    return encode_gateway_payload(
        GATEWAY_FRAME_MOTION_DATA,
        motion_payload,
        gateway_id,
        gateway_sequence,
    )


def encode_gateway_command(
    device_id: int,
    command: MotionCommand | int,
    request_id: int,
    command_payload: bytes = b"",
) -> bytes:
    if not 1 <= device_id <= 255:
        raise ValueError("device id must be 1..255")
    if len(command_payload) > 16:
        raise ValueError("command payload exceeds BLE request limit")
    request = struct.pack(
        "<BBBB",
        MOTION_CONTROL_VERSION,
        int(command),
        request_id & 0xFF,
        len(command_payload),
    ) + command_payload
    return encode_gateway_payload(
        GATEWAY_FRAME_CONTROL_REQUEST,
        bytes((device_id,)) + request,
    )


def decode_gateway_frame(raw: bytes) -> GatewayFrame:
    """Validate one decoded variable-length gateway frame."""
    if not GATEWAY_HEADER_SIZE + GATEWAY_CRC_SIZE <= len(raw) <= (
        GATEWAY_MAX_RAW_FRAME_SIZE
    ):
        raise GatewayFrameError(f"invalid gateway length {len(raw)}")

    (
        version,
        gateway_id,
        frame_type,
        reserved,
        gateway_sequence,
        payload_length,
    ) = struct.unpack_from("<BBBBIH", raw, 0)
    if version != GATEWAY_FRAME_VERSION:
        raise GatewayFrameError("unsupported gateway frame version")
    if frame_type not in {
        GATEWAY_FRAME_MOTION_DATA,
        GATEWAY_FRAME_CONTROL_RESPONSE,
        GATEWAY_FRAME_LINK_DIAGNOSTIC,
    }:
        raise GatewayFrameError("unsupported gateway frame type")
    if reserved != 0:
        raise GatewayFrameError("gateway reserved byte is not zero")
    if len(raw) != GATEWAY_HEADER_SIZE + payload_length + GATEWAY_CRC_SIZE:
        raise GatewayFrameError("gateway payload length mismatch")

    expected_crc = struct.unpack_from("<I", raw, len(raw) - 4)[0]
    actual_crc = zlib.crc32(raw[:-4]) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise GatewayCrcError(
            f"gateway CRC mismatch: {actual_crc:08x} != {expected_crc:08x}"
        )

    payload = raw[
        GATEWAY_HEADER_SIZE : GATEWAY_HEADER_SIZE + payload_length
    ]
    if frame_type == GATEWAY_FRAME_MOTION_DATA:
        if payload_length != MOTION_PACKET_SIZE:
            raise GatewayFrameError("unexpected motion payload length")
        return GatewayFrame(
            gateway_id=gateway_id,
            gateway_sequence=gateway_sequence,
            frame_type=frame_type,
            motion=decode_motion_packet(payload),
        )

    if frame_type == GATEWAY_FRAME_LINK_DIAGNOSTIC:
        if payload_length not in (
            LINK_DIAGNOSTIC_V1_PAYLOAD_SIZE,
            LINK_DIAGNOSTIC_PAYLOAD_SIZE,
        ):
            raise GatewayFrameError(
                "unexpected link diagnostic payload length"
            )
        (
            diagnostic_version,
            device_id,
            flags,
            rssi_dbm,
            connection_interval_units,
            last_disconnect_reason,
            receive_rate_1s_centi,
            receive_rate_10s_centi,
            received_frames,
            sequence_gap_frames,
            invalid_frames,
            connect_count,
            disconnect_count,
            connection_update_count,
            gateway_uptime_ms,
            gateway_queue_drops,
        ) = struct.unpack(
            "<BBBbHHHHIIIIIIII",
            payload[:LINK_DIAGNOSTIC_V1_PAYLOAD_SIZE],
        )
        if diagnostic_version == 1:
            if payload_length != LINK_DIAGNOSTIC_V1_PAYLOAD_SIZE:
                raise GatewayFrameError(
                    "unexpected v1 link diagnostic payload length"
                )
            att_mtu = None
            dle_requested = False
            dle_valid = False
            max_tx_octets = None
            max_tx_time_us = None
            max_rx_octets = None
            max_rx_time_us = None
            phy_valid = False
            tx_phy = None
            rx_phy = None
            dle_request_result = None
            phy_read_result = None
            dle_update_count = 0
        elif diagnostic_version == LINK_DIAGNOSTIC_VERSION:
            if payload_length != LINK_DIAGNOSTIC_PAYLOAD_SIZE:
                raise GatewayFrameError(
                    "unexpected v2 link diagnostic payload length"
                )
            (
                raw_att_mtu,
                raw_max_tx_octets,
                raw_max_tx_time_us,
                raw_max_rx_octets,
                raw_max_rx_time_us,
                raw_tx_phy,
                raw_rx_phy,
                raw_dle_request_result,
                raw_phy_read_result,
                dle_update_count,
            ) = struct.unpack("<HHHHHBBhhI", payload[44:])
            mtu_valid = bool(flags & 0x08)
            dle_requested = bool(flags & 0x10)
            dle_valid = bool(flags & 0x20)
            phy_valid = bool(flags & 0x40)
            att_mtu = raw_att_mtu if mtu_valid else None
            max_tx_octets = raw_max_tx_octets if dle_valid else None
            max_tx_time_us = raw_max_tx_time_us if dle_valid else None
            max_rx_octets = raw_max_rx_octets if dle_valid else None
            max_rx_time_us = raw_max_rx_time_us if dle_valid else None
            tx_phy = raw_tx_phy if phy_valid else None
            rx_phy = raw_rx_phy if phy_valid else None
            dle_request_result = (
                raw_dle_request_result if dle_requested else None
            )
            phy_read_result = raw_phy_read_result
        else:
            raise GatewayFrameError(
                "unsupported link diagnostic version"
            )
        rssi_valid = bool(flags & 0x04)
        return GatewayFrame(
            gateway_id=gateway_id,
            gateway_sequence=gateway_sequence,
            frame_type=frame_type,
            link_diagnostic=LinkDiagnostic(
                device_id=device_id,
                connected=bool(flags & 0x01),
                ready=bool(flags & 0x02),
                rssi_valid=rssi_valid,
                rssi_dbm=rssi_dbm if rssi_valid else None,
                connection_interval_units=connection_interval_units,
                connection_interval_ms=(
                    connection_interval_units * 1.25
                    if connection_interval_units
                    else None
                ),
                last_disconnect_reason=last_disconnect_reason,
                receive_rate_1s_pps=receive_rate_1s_centi / 100.0,
                receive_rate_10s_pps=receive_rate_10s_centi / 100.0,
                received_frames=received_frames,
                sequence_gap_frames=sequence_gap_frames,
                invalid_frames=invalid_frames,
                connect_count=connect_count,
                disconnect_count=disconnect_count,
                connection_update_count=connection_update_count,
                gateway_uptime_ms=gateway_uptime_ms,
                gateway_queue_drops=gateway_queue_drops,
                att_mtu=att_mtu,
                dle_requested=dle_requested,
                dle_valid=dle_valid,
                max_tx_octets=max_tx_octets,
                max_tx_time_us=max_tx_time_us,
                max_rx_octets=max_rx_octets,
                max_rx_time_us=max_rx_time_us,
                phy_valid=phy_valid,
                tx_phy=tx_phy,
                rx_phy=rx_phy,
                dle_request_result=dle_request_result,
                phy_read_result=phy_read_result,
                dle_update_count=dle_update_count,
            ),
        )

    if payload_length < 6:
        raise GatewayFrameError("control response is too short")
    device_id = payload[0]
    version, response_command, request_id, result, response_length = (
        struct.unpack_from("<BBBBB", payload, 1)
    )
    if version != MOTION_CONTROL_VERSION or not (response_command & 0x80):
        raise GatewayFrameError("invalid control response header")
    if payload_length != 6 + response_length:
        raise GatewayFrameError("control response payload length mismatch")
    return GatewayFrame(
        gateway_id=gateway_id,
        gateway_sequence=gateway_sequence,
        frame_type=frame_type,
        control_response=ControlResponse(
            device_id=device_id,
            command=response_command & 0x7F,
            request_id=request_id,
            result=result,
            payload=bytes(payload[6:]),
        ),
    )


class GatewayStreamDecoder:
    """Incrementally split, COBS-decode and validate a serial byte stream."""

    def __init__(self) -> None:
        self._encoded = bytearray()
        self.encoded_frame_count = 0
        self.valid_frame_count = 0
        self.cobs_error_count = 0
        self.crc_error_count = 0
        self.frame_error_count = 0
        self.overflow_count = 0

    def feed(self, data: bytes) -> list[GatewayFrame]:
        frames: list[GatewayFrame] = []
        for value in data:
            if value != 0:
                if len(self._encoded) < GATEWAY_MAX_ENCODED_SIZE * 4:
                    self._encoded.append(value)
                else:
                    self._encoded.clear()
                    self.overflow_count += 1
                continue

            if not self._encoded:
                continue
            encoded = bytes(self._encoded)
            self._encoded.clear()
            self.encoded_frame_count += 1
            try:
                raw = cobs_decode(encoded)
                frame = decode_gateway_frame(raw)
            except CobsDecodeError:
                self.cobs_error_count += 1
                continue
            except GatewayCrcError:
                self.crc_error_count += 1
                continue
            except (GatewayFrameError, ValueError):
                self.frame_error_count += 1
                continue

            self.valid_frame_count += 1
            frames.append(frame)
        return frames


def decode_motion_packet(data: bytes) -> MotionFrame:
    """Decode one fixed-size Motion V1 packet."""
    if len(data) != MOTION_PACKET_SIZE:
        raise ValueError(f"expected 100 bytes, received {len(data)}")
    if data[0] != MOTION_PROTOCOL_VERSION or data[1] != MOTION_PACKET_TYPE_DATA:
        raise ValueError("unsupported Motion packet header")
    if data[22] != MOTION_SAMPLE_COUNT or data[23] != 1:
        raise ValueError("unsupported Motion sample layout")

    device_id = data[2]
    flags = data[3]
    session_id, first_sequence = struct.unpack_from("<II", data, 4)
    first_tick = struct.unpack_from("<Q", data, 12)[0]
    packet_sequence = struct.unpack_from("<H", data, 20)[0]
    samples: list[ImuSample] = []

    for index in range(MOTION_SAMPLE_COUNT):
        offset = 24 + index * 14
        tick_offset, ax, ay, az, gx, gy, gz = struct.unpack_from(
            "<Hhhhhhh", data, offset
        )
        samples.append(
            ImuSample(
                sequence=(first_sequence + index) & 0xFFFFFFFF,
                tick_32k=first_tick + tick_offset,
                accel_raw=(ax, ay, az),
                gyro_raw=(gx, gy, gz),
            )
        )

    coordinate_source_sequence = struct.unpack_from("<I", data, 80)[0]
    position_mm = struct.unpack_from("<iii", data, 84)
    coordinate_flags = struct.unpack_from("<H", data, 96)[0]
    return MotionFrame(
        device_id=device_id,
        packet_sequence=packet_sequence,
        session_id=session_id,
        flags=flags,
        samples=tuple(samples),
        coordinate_source_sequence=coordinate_source_sequence,
        position_mm=position_mm,
        coordinate_flags=coordinate_flags,
        source="motion-v1-100b",
    )


def parse_serial_line(line: str) -> Optional[MotionFrame]:
    """Parse either full packet hex forwarding or the current ESP32 log line."""
    clean = _ANSI_RE.sub("", line)
    hex_match = _HEX_RE.search(clean)
    if hex_match:
        return decode_motion_packet(bytes.fromhex(hex_match.group(1)))

    match = _DIAGNOSTIC_RE.search(clean)
    if not match:
        return None

    value = match.groupdict()
    sample = ImuSample(
        sequence=int(value["sample"]),
        tick_32k=int(value["tick"]),
        accel_raw=(
            int(value["ax"]),
            int(value["ay"]),
            int(value["az"]),
        ),
        gyro_raw=(
            int(value["gx"]),
            int(value["gy"]),
            int(value["gz"]),
        ),
    )
    return MotionFrame(
        device_id=int(value["device"]),
        packet_sequence=int(value["packet"]),
        session_id=0,
        flags=int(value["flags"], 16),
        samples=(sample,),
        coordinate_source_sequence=int(value["coord_seq"]),
        position_mm=(
            int(value["x"]),
            int(value["y"]),
            int(value["z"]),
        ),
        coordinate_flags=int(value["cflags"], 16),
        source="esp32-diagnostic-s0",
    )
