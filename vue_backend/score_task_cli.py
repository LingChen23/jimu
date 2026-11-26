#!/usr/bin/env python3
"""CLI utility to evaluate exported task ZIP archives with the V1.4 scoring engine.

Supports both single-task archives (``task_*.zip``) and batch archives (``tasks_batch*.zip``).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile
from typing import Any

from scoring_engine import DEFAULT_CONFIG_PATH, ScoringConfig, evaluate_task


def score_single_task_zip_bytes(
    archive_bytes: bytes,
    config: ScoringConfig,
    *,
    include_synthetic_samples: bool = True,
) -> dict[str, Any]:
    movements_input: list[dict[str, Any]] = []
    task_meta: dict[str, Any] = {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        names = set(zf.namelist())
        if "task.json" in names:
            manifest = json.loads(zf.read("task.json"))
            task_meta = manifest.get("task", {})
            manifest_movements = manifest.get("movements", [])
            for ref in manifest_movements:
                included = ref.get("included_files", [])
                detail_path = next((p for p in included if p.endswith("/detail.json")), None)
                samples_path = next((p for p in included if p.endswith("/samples.json")), None)
                detail = json.loads(zf.read(detail_path)) if detail_path and detail_path in names else None
                samples_rows = (
                    json.loads(zf.read(samples_path)).get("samples", [])
                    if samples_path and samples_path in names
                    else None
                )
                movements_input.append({
                    "reference": ref,
                    "detail": detail,
                    "samples_rows": samples_rows,
                })
        else:
            for path in sorted(names):
                if not path.endswith("/detail.json"):
                    continue
                detail = json.loads(zf.read(path))
                samples_path = path[:-len("detail.json")] + "samples.json"
                samples_rows = (
                    json.loads(zf.read(samples_path)).get("samples", [])
                    if samples_path in names
                    else None
                )
                movements_input.append({
                    "reference": {
                        "device_id": detail.get("device_id"),
                        "block_id": detail.get("block_id"),
                        "movement_id": detail.get("movement_id"),
                        "analysis_mode": detail.get("analysis_mode", "pc"),
                    },
                    "detail": detail,
                    "samples_rows": samples_rows,
                })

    evaluated = evaluate_task(
        movements_input,
        config,
        task_status=str(task_meta.get("status", "completed")),
        include_synthetic_samples=include_synthetic_samples,
    )
    return {
        "task_id": task_meta.get("task_id"),
        "participant_id": task_meta.get("participant_id"),
        "source_archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "reference_parameters": config.reference_parameters_snapshot(),
        **evaluated,
        # Aliases matching the offline prototype score_task_v14.py output keys:
        "moved_block_count": evaluated["participation"]["participated_block_count"],
        "scored_block_count": evaluated["participation"]["scored_block_count"],
        "task_score": evaluated["total_score_raw"],
        "task_score_display": evaluated["total_score"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_zip", help="Path to task_*.zip or tasks_batch*.zip")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to scoring_config.json",
    )
    parser.add_argument(
        "--threshold-percent",
        type=float,
        default=None,
        help="Override missing sample ratio threshold percent (0..100), default from config (10)",
    )
    parser.add_argument(
        "--output",
        default="score_result.json",
        help="Output JSON report path",
    )
    args = parser.parse_args()

    config = ScoringConfig.from_file(args.config)
    if args.threshold_percent is not None:
        if not 0.0 <= args.threshold_percent <= 100.0:
            parser.error("--threshold-percent 必须在 0～100 之间")
        config = config.with_threshold_ratio(args.threshold_percent / 100.0)

    raw_bytes = Path(args.archive_zip).read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        names = set(zf.namelist())
        if "batch.json" in names:
            batch_meta = json.loads(zf.read("batch.json"))
            task_reports = []
            for entry in batch_meta.get("tasks", []):
                prefix = str(entry["directory"]).rstrip("/") + "/"
                sub_buf = io.BytesIO()
                with zipfile.ZipFile(sub_buf, "w", compression=zipfile.ZIP_DEFLATED) as sub_zf:
                    for member in zf.infolist():
                        if not member.is_dir() and member.filename.startswith(prefix):
                            rel_name = member.filename[len(prefix):]
                            sub_zf.writestr(rel_name, zf.read(member))
                task_reports.append(score_single_task_zip_bytes(sub_buf.getvalue(), config))
            report: dict[str, Any] = {
                "batch_task_count": len(task_reports),
                "model_version": config.model_version,
                "tasks": task_reports,
            }
        else:
            report = score_single_task_zip_bytes(raw_bytes, config)

    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if "tasks" in report:
        for item in report["tasks"]:
            print(item.get("task_id"), "score:", item.get("task_score_display"), "completeness:", item.get("completeness"))
    else:
        print("task score:", report.get("task_score_display"), f"(raw={report.get('task_score')})")
        for mov in sorted(report.get("movements", []), key=lambda m: str(m.get("block_id", ""))):
            print(
                mov.get("block_name"),
                (mov.get("quality") or {}).get("status"),
                mov.get("score_display"),
                mov.get("invalid_reason", ""),
            )


if __name__ == "__main__":
    main()
