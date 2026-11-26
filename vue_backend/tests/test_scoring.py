from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import motion_model as mm
from motion_protocol import ImuSample, MotionFrame
from score_task_cli import score_single_task_zip_bytes
from scoring_engine import (
    DEFAULT_CONFIG_PATH,
    ScoringConfig,
    ScoringValidationError,
    compute_feature_subscore,
    evaluate_movement,
    evaluate_task,
    score_movement_features,
)
from task_service import TaskService


ACCEL_SCALE_MPS2 = mm.GRAVITY / mm.ACCEL_LSB_PER_G
GYRO_SCALE_DPS = 1.0 / mm.GYRO_LSB_PER_DPS
STEP_TICKS = round(mm.TICK_HZ * 0.01)


def _make_frame(
    device_id: int,
    packet_sequence: int,
    samples: list[ImuSample],
    *,
    session_id: int = 1,
    source: str = "motion-v2-100b",
) -> MotionFrame:
    return MotionFrame(
        device_id=device_id,
        packet_sequence=packet_sequence & 0xFFFF,
        session_id=session_id,
        flags=0,
        samples=tuple(samples),
        coordinate_source_sequence=samples[-1].sequence,
        position_mm=(0, 0, 0),
        coordinate_flags=0,
        source=source,
    )


def _sample(
    seq: int,
    tick: int,
    *,
    ax_mps2: float = 0.0,
    ay_mps2: float = 0.0,
    az_mps2: float = mm.GRAVITY,
    gx_dps: float = 0.0,
    gy_dps: float = 0.0,
    gz_dps: float = 0.0,
) -> ImuSample:
    return ImuSample(
        sequence=seq & 0xFFFFFFFF,
        tick_32k=tick & 0xFFFFFFFF,
        accel_raw=(
            round(ax_mps2 / ACCEL_SCALE_MPS2),
            round(ay_mps2 / ACCEL_SCALE_MPS2),
            round(az_mps2 / ACCEL_SCALE_MPS2),
        ),
        gyro_raw=(
            round(gx_dps / GYRO_SCALE_DPS),
            round(gy_dps / GYRO_SCALE_DPS),
            round(gz_dps / GYRO_SCALE_DPS),
        ),
    )


def _synthetic_detail(
    block_id: str = "G",
    *,
    movement_id: int = 1,
    device_id: int = 7,
    moving_seconds: float = 18.0,
    pause_total_seconds: float = 2.5,
    pause_count: int = 1,
    p05: float = 0.08,
    max_speed: float = 1.20,
    mean_speed: float = 0.45,
    median_speed: float = 0.40,
    std_speed: float = 0.28,
    intensity_dps: float = 8.0,
    band_rms_dps: float = 18.0,
    angle: float = 2160.0,
    rotation_count: int = 3,
    flip_count: int = 0,
    drop_count: int = 0,
    shake_count: int = 2,
) -> dict:
    return {
        "device_id": device_id,
        "block_id": block_id,
        "movement_id": movement_id,
        "analysis_mode": "pc",
        "state": "已完成",
        "samples_truncated": False,
        "segment_seconds": moving_seconds + pause_total_seconds,
        "moving_seconds": moving_seconds,
        "pause_total_seconds": pause_total_seconds,
        "pause_count": pause_count,
        "spatial_rotation_degrees": angle,
        "rotation_count": rotation_count,
        "flip_count": flip_count,
        "drop_count": drop_count,
        "shake_count": shake_count,
        "gap_events": [],
        "speed": {
            "p05": p05,
            "max": max_speed,
            "mean": mean_speed,
            "median": median_speed,
            "std": std_speed,
            "p05_sample_count": 120,
        },
        "tremor": {
            "valid": True,
            "dominant_frequency_hz": 5.5,
            "intensity_dps": intensity_dps,
            "band_rms_dps": band_rms_dps,
            "reason": None,
        },
    }


class ScoringEngineFormulaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ScoringConfig.from_file(DEFAULT_CONFIG_PATH)
        self.trial_config = ScoringConfig.from_file(
            DEFAULT_CONFIG_PATH.parent / "scoring_config_v14_trial.json"
        )

    def test_pdf_page5_thread_cylinder_worked_example_matches_exact_values(self) -> None:
        # PDF Page 5 example: Thread cylinder (G) with T=80, P=90, V=75, Q=85, R=92 -> B=85.85, 1 drop -> S=75.85
        example_kwargs = dict(
            moving_seconds=17.0,        # s-(17; [10, 45]) = 80
            pause_total_seconds=2.5,    # s-(2.5; [0, 25]) = 90
            pause_count=1.5,            # s-(1.5; [0, 15]) = 90 -> P = 90
            p05=0.1925,                 # s+(0.1925; [0.02, 0.25]) = 75
            max_speed=2.325,            # s+(2.325; [0.30, 3.0]) = 75
            mean_speed=1.15,            # s+(1.15; [0.10, 1.50]) = 75
            median_speed=1.15,          # s+(1.15; [0.10, 1.50]) = 75 -> V = 75
            std_speed=0.265,            # s-(0.265; [0.10, 1.20]) = 85
            intensity_dps=8.0,          # s-(8.0; [5, 25]) = 85
            band_rms_dps=19.0,          # s-(19.0; [10, 70]) = 85 -> Q = 85
            angle=2400.0,               # sR(2400; [1800, 3000, 1800]) = 100
            rotation_count=5.6,         # sR(5.6; [2, 5, 3]) = 80 -> R = 0.6*100 + 0.4*80 = 92
        )
        no_drop = score_movement_features(
            _synthetic_detail("G", drop_count=0, **example_kwargs),
            self.trial_config,
        )
        self.assertAlmostEqual(no_drop["dimensions"]["T"], 80.0, places=6)
        self.assertAlmostEqual(no_drop["dimensions"]["P"], 90.0, places=6)
        self.assertAlmostEqual(no_drop["dimensions"]["V"], 75.0, places=6)
        self.assertAlmostEqual(no_drop["dimensions"]["Q"], 85.0, places=6)
        self.assertAlmostEqual(no_drop["dimensions"]["R"], 92.0, places=6)
        self.assertAlmostEqual(no_drop["base_score"], 85.85, places=6)
        self.assertAlmostEqual(no_drop["score"], 85.85, places=6)

        one_drop = score_movement_features(
            _synthetic_detail("G", drop_count=1, **example_kwargs),
            self.trial_config,
        )
        self.assertAlmostEqual(one_drop["drop_penalty"], 10.0, places=6)
        self.assertAlmostEqual(one_drop["score"], 75.85, places=6)

        capped_drop = score_movement_features(
            _synthetic_detail("G", drop_count=5, **example_kwargs),
            self.trial_config,
        )
        self.assertAlmostEqual(capped_drop["drop_penalty"], 30.0, places=6)
        self.assertAlmostEqual(capped_drop["score"], 55.85, places=6)

    def test_all_ten_blocks_including_provisional_h_and_j_score_cleanly(self) -> None:
        for idx, block_id in enumerate("ABCDEFGHIJ", start=1):
            detail = _synthetic_detail(
                block_id,
                device_id=idx,
                moving_seconds=6.0 if block_id in "ABCDEFI" else 15.0,
                angle=120.0 if block_id in "ABCDEFI" else 400.0 if block_id == "H" else 2000.0,
                rotation_count=0 if block_id in "ABCDEFI" else 3,
                flip_count=0 if block_id != "H" else 2,
            )
            scored = score_movement_features(detail, self.config)
            self.assertEqual(scored["block_id"], block_id)
            self.assertGreaterEqual(scored["score"], 0.0)
            self.assertLessEqual(scored["score"], 100.0)
            if block_id == "J":
                self.assertEqual(set(scored["feature_scores"]).intersection({"rotation_count", "flip_count"}), set())
            if block_id == "H":
                self.assertTrue(scored["provisional"])
                self.assertIn("flip_count", scored["feature_scores"])

    def test_missing_or_nan_feature_never_substitutes_zero(self) -> None:
        bad = _synthetic_detail("A", device_id=1, moving_seconds=4.0, angle=100.0, rotation_count=0)
        bad["speed"]["median"] = None
        with self.assertRaises(ScoringValidationError) as ctx:
            score_movement_features(bad, self.config)
        self.assertEqual(ctx.exception.code, "missing_or_nonfinite_feature")

        nan_tremor = _synthetic_detail("A", device_id=1, moving_seconds=4.0, angle=100.0, rotation_count=0)
        nan_tremor["tremor"]["valid"] = False
        nan_tremor["tremor"]["reason"] = "移动时间过短"
        with self.assertRaises(ScoringValidationError) as ctx2:
            score_movement_features(nan_tremor, self.config)
        self.assertEqual(ctx2.exception.code, "tremor_unavailable")

    def test_fixed_disabled_submetric_renormalizes_group_weights_and_hole_override_works(self) -> None:
        payload = json.loads(
            (DEFAULT_CONFIG_PATH.parent / "scoring_config_v14_trial.json").read_text(encoding="utf-8")
        )
        payload["intra_group_weights"]["Q"]["band_rms_dps"]["enabled"] = False
        payload["drop_penalty"]["enabled"] = False
        payload["hole_overrides"]["A:圆孔"] = {
            "parameters": {"angle": [30.0, 180.0]},
            "provisional": False,
        }
        custom_cfg = ScoringConfig(payload)
        detail = _synthetic_detail("A", device_id=1, moving_seconds=5.0, angle=105.0, rotation_count=0, drop_count=2)
        without_hole = score_movement_features(detail, custom_cfg)
        with_hole = score_movement_features(detail, custom_cfg, hole_id="圆孔")

        self.assertNotIn("band_rms_dps", without_hole["feature_scores"])
        expected_q = (0.40 / 0.76) * without_hole["feature_scores"]["std"] + (0.36 / 0.76) * without_hole["feature_scores"]["intensity_dps"]
        self.assertAlmostEqual(without_hole["dimensions"]["Q"], expected_q, places=6)
        self.assertEqual(without_hole["drop_penalty"], 0.0)
        self.assertEqual(without_hole["protocol_key"], "A")
        self.assertEqual(with_hole["protocol_key"], "A:圆孔")
        self.assertAlmostEqual(with_hole["feature_scores"]["angle"], 50.0, places=5)
        self.assertAlmostEqual(
            compute_feature_subscore(105.0, [90.0, 360.0], "negative"),
            without_hole["feature_scores"]["angle"],
            places=5,
        )

    def test_norm_score_scaling_stretches_monotonically_and_validates_anchors(self) -> None:
        grid = [float(i) for i in range(0, 101)]
        scaled_mov = [self.config.scale_movement_score(v) for v in grid]
        scaled_task = [self.config.scale_task_score(v) for v in grid]
        self.assertTrue(all(a <= b + 1e-9 for a, b in zip(scaled_mov, scaled_mov[1:])))
        self.assertTrue(all(a <= b + 1e-9 for a, b in zip(scaled_task, scaled_task[1:])))
        self.assertAlmostEqual(scaled_task[0], 0.0, places=6)
        self.assertAlmostEqual(scaled_task[-1], 100.0, places=6)

        bad_payload = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        bad_payload["score_scaling"] = {
            "enabled": True,
            "task_anchors": [[0.0, 0.0], [70.0, 85.0], [80.0, 75.0], [100.0, 100.0]],
        }
        with self.assertRaises(ValueError):
            ScoringConfig(bad_payload)


class ScoringPipelineReplayAndTaskTests(unittest.TestCase):
    def _feed_pc_movement(
        self,
        store: mm.MotionStore,
        device_id: int,
        *,
        start_seq: int = 1,
        start_tick: int = 1000,
        start_pkt: int = 1,
        start_now: float | None = None,
        start_mono: float = 1_000.0,
        moving_frames: int = 200,
        pause_after: int | None = None,
        pause_frames: int = 120,
        drop_sequences: set[int] | None = None,
        session_switch_at: int | None = None,
        clock_jump_at: int | None = None,
        host_delay_at: int | None = None,
    ) -> tuple[int, int, int, float, float]:
        drop_sequences = drop_sequences or set()
        seq = start_seq
        tick = start_tick
        pkt = start_pkt
        fake_now = [start_now if start_now is not None else mm.time.time() + 1.0]
        fake_mono = [start_mono]

        def send_sample(sample: ImuSample, *, session_id: int = 1, host_delay: float = 0.0) -> None:
            nonlocal pkt
            fake_now[0] += 0.01 + host_delay
            fake_mono[0] += 0.01 + host_delay
            frame = _make_frame(device_id, pkt, [sample], session_id=session_id)
            pkt += 1
            with patch.object(mm.time, "time", side_effect=lambda: fake_now[0]), patch.object(
                mm.time, "monotonic", side_effect=lambda: fake_mono[0]
            ):
                store.process(frame)

        # 1. Calibrate PC detector (120 stationary samples)
        for _ in range(120):
            send_sample(_sample(seq, tick))
            seq += 1
            tick += STEP_TICKS

        # 2. Active motion with optional internal pause and simulated wire drops
        current_session = 1
        for step in range(moving_frames):
            if pause_after is not None and step == pause_after:
                for _ in range(pause_frames):
                    send_sample(_sample(seq, tick), session_id=current_session)
                    seq += 1
                    tick += STEP_TICKS

            if session_switch_at is not None and step == session_switch_at:
                current_session = 2
            if clock_jump_at is not None and step == clock_jump_at:
                tick += round(mm.TICK_HZ * 0.50)

            delay = 1.6 if (host_delay_at is not None and step == host_delay_at) else 0.0
            angle_phase = 2.0 * math.pi * (step / 25.0)
            s = _sample(
                seq,
                tick,
                ax_mps2=1.4 * math.sin(angle_phase),
                ay_mps2=0.8 * math.cos(angle_phase),
                az_mps2=mm.GRAVITY + 0.5 * math.sin(2.0 * angle_phase),
                gx_dps=35.0 * math.sin(angle_phase) + 6.0 * math.sin(2.0 * math.pi * 6.0 * step * 0.01),
                gy_dps=20.0 * math.cos(angle_phase),
                gz_dps=45.0,
            )
            if seq not in drop_sequences:
                send_sample(s, session_id=current_session, host_delay=delay)
            seq += 1
            tick += STEP_TICKS

        # 3. Hold stationary >= 3.2s to finalize PC movement
        for _ in range(340):
            send_sample(_sample(seq, tick), session_id=current_session)
            seq += 1
            tick += STEP_TICKS

        store.wait_for_finalizations(timeout_seconds=5.0)
        return seq, tick, pkt, fake_now[0], fake_mono[0]

    def test_formal_replay_handles_zero_gap_pause_host_delay_and_sub10pct_gap_without_mutating_archive(self) -> None:
        config = ScoringConfig.from_file(DEFAULT_CONFIG_PATH)

        # Case 1: Clean movement with a 1.2s internal pause and a 1.6s host arrival delay (0 missing wire samples)
        store_clean = mm.MotionStore(device_count=10, default_analysis_mode=mm.ANALYSIS_MODE_PC)
        self._feed_pc_movement(
            store_clean,
            1,
            moving_frames=180,
            pause_after=90,
            pause_frames=120,
            host_delay_at=60,
        )
        detail_clean = store_clean.movement_detail(1, 1, "pc")
        scoring_in_clean = store_clean.movement_scoring_input(1, 1, "pc")
        self.assertIsNotNone(detail_clean)
        self.assertIsNotNone(scoring_in_clean)
        self.assertGreaterEqual(detail_clean["pause_count"], 1)

        evaluated_clean = evaluate_movement(detail_clean, config, scoring_input=scoring_in_clean)
        self.assertTrue(evaluated_clean["valid"])
        self.assertEqual(evaluated_clean["quality"]["status"], "observed")
        self.assertEqual(evaluated_clean["quality"]["missing_frames_total"], 0)

        # Case 2: 12 missing wire frames out of ~200 (~6% < 10%) -> repaired on copy, original export unchanged
        store_gap = mm.MotionStore(device_count=10, default_analysis_mode=mm.ANALYSIS_MODE_PC)
        dropped = set(range(170, 182))  # 12 frames (> live 5-frame interpolation cap)
        self._feed_pc_movement(store_gap, 1, moving_frames=200, drop_sequences=dropped)

        detail_before = store_gap.movement_detail(1, 1, "pc")
        json_before = store_gap.movement_export(1, 1, "json", "pc")
        csv_before = store_gap.movement_export(1, 1, "csv", "pc")
        scoring_in_gap = store_gap.movement_scoring_input(1, 1, "pc")

        evaluated_gap = evaluate_movement(
            detail_before,
            config,
            scoring_input=scoring_in_gap,
            include_synthetic_samples=True,
        )
        self.assertTrue(evaluated_gap["valid"])
        self.assertEqual(evaluated_gap["quality"]["status"], "repaired")
        self.assertEqual(evaluated_gap["quality"]["missing_frames_total"], 12)
        self.assertLess(evaluated_gap["quality"]["missing_ratio"], 0.10)
        self.assertEqual(len(evaluated_gap["scoring_only_inserted_samples"]), 12)

        # Original detail & exports in MotionStore are byte-for-byte identical
        self.assertEqual(store_gap.movement_detail(1, 1, "pc"), detail_before)
        self.assertEqual(store_gap.movement_export(1, 1, "json", "pc"), json_before)
        self.assertEqual(store_gap.movement_export(1, 1, "csv", "pc"), csv_before)

    def test_formal_replay_rejects_ge_10pct_session_switch_and_clock_jump(self) -> None:
        config = ScoringConfig.from_file(DEFAULT_CONFIG_PATH)

        # >= 10% missing (25 out of ~200 frames = 12.5%)
        store_high_loss = mm.MotionStore(device_count=10, default_analysis_mode=mm.ANALYSIS_MODE_PC)
        self._feed_pc_movement(
            store_high_loss, 1, moving_frames=200, drop_sequences=set(range(160, 185))
        )
        res_loss = evaluate_movement(
            store_high_loss.movement_detail(1, 1, "pc"),
            config,
            scoring_input=store_high_loss.movement_scoring_input(1, 1, "pc"),
        )
        self.assertFalse(res_loss["valid"])
        self.assertEqual(res_loss["invalid_code"], "missing_ratio_exceeded")

        # < 10% missing, but session_id changes mid-movement
        store_session = mm.MotionStore(device_count=10, default_analysis_mode=mm.ANALYSIS_MODE_PC)
        self._feed_pc_movement(store_session, 1, moving_frames=200, session_switch_at=100)
        res_session = evaluate_movement(
            store_session.movement_detail(1, 1, "pc"),
            config,
            scoring_input=store_session.movement_scoring_input(1, 1, "pc"),
        )
        self.assertFalse(res_session["valid"])
        self.assertEqual(res_session["invalid_code"], "session_discontinuity")

        # Clock jump with continuous sequence
        store_clock = mm.MotionStore(device_count=10, default_analysis_mode=mm.ANALYSIS_MODE_PC)
        self._feed_pc_movement(store_clock, 1, moving_frames=200, clock_jump_at=100)
        res_clock = evaluate_movement(
            store_clock.movement_detail(1, 1, "pc"),
            config,
            scoring_input=store_clock.movement_scoring_input(1, 1, "pc"),
        )
        self.assertFalse(res_clock["valid"])
        self.assertEqual(res_clock["invalid_code"], "inconsistent_sample_clock")

    def test_two_stage_equal_weight_aggregation_partial_and_null_cases(self) -> None:
        config = ScoringConfig.from_file(DEFAULT_CONFIG_PATH)
        mov_a1 = _synthetic_detail("A", device_id=1, movement_id=1, moving_seconds=4.0, angle=110.0, rotation_count=0)
        mov_a2 = _synthetic_detail("A", device_id=1, movement_id=2, moving_seconds=8.0, angle=200.0, rotation_count=1)
        mov_g1 = _synthetic_detail("G", device_id=7, movement_id=1)
        rows_ok = [{"sequence": i, "tick_32k": i * 328, "segment_index": 0, "interpolated": False, "accel_mps2": [0, 0, 9.8], "gyro_dps": [1, 2, 3]} for i in range(1, 101)]

        agg = evaluate_task(
            [
                {"reference": {"device_id": 1, "block_id": "A", "movement_id": 1}, "detail": mov_a1, "samples_rows": rows_ok},
                {"reference": {"device_id": 1, "block_id": "A", "movement_id": 2}, "detail": mov_a2, "samples_rows": rows_ok},
                {"reference": {"device_id": 7, "block_id": "G", "movement_id": 1}, "detail": mov_g1, "samples_rows": rows_ok},
            ],
            config,
        )
        self.assertEqual(agg["completeness"], "complete")
        self.assertFalse(agg["partial"])
        self.assertEqual(agg["participation"]["participated_over_total"], "2/10")
        self.assertEqual(agg["participation"]["scored_over_participated"], "2/2")
        self.assertEqual(agg["participation"]["valid_over_total_movements"], "3/3")
        self.assertEqual(len(agg["unmoved_block_ids"]), 8)
        expected_a = 0.5 * (agg["movements"][0]["score"] + agg["movements"][1]["score"])
        expected_task = 0.5 * (expected_a + agg["movements"][2]["score"])
        self.assertAlmostEqual(agg["block_scores"]["A"], expected_a, places=6)
        self.assertAlmostEqual(agg["total_score_raw"], expected_task, places=6)

        # Add an invalid movement on block I -> partial=True, total_score still equals mean(A, G)
        mov_i_bad = _synthetic_detail("I", device_id=9, movement_id=1, moving_seconds=3.0, angle=40.0, rotation_count=0)
        mov_i_bad["tremor"]["valid"] = False
        agg_partial = evaluate_task(
            [
                {"reference": {"device_id": 1, "block_id": "A", "movement_id": 1}, "detail": mov_a1, "samples_rows": rows_ok},
                {"reference": {"device_id": 9, "block_id": "I", "movement_id": 1}, "detail": mov_i_bad, "samples_rows": rows_ok},
            ],
            config,
        )
        self.assertEqual(agg_partial["completeness"], "partial")
        self.assertTrue(agg_partial["partial"])
        self.assertEqual(agg_partial["participation"]["participated_over_total"], "2/10")
        self.assertEqual(agg_partial["participation"]["scored_over_participated"], "1/2")
        self.assertEqual(len(agg_partial["invalid_movements"]), 1)
        self.assertAlmostEqual(agg_partial["total_score_raw"], agg["movements"][0]["score"], places=6)

        # Only invalid movement -> total_score is None
        agg_null = evaluate_task(
            [{"reference": {"device_id": 9, "block_id": "I", "movement_id": 1}, "detail": mov_i_bad, "samples_rows": rows_ok}],
            config,
        )
        self.assertIsNone(agg_null["total_score"])
        self.assertEqual(agg_null["completeness"], "invalid")
        self.assertEqual(agg_null["score_status"], "failed")

    def test_task_service_persists_scoring_input_across_ram_pruning_and_passes_cloud_archive_validation(self) -> None:
        from cloud_backend.archive import _validate as validate_cloud_archive
        from cloud_backend.config import Settings as CloudSettings

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = mm.MotionStore(device_count=10, default_analysis_mode=mm.ANALYSIS_MODE_PC)
            service = TaskService(
                root / "task_records.json",
                store.snapshot,
                device_ids=tuple(range(1, 11)),
                database_path=root / "motion.sqlite3",
                movement_root=root / "task_movements",
                movement_archived_callback=store.mark_movement_archived,
            )
            store.set_task_collection_active(True)
            started = service.start(1, "P100", "多积木含补帧测试")
            task_id = started["task_id"]

            # Generate 3 movements on device 1 (exceeding RECENT_MOVEMENT_DETAIL_LIMIT=2) + 1 movement with 8-frame gap on device 8 (block H)
            seq, tick, pkt = 1, 1000, 1
            now_cursor = mm.time.time() + 1.0
            mono_cursor = 1_000.0
            for _ in range(3):
                seq, tick, pkt, now_cursor, mono_cursor = self._feed_pc_movement(
                    store,
                    1,
                    start_seq=seq,
                    start_tick=tick,
                    start_pkt=pkt,
                    start_now=now_cursor,
                    start_mono=mono_cursor,
                    moving_frames=160,
                )
                service.current(1)  # Triggers archiving & RAM pruning of older movement!

            self._feed_pc_movement(
                store,
                8,
                start_seq=1,
                start_tick=1000,
                start_pkt=1,
                start_now=now_cursor,
                start_mono=mono_cursor,
                moving_frames=180,
                drop_sequences=set(range(150, 158)),
            )
            finished = service.finish(task_id)
            store.set_task_collection_active(False)

            self.assertIsNotNone(finished["total_score"])
            self.assertNotEqual(finished["score_model_version"], TaskService.TEMPORARY_SCORE_MODEL_VERSION)
            result = service.result(task_id)
            self.assertFalse(result["score"]["demo_only"])
            self.assertEqual(result["score"]["participation"]["participated_over_total"], "2/10")
            self.assertEqual(len(finished["movement_references"]), 4)
            self.assertTrue(all(ref.get("score") is not None for ref in finished["movement_references"]))
            self.assertEqual(finished["movement_references"][-1]["score_quality_status"], "repaired")

            # Export task ZIP: verify scoring_input.json is NOT inside ZIP, CLI scores it, and cloud_backend accepts it!
            archive_bytes = service.task_export_archive(task_id)
            self.assertIsNotNone(archive_bytes)
            zip_path = root / f"task_{task_id}.zip"
            zip_path.write_bytes(archive_bytes)
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
                self.assertFalse(any(name.endswith("scoring_input.json") for name in zf.namelist()))

            cloud_settings = CloudSettings(
                token="t" * 32,
                data_dir=root / "cloud_data",
                temp_dir=root / "cloud_tmp",
            )
            summary, members = validate_cloud_archive(zip_path, cloud_settings)
            self.assertEqual(summary["task_id"], task_id)
            self.assertAlmostEqual(summary["total_score"], finished["total_score"], places=2)
            self.assertIn("task.json", members)

            cli_report = score_single_task_zip_bytes(archive_bytes, ScoringConfig.from_file(DEFAULT_CONFIG_PATH))
            self.assertIsNotNone(cli_report["task_score_display"])

    def test_startup_recalculates_legacy_random_demo_task_without_modifying_archived_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_id = "T20261027-000001-DEMO"
            token = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:24]
            archive_dir = root / "task_movements" / token
            archive_dir.mkdir(parents=True, exist_ok=True)

            detail = _synthetic_detail("C", device_id=3, movement_id=1, moving_seconds=5.0, angle=140.0, rotation_count=0)
            detail["gap_events"] = [{"gap_index": 1, "reason": "sample_gap", "duration_seconds": 0.05, "missing_samples": 5, "interpolated": False, "segment_after": 1}]
            rows = []
            seq, tick = 10, 1000
            for idx in range(250):
                if idx == 120:
                    seq += 5
                    tick += 5 * STEP_TICKS
                seg = 0 if idx < 120 else 1
                rows.append({
                    "sample_index": idx,
                    "sequence": seq,
                    "tick_32k": tick,
                    "elapsed_s": (tick - 1000) / mm.TICK_HZ,
                    "dt_s": 0.01,
                    "segment_index": seg,
                    "interpolated": False,
                    "accel_mps2": [0.2, -0.1, 9.81],
                    "gyro_dps": [15.0 * math.sin(idx * 0.2), 5.0, 10.0],
                    "gyro_corrected_dps": [15.0 * math.sin(idx * 0.2) + 4.0 * math.sin(2.0 * math.pi * 6.0 * idx * 0.01), 5.0, 10.0],
                    "speed_mps": 0.08 + 0.04 * math.sin(idx * 0.05),
                })
                seq += 1
                tick += STEP_TICKS

            detail_file = archive_dir / "device_3_pc_movement_1.detail.json"
            export_json = archive_dir / "device_3_pc_movement_1.export.json"
            export_csv = archive_dir / "device_3_pc_movement_1.export.csv"
            detail_bytes = (json.dumps(detail, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            samples_bytes = (json.dumps({**detail, "samples": rows}, ensure_ascii=False) + "\n").encode("utf-8")
            csv_bytes = b"metadata,movement_id,1\n"
            detail_file.write_bytes(detail_bytes)
            export_json.write_bytes(samples_bytes)
            export_csv.write_bytes(csv_bytes)

            legacy_tasks_path = root / "task_records.json"
            legacy_tasks_path.write_text(
                json.dumps({
                    "tasks": [{
                        "task_id": task_id,
                        "participant_id": "P001",
                        "block_id": "C",
                        "device_id": 3,
                        "device_ids": [3],
                        "block_bindings": [{"device_id": 3, "block_id": "C"}],
                        "start_time": "2026-10-27T10:00:00+08:00",
                        "end_time": "2026-10-27T10:01:00+08:00",
                        "duration_seconds": 60,
                        "status": "completed",
                        "data_quality": "complete",
                        "total_score": 91,
                        "score_model_version": "temporary-random-task-v2",
                        "movement_references": [{
                            "device_id": 3,
                            "block_id": "C",
                            "movement_id": 1,
                            "analysis_mode": "pc",
                            "task_movement_index": 1,
                            "device_movement_index": 1,
                        }],
                        "movement_files": {
                            "3:pc:1": {
                                "detail": f"{token}/device_3_pc_movement_1.detail.json",
                                "json": f"{token}/device_3_pc_movement_1.export.json",
                                "csv": f"{token}/device_3_pc_movement_1.export.csv",
                            }
                        },
                    }]
                }),
                encoding="utf-8",
            )

            snapshot = lambda did: {"device_id": did, "block_id": "C", "analysis_mode": "pc", "completed_movements": []}
            service = TaskService(
                legacy_tasks_path,
                snapshot,
                device_ids=(3,),
                database_path=root / "motion.sqlite3",
                movement_root=root / "task_movements",
            )
            res = service.result(task_id)
            self.assertFalse(res["score"]["demo_only"])
            self.assertNotEqual(res["task"]["score_model_version"], "temporary-random-task-v2")
            self.assertIsNotNone(res["task"]["total_score"])
            self.assertEqual(
                res["task"]["movement_references"][0]["score_quality_status"],
                "repaired_estimate",
            )
            # Original archived files remain byte-for-byte untouched
            self.assertEqual(detail_file.read_bytes(), detail_bytes)
            self.assertEqual(export_json.read_bytes(), samples_bytes)
            self.assertEqual(export_csv.read_bytes(), csv_bytes)


if __name__ == "__main__":
    unittest.main()
