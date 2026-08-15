"""Twelve-device histories, dual motion segmentation and PC inertial tracking.

Two analysis pipelines run in parallel for every device:

* ``device`` keeps the original behaviour and uses the delayed coordinate flags
  sent by the transmitter as the motion-state authority.
* ``pc`` treats transmitter state as reference only.  It detects calibration,
  movement and stationary periods causally from the current accelerometer and
  gyroscope samples, so current IMU samples are never labelled with a state that
  belongs to a coordinate sample roughly 300 ms in the past.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import io
import json
import math
import queue
import statistics
import threading
import time
from typing import Any
import numpy as np

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
MAX_MOVEMENT_SAMPLES = 30000
LIVE_SPEED_QUANTILE_SAMPLES = 512
RECENT_MOVEMENT_DETAIL_LIMIT = 2
RECENT_MOVEMENT_DETAIL_SAMPLE_BUDGET = 12000
FINALIZATION_WORKERS = 2
UINT32_MASK = 0xFFFFFFFF
UINT16_MASK = 0xFFFF
MAX_CONTINUOUS_SAMPLE_GAP_SECONDS = 0.25
MAX_HOST_ARRIVAL_GAP_SECONDS = 1.0

ANALYSIS_MODE_DEVICE = "device"
ANALYSIS_MODE_PC = "pc"
ANALYSIS_MODES = (ANALYSIS_MODE_DEVICE, ANALYSIS_MODE_PC)
ANALYSIS_MODE_LABELS = {
    ANALYSIS_MODE_DEVICE: "设备状态驱动（原方案）",
    ANALYSIS_MODE_PC: "PC 独立判定（推荐）",
}

COORD_STATIONARY = 0x0001
COORD_MOVING = 0x0002
COORD_ORIGIN = 0x0004
COORD_STOP = 0x0008
COORD_CALIBRATING = 0x0040

PAUSE_MIN_SECONDS = 0.8
MOVEMENT_END_HOLD_SECONDS = 3.0
# Severe shake is counted as a continuous back-and-forth episode, not as each
# threshold-crossing peak.  Acceleration energy is evaluated over a rolling
# window while direction reversals reject isolated impacts and pure rotations.
SHAKE_WINDOW_SECONDS = 0.50
SHAKE_SMOOTHING_SECONDS = 0.08
SHAKE_START_RMS_MPS2 = 0.8 * GRAVITY
SHAKE_START_PEAK_MPS2 = 1.5 * GRAVITY
SHAKE_END_MPS2 = 0.45 * GRAVITY
SHAKE_DIRECTION_DEADBAND_MPS2 = 0.25 * GRAVITY
SHAKE_MIN_DIRECTION_REVERSALS = 2
SHAKE_CONFIRM_SECONDS = 0.25
SHAKE_MERGE_GAP_SECONDS = 1.20
# A physical full turn often integrates below 360 degrees because the first and
# last slow samples fall under the rate gate. 330 degrees keeps a modest sensor
# tolerance without allowing ordinary movement to become a completed rotation.
ROTATION_THRESHOLD_DEGREES = 330.0
ROTATION_START_RATE_DPS = 10.0
ROTATION_CONTINUE_RATE_DPS = 5.0
ROTATION_AXIS_ALIGNMENT = 0.65
ROTATION_GAP_RESET_SECONDS = 0.35
ROTATION_AXIS_TRACK_RATE = 0.05
ROTATION_AXIS_SWITCH_CONFIRM_SECONDS = 0.12

# Spatial rotation is the direction-independent angular path travelled during a
# movement. Small angular-rate noise cannot start a segment, and segments below
# 20 degrees are discarded instead of becoming long-term accumulated drift.
SPATIAL_ROTATION_START_RATE_DPS = 10.0
SPATIAL_ROTATION_CONTINUE_RATE_DPS = 5.0
SPATIAL_ROTATION_GAP_RESET_SECONDS = 0.35
SPATIAL_ROTATION_MIN_SEGMENT_DEGREES = 20.0
SPATIAL_ROTATION_TURN_DEGREES = 360.0

# A flip is a persistent transition of the device Z axis to the opposite
# hemisphere.  The reference is updated after every accepted flip, so flipping
# back counts as another event while a pure yaw rotation does not.
FLIP_OPPOSITE_DOT = math.cos(math.radians(150.0))
FLIP_CONFIRM_SECONDS = 0.10

# Drop: a confirmed low-g interval followed by a strong impact.  These defaults
# are deliberately conservative and are exposed in movement detail metadata.
DROP_FREEFALL_G = 0.35
DROP_FREEFALL_CONFIRM_SECONDS = 0.08
DROP_IMPACT_G = 2.5
DROP_IMPACT_WINDOW_SECONDS = 0.80

# Tremor analysis. Each corrected angular-rate axis is resampled uniformly and
# high-pass filtered to remove gross movement. A Hann-window FFT is calculated
# per axis and the three spectra are combined vectorially, preserving frequency.
TREMOR_HIGHPASS_HZ = 2.0
TREMOR_BAND_LOW_HZ = 3.0
TREMOR_BAND_HIGH_HZ = 20.0
TREMOR_MIN_SECONDS = 0.75
TREMOR_MIN_SAMPLES = 64

DEFAULT_MINIMUM_MOVEMENT_SECONDS = 0.30
MAXIMUM_MOVEMENT_FILTER_SECONDS = 60.0

# P05 speed is calculated from the movement core instead of the full segment.
# Trimming the first/last 10% (capped at 100 ms per side) excludes the deliberate
# zero-speed start/end constraints without deleting genuine low-speed samples in
# the middle of a movement.
SPEED_CORE_TRIM_FRACTION = 0.10
SPEED_CORE_TRIM_MAX_SECONDS = 0.10
SPEED_PERCENTILE = 5.0

# Suppress only the smallest residual linear-acceleration vector before double
# integration.  This reduces visible drift but does not make IMU-only position
# a trustworthy metric distance; the UI therefore defaults to relative shape.
LINEAR_ACCEL_SOFT_DEADBAND_MPS2 = 0.06

# Movement-end trajectory correction, statistics and FFT must never execute in
# the serial reader.  Two workers are enough to absorb simultaneous device/PC
# pipeline completions without letting analysis compete excessively with live
# ingestion on a multi-device session.
_FINALIZATION_EXECUTOR = ThreadPoolExecutor(
    max_workers=FINALIZATION_WORKERS,
    thread_name_prefix="motion-finalize",
)

# PC-only causal motion detector.  Start confirmation is intentionally short;
# stop confirmation is longer because false stops damage inertial integration
# much more than a 10 ms start delay.
PC_CALIBRATION_SAMPLES = 100
PC_START_CONFIRM_SAMPLES = 2
PC_STOP_CONFIRM_SAMPLES = 20
PC_ZUPT_CONFIRM_SAMPLES = 3
PC_START_ACCEL_DELTA_MPS2 = 0.45
PC_START_ACCEL_NORM_DELTA_MPS2 = 0.65
PC_START_GYRO_DPS = 8.0
PC_STOP_ACCEL_NORM_DELTA_MPS2 = 0.80
PC_STOP_GYRO_DPS = 6.0
PC_CALIBRATION_ACCEL_TOLERANCE_MPS2 = 0.60
PC_CALIBRATION_GYRO_DPS = math.degrees(0.10)
PC_BASELINE_RATE = 0.02

# Same device-specific six-face calibration used by realtime_traj.c.
ACCEL_CALIBRATION_MATRIX = (
    (1.001725078, 0.0, 0.0),
    (0.0, 1.001929374, 0.0),
    (0.0, 0.0, 0.986569848),
)
ACCEL_CALIBRATION_OFFSET = (0.167142211, -0.035488054, 0.159343099)


def _norm(vector: list[float] | tuple[float, ...]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _tick_delta(current: int, previous: int) -> int:
    return (current - previous) & UINT32_MASK


def _tick_seconds(current: int, previous: int) -> float:
    return _tick_delta(current, previous) / TICK_HZ


def _sequence_at_or_after(value: int, reference: int) -> bool:
    return ((value - reference) & UINT32_MASK) < 0x80000000




BEIJING_ZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _iso_times(epoch: float | None) -> dict[str, str | None]:
    if epoch is None:
        return {"computer": None, "beijing": None}
    # Convert from an aware UTC timestamp first. On Windows, the naive
    # fromtimestamp() path can reject otherwise valid timestamps near 1970
    # while resolving the local timezone.
    local = datetime.fromtimestamp(epoch, timezone.utc).astimezone()
    beijing = datetime.fromtimestamp(epoch, BEIJING_ZONE)
    return {
        "computer": local.isoformat(timespec="milliseconds"),
        "beijing": beijing.isoformat(timespec="milliseconds"),
    }


def _sample_epoch(frame_epoch: float, sample_tick: int, last_tick: int) -> float:
    return frame_epoch - _tick_seconds(last_tick, sample_tick)


def _safe_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "median": 0.0, "std": 0.0}
    return {
        "min": float(min(values)),
        "max": float(max(values)),
        "mean": float(statistics.fmean(values)),
        "median": float(statistics.median(values)),
        "std": float(statistics.pstdev(values)) if len(values) > 1 else 0.0,
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=float), percentile))


def _movement_core_speeds(records: list[dict[str, Any]]) -> list[float]:
    """Return speeds from the movement core for robust low-speed statistics.

    The segment starts from a deliberate zero velocity and rest-to-rest
    correction deliberately drives the final velocity back to zero.  Those
    boundary constraints are useful for integration but must not define the
    reported low-speed statistic.
    """
    if not records:
        return []
    duration = sum(max(0.0, float(row.get("dt_s", 0.0))) for row in records)
    trim = min(
        SPEED_CORE_TRIM_MAX_SECONDS,
        max(0.0, duration * SPEED_CORE_TRIM_FRACTION),
    )
    lower = trim
    upper = max(lower, duration - trim)
    core = []
    for row in records:
        midpoint = float(row.get("elapsed_s", 0.0)) + 0.5 * float(row.get("dt_s", 0.0))
        if midpoint < lower or midpoint > upper:
            continue
        if bool(row.get("stationary", False)):
            continue
        core.append(float(row.get("speed_mps", 0.0)))
    if core:
        return core
    # Very short or sparse valid segments: drop one sample at each boundary
    # when possible, while still retaining true zero-speed motion-only segments.
    nonstationary = [
        float(row.get("speed_mps", 0.0))
        for row in records
        if not bool(row.get("stationary", False))
    ]
    return nonstationary[1:-1] if len(nonstationary) > 2 else nonstationary


def _speed_statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    all_speeds = [float(row.get("speed_mps", 0.0)) for row in records]
    result: dict[str, Any] = _safe_stats(all_speeds)
    core_speeds = _movement_core_speeds(records)
    result.update({
        "p05": _percentile(core_speeds, SPEED_PERCENTILE),
        "p05_percentile": SPEED_PERCENTILE,
        "p05_sample_count": len(core_speeds),
        "p05_definition": "运动核心区间速度的5%分位数；排除首尾边界与静止样本",
    })
    return result


def _axis_statistics(rows: list[list[float]], labels: tuple[str, str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for axis, label in enumerate(labels):
        result[label] = _safe_stats([row[axis] for row in rows])
    result["norm"] = _safe_stats([_norm(row) for row in rows])
    return result


def _first_order_highpass(values: np.ndarray, sample_rate: float, cutoff_hz: float) -> np.ndarray:
    if values.size == 0:
        return values.copy()
    dt = 1.0 / sample_rate
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    alpha = rc / (rc + dt)
    output = np.zeros_like(values, dtype=float)
    for index in range(1, values.size):
        output[index] = alpha * (output[index - 1] + values[index] - values[index - 1])
    return output


def _tremor_fft(records: list[dict[str, Any]]) -> dict[str, Any]:
    unavailable = {
        "valid": False,
        "dominant_frequency_hz": None,
        "intensity_dps": None,
        "band_rms_dps": None,
        "sample_rate_hz": None,
        "frequency_resolution_hz": None,
        "reason": "有效移动数据不足",
    }
    if len(records) < TREMOR_MIN_SAMPLES:
        return unavailable
    times = np.asarray([float(row["elapsed_s"]) for row in records], dtype=float)
    gyros = np.asarray([row["gyro_corrected_dps"] for row in records], dtype=float)
    duration = float(times[-1] - times[0]) if times.size > 1 else 0.0
    if duration < TREMOR_MIN_SECONDS:
        unavailable["reason"] = "移动时间过短，FFT频率分辨率不足"
        return unavailable
    deltas = np.diff(times)
    deltas = deltas[(deltas > 0.0005) & (deltas < 0.2)]
    if deltas.size == 0:
        unavailable["reason"] = "采样时间戳无效"
        return unavailable
    sample_rate = float(1.0 / np.median(deltas))
    if sample_rate < 2.0 * TREMOR_BAND_LOW_HZ:
        unavailable["reason"] = "采样率不足"
        return unavailable
    uniform_times = np.arange(times[0], times[-1] + 0.5 / sample_rate, 1.0 / sample_rate)
    if uniform_times.size < TREMOR_MIN_SAMPLES:
        return unavailable
    uniform_axes = np.column_stack([
        np.interp(uniform_times, times, gyros[:, axis]) for axis in range(3)
    ])
    filtered_axes = np.column_stack([
        _first_order_highpass(
            uniform_axes[:, axis], sample_rate, TREMOR_HIGHPASS_HZ
        )
        for axis in range(3)
    ])
    filtered_axes -= np.mean(filtered_axes, axis=0, keepdims=True)
    window = np.hanning(filtered_axes.shape[0])
    window_sum = float(np.sum(window))
    if window_sum <= 1e-9:
        return unavailable
    nfft = 1 << max(1, int(math.ceil(math.log2(filtered_axes.shape[0] * 4))))
    spectra = np.fft.rfft(filtered_axes * window[:, None], n=nfft, axis=0)
    frequencies = np.fft.rfftfreq(nfft, d=1.0 / sample_rate)
    axis_amplitudes = 2.0 * np.abs(spectra) / window_sum
    amplitudes = np.sqrt(np.sum(np.square(axis_amplitudes), axis=1))
    high = min(TREMOR_BAND_HIGH_HZ, sample_rate * 0.45)
    band = (frequencies >= TREMOR_BAND_LOW_HZ) & (frequencies <= high)
    if not np.any(band):
        unavailable["reason"] = "FFT分析频带为空"
        return unavailable
    band_indices = np.flatnonzero(band)
    peak_index = int(band_indices[int(np.argmax(amplitudes[band]))])
    # Stable band-limited RMS from an unwindowed FFT/iFFT.  This does not depend
    # on zero-padding density, unlike summing displayed spectrum bins.
    band_spectra = np.fft.rfft(filtered_axes, axis=0)
    band_frequencies = np.fft.rfftfreq(filtered_axes.shape[0], d=1.0 / sample_rate)
    band_mask = (
        (band_frequencies >= TREMOR_BAND_LOW_HZ)
        & (band_frequencies <= high)
    )
    band_spectra[~band_mask, :] = 0.0
    band_signal = np.fft.irfft(
        band_spectra, n=filtered_axes.shape[0], axis=0
    )
    band_rms = float(
        math.sqrt(float(np.mean(np.sum(np.square(band_signal), axis=1))))
    )
    return {
        "valid": True,
        "dominant_frequency_hz": float(frequencies[peak_index]),
        "intensity_dps": float(amplitudes[peak_index]),
        "band_rms_dps": band_rms,
        "sample_rate_hz": sample_rate,
        "frequency_resolution_hz": float(1.0 / duration),
        "fft_bin_spacing_hz": float(sample_rate / nfft),
        "reason": None,
        "method": {
            "signal": "三轴校正角速度分别高通后进行频谱矢量合成",
            "highpass_hz": TREMOR_HIGHPASS_HZ,
            "band_hz": [TREMOR_BAND_LOW_HZ, high],
            "window": "Hann",
            "fft_points": nfft,
            "intensity_unit": "°/s peak amplitude",
        },
    }


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


def _quaternion_to_euler_degrees(q: list[float]) -> list[float]:
    """Return intrinsic XYZ roll/pitch/yaw in degrees.

    The estimator has no magnetometer, so yaw is always relative and may drift.
    """
    w, x, y, z = q
    roll = math.atan2(
        2.0 * (w * x + y * z),
        1.0 - 2.0 * (x * x + y * y),
    )
    pitch_term = max(-1.0, min(1.0, 2.0 * (w * y - z * x)))
    pitch = math.asin(pitch_term)
    yaw = math.atan2(
        2.0 * (w * z + x * y),
        1.0 - 2.0 * (y * y + z * z),
    )
    return [math.degrees(roll), math.degrees(pitch), math.degrees(yaw)]


def _device_state_label(flags: int) -> str:
    if flags & COORD_MOVING:
        return "移动中"
    if flags & COORD_STATIONARY:
        return "静止"
    if flags & COORD_CALIBRATING:
        return "校准中"
    return "等待状态"


@dataclass(frozen=True)
class InertialEstimate:
    accel: list[float]
    gyro_dps: list[float]
    corrected_gyro_dps: list[float]
    linear_world: list[float]
    velocity: list[float]
    position: list[float]
    up_world: list[float]
    dt: float
    stationary: bool
    gyro_world_dps: list[float] | None = None


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
    def calibrated_accel(sample: ImuSample) -> list[float]:
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

    @staticmethod
    def measurements(sample: ImuSample) -> tuple[list[float], list[float]]:
        return (
            InertialEstimator.calibrated_accel(sample),
            [value / GYRO_LSB_PER_DPS for value in sample.gyro_raw],
        )

    def update(
        self,
        sample: ImuSample,
        sparse_diagnostic: bool = False,
        stationary_hint: bool | None = None,
        measurements: tuple[list[float], list[float]] | None = None,
    ) -> InertialEstimate:
        if measurements is None:
            accel, gyro_dps = self.measurements(sample)
        else:
            accel = list(measurements[0])
            gyro_dps = list(measurements[1])
        gyro = [math.radians(value) for value in gyro_dps]

        if self.last_tick is None:
            dt = 0.01
        else:
            dt = _tick_seconds(sample.tick_32k, self.last_tick)
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
            measured = [value / attitude_norm for value in accel_for_attitude]
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
        linear_magnitude = _norm(linear_world)
        if (
            not stationary
            and 0.0 < linear_magnitude < LINEAR_ACCEL_SOFT_DEADBAND_MPS2
        ):
            linear_world = [0.0, 0.0, 0.0]
        elif not stationary and linear_magnitude > 0.0:
            retained = max(
                0.0,
                linear_magnitude - LINEAR_ACCEL_SOFT_DEADBAND_MPS2,
            ) / linear_magnitude
            linear_world = [value * retained for value in linear_world]
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

        corrected_gyro_dps = [math.degrees(value) for value in angular_rate]
        return InertialEstimate(
            accel=accel,
            gyro_dps=gyro_dps,
            corrected_gyro_dps=corrected_gyro_dps,
            linear_world=linear_world,
            velocity=list(self.velocity),
            position=list(self.position),
            up_world=_body_up_in_world(self.quaternion),
            dt=dt,
            stationary=stationary,
            gyro_world_dps=_rotate_body_to_world(
                self.quaternion, corrected_gyro_dps
            ),
        )


@dataclass(frozen=True)
class MotionDecision:
    state: str
    stationary_hint: bool
    started: bool = False
    stopped: bool = False
    motion_candidate: bool = False
    quiet_candidate: bool = False
    transition_tick: int | None = None
    transition_sequence: int | None = None


@dataclass
class PcMotionDetector:
    """Causal motion-state detector using only current PC-side IMU data."""

    state: str = "calibrating"
    calibration_count: int = 0
    motion_count: int = 0
    quiet_count: int = 0
    baseline_accel: list[float] | None = None
    motion_candidate_tick: int | None = None
    motion_candidate_sequence: int | None = None
    quiet_candidate_tick: int | None = None
    quiet_candidate_sequence: int | None = None

    @property
    def calibrated(self) -> bool:
        return self.state != "calibrating"

    def reset_analysis(self) -> None:
        self.motion_count = 0
        self.quiet_count = 0
        self.motion_candidate_tick = None
        self.motion_candidate_sequence = None
        self.quiet_candidate_tick = None
        self.quiet_candidate_sequence = None
        if self.state != "calibrating":
            self.state = "stationary"

    def _update_baseline(self, accel: list[float], rate: float) -> None:
        if self.baseline_accel is None:
            self.baseline_accel = list(accel)
            return
        for axis in range(3):
            self.baseline_accel[axis] += rate * (
                accel[axis] - self.baseline_accel[axis]
            )

    def update(
        self,
        accel: list[float],
        gyro_dps: list[float],
        tick: int,
        sequence: int,
    ) -> MotionDecision:
        accel_norm = _norm(accel)
        gyro_norm = _norm(gyro_dps)

        if self.state == "calibrating":
            stable = (
                abs(accel_norm - GRAVITY)
                < PC_CALIBRATION_ACCEL_TOLERANCE_MPS2
                and gyro_norm < PC_CALIBRATION_GYRO_DPS
            )
            if not stable:
                self.calibration_count = 0
                self.baseline_accel = None
                return MotionDecision("calibrating", stationary_hint=False)

            self.calibration_count += 1
            self._update_baseline(accel, 1.0 / self.calibration_count)
            if self.calibration_count >= PC_CALIBRATION_SAMPLES:
                self.state = "stationary"
                self.motion_count = 0
                self.quiet_count = 0
                return MotionDecision("stationary", stationary_hint=True)
            return MotionDecision("calibrating", stationary_hint=True)

        baseline_delta = (
            _norm([
                accel[axis] - self.baseline_accel[axis]
                for axis in range(3)
            ])
            if self.baseline_accel is not None
            else 0.0
        )
        motion_evidence = (
            baseline_delta >= PC_START_ACCEL_DELTA_MPS2
            or abs(accel_norm - GRAVITY)
            >= PC_START_ACCEL_NORM_DELTA_MPS2
            or gyro_norm >= PC_START_GYRO_DPS
        )
        quiet_evidence = (
            abs(accel_norm - GRAVITY)
            < PC_STOP_ACCEL_NORM_DELTA_MPS2
            and gyro_norm < PC_STOP_GYRO_DPS
        )

        if self.state == "stationary":
            self.quiet_count = 0
            self.quiet_candidate_tick = None
            self.quiet_candidate_sequence = None
            if motion_evidence:
                if self.motion_count == 0:
                    self.motion_candidate_tick = tick
                    self.motion_candidate_sequence = sequence
                self.motion_count += 1
                if self.motion_count >= PC_START_CONFIRM_SAMPLES:
                    transition_tick = self.motion_candidate_tick
                    transition_sequence = self.motion_candidate_sequence
                    self.state = "moving"
                    self.motion_count = 0
                    self.motion_candidate_tick = None
                    self.motion_candidate_sequence = None
                    return MotionDecision(
                        "moving",
                        stationary_hint=False,
                        started=True,
                        motion_candidate=True,
                        transition_tick=transition_tick,
                        transition_sequence=transition_sequence,
                    )
                return MotionDecision(
                    "stationary",
                    stationary_hint=False,
                    motion_candidate=True,
                    transition_tick=self.motion_candidate_tick,
                    transition_sequence=self.motion_candidate_sequence,
                )

            self.motion_count = 0
            self.motion_candidate_tick = None
            self.motion_candidate_sequence = None
            self._update_baseline(accel, PC_BASELINE_RATE)
            return MotionDecision("stationary", stationary_hint=True)

        # Moving: a stop is accepted only after a causal 200 ms quiet run.  The
        # transition timestamp is the first quiet sample, not the confirming
        # sample, so movement segmentation remains aligned with the IMU event.
        self.motion_count = 0
        self.motion_candidate_tick = None
        self.motion_candidate_sequence = None
        if quiet_evidence:
            if self.quiet_count == 0:
                self.quiet_candidate_tick = tick
                self.quiet_candidate_sequence = sequence
            self.quiet_count += 1
            stationary_hint = self.quiet_count >= PC_ZUPT_CONFIRM_SAMPLES
            if self.quiet_count >= PC_STOP_CONFIRM_SAMPLES:
                transition_tick = self.quiet_candidate_tick
                transition_sequence = self.quiet_candidate_sequence
                self.state = "stationary"
                self.quiet_count = 0
                self.quiet_candidate_tick = None
                self.quiet_candidate_sequence = None
                self.baseline_accel = list(accel)
                return MotionDecision(
                    "stationary",
                    stationary_hint=True,
                    stopped=True,
                    quiet_candidate=True,
                    transition_tick=transition_tick,
                    transition_sequence=transition_sequence,
                )
            return MotionDecision(
                "moving",
                stationary_hint=stationary_hint,
                quiet_candidate=True,
                transition_tick=self.quiet_candidate_tick,
                transition_sequence=self.quiet_candidate_sequence,
            )

        self.quiet_count = 0
        self.quiet_candidate_tick = None
        self.quiet_candidate_sequence = None
        return MotionDecision("moving", stationary_hint=False)


@dataclass
class MovementStats:
    movement_id: int
    start_tick: int
    start_up: list[float]
    block_id: str = ""
    mapping_version: str = ""
    start_sequence: int = 0
    start_epoch: float | None = None
    moving_seconds: float = 0.0
    end_tick: int | None = None
    end_sequence: int | None = None
    end_epoch: float | None = None
    latest_tick: int | None = None
    latest_epoch: float | None = None
    accel_sum: float = 0.0
    accel_count: int = 0
    accel_min: float = math.inf
    accel_max: float = 0.0
    shake_count: int = 0
    rotation_count: int = 0
    flip_count: int = 0
    pause_count: int = 0
    drop_count: int = 0
    pending_stop_tick: int | None = None
    pending_stop_sequence: int | None = None
    pending_stop_epoch: float | None = None
    shake_active: bool = False
    shake_candidate_seconds: float = 0.0
    shake_candidate_start_s: float | None = None
    shake_quiet_seconds: float = 0.0
    shake_last_energy_elapsed_s: float | None = None
    shake_last_energy_epoch: float | None = None
    shake_smoothed_linear: list[float] = field(default_factory=list, repr=False)
    shake_window: deque[tuple[float, float, list[float], float, float]] = field(
        default_factory=deque,
        repr=False,
    )
    rotation_degrees_total: float = 0.0
    rotation_progress_degrees: float = 0.0
    rotation_axis: list[float] | None = None
    rotation_gap_seconds: float = 0.0
    rotation_candidate_axis: list[float] | None = None
    rotation_candidate_degrees: float = 0.0
    rotation_candidate_seconds: float = 0.0
    spatial_rotation_degrees_total: float = 0.0
    spatial_rotation_segment_degrees: float = 0.0
    spatial_rotation_active: bool = False
    spatial_rotation_gap_seconds: float = 0.0
    spatial_rotation_previous_rate_dps: float | None = None
    flip_reference_up: list[float] = field(default_factory=list)
    flip_candidate_seconds: float = 0.0
    pause_events: list[dict[str, Any]] = field(default_factory=list)
    shake_events: list[dict[str, Any]] = field(default_factory=list)
    rotation_events: list[dict[str, Any]] = field(default_factory=list)
    flip_events: list[dict[str, Any]] = field(default_factory=list)
    drop_freefall_candidate_seconds: float = 0.0
    drop_freefall_start_s: float | None = None
    drop_armed: bool = False
    drop_post_freefall_seconds: float = 0.0
    drop_events: list[dict[str, Any]] = field(default_factory=list)
    integration_samples: list[tuple[float, list[float]]] = field(
        default_factory=list,
        repr=False,
    )
    sample_records: list[dict[str, Any]] = field(default_factory=list, repr=False)
    device_coordinate_records: list[dict[str, Any]] = field(default_factory=list, repr=False)
    calculated_path_final: list[list[float]] = field(default_factory=list, repr=False)
    received_path_final: list[list[float]] = field(default_factory=list, repr=False)
    position_corrected: bool = False
    samples_truncated: bool = False
    finalized: bool = False
    prepared_for_finalize: bool = False
    speed_statistics: dict[str, Any] = field(default_factory=dict)
    accel_statistics: dict[str, Any] = field(default_factory=dict)
    tremor: dict[str, Any] = field(default_factory=dict)
    live_speed_sum: float = 0.0
    live_speed_square_sum: float = 0.0
    live_speed_min: float = math.inf
    live_speed_max: float = 0.0
    live_speed_count: int = 0
    live_speed_sample_stride: int = 1
    live_speed_samples: list[float] = field(default_factory=list, repr=False)
    high_frequency_released: bool = False

    def __post_init__(self) -> None:
        self.flip_reference_up = list(self.start_up)

    def _shake_window_metrics(self) -> dict[str, float | int]:
        if not self.shake_window:
            return {
                "accel_rms": 0.0,
                "accel_peak": 0.0,
                "gyro_rms": 0.0,
                "gyro_peak": 0.0,
                "direction_reversals": 0,
            }
        total_dt = sum(max(sample[1], 0.0) for sample in self.shake_window)
        if total_dt <= 1e-9:
            total_dt = float(len(self.shake_window))
            weights = [1.0] * len(self.shake_window)
        else:
            weights = [max(sample[1], 0.0) for sample in self.shake_window]
        accel_rms = math.sqrt(sum(
            sample[3] * sample[3] * weight
            for sample, weight in zip(self.shake_window, weights)
        ) / total_dt)
        gyro_rms = math.sqrt(sum(
            sample[4] * sample[4] * weight
            for sample, weight in zip(self.shake_window, weights)
        ) / total_dt)
        axis_energy = [
            sum(sample[2][axis] * sample[2][axis] for sample in self.shake_window)
            for axis in range(3)
        ]
        dominant_axis = max(range(3), key=axis_energy.__getitem__)
        previous_sign = 0
        reversals = 0
        for sample in self.shake_window:
            component = sample[2][dominant_axis]
            sign = (
                1 if component >= SHAKE_DIRECTION_DEADBAND_MPS2
                else -1 if component <= -SHAKE_DIRECTION_DEADBAND_MPS2
                else 0
            )
            if sign and previous_sign and sign != previous_sign:
                reversals += 1
            if sign:
                previous_sign = sign
        return {
            "accel_rms": accel_rms,
            "accel_peak": max(sample[3] for sample in self.shake_window),
            "gyro_rms": gyro_rms,
            "gyro_peak": max(sample[4] for sample in self.shake_window),
            "direction_reversals": reversals,
        }

    def _start_shake_episode(
        self,
        elapsed_s: float,
        sequence: int,
        wall_epoch: float | None,
        metrics: dict[str, float | int],
    ) -> None:
        start_elapsed_s = (
            self.shake_candidate_start_s
            if self.shake_candidate_start_s is not None
            else elapsed_s
        )
        start_epoch = (
            wall_epoch - max(0.0, elapsed_s - start_elapsed_s)
            if wall_epoch is not None
            else None
        )
        self.shake_count += 1
        self.shake_active = True
        self.shake_quiet_seconds = 0.0
        self.shake_last_energy_elapsed_s = elapsed_s
        self.shake_last_energy_epoch = wall_epoch
        self.shake_events.append({
            "shake_index": self.shake_count,
            "elapsed_s": float(start_elapsed_s),
            "end_elapsed_s": None,
            "duration_seconds": 0.0,
            "sequence": int(sequence),
            "dynamic_acceleration_mps2": float(metrics["accel_peak"]),
            "dynamic_acceleration_rms_mps2": float(metrics["accel_rms"]),
            "angular_speed_dps": float(metrics["gyro_peak"]),
            "angular_speed_rms_dps": float(metrics["gyro_rms"]),
            "direction_reversals": int(metrics["direction_reversals"]),
            "time": _iso_times(start_epoch),
            "end_time": _iso_times(None),
        })

    def _finish_shake_episode(self) -> None:
        if not self.shake_active or not self.shake_events:
            return
        event = self.shake_events[-1]
        end_elapsed_s = (
            self.shake_last_energy_elapsed_s
            if self.shake_last_energy_elapsed_s is not None
            else self.moving_seconds
        )
        event["end_elapsed_s"] = float(end_elapsed_s)
        event["duration_seconds"] = max(
            0.0,
            float(end_elapsed_s) - float(event["elapsed_s"]),
        )
        event["end_time"] = _iso_times(self.shake_last_energy_epoch)
        self.shake_active = False
        self.shake_candidate_seconds = 0.0
        self.shake_candidate_start_s = None
        self.shake_quiet_seconds = 0.0
        self.shake_last_energy_elapsed_s = None
        self.shake_last_energy_epoch = None

    def _update_shake(
        self,
        linear_world: list[float],
        linear_magnitude: float,
        gyro_magnitude: float,
        dt: float,
        elapsed_s: float,
        sequence: int,
        wall_epoch: float | None,
    ) -> None:
        dt = max(0.0, float(dt))
        if not self.shake_smoothed_linear:
            self.shake_smoothed_linear = list(linear_world)
        else:
            alpha = min(1.0, dt / (SHAKE_SMOOTHING_SECONDS + dt)) if dt else 0.0
            self.shake_smoothed_linear = [
                previous + alpha * (current - previous)
                for previous, current in zip(
                    self.shake_smoothed_linear,
                    linear_world,
                )
            ]
        self.shake_window.append((
            float(elapsed_s),
            dt,
            list(self.shake_smoothed_linear),
            float(linear_magnitude),
            float(gyro_magnitude),
        ))
        while (
            self.shake_window
            and elapsed_s - self.shake_window[0][0] > SHAKE_WINDOW_SECONDS
        ):
            self.shake_window.popleft()

        metrics = self._shake_window_metrics()
        qualifies = (
            float(metrics["accel_rms"]) >= SHAKE_START_RMS_MPS2
            and float(metrics["accel_peak"]) >= SHAKE_START_PEAK_MPS2
            and int(metrics["direction_reversals"])
            >= SHAKE_MIN_DIRECTION_REVERSALS
        )
        # Ending/merging is measured from the most recent energetic sample,
        # rather than after the rolling RMS window has drained. This makes the
        # configured gap correspond to the physical pause between two bouts.
        energetic = linear_magnitude >= SHAKE_END_MPS2

        if self.shake_active:
            event = self.shake_events[-1]
            event["dynamic_acceleration_mps2"] = max(
                float(event["dynamic_acceleration_mps2"]),
                float(metrics["accel_peak"]),
            )
            event["dynamic_acceleration_rms_mps2"] = max(
                float(event["dynamic_acceleration_rms_mps2"]),
                float(metrics["accel_rms"]),
            )
            event["angular_speed_dps"] = max(
                float(event["angular_speed_dps"]),
                float(metrics["gyro_peak"]),
            )
            event["angular_speed_rms_dps"] = max(
                float(event["angular_speed_rms_dps"]),
                float(metrics["gyro_rms"]),
            )
            event["direction_reversals"] = max(
                int(event["direction_reversals"]),
                int(metrics["direction_reversals"]),
            )
            if energetic:
                self.shake_quiet_seconds = 0.0
                self.shake_last_energy_elapsed_s = elapsed_s + dt
                self.shake_last_energy_epoch = wall_epoch
            else:
                self.shake_quiet_seconds += dt
                if self.shake_quiet_seconds >= SHAKE_MERGE_GAP_SECONDS:
                    self._finish_shake_episode()
            return

        if qualifies:
            if self.shake_candidate_seconds <= 0.0:
                self.shake_candidate_start_s = float(self.shake_window[0][0])
            self.shake_candidate_seconds += dt
            if self.shake_candidate_seconds >= SHAKE_CONFIRM_SECONDS:
                self._start_shake_episode(
                    elapsed_s,
                    sequence,
                    wall_epoch,
                    metrics,
                )
        else:
            self.shake_candidate_seconds = 0.0
            self.shake_candidate_start_s = None

    def _add_live_speed(self, speed: float) -> None:
        """Maintain O(1) live moments and a bounded quantile sample.

        Completed movement statistics are still calculated from every corrected
        sample.  The bounded sample exists only so an HTTP snapshot never scans
        an ever-growing movement while holding the store lock.
        """
        self.live_speed_count += 1
        self.live_speed_sum += speed
        self.live_speed_square_sum += speed * speed
        self.live_speed_min = min(self.live_speed_min, speed)
        self.live_speed_max = max(self.live_speed_max, speed)
        sample_index = self.live_speed_count - 1
        if sample_index % self.live_speed_sample_stride == 0:
            self.live_speed_samples.append(speed)
        if len(self.live_speed_samples) > LIVE_SPEED_QUANTILE_SAMPLES:
            self.live_speed_samples = self.live_speed_samples[::2]
            self.live_speed_sample_stride *= 2

    def _live_speed_statistics(self) -> dict[str, Any]:
        if self.live_speed_count <= 0:
            return {
                **_safe_stats([]),
                "p05": 0.0,
                "p05_percentile": SPEED_PERCENTILE,
                "p05_sample_count": 0,
                "p05_definition": "移动中有界抽样近似；结束后使用完整修正速度重算",
                "provisional": True,
            }
        mean = self.live_speed_sum / self.live_speed_count
        variance = max(
            0.0,
            self.live_speed_square_sum / self.live_speed_count - mean * mean,
        )
        return {
            "min": self.live_speed_min,
            "max": self.live_speed_max,
            "mean": mean,
            "median": float(statistics.median(self.live_speed_samples)),
            "std": math.sqrt(variance),
            "p05": _percentile(self.live_speed_samples, SPEED_PERCENTILE),
            "p05_percentile": SPEED_PERCENTILE,
            "p05_sample_count": len(self.live_speed_samples),
            "p05_definition": "移动中有界抽样近似；结束后使用完整修正速度重算",
            "provisional": True,
        }

    def _clear_rotation_candidate(self) -> None:
        self.rotation_candidate_axis = None
        self.rotation_candidate_degrees = 0.0
        self.rotation_candidate_seconds = 0.0

    def _track_rotation_axis(
        self,
        axis: list[float],
        direction: list[float],
    ) -> list[float]:
        tracked_axis = [
            (1.0 - ROTATION_AXIS_TRACK_RATE) * axis[index]
            + ROTATION_AXIS_TRACK_RATE * direction[index]
            for index in range(3)
        ]
        tracked_norm = _norm(tracked_axis)
        if tracked_norm <= 1e-9:
            return list(axis)
        return [value / tracked_norm for value in tracked_axis]

    def _count_completed_axis_rotations(
        self,
        elapsed_s: float,
        sequence: int,
        wall_epoch: float | None,
    ) -> None:
        while (
            self.rotation_progress_degrees + 1e-6
            >= ROTATION_THRESHOLD_DEGREES
        ):
            self.rotation_count += 1
            self.rotation_degrees_total += ROTATION_THRESHOLD_DEGREES
            self.rotation_progress_degrees -= ROTATION_THRESHOLD_DEGREES
            self.rotation_events.append({
                "rotation_index": self.rotation_count,
                "elapsed_s": float(elapsed_s),
                "sequence": int(sequence),
                "degrees": ROTATION_THRESHOLD_DEGREES,
                "time": _iso_times(wall_epoch),
            })

    def _update_rotation(
        self,
        gyro_world_dps: list[float],
        dt: float,
        elapsed_s: float,
        sequence: int,
        wall_epoch: float | None,
    ) -> None:
        """Count complete turns around one stable, arbitrary world-space axis.

        A confirmed direction change starts an independent rotation segment.
        The unfinished angle from the old segment is discarded rather than
        cancelled by the new direction. Samples collected during the 120 ms
        switch confirmation are replayed into the new segment.
        """
        gyro_dps = gyro_world_dps
        magnitude = _norm(gyro_dps)
        if magnitude < ROTATION_CONTINUE_RATE_DPS:
            if self.rotation_axis is not None:
                self.rotation_gap_seconds += dt
            self._clear_rotation_candidate()
        else:
            direction = [value / magnitude for value in gyro_dps]
            if self.rotation_axis is None:
                if magnitude >= ROTATION_START_RATE_DPS:
                    self.rotation_axis = direction
                    self.rotation_progress_degrees = magnitude * dt
                    self.rotation_gap_seconds = 0.0
            else:
                alignment = sum(
                    direction[axis] * self.rotation_axis[axis]
                    for axis in range(3)
                )
                if alignment >= ROTATION_AXIS_ALIGNMENT:
                    self.rotation_progress_degrees += (
                        magnitude * alignment * dt
                    )
                    self.rotation_axis = self._track_rotation_axis(
                        self.rotation_axis, direction
                    )
                    self.rotation_gap_seconds = 0.0
                    self._clear_rotation_candidate()
                else:
                    if magnitude < ROTATION_START_RATE_DPS:
                        self.rotation_gap_seconds += dt
                        self._clear_rotation_candidate()
                    else:
                        if self.rotation_candidate_axis is None:
                            self.rotation_candidate_axis = direction
                            self.rotation_candidate_degrees = magnitude * dt
                            self.rotation_candidate_seconds = dt
                        else:
                            candidate_alignment = sum(
                                direction[axis]
                                * self.rotation_candidate_axis[axis]
                                for axis in range(3)
                            )
                            if candidate_alignment >= ROTATION_AXIS_ALIGNMENT:
                                self.rotation_candidate_degrees += (
                                    magnitude * candidate_alignment * dt
                                )
                                self.rotation_candidate_axis = (
                                    self._track_rotation_axis(
                                        self.rotation_candidate_axis,
                                        direction,
                                    )
                                )
                                self.rotation_candidate_seconds += dt
                            else:
                                self.rotation_candidate_axis = direction
                                self.rotation_candidate_degrees = magnitude * dt
                                self.rotation_candidate_seconds = dt

                        # Keep the old segment only during the bounded switch
                        # decision. If no stable new axis emerges for 350 ms,
                        # the old incomplete progress is no longer coherent.
                        self.rotation_gap_seconds += dt
                        if (
                            self.rotation_candidate_seconds + 1e-9
                            >= ROTATION_AXIS_SWITCH_CONFIRM_SECONDS
                        ):
                            self.rotation_axis = list(
                                self.rotation_candidate_axis
                            )
                            self.rotation_progress_degrees = (
                                self.rotation_candidate_degrees
                            )
                            self.rotation_gap_seconds = 0.0
                            self._clear_rotation_candidate()

        if self.rotation_gap_seconds >= ROTATION_GAP_RESET_SECONDS:
            self.rotation_axis = None
            self.rotation_progress_degrees = 0.0
            self.rotation_gap_seconds = 0.0
            self._clear_rotation_candidate()

        self._count_completed_axis_rotations(
            elapsed_s, sequence, wall_epoch
        )

    def _update_spatial_rotation(
        self,
        gyro_dps: list[float],
        dt: float,
    ) -> None:
        """Integrate direction-independent angular travel with noise gating."""
        magnitude = _norm(gyro_dps)
        if not self.spatial_rotation_active:
            if magnitude < SPATIAL_ROTATION_START_RATE_DPS:
                return
            self.spatial_rotation_active = True
            self.spatial_rotation_segment_degrees = magnitude * dt
            self.spatial_rotation_gap_seconds = 0.0
            self.spatial_rotation_previous_rate_dps = magnitude
            return

        if magnitude >= SPATIAL_ROTATION_CONTINUE_RATE_DPS:
            previous = self.spatial_rotation_previous_rate_dps
            sample_rate = magnitude if previous is None else 0.5 * (
                previous + magnitude
            )
            self.spatial_rotation_segment_degrees += sample_rate * dt
            self.spatial_rotation_gap_seconds = 0.0
            self.spatial_rotation_previous_rate_dps = magnitude
            return

        self.spatial_rotation_gap_seconds += dt
        self.spatial_rotation_previous_rate_dps = None
        if (
            self.spatial_rotation_gap_seconds + 1e-9
            >= SPATIAL_ROTATION_GAP_RESET_SECONDS
        ):
            self._finish_spatial_rotation_segment()

    def _finish_spatial_rotation_segment(self) -> None:
        if (
            self.spatial_rotation_segment_degrees + 1e-6
            >= SPATIAL_ROTATION_MIN_SEGMENT_DEGREES
        ):
            self.spatial_rotation_degrees_total += (
                self.spatial_rotation_segment_degrees
            )
        self.spatial_rotation_segment_degrees = 0.0
        self.spatial_rotation_active = False
        self.spatial_rotation_gap_seconds = 0.0
        self.spatial_rotation_previous_rate_dps = None

    def _spatial_rotation_degrees(self) -> float:
        live_segment = (
            self.spatial_rotation_segment_degrees
            if self.spatial_rotation_segment_degrees + 1e-6
            >= SPATIAL_ROTATION_MIN_SEGMENT_DEGREES
            else 0.0
        )
        return self.spatial_rotation_degrees_total + live_segment

    def _update_flip(
        self,
        up_world: list[float],
        dt: float,
        elapsed_s: float,
        sequence: int,
        wall_epoch: float | None,
    ) -> None:
        reference_norm = _norm(self.flip_reference_up)
        up_norm = _norm(up_world)
        if reference_norm <= 1e-9 or up_norm <= 1e-9:
            self.flip_candidate_seconds = 0.0
            return
        opposite_dot = sum(
            self.flip_reference_up[axis] * up_world[axis]
            for axis in range(3)
        ) / (reference_norm * up_norm)
        if opposite_dot <= FLIP_OPPOSITE_DOT:
            self.flip_candidate_seconds += dt
            if self.flip_candidate_seconds >= FLIP_CONFIRM_SECONDS:
                self.flip_count += 1
                self.flip_events.append({
                    "flip_index": self.flip_count,
                    "elapsed_s": float(elapsed_s),
                    "sequence": int(sequence),
                    "time": _iso_times(wall_epoch),
                })
                self.flip_reference_up = [
                    value / up_norm for value in up_world
                ]
                self.flip_candidate_seconds = 0.0
        else:
            self.flip_candidate_seconds = 0.0

    def add_pause(
        self,
        start_tick: int,
        end_tick: int,
        start_epoch: float | None,
        end_epoch: float | None,
    ) -> None:
        duration = _tick_seconds(end_tick, start_tick)
        if duration + 1e-9 < PAUSE_MIN_SECONDS:
            return
        self.pause_count += 1
        self.pause_events.append({
            "pause_index": self.pause_count,
            "elapsed_s": _tick_seconds(start_tick, self.start_tick),
            "duration_seconds": duration,
            "start": _iso_times(start_epoch),
            "end": _iso_times(end_epoch),
        })

    def add_sample(
        self,
        estimate: InertialEstimate,
        tick: int,
        sequence: int = 0,
        wall_epoch: float | None = None,
    ) -> None:
        self.latest_tick = tick
        self.latest_epoch = wall_epoch
        elapsed_s = self.moving_seconds
        linear_magnitude = _norm(estimate.linear_world)
        gyro_magnitude = _norm(estimate.corrected_gyro_dps)
        specific_force = _norm(estimate.accel)
        specific_force_g = specific_force / GRAVITY
        speed = _norm(estimate.velocity)

        self.moving_seconds += estimate.dt
        self.accel_sum += linear_magnitude
        self.accel_count += 1
        self.accel_min = min(self.accel_min, linear_magnitude)
        self.accel_max = max(self.accel_max, linear_magnitude)
        self._add_live_speed(speed)

        self._update_shake(
            estimate.linear_world,
            linear_magnitude,
            gyro_magnitude,
            estimate.dt,
            elapsed_s,
            sequence,
            wall_epoch,
        )

        # Complete one-axis rotations are tracked in world coordinates so an
        # arbitrary physical axis remains comparable while the body turns.
        # Older/synthetic estimates without this field retain body-frame
        # behaviour for backward compatibility.
        rotation_gyro = (
            estimate.gyro_world_dps
            if estimate.gyro_world_dps is not None
            else estimate.corrected_gyro_dps
        )
        self._update_rotation(
            rotation_gyro,
            estimate.dt,
            elapsed_s,
            sequence,
            wall_epoch,
        )

        # Spatial rotation is deliberately independent from the one-axis state
        # machine: changing axis or direction never subtracts angular travel.
        self._update_spatial_rotation(
            estimate.corrected_gyro_dps, estimate.dt
        )

        # Flip counting is independent from complete 360-degree rotations.
        self._update_flip(
            estimate.up_world,
            estimate.dt,
            elapsed_s,
            sequence,
            wall_epoch,
        )

        # Drop: confirmed near-free-fall followed by a high-g impact within the
        # configured time window. Low-g alone is neither a pause nor a drop.
        if not self.drop_armed:
            if specific_force_g <= DROP_FREEFALL_G:
                if self.drop_freefall_candidate_seconds <= 0.0:
                    self.drop_freefall_start_s = elapsed_s
                self.drop_freefall_candidate_seconds += estimate.dt
                if (
                    self.drop_freefall_candidate_seconds
                    >= DROP_FREEFALL_CONFIRM_SECONDS
                ):
                    self.drop_armed = True
                    self.drop_post_freefall_seconds = 0.0
            else:
                self.drop_freefall_candidate_seconds = 0.0
                self.drop_freefall_start_s = None
        else:
            if specific_force_g <= DROP_FREEFALL_G:
                self.drop_post_freefall_seconds = 0.0
            else:
                self.drop_post_freefall_seconds += estimate.dt
                if specific_force_g >= DROP_IMPACT_G:
                    self.drop_count += 1
                    event_epoch = wall_epoch
                    self.drop_events.append({
                        "drop_index": self.drop_count,
                        "freefall_start_s": self.drop_freefall_start_s,
                        "impact_s": elapsed_s,
                        "sequence": int(sequence),
                        "impact_g": specific_force_g,
                        "impact_time": _iso_times(event_epoch),
                    })
                    self.drop_armed = False
                    self.drop_freefall_candidate_seconds = 0.0
                    self.drop_freefall_start_s = None
                    self.drop_post_freefall_seconds = 0.0
                elif self.drop_post_freefall_seconds > DROP_IMPACT_WINDOW_SECONDS:
                    self.drop_armed = False
                    self.drop_freefall_candidate_seconds = 0.0
                    self.drop_freefall_start_s = None
                    self.drop_post_freefall_seconds = 0.0

        if len(self.integration_samples) < MAX_MOVEMENT_SAMPLES:
            self.integration_samples.append(
                (estimate.dt, list(estimate.linear_world))
            )
            self.sample_records.append({
                "sample_index": len(self.sample_records),
                "sequence": int(sequence),
                "tick_32k": int(tick),
                "elapsed_s": float(elapsed_s),
                "dt_s": float(estimate.dt),
                "computer_epoch": wall_epoch,
                "accel_mps2": list(estimate.accel),
                "accel_norm_mps2": specific_force,
                "linear_world_mps2": list(estimate.linear_world),
                "dynamic_accel_norm_mps2": linear_magnitude,
                "gyro_dps": list(estimate.gyro_dps),
                "gyro_corrected_dps": list(estimate.corrected_gyro_dps),
                "gyro_norm_dps": gyro_magnitude,
                "velocity_mps": list(estimate.velocity),
                "speed_mps": speed,
                "position_m": list(estimate.position),
                "stationary": bool(estimate.stationary),
                "specific_force_g": specific_force_g,
            })
        else:
            self.samples_truncated = True

    def add_device_coordinate(
        self,
        source_sequence: int,
        position_m: list[float],
        wall_epoch: float | None,
    ) -> None:
        self.device_coordinate_records.append({
            "coordinate_index": len(self.device_coordinate_records),
            "source_sequence": int(source_sequence),
            "computer_epoch": wall_epoch,
            "position_m": list(position_m),
        })

    def corrected_kinematics(
        self,
    ) -> tuple[list[list[float]], list[list[float]]]:
        if not self.integration_samples:
            return [[0.0, 0.0, 0.0]], []
        sample_array = np.asarray(
            [[dt, *acceleration] for dt, acceleration in self.integration_samples],
            dtype=float,
        )
        dt = sample_array[:, 0]
        acceleration = sample_array[:, 1:4]
        total_seconds = float(np.sum(dt))
        if total_seconds <= 1e-6:
            return [[0.0, 0.0, 0.0]], [[0.0, 0.0, 0.0]] * len(self.integration_samples)

        residual_velocity = np.sum(acceleration * dt[:, None], axis=0)
        corrected = acceleration - residual_velocity / total_seconds
        velocity_delta = corrected * dt[:, None]
        velocities = np.cumsum(velocity_delta, axis=0)
        previous_velocities = velocities - velocity_delta
        position_delta = (
            previous_velocities * dt[:, None]
            + 0.5 * corrected * np.square(dt)[:, None]
        )
        positions = np.cumsum(position_delta, axis=0)
        path = np.vstack((np.zeros((1, 3), dtype=float), positions))
        return path.tolist(), velocities.tolist()

    def rest_to_rest_corrected_path(self) -> list[list[float]]:
        path, _ = self.corrected_kinematics()
        if len(path) > PATH_HISTORY:
            stride = math.ceil(len(path) / PATH_HISTORY)
            reduced = path[::stride]
            if reduced[-1] != path[-1]:
                reduced.append(path[-1])
            return reduced
        return path

    def prepare_for_finalize(self, received_path: list[list[float]]) -> None:
        """Seal the segment using only bounded, non-FFT work."""
        if self.prepared_for_finalize:
            return
        self._finish_shake_episode()
        self._finish_spatial_rotation_segment()
        # PC stop confirmation is backdated to the first quiet sample. Remove any
        # already-buffered samples at/after that transition so duration, FFT and
        # speed statistics describe exactly the recorded movement interval.
        if self.end_sequence is not None:
            while self.sample_records and _sequence_at_or_after(
                int(self.sample_records[-1]["sequence"]), self.end_sequence
            ):
                self.sample_records.pop()
                if self.integration_samples:
                    self.integration_samples.pop()
        self.moving_seconds = sum(
            float(row.get("dt_s", 0.0)) for row in self.sample_records
        )
        linear_magnitudes = [
            float(row["dynamic_accel_norm_mps2"]) for row in self.sample_records
        ]
        self.accel_count = len(linear_magnitudes)
        self.accel_sum = sum(linear_magnitudes)
        self.accel_min = min(linear_magnitudes) if linear_magnitudes else math.inf
        self.accel_max = max(linear_magnitudes) if linear_magnitudes else 0.0
        self.received_path_final = [list(point) for point in received_path]
        self.prepared_for_finalize = True

    def finalize(
        self,
        apply_rest_correction: bool,
        calculated_path: list[list[float]],
        received_path: list[list[float]],
    ) -> None:
        if self.finalized:
            return
        self.prepare_for_finalize(received_path)

        if apply_rest_correction:
            corrected_path, corrected_velocities = self.corrected_kinematics()
            self.calculated_path_final = corrected_path
            self.position_corrected = len(corrected_path) > 1
            for index, row in enumerate(self.sample_records):
                if index < len(corrected_velocities):
                    row["velocity_mps"] = corrected_velocities[index]
                    row["speed_mps"] = _norm(corrected_velocities[index])
                if index + 1 < len(corrected_path):
                    row["position_m"] = corrected_path[index + 1]
        else:
            self.calculated_path_final = [list(point) for point in calculated_path]

        self.speed_statistics = _speed_statistics(self.sample_records)
        accel_rows = [list(row["accel_mps2"]) for row in self.sample_records]
        dynamic_rows = [list(row["linear_world_mps2"]) for row in self.sample_records]
        self.accel_statistics = {
            "specific_force_mps2": _axis_statistics(accel_rows, ("x", "y", "z")),
            "dynamic_world_mps2": _axis_statistics(dynamic_rows, ("x", "y", "z")),
        }
        self.tremor = _tremor_fft(self.sample_records)
        # The integration input duplicates values already retained in the
        # finalized sample rows and is never needed by detail/export readers.
        self.integration_samples.clear()
        self.live_speed_samples.clear()
        self.finalized = True

    def release_high_frequency_detail(self) -> bool:
        """Release archived sample payload while preserving every summary.

        Task archives are the durable source for historical detail.  Counts,
        speed statistics, tremor results and event summaries stay resident so
        snapshot/current-task APIs keep exactly the same frontend contract.
        """
        if not self.finalized or self.high_frequency_released:
            return False
        self.integration_samples.clear()
        self.sample_records.clear()
        self.device_coordinate_records.clear()
        self.calculated_path_final.clear()
        self.received_path_final.clear()
        self.live_speed_samples.clear()
        self.high_frequency_released = True
        return True

    def _time_snapshot(self) -> dict[str, Any]:
        return {
            "start": _iso_times(self.start_epoch),
            "end": _iso_times(self.end_epoch),
            "start_epoch": self.start_epoch,
            "end_epoch": self.end_epoch,
        }

    def snapshot(self, state: str) -> dict[str, Any]:
        accel_mean = self.accel_sum / self.accel_count if self.accel_count else 0.0
        end_tick = (
            self.end_tick
            if self.end_tick is not None
            else self.latest_tick
            if self.latest_tick is not None
            else self.start_tick
        )
        segment_seconds = _tick_seconds(end_tick, self.start_tick)
        live_speed_stats = (
            self.speed_statistics
            if self.speed_statistics
            else self._live_speed_statistics()
        )
        tremor = self.tremor or {
            "valid": False,
            "dominant_frequency_hz": None,
            "intensity_dps": None,
            "band_rms_dps": None,
            "reason": "移动尚未结束",
        }
        spatial_rotation_degrees = self._spatial_rotation_degrees()
        return {
            "movement_id": self.movement_id,
            "block_id": self.block_id,
            "mapping_version": self.mapping_version,
            "state": state,
            "moving_seconds": self.moving_seconds,
            "segment_seconds": segment_seconds,
            "accel_mean": accel_mean,
            "accel_min": self.accel_min if self.accel_count else 0.0,
            "accel_max": self.accel_max,
            "shake_count": self.shake_count,
            "rotation_count": self.rotation_count,
            "rotation_turns": self.rotation_degrees_total / ROTATION_THRESHOLD_DEGREES,
            "rotation_progress_degrees": self.rotation_progress_degrees,
            "spatial_rotation_degrees": spatial_rotation_degrees,
            "spatial_rotation_turns": (
                spatial_rotation_degrees / SPATIAL_ROTATION_TURN_DEGREES
            ),
            "spatial_rotation_count": int(
                (spatial_rotation_degrees + 1e-6)
                // SPATIAL_ROTATION_TURN_DEGREES
            ),
            "flip_count": self.flip_count,
            "pause_count": self.pause_count,
            "pause_total_seconds": sum(
                float(event["duration_seconds"]) for event in self.pause_events
            ),
            "pause_definition": "stationary_at_least_0.8s_then_resume",
            "drop_count": self.drop_count,
            "speed": live_speed_stats,
            "tremor": tremor,
            "position_corrected": self.position_corrected,
            "sample_count": self.accel_count,
            "samples_truncated": self.samples_truncated,
            "time": self._time_snapshot(),
        }

    def detail_snapshot(self, state: str = "已完成") -> dict[str, Any]:
        summary = self.snapshot(state)
        acceleration_series = [
            [row["elapsed_s"], *row["accel_mps2"]]
            for row in self.sample_records
        ]
        dynamic_acceleration_series = [
            [row["elapsed_s"], *row["linear_world_mps2"]]
            for row in self.sample_records
        ]
        gyroscope_series = [
            [row["elapsed_s"], *row["gyro_dps"]]
            for row in self.sample_records
        ]
        speed_series = [
            [row["elapsed_s"], row["speed_mps"]]
            for row in self.sample_records
        ]
        return {
            **summary,
            "acceleration_statistics": self.accel_statistics,
            "acceleration_series": acceleration_series,
            "dynamic_acceleration_series": dynamic_acceleration_series,
            "gyroscope_series": gyroscope_series,
            "speed_series": speed_series,
            "trajectory_series": [
                [
                    float(row["elapsed_s"]),
                    *row["position_m"],
                    float(row["speed_mps"]),
                ]
                for row in self.sample_records
            ],
            "pause_events": self.pause_events,
            "drop_events": self.drop_events,
            "shake_events": self.shake_events,
            "rotation_events": self.rotation_events,
            "flip_events": self.flip_events,
            "calculated_path": self.calculated_path_final,
            "received_path": self.received_path_final,
            "device_coordinates": self.device_coordinate_records,
            "analysis_parameters": {
                "pause_min_seconds": PAUSE_MIN_SECONDS,
                "movement_end_hold_seconds": MOVEMENT_END_HOLD_SECONDS,
                "shake_window_seconds": SHAKE_WINDOW_SECONDS,
                "shake_start_rms_mps2": SHAKE_START_RMS_MPS2,
                "shake_start_peak_mps2": SHAKE_START_PEAK_MPS2,
                "shake_end_mps2": SHAKE_END_MPS2,
                "shake_direction_deadband_mps2": (
                    SHAKE_DIRECTION_DEADBAND_MPS2
                ),
                "shake_min_direction_reversals": (
                    SHAKE_MIN_DIRECTION_REVERSALS
                ),
                "shake_confirm_seconds": SHAKE_CONFIRM_SECONDS,
                "shake_merge_gap_seconds": SHAKE_MERGE_GAP_SECONDS,
                "rotation_start_rate_dps": ROTATION_START_RATE_DPS,
                "rotation_continue_rate_dps": ROTATION_CONTINUE_RATE_DPS,
                "rotation_threshold_degrees": ROTATION_THRESHOLD_DEGREES,
                "rotation_axis_alignment": ROTATION_AXIS_ALIGNMENT,
                "rotation_axis_switch_confirm_seconds": (
                    ROTATION_AXIS_SWITCH_CONFIRM_SECONDS
                ),
                "rotation_gap_reset_seconds": ROTATION_GAP_RESET_SECONDS,
                "spatial_rotation_start_rate_dps": (
                    SPATIAL_ROTATION_START_RATE_DPS
                ),
                "spatial_rotation_continue_rate_dps": (
                    SPATIAL_ROTATION_CONTINUE_RATE_DPS
                ),
                "spatial_rotation_gap_reset_seconds": (
                    SPATIAL_ROTATION_GAP_RESET_SECONDS
                ),
                "spatial_rotation_min_segment_degrees": (
                    SPATIAL_ROTATION_MIN_SEGMENT_DEGREES
                ),
                "flip_opposite_degrees": 150.0,
                "flip_confirm_seconds": FLIP_CONFIRM_SECONDS,
                "speed_p05_percentile": SPEED_PERCENTILE,
                "speed_core_trim_fraction": SPEED_CORE_TRIM_FRACTION,
                "speed_core_trim_max_seconds": SPEED_CORE_TRIM_MAX_SECONDS,
                "drop_freefall_g": DROP_FREEFALL_G,
                "drop_impact_g": DROP_IMPACT_G,
                "drop_impact_window_seconds": DROP_IMPACT_WINDOW_SECONDS,
                "tremor_highpass_hz": TREMOR_HIGHPASS_HZ,
                "tremor_band_hz": [TREMOR_BAND_LOW_HZ, TREMOR_BAND_HIGH_HZ],
                "speed_semantics": "PC惯导估算；仅用于同一设备同一流程内比较，不是标定速度计",
            },
        }

    def export_json(self, session_tremor: dict[str, Any]) -> str:
        payload = self.detail_snapshot()
        payload["session_tremor_average"] = session_tremor
        payload["samples"] = [
            {
                **row,
                "time": _iso_times(row.get("computer_epoch")),
            }
            for row in self.sample_records
        ]
        payload["device_coordinates"] = [
            {
                **row,
                "time": _iso_times(row.get("computer_epoch")),
            }
            for row in self.device_coordinate_records
        ]
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def export_csv(self, session_tremor: dict[str, Any]) -> str:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["metadata", "movement_id", self.movement_id])
        writer.writerow(["metadata", "block_id", self.block_id])
        writer.writerow(["metadata", "mapping_version", self.mapping_version])
        writer.writerow(["metadata", "start_computer", _iso_times(self.start_epoch)["computer"]])
        writer.writerow(["metadata", "end_computer", _iso_times(self.end_epoch)["computer"]])
        writer.writerow(["metadata", "start_beijing", _iso_times(self.start_epoch)["beijing"]])
        writer.writerow(["metadata", "end_beijing", _iso_times(self.end_epoch)["beijing"]])
        writer.writerow(["metadata", "pause_count", self.pause_count])
        writer.writerow(["metadata", "pause_total_seconds", sum(
            float(event["duration_seconds"]) for event in self.pause_events
        )])
        writer.writerow(["metadata", "speed_p05_mps", self.speed_statistics.get("p05")])
        writer.writerow(["metadata", "drop_count", self.drop_count])
        writer.writerow(["metadata", "rotation_count", self.rotation_count])
        writer.writerow([
            "metadata",
            "spatial_rotation_degrees",
            self._spatial_rotation_degrees(),
        ])
        writer.writerow([
            "metadata",
            "spatial_rotation_turns",
            self._spatial_rotation_degrees() / SPATIAL_ROTATION_TURN_DEGREES,
        ])
        writer.writerow(["metadata", "flip_count", self.flip_count])
        writer.writerow(["metadata", "shake_events", json.dumps(self.shake_events, ensure_ascii=False)])
        writer.writerow(["metadata", "rotation_events", json.dumps(self.rotation_events, ensure_ascii=False)])
        writer.writerow(["metadata", "flip_events", json.dumps(self.flip_events, ensure_ascii=False)])
        writer.writerow(["metadata", "pause_events", json.dumps(self.pause_events, ensure_ascii=False)])
        writer.writerow(["metadata", "drop_events", json.dumps(self.drop_events, ensure_ascii=False)])
        writer.writerow(["metadata", "tremor_frequency_hz", self.tremor.get("dominant_frequency_hz")])
        writer.writerow(["metadata", "tremor_intensity_dps", self.tremor.get("intensity_dps")])
        writer.writerow(["metadata", "session_mean_tremor_frequency_hz", session_tremor.get("mean_frequency_hz")])
        writer.writerow(["metadata", "session_mean_tremor_intensity_dps", session_tremor.get("mean_intensity_dps")])
        writer.writerow([])
        writer.writerow([
            "record_type", "sample_index", "sequence", "tick_32k", "elapsed_s",
            "computer_time", "beijing_time",
            "accel_x_mps2", "accel_y_mps2", "accel_z_mps2", "accel_norm_mps2",
            "linear_x_mps2", "linear_y_mps2", "linear_z_mps2", "dynamic_accel_norm_mps2",
            "gyro_x_dps", "gyro_y_dps", "gyro_z_dps", "gyro_norm_dps",
            "velocity_x_mps", "velocity_y_mps", "velocity_z_mps", "speed_mps",
            "pc_position_x_m", "pc_position_y_m", "pc_position_z_m",
            "stationary", "specific_force_g", "coordinate_source_sequence",
            "device_position_x_m", "device_position_y_m", "device_position_z_m",
        ])
        for row in self.sample_records:
            times = _iso_times(row.get("computer_epoch"))
            writer.writerow([
                "imu_pc", row["sample_index"], row["sequence"], row["tick_32k"], row["elapsed_s"],
                times["computer"], times["beijing"],
                *row["accel_mps2"], row["accel_norm_mps2"],
                *row["linear_world_mps2"], row["dynamic_accel_norm_mps2"],
                *row["gyro_corrected_dps"], row["gyro_norm_dps"],
                *row["velocity_mps"], row["speed_mps"],
                *row["position_m"], row["stationary"], row["specific_force_g"],
                "", "", "", "",
            ])
        for row in self.device_coordinate_records:
            times = _iso_times(row.get("computer_epoch"))
            writer.writerow([
                "device_coordinate", row["coordinate_index"], "", "", "",
                times["computer"], times["beijing"],
                "", "", "", "", "", "", "", "", "", "", "", "",
                "", "", "", "", "", "", "", "", "",
                row["source_sequence"], *row["position_m"],
            ])
        return output.getvalue()


@dataclass(frozen=True)
class MovementFinalizationRequest:
    device_id: int
    mode: str
    generation: int
    movement: MovementStats
    calculated_path: list[list[float]]
    received_path: list[list[float]]
    queued_monotonic: float


@dataclass(frozen=True)
class MovementFinalizationResult:
    request: MovementFinalizationRequest
    elapsed_seconds: float
    error: str | None = None


def _run_movement_finalization(
    request: MovementFinalizationRequest,
) -> MovementFinalizationResult:
    started = time.perf_counter()
    try:
        request.movement.finalize(
            apply_rest_correction=True,
            calculated_path=request.calculated_path,
            received_path=request.received_path,
        )
        error = None
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        error = f"{type(exc).__name__}: {exc}"
    return MovementFinalizationResult(
        request=request,
        elapsed_seconds=time.perf_counter() - started,
        error=error,
    )


@dataclass
class AnalysisPipeline:
    mode: str
    device_id: int = 0
    block_id: str = ""
    mapping_version: str = ""
    minimum_movement_seconds: float = DEFAULT_MINIMUM_MOVEMENT_SECONDS
    estimator: InertialEstimator = field(default_factory=InertialEstimator)
    detector: PcMotionDetector | None = None
    received_path: deque = field(default_factory=lambda: deque(maxlen=PATH_HISTORY))
    calculated_path: deque = field(default_factory=lambda: deque(maxlen=PATH_HISTORY))
    completed_movements: deque = field(default_factory=deque)
    current_movement: MovementStats | None = None
    next_movement_id: int = 1
    ignored_short_movement_count: int = 0
    received_origin: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    received_origin_set: bool = False
    last_coordinate_source_sequence: int | None = None
    trajectory_revision: int = 0
    received_path_cursor: int = 0
    calculated_path_cursor: int = 0
    interrupted_movement_count: int = 0
    motion_state: str = "等待静止"
    analysis_generation: int = 0
    pending_finalization_ids: set[int] = field(default_factory=set)
    archived_movement_ids: set[int] = field(default_factory=set)
    task_collection_active: bool = False
    finalization_requests: deque = field(default_factory=deque, repr=False)
    finalization_error_count: int = 0
    last_finalization_error: str | None = None
    last_finalization_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.mode == ANALYSIS_MODE_PC and self.detector is None:
            self.detector = PcMotionDetector()

    def _begin_movement(
        self,
        start_tick: int,
        start_sequence: int,
        start_epoch: float | None,
        frame: MotionFrame | None = None,
    ) -> None:
        self.trajectory_revision += 1
        self.estimator.reset_position()
        self.received_path.clear()
        self.calculated_path.clear()
        self.received_path.append([0.0, 0.0, 0.0])
        self.calculated_path.append([0.0, 0.0, 0.0])
        self.received_path_cursor = 1
        self.calculated_path_cursor = 1
        self.last_coordinate_source_sequence = None

        if (
            self.mode == ANALYSIS_MODE_DEVICE
            and frame is not None
            and frame.coordinate_valid
        ):
            self.received_origin = [value / 1000.0 for value in frame.position_mm]
            self.received_origin_set = True
        else:
            self.received_origin = [0.0, 0.0, 0.0]
            self.received_origin_set = False

        self.current_movement = MovementStats(
            movement_id=self.next_movement_id,
            start_tick=start_tick,
            start_up=_body_up_in_world(self.estimator.quaternion),
            block_id=self.block_id,
            mapping_version=self.mapping_version,
            start_sequence=start_sequence,
            start_epoch=start_epoch,
        )
        self.motion_state = "移动中"

    def set_block_mapping(self, block_id: str, mapping_version: str) -> None:
        # An active movement keeps the mapping captured at its start.  The new
        # mapping applies to the next complete movement only.
        self.block_id = block_id
        self.mapping_version = mapping_version

    def break_segment(self, reason: str) -> None:
        """Discard an incomplete segment and rebuild all causal tracking state."""
        if self.current_movement is not None:
            self.interrupted_movement_count += 1
        self.current_movement = None
        self.estimator = InertialEstimator()
        if self.mode == ANALYSIS_MODE_PC:
            self.detector = PcMotionDetector()
            self.motion_state = "数据中断，等待静止校准"
        else:
            self.detector = None
            self.motion_state = "数据中断，等待设备状态"
        self.received_path.clear()
        self.calculated_path.clear()
        self.received_path_cursor = 0
        self.calculated_path_cursor = 0
        self.received_origin = [0.0, 0.0, 0.0]
        self.received_origin_set = False
        self.last_coordinate_source_sequence = None
        self.trajectory_revision += 1

    def _resume_movement(
        self,
        tick: int,
        resume_epoch: float | None,
    ) -> None:
        movement = self.current_movement
        if movement is None or movement.pending_stop_tick is None:
            return
        movement.add_pause(
            movement.pending_stop_tick,
            tick,
            movement.pending_stop_epoch,
            resume_epoch,
        )
        movement.pending_stop_tick = None
        movement.pending_stop_sequence = None
        movement.pending_stop_epoch = None
        self.motion_state = "移动中"

    def _mark_stop(
        self,
        tick: int,
        sequence: int,
        stop_epoch: float | None,
    ) -> None:
        movement = self.current_movement
        if movement is None:
            self.motion_state = "静止"
            return
        if movement.pending_stop_tick is None:
            movement.pending_stop_tick = tick
            movement.pending_stop_sequence = sequence
            movement.pending_stop_epoch = stop_epoch
        self.motion_state = "停止确认"

    def _finish_movement(self) -> None:
        movement = self.current_movement
        if movement is None:
            return
        movement.end_tick = (
            movement.pending_stop_tick
            if movement.pending_stop_tick is not None
            else movement.latest_tick
        )
        movement.end_sequence = (
            movement.pending_stop_sequence
            if movement.pending_stop_sequence is not None
            else movement.start_sequence
        )
        movement.end_epoch = (
            movement.pending_stop_epoch
            if movement.pending_stop_epoch is not None
            else movement.latest_epoch
            if movement.latest_epoch is not None
            else time.time()
        )
        movement.prepare_for_finalize(list(self.received_path))
        if movement.moving_seconds + 1e-9 < self.minimum_movement_seconds:
            # A candidate caused by desk vibration or a brief knock is not a
            # completed movement. It does not consume a visible movement number,
            # enter history, participate in FFT/session averages, or remain as
            # the displayed trajectory.
            self.ignored_short_movement_count += 1
            self.received_path.clear()
            self.calculated_path.clear()
            self.received_path_cursor = 0
            self.calculated_path_cursor = 0
            self.received_origin = [0.0, 0.0, 0.0]
            self.received_origin_set = False
            self.last_coordinate_source_sequence = None
            self.current_movement = None
            self.motion_state = "静止"
            self.trajectory_revision += 1
            return

        request = MovementFinalizationRequest(
            device_id=self.device_id,
            mode=self.mode,
            generation=self.analysis_generation,
            movement=movement,
            calculated_path=list(self.calculated_path),
            received_path=list(self.received_path),
            queued_monotonic=time.monotonic(),
        )
        self.finalization_requests.append(request)
        self.pending_finalization_ids.add(movement.movement_id)
        self.next_movement_id += 1
        self.current_movement = None
        self.motion_state = "静止"

    def take_finalization_requests(self) -> list[MovementFinalizationRequest]:
        requests = list(self.finalization_requests)
        self.finalization_requests.clear()
        return requests

    def apply_finalization_result(
        self,
        result: MovementFinalizationResult,
    ) -> None:
        request = result.request
        if request.generation != self.analysis_generation:
            return
        movement = request.movement
        self.pending_finalization_ids.discard(movement.movement_id)
        self.last_finalization_seconds = result.elapsed_seconds
        if result.error is not None:
            self.finalization_error_count += 1
            self.last_finalization_error = result.error
            return
        if movement.moving_seconds + 1e-9 < self.minimum_movement_seconds:
            self.ignored_short_movement_count += 1
            return

        movements = list(self.completed_movements)
        movements.append(movement)
        movements.sort(key=lambda item: item.movement_id, reverse=True)
        self.completed_movements = deque(movements)

        newest_id = movements[0].movement_id if movements else 0
        latest_pending_id = max(self.pending_finalization_ids, default=0)
        if (
            self.current_movement is None
            and movement.movement_id == newest_id
            and movement.movement_id > latest_pending_id
        ):
            if movement.calculated_path_final:
                display_path = movement.calculated_path_final
                if len(display_path) > PATH_HISTORY:
                    stride = math.ceil(len(display_path) / PATH_HISTORY)
                    display_path = display_path[::stride]
                    if display_path[-1] != movement.calculated_path_final[-1]:
                        display_path.append(movement.calculated_path_final[-1])
                self.calculated_path.clear()
                self.calculated_path.extend(display_path)
                self.calculated_path_cursor = len(display_path)
            self.received_path.clear()
            self.received_path.extend(
                movement.received_path_final[-PATH_HISTORY:]
            )
            self.received_path_cursor = len(self.received_path)
        self.last_finalization_error = None
        self.trajectory_revision += 1
        self._prune_detail_cache(release_unarchived=not self.task_collection_active)

    def mark_movement_archived(self, movement_id: int) -> int:
        """Mark durable detail as available and enforce the RAM cache bound."""
        identifier = int(movement_id)
        if not any(item.movement_id == identifier for item in self.completed_movements):
            return 0
        self.archived_movement_ids.add(identifier)
        return self._prune_detail_cache(
            release_unarchived=not self.task_collection_active
        )

    def set_task_collection_active(self, active: bool) -> int:
        """Protect task data until archived; trim task-external detail eagerly."""
        self.task_collection_active = bool(active)
        if self.task_collection_active:
            return 0
        return self._prune_detail_cache(release_unarchived=True)

    def _prune_detail_cache(self, release_unarchived: bool) -> int:
        cached_count = 0
        cached_samples = 0
        released = 0
        for movement in sorted(
            self.completed_movements,
            key=lambda item: item.movement_id,
            reverse=True,
        ):
            if (
                movement.movement_id not in self.archived_movement_ids
                and not release_unarchived
            ):
                continue
            sample_count = len(movement.sample_records)
            keep = (
                not movement.high_frequency_released
                and cached_count < RECENT_MOVEMENT_DETAIL_LIMIT
                and (
                    cached_count == 0
                    or cached_samples + sample_count
                    <= RECENT_MOVEMENT_DETAIL_SAMPLE_BUDGET
                )
            )
            if keep:
                cached_count += 1
                cached_samples += sample_count
            elif movement.release_high_frequency_detail():
                released += 1
        return released

    def _check_finish(self, tick: int) -> None:
        movement = self.current_movement
        if movement is None or movement.pending_stop_tick is None:
            return
        stopped_seconds = _tick_seconds(tick, movement.pending_stop_tick)
        if stopped_seconds >= MOVEMENT_END_HOLD_SECONDS:
            self._finish_movement()
        elif stopped_seconds >= PAUSE_MIN_SECONDS:
            self.motion_state = "停顿中"

    def _append_received_coordinate(
        self,
        frame: MotionFrame,
        frame_epoch: float,
    ) -> None:
        movement = self.current_movement
        if not frame.coordinate_valid or movement is None:
            return

        source_sequence = frame.coordinate_source_sequence
        if self.mode == ANALYSIS_MODE_PC and not _sequence_at_or_after(
            source_sequence, movement.start_sequence
        ):
            return
        if self.last_coordinate_source_sequence == source_sequence:
            return
        self.last_coordinate_source_sequence = source_sequence

        position = [value / 1000.0 for value in frame.position_mm]
        if not self.received_origin_set:
            self.received_origin = position
            self.received_origin_set = True
            movement.add_device_coordinate(
                source_sequence, [0.0, 0.0, 0.0], frame_epoch
            )
            return
        relative = [position[axis] - self.received_origin[axis] for axis in range(3)]
        self.received_path.append(relative)
        self.received_path_cursor += 1
        movement.add_device_coordinate(source_sequence, relative, frame_epoch)

    def process_device(
        self,
        frame: MotionFrame,
        sparse: bool,
        frame_epoch: float,
    ) -> None:
        moving_now = bool(frame.coordinate_flags & COORD_MOVING)
        stationary_now = bool(frame.coordinate_flags & COORD_STATIONARY)
        calibrating_now = bool(frame.coordinate_flags & COORD_CALIBRATING)
        first_sample = frame.samples[0]
        last_tick = frame.samples[-1].tick_32k
        first_epoch = _sample_epoch(frame_epoch, first_sample.tick_32k, last_tick)

        if moving_now:
            if self.current_movement is None:
                self._begin_movement(
                    first_sample.tick_32k,
                    first_sample.sequence,
                    first_epoch,
                    frame,
                )
            elif self.current_movement.pending_stop_tick is not None:
                self._resume_movement(
                    first_sample.tick_32k,
                    first_epoch,
                )
        elif stationary_now:
            if self.current_movement is not None:
                self._mark_stop(
                    first_sample.tick_32k,
                    first_sample.sequence,
                    first_epoch,
                )
            else:
                self.motion_state = "静止"
        elif calibrating_now:
            self.motion_state = "校准中"
        elif self.current_movement is None:
            self.motion_state = "等待移动"

        if stationary_now or calibrating_now:
            stationary_hint: bool | None = True
        elif moving_now:
            stationary_hint = False
        else:
            stationary_hint = None

        for sample in frame.samples:
            sample_epoch = _sample_epoch(frame_epoch, sample.tick_32k, last_tick)
            estimate = self.estimator.update(
                sample,
                sparse_diagnostic=sparse,
                stationary_hint=stationary_hint,
            )
            if self.current_movement is not None and moving_now:
                self.current_movement.add_sample(
                    estimate,
                    sample.tick_32k,
                    sample.sequence,
                    sample_epoch,
                )
                self.calculated_path.append(estimate.position)
                self.calculated_path_cursor += 1
            if self.current_movement is not None:
                self.current_movement.latest_tick = sample.tick_32k
                self.current_movement.latest_epoch = sample_epoch

        self._append_received_coordinate(frame, frame_epoch)
        self._check_finish(last_tick)

    def process_pc(
        self,
        frame: MotionFrame,
        sparse: bool,
        frame_epoch: float,
    ) -> None:
        assert self.detector is not None
        last_tick = frame.samples[-1].tick_32k

        for sample in frame.samples:
            sample_epoch = _sample_epoch(frame_epoch, sample.tick_32k, last_tick)
            measurements = InertialEstimator.measurements(sample)
            decision = self.detector.update(
                measurements[0], measurements[1], sample.tick_32k, sample.sequence
            )

            if (
                decision.motion_candidate
                and self.current_movement is not None
                and self.current_movement.pending_stop_tick is not None
            ):
                self._resume_movement(
                    decision.transition_tick
                    if decision.transition_tick is not None
                    else sample.tick_32k,
                    _sample_epoch(
                        frame_epoch,
                        decision.transition_tick
                        if decision.transition_tick is not None
                        else sample.tick_32k,
                        last_tick,
                    ),
                )

            if decision.started and self.current_movement is None:
                transition_tick = (
                    decision.transition_tick
                    if decision.transition_tick is not None
                    else sample.tick_32k
                )
                self._begin_movement(
                    transition_tick,
                    decision.transition_sequence
                    if decision.transition_sequence is not None
                    else sample.sequence,
                    _sample_epoch(frame_epoch, transition_tick, last_tick),
                )

            if decision.stopped:
                transition_tick = (
                    decision.transition_tick
                    if decision.transition_tick is not None
                    else sample.tick_32k
                )
                self._mark_stop(
                    transition_tick,
                    decision.transition_sequence
                    if decision.transition_sequence is not None
                    else sample.sequence,
                    _sample_epoch(frame_epoch, transition_tick, last_tick),
                )

            estimate = self.estimator.update(
                sample,
                sparse_diagnostic=sparse,
                stationary_hint=decision.stationary_hint,
                measurements=measurements,
            )

            movement = self.current_movement
            if movement is not None:
                # A quiet candidate is the first sample that may become the
                # backdated stop boundary.  Keep that sample and the following
                # stop-hold window out of the live path.  If motion resumes,
                # trajectory collection continues from the next moving sample.
                if (
                    movement.pending_stop_tick is None
                    and not decision.quiet_candidate
                ):
                    self.calculated_path.append(estimate.position)
                    self.calculated_path_cursor += 1
                if not estimate.stationary:
                    movement.add_sample(
                        estimate,
                        sample.tick_32k,
                        sample.sequence,
                        sample_epoch,
                    )
                movement.latest_tick = sample.tick_32k
                movement.latest_epoch = sample_epoch

            if decision.state == "calibrating":
                self.motion_state = "PC校准中"
            elif self.current_movement is None:
                self.motion_state = (
                    "静止" if decision.state == "stationary" else "等待移动"
                )
            elif self.current_movement.pending_stop_tick is not None:
                self.motion_state = "停止确认"
            else:
                self.motion_state = "移动中"

        self._append_received_coordinate(frame, frame_epoch)
        self._check_finish(last_tick)

    def set_minimum_movement_seconds(self, value: float) -> int:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("最短有效移动时长必须是有限数字")
        if value < 0.0 or value > MAXIMUM_MOVEMENT_FILTER_SECONDS:
            raise ValueError(
                f"最短有效移动时长必须在 0～{MAXIMUM_MOVEMENT_FILTER_SECONDS:g} 秒之间"
            )
        self.minimum_movement_seconds = value

        kept = [
            movement
            for movement in self.completed_movements
            if movement.moving_seconds + 1e-9 >= value
        ]
        removed = len(self.completed_movements) - len(kept)
        if removed:
            self.ignored_short_movement_count += removed
            self.completed_movements = deque(kept)
            self.archived_movement_ids.intersection_update(
                movement.movement_id for movement in kept
            )

        if self.current_movement is not None:
            self.current_movement.movement_id = max(
                self.current_movement.movement_id,
                self.next_movement_id,
            )

        if self.current_movement is None:
            self.received_path.clear()
            self.calculated_path.clear()
            self.received_path_cursor = 0
            self.calculated_path_cursor = 0
            if self.completed_movements:
                latest = self.completed_movements[0]
                self.received_path.extend(latest.received_path_final[-PATH_HISTORY:])
                self.calculated_path.extend(latest.calculated_path_final[-PATH_HISTORY:])
                self.received_path_cursor = len(self.received_path)
                self.calculated_path_cursor = len(self.calculated_path)
        return removed

    def clear_analysis(self) -> None:
        self.analysis_generation += 1
        self.estimator.reset_position()
        self.received_path.clear()
        self.calculated_path.clear()
        self.received_path_cursor = 0
        self.calculated_path_cursor = 0
        self.completed_movements.clear()
        self.archived_movement_ids.clear()
        self.current_movement = None
        self.next_movement_id = 1
        self.ignored_short_movement_count = 0
        self.interrupted_movement_count = 0
        self.pending_finalization_ids.clear()
        self.finalization_requests.clear()
        self.finalization_error_count = 0
        self.last_finalization_error = None
        self.last_finalization_seconds = None
        self.received_origin = [0.0, 0.0, 0.0]
        self.received_origin_set = False
        self.last_coordinate_source_sequence = None
        self.trajectory_revision += 1
        if self.detector is not None:
            self.detector.reset_analysis()
            self.motion_state = "静止" if self.detector.calibrated else "PC校准中"
        else:
            self.motion_state = "等待移动"

    def orientation_snapshot(self) -> dict[str, Any]:
        quaternion = list(self.estimator.quaternion)
        return {
            "initialized": self.estimator.initialized,
            "quaternion_wxyz": quaternion,
            "euler_degrees": _quaternion_to_euler_degrees(quaternion),
            "body_x_world": _rotate_body_to_world(quaternion, [1.0, 0.0, 0.0]),
            "body_y_world": _rotate_body_to_world(quaternion, [0.0, 1.0, 0.0]),
            "body_z_world": _rotate_body_to_world(quaternion, [0.0, 0.0, 1.0]),
            "position_m": list(self.estimator.position),
            "velocity_mps": list(self.estimator.velocity),
            "yaw_reference": "relative",
        }

    def session_tremor_average(self) -> dict[str, Any]:
        valid = [
            movement.tremor
            for movement in self.completed_movements
            if movement.tremor.get("valid")
        ]
        frequencies = [
            float(item["dominant_frequency_hz"])
            for item in valid
            if item.get("dominant_frequency_hz") is not None
        ]
        intensities = [
            float(item["intensity_dps"])
            for item in valid
            if item.get("intensity_dps") is not None
        ]
        return {
            "valid_movement_count": min(len(frequencies), len(intensities)),
            "total_movement_count": len(self.completed_movements),
            "mean_frequency_hz": (
                float(statistics.fmean(frequencies)) if frequencies else None
            ),
            "mean_intensity_dps": (
                float(statistics.fmean(intensities)) if intensities else None
            ),
            "aggregation": "已完成移动的有效FFT主频/主频峰值幅值算术平均",
        }

    def movement_detail(self, movement_id: int) -> dict[str, Any] | None:
        for movement in self.completed_movements:
            if movement.movement_id == movement_id:
                if movement.high_frequency_released:
                    return None
                detail = movement.detail_snapshot()
                detail["session_tremor_average"] = self.session_tremor_average()
                detail["analysis_mode"] = self.mode
                detail["analysis_mode_label"] = ANALYSIS_MODE_LABELS[self.mode]
                return detail
        return None

    def movement_export(self, movement_id: int, export_format: str) -> str | None:
        for movement in self.completed_movements:
            if movement.movement_id == movement_id:
                if movement.high_frequency_released:
                    return None
                session = self.session_tremor_average()
                if export_format == "csv":
                    return movement.export_csv(session)
                if export_format == "json":
                    return movement.export_json(session)
                raise ValueError("不支持的导出格式")
        return None

    def finalization_snapshot(self) -> dict[str, Any]:
        pending_ids = sorted(self.pending_finalization_ids)
        cached = [
            movement for movement in self.completed_movements
            if not movement.high_frequency_released
        ]
        return {
            "pending_count": len(pending_ids),
            "pending_movement_ids": pending_ids,
            "latest_pending_movement_id": pending_ids[-1] if pending_ids else None,
            "worker_count": FINALIZATION_WORKERS,
            "error_count": self.finalization_error_count,
            "last_error": self.last_finalization_error,
            "last_elapsed_seconds": self.last_finalization_seconds,
            "execution": "background_worker",
            "detail_cache_count": len(cached),
            "detail_cache_samples": sum(len(item.sample_records) for item in cached),
            "detail_cache_limit": RECENT_MOVEMENT_DETAIL_LIMIT,
            "detail_cache_sample_budget": RECENT_MOVEMENT_DETAIL_SAMPLE_BUDGET,
        }

    @staticmethod
    def _path_delta(
        path: deque,
        cursor: int,
        after: int | None,
        reset: bool,
    ) -> dict[str, Any]:
        points = list(path)
        window_start = max(0, cursor - len(points))
        requested = window_start if reset or after is None else max(0, int(after))
        truncated = requested < window_start or requested > cursor
        if truncated:
            requested = window_start
        start = max(requested, window_start) - window_start
        return {
            "points": points[start:],
            "cursor": cursor,
            "truncated": truncated,
        }

    def live_snapshot(
        self,
        trajectory_revision: int | None = None,
        received_after: int | None = None,
        calculated_after: int | None = None,
    ) -> dict[str, Any]:
        reset = trajectory_revision != self.trajectory_revision
        return {
            "motion_state": self.motion_state,
            "movement_active": self.current_movement is not None,
            "finalization": self.finalization_snapshot(),
            "trajectory_revision": self.trajectory_revision,
            "movement_filter": {
                "minimum_seconds": self.minimum_movement_seconds,
                "ignored_short_count": self.ignored_short_movement_count,
                "rule": "移动结束后，时长小于阈值的候选段不记录、不编号、不参与FFT和会话统计",
            },
            "movement_id": (
                self.current_movement.movement_id
                if self.current_movement is not None
                else None
            ),
            "orientation": self.orientation_snapshot(),
            "calculated_position_m": (
                list(self.calculated_path[-1])
                if self.calculated_path
                else list(self.estimator.position)
            ),
            "received_position_m": (
                list(self.received_path[-1])
                if self.received_path
                else [0.0, 0.0, 0.0]
            ),
            "trajectory_delta": {
                "reset": reset,
                "received": self._path_delta(
                    self.received_path,
                    self.received_path_cursor,
                    received_after,
                    reset,
                ),
                "calculated": self._path_delta(
                    self.calculated_path,
                    self.calculated_path_cursor,
                    calculated_after,
                    reset,
                ),
            },
            "position_semantics": {
                "default_display": "relative_shape",
                "pc_metric_reliable": False,
                "metric_mode_label": "惯导米制估算（实验）",
                "relative_mode_label": "相对形状（无量纲）",
                "note": "IMU双积分无法长期保证真实米制距离；完成段仅做零末速度漂移修正。",
            },
        }

    def snapshot(self) -> dict[str, Any]:
        current = (
            self.current_movement.snapshot(self.motion_state)
            if self.current_movement is not None
            else None
        )
        detector_snapshot: dict[str, Any] | None = None
        if self.detector is not None:
            detector_snapshot = {
                "state": self.detector.state,
                "calibration_samples": self.detector.calibration_count,
                "calibration_required": PC_CALIBRATION_SAMPLES,
                "motion_confirm_samples": PC_START_CONFIRM_SAMPLES,
                "stop_confirm_samples": PC_STOP_CONFIRM_SAMPLES,
            }
        return {
            "motion_state": self.motion_state,
            "finalization": self.finalization_snapshot(),
            "trajectory_revision": self.trajectory_revision,
            "interrupted_movement_count": self.interrupted_movement_count,
            "movement_filter": {
                "minimum_seconds": self.minimum_movement_seconds,
                "ignored_short_count": self.ignored_short_movement_count,
                "rule": "移动结束后，时长小于阈值的候选段不记录、不编号、不参与FFT和会话统计",
            },
            "current_movement": current,
            "completed_movements": [
                movement.snapshot("已完成") for movement in self.completed_movements
            ],
            "completed_movement_count": len(self.completed_movements),
            "session_tremor_average": self.session_tremor_average(),
            "received_path": list(self.received_path),
            "calculated_path": list(self.calculated_path),
            "pc_detector": detector_snapshot,
            "orientation": self.orientation_snapshot(),
            "position_semantics": {
                "default_display": "relative_shape",
                "pc_metric_reliable": False,
                "metric_mode_label": "惯导米制估算（实验）",
                "relative_mode_label": "相对形状（无量纲）",
                "note": "IMU双积分无法长期保证真实米制距离；完成段仅做零末速度漂移修正。",
            },
        }


@dataclass
class DeviceState:
    device_id: int
    block_id: str = ""
    mapping_version: str = ""
    minimum_movement_seconds: float = DEFAULT_MINIMUM_MOVEMENT_SECONDS
    accel_history: deque = field(
        default_factory=lambda: deque(maxlen=SIGNAL_HISTORY)
    )
    gyro_history: deque = field(
        default_factory=lambda: deque(maxlen=SIGNAL_HISTORY)
    )
    last_seen: float = 0.0
    last_arrival_monotonic: float = 0.0
    first_tick: int | None = None
    clock_anchor_tick: int | None = None
    clock_anchor_epoch: float | None = None
    last_packet_sequence: int | None = None
    last_sample_sequence: int | None = None
    last_continuity_sample_sequence: int | None = None
    last_sample_tick: int | None = None
    current_session_id: int | None = None
    packet_count: int = 0
    sample_count: int = 0
    lost_packets: int = 0
    duplicate_packet_count: int = 0
    out_of_order_packet_count: int = 0
    continuity_gap_count: int = 0
    discontinuity_count: int = 0
    last_discontinuity_reason: str | None = None
    last_discontinuity_epoch: float | None = None
    source: str = "waiting"
    last_flags: int = 0
    coordinate_flags: int = 0
    analysis_mode: str = ANALYSIS_MODE_DEVICE
    analyses: dict[str, AnalysisPipeline] = field(init=False)

    def __post_init__(self) -> None:
        self.analyses = {
            ANALYSIS_MODE_DEVICE: AnalysisPipeline(
                ANALYSIS_MODE_DEVICE,
                device_id=self.device_id,
                block_id=self.block_id,
                mapping_version=self.mapping_version,
                minimum_movement_seconds=self.minimum_movement_seconds,
            ),
            ANALYSIS_MODE_PC: AnalysisPipeline(
                ANALYSIS_MODE_PC,
                device_id=self.device_id,
                block_id=self.block_id,
                mapping_version=self.mapping_version,
                minimum_movement_seconds=self.minimum_movement_seconds,
            ),
        }

    def set_block_mapping(self, block_id: str, mapping_version: str) -> None:
        self.block_id = block_id
        self.mapping_version = mapping_version
        for analysis in self.analyses.values():
            analysis.set_block_mapping(block_id, mapping_version)

    def take_finalization_requests(self) -> list[MovementFinalizationRequest]:
        requests: list[MovementFinalizationRequest] = []
        for analysis in self.analyses.values():
            requests.extend(analysis.take_finalization_requests())
        return requests

    def apply_finalization_result(
        self,
        result: MovementFinalizationResult,
    ) -> None:
        analysis = self.analyses.get(result.request.mode)
        if analysis is not None:
            analysis.apply_finalization_result(result)

    def set_analysis_mode(self, mode: str) -> None:
        if mode not in ANALYSIS_MODES:
            raise ValueError("未知分析模式")
        self.analysis_mode = mode

    def set_minimum_movement_seconds(self, value: float) -> int:
        self.minimum_movement_seconds = float(value)
        removed = 0
        for analysis in self.analyses.values():
            removed += analysis.set_minimum_movement_seconds(value)
        return removed

    def clear_analysis(self) -> None:
        self.accel_history.clear()
        self.gyro_history.clear()
        for analysis in self.analyses.values():
            analysis.clear_analysis()
        self.first_tick = None
        self.clock_anchor_tick = None
        self.clock_anchor_epoch = None

    def _break_analysis_segment(
        self,
        reason: str,
        event_epoch: float,
        mark_offline: bool = False,
    ) -> None:
        for analysis in self.analyses.values():
            analysis.break_segment(reason)
        self.accel_history.clear()
        self.gyro_history.clear()
        self.first_tick = None
        self.clock_anchor_tick = None
        self.clock_anchor_epoch = None
        self.discontinuity_count += 1
        self.last_discontinuity_reason = reason
        self.last_discontinuity_epoch = event_epoch
        if mark_offline:
            self.last_seen = 0.0
            self.last_arrival_monotonic = 0.0
            self.source = "waiting"

    def transport_discontinuity(self, reason: str) -> None:
        has_stream_state = (
            self.current_session_id is not None
            or self.last_seen > 0.0
            or self.last_packet_sequence is not None
            or any(
                analysis.current_movement is not None
                or analysis.estimator.initialized
                for analysis in self.analyses.values()
            )
        )
        if not has_stream_state:
            return
        self._break_analysis_segment(reason, time.time(), mark_offline=True)
        self.last_packet_sequence = None
        self.last_sample_sequence = None
        self.last_continuity_sample_sequence = None
        self.last_sample_tick = None
        self.current_session_id = None

    def reset_packet_counters(self) -> None:
        self.packet_count = 0
        self.lost_packets = 0
        self.duplicate_packet_count = 0
        self.out_of_order_packet_count = 0
        self.continuity_gap_count = 0
        self.last_packet_sequence = None
        self.last_continuity_sample_sequence = None
        self.last_sample_tick = None

    def _reset_stream_continuity(self) -> None:
        """Forget sequence anchors before accepting a new stream segment."""
        self.last_packet_sequence = None
        self.last_sample_sequence = None
        self.last_continuity_sample_sequence = None
        self.last_sample_tick = None

    def process(self, frame: MotionFrame) -> bool:
        frame_epoch = time.time()
        frame_arrival_monotonic = time.monotonic()
        if not frame.samples:
            return False
        full_motion_frame = frame.source in {
            "motion-v1-100b",
            "motion-v2-100b",
        }
        discontinuity_reason: str | None = None

        if full_motion_frame:
            reset_stream_continuity = False
            if (
                self.last_arrival_monotonic > 0.0
                and frame_arrival_monotonic - self.last_arrival_monotonic
                > MAX_HOST_ARRIVAL_GAP_SECONDS
            ):
                discontinuity_reason = "host_arrival_gap"
                reset_stream_continuity = True

            if (
                self.source in {"motion-v1-100b", "motion-v2-100b"}
                and frame.source != self.source
            ):
                discontinuity_reason = "stream_format_changed"
                reset_stream_continuity = True

            # A reboot can reuse a session ID on older firmware.  Once the
            # host has observed a real input gap (or a protocol transition),
            # the next frame is the start of a new segment and must not be
            # rejected against stale packet/sample sequence anchors.
            if reset_stream_continuity:
                self._reset_stream_continuity()

            if self.current_session_id is None:
                self.current_session_id = frame.session_id
            elif frame.session_id != self.current_session_id:
                self.current_session_id = frame.session_id
                self.last_packet_sequence = None
                self.last_continuity_sample_sequence = None
                self.last_sample_tick = None
                discontinuity_reason = "session_changed"

            if self.last_packet_sequence is not None:
                if frame.packet_sequence == self.last_packet_sequence:
                    self.duplicate_packet_count += 1
                    return False
                expected_packet = (
                    self.last_packet_sequence + 1
                ) & UINT16_MASK
                packet_gap = (
                    frame.packet_sequence - expected_packet
                ) & UINT16_MASK
                if 0 < packet_gap < 0x8000:
                    self.lost_packets += packet_gap
                    discontinuity_reason = (
                        discontinuity_reason or "packet_gap"
                    )
                elif packet_gap >= 0x8000:
                    self.out_of_order_packet_count += 1
                    return False

            first_sample = frame.samples[0]
            if self.last_continuity_sample_sequence is not None:
                expected_sample = (
                    self.last_continuity_sample_sequence + 1
                ) & UINT32_MASK
                sample_gap = (
                    first_sample.sequence - expected_sample
                ) & UINT32_MASK
                if 0 < sample_gap < 0x80000000:
                    discontinuity_reason = (
                        discontinuity_reason or "sample_gap"
                    )
                elif sample_gap >= 0x80000000:
                    self.out_of_order_packet_count += 1
                    return False

            if self.last_sample_tick is not None:
                sample_gap_seconds = _tick_seconds(
                    first_sample.tick_32k,
                    self.last_sample_tick,
                )
                if (
                    sample_gap_seconds <= 0.0
                    or sample_gap_seconds
                    > MAX_CONTINUOUS_SAMPLE_GAP_SECONDS
                ):
                    discontinuity_reason = (
                        discontinuity_reason or "sample_time_gap"
                    )

        if discontinuity_reason is not None:
            if discontinuity_reason != "session_changed":
                self.continuity_gap_count += 1
            self._break_analysis_segment(
                discontinuity_reason,
                frame_epoch,
            )

        last_sample_tick = frame.samples[-1].tick_32k
        if self.clock_anchor_tick is None or self.clock_anchor_epoch is None:
            self.clock_anchor_tick = last_sample_tick
            self.clock_anchor_epoch = frame_epoch
        timeline_epoch = self.clock_anchor_epoch + _tick_seconds(
            last_sample_tick, self.clock_anchor_tick
        )
        # A reconnect/session reset can make the device tick unrelated to the old
        # anchor.  Re-anchor only for a large wall-clock mismatch; normal serial
        # jitter must not change segment start/end timestamps.
        if abs(timeline_epoch - frame_epoch) > 5.0:
            self.clock_anchor_tick = last_sample_tick
            self.clock_anchor_epoch = frame_epoch
            timeline_epoch = frame_epoch
        if full_motion_frame:
            self.last_packet_sequence = frame.packet_sequence
            self.last_arrival_monotonic = frame_arrival_monotonic
        self.packet_count += 1
        self.last_seen = frame_epoch
        self.source = frame.source
        self.last_flags = frame.flags
        self.coordinate_flags = frame.coordinate_flags
        sparse = frame.source == "esp32-diagnostic-s0"

        # Both pipelines receive every sample, so switching modes never loses
        # history and does not require replaying serial data.
        self.analyses[ANALYSIS_MODE_DEVICE].process_device(
            frame, sparse, timeline_epoch
        )
        self.analyses[ANALYSIS_MODE_PC].process_pc(
            frame, sparse, timeline_epoch
        )

        for sample in frame.samples:
            if self.first_tick is None:
                self.first_tick = sample.tick_32k
            relative_time = _tick_seconds(sample.tick_32k, self.first_tick)
            accel, gyro_dps = InertialEstimator.measurements(sample)
            self.accel_history.append(
                [relative_time, accel[0], accel[1], accel[2]]
            )
            self.gyro_history.append(
                [relative_time, gyro_dps[0], gyro_dps[1], gyro_dps[2]]
            )
            self.last_sample_sequence = sample.sequence
            self.sample_count += 1
        if full_motion_frame:
            self.last_continuity_sample_sequence = frame.samples[-1].sequence
            self.last_sample_tick = frame.samples[-1].tick_32k
        return True

    def snapshot(self) -> dict[str, Any]:
        age = time.time() - self.last_seen if self.last_seen else None
        active = self.analyses[self.analysis_mode].snapshot()
        device_analysis = self.analyses[ANALYSIS_MODE_DEVICE]
        pc_analysis = self.analyses[ANALYSIS_MODE_PC]
        return {
            "device_id": self.device_id,
            "block_id": self.block_id,
            "mapping_version": self.mapping_version,
            "online": age is not None and age < 3.0,
            "age_seconds": age,
            "source": self.source,
            "packet_count": self.packet_count,
            "sample_count": self.sample_count,
            "lost_packets": self.lost_packets,
            "duplicate_packet_count": self.duplicate_packet_count,
            "out_of_order_packet_count": self.out_of_order_packet_count,
            "continuity_gap_count": self.continuity_gap_count,
            "discontinuity_count": self.discontinuity_count,
            "last_discontinuity_reason": self.last_discontinuity_reason,
            "last_discontinuity_epoch": self.last_discontinuity_epoch,
            "session_id": self.current_session_id,
            "last_packet_sequence": self.last_packet_sequence,
            "last_sample_sequence": self.last_sample_sequence,
            "flags": self.last_flags,
            "coordinate_flags": self.coordinate_flags,
            "device_reference_state": _device_state_label(
                self.coordinate_flags
            ),
            "device_mode_state": device_analysis.motion_state,
            "pc_mode_state": pc_analysis.motion_state,
            "analysis_mode": self.analysis_mode,
            "analysis_mode_label": ANALYSIS_MODE_LABELS[self.analysis_mode],
            "analysis_modes": [
                {"value": mode, "label": ANALYSIS_MODE_LABELS[mode]}
                for mode in ANALYSIS_MODES
            ],
            "accel": list(self.accel_history),
            "gyro": list(self.gyro_history),
            **active,
        }

    def summary_snapshot(self) -> dict[str, Any]:
        """Return the lightweight device facts used by inventory views."""
        age = time.time() - self.last_seen if self.last_seen else None
        active = self.analyses[self.analysis_mode]
        return {
            "device_id": self.device_id,
            "block_id": self.block_id,
            "mapping_version": self.mapping_version,
            "online": age is not None and age < 3.0,
            "age_seconds": age,
            "source": self.source,
            "packet_count": self.packet_count,
            "sample_count": self.sample_count,
            "lost_packets": self.lost_packets,
            "duplicate_packet_count": self.duplicate_packet_count,
            "out_of_order_packet_count": self.out_of_order_packet_count,
            "continuity_gap_count": self.continuity_gap_count,
            "discontinuity_count": self.discontinuity_count,
            "last_discontinuity_reason": self.last_discontinuity_reason,
            "session_id": self.current_session_id,
            "last_sample_sequence": self.last_sample_sequence,
            "analysis_mode": self.analysis_mode,
            "motion_state": active.motion_state,
        }

    def live_snapshot(
        self,
        trajectory_revision: int | None = None,
        received_after: int | None = None,
        calculated_after: int | None = None,
    ) -> dict[str, Any]:
        age = time.time() - self.last_seen if self.last_seen else None
        active = self.analyses[self.analysis_mode]
        latest_accel = (
            list(self.accel_history[-1][1:])
            if self.accel_history
            else [0.0, 0.0, 0.0]
        )
        latest_gyro = (
            list(self.gyro_history[-1][1:])
            if self.gyro_history
            else [0.0, 0.0, 0.0]
        )
        return {
            "device_id": self.device_id,
            "block_id": self.block_id,
            "mapping_version": self.mapping_version,
            "online": age is not None and age < 3.0,
            "age_seconds": age,
            "source": self.source,
            "last_sample_sequence": self.last_sample_sequence,
            "session_id": self.current_session_id,
            "discontinuity_count": self.discontinuity_count,
            "last_discontinuity_reason": self.last_discontinuity_reason,
            "analysis_mode": self.analysis_mode,
            "analysis_mode_label": ANALYSIS_MODE_LABELS[self.analysis_mode],
            "device_reference_state": _device_state_label(
                self.coordinate_flags
            ),
            "accel_mps2": latest_accel,
            "gyro_dps": latest_gyro,
            "accel_sample": (
                list(self.accel_history[-1])
                if self.accel_history
                else None
            ),
            "gyro_sample": (
                list(self.gyro_history[-1])
                if self.gyro_history
                else None
            ),
            **active.live_snapshot(
                trajectory_revision,
                received_after,
                calculated_after,
            ),
        }


    def movement_detail(
        self, movement_id: int, mode: str | None = None
    ) -> dict[str, Any] | None:
        selected_mode = mode or self.analysis_mode
        if selected_mode not in ANALYSIS_MODES:
            raise ValueError("未知分析模式")
        return self.analyses[selected_mode].movement_detail(movement_id)

    def movement_export(
        self, movement_id: int, export_format: str, mode: str | None = None
    ) -> str | None:
        selected_mode = mode or self.analysis_mode
        if selected_mode not in ANALYSIS_MODES:
            raise ValueError("未知分析模式")
        return self.analyses[selected_mode].movement_export(
            movement_id, export_format
        )

    def mark_movement_archived(self, movement_id: int, mode: str) -> int:
        if mode not in ANALYSIS_MODES:
            raise ValueError("未知分析模式")
        return self.analyses[mode].mark_movement_archived(movement_id)

    def set_task_collection_active(self, active: bool) -> int:
        return sum(
            analysis.set_task_collection_active(active)
            for analysis in self.analyses.values()
        )


class MotionStore:
    def __init__(
        self,
        device_count: int = 10,
        default_analysis_mode: str = ANALYSIS_MODE_DEVICE,
        minimum_movement_seconds: float = DEFAULT_MINIMUM_MOVEMENT_SECONDS,
        settings_path: str | Path | None = None,
        mapping_path: str | Path | None = None,
    ) -> None:
        if default_analysis_mode not in ANALYSIS_MODES:
            raise ValueError("未知默认分析模式")
        self._lock = threading.Lock()
        self._completed_finalizations: queue.SimpleQueue[
            MovementFinalizationResult
        ] = queue.SimpleQueue()
        self._settings_path = Path(settings_path) if settings_path else None
        self._mapping_path = Path(mapping_path) if mapping_path else None
        self._mapping_version, self._block_mapping = self._load_block_mapping(
            device_count
        )
        self._minimum_movement_seconds = self._load_minimum_movement_seconds(
            minimum_movement_seconds
        )
        self._devices = {
            device_id: DeviceState(
                device_id=device_id,
                block_id=self._block_mapping[device_id],
                mapping_version=self._mapping_version,
                minimum_movement_seconds=self._minimum_movement_seconds,
                analysis_mode=default_analysis_mode,
            )
            for device_id in range(1, device_count + 1)
        }

    def _load_block_mapping(
        self,
        device_count: int,
    ) -> tuple[str, dict[int, str]]:
        fallback = {
            device_id: (
                chr(ord("A") + device_id - 1)
                if device_id <= 26
                else str(device_id)
            )
            for device_id in range(1, device_count + 1)
        }
        if self._mapping_path is None:
            return "default-device-order-v1", fallback
        try:
            payload = json.loads(self._mapping_path.read_text(encoding="utf-8"))
            version = str(payload["mapping_version"]).strip()
            entries = payload["devices"]
            mapping = {
                int(entry["device_id"]): str(entry["block_id"]).strip()
                for entry in entries
            }
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"积木/nRF 映射配置无效：{exc}") from exc
        expected_ids = set(range(1, device_count + 1))
        if not version:
            raise ValueError("积木/nRF 映射配置缺少 mapping_version")
        if set(mapping) != expected_ids:
            raise ValueError(
                f"积木/nRF 映射必须完整包含 device_id 1～{device_count}"
            )
        block_ids = list(mapping.values())
        if any(not block_id for block_id in block_ids):
            raise ValueError("积木序号不能为空")
        if len(set(block_ids)) != len(block_ids):
            raise ValueError("一个积木序号不能同时映射到多个 nRF 设备")
        return version, mapping

    def _load_minimum_movement_seconds(self, fallback: float) -> float:
        value = float(fallback)
        if self._settings_path is not None and self._settings_path.exists():
            try:
                payload = json.loads(self._settings_path.read_text(encoding="utf-8"))
                value = float(payload.get("minimum_movement_seconds", value))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                value = float(fallback)
        return max(0.0, min(MAXIMUM_MOVEMENT_FILTER_SECONDS, value))

    def _save_settings(self) -> None:
        if self._settings_path is None:
            return
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._settings_path.with_suffix(
            self._settings_path.suffix + ".tmp"
        )
        temporary.write_text(
            json.dumps(
                {"minimum_movement_seconds": self._minimum_movement_seconds},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self._settings_path)

    def _submit_finalization(
        self,
        request: MovementFinalizationRequest,
    ) -> None:
        try:
            future = _FINALIZATION_EXECUTOR.submit(
                _run_movement_finalization,
                request,
            )
        except RuntimeError as exc:  # executor shutdown during interpreter exit
            self._completed_finalizations.put(
                MovementFinalizationResult(
                    request=request,
                    elapsed_seconds=0.0,
                    error=f"RuntimeError: {exc}",
                )
            )
            return
        future.add_done_callback(
            lambda completed, queued=request: self._queue_finalization_result(
                completed,
                queued,
            )
        )

    def _queue_finalization_result(
        self,
        future: Future[MovementFinalizationResult],
        request: MovementFinalizationRequest,
    ) -> None:
        try:
            result = future.result()
        except Exception as exc:  # pragma: no cover - defensive callback boundary
            result = MovementFinalizationResult(
                request=request,
                elapsed_seconds=0.0,
                error=f"{type(exc).__name__}: {exc}",
            )
        self._completed_finalizations.put(result)

    def _drain_finalizations_locked(self) -> int:
        applied = 0
        while True:
            try:
                result = self._completed_finalizations.get_nowait()
            except queue.Empty:
                break
            state = self._devices.get(result.request.device_id)
            if state is not None:
                state.apply_finalization_result(result)
            applied += 1
        return applied

    def wait_for_finalizations(self, timeout_seconds: float = 5.0) -> bool:
        """Wait helper for tests, orderly shutdown and explicit batch exports."""
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            with self._lock:
                self._drain_finalizations_locked()
                pending = sum(
                    len(analysis.pending_finalization_ids)
                    for state in self._devices.values()
                    for analysis in state.analyses.values()
                )
            if pending == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)

    def block_mapping_snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._drain_finalizations_locked()
            return {
                "mapping_version": self._mapping_version,
                "editable_source": (
                    str(self._mapping_path) if self._mapping_path else None
                ),
                "devices": [
                    {
                        "device_id": device_id,
                        "block_id": self._block_mapping[device_id],
                    }
                    for device_id in sorted(self._block_mapping)
                ],
            }

    def devices_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            self._drain_finalizations_locked()
            return [
                self._devices[device_id].summary_snapshot()
                for device_id in sorted(self._devices)
            ]

    def update_block_mapping(
        self,
        devices: list[dict[str, Any]],
        mapping_version: str,
    ) -> dict[str, Any]:
        version = str(mapping_version).strip()
        if not version:
            raise ValueError("积木/nRF 映射配置缺少 mapping_version")
        try:
            mapping = {
                int(entry["device_id"]): str(entry["block_id"]).strip().upper()
                for entry in devices
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"积木/nRF 映射配置无效：{exc}") from exc
        expected_ids = set(self._devices)
        if set(mapping) != expected_ids or len(devices) != len(expected_ids):
            raise ValueError(
                f"积木/nRF 映射必须完整包含 device_id 1～{len(expected_ids)}"
            )
        allowed_blocks = {chr(ord("A") + index) for index in range(len(expected_ids))}
        if set(mapping.values()) != allowed_blocks:
            last_block = chr(ord("A") + len(expected_ids) - 1)
            raise ValueError(f"积木编号必须完整且唯一地使用 A～{last_block}")

        with self._lock:
            self._drain_finalizations_locked()
            if self._mapping_path is not None:
                payload = {
                    "mapping_version": version,
                    "status": "configured_from_vue",
                    "description": "由 Vue 设备状态页面维护；历史移动保留开始时的映射版本。",
                    "devices": [
                        {"device_id": device_id, "block_id": mapping[device_id]}
                        for device_id in sorted(mapping)
                    ],
                }
                temporary = self._mapping_path.with_suffix(
                    self._mapping_path.suffix + ".tmp"
                )
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(self._mapping_path)
            self._mapping_version = version
            self._block_mapping = dict(mapping)
            for device_id, block_id in mapping.items():
                self._devices[device_id].set_block_mapping(block_id, version)
            return {
                "mapping_version": self._mapping_version,
                "editable_source": (
                    str(self._mapping_path) if self._mapping_path else None
                ),
                "devices": [
                    {
                        "device_id": device_id,
                        "block_id": self._block_mapping[device_id],
                    }
                    for device_id in sorted(self._block_mapping)
                ],
            }

    def settings_snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._drain_finalizations_locked()
            return {
                "minimum_movement_seconds": self._minimum_movement_seconds,
                "maximum_allowed_seconds": MAXIMUM_MOVEMENT_FILTER_SECONDS,
                "default_seconds": DEFAULT_MINIMUM_MOVEMENT_SECONDS,
                "scope": "所有设备和两种分析模式",
            }

    def set_minimum_movement_seconds(self, value: float) -> dict[str, Any]:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("最短有效移动时长必须是有限数字")
        if value < 0.0 or value > MAXIMUM_MOVEMENT_FILTER_SECONDS:
            raise ValueError(
                f"最短有效移动时长必须在 0～{MAXIMUM_MOVEMENT_FILTER_SECONDS:g} 秒之间"
            )
        with self._lock:
            self._drain_finalizations_locked()
            self._minimum_movement_seconds = value
            removed = sum(
                state.set_minimum_movement_seconds(value)
                for state in self._devices.values()
            )
            self._save_settings()
            return {
                "minimum_movement_seconds": value,
                "removed_existing_records_across_pipelines": removed,
                "scope": "所有设备和两种分析模式",
            }

    def process(self, frame: MotionFrame) -> bool:
        if frame.device_id not in self._devices:
            return False
        requests: list[MovementFinalizationRequest]
        with self._lock:
            self._drain_finalizations_locked()
            state = self._devices[frame.device_id]
            processed = state.process(frame)
            requests = state.take_finalization_requests()
        for request in requests:
            self._submit_finalization(request)
        return processed

    def transport_discontinuity(self, reason: str) -> None:
        with self._lock:
            self._drain_finalizations_locked()
            for state in self._devices.values():
                state.transport_discontinuity(reason)

    def reset_analysis(self, device_id: int) -> bool:
        with self._lock:
            self._drain_finalizations_locked()
            state = self._devices.get(device_id)
            if state is None:
                return False
            state.clear_analysis()
        return True

    def reset_packet_counters(self, device_id: int) -> bool:
        with self._lock:
            self._drain_finalizations_locked()
            state = self._devices.get(device_id)
            if state is None:
                return False
            state.reset_packet_counters()
        return True

    def set_analysis_mode(self, device_id: int, mode: str) -> bool:
        with self._lock:
            self._drain_finalizations_locked()
            state = self._devices.get(device_id)
            if state is None:
                return False
            state.set_analysis_mode(mode)
        return True

    def snapshot(self, device_id: int) -> dict[str, Any]:
        with self._lock:
            self._drain_finalizations_locked()
            selected = self._devices.get(device_id, self._devices[1])
            devices = [
                {
                    "device_id": state.device_id,
                    "block_id": state.block_id,
                    "mapping_version": state.mapping_version,
                    "online": (
                        state.last_seen > 0
                        and time.time() - state.last_seen < 3.0
                    ),
                    "packet_count": state.packet_count,
                    "analysis_mode": state.analysis_mode,
                }
                for state in self._devices.values()
            ]
            result = selected.snapshot()
            result["devices"] = devices
            return result

    def movement_detail(
        self,
        device_id: int,
        movement_id: int,
        mode: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            self._drain_finalizations_locked()
            state = self._devices.get(device_id)
            if state is None:
                return None
            return state.movement_detail(movement_id, mode)

    def movement_export(
        self,
        device_id: int,
        movement_id: int,
        export_format: str,
        mode: str | None = None,
    ) -> str | None:
        with self._lock:
            self._drain_finalizations_locked()
            state = self._devices.get(device_id)
            if state is None:
                return None
            return state.movement_export(movement_id, export_format, mode)

    def mark_movement_archived(
        self,
        device_id: int,
        movement_id: int,
        mode: str,
    ) -> int:
        """Release older detail only after TaskService confirms persistence."""
        with self._lock:
            self._drain_finalizations_locked()
            state = self._devices.get(int(device_id))
            if state is None:
                return 0
            return state.mark_movement_archived(int(movement_id), str(mode))

    def set_task_collection_active(self, active: bool) -> int:
        """Switch every pipeline between task-safe and rolling-cache modes."""
        with self._lock:
            self._drain_finalizations_locked()
            return sum(
                state.set_task_collection_active(active)
                for state in self._devices.values()
            )

    def live_snapshot(
        self,
        device_id: int,
        trajectory_revision: int | None = None,
        received_after: int | None = None,
        calculated_after: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._drain_finalizations_locked()
            selected = self._devices.get(device_id, self._devices[1])
            return selected.live_snapshot(
                trajectory_revision,
                received_after,
                calculated_after,
            )
