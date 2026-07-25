"""Motion V1 packet and ESP32 diagnostic-line decoding."""

from __future__ import annotations

from dataclasses import dataclass
import re
import struct
from typing import Optional


MOTION_PACKET_SIZE = 100
MOTION_PROTOCOL_VERSION = 1
MOTION_PACKET_TYPE_DATA = 0x10
MOTION_SAMPLE_COUNT = 4
ACCEL_LSB_PER_G = 8192.0
GYRO_LSB_PER_DPS = 65.5
TICK_HZ = 32768.0

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
