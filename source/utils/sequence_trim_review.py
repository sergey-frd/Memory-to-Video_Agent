from __future__ import annotations

import json
from pathlib import Path

from models.sequence_trim_review import SequenceTrimReviewResult, TrimClipDecision
from utils.premiere_project import parse_premiere_project_sequence_visual_clips
from utils.premiere_trim_review_export import export_trim_review_premiere_projects
from utils.sequence_trim_classifier import CompactKeepSettings, classify_sequence_trim_review, ticks_to_seconds
from utils.sequence_trim_semantic import classify_sequence_trim_review_semantic


def _compact_keep_from_payload(payload: dict) -> CompactKeepSettings:
    return CompactKeepSettings(
        enabled=bool(payload.get("compact_keep", True)),
        photo_keep_min_seconds=float(payload.get("photo_keep_min_seconds", 1.5)),
        photo_keep_max_seconds=float(payload.get("photo_keep_max_seconds", 3.0)),
        video_keep_min_seconds=float(payload.get("video_keep_min_seconds", 2.0)),
        video_keep_max_seconds=float(payload.get("video_keep_max_seconds", 8.0)),
    )


def run_sequence_trim_review_from_config(config_path: Path) -> tuple[Path, Path, Path | None]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    project_path = Path(str(payload["project_path"]))
    source_sequence_name = str(payload.get("source_sequence_name") or "").strip() or None
    reports_dir = Path(str(payload.get("reports_dir") or (project_path.parent / "trim_review_reports")))
    output_project_path = Path(
        str(payload.get("output_project_path") or (project_path.parent / f"{project_path.stem}_trim_review.prproj"))
    )
    target_keep_seconds = float(payload.get("target_keep_seconds", 300))
    min_keep_seconds = float(payload.get("min_keep_seconds", 180))
    max_keep_seconds = payload.get("max_keep_seconds")
    context_notes = str(payload.get("context_notes") or "")
    force_keep_names = [str(item) for item in (payload.get("force_keep_names") or [])]
    force_drop_names = [str(item) for item in (payload.get("force_drop_names") or [])]
    split_tracks = bool(payload.get("split_tracks", True))
    keep_track_index = int(payload.get("keep_track_index", 0))
    drop_track_index = int(payload.get("drop_track_index", 1))
    write_project = bool(payload.get("write_project", True))
    compact_keep = _compact_keep_from_payload(payload)
    engines = [str(item).strip().casefold() for item in (payload.get("engines") or ["heuristic", "semantic"])]
    engines = [item for item in engines if item]
    if not engines:
        engines = ["heuristic", "semantic"]

    base_sequence_name = str(payload.get("new_sequence_name") or "").strip()
    heuristic_name = str(payload.get("new_sequence_name_heuristic") or "").strip()
    semantic_name = str(payload.get("new_sequence_name_semantic") or "").strip()
    frames_per_clip = int(payload.get("semantic_frames_per_clip", 5))
    semantic_model = str(payload.get("semantic_model") or "").strip() or None
    frames_dir_value = str(payload.get("semantic_frames_dir") or "").strip()
    frames_dir = Path(frames_dir_value) if frames_dir_value else (reports_dir / "semantic_frames")

    selected_sequence_name, clips = parse_premiere_project_sequence_visual_clips(
        project_path,
        source_sequence_name,
    )
    if not base_sequence_name:
        base_sequence_name = f"{selected_sequence_name}_trim_review"
    if not heuristic_name:
        heuristic_name = f"{base_sequence_name}_heuristic"
    if not semantic_name:
        semantic_name = f"{base_sequence_name}_semantic"

    max_keep = float(max_keep_seconds) if max_keep_seconds is not None else None
    results: list[SequenceTrimReviewResult] = []

    if "heuristic" in engines:
        results.append(
            classify_sequence_trim_review(
                clips,
                source_project_path=project_path,
                source_sequence_name=selected_sequence_name,
                new_sequence_name=heuristic_name,
                target_keep_seconds=target_keep_seconds,
                min_keep_seconds=min_keep_seconds,
                max_keep_seconds=max_keep,
                context_notes=context_notes,
                force_keep_names=force_keep_names,
                force_drop_names=force_drop_names,
                compact_keep=compact_keep,
            )
        )

    if "semantic" in engines:
        results.append(
            classify_sequence_trim_review_semantic(
                clips,
                source_project_path=project_path,
                source_sequence_name=selected_sequence_name,
                new_sequence_name=semantic_name,
                target_keep_seconds=target_keep_seconds,
                min_keep_seconds=min_keep_seconds,
                max_keep_seconds=max_keep,
                context_notes=context_notes,
                force_keep_names=force_keep_names,
                force_drop_names=force_drop_names,
                frames_per_clip=frames_per_clip,
                model=semantic_model,
                frames_dir=frames_dir,
                compact_keep=compact_keep,
            )
        )

    if not results:
        raise ValueError(f"No supported engines selected in config: {engines}")

    exported_project: Path | None = None
    if write_project:
        exported_project, export_warnings = export_trim_review_premiere_projects(
            source_project_path=project_path,
            review_results=results,
            output_project_path=output_project_path,
            keep_track_index=keep_track_index,
            drop_track_index=drop_track_index,
            split_tracks=split_tracks,
        )
        for result in results:
            result.warnings.extend(export_warnings)

    reports_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = _safe_filename(base_sequence_name)
    json_path = reports_dir / f"{bundle_name}_trim_review_bundle.json"
    txt_path = reports_dir / f"{bundle_name}_trim_review_bundle.txt"
    bundle_payload = {
        "source_project_path": str(project_path),
        "source_sequence_name": selected_sequence_name,
        "output_project_path": str(exported_project) if exported_project is not None else None,
        "engines": [result.engine for result in results],
        "results": [result.to_dict() for result in results],
    }
    json_path.write_text(json.dumps(bundle_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(
        build_trim_review_bundle_report(results, exported_project=exported_project),
        encoding="utf-8",
    )

    for result in results:
        safe_name = _safe_filename(result.new_sequence_name)
        (reports_dir / f"{safe_name}_trim_review.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (reports_dir / f"{safe_name}_trim_review.txt").write_text(
            build_trim_review_report(result, exported_project=exported_project),
            encoding="utf-8",
        )

    return json_path, txt_path, exported_project


def build_trim_review_bundle_report(
    results: list[SequenceTrimReviewResult],
    *,
    exported_project: Path | None = None,
) -> str:
    lines = [
        "Sequence Trim Review Bundle",
        "=" * 72,
        f"Engines: {', '.join(result.engine for result in results)}",
    ]
    if exported_project is not None:
        lines.append(f"Output project: {exported_project}")
    lines.append("Sequences:")
    for result in results:
        lines.append(
            f"- {result.new_sequence_name}: KEEP {result.keep_seconds:.1f}s / "
            f"DROP {result.drop_seconds:.1f}s ({result.engine})"
        )
    lines.append("")
    for result in results:
        lines.append(build_trim_review_report(result, exported_project=exported_project))
        lines.append("")
    return "\n".join(lines)


def build_trim_review_report(
    result: SequenceTrimReviewResult,
    *,
    exported_project: Path | None = None,
) -> str:
    mixed_count = sum(1 for item in result.decisions if item.decision == "mixed")
    lines = [
        "Sequence Trim Review (per-clip KEEP/DROP segments)",
        "=" * 72,
        f"Source project: {result.source_project_path}",
        f"Source sequence: {result.source_sequence_name}",
        f"Review sequence: {result.new_sequence_name}",
        f"Engine: {result.engine}",
        (
            f"Budget: target {result.target_keep_seconds:.0f}s / "
            f"min {result.min_keep_seconds:.0f}s / max {result.max_keep_seconds:.0f}s"
        ),
        (
            f"Durations: source {result.total_source_seconds:.1f}s | "
            f"KEEP {result.keep_seconds:.1f}s | DROP {result.drop_seconds:.1f}s"
        ),
        f"Clips: {len(result.decisions)} | mixed (both keep+drop inside): {mixed_count}",
    ]
    if exported_project is not None:
        lines.append(f"Output project: {exported_project}")
    if result.context_notes:
        lines.extend(["", "Context notes:", result.context_notes])

    lines.extend(["", "How to use in Premiere:", "-" * 72])
    lines.extend(
        [
            "1. Open the output .prproj and this review sequence.",
            "2. Each source clip is split into [KEEP] / [DROP] segments.",
            "3. KEEP segments are on the keep track (V1 by default), DROP segments on the drop track (V2).",
            "4. Mute/hide V2 to preview only the keep islands (gaps show removed parts).",
            "5. Compare heuristic vs semantic sequences side by side, then delete DROP yourself.",
            "6. Compact keep is on: stills ~1.5-3s, video keep islands stay short enough to catch the point.",
        ]
    )

    if result.open_questions:
        lines.extend(["", "Questions that would improve the next pass:", "-" * 72])
        for index, question in enumerate(result.open_questions, start=1):
            lines.append(f"{index}. {question}")

    if result.warnings:
        lines.extend(["", "Warnings:", "-" * 72])
        for warning in result.warnings:
            lines.append(f"- {warning}")

    lines.extend(["", "Per-clip segment plan:", "-" * 72])
    for item in result.decisions:
        lines.append(_format_clip_header(item))
        for segment in item.segments:
            local_start = ticks_to_seconds(segment.local_start)
            local_end = ticks_to_seconds(segment.local_end)
            lines.append(
                f"    [{segment.decision.upper():4}] s{segment.segment_index}  "
                f"{segment.duration_seconds:6.1f}s  "
                f"local {local_start:6.1f}-{local_end:6.1f}s  | {segment.reason}"
            )

    lines.append("")
    return "\n".join(lines)


def _format_clip_header(item: TrimClipDecision) -> str:
    return (
        f"#{item.order_index:03d}  {item.name}  "
        f"total {item.duration_seconds:.1f}s  "
        f"keep {item.keep_seconds:.1f}s / drop {item.drop_seconds:.1f}s  "
        f"[{item.decision}]  score={item.score:.2f}"
    )


def _safe_filename(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return cleaned.strip("_") or "sequence"
