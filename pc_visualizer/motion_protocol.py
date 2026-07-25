"""Motion V1 packet and ESP32 diagnostic-line decoding."""

from __future__ import annotations

from dataclasses import dataclass
import re
import struct
from typing import Optional
import zlib


MOTION_PACKET_SIZE = 100
MOTION_PROTOCOL_VERSION = 1
MOTION_PACKET_TYPE_DATA = 0x10
MOTION_SAMPLE_COUNT = 4
ACCEL_LSB_PER_G = 8192.0
GYRO_LSB_PER_DPS = 65.5
TICK_HZ = 32768.0
GATEWAY_FRAME_VERSION = 1
GATEWAY_FRAME_MOTION_DATA = 0x10
GATEWAY_HEADER_SIZE = 10
GATEWAY_RAW_FRAME_SIZE = 114
GATEWAY_MAX_ENCODED_SIZE = 115

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
    motion: MotionFrame


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


def encode_gateway_frame(
    motion_payload: bytes,
    gateway_id: int = 0,
    gateway_sequence: int = 0,
) -> bytes:
    """Build COBS(raw gateway frame) + 0x00, mainly for tests/tools."""
    if len(motion_payload) != MOTION_PACKET_SIZE:
        raise ValueError("gateway motion payload must be 100 bytes")
    raw = bytearray(GATEWAY_RAW_FRAME_SIZE)
    struct.pack_into(
        "<BBBBIH",
        raw,
        0,
        GATEWAY_FRAME_VERSION,
        gateway_id,
        GATEWAY_FRAME_MOTION_DATA,
        0,
        gateway_sequence,
        MOTION_PACKET_SIZE,
    )
    raw[GATEWAY_HEADER_SIZE : GATEWAY_HEADER_SIZE + MOTION_PACKET_SIZE] = (
        motion_payload
    )
    crc = zlib.crc32(raw[:-4]) & 0xFFFFFFFF
    struct.pack_into("<I", raw, len(raw) - 4, crc)
    return cobs_encode(bytes(raw)) + b"\x00"


def decode_gateway_frame(raw: bytes) -> GatewayFrame:
    """Validate one decoded 114-byte gateway frame."""
    if len(raw) != GATEWAY_RAW_FRAME_SIZE:
        raise GatewayFrameError(
            f"expected 114 gateway bytes, received {len(raw)}"
        )

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
    if frame_type != GATEWAY_FRAME_MOTION_DATA:
        raise GatewayFrameError("unsupported gateway frame type")
    if reserved != 0:
        raise GatewayFrameError("gateway reserved byte is not zero")
    if payload_length != MOTION_PACKET_SIZE:
        raise GatewayFrameError("unexpected gateway payload length")

    expected_crc = struct.unpack_from("<I", raw, len(raw) - 4)[0]
    actual_crc = zlib.crc32(raw[:-4]) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise GatewayCrcError(
            f"gateway CRC mismatch: {actual_crc:08x} != {expected_crc:08x}"
        )

    payload = raw[
        GATEWAY_HEADER_SIZE : GATEWAY_HEADER_SIZE + payload_length
    ]
    return GatewayFrame(
        gateway_id=gateway_id,
        gateway_sequence=gateway_sequence,
        motion=decode_motion_packet(payload),
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
