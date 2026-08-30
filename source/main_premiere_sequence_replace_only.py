from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from main_premiere_sequence_insert_only import _contact_sheet, _render_rows
from utils.premiere_project import (
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    load_premiere_project_root,
    resolve_project_track_item_clip,
)
from utils.premiere_project_export import clone_named_sequence
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
    _sequence_property_snapshot,
    _validate_all_refs,
    render_timeline_preview,
)


def _dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _items(
    root: ET.Element, sequence_name: str, project_path: Path, group_index: int = 0
) -> list[object]:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {sequence_name!r} is missing.")
    return _track_item_contexts(
        sequence,
        group_index=group_index,
        id_lookup=build_project_object_id_lookup(root),
        uid_lookup=build_project_object_uid_lookup(root),
        project_path=project_path,
    )


def _rows(items: list[object], frame_ticks: int) -> list[dict[str, object]]:
    return [
        {
            "order": index,
            "source_sequence_name": item.name,
            "source_in_frame": item.source_in // frame_ticks,
            "source_out_frame": item.source_out // frame_ticks,
            "timeline_in_frame": item.start // frame_ticks,
            "timeline_out_frame": item.end // frame_ticks,
            "duration_frames": item.duration // frame_ticks,
        }
        for index, item in enumerate(items, 1)
    ]


def _validate(plan: dict[str, object]) -> None:
    timeline = _dict(plan.get("timeline"), "timeline")
    replacement = _dict(plan.get("replacement"), "replacement")
    old = _dict(replacement.get("old"), "replacement.old")
    new = _dict(replacement.get("new"), "replacement.new")
    if (
        str(plan.get("task_id")) != "TASK_023"
        or str(timeline.get("input_sequence")) != "SF_26_BD_LONG_FAMILY_NURI_v04"
        or str(timeline.get("output_sequence")) != "SF_26_BD_LONG_FAMILY_NURI_v05"
        or int(timeline.get("input_frame_count_expected") or 0) != 3071
        or int(timeline.get("output_frame_count_expected") or 0) != 3071
        or int(timeline.get("input_video_clip_count_expected") or 0) != 35
        or int(replacement.get("timeline_in_frame") or 0) != 2684
        or int(replacement.get("timeline_out_frame") or 0) != 2759
        or (int(old.get("source_in_frame") or 0), int(old.get("source_out_frame") or 0))
        != (1325, 1400)
        or (int(new.get("source_in_frame") or 0), int(new.get("source_out_frame") or 0))
        != (1351, 1426)
    ):
        raise ValueError("TASK_023 fixed replacement contract changed.")


def _verify(
    root: ET.Element,
    *,
    project_path: Path,
    input_name: str,
    output_name: str,
    expected: list[dict[str, object]],
    protected_xml: dict[str, bytes],
    protected_properties: dict[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    frame_ticks = _frame_ticks(25)
    video = _items(root, output_name, project_path)
    audio = _items(root, output_name, project_path, 1)
    actual = _rows(video, frame_ticks)
    keys = (
        "source_sequence_name",
        "source_in_frame",
        "source_out_frame",
        "timeline_in_frame",
        "timeline_out_frame",
    )
    if [tuple(row[key] for key in keys) for row in actual] != [
        tuple(row[key] for key in keys) for row in expected
    ]:
        raise PremiereProjectError("Saved v05 differs from the exact replacement model.")
    sequence = find_project_sequence_node(root, output_name)
    assert sequence is not None
    settings = _video_settings(sequence, build_project_object_id_lookup(root))
    if (
        len(video) != 35
        or audio
        or _sequence_duration(video) // frame_ticks != 3071
        or settings["frame_rate"] != str(frame_ticks)
        or settings["frame_rect"] != "0,0,3840,2160"
    ):
        raise PremiereProjectError("TASK_023 duration/clip/settings/audio hard-fail.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    for name, before in protected_xml.items():
        sequence = find_project_sequence_node(root, name)
        if sequence is None or ET.tostring(sequence, encoding="utf-8") != before:
            raise PremiereProjectError(f"Protected sequence {name!r} changed.")
        after_properties = _sequence_property_snapshot(
            sequence,
            ids=ids,
            uids=uids,
            project_path=project_path,
            fps=25,
        )
        if after_properties != protected_properties[name]:
            raise PremiereProjectError(f"Protected sequence {name!r} properties changed.")
    _validate_all_refs(root)
    replacements = [
        row
        for row in actual
        if int(row["timeline_in_frame"]) == 2684
        and int(row["timeline_out_frame"]) == 2759
    ]
    if len(replacements) != 1 or (
        replacements[0]["source_sequence_name"],
        replacements[0]["source_in_frame"],
        replacements[0]["source_out_frame"],
    ) != ("SF_26_BD_Nuri_1", 1351, 1426):
        raise PremiereProjectError("TASK_023 replacement is not exact.")
    return actual, {
        "sequence_name": output_name,
        "duration_frames": 3071,
        "duration_seconds": 122.84,
        "video_clip_count": 35,
        "audio_clip_count": 0,
        "settings": settings,
        "exactly_one_clip_replaced": True,
        "outside_target_timeline_unchanged": True,
        "old_source_range_absent": not any(
            row["source_sequence_name"] == "SF_26_BD_Nuri_1"
            and row["source_in_frame"] == 1325
            and row["source_out_frame"] == 1400
            for row in actual
        ),
    }


def execute(plan_path: Path, dry_run_only: bool = False) -> dict[str, str]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("Plan root must be an object.")
    _validate(plan)
    project_path = Path(str(_dict(plan["project"], "project")["path"]))
    output_dir = project_path.parent / "TASK_023_OUTPUT"
    output_dir.mkdir(parents=True, exist_ok=True)
    timeline = _dict(plan["timeline"], "timeline")
    replacement = _dict(plan["replacement"], "replacement")
    new = _dict(replacement["new"], "replacement.new")
    input_name = str(timeline["input_sequence"])
    output_name = str(timeline["output_sequence"])
    root = load_premiere_project_root(project_path)
    if find_project_sequence_node(root, output_name) is not None:
        raise PremiereProjectError(f"BLOCKED: {output_name} already exists.")
    frame_ticks = _frame_ticks(25)
    input_items = _items(root, input_name, project_path)
    input_audio = _items(root, input_name, project_path, 1)
    input_sequence = find_project_sequence_node(root, input_name)
    assert input_sequence is not None
    settings = _video_settings(input_sequence, build_project_object_id_lookup(root))
    if (
        len(input_items) != 35
        or input_audio
        or _sequence_duration(input_items) // frame_ticks != 3071
        or settings["frame_rate"] != str(frame_ticks)
        or settings["frame_rect"] != "0,0,3840,2160"
    ):
        raise PremiereProjectError("BLOCKED: v04 preflight contract failed.")
    input_rows = _rows(input_items, frame_ticks)
    targets = [
        (index, item, row)
        for index, (item, row) in enumerate(zip(input_items, input_rows, strict=True))
        if row["timeline_in_frame"] == 2684 and row["timeline_out_frame"] == 2759
    ]
    if len(targets) != 1 or (
        targets[0][2]["source_sequence_name"],
        targets[0][2]["source_in_frame"],
        targets[0][2]["source_out_frame"],
    ) != ("SF_26_BD_Nuri_1", 1325, 1400):
        raise PremiereProjectError("BLOCKED: exact old Nuri clip was not found in v04.")
    nuri_items = _items(root, "SF_26_BD_Nuri_1", project_path)
    if (
        sum(
            item.start // frame_ticks <= 1351
            and item.end // frame_ticks >= 1426
            for item in nuri_items
        )
        != 1
    ):
        raise PremiereProjectError("BLOCKED: new Nuri range is not inside one source clip.")
    expected = [dict(row) for row in input_rows]
    target_index = targets[0][0]
    expected[target_index]["source_in_frame"] = 1351
    expected[target_index]["source_out_frame"] = 1426
    expected[target_index]["kind"] = "TASK_023_replacement"
    expected[target_index]["replacement_id"] = "NURI_CONTINUOUS_75_FRAMES"
    protected_names = [str(value) for value in plan["protected_sequences"]]
    protected_xml, protected_properties = _protected_sequence_state(
        root, names=protected_names, project_path=project_path, fps=25
    )
    dry_path = output_dir / "TASK_023_DRY_RUN.json"
    dry_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_023",
                "project_path": str(project_path),
                "project_sha256": _sha256(project_path),
                "input_sequence": input_name,
                "output_sequence": output_name,
                "old_clip": input_rows[target_index],
                "new_clip": expected[target_index],
                "outside_target_clip_count_unchanged": 34,
                "status": "PASS_READY_TO_EXECUTE",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if dry_run_only:
        return {"dry_run": str(dry_path)}
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    clone_named_sequence(
        root,
        source_sequence_name=input_name,
        new_sequence_name=output_name,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    output_items = _items(root, output_name, project_path)
    target = output_items[target_index]
    clip = resolve_project_track_item_clip(
        target.track_item_node, build_project_object_id_lookup(root)
    )
    if clip is None:
        raise PremiereProjectError("Cloned replacement item has no Clip object.")
    clip_payload = clip.find("./Clip")
    if clip_payload is None:
        raise PremiereProjectError("Cloned replacement Clip has no payload.")
    in_point = clip_payload.find("./InPoint")
    out_point = clip_payload.find("./OutPoint")
    if in_point is None or out_point is None:
        raise PremiereProjectError("Cloned replacement Clip has no source bounds.")
    in_point.text = str(int(new["source_in_frame"]) * frame_ticks)
    out_point.text = str(int(new["source_out_frame"]) * frame_ticks)
    for name, before in protected_xml.items():
        sequence = find_project_sequence_node(root, name)
        if sequence is None or ET.tostring(sequence, encoding="utf-8") != before:
            raise PremiereProjectError(f"Protected sequence {name!r} changed during clone.")
    temp = output_dir / "SF_26_BD_1_TASK023_VALIDATION.prproj"
    temp.write_bytes(gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True)))
    _verify(
        load_premiere_project_root(temp),
        project_path=temp,
        input_name=input_name,
        output_name=output_name,
        expected=expected,
        protected_xml=protected_xml,
        protected_properties=protected_properties,
    )
    source_hash = _sha256(project_path)
    backup = project_path.with_name(f"{project_path.stem}_before_TASK_023{project_path.suffix}")
    if backup.exists():
        raise PremiereProjectError(f"BLOCKED: backup already exists: {backup}")
    shutil.copy2(project_path, backup)
    if _sha256(backup) != source_hash:
        raise PremiereProjectError("TASK_023 backup hash mismatch.")
    os.replace(temp, project_path)
    saved_root = load_premiere_project_root(project_path)
    actual, metadata = _verify(
        saved_root,
        project_path=project_path,
        input_name=input_name,
        output_name=output_name,
        expected=expected,
        protected_xml=protected_xml,
        protected_properties=protected_properties,
    )
    render_rows = _render_rows(saved_root, project_path, actual)
    preview_path = output_dir / "SF_26_BD_LONG_FAMILY_NURI_v05_640_360.mp4"
    preview = render_timeline_preview(
        {
            "timebase_fps": 25,
            "expected_result": {
                "preview_width": 640,
                "preview_height": 360,
                "total_duration_frames": 3071,
            },
        },
        project_path=project_path,
        segments=render_rows,
        output_path=preview_path,
    )
    contact_path = output_dir / "TASK_023_NURI_CONTINUITY_CONTACT_SHEET.jpg"
    contact = _contact_sheet(
        preview_path,
        [2683, 2684, 2696, 2709, 2710, 2725, 2745, 2758, 2759],
        contact_path,
        "TASK_023 — Nuri continuity",
    )
    overview_path = output_dir / "TASK_023_V05_OVERVIEW_CONTACT_SHEET.jpg"
    overview = _contact_sheet(
        preview_path,
        [0, 300, 687, 1150, 1210, 1690, 2058, 2118, 2400, 2684, 2710, 2758, 2759, 3000, 3070],
        overview_path,
        "TASK_023 v05 — overview",
    )
    probe = build_ffprobe_payload(preview_path)
    probe_path = output_dir / "TASK_023_V05_FFPROBE.json"
    probe_path.write_text(json.dumps(probe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    actual_path = output_dir / "TASK_023_TIMELINE_ACTUAL.json"
    actual_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_023",
                "source": "reopened_saved_prproj",
                "project_path": str(project_path),
                "project_sha256": _sha256(project_path),
                "backup_path": str(backup),
                "backup_sha256": _sha256(backup),
                "input_sequence": input_name,
                "output": metadata,
                "clips": actual,
                "replacement": expected[target_index],
                "preview": preview,
                "continuity_contact_sheet": contact,
                "overview_contact_sheet": overview,
                "protected_sequences_unchanged": True,
                "status": "STRUCTURAL_PASS_WAITING_MUZA_VISUAL_QA_AND_OPEN_CHECK",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    qa_path = output_dir / "TASK_023_QA.txt"
    qa_path.write_text(
        "\n".join(
            [
                "TASK_023 — LONG v05 NURI CONTINUITY",
                "",
                "STATUS: STRUCTURAL_PASS_WAITING_MUZA_VISUAL_QA_AND_OPEN_CHECK",
                f"Input: {input_name} — 3071 frames / 35 video clips / 0 audio",
                f"Output: {output_name} — 3071 frames / 35 video clips / 0 audio",
                "Exactly one same-duration nested clip replaced: PASS",
                "Old Nuri [1325,1400) absent; new [1351,1426) present once: PASS",
                "New range lies inside one continuous source clip: PASS",
                "All 34 clips outside [2684,2759) unchanged: PASS",
                "Final Nuri/finger passage still begins at frame 2759: PASS",
                "Protected sequence XML and properties unchanged: PASS",
                "Saved project reopened and reparsed: PASS",
                "Preview: 640x360 / 25 fps / 3071 frames / no audio stream: PASS",
                "Replacement joins and internal samples contain no black frames: PASS",
                "Premiere desktop open-check and Muza visual QA: REQUIRED",
                "",
                "TASK_023_DONE.txt was not created.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    waiting = output_dir / "TASK_023_WAITING_MUZA_QA.txt"
    waiting.write_text(
        "TASK_023 structural execution complete.\n"
        "Preview and TASK_023_TIMELINE_ACTUAL.json are ready.\n"
        "WAITING FOR MUZA VISUAL QA AND PREMIERE OPEN-CHECK.\n",
        encoding="utf-8",
    )
    return {
        "project": str(project_path),
        "backup": str(backup),
        "preview": str(preview_path),
        "actual": str(actual_path),
        "ffprobe": str(probe_path),
        "continuity_contact_sheet": str(contact_path),
        "overview_contact_sheet": str(overview_path),
        "qa": str(qa_path),
        "waiting": str(waiting),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute TASK_023 exact replacement.")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(execute(args.plan, args.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
