"""V1.4 motion performance scoring engine for the block wall-passing task.

Implements:
- Configurable V1.3 multi-feature prior weights and reference bounds/target intervals
- Fixed intra-group disabled-metric weight renormalization (no ad-hoc reallocation)
- V1.4 scoring-only gap quality gate & 6-axis linear interpolation:
  * Formal replay path (``repaired`` / ``observed``) using raw continuous stream + initial state
  * Legacy archive fallback path (``repaired_estimate`` / ``previously_interpolated`` / ``observed``)
- Two-stage equal-weight task aggregation over actually participating valid blocks
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path
import json
import math
import statistics
from typing import Any

import motion_model as mm


BLOCK_ORDER = tuple("ABCDEFGHIJ")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "defaults" / "scoring_config.json"
UINT32_MASK = 0xFFFFFFFF
RECOVERABLE_GAP_REASONS = frozenset({
    "host_arrival_gap",
    "sample_gap",
    "sample_time_gap",
})


class ScoringValidationError(ValueError):
    """Raised when a movement fails quality or feature validity checks."""

    def __init__(self, code: str, message: str, *, quality: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.quality = quality


def clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _require_finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ScoringValidationError(
            "missing_or_nonfinite_feature",
            f"特征 {field_name} 缺失或非有限值",
        )
    return float(value)


def compute_feature_subscore(
    value: Any,
    bounds: list[float] | tuple[float, ...],
    direction: str,
    *,
    field_name: str = "feature",
) -> float:
    """Compute a 0..100 single-feature subscore following V1.4 section 04."""
    x = _require_finite_number(value, field_name)
    if direction == "target":
        if len(bounds) != 3:
            raise ValueError(f"目标区间参数 {field_name} 必须包含 [a, b, d]")
        low, high, decay = float(bounds[0]), float(bounds[1]), float(bounds[2])
        if high < low or decay <= 0.0:
            raise ValueError(f"目标区间参数 {field_name} 无效：{bounds}")
        deviation = max(low - x, 0.0, x - high)
        return 100.0 * clip01(1.0 - deviation / decay)

    if len(bounds) != 2:
        raise ValueError(f"上下限参数 {field_name} 必须包含 [L, U]")
    lower, upper = float(bounds[0]), float(bounds[1])
    if upper <= lower:
        raise ValueError(f"上下限参数 {field_name} 必须满足 U > L：{bounds}")
    if direction == "positive":
        return 100.0 * clip01((x - lower) / (upper - lower))
    if direction == "negative":
        return 100.0 * clip01((upper - x) / (upper - lower))
    raise ValueError(f"未知评分方向：{direction}")


def _combine_weighted_group(
    group_name: str,
    group_specs: dict[str, dict[str, Any]],
    feature_values: dict[str, Any],
    parameters: dict[str, list[float]],
    feature_scores_out: dict[str, float],
) -> float:
    enabled_items = [
        (metric_name, spec)
        for metric_name, spec in group_specs.items()
        if bool(spec.get("enabled", True))
    ]
    if not enabled_items:
        raise ValueError(f"维度 {group_name} 没有启用的计分指标")
    weight_sum = sum(float(spec["weight"]) for _, spec in enabled_items)
    if weight_sum <= 0.0 or not math.isfinite(weight_sum):
        raise ValueError(f"维度 {group_name} 启用指标权重之和无效")

    total = 0.0
    for metric_name, spec in enabled_items:
        if metric_name not in parameters:
            raise ScoringValidationError(
                "uncalibrated_parameter",
                f"缺少指标 {metric_name} 的参考参数配置",
            )
        subscore = compute_feature_subscore(
            feature_values.get(metric_name),
            parameters[metric_name],
            str(spec["direction"]),
            field_name=metric_name,
        )
        feature_scores_out[metric_name] = subscore
        total += (float(spec["weight"]) / weight_sum) * subscore
    return total


class ScoringConfig:
    """Validated scoring configuration loaded from JSON."""

    def __init__(self, payload: dict[str, Any], *, source_path: Path | None = None) -> None:
        self._payload = deepcopy(payload)
        self.source_path = source_path
        self.scoring_version = str(payload.get("scoring_version", "v1.4")).strip()
        self.weights_version = str(payload.get("weights_version", "v1.3-prior")).strip()
        self.calibration_version = str(
            payload.get("calibration_version", "v1.4-provisional-trial")
        ).strip()
        self.model_id = str(payload.get("model_id", "jimu-wall-scoring-v1.4")).strip()
        self.calibrated = bool(payload.get("calibrated", False))
        self.provisional = bool(payload.get("provisional", not self.calibrated))
        self.quality = dict(payload.get("quality", {}))
        self.drop_penalty = dict(payload.get("drop_penalty", {}))
        self.intra_group_weights: dict[str, dict[str, dict[str, Any]]] = deepcopy(
            payload.get("intra_group_weights", {})
        )
        self.blocks: dict[str, dict[str, Any]] = deepcopy(payload.get("blocks", {}))
        self.hole_overrides: dict[str, dict[str, Any]] = deepcopy(
            payload.get("hole_overrides", {})
        )
        self._validate()

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> "ScoringConfig":
        target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("评分配置文件根节点必须是对象")
        return cls(payload, source_path=target)

    def with_threshold_ratio(self, threshold_ratio: float) -> "ScoringConfig":
        ratio = float(threshold_ratio)
        if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
            raise ValueError("缺失补帧比例阈值必须在 0～1 之间")
        cloned = deepcopy(self._payload)
        cloned.setdefault("quality", {})["missing_ratio_threshold"] = ratio
        return ScoringConfig(cloned, source_path=self.source_path)

    @property
    def model_version(self) -> str:
        return f"{self.model_id}:{self.calibration_version}"

    @property
    def missing_ratio_threshold(self) -> float:
        return float(self.quality.get("missing_ratio_threshold", 0.10))

    @property
    def required_analysis_mode(self) -> str | None:
        mode = self.quality.get("required_analysis_mode", "pc")
        return str(mode).strip() if mode else None

    def block_names(self) -> dict[str, str]:
        return {
            block_id: str(spec.get("name", block_id))
            for block_id, spec in self.blocks.items()
        }

    def resolve_block_spec(
        self,
        block_id: str,
        hole_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_block = str(block_id or "").strip().upper()
        if normalized_block not in self.blocks:
            raise ScoringValidationError(
                "unknown_block_id",
                f"未知积木编号：{block_id!r}",
            )
        spec = deepcopy(self.blocks[normalized_block])
        spec["block_id"] = normalized_block
        spec["protocol_key"] = normalized_block
        if hole_id:
            normalized_hole = str(hole_id).strip()
            override_key = f"{normalized_block}:{normalized_hole}"
            if override_key in self.hole_overrides:
                override = self.hole_overrides[override_key]
                if "parameters" in override:
                    spec["parameters"].update(deepcopy(override["parameters"]))
                if "posture_weights" in override:
                    spec["posture_weights"] = deepcopy(override["posture_weights"])
                if "provisional" in override:
                    spec["provisional"] = bool(override["provisional"])
                spec["protocol_key"] = override_key
            spec["hole_id"] = normalized_hole
        else:
            spec["hole_id"] = None
        return spec

    def reference_parameters_snapshot(self) -> dict[str, Any]:
        return {
            block_id: deepcopy(spec.get("parameters", {}))
            for block_id, spec in sorted(self.blocks.items())
        }

    def _validate(self) -> None:
        ratio = self.missing_ratio_threshold
        if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
            raise ValueError("quality.missing_ratio_threshold 必须在 0～1 之间")
        for dim in ("T", "P", "V", "Q"):
            if dim not in self.intra_group_weights:
                raise ValueError(f"缺少组内权重配置：{dim}")
        for block_id, spec in self.blocks.items():
            weights = spec.get("dimension_weights", {})
            dims = ("T", "P", "V", "Q", "R")
            if any(dim not in weights for dim in dims):
                raise ValueError(f"积木 {block_id} 缺少五维权重")
            total = sum(float(weights[dim]) for dim in dims)
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"积木 {block_id} 五维权重之和必须为 1，当前为 {total}")


def extract_movement_features(detail: dict[str, Any]) -> dict[str, Any]:
    speed = detail.get("speed") if isinstance(detail.get("speed"), dict) else {}
    tremor = detail.get("tremor") if isinstance(detail.get("tremor"), dict) else {}
    return {
        "moving_seconds": detail.get("moving_seconds"),
        "pause_total_seconds": detail.get("pause_total_seconds"),
        "pause_count": detail.get("pause_count"),
        "p05": speed.get("p05"),
        "max": speed.get("max"),
        "mean": speed.get("mean"),
        "median": speed.get("median"),
        "std": speed.get("std"),
        "intensity_dps": tremor.get("intensity_dps"),
        "band_rms_dps": tremor.get("band_rms_dps"),
        "dominant_frequency_hz": tremor.get("dominant_frequency_hz"),
        "angle": detail.get("spatial_rotation_degrees"),
        "rotation_count": detail.get("rotation_count"),
        "flip_count": detail.get("flip_count"),
        "drop_count": detail.get("drop_count"),
        "shake_count": detail.get("shake_count", 0),
    }


def score_movement_features(
    detail: dict[str, Any],
    config: ScoringConfig,
    *,
    block_id: str | None = None,
    hole_id: str | None = None,
) -> dict[str, Any]:
    """Calculate V1.3/V1.4 feature subscores, dimension scores and movement score."""
    resolved_block_id = str(block_id or detail.get("block_id") or "").strip().upper()
    block_spec = config.resolve_block_spec(resolved_block_id, hole_id=hole_id)
    if block_spec.get("uncalibrated"):
        raise ScoringValidationError(
            "block_uncalibrated",
            f"积木 {block_spec['name']}（{resolved_block_id}）尚未标定参考区间",
        )

    speed = detail.get("speed") if isinstance(detail.get("speed"), dict) else {}
    tremor = detail.get("tremor") if isinstance(detail.get("tremor"), dict) else {}
    v_specs = config.intra_group_weights["V"]
    q_specs = config.intra_group_weights["Q"]

    if bool(v_specs.get("p05", {}).get("enabled", True)):
        p05_count = speed.get("p05_sample_count")
        if not isinstance(p05_count, (int, float)) or p05_count <= 0:
            raise ScoringValidationError(
                "speed_p05_unavailable",
                "有效核心速度样本不足，无法计算 P05 速度",
            )

    tremor_required = any(
        bool(q_specs.get(name, {}).get("enabled", True))
        for name in ("intensity_dps", "band_rms_dps")
    )
    if tremor_required and not bool(tremor.get("valid")):
        reason = str(tremor.get("reason") or "FFT 震颤特征不可用")
        raise ScoringValidationError("tremor_unavailable", f"震颤特征无效：{reason}")

    features = extract_movement_features(detail)
    parameters = block_spec["parameters"]
    feature_scores: dict[str, float] = {}

    dim_t = _combine_weighted_group(
        "T", config.intra_group_weights["T"], features, parameters, feature_scores
    )
    dim_p = _combine_weighted_group(
        "P", config.intra_group_weights["P"], features, parameters, feature_scores
    )
    dim_v = _combine_weighted_group(
        "V", config.intra_group_weights["V"], features, parameters, feature_scores
    )
    dim_q = _combine_weighted_group(
        "Q", config.intra_group_weights["Q"], features, parameters, feature_scores
    )
    dim_r = _combine_weighted_group(
        "R", block_spec["posture_weights"], features, parameters, feature_scores
    )

    dimensions = {"T": dim_t, "P": dim_p, "V": dim_v, "Q": dim_q, "R": dim_r}
    dim_weights = block_spec["dimension_weights"]
    base_score = sum(float(dim_weights[dim]) * dimensions[dim] for dim in ("T", "P", "V", "Q", "R"))

    drop_count = _require_finite_number(features.get("drop_count"), "drop_count")
    if drop_count < 0:
        raise ScoringValidationError("invalid_drop_count", "掉落次数不能为负数")
    if bool(config.drop_penalty.get("enabled", True)):
        per_drop = float(config.drop_penalty.get("per_drop", 10.0))
        max_penalty = float(config.drop_penalty.get("max_penalty", 30.0))
        penalty = min(max_penalty, per_drop * drop_count)
    else:
        penalty = 0.0

    final_score = max(0.0, base_score - penalty)
    return {
        "block_id": resolved_block_id,
        "block_name": block_spec["name"],
        "category": block_spec["category"],
        "protocol_key": block_spec["protocol_key"],
        "provisional": bool(block_spec.get("provisional", config.provisional)),
        "provisional_note": block_spec.get("provisional_note"),
        "features": {
            key: value
            for key, value in features.items()
            if key not in ("dominant_frequency_hz", "shake_count")
        },
        "recorded_only_features": {
            "dominant_frequency_hz": features.get("dominant_frequency_hz"),
            "shake_count": features.get("shake_count"),
        },
        "feature_scores": feature_scores,
        "dimensions": dimensions,
        "dimension_weights": {dim: float(dim_weights[dim]) for dim in ("T", "P", "V", "Q", "R")},
        "base_score": base_score,
        "drop_penalty": penalty,
        "score": final_score,
    }


def repair_legacy_archive_movement(
    detail: dict[str, Any],
    rows: list[dict[str, Any]],
    config: ScoringConfig,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Offline V1.4 gap check and 6-axis linear bridge estimate for legacy archives.

    Used only when continuous stationary samples and initial detector/estimator
    replay state were not archived; clearly marks results as ``repaired_estimate``
    rather than claiming a full replay repair.
    """
    if not rows or bool(detail.get("samples_truncated")) or detail.get("state") != "已完成":
        raise ScoringValidationError("incomplete_archive", "移动归档样本不完整或被截断")
    required_mode = config.required_analysis_mode
    actual_mode = str(detail.get("analysis_mode") or "pc")
    if required_mode and actual_mode != required_mode:
        raise ScoringValidationError(
            "unsupported_analysis_mode",
            f"仅支持 {required_mode} 独立判定模式评分，当前为 {actual_mode}",
        )
    tremor = detail.get("tremor") if isinstance(detail.get("tremor"), dict) else {}
    speed = detail.get("speed") if isinstance(detail.get("speed"), dict) else {}
    if not bool(tremor.get("valid")):
        reason = str(tremor.get("reason") or "FFT 震颤特征不可用")
        raise ScoringValidationError("invalid_required_feature", f"震颤特征无效：{reason}")
    if int(speed.get("p05_sample_count") or 0) <= 0:
        raise ScoringValidationError("invalid_required_feature", "有效核心速度样本不足")

    nominal_dt = float(config.quality.get("nominal_sample_period_seconds", 0.01))
    clock_min_tol = float(config.quality.get("clock_tolerance_min_seconds", 0.03))
    clock_ratio_tol = float(config.quality.get("clock_tolerance_ratio", 0.02))
    max_gap_s = config.quality.get("max_continuous_gap_seconds")

    span = round(float(detail.get("segment_seconds") or 0.0) / nominal_dt)
    if span <= 0:
        raise ScoringValidationError("invalid_duration", "移动采样跨度无效")

    events = [
        event
        for event in detail.get("gap_events", [])
        if isinstance(event, dict) and not bool(event.get("interpolated", False))
    ]
    boundaries = [
        (index, prev_row, next_row)
        for index, (prev_row, next_row) in enumerate(zip(rows, rows[1:]), 1)
        if int(prev_row.get("segment_index", 0)) != int(next_row.get("segment_index", 0))
    ]

    first_seg = int(rows[0].get("segment_index", 0))
    last_seg = int(rows[-1].get("segment_index", 0))
    active_events: list[dict[str, Any]] = []
    for event in events:
        seg_after = int(event.get("segment_after", -1))
        reason = str(event.get("reason", ""))
        rec_missing = int(event.get("missing_samples") or 0)
        if seg_after <= first_seg or seg_after > last_seg:
            # A host_arrival_gap with 0 missing samples recorded during the
            # pre-start or 3.0s post-stop stationary hold window has no lost
            # motion samples inside the movement interval.
            if reason == "host_arrival_gap" and rec_missing == 0:
                continue
            raise ScoringValidationError(
                "missing_gap_boundary",
                "采样间断位于首尾边界之外，缺少有效插值端点",
            )
        active_events.append(event)
    events = active_events

    matched_event_ids: set[int] = set()
    holes: dict[int, tuple[int, int]] = {}
    audit: list[dict[str, Any]] = []
    for index, prev_row, next_row in boundaries:
        seg_before = int(prev_row.get("segment_index", 0))
        seg_after = int(next_row.get("segment_index", 0))
        matched = [
            (pos, ev)
            for pos, ev in enumerate(events)
            if seg_before < int(ev.get("segment_after", -1)) <= seg_after
        ]
        if not matched:
            raise ScoringValidationError("unmatched_gap_events", "分段边界缺少对应的间断事件记录")
        for pos, ev in matched:
            matched_event_ids.add(pos)
            reason = str(ev.get("reason", ""))
            if reason not in RECOVERABLE_GAP_REASONS:
                raise ScoringValidationError(
                    "session_discontinuity",
                    f"发生不可恢复的会话或连接中断：{reason}",
                )

        delta_seq = (int(next_row["sequence"]) - int(prev_row["sequence"])) & UINT32_MASK
        ticks = (int(next_row["tick_32k"]) - int(prev_row["tick_32k"])) & UINT32_MASK
        seconds = ticks / mm.TICK_HZ
        tolerance = max(clock_min_tol, clock_ratio_tol * seconds)
        if not 0 < delta_seq < 0x80000000 or abs(seconds - nominal_dt * delta_seq) > tolerance:
            raise ScoringValidationError(
                "inconsistent_sample_clock",
                "间断两端采样序号与 32.768 kHz 采样时钟不一致（疑似设备重启或时钟异常）",
            )
        seq_span_missing = delta_seq - 1
        has_host_gap = any(str(ev.get("reason", "")) == "host_arrival_gap" for _, ev in matched)
        recorded_missing_sum = sum(int(ev.get("missing_samples") or 0) for _, ev in matched)
        if not has_host_gap and recorded_missing_sum > 0:
            # Exported samples.json omits ZUPT stationary frames, so delta_seq - 1
            # equals recorded_missing_sum + unarchived_stationary_frames (>= recorded_missing_sum).
            if recorded_missing_sum > seq_span_missing:
                raise ScoringValidationError(
                    "gap_count_mismatch",
                    "间断事件记录的缺失帧数与归档边界序号不一致",
                )
            missing_n = recorded_missing_sum
            ticks_for_bridge = round(missing_n * nominal_dt * mm.TICK_HZ)
        else:
            missing_n = seq_span_missing
            ticks_for_bridge = ticks
        if max_gap_s is not None and missing_n * nominal_dt > float(max_gap_s) + 1e-6:
            raise ScoringValidationError(
                "continuous_gap_too_long",
                f"单次连续缺口过长（{missing_n * nominal_dt:.2f}s）",
            )
        holes[index] = (missing_n, ticks_for_bridge)
        last_ev = matched[-1][1]
        audit.append({
            "reason": last_ev.get("reason"),
            "segment_before": seg_before,
            "segment_after": seg_after,
            "boundary_missing_estimate": missing_n,
            "device_span_seconds": seconds,
            "host_gap_seconds": last_ev.get("duration_seconds"),
        })

    if len(matched_event_ids) != len(events):
        raise ScoringValidationError("unmatched_gap_events", "存在无法匹配到归档分段边界的间断事件")

    old_interpolated = sum(bool(row.get("interpolated", False)) for row in rows)
    new_count = sum(n for n, _ in holes.values())
    total_missing = new_count + old_interpolated
    ratio = total_missing / span
    status = (
        "repaired_estimate"
        if new_count > 0
        else "observed"
        if old_interpolated == 0
        else "previously_interpolated"
    )
    quality: dict[str, Any] = {
        "mode": "legacy_archive_estimate",
        "expected_frames_approx": span,
        "recorded_motion_samples": len(rows),
        "previous_interpolated_frames": old_interpolated,
        "new_interpolated_frames": new_count,
        "missing_frames_total": total_missing,
        "missing_ratio": ratio,
        "missing_ratio_estimate": ratio,
        "threshold_ratio": config.missing_ratio_threshold,
        "gap_events": audit,
        "velocity_indirect_interpolation_impact": bool(total_missing > 0),
        "status": status,
    }
    if total_missing > 0 and ratio >= config.missing_ratio_threshold:
        raise ScoringValidationError(
            "missing_ratio_exceeded",
            f"缺失帧比例 {ratio * 100:.2f}% 达到或超过阈值 {config.missing_ratio_threshold * 100:.1f}%",
            quality={**quality, "status": "invalid"},
        )

    repaired_detail = deepcopy(detail)
    synthetic: list[dict[str, Any]] = []
    for index, (missing_n, ticks) in holes.items():
        if missing_n <= 0:
            continue
        prev_row, next_row = rows[index - 1], rows[index]
        dt = ticks / mm.TICK_HZ / (missing_n + 1)
        for offset in range(1, missing_n + 1):
            frac = offset / (missing_n + 1)
            synthetic.append({
                "sequence": (int(prev_row["sequence"]) + offset) & UINT32_MASK,
                "tick_32k": (int(prev_row["tick_32k"]) + round(ticks * frac)) & UINT32_MASK,
                "dt_s": dt,
                "interpolated": True,
                "gap_segment_after": int(next_row.get("segment_index", 0)),
                "accel_mps2": [
                    float(x) + frac * (float(y) - float(x))
                    for x, y in zip(prev_row["accel_mps2"], next_row["accel_mps2"])
                ],
                "gyro_dps": [
                    float(x) + frac * (float(y) - float(x))
                    for x, y in zip(prev_row["gyro_dps"], next_row["gyro_dps"])
                ],
                "motion_state_assumption": "moving",
                "source": "scoring_only_linear_bridge",
            })

    added_moving_seconds = sum(float(item["dt_s"]) for item in synthetic)
    if new_count > 0:
        repaired_detail["moving_seconds"] = float(repaired_detail["moving_seconds"]) + added_moving_seconds
        repaired_detail["speed"] = mm._speed_statistics(rows)
        repaired_detail["tremor"] = mm._tremor_fft(rows)
        if not bool(repaired_detail["tremor"].get("valid")):
            raise ScoringValidationError(
                "tremor_unavailable_after_repair",
                "分段重算后连续真实样本不足以提取有效 FFT 震颤特征",
                quality={**quality, "status": "invalid"},
            )
        if int(repaired_detail["speed"].get("p05_sample_count") or 0) <= 0:
            raise ScoringValidationError(
                "speed_p05_unavailable_after_repair",
                "分段重算后有效核心速度样本不足",
                quality={**quality, "status": "invalid"},
            )
    quality["added_moving_seconds_estimate"] = added_moving_seconds
    return repaired_detail, quality, synthetic


def evaluate_movement(
    detail: dict[str, Any],
    config: ScoringConfig,
    *,
    samples_rows: list[dict[str, Any]] | None = None,
    scoring_input: dict[str, Any] | None = None,
    fallback_block_id: str | None = None,
    hole_id: str | None = None,
    include_synthetic_samples: bool = False,
) -> dict[str, Any]:
    """Evaluate quality, optional scoring-only gap repair, and V1.4 score for one movement."""
    block_id = str(detail.get("block_id") or fallback_block_id or "").strip().upper()
    block_name = config.block_names().get(block_id, block_id or "未知积木")
    movement_id = detail.get("movement_id")
    device_id = detail.get("device_id")
    analysis_mode = str(detail.get("analysis_mode") or "pc")

    result: dict[str, Any] = {
        "device_id": device_id,
        "block_id": block_id,
        "block_name": block_name,
        "movement_id": movement_id,
        "analysis_mode": analysis_mode,
        "valid": False,
        "score": None,
        "quality": None,
    }

    try:
        if scoring_input is not None:
            try:
                repaired_detail, quality, synthetic = mm.replay_pc_movement_for_scoring(
                    scoring_input,
                    detail,
                    threshold_ratio=config.missing_ratio_threshold,
                    required_analysis_mode=config.required_analysis_mode,
                    nominal_sample_period_seconds=float(
                        config.quality.get("nominal_sample_period_seconds", 0.01)
                    ),
                    clock_tolerance_min_seconds=float(
                        config.quality.get("clock_tolerance_min_seconds", 0.03)
                    ),
                    clock_tolerance_ratio=float(
                        config.quality.get("clock_tolerance_ratio", 0.02)
                    ),
                    max_continuous_gap_seconds=config.quality.get("max_continuous_gap_seconds"),
                )
            except mm.ScoringReplayError as exc:
                raise ScoringValidationError(exc.code, exc.message, quality=exc.quality) from exc
        else:
            if samples_rows is None:
                raise ScoringValidationError(
                    "missing_samples_archive",
                    "缺少移动采样归档数据，无法执行 V1.4 质量校验",
                )
            repaired_detail, quality, synthetic = repair_legacy_archive_movement(
                detail,
                samples_rows,
                config,
            )

        after_score = score_movement_features(
            repaired_detail,
            config,
            block_id=block_id,
            hole_id=hole_id,
        )
        before_score: dict[str, Any] | None = None
        try:
            before_score = score_movement_features(
                detail,
                config,
                block_id=block_id,
                hole_id=hole_id,
            )
        except ScoringValidationError:
            before_score = None

        result.update({
            "valid": True,
            "score": after_score["score"],
            "score_display": round(after_score["score"], 2),
            "provisional": after_score["provisional"],
            "provisional_note": after_score["provisional_note"],
            "quality": quality,
            "before": before_score,
            "after": after_score,
        })
        if include_synthetic_samples:
            result["scoring_only_inserted_samples"] = synthetic
    except ScoringValidationError as exc:
        result.update({
            "valid": False,
            "score": None,
            "score_display": None,
            "invalid_code": exc.code,
            "invalid_reason": exc.message,
            "error": exc.message,
            "quality": exc.quality or {
                "status": "invalid",
                "invalid_code": exc.code,
                "invalid_reason": exc.message,
            },
        })
    return result


def evaluate_task(
    movements_input: list[dict[str, Any]],
    config: ScoringConfig,
    *,
    task_status: str = "completed",
    include_synthetic_samples: bool = False,
) -> dict[str, Any]:
    """Aggregate movement scores into block scores C_k and task score S_task (V1.4 section 06).

    ``movements_input`` items contain:
      - ``reference`` (optional movement reference dict)
      - ``detail`` (dict or None)
      - ``samples_rows`` (list[dict] or None)
      - ``scoring_input`` (dict or None)
      - ``fallback_block_id`` (optional str)
      - ``hole_id`` (optional str)
    """
    block_names = config.block_names()
    evaluated_movements: list[dict[str, Any]] = []
    by_block_all: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_block_valid_scores: dict[str, list[float]] = defaultdict(list)
    invalid_movements: list[dict[str, Any]] = []
    repair_statuses: set[str] = set()

    for index, item in enumerate(movements_input, start=1):
        reference = item.get("reference") or {}
        detail = item.get("detail")
        fallback_block = str(
            item.get("fallback_block_id")
            or reference.get("block_id")
            or ""
        ).strip().upper()

        if not isinstance(detail, dict):
            block_id = fallback_block
            block_name = block_names.get(block_id, block_id or "未知积木")
            reason = "缺少移动详情归档文件（detail.json）"
            evaluated = {
                "task_movement_index": reference.get("task_movement_index", index),
                "device_id": reference.get("device_id"),
                "device_movement_index": reference.get("device_movement_index"),
                "block_id": block_id,
                "block_name": block_name,
                "movement_id": reference.get("movement_id"),
                "analysis_mode": reference.get("analysis_mode", "pc"),
                "valid": False,
                "score": None,
                "score_display": None,
                "invalid_code": "missing_detail_archive",
                "invalid_reason": reason,
                "error": reason,
                "quality": {
                    "status": "invalid",
                    "invalid_code": "missing_detail_archive",
                    "invalid_reason": reason,
                },
            }
        else:
            merged_detail = dict(detail)
            if "device_id" not in merged_detail and "device_id" in reference:
                merged_detail["device_id"] = reference["device_id"]
            if "analysis_mode" not in merged_detail and "analysis_mode" in reference:
                merged_detail["analysis_mode"] = reference["analysis_mode"]
            evaluated = evaluate_movement(
                merged_detail,
                config,
                samples_rows=item.get("samples_rows"),
                scoring_input=item.get("scoring_input"),
                fallback_block_id=fallback_block,
                hole_id=item.get("hole_id") or reference.get("hole_id"),
                include_synthetic_samples=include_synthetic_samples,
            )
            evaluated["task_movement_index"] = reference.get("task_movement_index", index)
            evaluated["device_movement_index"] = reference.get("device_movement_index")

        evaluated_movements.append(evaluated)
        b_id = str(evaluated.get("block_id") or "")
        if b_id:
            by_block_all[b_id].append(evaluated)
        if evaluated["valid"] and evaluated["score"] is not None:
            by_block_valid_scores[b_id].append(float(evaluated["score"]))
            q_status = str((evaluated.get("quality") or {}).get("status") or "observed")
            repair_statuses.add(q_status)
        else:
            invalid_movements.append({
                "task_movement_index": evaluated.get("task_movement_index", index),
                "device_id": evaluated.get("device_id"),
                "movement_id": evaluated.get("movement_id"),
                "block_id": b_id,
                "block_name": evaluated.get("block_name"),
                "code": evaluated.get("invalid_code", "invalid"),
                "reason": evaluated.get("invalid_reason", "无效移动"),
            })

    participated_blocks = [b for b in BLOCK_ORDER if b in by_block_all] + [
        b for b in sorted(by_block_all) if b not in BLOCK_ORDER
    ]
    unmoved_blocks = [b for b in BLOCK_ORDER if b not in by_block_all]

    block_scores: dict[str, float | None] = {}
    block_summaries: dict[str, dict[str, Any]] = {}
    for b_id in participated_blocks:
        all_items = by_block_all[b_id]
        valid_scores = by_block_valid_scores.get(b_id, [])
        mean_score = float(statistics.fmean(valid_scores)) if valid_scores else None
        block_scores[b_id] = mean_score
        invalid_reasons = [
            str(m.get("invalid_reason"))
            for m in all_items
            if not m.get("valid") and m.get("invalid_reason")
        ]
        provisional_flag = any(bool(m.get("provisional")) for m in all_items if m.get("valid"))
        block_summaries[b_id] = {
            "block_id": b_id,
            "block_name": block_names.get(b_id, b_id),
            "total_movements": len(all_items),
            "valid_movements": len(valid_scores),
            "score": mean_score,
            "score_display": round(mean_score, 2) if mean_score is not None else None,
            "provisional": provisional_flag,
            "invalid_reasons": invalid_reasons,
        }

    valid_block_values = [
        score for score in block_scores.values() if score is not None
    ]
    total_movements = len(evaluated_movements)
    valid_movements = sum(1 for m in evaluated_movements if m["valid"])
    participated_count = len(participated_blocks)
    scored_block_count = len(valid_block_values)

    raw_task_score = (
        float(statistics.fmean(valid_block_values))
        if valid_block_values
        else None
    )
    display_task_score = (
        round(raw_task_score, 2) if raw_task_score is not None else None
    )

    has_invalid = (
        valid_movements < total_movements
        or scored_block_count < participated_count
        or task_status == "abnormal"
    )
    if total_movements == 0:
        completeness = "empty"
        partial = False
        score_status = "pending"
    elif scored_block_count == 0:
        completeness = "invalid"
        partial = True
        score_status = "failed"
    elif has_invalid:
        completeness = "partial"
        partial = True
        score_status = "completed"
    else:
        completeness = "complete"
        partial = False
        score_status = "completed"

    provisional_used = config.provisional or any(
        bool(info.get("provisional")) for info in block_summaries.values()
    )

    explanations: list[str] = []
    if total_movements == 0:
        explanations.append("尚未检测到本任务中的完整移动，不生成评分。")
    elif display_task_score is None:
        explanations.append(
            f"实际参与 {participated_count}/10 种积木（共 {total_movements} 次移动），"
            "但有效可评分积木为 0，任务总分为空（null）。"
        )
    else:
        explanations.append(
            f"实际参与 {participated_count}/10 种积木，有效评分积木 {scored_block_count}/{participated_count} 种，"
            f"有效移动 {valid_movements}/{total_movements} 次；按同积木等权平均后再跨有效积木等权聚合。"
        )
        if partial:
            explanations.append("存在已移动但未通过质量/有效性判定的记录，本次任务结果标记为 partial。")
        if "repaired" in repair_statuses:
            explanations.append("部分移动存在低于阈值的采样缺口，已使用评分副本线性补帧重放（repaired），原始归档保持不变。")
        if "repaired_estimate" in repair_statuses or "previously_interpolated" in repair_statuses:
            explanations.append("部分历史移动缺少连续静止样本或起始惯导状态，按离线桥接估算评分（repaired_estimate）。")
        if provisional_used and not config.calibrated:
            explanations.append("当前参考上下限/目标区间为未独立标定的试行配置（provisional）。")
    for inv in invalid_movements:
        explanations.append(
            f"无效移动 #{inv['task_movement_index']}（{inv['block_name']} M{inv['movement_id']}）：{inv['reason']}"
        )

    return {
        "scoring_version": config.scoring_version,
        "weights_version": config.weights_version,
        "calibration_version": config.calibration_version,
        "model_version": config.model_version,
        "calibrated": config.calibrated,
        "provisional": provisional_used,
        "demo_only": False,
        "threshold_ratio": config.missing_ratio_threshold,
        "threshold_percent": round(config.missing_ratio_threshold * 100.0, 4),
        "completeness": completeness,
        "partial": partial,
        "score_status": score_status,
        "total_score": display_task_score,
        "total_score_raw": raw_task_score,
        "participation": {
            "total_block_types": len(BLOCK_ORDER),
            "participated_block_count": participated_count,
            "scored_block_count": scored_block_count,
            "total_movement_count": total_movements,
            "valid_movement_count": valid_movements,
            "participated_over_total": f"{participated_count}/{len(BLOCK_ORDER)}",
            "scored_over_participated": f"{scored_block_count}/{participated_count}",
            "valid_over_total_movements": f"{valid_movements}/{total_movements}",
        },
        "participated_blocks": [block_names.get(b, b) for b in participated_blocks],
        "participated_block_ids": participated_blocks,
        "unmoved_blocks": [block_names.get(b, b) for b in unmoved_blocks],
        "unmoved_block_ids": unmoved_blocks,
        "repair_statuses": sorted(repair_statuses),
        "block_scores": {
            b_id: score for b_id, score in block_scores.items() if score is not None
        },
        "block_scores_display": {
            b_id: round(score, 2) for b_id, score in block_scores.items() if score is not None
        },
        "blocks": block_summaries,
        "invalid_movements": invalid_movements,
        "explanation": explanations,
        "movements": evaluated_movements,
    }
