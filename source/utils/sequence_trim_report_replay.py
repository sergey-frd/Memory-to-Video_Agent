from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from models.sequence_trim_review import SequenceTrimReviewResult, TrimClipDecision, TrimSegmentDecision
from utils.premiere_trim_review_export import export_trim_review_premiere_projects


HERO_REPLAY_LEVELS = ("high", "medium", "review", "drop")


def run_sequence_trim_report_replay_from_config(
    config_path: Path,
) -> tuple[Path, Path, Path]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    review_json_path = Path(str(payload["review_json_path"]))
    if not review_json_path.is_file():
        raise FileNotFoundError(f"Review JSON does not exist: {review_json_path}")

    source_result = load_sequence_trim_review_result(review_json_path)
    source_project_value = str(payload.get("project_path") or source_result.source_project_path)
    source_project_path = Path(source_project_value)
    if not source_project_path.is_file():
        raise FileNotFoundError(f"Source Premiere project does not exist: {source_project_path}")

    reports_dir = Path(
        str(payload.get("reports_dir") or (review_json_path.parent / "hero_level_replay"))
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_project_path = Path(
        str(
            payload.get("output_project_path")
            or (source_project_path.parent / f"{source_project_path.stem}_hero_levels.prproj")
        )
    )
    progress = _build_progress_reporter(reports_dir / "sequence_trim_report_replay_progress.log")
    progress(f"Report replay started. No OpenAI requests will be made. Input: {review_json_path}")

    sequence_name = str(
        payload.get("sequence_name")
        or f"{source_result.new_sequence_name}_LEVEL_TRACKS"
    )
    configured_tracks = payload.get("track_indexes")
    track_values = configured_tracks if isinstance(configured_tracks, dict) else {}
    track_indexes = {
        "high": int(track_values.get("high", 0)),
        "medium": int(track_values.get("medium", 1)),
        "review": int(track_values.get("review", 2)),
        "drop": int(track_values.get("drop", 3)),
    }
    if len(set(track_indexes.values())) != len(HERO_REPLAY_LEVELS):
        raise ValueError("track_indexes must assign a different video track to every hero level.")
    if min(track_indexes.values()) < 0:
        raise ValueError("track_indexes must not contain negative values.")

    metadata = dict(source_result.engine_metadata)
    metadata.update(
        {
            "source_report_path": str(review_json_path),
            "openai_requests": 0,
            "track_indexes": track_indexes,
        }
    )
    combined_result = replace(
        source_result,
        new_sequence_name=sequence_name,
        engine="hero_report_replay_tracks_v1",
        engine_metadata=metadata,
    )
    level_stats = [_level_stats(source_result, level) for level in HERO_REPLAY_LEVELS]
    for item in level_stats:
        progress(
            f"Prepared {item['level'].upper()} on V{track_indexes[str(item['level'])] + 1}: "
            f"{item['selected_seconds']:.1f}s, {item['segment_count']} segments."
        )

    progress(f"Exporting one sequence with four level tracks to: {output_project_path}")
    exported_project, warnings = export_trim_review_premiere_projects(
        source_project_path=source_project_path,
        review_results=[combined_result],
        output_project_path=output_project_path,
        split_tracks=False,
        hero_level_track_indexes=track_indexes,
    )

    summary = {
        "mode": "report_replay",
        "source_report_path": str(review_json_path),
        "source_project_path": str(source_project_path),
        "source_sequence_name": source_result.source_sequence_name,
        "output_project_path": str(exported_project),
        "openai_requests": 0,
        "warnings": warnings,
        "sequence_name": sequence_name,
        "tracks": [
            {
                **item,
                "track_index": track_indexes[str(item["level"])],
                "premiere_track": f"V{track_indexes[str(item['level'])] + 1}",
            }
            for item in level_stats
        ],
    }
    json_path = reports_dir / "hero_level_replay_summary.json"
    txt_path = reports_dir / "hero_level_replay_summary.txt"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(_build_summary_text(summary), encoding="utf-8")
    progress(f"Report replay completed. Project: {exported_project}")
    return json_path, txt_path, exported_project


def load_sequence_trim_review_result(review_json_path: Path) -> SequenceTrimReviewResult:
    payload = json.loads(review_json_path.read_text(encoding="utf-8"))
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("Review JSON must contain a decisions array.")
    decisions: list[TrimClipDecision] = []
    for raw_decision in raw_decisions:
        if not isinstance(raw_decision, dict):
            continue
        decision_payload = dict(raw_decision)
        raw_segments = decision_payload.pop("segments", [])
        decision_payload["segments"] = [
            TrimSegmentDecision(**segment)
            for segment in raw_segments
            if isinstance(segment, dict)
        ]
        decisions.append(TrimClipDecision(**decision_payload))

    return SequenceTrimReviewResult(
        source_project_path=str(payload["source_project_path"]),
        source_sequence_name=str(payload["source_sequence_name"]),
        new_sequence_name=str(payload["new_sequence_name"]),
        engine=str(payload["engine"]),
        target_keep_seconds=float(payload.get("target_keep_seconds", 0.0)),
        min_keep_seconds=float(payload.get("min_keep_seconds", 0.0)),
        max_keep_seconds=float(payload.get("max_keep_seconds", 0.0)),
        total_source_seconds=float(payload.get("total_source_seconds", 0.0)),
        keep_seconds=float(payload.get("keep_seconds", 0.0)),
        drop_seconds=float(payload.get("drop_seconds", 0.0)),
        context_notes=str(payload.get("context_notes") or ""),
        engine_metadata=dict(payload.get("engine_metadata") or {}),
        open_questions=[str(item) for item in payload.get("open_questions", [])],
        warnings=[str(item) for item in payload.get("warnings", [])],
        decisions=decisions,
    )


def split_hero_result_by_level(
    source_result: SequenceTrimReviewResult,
    *,
    sequence_names: dict[str, str],
    source_report_path: Path,
) -> list[SequenceTrimReviewResult]:
    results: list[SequenceTrimReviewResult] = []
    for level in HERO_REPLAY_LEVELS:
        decisions: list[TrimClipDecision] = []
        selected_seconds = 0.0
        for decision in source_result.decisions:
            selected_segments = [
                segment
                for segment in decision.segments
                if _segment_belongs_to_level(segment, level)
            ]
            duration = sum(segment.duration_seconds for segment in selected_segments)
            selected_seconds += duration
            decisions.append(
                replace(
                    decision,
                    keep_seconds=round(duration if level != "drop" else 0.0, 3),
                    drop_seconds=round(duration if level == "drop" else 0.0, 3),
                    decision=("drop" if level == "drop" else "keep") if selected_segments else "empty",
                    segments=selected_segments,
                    hero_match_level=level,
                )
            )

        metadata = dict(source_result.engine_metadata)
        metadata.update(
            {
                "replay_level": level,
                "source_report_path": str(source_report_path),
                "openai_requests": 0,
            }
        )
        results.append(
            SequenceTrimReviewResult(
                source_project_path=source_result.source_project_path,
                source_sequence_name=source_result.source_sequence_name,
                new_sequence_name=sequence_names[level],
                engine=f"hero_report_replay_{level}_v1",
                target_keep_seconds=0.0,
                min_keep_seconds=0.0,
                max_keep_seconds=source_result.total_source_seconds,
                total_source_seconds=source_result.total_source_seconds,
                keep_seconds=round(selected_seconds if level != "drop" else 0.0, 3),
                drop_seconds=round(selected_seconds if level == "drop" else 0.0, 3),
                context_notes=source_result.context_notes,
                engine_metadata=metadata,
                open_questions=[],
                warnings=list(source_result.warnings),
                decisions=decisions,
            )
        )
    return results


def _segment_belongs_to_level(segment: TrimSegmentDecision, level: str) -> bool:
    match_level = segment.hero_match_level.casefold()
    if level == "drop":
        return segment.decision == "drop"
    if level == "review":
        return segment.decision == "keep" and match_level in {"review", "uncertain"}
    return segment.decision == "keep" and match_level == level


def _level_stats(
    source_result: SequenceTrimReviewResult,
    level: str,
) -> dict[str, object]:
    selected_by_clip = [
        [
            segment
            for segment in decision.segments
            if _segment_belongs_to_level(segment, level)
        ]
        for decision in source_result.decisions
    ]
    selected_segments = [
        segment
        for clip_segments in selected_by_clip
        for segment in clip_segments
    ]
    return {
        "level": level,
        "selected_seconds": round(
            sum(segment.duration_seconds for segment in selected_segments),
            3,
        ),
        "segment_count": len(selected_segments),
        "clip_count": sum(bool(segments) for segments in selected_by_clip),
    }


def _build_summary_text(summary: dict[str, object]) -> str:
    lines = [
        "Hero Level Report Replay",
        "=" * 72,
        f"Source report: {summary['source_report_path']}",
        f"Output project: {summary['output_project_path']}",
        "OpenAI requests: 0",
        f"Sequence: {summary['sequence_name']}",
        "",
        "Tracks:",
    ]
    for item in summary["tracks"]:  # type: ignore[union-attr]
        lines.append(
            f"- {item['premiere_track']} {item['level'].upper()} | "
            f"{item['selected_seconds']:.1f}s | {item['segment_count']} segments"
        )
    warnings = summary.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)


def _build_progress_reporter(log_path: Path) -> Callable[[str], None]:
    def report(message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")

    return report
