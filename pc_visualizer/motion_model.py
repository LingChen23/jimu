"""Twelve-device histories, segment statistics and PC-side inertial tracking."""

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
SIGNAL_HISTORY = 1000
PATH_HISTORY = 3000

COORD_STATIONARY = 0x0001
COORD_MOVING = 0x0002
COORD_ORIGIN = 0x0004
COORD_STOP = 0x0008
COORD_CALIBRATING = 0x0040

STOP_HOLD_SECONDS = 0.8
PAUSE_MIN_SECONDS = 0.15
SHAKE_START_MPS2 = 1.5 * GRAVITY
SHAKE_END_MPS2 = 0.7 * GRAVITY
SHAKE_GYRO_DPS = 300.0
SHAKE_COOLDOWN_SECONDS = 0.45
FLIP_START_DEGREES = 120.0
FLIP_RESET_DEGREES = 60.0
FLIP_HOLD_SECONDS = 0.2
ROTATION_THRESHOLD_DEGREES = 360.0
ROTATION_RATE_DEADBAND_DPS = 20.0

# Same device-specific six-face calibration used by realtime_traj.c.
ACCEL_CALIBRATION_MATRIX = (
    (1.001725078, 0.0, 0.0),
    (0.0, 1.001929374, 0.0),
    (0.0, 0.0, 0.986569848),
)
ACCEL_CALIBRATION_OFFSET = (0.167142211, -0.035488054, 0.159343099)


def _norm(vector: list[float] | tuple[float, ...]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _normalize_quaternion(q: list[float]) -> None:
    length = _norm(q)
    if length <= 1e-9:
        q[:] = [1.0, 0.0, 0.0, 0.0]
        return
    for index in range(4):
        q[index] /= length


def _quaternion_from_gravity(accel: list[float]) -> list[float]:
    accel_norm = _norm(accel)
    if accel_norm < 1e-6:
        return [1.0, 0.0, 0.0, 0.0]
    ax, ay, az = (value / accel_norm for value in accel)
    if az < -0.999:
        return [0.0, 1.0, 0.0, 0.0]
    quaternion = [1.0 + az, ay, -ax, 0.0]
    _normalize_quaternion(quaternion)
    return quaternion


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


def _gravity_in_body(q: list[float]) -> list[float]:
    w, x, y, z = q
    return [
        GRAVITY * 2.0 * (x * z - w * y),
        GRAVITY * 2.0 * (w * x + y * z),
        GRAVITY * (w * w - x * x - y * y + z * z),
    ]


def _body_up_in_world(q: list[float]) -> list[float]:
    return _rotate_body_to_world(q, [0.0, 0.0, 1.0])


@dataclass(frozen=True)
class InertialEstimate:
    accel: list[float]
    gyro_dps: list[float]
    corrected_gyro_dps: list[float]
    linear_world: list[float]
    position: list[float]
    up_world: list[float]
    dt: float
    stationary: bool


@dataclass
class InertialEstimator:
    quaternion: list[float] = field(
        default_factory=lambda: [1.0, 0.0, 0.0, 0.0]
    )
    velocity: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    gyro_bias: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    accel_bias: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    last_tick: int | None = None
    initialized: bool = False
    stationary_samples: int = 0

    def reset_position(self) -> None:
        """Start a new movement at 0,0,0 while retaining attitude and biases."""
        self.position[:] = [0.0, 0.0, 0.0]
        self.velocity[:] = [0.0, 0.0, 0.0]

    @staticmethod
    def _calibrated_accel(sample: ImuSample) -> list[float]:
        raw_si = [
            value * GRAVITY / ACCEL_LSB_PER_G
            for value in sample.accel_raw
        ]
        centered = [
            raw_si[index] - ACCEL_CALIBRATION_OFFSET[index]
            for index in range(3)
        ]
        return [
            sum(
                ACCEL_CALIBRATION_MATRIX[row][column] * centered[column]
                for column in range(3)
            )
            for row in range(3)
        ]

    def update(
        self,
        sample: ImuSample,
        sparse_diagnostic: bool = False,
        stationary_hint: bool | None = None,
    ) -> InertialEstimate:
        accel = self._calibrated_accel(sample)
        gyro_dps = [value / GYRO_LSB_PER_DPS for value in sample.gyro_raw]
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

        accel_norm = _norm(accel)
        gyro_norm_dps = _norm(gyro_dps)
        detected_stationary = (
            abs(accel_norm - GRAVITY) < 0.8 and gyro_norm_dps < 8.0
        )
        stationary = (
            stationary_hint
            if stationary_hint is not None
            else detected_stationary
        )
        self.stationary_samples = (
            self.stationary_samples + 1 if stationary else 0
        )

        if not self.initialized:
            self.quaternion = _quaternion_from_gravity(accel)
            if stationary:
                self.gyro_bias[:] = gyro
            self.initialized = True

        angular_rate = [
            gyro[axis] - self.gyro_bias[axis] for axis in range(3)
        ]
        accel_for_attitude = [
            accel[axis] - self.accel_bias[axis] for axis in range(3)
        ]
        attitude_norm = _norm(accel_for_attitude)
        if attitude_norm > 1e-6:
            measured = [
                value / attitude_norm for value in accel_for_attitude
            ]
            w, x, y, z = self.quaternion
            predicted = [
                2 * (x * z - w * y),
                2 * (w * x + y * z),
                w * w - x * x - y * y + z * z,
            ]
            dot = max(
                -1.0,
                min(1.0, sum(predicted[i] * measured[i] for i in range(3))),
            )
            angle = math.acos(dot)
            norm_error = abs(attitude_norm - GRAVITY)
            gain = 0.0
            if stationary:
                gain = 2.5
            elif angle < math.radians(10.0) and norm_error < 0.35:
                gain = (
                    0.12
                    * (1.0 - angle / math.radians(10.0))
                    * (1.0 - norm_error / 0.35)
                )
            error = [
                predicted[1] * measured[2] - predicted[2] * measured[1],
                predicted[2] * measured[0] - predicted[0] * measured[2],
                predicted[0] * measured[1] - predicted[1] * measured[0],
            ]
            for axis in range(3):
                angular_rate[axis] -= gain * error[axis]

        w, x, y, z = self.quaternion
        gx, gy, gz = angular_rate
        self.quaternion[0] += 0.5 * (-x * gx - y * gy - z * gz) * dt
        self.quaternion[1] += 0.5 * (w * gx + y * gz - z * gy) * dt
        self.quaternion[2] += 0.5 * (w * gy - x * gz + z * gx) * dt
        self.quaternion[3] += 0.5 * (w * gz + x * gy - y * gx) * dt
        _normalize_quaternion(self.quaternion)

        if stationary:
            gravity_body = _gravity_in_body(self.quaternion)
            for axis in range(3):
                self.gyro_bias[axis] += 0.01 * (
                    gyro[axis] - self.gyro_bias[axis]
                )
                target_accel_bias = accel[axis] - gravity_body[axis]
                self.accel_bias[axis] += 0.0005 * (
                    target_accel_bias - self.accel_bias[axis]
                )

        accel_body = [
            accel[axis] - self.accel_bias[axis] for axis in range(3)
        ]
        linear_world = _rotate_body_to_world(self.quaternion, accel_body)
        linear_world[2] -= GRAVITY
        if stationary:
            self.velocity[:] = [0.0, 0.0, 0.0]
            linear_world = [0.0, 0.0, 0.0]
        else:
            for axis in range(3):
                self.position[axis] += (
                    self.velocity[axis] * dt
                    + 0.5 * linear_world[axis] * dt * dt
                )
                self.velocity[axis] += linear_world[axis] * dt

        return InertialEstimate(
            accel=accel,
            gyro_dps=gyro_dps,
            corrected_gyro_dps=[
                math.degrees(value) for value in angular_rate
            ],
            linear_world=linear_world,
            position=list(self.position),
            up_world=_body_up_in_world(self.quaternion),
            dt=dt,
            stationary=stationary,
        )


@dataclass
class MovementStats:
    movement_id: int
    start_tick: int
    start_up: list[float]
    moving_seconds: float = 0.0
    end_tick: int | None = None
    latest_tick: int | None = None
    accel_sum: float = 0.0
    accel_count: int = 0
    accel_min: float = math.inf
    accel_max: float = 0.0
    shake_count: int = 0
    flip_count: int = 0
    rotation_count: int = 0
    pause_count: int = 0
    pending_stop_tick: int | None = None
    shake_active: bool = False
    last_shake_tick: int | None = None
    flip_candidate_seconds: float = 0.0
    flip_armed: bool = True
    rotation_degrees: list[float] = field(
        default_factory=lambda: [0.0, 0.0, 0.0]
    )

    def add_sample(self, estimate: InertialEstimate, tick: int) -> None:
        self.latest_tick = tick
        linear_magnitude = _norm(estimate.linear_world)
        gyro_magnitude = _norm(estimate.corrected_gyro_dps)
        self.moving_seconds += estimate.dt
        self.accel_sum += linear_magnitude
        self.accel_count += 1
        self.accel_min = min(self.accel_min, linear_magnitude)
        self.accel_max = max(self.accel_max, linear_magnitude)

        cooldown_ready = (
            self.last_shake_tick is None
            or (tick - self.last_shake_tick) / TICK_HZ
            >= SHAKE_COOLDOWN_SECONDS
        )
        severe = (
            linear_magnitude >= SHAKE_START_MPS2
            or gyro_magnitude >= SHAKE_GYRO_DPS
        )
        if severe and not self.shake_active and cooldown_ready:
            self.shake_count += 1
            self.shake_active = True
            self.last_shake_tick = tick
        elif (
            linear_magnitude < SHAKE_END_MPS2
            and gyro_magnitude < SHAKE_GYRO_DPS * 0.5
        ):
            self.shake_active = False

        up_dot = max(
            -1.0,
            min(
                1.0,
                sum(
                    self.start_up[axis] * estimate.up_world[axis]
                    for axis in range(3)
                ),
            ),
        )
        up_angle = math.degrees(math.acos(up_dot))
        if self.flip_armed and up_angle >= FLIP_START_DEGREES:
            self.flip_candidate_seconds += estimate.dt
            if self.flip_candidate_seconds >= FLIP_HOLD_SECONDS:
                self.flip_count += 1
                self.flip_armed = False
                self.flip_candidate_seconds = 0.0
        elif up_angle <= FLIP_RESET_DEGREES:
            self.flip_armed = True
            self.flip_candidate_seconds = 0.0
        else:
            self.flip_candidate_seconds = 0.0

        for axis in range(3):
            rate = estimate.corrected_gyro_dps[axis]
            if abs(rate) < ROTATION_RATE_DEADBAND_DPS:
                continue
            self.rotation_degrees[axis] += rate * estimate.dt
            while abs(self.rotation_degrees[axis]) >= ROTATION_THRESHOLD_DEGREES:
                self.rotation_count += 1
                direction = 1.0 if self.rotation_degrees[axis] > 0 else -1.0
                self.rotation_degrees[axis] -= (
                    direction * ROTATION_THRESHOLD_DEGREES
                )

    def snapshot(self, state: str) -> dict[str, Any]:
        accel_mean = (
            self.accel_sum / self.accel_count if self.accel_count else 0.0
        )
        segment_seconds = (
            (
                (self.end_tick or self.latest_tick or self.start_tick)
                - self.start_tick
            )
            / TICK_HZ
        )
        return {
            "movement_id": self.movement_id,
            "state": state,
            "moving_seconds": self.moving_seconds,
            "segment_seconds": segment_seconds,
            "accel_mean": accel_mean,
            "accel_min": (
                self.accel_min if self.accel_count else 0.0
            ),
            "accel_max": self.accel_max,
            "shake_count": self.shake_count,
            "flip_count": self.flip_count,
            "rotation_count": self.rotation_count,
            "pause_count": self.pause_count,
            "sample_count": self.accel_count,
        }


@dataclass
class DeviceState:
    device_id: int
    estimator: InertialEstimator = field(default_factory=InertialEstimator)
    accel_history: deque = field(
        default_factory=lambda: deque(maxlen=SIGNAL_HISTORY)
    )
    gyro_history: deque = field(
        default_factory=lambda: deque(maxlen=SIGNAL_HISTORY)
    )
    received_path: deque = field(
        default_factory=lambda: deque(maxlen=PATH_HISTORY)
    )
    calculated_path: deque = field(
        default_factory=lambda: deque(maxlen=PATH_HISTORY)
    )
    completed_movements: deque = field(
        default_factory=lambda: deque(maxlen=20)
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
    current_movement: MovementStats | None = None
    next_movement_id: int = 1
    received_origin: list[float] = field(
        default_factory=lambda: [0.0, 0.0, 0.0]
    )
    motion_state: str = "等待静止"

    def _begin_movement(self, frame: MotionFrame) -> None:
        self.estimator.reset_position()
        self.received_path.clear()
        self.calculated_path.clear()
        self.received_path.append([0.0, 0.0, 0.0])
        self.calculated_path.append([0.0, 0.0, 0.0])
        if frame.coordinate_valid:
            self.received_origin = [
                value / 1000.0 for value in frame.position_mm
            ]
        else:
            self.received_origin = [0.0, 0.0, 0.0]
        start_up = _body_up_in_world(self.estimator.quaternion)
        self.current_movement = MovementStats(
            movement_id=self.next_movement_id,
            start_tick=frame.samples[0].tick_32k,
            start_up=start_up,
        )
        self.next_movement_id += 1
        self.motion_state = "移动中"

    def _finish_movement(self) -> None:
        movement = self.current_movement
        if movement is None:
            return
        movement.end_tick = movement.pending_stop_tick or movement.start_tick
        self.completed_movements.appendleft(
            movement.snapshot("已完成")
        )
        self.current_movement = None
        self.motion_state = "静止"

    def clear_analysis(self) -> None:
        self.estimator.reset_position()
        self.accel_history.clear()
        self.gyro_history.clear()
        self.received_path.clear()
        self.calculated_path.clear()
        self.completed_movements.clear()
        self.current_movement = None
        self.next_movement_id = 1
        self.received_origin = [0.0, 0.0, 0.0]
        self.first_tick = None
        self.motion_state = "等待移动"

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
        moving_now = bool(frame.coordinate_flags & COORD_MOVING)
        stationary_now = bool(frame.coordinate_flags & COORD_STATIONARY)
        calibrating_now = bool(frame.coordinate_flags & COORD_CALIBRATING)
        first_tick = frame.samples[0].tick_32k
        last_tick = frame.samples[-1].tick_32k

        if moving_now:
            if self.current_movement is None:
                self._begin_movement(frame)
            elif self.current_movement.pending_stop_tick is not None:
                pause_seconds = (
                    first_tick - self.current_movement.pending_stop_tick
                ) / TICK_HZ
                if pause_seconds >= PAUSE_MIN_SECONDS:
                    self.current_movement.pause_count += 1
                self.current_movement.pending_stop_tick = None
                self.motion_state = "移动中"
        elif stationary_now:
            if self.current_movement is not None:
                if self.current_movement.pending_stop_tick is None:
                    self.current_movement.pending_stop_tick = first_tick
                self.motion_state = "停止确认"
            else:
                self.motion_state = "静止"
        elif calibrating_now:
            self.motion_state = "校准中"
        elif self.current_movement is None:
            self.motion_state = "等待移动"

        stationary_hint: bool | None
        if stationary_now or calibrating_now:
            stationary_hint = True
        elif moving_now:
            stationary_hint = False
        else:
            stationary_hint = None

        for sample in frame.samples:
            if self.first_tick is None:
                self.first_tick = sample.tick_32k
            relative_time = (sample.tick_32k - self.first_tick) / TICK_HZ
            estimate = self.estimator.update(
                sample,
                sparse_diagnostic=sparse,
                stationary_hint=stationary_hint,
            )
            self.accel_history.append(
                [
                    relative_time,
                    estimate.accel[0],
                    estimate.accel[1],
                    estimate.accel[2],
                ]
            )
            self.gyro_history.append(
                [
                    relative_time,
                    estimate.gyro_dps[0],
                    estimate.gyro_dps[1],
                    estimate.gyro_dps[2],
                ]
            )
            if self.current_movement is not None and moving_now:
                self.current_movement.add_sample(
                    estimate, sample.tick_32k
                )
                self.calculated_path.append(estimate.position)
            self.last_sample_sequence = sample.sequence
            self.sample_count += 1

        if frame.coordinate_valid and self.current_movement is not None:
            point = [
                frame.position_mm[axis] / 1000.0
                - self.received_origin[axis]
                for axis in range(3)
            ]
            self.received_path.append(point)

        if self.current_movement is not None:
            self.current_movement.latest_tick = last_tick

        if (
            self.current_movement is not None
            and self.current_movement.pending_stop_tick is not None
            and (last_tick - self.current_movement.pending_stop_tick) / TICK_HZ
            >= STOP_HOLD_SECONDS
        ):
            self._finish_movement()

    def snapshot(self) -> dict[str, Any]:
        age = time.time() - self.last_seen if self.last_seen else None
        current = (
            self.current_movement.snapshot(self.motion_state)
            if self.current_movement is not None
            else None
        )
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
            "motion_state": self.motion_state,
            "current_movement": current,
            "completed_movements": list(self.completed_movements),
            "completed_movement_count": len(self.completed_movements),
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

    def reset_analysis(self, device_id: int) -> bool:
        with self._lock:
            state = self._devices.get(device_id)
            if state is None:
                return False
            state.clear_analysis()
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
