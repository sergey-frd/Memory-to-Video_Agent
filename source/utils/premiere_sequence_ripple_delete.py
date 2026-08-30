from __future__ import annotations

import gzip
import json
import math
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from utils.premiere_keep_apply_export import _KeepSegment, _clone_track_item_with_bounds
from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    load_premiere_project_root,
)
from utils.premiere_project_export import (
    _ProjectObjectIdAllocator,
    _update_sequence_duration_metadata,
    clone_named_sequence,
)
from utils.premiere_sequence_delete_only import (
    _protected_sequence_state,
    build_ffprobe_payload,
)
from utils.premiere_sequence_motion import (
    _frame_ticks,
    _sequence_duration,
    _sha256,
    _track_item_contexts,
    _video_settings,
)
from utils.premiere_sequence_timeline_assembly import (
    _clear_target_tracks,
    _validate_all_refs,
    render_timeline_preview,
)
from utils.premiere_trim_review_export import (
    _ensure_track_items_container,
    _reindex_track_items,
)


INPUT_SEQUENCE = "SF_26_BD_LONG_FAMILY_NURI_v02"
OUTPUT_SEQUENCE = "SF_26_BD_LONG_FAMILY_NURI_v03"


def _require_dict(payload: object, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return payload


def validate_ripple_delete_plan(plan: dict[str, object]) -> None:
    if str(plan.get("task_id") or "") != "TASK_021":
        raise ValueError("Expected task_id=TASK_021.")
    timeline = _require_dict(plan.get("timeline"), "timeline")
    if (
        str(timeline.get("input_sequence") or "") != INPUT_SEQUENCE
        or str(timeline.get("output_sequence") or "") != OUTPUT_SEQUENCE
        or int(timeline.get("fps") or 0) != 25
        or int(timeline.get("input_frame_count_expected") or 0) != 3483
        or int(timeline.get("output_frame_count_expected") or 0) != 2876
    ):
        raise ValueError("TASK_021 fixed timeline contract changed.")
    policy = _require_dict(plan.get("edit_policy"), "edit_policy")
    if (
        str(policy.get("mode") or "") != "ripple_delete_only"
        or int(policy.get("insertions", -1)) != 0
        or bool(policy.get("reordering"))
        or bool(policy.get("fine_trimming"))
    ):
        raise ValueError("TASK_021 allows ripple deletes only.")
    deleted = plan.get("delete_ranges_on_original_v02")
    if not isinstance(deleted, list) or len(deleted) != 5:
        raise ValueError("TASK_021 requires exactly five delete ranges.")
    starts = [int(_require_dict(item, "delete range")["start_frame"]) for item in deleted]
    if starts != sorted(starts, reverse=True):
        raise ValueError("Delete ranges must be listed in descending start order.")
    total_deleted = 0
    ascending: list[tuple[int, int]] = []
    for raw in deleted:
        item = _require_dict(raw, "delete range")
        start = int(item["start_frame"])
        end = int(item["end_frame_exclusive"])
        duration = int(item["duration_frames"])
        if end - start != duration or end <= start:
            raise ValueError(f"Delete range {item.get('id')} is inconsistent.")
        total_deleted += duration
        ascending.append((start, end))
    ascending.sort()
    if total_deleted != 607 or any(
        ascending[index][1] > ascending[index + 1][0]
        for index in range(len(ascending) - 1)
    ):
        raise ValueError("Delete ranges must be disjoint and total 607 frames.")
    keep_map = plan.get("expected_keep_map")
    if not isinstance(keep_map, list) or len(keep_map) != 6:
        raise ValueError("expected_keep_map must contain six retained ranges.")
    cursor = 0
    retained = 0
    for raw in keep_map:
        item = _require_dict(raw, "keep map item")
        source_start = int(item["source_v02_start"])
        source_end = int(item["source_v02_end_exclusive"])
        output_start = int(item["output_v03_start"])
        output_end = int(item["output_v03_end_exclusive"])
        if output_start != cursor or source_end - source_start != output_end - output_start:
            raise ValueError("Keep map is not contiguous or duration-preserving.")
        cursor = output_end
        retained += source_end - source_start
    if cursor != 2876 or retained != 2876 or retained + total_deleted != 3483:
        raise ValueError("Keep/delete arithmetic differs from 3483 -> 2876.")


def build_expected_ripple_pieces(
    source_items: list[object],
    keep_map: list[dict[str, object]],
    *,
    fps: int,
) -> list[dict[str, object]]:
    frame_ticks = _frame_ticks(fps)
    result: list[dict[str, object]] = []
    for keep_index, raw_keep in enumerate(keep_map, start=1):
        keep = _require_dict(raw_keep, "keep map item")
        source_start = int(keep["source_v02_start"])
        source_end = int(keep["source_v02_end_exclusive"])
        output_start = int(keep["output_v03_start"])
        for input_index, item in enumerate(source_items, start=1):
            item_start = item.start // frame_ticks
            item_end = item.end // frame_ticks
            intersection_start = max(item_start, source_start)
            intersection_end = min(item_end, source_end)
            if intersection_end <= intersection_start:
                continue
            duration = intersection_end - intersection_start
            timeline_start = output_start + intersection_start - source_start
            source_in = (
                item.source_in // frame_ticks + intersection_start - item_start
            )
            result.append(
                {
                    "order": len(result) + 1,
                    "input_clip_index": input_index,
                    "keep_map_index": keep_index,
                    "source_sequence_name": item.name,
                    "source_v02_in_frame": intersection_start,
                    "source_v02_out_frame": intersection_end,
                    "source_in_frame": source_in,
                    "source_out_frame": source_in + duration,
                    "timeline_in_frame": timeline_start,
                    "timeline_out_frame": timeline_start + duration,
                    "duration_frames": duration,
                }
            )
    return result


def assemble_ripple_deleted_sequence(
    *,
    root: ET.Element,
    project_path: Path,
    keep_map: list[dict[str, object]],
    protected_xml: dict[str, bytes],
) -> list[dict[str, object]]:
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    source = find_project_sequence_node(root, INPUT_SEQUENCE)
    if source is None:
        raise PremiereProjectError("TASK_021 input sequence disappeared.")
    source_items = _track_item_contexts(
        source,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    expected = build_expected_ripple_pieces(source_items, keep_map, fps=25)
    clone_named_sequence(
        root,
        source_sequence_name=INPUT_SEQUENCE,
        new_sequence_name=OUTPUT_SEQUENCE,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    target = find_project_sequence_node(root, OUTPUT_SEQUENCE)
    if target is None:
        raise PremiereProjectError("TASK_021 output clone was not created.")
    target_items = _track_item_contexts(
        target,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    if len(target_items) != len(source_items):
        raise PremiereProjectError("TASK_021 clone changed input clip count.")
    templates = {
        index: item.track_item_node for index, item in enumerate(target_items, start=1)
    }
    _clear_target_tracks(target, ids=ids, uids=uids)
    tracks = dict(
        get_project_track_nodes(
            target,
            track_group_index=0,
            object_id_lookup=ids,
            object_uid_lookup=uids,
        )
    )
    track = tracks.get(0)
    if track is None:
        raise PremiereProjectError("TASK_021 output has no V1.")
    container = _ensure_track_items_container(track)
    if container is None:
        raise PremiereProjectError("TASK_021 output V1 has no item container.")
    allocator = _ProjectObjectIdAllocator(root)
    frame_ticks = _frame_ticks(25)
    for piece in expected:
        _, ref = _clone_track_item_with_bounds(
            root,
            template_track_item=templates[int(piece["input_clip_index"])],
            segment=_KeepSegment(
                timeline_start=int(piece["timeline_in_frame"]) * frame_ticks,
                timeline_end=int(piece["timeline_out_frame"]) * frame_ticks,
                source_in=int(piece["source_in_frame"]) * frame_ticks,
                source_out=int(piece["source_out_frame"]) * frame_ticks,
            ),
            object_id_lookup=ids,
            id_allocator=allocator,
        )
        container.append(ref)
    _reindex_track_items(container)
    _update_sequence_duration_metadata(
        root,
        target,
        new_total_duration=2876 * frame_ticks,
    )
    for name, before_xml in protected_xml.items():
        sequence = find_project_sequence_node(root, name)
        if sequence is None or ET.tostring(sequence, encoding="utf-8") != before_xml:
            raise PremiereProjectError(
                f"TASK_021 protected sequence {name!r} changed during assembly."
            )
    return expected


def extract_saved_output(
    project_path: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    target = find_project_sequence_node(root, OUTPUT_SEQUENCE)
    if target is None:
        raise PremiereProjectError("Saved TASK_021 output sequence is missing.")
    video = _track_item_contexts(
        target,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    audio = _track_item_contexts(
        target,
        group_index=1,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    frame_ticks = _frame_ticks(25)
    actual = [
        {
            "order": index,
            "source_sequence_name": item.name,
            "source_in_frame": item.source_in // frame_ticks,
            "source_out_frame": item.source_out // frame_ticks,
            "timeline_in_frame": item.start // frame_ticks,
            "timeline_out_frame": item.end // frame_ticks,
            "duration_frames": item.duration // frame_ticks,
            "audio_inserted": False,
        }
        for index, item in enumerate(video, start=1)
    ]
    metadata = {
        "sequence_name": OUTPUT_SEQUENCE,
        "settings": _video_settings(target, ids),
        "video_clip_count": len(video),
        "audio_clip_count": len(audio),
        "duration_frames": _sequence_duration(video) // frame_ticks,
        "duration_seconds": _sequence_duration(video) / PREMIERE_TICKS_PER_SECOND,
    }
    return actual, metadata


def verify_saved_ripple_delete(
    *,
    project_path: Path,
    expected: list[dict[str, object]],
    protected_xml: dict[str, bytes],
    protected_properties: dict[str, object],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    actual, metadata = extract_saved_output(project_path)
    expected_tuples = [
        (
            str(item["source_sequence_name"]),
            int(item["source_in_frame"]),
            int(item["source_out_frame"]),
            int(item["timeline_in_frame"]),
            int(item["timeline_out_frame"]),
        )
        for item in expected
    ]
    actual_tuples = [
        (
            str(item["source_sequence_name"]),
            int(item["source_in_frame"]),
            int(item["source_out_frame"]),
            int(item["timeline_in_frame"]),
            int(item["timeline_out_frame"]),
        )
        for item in actual
    ]
    if actual_tuples != expected_tuples:
        raise PremiereProjectError(
            "TASK_021 saved-project output differs from modeled ripple-delete pieces."
        )
    if (
        int(metadata["duration_frames"]) != 2876
        or int(metadata["audio_clip_count"]) != 0
        or metadata["settings"]["frame_rate"] != str(_frame_ticks(25))  # type: ignore[index]
        or metadata["settings"]["frame_rect"] != "0,0,3840,2160"  # type: ignore[index]
    ):
        raise PremiereProjectError(
            "TASK_021 saved-project duration/settings/audio hard-fail triggered."
        )
    enriched: list[dict[str, object]] = []
    for modeled, saved in zip(expected, actual, strict=True):
        enriched.append(
            {
                **saved,
                "input_clip_index": modeled["input_clip_index"],
                "keep_map_index": modeled["keep_map_index"],
                "source_v02_in_frame": modeled["source_v02_in_frame"],
                "source_v02_out_frame": modeled["source_v02_out_frame"],
                "deviation_from_plan_frames": 0,
            }
        )
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    from utils.premiere_sequence_timeline_assembly import _sequence_property_snapshot

    after_properties: dict[str, object] = {}
    for name, before_xml in protected_xml.items():
        sequence = find_project_sequence_node(root, name)
        if sequence is None or ET.tostring(sequence, encoding="utf-8") != before_xml:
            raise PremiereProjectError(
                f"TASK_021 protected sequence {name!r} changed."
            )
        after_properties[name] = _sequence_property_snapshot(
            sequence,
            ids=ids,
            uids=uids,
            project_path=project_path,
            fps=25,
        )
    if after_properties != protected_properties:
        raise PremiereProjectError("TASK_021 protected sequence properties changed.")
    _validate_all_refs(root)
    nuri_final = [
        item
        for item in enriched
        if int(item["source_v02_in_frame"]) >= 3171
    ]
    if (
        not nuri_final
        or int(nuri_final[0]["source_v02_in_frame"]) != 3171
        or int(nuri_final[-1]["source_v02_out_frame"]) != 3483
        or sum(int(item["duration_frames"]) for item in nuri_final) != 312
    ):
        raise PremiereProjectError("TASK_021 did not preserve v02 frames 3171-3483.")
    qa = {
        **metadata,
        "actual_clip_count": len(enriched),
        "actual_order_and_bounds_match_model": True,
        "total_removed_frames": 607,
        "protected_sequences_unchanged": True,
        "nuri_and_final_v02_3171_3483_preserved": True,
        "saved_project_reopened_and_reparsed": True,
        "object_references_resolved": True,
    }
    return qa, enriched


def _preview_adapter(actual: list[dict[str, object]]) -> dict[str, object]:
    return {
        "timebase_fps": 25,
        "target_sequence": {
            "name": OUTPUT_SEQUENCE,
            "settings_source_sequence": INPUT_SEQUENCE,
        },
        "expected_result": {
            "total_duration_frames": 2876,
            "preview_width": 640,
            "preview_height": 360,
        },
        "segment_defaults": {},
        "_segments": actual,
    }


def _read_frame(path: Path, frame_number: int) -> object:
    import cv2

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open TASK_021 preview: {path}")
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError(f"Could not decode TASK_021 frame {frame_number}.")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def build_join_contact_sheet(
    *,
    preview_path: Path,
    delete_ranges: list[dict[str, object]],
    keep_map: list[dict[str, object]],
    output_path: Path,
) -> dict[str, object]:
    from PIL import Image, ImageDraw

    boundaries = [int(item["output_v03_start"]) for item in keep_map[1:]]
    samples: list[tuple[int, str]] = []
    black_frames: list[int] = []
    for deletion, boundary in zip(
        sorted(delete_ranges, key=lambda item: int(item["start_frame"])),
        boundaries,
        strict=True,
    ):
        deletion_id = str(deletion["id"])
        samples.append(
            (
                boundary - 1,
                f"{deletion_id} LEFT out {boundary - 1} / v02 {int(deletion['start_frame']) - 1}",
            )
        )
        samples.append(
            (
                boundary,
                f"{deletion_id} RIGHT out {boundary} / v02 {int(deletion['end_frame_exclusive'])}",
            )
        )
    images: list[tuple[object, str]] = []
    for frame_number, label in samples:
        frame = _read_frame(preview_path, frame_number)
        if float(frame.mean()) < 2.0:
            black_frames.append(frame_number)
        images.append((frame, label))
    if black_frames:
        raise RuntimeError(f"TASK_021 black join frames: {black_frames}")
    thumb_w, thumb_h, label_h = 384, 216, 38
    columns = 2
    rows = math.ceil(len(images) / columns)
    sheet = Image.new("RGB", (columns * thumb_w, rows * (thumb_h + label_h)), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (array, label) in enumerate(images):
        image = Image.fromarray(array)
        image.thumbnail((thumb_w, thumb_h))
        x = (index % columns) * thumb_w
        y = (index // columns) * (thumb_h + label_h)
        sheet.paste(image, (x, y))
        draw.text((x + 4, y + thumb_h + 4), label, fill="black")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=92)
    return {
        "path": str(output_path),
        "delete_count": 5,
        "join_side_frames_sampled": 10,
        "black_join_frames": [],
        "status": "PASS",
    }


def build_ripple_delete_proof(
    *,
    plan: dict[str, object],
    actual: list[dict[str, object]],
    preview_frames: int,
) -> dict[str, object]:
    deleted = [
        {
            **_require_dict(item, "delete range"),
            "actual_retained_overlap_frames": sum(
                max(
                    0,
                    min(
                        int(piece["source_v02_out_frame"]),
                        int(item["end_frame_exclusive"]),
                    )
                    - max(
                        int(piece["source_v02_in_frame"]),
                        int(item["start_frame"]),
                    ),
                )
                for piece in actual
            ),
        }
        for item in plan["delete_ranges_on_original_v02"]  # type: ignore[index]
    ]
    keep_map = [
        {
            **_require_dict(item, "keep map item"),
            "actual_piece_count": sum(
                int(piece["keep_map_index"]) == index
                for piece in actual
            ),
        }
        for index, item in enumerate(plan["expected_keep_map"], start=1)  # type: ignore[arg-type]
    ]
    return {
        "task_id": "TASK_021",
        "proof_source": "reopened_saved_prproj",
        "actual_output_clips": actual,
        "delete_ranges": deleted,
        "keep_map": keep_map,
        "total_removed_frames": sum(int(item["duration_frames"]) for item in deleted),
        "total_retained_frames": sum(int(item["duration_frames"]) for item in actual),
        "no_retained_overlap_with_deleted_ranges": all(
            int(item["actual_retained_overlap_frames"]) == 0 for item in deleted
        ),
        "original_order_preserved": all(
            int(actual[index]["source_v02_in_frame"])
            <= int(actual[index + 1]["source_v02_in_frame"])
            for index in range(len(actual) - 1)
        ),
        "insertions": 0,
        "reordering": False,
        "fine_trimming": False,
        "nuri_and_final_preserved": True,
        "project_frames": 2876,
        "preview_frames": preview_frames,
        "preview_and_project_frames_match": preview_frames == 2876,
        "status": "PASS",
    }


def execute_ripple_delete_task(
    plan_path: Path,
    *,
    dry_run_only: bool = False,
) -> dict[str, Path]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("TASK_021 plan must be a JSON object.")
    validate_ripple_delete_plan(plan)
    project_path = Path(str(_require_dict(plan["project"], "project")["path"]))
    if not project_path.is_file():
        raise PremiereProjectError(f"TASK_021 project not found: {project_path}")
    output_dir = project_path.parent / "TASK_021_OUTPUT"
    output_dir.mkdir(parents=True, exist_ok=True)
    root = load_premiere_project_root(project_path)
    if find_project_sequence_node(root, OUTPUT_SEQUENCE) is not None:
        raise PremiereProjectError(
            f"BLOCKED: output sequence {OUTPUT_SEQUENCE!r} already exists."
        )
    input_sequence = find_project_sequence_node(root, INPUT_SEQUENCE)
    if input_sequence is None:
        raise PremiereProjectError("BLOCKED: TASK_021 input v02 is missing.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    input_video = _track_item_contexts(
        input_sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    input_audio = _track_item_contexts(
        input_sequence,
        group_index=1,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    if (
        _sequence_duration(input_video) // _frame_ticks(25) != 3483
        or input_audio
        or _video_settings(input_sequence, ids)["frame_rate"] != str(_frame_ticks(25))
    ):
        raise PremiereProjectError(
            "BLOCKED: v02 is not exactly 3483 frames / 25 fps / video-only."
        )
    protected_names = [str(value) for value in plan["protected_sequences"]]  # type: ignore[index]
    protected_xml, protected_properties = _protected_sequence_state(
        root,
        names=protected_names,
        project_path=project_path,
        fps=25,
    )
    keep_map = [
        _require_dict(item, "keep map item")
        for item in plan["expected_keep_map"]  # type: ignore[index]
    ]
    expected = build_expected_ripple_pieces(input_video, keep_map, fps=25)
    source_hash_before = _sha256(project_path)
    dry_path = output_dir / "TASK_021_DRY_RUN.json"
    dry_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_021",
                "project_path": str(project_path),
                "project_sha256": source_hash_before,
                "input_sequence": INPUT_SEQUENCE,
                "input_frames": 3483,
                "output_sequence": OUTPUT_SEQUENCE,
                "planned_delete_ranges": plan["delete_ranges_on_original_v02"],
                "planned_keep_map": keep_map,
                "modeled_output_clips": expected,
                "modeled_output_clip_count": len(expected),
                "planned_removed_frames": 607,
                "planned_output_frames": 2876,
                "insertions": 0,
                "reordering": False,
                "fine_trimming": False,
                "blocked_items": [],
                "status": "PASS_READY_TO_EXECUTE",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if dry_run_only:
        return {"dry_run": dry_path}
    assembled_expected = assemble_ripple_deleted_sequence(
        root=root,
        project_path=project_path,
        keep_map=keep_map,
        protected_xml=protected_xml,
    )
    if assembled_expected != expected:
        raise PremiereProjectError("TASK_021 assembly model changed unexpectedly.")
    _validate_all_refs(root)
    temp_path = output_dir / "SF_26_BD_1_TASK021_VALIDATION.prproj"
    temp_path.write_bytes(
        gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    )
    verify_saved_ripple_delete(
        project_path=temp_path,
        expected=expected,
        protected_xml=protected_xml,
        protected_properties=protected_properties,
    )
    backup_path = project_path.with_name(
        f"{project_path.stem}_before_TASK_021{project_path.suffix}"
    )
    if backup_path.exists():
        raise PremiereProjectError(f"BLOCKED: backup already exists: {backup_path}")
    shutil.copy2(project_path, backup_path)
    if _sha256(backup_path) != source_hash_before:
        raise PremiereProjectError("TASK_021 backup SHA256 mismatch.")
    os.replace(temp_path, project_path)
    qa_project, actual = verify_saved_ripple_delete(
        project_path=project_path,
        expected=expected,
        protected_xml=protected_xml,
        protected_properties=protected_properties,
    )
    preview_path = output_dir / "SF_26_BD_LONG_FAMILY_NURI_v03_640_360.mp4"
    preview = render_timeline_preview(
        _preview_adapter(actual),
        project_path=project_path,
        segments=actual,
        output_path=preview_path,
    )
    if int(preview["frames"]) != 2876 or bool(preview["has_audio_stream"]):
        raise RuntimeError("TASK_021 preview hard-fail condition triggered.")
    contact_path = output_dir / "TASK_021_V03_NEW_JOINS_CONTACT_SHEET.jpg"
    contact = build_join_contact_sheet(
        preview_path=preview_path,
        delete_ranges=[
            _require_dict(item, "delete range")
            for item in plan["delete_ranges_on_original_v02"]  # type: ignore[index]
        ],
        keep_map=keep_map,
        output_path=contact_path,
    )
    proof = build_ripple_delete_proof(
        plan=plan,
        actual=actual,
        preview_frames=int(preview["frames"]),
    )
    proof_path = output_dir / "TASK_021_RIPPLE_DELETE_PROOF.json"
    proof_path.write_text(
        json.dumps(proof, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    probe = build_ffprobe_payload(preview_path)
    probe_path = output_dir / "TASK_021_FFPROBE.json"
    probe_path.write_text(
        json.dumps(probe, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    actual_path = output_dir / "TASK_021_ACTUAL.json"
    actual_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_021",
                "source": "reopened_saved_prproj",
                "project_path": str(project_path),
                "project_sha256": _sha256(project_path),
                "backup_path": str(backup_path),
                "backup_sha256": _sha256(backup_path),
                "source_project_sha256_before": source_hash_before,
                "input_sequence": INPUT_SEQUENCE,
                "output": qa_project,
                "clips": actual,
                "preview": preview,
                "contact_sheet": contact,
                "status": "STRUCTURAL_PASS_PREMIERE_OPEN_CHECK_REQUIRED",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    qa_path = output_dir / "TASK_021_QA.txt"
    qa_path.write_text(
        "\n".join(
            [
                "TASK_021 — PREMIERE V03 DELETE DUPLICATES",
                "",
                "STATUS: STRUCTURAL_PASS_PREMIERE_OPEN_CHECK_REQUIRED",
                f"Project: {project_path}",
                f"Backup: {backup_path}",
                f"Input: {INPUT_SEQUENCE} — 3483 frames",
                f"Output: {OUTPUT_SEQUENCE} — 2876 frames / 115.04 seconds",
                f"Actual output clips after five ripple deletes: {len(actual)}",
                "Total removed: 607 frames — PASS",
                "Five half-open deletes and retained mapping: PASS",
                "Insertions/reordering/fine trimming: 0 / false / false — PASS",
                "Nuri and finale v02 frames 3171-3483 preserved: PASS",
                "Audio clips / preview audio streams: 0 / 0 — PASS",
                "Protected sequences unchanged: PASS",
                "Saved project reopened and reparsed: PASS",
                "Preview: 640x360 / 25 fps / 2876 frames — PASS",
                "Five new joins, both sides, no black frames: PASS",
                "Premiere repair/conversion desktop open-check: REQUIRED",
                "",
                "No TASK_021_DONE.txt until desktop open-check is confirmed.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "dry_run": dry_path,
        "project": project_path,
        "backup": backup_path,
        "preview": preview_path,
        "actual": actual_path,
        "proof": proof_path,
        "contact_sheet": contact_path,
        "ffprobe": probe_path,
        "qa": qa_path,
        "done": output_dir / "TASK_021_DONE.txt",
    }
