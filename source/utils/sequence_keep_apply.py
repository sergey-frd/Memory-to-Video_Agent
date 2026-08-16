from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from models.sequence_keep_apply import KeepRange, MediaKeepSpec
from utils.premiere_keep_apply_export import (
    export_keep_apply_premiere_project,
    resolve_keep_windows_ticks,
)
from utils.premiere_project import (
    PremiereProjectError,
    load_premiere_project_root,
    list_named_project_sequence_names,
    parse_premiere_project_sequence_visual_clips,
)
from utils.sequence_trim_classifier import seconds_to_ticks, ticks_to_seconds


def parse_timecode_seconds(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("Keep timecode must be a string or number, not a boolean.")
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds < 0:
            raise ValueError(f"Keep timecode cannot be negative: {value}")
        return seconds

    text = str(value).strip()
    if not text:
        raise ValueError("Keep timecode is empty.")
    if ":" not in text:
        seconds = float(text)
        if seconds < 0:
            raise ValueError(f"Keep timecode cannot be negative: {value}")
        return seconds

    parts = text.split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(f"Unsupported keep timecode: {value}")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"Unsupported keep timecode: {value}") from exc
    if any(number < 0 for number in numbers):
        raise ValueError(f"Keep timecode cannot be negative: {value}")
    if len(numbers) == 2:
        minutes, seconds = numbers
        return minutes * 60.0 + seconds
    hours, minutes, seconds = numbers
    return hours * 3600.0 + minutes * 60.0 + seconds


def is_keep_apply_config(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    mode = str(payload.get("mode") or "").strip().casefold()
    if mode == "apply_keep_ranges":
        return True
    return isinstance(payload.get("operations"), list)


def load_media_keep_specs(payload: dict[str, object] | list[object]) -> list[MediaKeepSpec]:
    if isinstance(payload, dict):
        raw_clips = payload.get("operations")
        if not isinstance(raw_clips, list) or not raw_clips:
            raw_clips = payload.get("clips")
    else:
        raw_clips = payload
    if not isinstance(raw_clips, list) or not raw_clips:
        raise ValueError("Keep-ranges JSON must contain a non-empty 'operations' or 'clips' list.")

    specs: list[MediaKeepSpec] = []
    seen: set[str] = set()
    for index, raw_clip in enumerate(raw_clips, start=1):
        if not isinstance(raw_clip, dict):
            raise ValueError(f"Keep clip #{index} must be an object.")
        file_name = str(raw_clip.get("file") or raw_clip.get("filename") or "").strip()
        if not file_name:
            raise ValueError(f"Keep clip #{index} is missing 'file'.")
        match_key = Path(file_name).name.casefold()
        if match_key in seen:
            raise ValueError(f"Keep clip '{file_name}' is listed more than once.")
        seen.add(match_key)

        raw_ranges = raw_clip.get("keep") or raw_clip.get("keep_ranges") or []
        duration_value = raw_clip.get("duration")
        ranges: list[KeepRange] = []
        duration_seconds: float | None = None
        if isinstance(raw_ranges, list) and raw_ranges:
            for range_index, raw_range in enumerate(raw_ranges, start=1):
                if not isinstance(raw_range, dict):
                    raise ValueError(f"Keep range #{range_index} for '{file_name}' must be an object.")
                start_seconds = parse_timecode_seconds(raw_range.get("in", raw_range.get("start")))
                end_seconds = parse_timecode_seconds(raw_range.get("out", raw_range.get("end")))
                if end_seconds <= start_seconds:
                    raise ValueError(
                        f"Keep range #{range_index} for '{file_name}' has out <= in "
                        f"({start_seconds:.3f}s / {end_seconds:.3f}s)."
                    )
                ranges.append(KeepRange(start_seconds=start_seconds, end_seconds=end_seconds))
            ranges.sort(key=lambda item: (item.start_seconds, item.end_seconds))
            _reject_overlapping_ranges(file_name, ranges)
        elif duration_value is not None and str(duration_value).strip() != "":
            duration_seconds = parse_timecode_seconds(duration_value)
            if duration_seconds <= 0:
                raise ValueError(f"Keep clip '{file_name}' duration must be greater than 0.")
        else:
            raise ValueError(
                f"Keep clip '{file_name}' must contain a non-empty 'keep'/'keep_ranges' list "
                "or a 'duration' value."
            )
        specs.append(
            MediaKeepSpec(
                file_name=Path(file_name).name,
                ranges=tuple(ranges),
                duration_seconds=duration_seconds,
            )
        )
    return specs


def run_sequence_keep_apply_from_config(config_path: Path) -> tuple[Path, Path, Path | None]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Keep-apply config must be a JSON object: {config_path}")

    keep_payload, keep_specs, keep_ranges_path = _load_keep_job_from_config(payload, config_path)
    project_path = _resolve_existing_project_path(payload, keep_payload)
    source_sequence_name = _first_non_empty_text(
        payload.get("source_sequence_name"),
        payload.get("sequence_name"),
        keep_payload.get("source_sequence_name"),
        keep_payload.get("sequence_name"),
    )
    reports_dir = Path(str(payload.get("reports_dir") or (project_path.parent / "keep_apply_reports")))
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_project_path = Path(
        str(payload.get("output_project_path") or (project_path.parent / f"{project_path.stem}_keep.prproj"))
    )
    ripple_compact = bool(payload.get("ripple_compact", True))
    write_project = bool(payload.get("write_project", True))
    prin_path = str(payload.get("prin_path") or keep_payload.get("prin_path") or "").strip()

    progress = _build_progress_reporter(reports_dir / "sequence_keep_apply_progress.log")
    progress(f"Keep-apply started. Config: {config_path}")
    project_path = _prefer_import_project_if_empty(project_path, source_sequence_name, progress)
    progress(f"Project: {project_path}")
    if prin_path:
        progress(f"Reference .prin (not parsed): {prin_path}")
    progress(f"Keep specs: {len(keep_specs)} media file(s)")

    root = load_premiere_project_root(project_path)
    sequence_names = _resolve_target_sequence_names(root, source_sequence_name)
    progress(f"Target sequences: {', '.join(sequence_names)}")

    sequence_summaries: list[dict[str, object]] = []
    all_warnings: list[str] = []
    for sequence_name in sequence_names:
        try:
            selected_name, clips = parse_premiere_project_sequence_visual_clips(project_path, sequence_name)
        except PremiereProjectError as exc:
            warning = str(exc)
            all_warnings.append(warning)
            progress(f"Skip sequence '{sequence_name}': {warning}")
            continue
        summary = _build_sequence_plan_summary(
            sequence_name=selected_name,
            clips=clips,
            keep_specs=keep_specs,
        )
        sequence_summaries.append(summary)
        progress(
            f"Sequence '{selected_name}': matched {summary['matched_clips']} clip(s), "
            f"keep {summary['keep_seconds']:.2f}s, drop {summary['drop_seconds']:.2f}s."
        )
        for clip_row in summary.get("clips") or []:
            if not isinstance(clip_row, dict):
                continue
            for restored in clip_row.get("restored_ranges") or []:
                if not isinstance(restored, dict):
                    continue
                warning = (
                    f"Restored source {float(restored.get('source_in_seconds', 0)):.3f}-"
                    f"{float(restored.get('source_out_seconds', 0)):.3f}s for "
                    f"{clip_row.get('name')} outside the current clip window."
                )
                all_warnings.append(warning)
                progress(warning)

    exported_project: Path | None = None
    export_warnings: list[str] = []
    if write_project:
        exported_project, export_warnings = export_keep_apply_premiere_project(
            source_project_path=project_path,
            output_project_path=output_project_path,
            keep_specs=keep_specs,
            sequence_names=sequence_names,
            ripple_compact=ripple_compact,
        )
        progress(f"Wrote project: {exported_project}")
    else:
        progress("Project write skipped (write_project=false).")
    all_warnings.extend(export_warnings)

    report_payload = {
        "mode": "apply_keep_ranges",
        "source_project_path": str(project_path),
        "prin_path": prin_path or None,
        "output_project_path": str(exported_project) if exported_project is not None else None,
        "keep_ranges_path": str(keep_ranges_path) if keep_ranges_path is not None else None,
        "source_sequence_name": source_sequence_name,
        "ripple_compact": ripple_compact,
        "keep_specs": [
            {
                "file": spec.file_name,
                "duration_seconds": spec.duration_seconds,
                "keep": [
                    {
                        "in": range_item.start_seconds,
                        "out": range_item.end_seconds,
                        "duration_seconds": range_item.duration_seconds,
                    }
                    for range_item in spec.ranges
                ],
            }
            for spec in keep_specs
        ],
        "sequences": sequence_summaries,
        "warnings": all_warnings,
    }
    json_path = reports_dir / f"{project_path.stem}_keep_apply.json"
    txt_path = reports_dir / f"{project_path.stem}_keep_apply.txt"
    json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(
        build_keep_apply_report(report_payload, exported_project=exported_project),
        encoding="utf-8",
    )
    progress(f"Keep-apply completed. Report: {json_path}")
    return json_path, txt_path, exported_project


def build_keep_apply_report(
    payload: dict[str, object],
    *,
    exported_project: Path | None = None,
) -> str:
    lines = [
        "Sequence Keep Apply",
        "=" * 72,
        f"Source project: {payload.get('source_project_path')}",
        f"Ripple compact: {payload.get('ripple_compact')}",
    ]
    if payload.get("prin_path"):
        lines.append(f"Reference .prin: {payload['prin_path']}")
    if exported_project is not None:
        lines.append(f"Output project: {exported_project}")
    lines.extend(["", "Keep specs:", "-" * 72])
    for spec in payload.get("keep_specs") or []:
        if not isinstance(spec, dict):
            continue
        lines.append(f"- {spec.get('file')}")
        if spec.get("duration_seconds") is not None and not spec.get("keep"):
            lines.append(
                f"    DURATION {float(spec.get('duration_seconds', 0)):.3f}s from current InPoint"
            )
        for keep_range in spec.get("keep") or []:
            if not isinstance(keep_range, dict):
                continue
            lines.append(
                f"    KEEP {float(keep_range.get('in', 0)):8.3f}s - "
                f"{float(keep_range.get('out', 0)):8.3f}s "
                f"({float(keep_range.get('duration_seconds', 0)):.3f}s)"
            )

    lines.extend(["", "How to use in Premiere:", "-" * 72])
    lines.extend(
        [
            "1. Open the output .prproj. The original project file was not modified.",
            "2. Sequence names, bins, and unlisted clips stay as they were.",
            "3. Listed media files keep only the specified source ranges; unused pieces are removed.",
            "4. Linked audio for those files is trimmed to the same ranges.",
            "5. With ripple_compact=true the following clips move left to close the deleted gaps.",
        ]
    )

    warnings = [str(item) for item in (payload.get("warnings") or [])]
    if warnings:
        lines.extend(["", "Warnings:", "-" * 72])
        lines.extend(f"- {warning}" for warning in warnings)

    lines.extend(["", "Sequence plans:", "-" * 72])
    for summary in payload.get("sequences") or []:
        if not isinstance(summary, dict):
            continue
        lines.append(
            f"{summary.get('sequence_name')}: matched {summary.get('matched_clips')} / "
            f"{summary.get('clip_count')} clips, keep {float(summary.get('keep_seconds', 0)):.2f}s, "
            f"drop {float(summary.get('drop_seconds', 0)):.2f}s"
        )
        for clip in summary.get("clips") or []:
            if not isinstance(clip, dict):
                continue
            lines.append(
                f"  #{int(clip.get('order_index', 0)):03d}  {clip.get('name')}  "
                f"[{clip.get('decision')}]  "
                f"old {float(clip.get('duration_seconds', 0)):.3f}s -> "
                f"keep {float(clip.get('keep_seconds', 0)):.3f}s"
            )
            for segment in clip.get("keep_ranges") or []:
                if not isinstance(segment, dict):
                    continue
                lines.append(
                    f"      source {float(segment.get('source_in_seconds', 0)):.3f}-"
                    f"{float(segment.get('source_out_seconds', 0)):.3f}s"
                )
    lines.append("")
    return "\n".join(lines)


def _load_keep_job_from_config(
    payload: dict[str, object],
    config_path: Path,
) -> tuple[dict[str, object], list[MediaKeepSpec], Path | None]:
    keep_ranges_value = str(payload.get("keep_ranges_path") or "").strip()
    if keep_ranges_value:
        keep_ranges_path = Path(keep_ranges_value)
        if not keep_ranges_path.is_file() and not keep_ranges_path.is_absolute():
            keep_ranges_path = (config_path.parent / keep_ranges_path).resolve()
        if not keep_ranges_path.is_file():
            raise FileNotFoundError(f"Keep-ranges JSON does not exist: {keep_ranges_path}")
        keep_payload = json.loads(keep_ranges_path.read_text(encoding="utf-8"))
        if not isinstance(keep_payload, dict):
            raise ValueError(f"Keep-ranges JSON must be an object: {keep_ranges_path}")
        return keep_payload, load_media_keep_specs(keep_payload), keep_ranges_path
    if "operations" in payload or "clips" in payload:
        return payload, load_media_keep_specs(payload), None
    raise ValueError(
        "Keep-apply config must contain 'keep_ranges_path', inline 'operations', or inline 'clips'."
    )


def _resolve_existing_project_path(
    payload: dict[str, object],
    keep_payload: dict[str, object],
) -> Path:
    project_value = _first_non_empty_text(payload.get("project_path"), keep_payload.get("project_path"))
    if not project_value:
        raise ValueError("Keep-apply config must contain 'project_path'.")
    project_path = Path(project_value).expanduser()
    if not project_path.is_file():
        raise FileNotFoundError(f"Premiere project file not found: {project_path}")
    return project_path


def _prefer_import_project_if_empty(
    project_path: Path,
    sequence_name: str | None,
    progress: Callable[[str], None],
) -> Path:
    try:
        parse_premiere_project_sequence_visual_clips(project_path, sequence_name)
        return project_path
    except PremiereProjectError:
        candidate = project_path.with_name(f"{project_path.stem}_import{project_path.suffix}")
        if not candidate.is_file() or candidate.resolve() == project_path.resolve():
            return project_path
        try:
            parse_premiere_project_sequence_visual_clips(candidate, sequence_name)
        except PremiereProjectError:
            return project_path
        progress(
            f"Source project has no visual clips; using imported sibling '{candidate.name}'."
        )
        return candidate


def _first_non_empty_text(*values: object) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _resolve_target_sequence_names(root, source_sequence_name: str | None) -> list[str]:
    named = list_named_project_sequence_names(root)
    if source_sequence_name:
        normalized = source_sequence_name.casefold()
        for name in named:
            if name.casefold() == normalized:
                return [name]
        raise PremiereProjectError(f"Sequence '{source_sequence_name}' was not found in the project.")
    if not named:
        raise PremiereProjectError("No named sequences were found in the Premiere project.")
    return named


def _build_sequence_plan_summary(
    *,
    sequence_name: str,
    clips,
    keep_specs: list[MediaKeepSpec],
) -> dict[str, object]:
    specs_by_key = {spec.match_key: spec for spec in keep_specs}
    clip_rows: list[dict[str, object]] = []
    matched_keys: set[str] = set()
    keep_seconds = 0.0
    drop_seconds = 0.0
    for clip in clips:
        match_key = Path(clip.source_path or clip.name).name.casefold()
        spec = specs_by_key.get(match_key)
        if spec is None:
            clip_rows.append(
                {
                    "order_index": clip.order_index,
                    "name": clip.name,
                    "source_path": clip.source_path,
                    "decision": "unchanged",
                    "duration_seconds": ticks_to_seconds(clip.duration),
                    "keep_seconds": ticks_to_seconds(clip.duration),
                    "drop_seconds": 0.0,
                    "keep_ranges": [],
                }
            )
            continue
        matched_keys.add(match_key)
        intersections = resolve_keep_windows_ticks(spec, clip.in_point)
        clip_keep = sum(ticks_to_seconds(end - start) for start, end in intersections)
        clip_drop = max(0.0, ticks_to_seconds(clip.duration) - clip_keep)
        keep_seconds += clip_keep
        drop_seconds += clip_drop
        restored = [
            {
                "source_in_seconds": ticks_to_seconds(start),
                "source_out_seconds": ticks_to_seconds(end),
            }
            for start, end in intersections
            if end <= clip.in_point or start >= clip.out_point
        ]
        clip_rows.append(
            {
                "order_index": clip.order_index,
                "name": clip.name,
                "source_path": clip.source_path,
                "decision": "keep" if intersections else "removed",
                "duration_seconds": ticks_to_seconds(clip.duration),
                "keep_seconds": clip_keep,
                "drop_seconds": clip_drop,
                "keep_ranges": [
                    {
                        "source_in_seconds": ticks_to_seconds(start),
                        "source_out_seconds": ticks_to_seconds(end),
                    }
                    for start, end in intersections
                ],
                "restored_ranges": restored,
            }
        )
    missing = [spec.file_name for spec in keep_specs if spec.match_key not in matched_keys]
    return {
        "sequence_name": sequence_name,
        "clip_count": len(clips),
        "matched_clips": len(matched_keys),
        "keep_seconds": keep_seconds,
        "drop_seconds": drop_seconds,
        "missing_keep_files": missing,
        "clips": clip_rows,
    }


def _reject_overlapping_ranges(file_name: str, ranges: list[KeepRange]) -> None:
    for previous, current in zip(ranges, ranges[1:]):
        if current.start_seconds < previous.end_seconds:
            raise ValueError(
                f"Keep ranges for '{file_name}' overlap: "
                f"{previous.start_seconds:.3f}-{previous.end_seconds:.3f}s and "
                f"{current.start_seconds:.3f}-{current.end_seconds:.3f}s."
            )


def _build_progress_reporter(log_path: Path) -> Callable[[str], None]:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def report(message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")

    return report
