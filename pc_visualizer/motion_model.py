"""Thread-safe twelve-device history and a small PC-side inertial estimator."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import threading
import time
from typing import Any

from motion_protocol import (
    ACCEL_LSB_PER_G,
    GYRO_LSB_PER_DPS,
    TICK_HZ,
    ImuSample,
    MotionFrame,
)


GRAVITY = 9.80665
MAX_HISTORY = 600


def _normalize_quaternion(q: list[float]) -> None:
    norm = math.sqrt(sum(value * value for value in q))
    if norm <= 1e-9:
        q[:] = [1.0, 0.0, 0.0, 0.0]
        return
    for index in range(4):
        q[index] /= norm


def _rotate_body_to_world(q: list[float], vector: list[float]) -> list[float]:
    w, x, y, z = q
    vx, vy, vz = vector
    return [
        (1 - 2 * (y * y + z * z)) * vx
        + 2 * (x * y - w * z) * vy
        + 2 * (x * z + w * y) * vz,
        2 * (x * y + w * z) * vx
        + (1 - 2 * (x * x + z * z)) * vy
        + 2 * (y * z - w * x) * vz,
        2 * (x * z - w * y) * vx
        + 2 * (y * z + w * x) * vy
        + (1 - 2 * (x * x + y * y)) * vz,
    ]


@dataclass
class InertialEstimator:
    quaternion: list[float] = field(
        default_factory=lambda: [1.0, 0.0, 0.0, 0.0]
    )
    velocity: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    last_tick: int | None = None
    stationary_samples: int = 0

    def update(
        self, sample: ImuSample, sparse_diagnostic: bool
    ) -> tuple[list[float], list[float], list[float], float]:
        accel = [
            raw * GRAVITY / ACCEL_LSB_PER_G for raw in sample.accel_raw
        ]
        gyro_dps = [raw / GYRO_LSB_PER_DPS for raw in sample.gyro_raw]
        gyro = [math.radians(value) for value in gyro_dps]

        if self.last_tick is None:
            dt = 0.01
        else:
            dt = (sample.tick_32k - self.last_tick) / TICK_HZ
            if dt <= 0.0 or dt > 0.25:
                self.velocity[:] = [0.0, 0.0, 0.0]
                dt = 0.01 if sparse_diagnostic else max(0.001, min(dt, 0.05))
            elif sparse_diagnostic:
                dt = min(dt, 0.05)
        self.last_tick = sample.tick_32k

        accel_norm = math.sqrt(sum(value * value for value in accel))
        gyro_norm_dps = math.sqrt(sum(value * value for value in gyro_dps))
        stationary = (
            abs(accel_norm - GRAVITY) < 0.8 and gyro_norm_dps < 8.0
        )
        self.stationary_samples = (
            self.stationary_samples + 1 if stationary else 0
        )

        if accel_norm > 1e-6 and abs(accel_norm - GRAVITY) < 2.5:
            measured = [value / accel_norm for value in accel]
            w, x, y, z = self.quaternion
            estimated = [
                2 * (x * z - w * y),
                2 * (w * x + y * z),
                w * w - x * x - y * y + z * z,
            ]
            error = [
                measured[1] * estimated[2] - measured[2] * estimated[1],
                measured[2] * estimated[0] - measured[0] * estimated[2],
                measured[0] * estimated[1] - measured[1] * estimated[0],
            ]
            for axis in range(3):
                gyro[axis] += 1.5 * error[axis]

        w, x, y, z = self.quaternion
        gx, gy, gz = gyro
        self.quaternion[0] += 0.5 * (-x * gx - y * gy - z * gz) * dt
        self.quaternion[1] += 0.5 * (w * gx + y * gz - z * gy) * dt
        self.quaternion[2] += 0.5 * (w * gy - x * gz + z * gx) * dt
        self.quaternion[3] += 0.5 * (w * gz + x * gy - y * gx) * dt
        _normalize_quaternion(self.quaternion)

        linear_world = _rotate_body_to_world(self.quaternion, accel)
        linear_world[2] -= GRAVITY
        if self.stationary_samples >= 5:
            self.velocity[:] = [0.0, 0.0, 0.0]
            linear_world = [0.0, 0.0, 0.0]
        else:
            for axis in range(3):
                self.position[axis] += (
                    self.velocity[axis] * dt
                    + 0.5 * linear_world[axis] * dt * dt
                )
                self.velocity[axis] += linear_world[axis] * dt

        return accel, gyro_dps, list(self.position), dt


@dataclass
class DeviceState:
    device_id: int
    estimator: InertialEstimator = field(default_factory=InertialEstimator)
    accel_history: deque = field(
        default_factory=lambda: deque(maxlen=MAX_HISTORY)
    )
    gyro_history: deque = field(
        default_factory=lambda: deque(maxlen=MAX_HISTORY)
    )
    received_path: deque = field(
        default_factory=lambda: deque(maxlen=MAX_HISTORY)
    )
    calculated_path: deque = field(
        default_factory=lambda: deque(maxlen=MAX_HISTORY)
    )
    last_seen: float = 0.0
    first_tick: int | None = None
    last_packet_sequence: int | None = None
    last_sample_sequence: int | None = None
    packet_count: int = 0
    sample_count: int = 0
    lost_packets: int = 0
    source: str = "waiting"
    last_flags: int = 0
    coordinate_flags: int = 0

    def process(self, frame: MotionFrame) -> None:
        if (
            frame.source == "motion-v1-100b"
            and self.last_packet_sequence is not None
            and frame.packet_sequence != self.last_packet_sequence
        ):
            expected = (self.last_packet_sequence + 1) & 0xFFFF
            gap = (frame.packet_sequence - expected) & 0xFFFF
            if 0 < gap < 0x8000:
                self.lost_packets += gap

        self.last_packet_sequence = frame.packet_sequence
        self.packet_count += 1
        self.last_seen = time.time()
        self.source = frame.source
        self.last_flags = frame.flags
        self.coordinate_flags = frame.coordinate_flags
        sparse = frame.source == "esp32-diagnostic-s0"

        for sample in frame.samples:
            if self.first_tick is None:
                self.first_tick = sample.tick_32k
            relative_time = (sample.tick_32k - self.first_tick) / TICK_HZ
            accel, gyro, calculated, _ = self.estimator.update(sample, sparse)
            self.accel_history.append(
                [relative_time, accel[0], accel[1], accel[2]]
            )
            self.gyro_history.append(
                [relative_time, gyro[0], gyro[1], gyro[2]]
            )
            self.calculated_path.append(
                [calculated[0], calculated[1], calculated[2]]
            )
            self.last_sample_sequence = sample.sequence
            self.sample_count += 1

        if frame.coordinate_valid:
            self.received_path.append(
                [
                    frame.position_mm[0] / 1000.0,
                    frame.position_mm[1] / 1000.0,
                    frame.position_mm[2] / 1000.0,
                ]
            )

    def snapshot(self) -> dict[str, Any]:
        age = time.time() - self.last_seen if self.last_seen else None
        return {
            "device_id": self.device_id,
            "online": age is not None and age < 3.0,
            "age_seconds": age,
            "source": self.source,
            "packet_count": self.packet_count,
            "sample_count": self.sample_count,
            "lost_packets": self.lost_packets,
            "last_packet_sequence": self.last_packet_sequence,
            "last_sample_sequence": self.last_sample_sequence,
            "flags": self.last_flags,
            "coordinate_flags": self.coordinate_flags,
            "accel": list(self.accel_history),
            "gyro": list(self.gyro_history),
            "received_path": list(self.received_path),
            "calculated_path": list(self.calculated_path),
        }


class MotionStore:
    def __init__(self, device_count: int = 12) -> None:
        self._lock = threading.Lock()
        self._devices = {
            device_id: DeviceState(device_id)
            for device_id in range(1, device_count + 1)
        }

    def process(self, frame: MotionFrame) -> bool:
        if frame.device_id not in self._devices:
            return False
        with self._lock:
            self._devices[frame.device_id].process(frame)
        return True

    def snapshot(self, device_id: int) -> dict[str, Any]:
        with self._lock:
            selected = self._devices.get(device_id, self._devices[1])
            devices = [
                {
                    "device_id": state.device_id,
                    "online": (
                        state.last_seen > 0
                        and time.time() - state.last_seen < 3.0
                    ),
                    "packet_count": state.packet_count,
                }
                for state in self._devices.values()
            ]
            result = selected.snapshot()
            result["devices"] = devices
            return result
