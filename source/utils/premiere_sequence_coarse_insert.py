from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    load_premiere_project_root,
)
from utils.premiere_sequence_delete_only import (
    _protected_sequence_state,
    build_ffprobe_payload,
    extract_actual_from_saved_project,
)
from utils.premiere_sequence_motion import _frame_ticks, _sha256
from utils.premiere_sequence_timeline_assembly import (
    _validate_all_refs,
    assemble_target_sequence,
    render_timeline_preview,
)


STAGE = "B_COARSE_FAMILY_NURI_INSERTION"
TARGET_NAME = "SF_26_BD_LONG_FAMILY_NURI_STAGE_B_v01"
KEEP_SOURCE = "SF_26_BD_Keep_08"
FAMILY_SOURCE = "SF_26_BD_Family_1"
NURI_SOURCE = "SF_26_BD_Nuri_1"


def _require_dict(payload: object, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return payload


def build_coarse_insert_segments(
    plan: dict[str, object],
) -> list[dict[str, object]]:
    definitions = _require_dict(plan.get("segments"), "segments")
    order = plan.get("timeline_order")
    if not isinstance(order, list):
        raise ValueError("timeline_order must be a list.")
    cursor = 0
    result: list[dict[str, object]] = []
    for index, raw_id in enumerate(order, start=1):
        segment_id = str(raw_id)
        source = _require_dict(definitions.get(segment_id), f"segment {segment_id}")
        source_in = int(source["source_in_frame"])
        source_out = int(source["source_out_frame"])
        duration = int(source["duration_frames"])
        if source_out - source_in != duration:
            raise ValueError(f"Segment {segment_id} has inconsistent duration.")
        result.append(
            {
                "order": index,
                "segment_id": segment_id,
                "source_sequence_name": str(source["source_sequence"]),
                "source_in_frame": source_in,
                "source_out_frame": source_out,
                "timeline_in_frame": cursor,
                "timeline_out_frame": cursor + duration,
                "duration_frames": duration,
            }
        )
        cursor += duration
    return result


def validate_coarse_insert_plan(
    plan: dict[str, object],
) -> list[dict[str, object]]:
    if (
        str(plan.get("task_id") or "") != "TASK_020"
        or str(plan.get("stage") or "") != STAGE
        or not bool(plan.get("authorized_by_user"))
    ):
        raise ValueError("TASK_020 Stage B is not explicitly authorized.")
    target = _require_dict(plan.get("target_sequence"), "target_sequence")
    if (
        str(target.get("name") or "") != TARGET_NAME
        or int(target.get("fps") or 0) != 25
        or int(target.get("audio_clip_count", -1)) != 0
    ):
        raise ValueError("Stage B target contract was changed.")
    expected = _require_dict(plan.get("expected"), "expected")
    fixed = {
        "total_video_clips": 28,
        "keep_clip_count": 4,
        "family_clip_count": 23,
        "nuri_clip_count": 1,
        "keep_frames": 3827,
        "family_frames": 852,
        "nuri_frames": 75,
        "total_frames": 4754,
        "audio_clip_count": 0,
        "audio_stream_count": 0,
    }
    if any(int(expected.get(key, -1)) != value for key, value in fixed.items()):
        raise ValueError("Stage B fixed expected counts/durations were changed.")
    segments = build_coarse_insert_segments(plan)
    if len(segments) != 28 or sum(
        int(item["duration_frames"]) for item in segments
    ) != 4754:
        raise ValueError("Stage B must contain 28 clips totaling 4754 frames.")
    names = [str(item["source_sequence_name"]) for item in segments]
    if Counter(names) != Counter(
        {KEEP_SOURCE: 4, FAMILY_SOURCE: 23, NURI_SOURCE: 1}
    ):
        raise ValueError("Stage B source contribution counts differ.")
    groups = _require_dict(plan.get("groups"), "groups")
    family_order = [
        str(item["segment_id"])
        for item in segments
        if item["source_sequence_name"] == FAMILY_SOURCE
    ]
    expected_family_order = [
        str(segment_id)
        for group_name in ("G1", "G2", "G3", "G4")
        for segment_id in groups[group_name]
    ]
    if family_order != expected_family_order:
        raise ValueError("Family segment order/group membership differs.")
    ids = [str(item["segment_id"]) for item in segments]
    if ids[-2:] != ["N1", "K4"]:
        raise ValueError("N1 must occur exactly once immediately before K4.")
    if len(ids) != len(set(ids)):
        raise ValueError("Stage B segment ids must each occur exactly once.")
    return segments


def _adapter_plan(
    plan: dict[str, object],
    segments: list[dict[str, object]],
) -> dict[str, object]:
    target = _require_dict(plan["target_sequence"], "target_sequence")
    expected = _require_dict(plan["expected"], "expected")
    return {
        "task_id": "TASK_020_STAGE_B",
        "timebase_fps": int(target["fps"]),
        "target_sequence": {
            "name": target["name"],
            "settings_source_sequence": KEEP_SOURCE,
        },
        "expected_result": {
            "total_duration_frames": expected["total_frames"],
            "preview_width": 640,
            "preview_height": 360,
        },
        "segment_defaults": {
            "source_kind": "premiere_sequence",
            "video_track": "V1",
            "edit_mode": "video_only_nested_sequence_clip",
            "speed_percent": 100,
            "video_transition_in": "none",
            "video_transition_out": "none",
            "audio_track": None,
            "audio_mode": "ignore_all_audio",
            "audio_inserted": False,
        },
        "_segments": segments,
    }


def verify_saved_coarse_insert_project(
    *,
    plan: dict[str, object],
    project_path: Path,
    protected_xml: dict[str, bytes],
    protected_properties: dict[str, object],
    expected_segments: list[dict[str, object]],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    actual_raw, metadata = extract_actual_from_saved_project(
        project_path=project_path,
        target_name=TARGET_NAME,
        fps=25,
    )
    expected_tuples = [
        (
            str(item["source_sequence_name"]),
            int(item["source_in_frame"]),
            int(item["source_out_frame"]),
            int(item["timeline_in_frame"]),
            int(item["timeline_out_frame"]),
        )
        for item in expected_segments
    ]
    actual_tuples = [
        (
            str(item["source_sequence_name"]),
            int(item["source_in_frame"]),
            int(item["source_out_frame"]),
            int(item["timeline_in_frame"]),
            int(item["timeline_out_frame"]),
        )
        for item in actual_raw
    ]
    if actual_tuples != expected_tuples:
        raise PremiereProjectError(
            "Saved-project QA: actual 28-clip order or source bounds differ from plan."
        )
    if (
        metadata["video_clip_count"] != 28
        or metadata["audio_clip_count"] != 0
        or metadata["duration_frames"] != 4754
    ):
        raise PremiereProjectError(
            "Saved-project QA: count, duration, or audio hard-fail triggered."
        )
    actual: list[dict[str, object]] = []
    for expected, raw in zip(expected_segments, actual_raw, strict=True):
        actual.append(
            {
                **raw,
                "segment_id": expected["segment_id"],
                "deviation_from_plan_frames": 0,
            }
        )
    counts = Counter(str(item["segment_id"]) for item in actual)
    if any(counts[str(item["segment_id"])] != 1 for item in expected_segments):
        raise PremiereProjectError(
            "Saved-project QA: a selected segment is missing or duplicated."
        )
    if [item["segment_id"] for item in actual[-2:]] != ["N1", "K4"]:
        raise PremiereProjectError("Saved-project QA: N1 is not immediately before K4.")
    frame_totals = {
        name: sum(
            int(item["duration_frames"])
            for item in actual
            if item["source_sequence_name"] == name
        )
        for name in (KEEP_SOURCE, FAMILY_SOURCE, NURI_SOURCE)
    }
    if frame_totals != {KEEP_SOURCE: 3827, FAMILY_SOURCE: 852, NURI_SOURCE: 75}:
        raise PremiereProjectError(
            f"Saved-project QA: source duration totals differ: {frame_totals!r}"
        )
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    after_properties: dict[str, object] = {}
    from utils.premiere_sequence_timeline_assembly import _sequence_property_snapshot

    for name, before_xml in protected_xml.items():
        sequence = find_project_sequence_node(root, name)
        if sequence is None or ET.tostring(sequence, encoding="utf-8") != before_xml:
            raise PremiereProjectError(
                f"Saved-project QA: protected sequence {name!r} changed."
            )
        after_properties[name] = _sequence_property_snapshot(
            sequence,
            ids=ids,
            uids=uids,
            project_path=project_path,
            fps=25,
        )
    if after_properties != protected_properties:
        raise PremiereProjectError(
            "Saved-project QA: protected sequence properties changed."
        )
    _validate_all_refs(root)
    qa = {
        **metadata,
        "actual_order_and_bounds_match_exactly": True,
        "segment_occurrence_counts": dict(sorted(counts.items())),
        "source_duration_frames": frame_totals,
        "n1_immediately_before_k4": True,
        "protected_sequences_unchanged": True,
        "saved_project_reopened_and_reparsed": True,
        "object_references_resolved": True,
    }
    return qa, actual


def build_insertion_proof(
    *,
    plan: dict[str, object],
    actual: list[dict[str, object]],
    project_frames: int,
    preview_frames: int,
) -> dict[str, object]:
    expected_ids = [str(value) for value in plan["timeline_order"]]  # type: ignore[index]
    actual_ids = [str(item["segment_id"]) for item in actual]
    actual_counts = Counter(actual_ids)
    duration_by_source = {
        source: sum(
            int(item["duration_frames"])
            for item in actual
            if item["source_sequence_name"] == source
        )
        for source in (KEEP_SOURCE, FAMILY_SOURCE, NURI_SOURCE)
    }
    occurrence_proof = {
        segment_id: {
            "expected": 1,
            "actual": actual_counts[segment_id],
            "status": "PASS" if actual_counts[segment_id] == 1 else "FAIL",
        }
        for segment_id in expected_ids
    }
    return {
        "task_id": "TASK_020",
        "stage": STAGE,
        "proof_source": "reopened_saved_prproj",
        "ordered_actual_clips": actual,
        "expected_timeline_order": expected_ids,
        "actual_timeline_order": actual_ids,
        "order_matches_exactly": actual_ids == expected_ids,
        "segment_occurrence_proof": occurrence_proof,
        "duration_frames_by_source_sequence": duration_by_source,
        "n1_index": actual_ids.index("N1"),
        "k4_index": actual_ids.index("K4"),
        "n1_immediately_before_k4": actual_ids.index("N1") + 1
        == actual_ids.index("K4"),
        "project_frames": project_frames,
        "preview_frames": preview_frames,
        "preview_and_project_frame_counts_match": project_frames == preview_frames,
        "known_rough_cut_remainders": plan.get("known_rough_cut_remainders"),
        "stage_b_is_rough_not_final": True,
        "status": "PASS",
    }


def _read_frame(path: Path, frame_number: int) -> object:
    import cv2

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video for contact sheet: {path}")
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError(f"Could not read timeline frame {frame_number}.")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def build_stage_b_contact_sheet(
    *,
    preview_path: Path,
    actual: list[dict[str, object]],
    groups: dict[str, object],
    output_path: Path,
) -> dict[str, object]:
    from PIL import Image, ImageDraw

    by_id = {str(item["segment_id"]): item for item in actual}
    samples: list[tuple[int, str]] = []

    def add(frame: int, label: str) -> None:
        key = (frame, label)
        if key not in samples:
            samples.append(key)

    for group_name in ("G1", "G2", "G3", "G4"):
        members = [str(value) for value in groups[group_name]]  # type: ignore[index]
        first = by_id[members[0]]
        last = by_id[members[-1]]
        add(int(first["timeline_in_frame"]), f"{group_name} START {members[0]}")
        add(int(last["timeline_out_frame"]) - 1, f"{group_name} END {members[-1]}")
    actual_ids = [str(item["segment_id"]) for item in actual]
    for index in range(len(actual) - 1):
        left = actual[index]
        right = actual[index + 1]
        left_id = actual_ids[index]
        right_id = actual_ids[index + 1]
        keep_family_join = (left_id.startswith("K") and right_id[0] in "FS") or (
            left_id[0] in "FS" and right_id.startswith("K")
        )
        nuri_k4_join = left_id == "N1" and right_id == "K4"
        if keep_family_join or nuri_k4_join:
            add(
                int(left["timeline_out_frame"]) - 1,
                f"JOIN LEFT {left_id}->{right_id}",
            )
            add(int(right["timeline_in_frame"]), f"JOIN RIGHT {left_id}->{right_id}")
    thumb_w, thumb_h, label_h = 320, 180, 34
    columns = 4
    rows = math.ceil(len(samples) / columns)
    sheet = Image.new("RGB", (columns * thumb_w, rows * (thumb_h + label_h)), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (frame_number, label) in enumerate(samples):
        image = Image.fromarray(_read_frame(preview_path, frame_number))
        image.thumbnail((thumb_w, thumb_h))
        x = (index % columns) * thumb_w
        y = (index // columns) * (thumb_h + label_h)
        sheet.paste(image, (x, y))
        draw.text((x + 4, y + thumb_h + 3), f"{label} | out {frame_number}", fill="black")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=92)
    return {
        "path": str(output_path),
        "sample_count": len(samples),
        "family_groups_shown": ["G1", "G2", "G3", "G4"],
        "keep_family_join_sides_shown": True,
        "n1_to_k4_join_shown": True,
        "status": "PASS",
    }


def execute_coarse_insert_stage(
    plan_path: Path,
    *,
    dry_run_only: bool = False,
) -> dict[str, Path]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("Stage B plan must be a JSON object.")
    segments = validate_coarse_insert_plan(plan)
    project_path = Path(str(plan["project_path"]))
    if not project_path.is_file():
        raise PremiereProjectError(f"Project not found: {project_path}")
    output_dir = project_path.parent / "TASK_020_STAGE_B_OUTPUT"
    output_dir.mkdir(parents=True, exist_ok=True)
    root = load_premiere_project_root(project_path)
    if find_project_sequence_node(root, TARGET_NAME) is not None:
        raise PremiereProjectError(
            f"BLOCKED: target sequence {TARGET_NAME!r} already exists."
        )
    protected_names = [str(value) for value in plan["protected_sequences"]]  # type: ignore[index]
    protected_xml, protected_properties = _protected_sequence_state(
        root,
        names=protected_names,
        project_path=project_path,
        fps=25,
    )
    source_hash_before = _sha256(project_path)
    dry_path = output_dir / "TASK_020_STAGE_B_DRY_RUN.json"
    dry_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_020",
                "stage": STAGE,
                "authorized_by_user": True,
                "project_path": str(project_path),
                "project_sha256": source_hash_before,
                "target_sequence": TARGET_NAME,
                "planned_segments": segments,
                "planned_clip_count": 28,
                "planned_duration_frames": 4754,
                "planned_source_duration_frames": {
                    KEEP_SOURCE: 3827,
                    FAMILY_SOURCE: 852,
                    NURI_SOURCE: 75,
                },
                "planned_audio_clips": 0,
                "fine_cleanup_executed": False,
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
    adapter = _adapter_plan(plan, segments)
    assemble_target_sequence(
        adapter,
        root=root,
        segments=segments,
        source_xml=protected_xml,
        project_path=project_path,
    )
    _validate_all_refs(root)
    temp_path = output_dir / "SF_26_BD_1_TASK020_STAGE_B_VALIDATION.prproj"
    temp_path.write_bytes(
        gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    )
    verify_saved_coarse_insert_project(
        plan=plan,
        project_path=temp_path,
        protected_xml=protected_xml,
        protected_properties=protected_properties,
        expected_segments=segments,
    )
    backup_path = project_path.with_name(
        f"{project_path.stem}_before_TASK_020_STAGE_B{project_path.suffix}"
    )
    if backup_path.exists():
        raise PremiereProjectError(f"BLOCKED: backup already exists: {backup_path}")
    shutil.copy2(project_path, backup_path)
    if _sha256(backup_path) != source_hash_before:
        raise PremiereProjectError("Stage B backup SHA256 mismatch.")
    os.replace(temp_path, project_path)
    qa_project, actual = verify_saved_coarse_insert_project(
        plan=plan,
        project_path=project_path,
        protected_xml=protected_xml,
        protected_properties=protected_properties,
        expected_segments=segments,
    )
    preview_path = output_dir / "TASK_020_STAGE_B_COARSE_FAMILY_NURI_640_360.mp4"
    preview = render_timeline_preview(
        adapter,
        project_path=project_path,
        segments=segments,
        output_path=preview_path,
    )
    if int(preview["frames"]) != 4754 or bool(preview["has_audio_stream"]):
        raise RuntimeError("Stage B preview hard-fail condition triggered.")
    proof = build_insertion_proof(
        plan=plan,
        actual=actual,
        project_frames=int(qa_project["duration_frames"]),
        preview_frames=int(preview["frames"]),
    )
    proof_path = output_dir / "TASK_020_STAGE_B_INSERTION_PROOF.json"
    proof_path.write_text(
        json.dumps(proof, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    groups = _require_dict(plan["groups"], "groups")
    contact_path = output_dir / "TASK_020_STAGE_B_JOIN_CONTACT_SHEET.jpg"
    contact = build_stage_b_contact_sheet(
        preview_path=preview_path,
        actual=actual,
        groups=groups,
        output_path=contact_path,
    )
    probe = build_ffprobe_payload(preview_path)
    probe_path = output_dir / "TASK_020_STAGE_B_FFPROBE.json"
    probe_path.write_text(
        json.dumps(probe, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    actual_path = output_dir / "TASK_020_STAGE_B_ACTUAL.json"
    actual_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_020",
                "stage": STAGE,
                "source": "reopened_saved_prproj",
                "project_path": str(project_path),
                "project_sha256": _sha256(project_path),
                "backup_path": str(backup_path),
                "backup_sha256": _sha256(backup_path),
                "source_project_sha256_before": source_hash_before,
                "target": qa_project,
                "clips": actual,
                "preview": preview,
                "contact_sheet": contact,
                "known_rough_cut_remainders": plan["known_rough_cut_remainders"],
                "fine_cleanup_executed": False,
                "status": "STRUCTURAL_PASS_WAITING_MUZA_VISUAL_QA",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    qa_path = output_dir / "TASK_020_STAGE_B_QA.txt"
    qa_path.write_text(
        "\n".join(
            [
                "TASK_020 — STAGE B COARSE FAMILY/NURI INSERTION",
                "",
                "STATUS: STRUCTURAL_PASS_WAITING_MUZA_VISUAL_QA",
                f"Project: {project_path}",
                f"Backup: {backup_path}",
                f"Target: {TARGET_NAME}",
                "Saved project reopened and parsed: PASS",
                "Exact 28-clip order and source IN/OUT: PASS",
                "KEEP clips/frames: 4 / 3827 — PASS",
                "Family clips/frames: 23 / 852 — PASS",
                "Nuri clips/frames: 1 / 75 — PASS",
                "N1 immediately before K4: PASS",
                "Total: 4754 frames / 190.16 seconds — PASS",
                "Audio clips and preview audio streams: 0 — PASS",
                "Four protected sequences unchanged: PASS",
                "Preview/project frame counts identical: PASS",
                "Known rough-cut remainders: hospital, young child, coarse joins",
                "Fine cleanup: NOT EXECUTED (deferred by authorization)",
                "Premiere open-check screenshot: when available",
                "",
                "STOP: wait for Sergey/Muza visual QA. Do not create TASK_020_DONE.txt.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    waiting_path = output_dir / "TASK_020_STAGE_B_WAITING_MUZA_QA.txt"
    waiting_path.write_text(
        "\n".join(
            [
                "TASK_020 STAGE B — WAITING MUZA QA",
                f"created_at: {datetime.now().isoformat(timespec='seconds')}",
                f"project: {project_path}",
                f"sequence: {TARGET_NAME}",
                "preview: TASK_020_STAGE_B_COARSE_FAMILY_NURI_640_360.mp4",
                "28 clips / 4754 frames / 190.16 seconds / NO AUDIO",
                "Family: 23 clips exactly once; N1 immediately before K4.",
                "Known defects are intentionally retained for later fine cleanup.",
                "No TASK_020_DONE.txt. Executor/tests remain uncommitted.",
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
        "insertion_proof": proof_path,
        "contact_sheet": contact_path,
        "ffprobe": probe_path,
        "qa": qa_path,
        "waiting": waiting_path,
    }
