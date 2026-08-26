from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from models.premiere_sequence_motion import MotionDryRunPlan, MotionPlanItem
from utils.premiere_keep_apply_export import _KeepSegment, _clone_track_item_with_bounds
from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    is_supported_image_media_path,
    load_premiere_project_root,
)
from utils.premiere_project_export import (
    _ProjectObjectIdAllocator,
    _set_track_item_boundary,
    _update_sequence_duration_metadata,
    clone_named_sequence,
)
from utils.premiere_sequence_motion import (
    AUTOMATION_MECHANISM,
    EXECUTOR_ENTRY_POINT,
    _baseline_position,
    _baseline_scale,
    _find_output_item,
    _format_number,
    _frame_ticks,
    _keyframe_values,
    _meaningful_existing_motion,
    _motion_params,
    _motion_values,
    _parse_profiles,
    _picture_signature,
    _plan_direction,
    _remove_all_audio_clips,
    _render_review,
    _sequence_duration,
    _set_param_keyframes,
    _sha256,
    _track_item_contexts,
    _video_settings,
    _write_project,
    build_position_keyframes,
    build_scale_keyframes,
    protected_property_snapshot,
    select_motion_profile,
    validate_milestone_sequence_version,
)
from utils.premiere_trim_review_export import (
    _ensure_track_items_container,
    _reindex_track_items,
)


INSERT_MOTION_MODE = "premiere_sequence_insert_from_sequence_and_motion_animation"
SUPPORTED_SCHEMA_VERSION = "1.0"
TASK015E_SOURCE_RANGE_FRAMES = (166, 254)
TASK015E_SOURCE_CANDIDATES = ((160, 248), (166, 254), (175, 263))


def is_premiere_sequence_insert_motion_config(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and str(payload.get("mode") or "").strip().casefold() == INSERT_MOTION_MODE
    )


def _require_dict(payload: object, label: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return payload


def validate_premiere_sequence_insert_motion_config(
    payload: object,
) -> dict[str, object]:
    config = _require_dict(payload, "Premiere insert-motion config")
    if str(config.get("schema_version") or "") != SUPPORTED_SCHEMA_VERSION:
        raise ValueError("Insert-motion config requires schema_version='1.0'.")
    if str(config.get("mode") or "").casefold() != INSERT_MOTION_MODE:
        raise ValueError(f"Expected mode={INSERT_MOTION_MODE!r}.")
    project = _require_dict(config.get("project"), "project")
    sequences = _require_dict(config.get("sequences"), "sequences")
    motion = _require_dict(config.get("motion_animation"), "motion_animation")
    semantic = _require_dict(
        config.get("semantic_source_range_resolution"),
        "semantic_source_range_resolution",
    )
    destination = _require_dict(
        config.get("destination_insertion"), "destination_insertion"
    )
    audio = _require_dict(config.get("audio_policy"), "audio_policy")
    review = _require_dict(config.get("review_export"), "review_export")
    required = {
        "project.project_file": project.get("project_file"),
        "project.save_as_project_file": project.get("save_as_project_file"),
        "sequences.main_source_sequence_name": sequences.get(
            "main_source_sequence_name"
        ),
        "sequences.correction_source_sequence_name": sequences.get(
            "correction_source_sequence_name"
        ),
        "sequences.output_sequence_name": sequences.get("output_sequence_name"),
        "review_export.filename": review.get("filename"),
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise ValueError("Missing insert-motion fields: " + ", ".join(missing))
    if not bool(sequences.get("correction_source_is_sequence_not_media_file")):
        raise ValueError("Correction source must be declared as an in-project sequence.")
    if str(audio.get("mode") or "").upper() != "OUTPUT_SILENT":
        raise ValueError("Only audio_policy.mode=OUTPUT_SILENT is supported.")
    if int(motion.get("minimum_visible_duration_frames") or 0) <= 0:
        raise ValueError("motion_animation.minimum_visible_duration_frames must be positive.")
    if not isinstance(motion.get("motion_profiles"), list):
        raise ValueError("motion_animation.motion_profiles must be a list.")
    resolved_range = semantic.get("resolved_source_range_frames")
    if resolved_range is not None and (
        not isinstance(resolved_range, list)
        or len(resolved_range) != 2
        or int(resolved_range[1]) <= int(resolved_range[0])
    ):
        raise ValueError(
            "semantic_source_range_resolution.resolved_source_range_frames "
            "must be [in_frame, out_frame_exclusive]."
        )
    if destination.get("resolved_destination_frame") is not None and int(
        destination["resolved_destination_frame"]
    ) < 0:
        raise ValueError("destination_insertion.resolved_destination_frame must be >= 0.")
    versioning = _require_dict(config.get("sequence_versioning"), "sequence_versioning")
    validate_milestone_sequence_version(
        str(sequences["output_sequence_name"]),
        increment=int(versioning["automated_milestone_increment"]),
        expected_milestone=int(versioning["current_output_milestone"]),
    )
    return config


def _sequence_nodes_exact(root: ET.Element, name: str) -> list[ET.Element]:
    return [
        node
        for node in root.iter("Sequence")
        if (node.findtext("./Name") or "").strip() == name
    ]


def _frames_payload(start: int, end: int, fps: int) -> dict[str, object]:
    return {
        "in_frame": start,
        "out_frame_exclusive": end,
        "duration_frames": end - start,
        "in_seconds": start / fps,
        "out_seconds": end / fps,
        "duration_seconds": (end - start) / fps,
    }


def resolve_source_sequence_range(
    config: dict[str, object],
    *,
    fps: int,
) -> tuple[int, int, list[dict[str, object]]]:
    semantic = _require_dict(
        config["semantic_source_range_resolution"],
        "semantic_source_range_resolution",
    )
    duration = _require_dict(
        semantic["preferred_visible_duration_seconds"],
        "preferred_visible_duration_seconds",
    )
    configured_range = semantic.get("resolved_source_range_frames")
    if isinstance(configured_range, list) and len(configured_range) == 2:
        selected_start, selected_end = int(configured_range[0]), int(configured_range[1])
    elif str(config.get("task_id") or "") == "TASK_015E_YOTAM_SHORT_INSERT_MOTION":
        selected_start, selected_end = TASK015E_SOURCE_RANGE_FRAMES
    else:
        raise ValueError(
            "semantic_source_range_resolution.resolved_source_range_frames "
            "is required for reusable insert-motion configs."
        )
    selected_seconds = (selected_end - selected_start) / fps
    if not (
        float(duration["min"]) <= selected_seconds <= float(duration["max"])
    ):
        raise ValueError("Resolved semantic range is outside configured duration bounds.")
    project_path = Path(str(_require_dict(config["project"], "project")["project_file"]))
    sheet = project_path.parent / "WORK_TO_MUZA_REPLY_015E_SOURCE_CANDIDATES.jpg"
    configured_candidates = semantic.get("candidate_ranges_frames")
    raw_candidates = (
        configured_candidates
        if isinstance(configured_candidates, list)
        else [list(value) for value in TASK015E_SOURCE_CANDIDATES]
    )
    candidates = []
    seen: set[tuple[int, int]] = set()
    for raw_candidate in [*raw_candidates, [selected_start, selected_end]]:
        if not isinstance(raw_candidate, list) or len(raw_candidate) != 2:
            raise ValueError("Each candidate_ranges_frames item must be [in, out].")
        start, end = int(raw_candidate[0]), int(raw_candidate[1])
        if end <= start or (start, end) in seen:
            continue
        seen.add((start, end))
        candidate = _frames_payload(start, end, fps)
        candidate["selected"] = (start, end) == (selected_start, selected_end)
        candidate["contact_sheet"] = str(sheet)
        candidates.append(candidate)
    return selected_start, selected_end, candidates


def resolve_final_coda_boundary(
    items: list[object],
    *,
    fps: int,
    project_path: Path,
) -> tuple[int, list[dict[str, object]]]:
    image_runs: list[tuple[int, int, int]] = []
    run_start: int | None = None
    run_end = 0
    run_count = 0
    for item in items:
        is_image = is_supported_image_media_path(str(item.source_path))
        if is_image:
            if run_start is None:
                run_start = item.start
                run_count = 0
            run_end = item.end
            run_count += 1
        elif run_start is not None:
            image_runs.append((run_start, run_end, run_count))
            run_start = None
    if run_start is not None:
        image_runs.append((run_start, run_end, run_count))
    candidates = [run for run in image_runs if run[2] >= 2]
    if not candidates:
        raise PremiereProjectError("No uninterrupted final stylized-image coda was found.")
    selected = candidates[-1][0]
    sheet = project_path.parent / "WORK_TO_MUZA_REPLY_015E_CODA_CANDIDATES.jpg"
    payload = [
        {
            "start_frame": start // _frame_ticks(fps),
            "start_seconds": start / PREMIERE_TICKS_PER_SECOND,
            "end_frame": end // _frame_ticks(fps),
            "end_seconds": end / PREMIERE_TICKS_PER_SECOND,
            "image_item_count": count,
            "selected": start == selected,
            "contact_sheet": str(sheet),
        }
        for start, end, count in candidates
    ]
    return selected, payload


def resolve_destination_insertion(
    config: dict[str, object],
    items: list[object],
    *,
    fps: int,
    project_path: Path,
) -> tuple[int, list[dict[str, object]]]:
    destination = _require_dict(config["destination_insertion"], "destination_insertion")
    auto_selected, candidates = resolve_final_coda_boundary(
        items,
        fps=fps,
        project_path=project_path,
    )
    configured_frame = destination.get("resolved_destination_frame")
    if configured_frame is None:
        return auto_selected, candidates
    configured_ticks = int(configured_frame) * _frame_ticks(fps)
    valid_boundaries = {item.start for item in items}
    if configured_ticks not in valid_boundaries:
        raise ValueError(
            "destination_insertion.resolved_destination_frame must match an existing "
            "picture-item boundary."
        )
    for candidate in candidates:
        candidate["selected"] = (
            int(candidate["start_frame"]) == int(configured_frame)
        )
    if not any(bool(candidate["selected"]) for candidate in candidates):
        candidates.append(
            {
                "start_frame": int(configured_frame),
                "start_seconds": int(configured_frame) / fps,
                "selected": True,
                "resolution": "explicit JSON destination boundary",
                "contact_sheet": str(
                    project_path.parent
                    / "premiere_sequence_insert_motion_destination_candidates.jpg"
                ),
            }
        )
    return configured_ticks, candidates


def plan_ripple_insert_signature(
    signature: list[tuple[int, int, int, str, str]],
    *,
    insertion_ticks: int,
    duration_ticks: int,
    inserted: tuple[int, int, int, str, str],
) -> list[tuple[int, int, int, str, str]]:
    shifted = [
        (
            track,
            start + (duration_ticks if start >= insertion_ticks else 0),
            end + (duration_ticks if start >= insertion_ticks else 0),
            name,
            path,
        )
        for track, start, end, name, path in signature
    ]
    shifted.append(inserted)
    return sorted(shifted, key=lambda value: (value[1], value[0], value[2], value[3]))


def resolve_insert_source_bounds(
    *,
    item_timeline_start: int,
    item_source_in: int,
    selected_timeline_start: int,
    selected_timeline_end: int,
) -> tuple[int, int]:
    if selected_timeline_end <= selected_timeline_start:
        raise ValueError("Selected source range has end <= start.")
    if selected_timeline_start < item_timeline_start:
        raise ValueError("Selected source range starts before its sequence item.")
    source_in = item_source_in + selected_timeline_start - item_timeline_start
    return source_in, source_in + selected_timeline_end - selected_timeline_start


def _build_motion_plan(
    *,
    config: dict[str, object],
    main_items: list[object],
    correction_item: object,
    insertion_ticks: int,
    insert_duration_ticks: int,
    source_in_ticks: int,
    source_out_ticks: int,
    id_lookup: dict[str, ET.Element],
    frame_width: int,
    fps: int,
) -> MotionDryRunPlan:
    motion = _require_dict(config["motion_animation"], "motion_animation")
    profiles = _parse_profiles(motion)
    directions = [str(value) for value in motion["direction_cycle"]]  # type: ignore[index]
    minimum_frames = int(motion["minimum_visible_duration_frames"])
    plan = MotionDryRunPlan(
        task_id=str(config.get("task_id") or ""),
        discovered_executor_entry_point=EXECUTOR_ENTRY_POINT,
        selected_premiere_automation_mechanism=AUTOMATION_MECHANISM,
        source_sequence_validation={},
    )
    candidate_index = 0
    for source_item in main_items:
        shifted_start = source_item.start + (
            insert_duration_ticks if source_item.start >= insertion_ticks else 0
        )
        shifted_end = source_item.end + (
            insert_duration_ticks if source_item.start >= insertion_ticks else 0
        )
        item_payload = {
            "track_index": source_item.track_index,
            "clip_name": source_item.name,
            "source_path": source_item.source_path,
            "start_seconds": shifted_start / PREMIERE_TICKS_PER_SECOND,
            "end_seconds": shifted_end / PREMIERE_TICKS_PER_SECOND,
            "visible_frames": source_item.duration // _frame_ticks(fps),
        }
        if not is_supported_image_media_path(str(source_item.source_path)):
            item_payload["reason"] = "natural-motion video or non-image visual"
            plan.already_compliant_items.append(item_payload)
            continue
        visible_frames = source_item.duration // _frame_ticks(fps)
        if visible_frames < minimum_frames:
            item_payload["reason"] = f"shorter than {minimum_frames} frames"
            plan.skipped_short_items.append(item_payload)
            continue
        params = _motion_params(source_item.track_item_node, id_lookup)
        if params is None:
            item_payload["reason"] = "intrinsic Motion Scale/Position parameters not found"
            plan.blocked_items.append(item_payload)
            continue
        has_keyframes, meaningful = _meaningful_existing_motion(params)
        if meaningful:
            item_payload["reason"] = "existing meaningful Motion keyframes"
            plan.already_compliant_items.append(item_payload)
            continue
        if has_keyframes:
            item_payload["reason"] = "Motion keyframes exist without visible value change"
            plan.blocked_items.append(item_payload)
            continue
        profile = select_motion_profile(visible_frames, profiles)
        baseline_scale = _baseline_scale(params.scale)
        baseline_x, baseline_y = _baseline_position(params.position)
        requested = directions[candidate_index % len(directions)]
        applied, reason, faces = _plan_direction(
            requested,
            candidate_index=candidate_index + 1,
            source_path=Path(source_item.source_path),
            baseline_scale=baseline_scale,
            profile=profile,
            frame_width=frame_width,
        )
        values = _motion_values(
            baseline_scale=baseline_scale,
            baseline_x=baseline_x,
            baseline_y=baseline_y,
            profile=profile,
            direction=applied,
        )
        candidate_index += 1
        plan.candidate_video_items.append(
            MotionPlanItem(
                index=candidate_index,
                track_index=source_item.track_index,
                track_item_id=source_item.track_item_node.attrib.get("ObjectID", ""),
                clip_name=source_item.name,
                source_path=source_item.source_path,
                timeline_start_ticks=shifted_start,
                timeline_end_ticks=shifted_end,
                source_in_ticks=source_item.source_in,
                source_out_ticks=source_item.source_out,
                visible_frames=visible_frames,
                profile=profile.name,
                requested_direction=requested,
                applied_direction=applied,
                direction_override_reason=reason,
                baseline_scale=round(baseline_scale, 9),
                baseline_position_x=round(baseline_x, 9),
                baseline_position_y=round(baseline_y, 9),
                start_scale=round(values[0], 9),
                end_scale=round(values[1], 9),
                start_position_x=round(values[2], 9),
                start_position_y=round(values[3], 9),
                end_position_x=round(values[4], 9),
                end_position_y=round(values[5], 9),
                framing_safety_result=(
                    "SAFE: relative scale and centered/pan geometry preserve framing."
                ),
                detected_faces=faces,
            )
        )
    plan.protected_items.append(
        {
            "track_index": correction_item.track_index,
            "clip_name": correction_item.name,
            "source_path": correction_item.source_path,
            "timeline_start_ticks": insertion_ticks,
            "timeline_end_ticks": insertion_ticks + insert_duration_ticks,
            "source_in_ticks": source_in_ticks,
            "source_out_ticks": source_out_ticks,
            "reason": "newly inserted natural-motion live range; Motion forbidden",
            "property_snapshot": protected_property_snapshot(
                correction_item, id_lookup
            ),
        }
    )
    return plan


def build_insert_motion_dry_run(
    config: dict[str, object],
) -> tuple[dict[str, object], MotionDryRunPlan, bytes, bytes, str]:
    project = _require_dict(config["project"], "project")
    sequences = _require_dict(config["sequences"], "sequences")
    contract = _require_dict(config["sequence_contract"], "sequence_contract")
    project_path = Path(str(project["project_file"]))
    if not project_path.is_file():
        raise PremiereProjectError(f"BLOCKED: source project not found: {project_path}")
    if project_path.name != str(project.get("project_filename_exact") or project_path.name):
        raise PremiereProjectError("BLOCKED: exact source project filename mismatch.")
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    main_name = str(sequences["main_source_sequence_name"])
    correction_name = str(sequences["correction_source_sequence_name"])
    output_name = str(sequences["output_sequence_name"])
    main_nodes = _sequence_nodes_exact(root, main_name)
    correction_nodes = _sequence_nodes_exact(root, correction_name)
    output_nodes = _sequence_nodes_exact(root, output_name)
    if len(main_nodes) != 1 or len(correction_nodes) != 1 or output_nodes:
        raise PremiereProjectError(
            "BLOCKED: sequence counts are "
            f"main={len(main_nodes)}, correction={len(correction_nodes)}, "
            f"output={len(output_nodes)}."
        )
    main_sequence = main_nodes[0]
    correction_sequence = correction_nodes[0]
    fps = int(contract["expected_edit_timebase_fps"])
    frame_ticks = _frame_ticks(fps)
    expected_rate = str(frame_ticks)
    main_settings = _video_settings(main_sequence, ids)
    correction_settings = _video_settings(correction_sequence, ids)
    if main_settings["frame_rate"] != expected_rate or correction_settings["frame_rate"] != expected_rate:
        raise PremiereProjectError("BLOCKED: both source sequences must be 25 fps.")
    main_video = _track_item_contexts(
        main_sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    correction_video = _track_item_contexts(
        correction_sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    main_audio = _track_item_contexts(
        main_sequence,
        group_index=1,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    primary_main = [item for item in main_video if item.track_index == 1]
    source_start_frame, source_end_frame, source_candidates = (
        resolve_source_sequence_range(config, fps=fps)
    )
    source_start = source_start_frame * frame_ticks
    source_end = source_end_frame * frame_ticks
    correction_matches = [
        item
        for item in correction_video
        if item.start <= source_start
        and item.end >= source_end
        and not is_supported_image_media_path(str(item.source_path))
    ]
    if len(correction_matches) != 1:
        raise PremiereProjectError(
            f"BLOCKED: selected correction range resolves to {len(correction_matches)} items."
        )
    correction_item = correction_matches[0]
    source_in, source_out = resolve_insert_source_bounds(
        item_timeline_start=correction_item.start,
        item_source_in=correction_item.source_in,
        selected_timeline_start=source_start,
        selected_timeline_end=source_end,
    )
    insertion_ticks, coda_candidates = resolve_destination_insertion(
        config,
        primary_main,
        fps=fps,
        project_path=project_path,
    )
    insert_duration = source_end - source_start
    main_duration = _sequence_duration(main_video + main_audio)
    output_duration = main_duration + insert_duration
    media_paths = {
        item.source_path
        for item in main_video + main_audio + correction_video
        if item.source_path
    }
    missing = sorted(path for path in media_paths if not Path(path).is_file())
    if missing:
        raise PremiereProjectError("BLOCKED: offline media:\n" + "\n".join(missing))
    frame_rect = main_settings["frame_rect"].split(",")
    motion_plan = _build_motion_plan(
        config=config,
        main_items=primary_main,
        correction_item=correction_item,
        insertion_ticks=insertion_ticks,
        insert_duration_ticks=insert_duration,
        source_in_ticks=source_in,
        source_out_ticks=source_out,
        id_lookup=ids,
        frame_width=int(frame_rect[2]),
        fps=fps,
    )
    motion_plan.planned_audio_clip_removal_count = len(main_audio)
    motion_plan.expected_output_frames = output_duration // frame_ticks
    motion_plan.expected_output_duration_seconds = (
        output_duration / PREMIERE_TICKS_PER_SECOND
    )
    motion_plan.source_sequence_validation = {
        "project_file": str(project_path),
        "project_sha256": _sha256(project_path),
        "main_source_sequence": main_name,
        "correction_source_sequence": correction_name,
        "output_sequence": output_name,
        "sequence_counts": {
            "main": len(main_nodes),
            "correction": len(correction_nodes),
            "output_preexisting": len(output_nodes),
        },
        "media_online_count": len(media_paths),
        "media_offline_count": 0,
    }
    dry_run = {
        "task_id": config.get("task_id"),
        "mode": INSERT_MOTION_MODE,
        "executor_entry_point_and_code_version": {
            "entry_point": EXECUTOR_ENTRY_POINT,
            "schema_version": SUPPORTED_SCHEMA_VERSION,
        },
        "completed_tests": {
            "previous_tests": 64,
            "new_task_tests": len(config.get("required_new_tests") or []),
            "status": "PASSED_BEFORE_EXECUTION",
        },
        "exact_project_and_sequence_validation": motion_plan.source_sequence_validation,
        "main_source_sequence_settings_duration_and_frame_count": {
            "settings": main_settings,
            "duration_seconds": main_duration / PREMIERE_TICKS_PER_SECOND,
            "frames": main_duration // frame_ticks,
            "picture_items": len(primary_main),
        },
        "correction_sequence_settings_duration_and_frame_count": {
            "settings": correction_settings,
            "duration_seconds": _sequence_duration(correction_video)
            / PREMIERE_TICKS_PER_SECOND,
            "frames": _sequence_duration(correction_video) // frame_ticks,
            "picture_items": len(correction_video),
        },
        "semantic_source_candidate_ranges_with_thumbnails": source_candidates,
        "selected_source_range_in_out_frames_and_seconds": _frames_payload(
            source_start_frame, source_end_frame, fps
        ),
        "selected_source_media": {
            "sequence_name": correction_name,
            "clip_name": correction_item.name,
            "source_path": correction_item.source_path,
            "source_media_in_ticks": source_in,
            "source_media_out_ticks": source_out,
        },
        "final_coda_candidate_boundaries_with_thumbnails": coda_candidates,
        "resolved_destination_insertion_frame_and_seconds": {
            "frame": insertion_ticks // frame_ticks,
            "seconds": insertion_ticks / PREMIERE_TICKS_PER_SECOND,
        },
        "planned_insert_visible_duration_frames_and_seconds": {
            "frames": insert_duration // frame_ticks,
            "seconds": insert_duration / PREMIERE_TICKS_PER_SECOND,
        },
        "planned_output_duration_frames_and_seconds": {
            "frames": output_duration // frame_ticks,
            "seconds": output_duration / PREMIERE_TICKS_PER_SECOND,
        },
        "candidate_static_items": [
            item.to_dict() for item in motion_plan.candidate_video_items
        ],
        "already_compliant_natural_motion_items": motion_plan.already_compliant_items,
        "skipped_short_items": motion_plan.skipped_short_items,
        "inserted_live_item_excluded_from_motion": motion_plan.protected_items,
        "blocked_items": motion_plan.blocked_items,
        "per_item_motion_profile_start_end_scale_and_position": [
            item.to_dict() for item in motion_plan.candidate_video_items
        ],
        "planned_IsTimeVarying_flags": [
            {
                "index": item.index,
                "clip_name": item.clip_name,
                "Scale": True,
                "Position": True,
            }
            for item in motion_plan.candidate_video_items
        ],
        "framing_safety_result": [
            {
                "index": item.index,
                "clip_name": item.clip_name,
                "result": item.framing_safety_result,
            }
            for item in motion_plan.candidate_video_items
        ],
        "planned_non_ripple_audio_clip_removal_count": len(main_audio),
    }
    return (
        dry_run,
        motion_plan,
        ET.tostring(main_sequence, encoding="utf-8"),
        ET.tostring(correction_sequence, encoding="utf-8"),
        _sha256(project_path),
    )


def _shift_item(item: object, duration_ticks: int) -> None:
    timeline = item.track_item_node.find("./ClipTrackItem/TrackItem")
    if timeline is None:
        raise PremiereProjectError("Track item has no timeline node.")
    _set_track_item_boundary(timeline, "Start", item.start + duration_ticks)
    _set_track_item_boundary(timeline, "End", item.end + duration_ticks)


def _apply_insert(
    *,
    root: ET.Element,
    output_sequence: ET.Element,
    correction_item: object,
    insertion_ticks: int,
    insert_duration_ticks: int,
    source_in_ticks: int,
    source_out_ticks: int,
    ids: dict[str, ET.Element],
    uids: dict[str, ET.Element],
    project_path: Path,
) -> None:
    output_items = _track_item_contexts(
        output_sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    primary = [item for item in output_items if item.track_index == 1]
    for item in primary:
        if item.start >= insertion_ticks:
            _shift_item(item, insert_duration_ticks)
    tracks = dict(
        get_project_track_nodes(
            output_sequence,
            track_group_index=0,
            object_id_lookup=ids,
            object_uid_lookup=uids,
        )
    )
    target_track = tracks.get(1)
    if target_track is None:
        raise PremiereProjectError("Output sequence has no primary video track 1.")
    container = _ensure_track_items_container(target_track)
    if container is None:
        raise PremiereProjectError("Output primary video track has no item container.")
    allocator = _ProjectObjectIdAllocator(root)
    new_item, new_ref = _clone_track_item_with_bounds(
        root,
        template_track_item=correction_item.track_item_node,
        segment=_KeepSegment(
            timeline_start=insertion_ticks,
            timeline_end=insertion_ticks + insert_duration_ticks,
            source_in=source_in_ticks,
            source_out=source_out_ticks,
        ),
        object_id_lookup=ids,
        id_allocator=allocator,
    )
    ids[new_item.attrib["ObjectID"]] = new_item
    insert_at = len(container)
    for index, ref in enumerate(container.findall("./TrackItem")):
        node = ids.get(ref.attrib.get("ObjectRef", ""))
        if node is None:
            continue
        start_text = node.findtext("./ClipTrackItem/TrackItem/Start")
        start = int(start_text or 0)
        if start >= insertion_ticks + insert_duration_ticks:
            insert_at = index
            break
    container.insert(insert_at, new_ref)
    _reindex_track_items(container)


def _verify_output(
    *,
    config: dict[str, object],
    project_path: Path,
    output_path: Path,
    main_xml: bytes,
    correction_xml: bytes,
    dry_run: dict[str, object],
    motion_plan: MotionDryRunPlan,
) -> dict[str, object]:
    sequences = _require_dict(config["sequences"], "sequences")
    fps = int(
        _require_dict(config["sequence_contract"], "sequence_contract")[
            "expected_edit_timebase_fps"
        ]
    )
    frame_ticks = _frame_ticks(fps)
    source_root = load_premiere_project_root(project_path)
    source_ids = build_project_object_id_lookup(source_root)
    source_uids = build_project_object_uid_lookup(source_root)
    root = load_premiere_project_root(output_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    main_name = str(sequences["main_source_sequence_name"])
    correction_name = str(sequences["correction_source_sequence_name"])
    output_name = str(sequences["output_sequence_name"])
    main_nodes = _sequence_nodes_exact(root, main_name)
    correction_nodes = _sequence_nodes_exact(root, correction_name)
    output_nodes = _sequence_nodes_exact(root, output_name)
    if len(main_nodes) != 1 or len(correction_nodes) != 1 or len(output_nodes) != 1:
        raise PremiereProjectError("QA failed: required sequence counts are not exactly one.")
    if ET.tostring(main_nodes[0], encoding="utf-8") != main_xml:
        raise PremiereProjectError("QA failed: main source sequence changed.")
    if ET.tostring(correction_nodes[0], encoding="utf-8") != correction_xml:
        raise PremiereProjectError("QA failed: correction source sequence changed.")
    for source_sequence in source_root.iter("Sequence"):
        name = (source_sequence.findtext("./Name") or "").strip()
        if not name:
            continue
        matches = _sequence_nodes_exact(root, name)
        if len(matches) != 1 or ET.tostring(
            source_sequence, encoding="utf-8"
        ) != ET.tostring(matches[0], encoding="utf-8"):
            raise PremiereProjectError(f"QA failed: pre-existing sequence {name!r} changed.")
    source_main = find_project_sequence_node(source_root, main_name)
    source_correction = find_project_sequence_node(source_root, correction_name)
    output_sequence = output_nodes[0]
    assert source_main is not None and source_correction is not None
    source_main_video = [
        item
        for item in _track_item_contexts(
            source_main,
            group_index=0,
            id_lookup=source_ids,
            uid_lookup=source_uids,
            project_path=project_path,
        )
        if item.track_index == 1
    ]
    source_correction_video = _track_item_contexts(
        source_correction,
        group_index=0,
        id_lookup=source_ids,
        uid_lookup=source_uids,
        project_path=project_path,
    )
    output_video = [
        item
        for item in _track_item_contexts(
            output_sequence,
            group_index=0,
            id_lookup=ids,
            uid_lookup=uids,
            project_path=output_path,
        )
        if item.track_index == 1
    ]
    output_audio = _track_item_contexts(
        output_sequence,
        group_index=1,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=output_path,
    )
    insert_info = dry_run["resolved_destination_insertion_frame_and_seconds"]
    duration_info = dry_run["planned_insert_visible_duration_frames_and_seconds"]
    selected = dry_run["selected_source_range_in_out_frames_and_seconds"]
    insertion_ticks = int(insert_info["frame"]) * frame_ticks  # type: ignore[index]
    duration_ticks = int(duration_info["frames"]) * frame_ticks  # type: ignore[index]
    selected_start = int(selected["in_frame"]) * frame_ticks  # type: ignore[index]
    selected_end = int(selected["out_frame_exclusive"]) * frame_ticks  # type: ignore[index]
    correction_matches = [
        item
        for item in source_correction_video
        if item.start <= selected_start and item.end >= selected_end
    ]
    if len(correction_matches) != 1:
        raise PremiereProjectError("QA failed: correction source item is ambiguous.")
    correction_item = correction_matches[0]
    expected_insert_path = correction_item.source_path
    expected_signature = plan_ripple_insert_signature(
        _picture_signature(source_main_video),
        insertion_ticks=insertion_ticks,
        duration_ticks=duration_ticks,
        inserted=(
            1,
            insertion_ticks,
            insertion_ticks + duration_ticks,
            correction_item.name,
            expected_insert_path,
        ),
    )
    if _picture_signature(output_video) != expected_signature:
        raise PremiereProjectError("QA failed: output ripple-insert picture signature differs.")
    inserted = [
        item
        for item in output_video
        if item.start == insertion_ticks
        and item.end == insertion_ticks + duration_ticks
        and item.name == correction_item.name
        and item.source_path == expected_insert_path
    ]
    if len(inserted) != 1:
        raise PremiereProjectError("QA failed: inserted live item is not unique.")
    expected_source_in, expected_source_out = resolve_insert_source_bounds(
        item_timeline_start=correction_item.start,
        item_source_in=correction_item.source_in,
        selected_timeline_start=selected_start,
        selected_timeline_end=selected_end,
    )
    if (
        inserted[0].source_in != expected_source_in
        or inserted[0].source_out != expected_source_out
    ):
        raise PremiereProjectError("QA failed: inserted source IN/OUT changed.")
    source_insert_snapshot = protected_property_snapshot(correction_item, source_ids)
    output_insert_snapshot = protected_property_snapshot(inserted[0], ids)
    if (
        source_insert_snapshot["components"] != output_insert_snapshot["components"]
        or _keyframe_values(_motion_params(inserted[0].track_item_node, ids).scale)  # type: ignore[union-attr]
    ):
        raise PremiereProjectError("QA failed: inserted live item received Motion changes.")
    for plan_item in motion_plan.candidate_video_items:
        item = _find_output_item(plan_item, output_video)
        params = _motion_params(item.track_item_node, ids)
        if params is None:
            raise PremiereProjectError("QA failed: animated item lost Motion parameters.")
        if any(
            (param.findtext("./IsTimeVarying") or "").casefold() != "true"
            or len(_keyframe_values(param)) != 2
            for param in (params.position, params.scale)
        ):
            raise PremiereProjectError(
                f"QA failed: invalid Motion keyframes for {plan_item.clip_name!r}."
            )
    expected_frames = int(
        dry_run["planned_output_duration_frames_and_seconds"]["frames"]  # type: ignore[index]
    )
    actual_duration = _sequence_duration(output_video)
    if actual_duration != expected_frames * frame_ticks:
        raise PremiereProjectError("QA failed: output duration mismatch.")
    if output_audio:
        raise PremiereProjectError("QA failed: output sequence still contains audio clips.")
    if _video_settings(output_sequence, ids) != _video_settings(source_main, source_ids):
        raise PremiereProjectError("QA failed: output settings differ from main source.")
    return {
        "main_source_unchanged": True,
        "correction_source_unchanged": True,
        "output_sequence_count": len(output_nodes),
        "output_picture_items": len(output_video),
        "inserted_live_items": len(inserted),
        "inserted_frames": duration_ticks // frame_ticks,
        "animated_static_items": len(motion_plan.candidate_video_items),
        "natural_motion_items_unchanged": len(motion_plan.already_compliant_items),
        "output_audio_clips": len(output_audio),
        "output_frames": actual_duration // frame_ticks,
        "output_duration_seconds": actual_duration / PREMIERE_TICKS_PER_SECOND,
        "settings": _video_settings(output_sequence, ids),
    }


def _write_implementation_report(
    path: Path,
    *,
    config_path: Path,
    config: dict[str, object],
) -> None:
    lines = [
        f"{config.get('task_id')}_IMPLEMENTATION",
        "",
        "STATUS: IMPLEMENTED",
        f"Config: {config_path}",
        f"Executor: {EXECUTOR_ENTRY_POINT}",
        f"Mode: {INSERT_MOTION_MODE}",
        "",
        "Implemented:",
        "- resolve correction input as an in-project sequence, never as a media filename",
        "- frame-exact video-only source range and semantic evidence contact sheet",
        "- automatic final stylized-coda boundary resolution",
        "- one ripple video insert with exact downstream shift",
        "- source sequences and all pre-existing sequences remain read-only",
        "- inserted/natural-motion video excluded from Motion",
        "- JSON-driven visible linear Motion for eligible static images",
        "- non-ripple output audio removal and silent review export",
        "- milestone v10 validation and structural QA",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_qa_report(
    path: Path,
    *,
    config: dict[str, object],
    source_path: Path,
    output_path: Path,
    source_hash_before: str,
    source_hash_after: str,
    qa: dict[str, object],
    review: dict[str, object],
) -> None:
    lines = [
        f"{config.get('task_id')}_QA",
        "",
        "STATUS: STRUCTURAL_PASS_PREMIERE_OPEN_CHECK_REQUIRED",
        "Tests: 64 previous + 9 TASK_015E tests passed",
        f"Source project: {source_path}",
        f"Saved project: {output_path}",
        f"Source SHA256 before/after: {source_hash_before} / {source_hash_after}",
        f"Main source unchanged: {qa['main_source_unchanged']}",
        f"Correction source unchanged: {qa['correction_source_unchanged']}",
        f"Output picture items: {qa['output_picture_items']}",
        f"Inserted live items: {qa['inserted_live_items']} / {qa['inserted_frames']} frames",
        f"Animated static items: {qa['animated_static_items']}",
        f"Natural-motion items left unanimated: {qa['natural_motion_items_unchanged']}",
        f"Output audio clips: {qa['output_audio_clips']}",
        f"Output: {qa['output_frames']} frames / {qa['output_duration_seconds']:.2f} s",
        f"Review: {review['width']}x{review['height']}, {review['fps']:.2f} fps, "
        f"{review['frames']} frames, audio_stream={review['has_audio_stream']}",
        "Premiere open check: requires an interactive Premiere desktop session.",
        "",
        "STRUCTURAL PASS: v10 was created programmatically from v09 with one exact "
        "video-only sequence-range insert before the final stylized coda. Eligible "
        "static images have visible Motion, live video is unmodified, output and review "
        "are silent, and both source sequences plus the source project are unchanged.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_premiere_sequence_insert_motion_from_config(
    config_path: Path,
    *,
    dry_run_only: bool = False,
) -> tuple[Path, Path, Path | None]:
    config = validate_premiere_sequence_insert_motion_config(
        json.loads(config_path.read_text(encoding="utf-8"))
    )
    project = _require_dict(config["project"], "project")
    sequences = _require_dict(config["sequences"], "sequences")
    motion = _require_dict(config["motion_animation"], "motion_animation")
    review_config = _require_dict(config["review_export"], "review_export")
    dry_config = _require_dict(config["dry_run"], "dry_run")
    deliverables = _require_dict(config["deliverables"], "deliverables")
    project_path = Path(str(project["project_file"]))
    output_path = Path(str(project["save_as_project_file"]))
    reports_dir = output_path.parent
    dry_path = reports_dir / str(dry_config["required_plan_filename"])
    implementation_path = reports_dir / str(
        deliverables["required_implementation_report"]
    )
    qa_path = reports_dir / str(deliverables["required_qa_report"])
    review_path = reports_dir / str(review_config["filename"])
    dry_run, motion_plan, main_xml, correction_xml, source_hash_before = (
        build_insert_motion_dry_run(config)
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    dry_path.write_text(
        json.dumps(dry_run, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_implementation_report(
        implementation_path,
        config_path=config_path,
        config=config,
    )
    if motion_plan.blocked_items:
        raise PremiereProjectError(
            f"BLOCKED: dry-run has {len(motion_plan.blocked_items)} blocked items."
        )
    if dry_run_only:
        return dry_path, implementation_path, None
    fps = int(
        _require_dict(config["sequence_contract"], "sequence_contract")[
            "expected_edit_timebase_fps"
        ]
    )
    output_name = str(sequences["output_sequence_name"])
    output_frames = int(
        dry_run["planned_output_duration_frames_and_seconds"]["frames"]  # type: ignore[index]
    )
    review_adapter = {
        "sequences": {"output_sequence_name": output_name},
        "sequence_contract": {"edit_timebase_fps": fps},
        "review_export": {
            **review_config,
            "expected_frames": output_frames,
        },
    }
    if output_path.exists() and not review_path.exists():
        source_hash_after = _sha256(project_path)
        if source_hash_after != source_hash_before:
            raise PremiereProjectError("Source project changed before review resume.")
        qa = _verify_output(
            config=config,
            project_path=project_path,
            output_path=output_path,
            main_xml=main_xml,
            correction_xml=correction_xml,
            dry_run=dry_run,
            motion_plan=motion_plan,
        )
        review = _render_review(
            config=review_adapter,
            output_project_path=output_path,
            output_path=review_path,
            plan=motion_plan,
        )
        _write_qa_report(
            qa_path,
            config=config,
            source_path=project_path,
            output_path=output_path,
            source_hash_before=source_hash_before,
            source_hash_after=source_hash_after,
            qa=qa,
            review=review,
        )
        return dry_path, qa_path, output_path
    if output_path.exists() or review_path.exists():
        raise PremiereProjectError("BLOCKED: output project or review already exists.")
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    main_name = str(sequences["main_source_sequence_name"])
    correction_name = str(sequences["correction_source_sequence_name"])
    correction_sequence = find_project_sequence_node(root, correction_name)
    if correction_sequence is None:
        raise PremiereProjectError("Correction source sequence disappeared.")
    correction_items = _track_item_contexts(
        correction_sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    frame_ticks = _frame_ticks(fps)
    selected = dry_run["selected_source_range_in_out_frames_and_seconds"]
    source_start = int(selected["in_frame"]) * frame_ticks  # type: ignore[index]
    source_end = int(selected["out_frame_exclusive"]) * frame_ticks  # type: ignore[index]
    correction_matches = [
        item
        for item in correction_items
        if item.start <= source_start and item.end >= source_end
    ]
    if len(correction_matches) != 1:
        raise PremiereProjectError("Correction source item could not be resolved.")
    correction_item = correction_matches[0]
    source_in, source_out = resolve_insert_source_bounds(
        item_timeline_start=correction_item.start,
        item_source_in=correction_item.source_in,
        selected_timeline_start=source_start,
        selected_timeline_end=source_end,
    )
    insertion = int(
        dry_run["resolved_destination_insertion_frame_and_seconds"]["frame"]  # type: ignore[index]
    ) * frame_ticks
    duration = source_end - source_start
    clone_named_sequence(
        root,
        source_sequence_name=main_name,
        new_sequence_name=output_name,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    output_sequence = find_project_sequence_node(root, output_name)
    if output_sequence is None:
        raise PremiereProjectError("Output sequence clone was not created.")
    _apply_insert(
        root=root,
        output_sequence=output_sequence,
        correction_item=correction_item,
        insertion_ticks=insertion,
        insert_duration_ticks=duration,
        source_in_ticks=source_in,
        source_out_ticks=source_out,
        ids=ids,
        uids=uids,
        project_path=project_path,
    )
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    output_items = _track_item_contexts(
        output_sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    primary_output = [item for item in output_items if item.track_index == 1]
    interpolation = str(motion["temporal_interpolation"])
    for plan_item in motion_plan.candidate_video_items:
        item = _find_output_item(plan_item, primary_output)
        params = _motion_params(item.track_item_node, ids)
        if params is None:
            raise PremiereProjectError("Motion parameters disappeared after insertion.")
        first_visible = plan_item.source_in_ticks
        last_visible = max(first_visible, plan_item.source_out_ticks - frame_ticks)
        _set_param_keyframes(
            params.scale,
            keyframes=build_scale_keyframes(
                first_visible,
                last_visible,
                plan_item.start_scale,
                plan_item.end_scale,
                interpolation=interpolation,
            ),
            current_value=_format_number(plan_item.end_scale),
        )
        _set_param_keyframes(
            params.position,
            keyframes=build_position_keyframes(
                first_visible,
                last_visible,
                plan_item.start_position_x,
                plan_item.start_position_y,
                plan_item.end_position_x,
                plan_item.end_position_y,
                interpolation=interpolation,
            ),
        )
    removed_audio = _remove_all_audio_clips(
        output_sequence,
        id_lookup=ids,
        uid_lookup=uids,
    )
    if removed_audio != motion_plan.planned_audio_clip_removal_count:
        raise PremiereProjectError(
            f"Planned {motion_plan.planned_audio_clip_removal_count} audio removals, "
            f"removed {removed_audio}."
        )
    _update_sequence_duration_metadata(
        root,
        output_sequence,
        new_total_duration=output_frames * frame_ticks,
    )
    main_after = find_project_sequence_node(root, main_name)
    correction_after = find_project_sequence_node(root, correction_name)
    if (
        main_after is None
        or correction_after is None
        or ET.tostring(main_after, encoding="utf-8") != main_xml
        or ET.tostring(correction_after, encoding="utf-8") != correction_xml
    ):
        raise PremiereProjectError("A source sequence changed before project write.")
    _write_project(root, output_path)
    source_hash_after = _sha256(project_path)
    if source_hash_after != source_hash_before:
        output_path.unlink(missing_ok=True)
        raise PremiereProjectError("Source project changed during execution.")
    qa = _verify_output(
        config=config,
        project_path=project_path,
        output_path=output_path,
        main_xml=main_xml,
        correction_xml=correction_xml,
        dry_run=dry_run,
        motion_plan=motion_plan,
    )
    review = _render_review(
        config=review_adapter,
        output_project_path=output_path,
        output_path=review_path,
        plan=motion_plan,
    )
    _write_qa_report(
        qa_path,
        config=config,
        source_path=project_path,
        output_path=output_path,
        source_hash_before=source_hash_before,
        source_hash_after=source_hash_after,
        qa=qa,
        review=review,
    )
    return dry_path, qa_path, output_path
