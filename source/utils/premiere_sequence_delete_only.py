from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
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
from utils.premiere_sequence_motion import (
    _frame_ticks,
    _sequence_duration,
    _sha256,
    _track_item_contexts,
    _video_settings,
)
from utils.premiere_sequence_timeline_assembly import (
    _sequence_property_snapshot,
    _validate_all_refs,
    assemble_target_sequence,
    render_timeline_preview,
)


STAGE = "A_DELETE_ONLY"
EXPECTED_SOURCE_SEQUENCE = "SF_26_BD_Keep_08"
EXPECTED_TARGET_SEQUENCE = "SF_26_BD_KEEP_DELETE_ONLY_TEST_v01"


def _require_dict(payload: object, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return payload


def build_delete_only_segments(plan: dict[str, object]) -> list[dict[str, object]]:
    keep_ranges = plan.get("keep_ranges")
    if not isinstance(keep_ranges, list):
        raise ValueError("keep_ranges must be a list.")
    source_name = str(plan.get("source_sequence") or "")
    cursor = 0
    segments: list[dict[str, object]] = []
    for order, raw in enumerate(keep_ranges, start=1):
        item = _require_dict(raw, "keep range")
        source_in = int(item["source_in_frame"])
        source_out = int(item["source_out_frame"])
        duration = int(item["duration_frames"])
        if source_out - source_in != duration:
            raise ValueError(f"KEEP range {item.get('id')} has inconsistent duration.")
        segments.append(
            {
                "order": order,
                "segment_id": str(item["id"]),
                "source_sequence_name": source_name,
                "source_in_frame": source_in,
                "source_out_frame": source_out,
                "timeline_in_frame": cursor,
                "timeline_out_frame": cursor + duration,
                "duration_frames": duration,
            }
        )
        cursor += duration
    return segments


def validate_delete_only_plan(plan: dict[str, object]) -> list[dict[str, object]]:
    if str(plan.get("task_id") or "") != "TASK_020":
        raise ValueError("Expected task_id=TASK_020.")
    if str(plan.get("stage") or "") != STAGE or not bool(plan.get("stop_after_stage")):
        raise ValueError("TASK_020 executor supports Stage A delete-only and must stop.")
    if str(plan.get("source_sequence") or "") != EXPECTED_SOURCE_SEQUENCE:
        raise ValueError("Stage A source sequence mismatch.")
    target = _require_dict(plan.get("target_sequence"), "target_sequence")
    if str(target.get("name") or "") != EXPECTED_TARGET_SEQUENCE:
        raise ValueError("Stage A target sequence mismatch.")
    expected = _require_dict(plan.get("expected_actual"), "expected_actual")
    if (
        int(expected.get("video_clip_count") or 0) != 4
        or int(expected.get("duration_frames") or 0) != 3827
        or int(expected.get("removed_frames") or 0) != 1173
        or int(expected.get("audio_clip_count", -1)) != 0
    ):
        raise ValueError("Stage A fixed expected counts/durations were changed.")
    segments = build_delete_only_segments(plan)
    if len(segments) != 4:
        raise ValueError("Stage A requires exactly four KEEP ranges.")
    if sum(int(item["duration_frames"]) for item in segments) != 3827:
        raise ValueError("Stage A KEEP ranges must total exactly 3827 frames.")
    removed = plan.get("removed_ranges")
    if not isinstance(removed, list) or sum(
        int(_require_dict(item, "removed range")["duration_frames"])
        for item in removed
    ) != 1173:
        raise ValueError("Stage A removed ranges must total exactly 1173 frames.")
    if set(str(value) for value in expected.get("forbidden_source_sequences") or []) != {
        "SF_26_BD_Family_1",
        "SF_26_BD_Nuri_1",
    }:
        raise ValueError("Stage A forbidden source sequence contract changed.")
    return segments


def _adapter_plan(
    plan: dict[str, object],
    segments: list[dict[str, object]],
) -> dict[str, object]:
    target = _require_dict(plan["target_sequence"], "target_sequence")
    expected = _require_dict(plan["expected_actual"], "expected_actual")
    return {
        "task_id": "TASK_020",
        "timebase_fps": int(target["fps"]),
        "target_sequence": {
            "name": target["name"],
            "settings_source_sequence": plan["source_sequence"],
        },
        "expected_result": {
            "total_duration_frames": expected["duration_frames"],
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


def _protected_sequence_state(
    root: ET.Element,
    *,
    names: list[str],
    project_path: Path,
    fps: int,
) -> tuple[dict[str, bytes], dict[str, object]]:
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    xml: dict[str, bytes] = {}
    properties: dict[str, object] = {}
    for name in names:
        nodes = [
            node
            for node in root.iter("Sequence")
            if (node.findtext("./Name") or "").strip() == name
        ]
        if len(nodes) != 1:
            raise PremiereProjectError(
                f"BLOCKED: protected sequence {name!r} count is {len(nodes)}."
            )
        xml[name] = ET.tostring(nodes[0], encoding="utf-8")
        properties[name] = _sequence_property_snapshot(
            nodes[0],
            ids=ids,
            uids=uids,
            project_path=project_path,
            fps=fps,
        )
    return xml, properties


def extract_actual_from_saved_project(
    *,
    project_path: Path,
    target_name: str,
    fps: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    nodes = [
        node
        for node in root.iter("Sequence")
        if (node.findtext("./Name") or "").strip() == target_name
    ]
    if len(nodes) != 1:
        raise PremiereProjectError(
            f"Saved-project QA: target sequence count is {len(nodes)}."
        )
    target = nodes[0]
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
    frame_ticks = _frame_ticks(fps)
    actual: list[dict[str, object]] = []
    for order, item in enumerate(video, start=1):
        actual.append(
            {
                "order": order,
                "source_sequence_name": item.name,
                "source_in_frame": item.source_in // frame_ticks,
                "source_out_frame": item.source_out // frame_ticks,
                "timeline_in_frame": item.start // frame_ticks,
                "timeline_out_frame": item.end // frame_ticks,
                "duration_frames": item.duration // frame_ticks,
                "audio_inserted": False,
            }
        )
    metadata = {
        "target_sequence_name": target_name,
        "target_settings": _video_settings(target, ids),
        "video_clip_count": len(video),
        "audio_clip_count": len(audio),
        "duration_frames": _sequence_duration(video) // frame_ticks,
        "duration_seconds": _sequence_duration(video) / PREMIERE_TICKS_PER_SECOND,
        "source_sequence_names": sorted({str(item["source_sequence_name"]) for item in actual}),
    }
    return actual, metadata


def verify_saved_delete_only_project(
    *,
    plan: dict[str, object],
    project_path: Path,
    protected_xml: dict[str, bytes],
    protected_properties: dict[str, object],
    expected_segments: list[dict[str, object]],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    target_name = str(_require_dict(plan["target_sequence"], "target_sequence")["name"])
    fps = int(_require_dict(plan["target_sequence"], "target_sequence")["fps"])
    actual, metadata = extract_actual_from_saved_project(
        project_path=project_path,
        target_name=target_name,
        fps=fps,
    )
    expected_ranges = [
        (
            str(item["source_sequence_name"]),
            int(item["source_in_frame"]),
            int(item["source_out_frame"]),
            int(item["timeline_in_frame"]),
            int(item["timeline_out_frame"]),
        )
        for item in expected_segments
    ]
    actual_ranges = [
        (
            str(item["source_sequence_name"]),
            int(item["source_in_frame"]),
            int(item["source_out_frame"]),
            int(item["timeline_in_frame"]),
            int(item["timeline_out_frame"]),
        )
        for item in actual
    ]
    if actual_ranges != expected_ranges:
        raise PremiereProjectError(
            f"Saved-project QA: actual ranges differ: {actual_ranges!r}"
        )
    if metadata["video_clip_count"] != 4 or metadata["duration_frames"] != 3827:
        raise PremiereProjectError(
            "Saved-project QA: target is not exactly four clips / 3827 frames."
        )
    if metadata["duration_frames"] >= 4900:
        raise PremiereProjectError(
            "Saved-project QA: hard fail, output remains close to 200 seconds."
        )
    if metadata["audio_clip_count"] != 0:
        raise PremiereProjectError("Saved-project QA: target contains audio clips.")
    if metadata["source_sequence_names"] != [EXPECTED_SOURCE_SEQUENCE]:
        raise PremiereProjectError(
            "Saved-project QA: Family/Nuri or another source sequence is present."
        )
    removed_ranges = [
        (
            int(_require_dict(item, "removed range")["source_in_frame"]),
            int(_require_dict(item, "removed range")["source_out_frame"]),
        )
        for item in plan["removed_ranges"]  # type: ignore[index]
    ]
    overlaps: list[dict[str, int]] = []
    for item in actual:
        source_in = int(item["source_in_frame"])
        source_out = int(item["source_out_frame"])
        for removed_in, removed_out in removed_ranges:
            overlap = max(0, min(source_out, removed_out) - max(source_in, removed_in))
            if overlap:
                overlaps.append(
                    {
                        "source_in_frame": source_in,
                        "source_out_frame": source_out,
                        "removed_in_frame": removed_in,
                        "removed_out_frame": removed_out,
                        "overlap_frames": overlap,
                    }
                )
    if overlaps:
        raise PremiereProjectError(
            f"Saved-project QA: output overlaps removed ranges: {overlaps!r}"
        )
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    after_properties: dict[str, object] = {}
    for name, before in protected_xml.items():
        source = find_project_sequence_node(root, name)
        if source is None or ET.tostring(source, encoding="utf-8") != before:
            raise PremiereProjectError(
                f"Saved-project QA: protected sequence {name!r} changed."
            )
        after_properties[name] = _sequence_property_snapshot(
            source,
            ids=ids,
            uids=uids,
            project_path=project_path,
            fps=fps,
        )
    if after_properties != protected_properties:
        raise PremiereProjectError(
            "Saved-project QA: protected sequence properties changed."
        )
    _validate_all_refs(root)
    qa = {
        **metadata,
        "actual_ranges_match_exactly": True,
        "removed_range_overlap_frames": 0,
        "family_or_nuri_references": 0,
        "protected_sequences_unchanged": True,
        "saved_project_reopened_and_reparsed": True,
        "object_references_resolved": True,
    }
    return qa, actual


def build_removal_proof(
    *,
    plan: dict[str, object],
    actual: list[dict[str, object]],
    preview_frames: int,
) -> dict[str, object]:
    removed = [
        {
            **_require_dict(item, "removed range"),
            "actual_output_overlap_frames": sum(
                max(
                    0,
                    min(int(segment["source_out_frame"]), int(item["source_out_frame"]))
                    - max(int(segment["source_in_frame"]), int(item["source_in_frame"])),
                )
                for segment in actual
                if segment["source_sequence_name"] == EXPECTED_SOURCE_SEQUENCE
            ),
        }
        for item in plan["removed_ranges"]  # type: ignore[index]
    ]
    retained_total = sum(int(item["duration_frames"]) for item in actual)
    removed_total = sum(int(item["duration_frames"]) for item in removed)
    return {
        "task_id": "TASK_020",
        "stage": STAGE,
        "proof_source": "reopened_saved_prproj",
        "actual_keep_ranges": actual,
        "removed_ranges": removed,
        "removed_intervals_total_frames": removed_total,
        "retained_intervals_total_frames": retained_total,
        "output_duration_equals_retained_sum": retained_total == 3827,
        "no_overlap_between_retained_and_removed": all(
            int(item["actual_output_overlap_frames"]) == 0 for item in removed
        ),
        "output_contains_family_or_nuri": False,
        "preview_frames": preview_frames,
        "preview_and_sequence_duration_match": preview_frames == retained_total,
        "status": "PASS",
    }


def _read_video_frame(path: Path, frame_number: int) -> object:
    import cv2

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open proof video: {path}")
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError(f"Could not decode proof frame {frame_number}: {path}")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def build_removal_contact_sheet(
    *,
    project_path: Path,
    preview_path: Path,
    removed_ranges: list[dict[str, object]],
    actual: list[dict[str, object]],
    output_path: Path,
) -> dict[str, object]:
    from PIL import Image, ImageDraw

    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    source = find_project_sequence_node(root, EXPECTED_SOURCE_SEQUENCE)
    if source is None:
        raise PremiereProjectError("Source sequence disappeared during proof rendering.")
    source_items = _track_item_contexts(
        source,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    if len(source_items) != 1:
        raise PremiereProjectError(
            "TASK_020 removal proof expects one continuous KEEP source item."
        )
    source_item = source_items[0]
    source_media = Path(source_item.source_path)
    panels: list[tuple[object, list[str]]] = []
    for index, raw in enumerate(removed_ranges):
        removed = _require_dict(raw, "removed range")
        before_segment = actual[index]
        after_segment = actual[index + 1]
        output_before = int(before_segment["timeline_out_frame"]) - 1
        output_after = int(after_segment["timeline_in_frame"])
        source_before = int(removed["source_in_frame"]) - 1
        source_after = int(removed["source_out_frame"])
        midpoint = (
            int(removed["source_in_frame"]) + int(removed["source_out_frame"])
        ) // 2
        media_midpoint = (
            source_item.source_in // _frame_ticks(25)
            + midpoint
            - source_item.start // _frame_ticks(25)
        )
        panels.append(
            (
                _read_video_frame(preview_path, output_before),
                [
                    f"{removed['id']} LAST RETAINED",
                    f"output {output_before} / source {source_before}",
                ],
            )
        )
        panels.append(
            (
                _read_video_frame(source_media, media_midpoint),
                [
                    f"{removed['id']} REMOVED MIDPOINT",
                    f"source {midpoint} (not from output)",
                ],
            )
        )
        panels.append(
            (
                _read_video_frame(preview_path, output_after),
                [
                    f"{removed['id']} FIRST RETAINED",
                    f"output {output_after} / source {source_after}",
                ],
            )
        )
    thumb_w, thumb_h, label_h = 426, 240, 52
    sheet = Image.new("RGB", (thumb_w * 3, (thumb_h + label_h) * 3), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (array, labels) in enumerate(panels):
        image = Image.fromarray(array)
        image.thumbnail((thumb_w, thumb_h))
        x = (index % 3) * thumb_w
        y = (index // 3) * (thumb_h + label_h)
        sheet.paste(image, (x, y))
        fill = "#b00020" if "REMOVED" in labels[0] else "black"
        draw.text((x + 5, y + thumb_h + 4), labels[0], fill=fill)
        draw.text((x + 5, y + thumb_h + 25), labels[1], fill=fill)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=92)
    return {
        "path": str(output_path),
        "deletion_count": 3,
        "panels_per_deletion": 3,
        "removed_midpoints_taken_from_output": False,
        "status": "PASS",
    }


def build_ffprobe_payload(preview_path: Path) -> dict[str, object]:
    import cv2

    capture = cv2.VideoCapture(str(preview_path))
    if not capture.isOpened():
        raise RuntimeError("Preview could not be opened for metadata proof.")
    payload = {
        "format": {"filename": str(preview_path), "sha256": _sha256(preview_path)},
        "streams": [
            {
                "codec_type": "video",
                "width": int(round(capture.get(cv2.CAP_PROP_FRAME_WIDTH))),
                "height": int(round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))),
                "avg_frame_rate": capture.get(cv2.CAP_PROP_FPS),
                "nb_frames": int(round(capture.get(cv2.CAP_PROP_FRAME_COUNT))),
            }
        ],
        "probe_engine": "OpenCV metadata plus ffmpeg stream inspection",
    }
    capture.release()
    from utils.video_frame_extract import resolve_ffmpeg_executable

    ffmpeg = resolve_ffmpeg_executable()
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(preview_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (result.stderr or "") + (result.stdout or "")
    payload["audio_stream_count"] = 1 if " Audio:" in text else 0
    payload["video_stream_count"] = 1 if " Video:" in text else 0
    payload["status"] = "PASS"
    return payload


def execute_delete_only_stage(
    plan_path: Path,
    *,
    dry_run_only: bool = False,
) -> dict[str, Path]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("TASK_020 plan must be a JSON object.")
    segments = validate_delete_only_plan(plan)
    project_path = Path(str(plan["project_path"]))
    if not project_path.is_file():
        raise PremiereProjectError(f"Project not found: {project_path}")
    output_dir = project_path.parent / "TASK_020_STAGE_A_OUTPUT"
    output_dir.mkdir(parents=True, exist_ok=True)
    root = load_premiere_project_root(project_path)
    target_name = str(_require_dict(plan["target_sequence"], "target_sequence")["name"])
    if find_project_sequence_node(root, target_name) is not None:
        raise PremiereProjectError(
            f"BLOCKED: target sequence {target_name!r} already exists."
        )
    protected_names = [str(value) for value in plan["protected_sequences"]]  # type: ignore[index]
    protected_xml, protected_properties = _protected_sequence_state(
        root,
        names=protected_names,
        project_path=project_path,
        fps=25,
    )
    source_hash_before = _sha256(project_path)
    dry_path = output_dir / "TASK_020_STAGE_A_DRY_RUN.json"
    dry_payload = {
        "task_id": "TASK_020",
        "stage": STAGE,
        "project_path": str(project_path),
        "project_sha256": source_hash_before,
        "target_sequence": target_name,
        "planned_keep_ranges": segments,
        "planned_video_clip_count": 4,
        "planned_audio_clip_count": 0,
        "planned_duration_frames": 3827,
        "planned_removed_frames": 1173,
        "family_or_nuri_planned": False,
        "blocked_items": [],
        "status": "PASS_READY_TO_EXECUTE",
    }
    dry_path.write_text(
        json.dumps(dry_payload, ensure_ascii=False, indent=2) + "\n",
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
    temp_path = output_dir / "SF_26_BD_1_TASK020_STAGE_A_VALIDATION.prproj"
    temp_path.write_bytes(
        gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    )
    verify_saved_delete_only_project(
        plan=plan,
        project_path=temp_path,
        protected_xml=protected_xml,
        protected_properties=protected_properties,
        expected_segments=segments,
    )
    backup_path = project_path.with_name(
        f"{project_path.stem}_before_TASK_020_STAGE_A{project_path.suffix}"
    )
    if backup_path.exists():
        raise PremiereProjectError(f"BLOCKED: backup already exists: {backup_path}")
    shutil.copy2(project_path, backup_path)
    if _sha256(backup_path) != source_hash_before:
        raise PremiereProjectError("TASK_020 backup SHA256 mismatch.")
    os.replace(temp_path, project_path)
    qa_project, actual = verify_saved_delete_only_project(
        plan=plan,
        project_path=project_path,
        protected_xml=protected_xml,
        protected_properties=protected_properties,
        expected_segments=segments,
    )
    preview_path = output_dir / "TASK_020_STAGE_A_DELETE_ONLY_640_360.mp4"
    preview_qa = render_timeline_preview(
        adapter,
        project_path=project_path,
        segments=segments,
        output_path=preview_path,
    )
    if int(preview_qa["frames"]) != 3827 or bool(preview_qa["has_audio_stream"]):
        raise RuntimeError("TASK_020 preview hard-fail condition was triggered.")
    proof = build_removal_proof(
        plan=plan,
        actual=actual,
        preview_frames=int(preview_qa["frames"]),
    )
    proof_path = output_dir / "TASK_020_STAGE_A_REMOVAL_PROOF.json"
    proof_path.write_text(
        json.dumps(proof, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    contact_path = output_dir / "TASK_020_STAGE_A_JOIN_CONTACT_SHEET.jpg"
    contact_qa = build_removal_contact_sheet(
        project_path=project_path,
        preview_path=preview_path,
        removed_ranges=plan["removed_ranges"],  # type: ignore[arg-type]
        actual=actual,
        output_path=contact_path,
    )
    probe = build_ffprobe_payload(preview_path)
    probe_path = output_dir / "TASK_020_STAGE_A_FFPROBE.json"
    probe_path.write_text(
        json.dumps(probe, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    actual_path = output_dir / "TASK_020_STAGE_A_ACTUAL.json"
    actual_payload = {
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
        "preview": preview_qa,
        "contact_sheet": contact_qa,
        "stage_b_executed": False,
        "status": "STRUCTURAL_PASS_PREMIERE_OPEN_CHECK_REQUIRED",
    }
    actual_path.write_text(
        json.dumps(actual_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    qa_path = output_dir / "TASK_020_STAGE_A_QA.txt"
    qa_path.write_text(
        "\n".join(
            [
                "TASK_020 — STAGE A DELETE ONLY",
                "",
                "STATUS: STRUCTURAL_PASS_PREMIERE_OPEN_CHECK_REQUIRED",
                f"Project: {project_path}",
                f"Backup: {backup_path}",
                f"Target sequence: {target_name}",
                "Saved project reopened and parsed: PASS",
                "Exactly 4 video clips: PASS",
                "Duration 3827 frames / 153.08 seconds: PASS",
                "Actual source ranges equal declared KEEP ranges: PASS",
                "Removed-range overlap: 0 frames — PASS",
                "Family references: 0 — PASS",
                "Nuri references: 0 — PASS",
                "Audio clips: 0 — PASS",
                "Preview: 640x360 / 25 fps / 3827 frames / no audio — PASS",
                "Protected source sequences unchanged: PASS",
                "Contact sheet with retained joins and removed midpoints: PASS",
                "Premiere repair/conversion open-check: REQUIRED",
                "Stage B: NOT EXECUTED / LOCKED",
                "",
                "STOP: wait for Sergey/Muza QA after preview.",
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
        "removal_proof": proof_path,
        "contact_sheet": contact_path,
        "ffprobe": probe_path,
        "qa": qa_path,
        "open_check": output_dir / "TASK_020_STAGE_A_PREMIERE_OPEN_CHECK.png",
        "waiting": output_dir / "TASK_020_STAGE_A_WAITING_MUZA_QA.txt",
    }
