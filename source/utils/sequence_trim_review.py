from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from models.sequence_trim_review import SequenceTrimReviewResult, TrimClipDecision
from utils.premiere_project import parse_premiere_project_sequence_visual_clips
from utils.premiere_trim_review_export import export_trim_review_premiere_projects
from utils.sequence_trim_classifier import CompactKeepSettings, classify_sequence_trim_review, ticks_to_seconds
from utils.sequence_trim_hero import classify_sequence_trim_review_hero
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
    reports_dir.mkdir(parents=True, exist_ok=True)
    progress = _build_progress_reporter(reports_dir / "sequence_trim_review_progress.log")
    progress(f"Run started. Config: {config_path}")
    progress(f"Project: {project_path}; requested sequence: {source_sequence_name or '<auto>'}")
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
    hero_name = str(payload.get("new_sequence_name_hero") or "").strip()
    frames_per_clip = int(payload.get("semantic_frames_per_clip", 5))
    semantic_model = str(payload.get("semantic_model") or "").strip() or None
    frames_dir_value = str(payload.get("semantic_frames_dir") or "").strip()
    frames_dir = Path(frames_dir_value) if frames_dir_value else (reports_dir / "semantic_frames")

    progress("Reading Premiere project and collecting visual clips...")
    selected_sequence_name, clips = parse_premiere_project_sequence_visual_clips(
        project_path,
        source_sequence_name,
    )
    progress(f"Sequence '{selected_sequence_name}' loaded: {len(clips)} visual clips.")
    if not base_sequence_name:
        base_sequence_name = f"{selected_sequence_name}_trim_review"
    if not heuristic_name:
        heuristic_name = f"{base_sequence_name}_heuristic"
    if not semantic_name:
        semantic_name = f"{base_sequence_name}_semantic"
    if not hero_name:
        hero_name = f"{base_sequence_name}_hero"

    max_keep = float(max_keep_seconds) if max_keep_seconds is not None else None
    results: list[SequenceTrimReviewResult] = []

    if "heuristic" in engines:
        progress("Starting heuristic engine...")
        heuristic_result = classify_sequence_trim_review(
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
        results.append(heuristic_result)
        progress(
            f"Heuristic engine completed: KEEP={heuristic_result.keep_seconds:.1f}s, "
            f"DROP={heuristic_result.drop_seconds:.1f}s."
        )

    if "semantic" in engines:
        progress("Starting semantic engine...")
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
                request_timeout_seconds=float(
                    payload.get("semantic_request_timeout_seconds", 180.0)
                ),
                progress=progress,
            )
        )
        progress(
            f"Semantic engine completed: KEEP={results[-1].keep_seconds:.1f}s, "
            f"DROP={results[-1].drop_seconds:.1f}s."
        )

    if "hero" in engines:
        progress("Starting hero-presence engine...")
        hero_definition_value = str(payload.get("hero_definition_path") or "").strip()
        if not hero_definition_value:
            raise ValueError("Engine 'hero' requires hero_definition_path.")
        hero_definition_path = Path(hero_definition_value)
        if not hero_definition_path.is_file():
            raise FileNotFoundError(f"Hero definition does not exist: {hero_definition_path}")
        hero_frames_dir_value = str(payload.get("hero_frames_dir") or "").strip()
        hero_frames_dir = (
            Path(hero_frames_dir_value) if hero_frames_dir_value else reports_dir / "hero_frames"
        )
        results.append(
            classify_sequence_trim_review_hero(
                clips,
                source_project_path=project_path,
                source_sequence_name=selected_sequence_name,
                new_sequence_name=hero_name,
                hero_definition_path=hero_definition_path,
                frames_dir=hero_frames_dir,
                model=str(payload.get("hero_match_model") or "").strip() or None,
                frame_interval_seconds=float(payload.get("hero_frame_interval_seconds", 5.0)),
                max_frames_per_clip=int(payload.get("hero_max_frames_per_clip", 48)),
                frames_per_request=int(payload.get("hero_frames_per_request", 10)),
                reference_image_limit=int(payload.get("hero_reference_image_limit", 6)),
                pre_roll_seconds=float(payload.get("hero_pre_roll_seconds", 10.0)),
                post_roll_seconds=float(payload.get("hero_post_roll_seconds", 10.0)),
                keep_medium_matches=bool(payload.get("hero_keep_medium_matches", True)),
                keep_clip_on_analysis_error=bool(
                    payload.get("hero_keep_clip_on_analysis_error", True)
                ),
                high_confidence_threshold=float(
                    payload.get("hero_high_confidence_threshold", 0.85)
                ),
                medium_confidence_threshold=float(
                    payload.get("hero_medium_confidence_threshold", 0.55)
                ),
                max_image_edge=int(payload.get("hero_max_image_edge", 1024)),
                request_timeout_seconds=float(
                    payload.get("hero_request_timeout_seconds", 180.0)
                ),
                cache_dir=Path(
                    str(payload.get("hero_cache_dir") or (reports_dir / "hero_match_cache"))
                ),
                resume_from_cache=bool(payload.get("hero_resume_from_cache", True)),
                context_notes=context_notes,
                progress=progress,
            )
        )

    if not results:
        raise ValueError(f"No supported engines selected in config: {engines}")

    exported_project: Path | None = None
    if write_project:
        progress("Analysis complete. Exporting review Premiere project...")
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

    progress("Writing JSON and text reports...")
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

    progress(
        f"Run completed. Bundle: {json_path}; project: {exported_project or '<not written>'}"
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
    budget_line = (
        "Selection mode: hero presence (duration budget is not applied)"
        if result.engine.startswith("hero_presence")
        else (
            f"Budget: target {result.target_keep_seconds:.0f}s / "
            f"min {result.min_keep_seconds:.0f}s / max {result.max_keep_seconds:.0f}s"
        )
    )
    lines = [
        "Sequence Trim Review (per-clip KEEP/DROP segments)",
        "=" * 72,
        f"Source project: {result.source_project_path}",
        f"Source sequence: {result.source_sequence_name}",
        f"Review sequence: {result.new_sequence_name}",
        f"Engine: {result.engine}",
        budget_line,
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
    if result.engine.startswith("hero_presence"):
        lines.extend(
            [
                "",
                "Hero matching:",
                f"- Hero: {result.engine_metadata.get('hero_name') or '<not named>'}",
                f"- Definition: {result.engine_metadata.get('hero_definition_path') or '<missing>'}",
                (
                    f"- Context window: {result.engine_metadata.get('pre_roll_seconds', 0)}s before / "
                    f"{result.engine_metadata.get('post_roll_seconds', 0)}s after"
                ),
                (
                    f"- Clip flags: HIGH {result.engine_metadata.get('high_match_clips', 0)} | "
                    f"MEDIUM {result.engine_metadata.get('medium_match_clips', 0)} | "
                    f"REVIEW {result.engine_metadata.get('uncertain_clips', 0)}"
                ),
            ]
        )

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
                f"local {local_start:6.1f}-{local_end:6.1f}s  "
                f"hero={segment.hero_match_level or '-'} | {segment.reason}"
            )

    lines.append("")
    return "\n".join(lines)


def _format_clip_header(item: TrimClipDecision) -> str:
    return (
        f"#{item.order_index:03d}  {item.name}  "
        f"total {item.duration_seconds:.1f}s  "
        f"keep {item.keep_seconds:.1f}s / drop {item.drop_seconds:.1f}s  "
        f"[{item.decision}]  score={item.score:.2f}  hero={item.hero_match_level or '-'}"
    )


def _safe_filename(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return cleaned.strip("_") or "sequence"


def _build_progress_reporter(log_path: Path) -> Callable[[str], None]:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def report(message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")

    return report
