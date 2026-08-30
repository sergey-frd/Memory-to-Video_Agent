from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

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
    _update_sequence_duration_metadata,
    clone_named_sequence,
)
from utils.premiere_sequence_delete_only import (
    _protected_sequence_state,
    build_ffprobe_payload,
)
from utils.premiere_sequence_insert_motion import _shift_item
from utils.premiere_sequence_motion import (
    _frame_ticks,
    _sequence_duration,
    _sha256,
    _track_item_contexts,
    _video_settings,
)
from utils.premiere_sequence_timeline_assembly import (
    _clone_nested_segment,
    _validate_all_refs,
    render_timeline_preview,
)
from utils.premiere_trim_review_export import (
    _ensure_track_items_container,
    _reindex_track_items,
)


def _dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _items(
    root: ET.Element, sequence_name: str, project_path: Path, group: int = 0
) -> list[object]:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {sequence_name!r} was not found.")
    return _track_item_contexts(
        sequence,
        group_index=group,
        id_lookup=build_project_object_id_lookup(root),
        uid_lookup=build_project_object_uid_lookup(root),
        project_path=project_path,
    )


def _validate(plan: dict[str, object]) -> None:
    timeline = _dict(plan.get("timeline"), "timeline")
    insertions = plan.get("insertions_descending_execution_order")
    if (
        str(plan.get("task_id")) != "TASK_022"
        or str(timeline.get("input_sequence")) != "SF_26_BD_LONG_FAMILY_NURI_v03"
        or str(timeline.get("output_sequence")) != "SF_26_BD_LONG_FAMILY_NURI_v04"
        or int(timeline.get("input_frame_count_expected") or 0) != 2876
        or int(timeline.get("output_frame_count_expected") or 0) != 3071
        or int(timeline.get("fps") or 0) != 25
        or not isinstance(insertions, list)
        or len(insertions) != 3
    ):
        raise ValueError("TASK_022 fixed contract changed.")
    starts = [int(_dict(item, "insertion")["timeline_insert_frame_original_v03"]) for item in insertions]
    if starts != sorted(starts, reverse=True):
        raise ValueError("Insertions must be in descending original-v03 order.")
    if sum(int(_dict(item, "insertion")["duration_frames"]) for item in insertions) != 195:
        raise ValueError("TASK_022 must add exactly 195 frames.")


def _model(
    input_items: list[object], insertions: list[dict[str, Any]], frame_ticks: int
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in input_items:
        start = item.start // frame_ticks
        end = item.end // frame_ticks
        shift = sum(
            int(raw["duration_frames"])
            for raw in insertions
            if int(raw["timeline_insert_frame_original_v03"]) <= start
        )
        result.append(
            {
                "kind": "retained_v03",
                "source_sequence_name": item.name,
                "source_in_frame": item.source_in // frame_ticks,
                "source_out_frame": item.source_out // frame_ticks,
                "timeline_in_frame": start + shift,
                "timeline_out_frame": end + shift,
                "duration_frames": end - start,
            }
        )
    for raw in insertions:
        result.append(
            {
                "kind": "TASK_022_insertion",
                "segment_id": raw["id"],
                "source_sequence_name": raw["source_sequence"],
                "source_in_frame": int(raw["source_in_frame"]),
                "source_out_frame": int(raw["source_out_frame"]),
                "timeline_in_frame": int(raw["final_timeline_in_frame"]),
                "timeline_out_frame": int(raw["final_timeline_out_frame"]),
                "duration_frames": int(raw["duration_frames"]),
            }
        )
    result.sort(key=lambda item: (int(item["timeline_in_frame"]), int(item["timeline_out_frame"])))
    return [{**item, "order": index} for index, item in enumerate(result, 1)]


def _assemble(
    root: ET.Element,
    *,
    project_path: Path,
    input_name: str,
    output_name: str,
    insertions: list[dict[str, Any]],
    protected_xml: dict[str, bytes],
) -> None:
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    source = find_project_sequence_node(root, input_name)
    if source is None:
        raise PremiereProjectError("TASK_022 input sequence disappeared.")
    templates = _track_item_contexts(
        source,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    clone_named_sequence(
        root,
        source_sequence_name=input_name,
        new_sequence_name=output_name,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    frame_ticks = _frame_ticks(25)
    for raw in insertions:
        ids = build_project_object_id_lookup(root)
        uids = build_project_object_uid_lookup(root)
        target = find_project_sequence_node(root, output_name)
        if target is None:
            raise PremiereProjectError("TASK_022 output clone disappeared.")
        at = int(raw["timeline_insert_frame_original_v03"]) * frame_ticks
        duration = int(raw["duration_frames"]) * frame_ticks
        current = _track_item_contexts(
            target,
            group_index=0,
            id_lookup=ids,
            uid_lookup=uids,
            project_path=project_path,
        )
        if any(item.start < at < item.end for item in current):
            raise PremiereProjectError(f"Insertion {raw['id']} is not on a clip boundary.")
        for item in current:
            if item.start >= at:
                _shift_item(item, duration)
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
            raise PremiereProjectError("TASK_022 output has no V1.")
        container = _ensure_track_items_container(track)
        if container is None:
            raise PremiereProjectError("TASK_022 V1 has no item container.")
        _, ref = _clone_nested_segment(
            root,
            template_item=templates[0].track_item_node,
            source_sequence_name=str(raw["source_sequence"]),
            source_in_ticks=int(raw["source_in_frame"]) * frame_ticks,
            source_out_ticks=int(raw["source_out_frame"]) * frame_ticks,
            timeline_in_ticks=at,
            timeline_out_ticks=at + duration,
            ids=ids,
            uids=uids,
        )
        ids = build_project_object_id_lookup(root)
        insert_at = len(container)
        for index, old_ref in enumerate(container.findall("./TrackItem")):
            node = ids.get(old_ref.attrib.get("ObjectRef", ""))
            start = int(node.findtext("./ClipTrackItem/TrackItem/Start") or 0) if node is not None else -1
            if start >= at + duration:
                insert_at = index
                break
        container.insert(insert_at, ref)
        _reindex_track_items(container)
    ids = build_project_object_id_lookup(root)
    target = find_project_sequence_node(root, output_name)
    assert target is not None
    _update_sequence_duration_metadata(root, target, new_total_duration=3071 * frame_ticks)
    for name, before in protected_xml.items():
        sequence = find_project_sequence_node(root, name)
        if sequence is None or ET.tostring(sequence, encoding="utf-8") != before:
            raise PremiereProjectError(f"Protected sequence {name!r} changed.")
    _validate_all_refs(root)


def _actual(
    root: ET.Element, project_path: Path, output_name: str, expected: list[dict[str, object]]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    frame_ticks = _frame_ticks(25)
    video = _items(root, output_name, project_path)
    audio = _items(root, output_name, project_path, 1)
    rows = [
        {
            "order": index,
            "source_sequence_name": item.name,
            "source_in_frame": item.source_in // frame_ticks,
            "source_out_frame": item.source_out // frame_ticks,
            "timeline_in_frame": item.start // frame_ticks,
            "timeline_out_frame": item.end // frame_ticks,
            "duration_frames": item.duration // frame_ticks,
        }
        for index, item in enumerate(video, 1)
    ]
    keys = (
        "source_sequence_name",
        "source_in_frame",
        "source_out_frame",
        "timeline_in_frame",
        "timeline_out_frame",
    )
    if [tuple(row[key] for key in keys) for row in rows] != [
        tuple(row[key] for key in keys) for row in expected
    ]:
        raise PremiereProjectError("Saved v04 differs from the modeled insert-only timeline.")
    sequence = find_project_sequence_node(root, output_name)
    assert sequence is not None
    settings = _video_settings(sequence, build_project_object_id_lookup(root))
    duration = _sequence_duration(video) // frame_ticks
    if (
        duration != 3071
        or audio
        or settings["frame_rate"] != str(frame_ticks)
        or settings["frame_rect"] != "0,0,3840,2160"
    ):
        raise PremiereProjectError("TASK_022 duration/settings/audio hard-fail.")
    enriched = [{**row, **{key: value for key, value in expected[index - 1].items() if key not in row}} for index, row in enumerate(rows, 1)]
    return enriched, {
        "sequence_name": output_name,
        "duration_frames": duration,
        "duration_seconds": duration / 25,
        "video_clip_count": len(video),
        "audio_clip_count": len(audio),
        "settings": settings,
    }


def _render_rows(
    root: ET.Element, project_path: Path, rows: list[dict[str, object]]
) -> list[dict[str, object]]:
    frame_ticks = _frame_ticks(25)
    result: list[dict[str, object]] = []
    cursor = 0
    for row in rows:
        source_name = str(row["source_sequence_name"])
        start = int(row["source_in_frame"])
        end = int(row["source_out_frame"])
        source_items = _items(root, source_name, project_path)
        boundaries = {start, end}
        for item in source_items:
            item_start = item.start // frame_ticks
            item_end = item.end // frame_ticks
            if start < item_start < end:
                boundaries.add(item_start)
            if start < item_end < end:
                boundaries.add(item_end)
        ordered = sorted(boundaries)
        pieces = list(zip(ordered, ordered[1:]))
        if any(
            not any(
                item.start // frame_ticks <= left
                and item.end // frame_ticks >= right
                for item in source_items
            )
            for left, right in pieces
        ):
            raise PremiereProjectError(
                f"Preview source range has a gap: {source_name} [{start},{end})."
            )
        for left, right in pieces:
            result.append(
                {
                    "source_sequence_name": source_name,
                    "source_in_frame": left,
                    "source_out_frame": right,
                    "timeline_in_frame": cursor,
                    "timeline_out_frame": cursor + right - left,
                    "duration_frames": right - left,
                }
            )
            cursor += right - left
    if cursor != 3071:
        raise PremiereProjectError(f"Preview model has {cursor} frames instead of 3071.")
    return result


def _contact_sheet(preview: Path, frames: list[int], output: Path, title: str) -> dict[str, object]:
    import cv2

    capture = cv2.VideoCapture(str(preview))
    if not capture.isOpened():
        raise RuntimeError("Could not open TASK_022 preview for contact sheet.")
    images = []
    black = []
    for frame_number in frames:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ok, frame = capture.read()
        if not ok:
            raise RuntimeError(f"Could not decode frame {frame_number}.")
        if float(frame.mean()) < 2:
            black.append(frame_number)
        images.append((frame_number, Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))))
    capture.release()
    if black:
        raise RuntimeError(f"Black frames found: {black}")
    width, height, label = 320, 180, 26
    columns = 4
    rows = math.ceil(len(images) / columns)
    sheet = Image.new("RGB", (columns * width, 34 + rows * (height + label)), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 8), title, fill="black")
    for index, (number, image) in enumerate(images):
        image.thumbnail((width, height))
        x = (index % columns) * width
        y = 34 + (index // columns) * (height + label)
        sheet.paste(image, (x, y))
        draw.text((x + 4, y + height + 3), f"frame {number}", fill="black")
    sheet.save(output, quality=92)
    return {"path": str(output), "sampled_frames": frames, "black_frames": [], "status": "PASS"}


def execute(plan_path: Path, dry_run_only: bool = False) -> dict[str, str]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("Plan root must be an object.")
    _validate(plan)
    project_path = Path(str(_dict(plan["project"], "project")["path"]))
    output_dir = project_path.parent / "TASK_022_OUTPUT"
    output_dir.mkdir(parents=True, exist_ok=True)
    timeline = _dict(plan["timeline"], "timeline")
    input_name = str(timeline["input_sequence"])
    output_name = str(timeline["output_sequence"])
    insertions = [_dict(item, "insertion") for item in plan["insertions_descending_execution_order"]]
    root = load_premiere_project_root(project_path)
    resume_existing_output = find_project_sequence_node(root, output_name) is not None
    input_items = _items(root, input_name, project_path)
    input_audio = _items(root, input_name, project_path, 1)
    ids = build_project_object_id_lookup(root)
    input_sequence = find_project_sequence_node(root, input_name)
    assert input_sequence is not None
    if (
        _sequence_duration(input_items) // _frame_ticks(25) != 2876
        or input_audio
        or _video_settings(input_sequence, ids)["frame_rect"] != "0,0,3840,2160"
    ):
        raise PremiereProjectError("BLOCKED: v03 preflight contract failed.")
    protected_names = [str(value) for value in plan["protected_sequences"]]
    protected_xml, protected_properties = _protected_sequence_state(
        root, names=protected_names, project_path=project_path, fps=25
    )
    expected = _model(input_items, insertions, _frame_ticks(25))
    dry_path = output_dir / "TASK_022_DRY_RUN.json"
    dry_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_022",
                "project_path": str(project_path),
                "project_sha256": _sha256(project_path),
                "input_sequence": input_name,
                "input_frames": 2876,
                "output_sequence": output_name,
                "insertions": insertions,
                "modeled_output_clips": expected,
                "modeled_output_frames": 3071,
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
    backup = project_path.with_name(f"{project_path.stem}_before_TASK_022{project_path.suffix}")
    if resume_existing_output:
        if not backup.is_file():
            raise PremiereProjectError("BLOCKED: existing v04 has no TASK_022 backup.")
        source_hash = _sha256(backup)
    else:
        source_hash = _sha256(project_path)
        _assemble(
            root,
            project_path=project_path,
            input_name=input_name,
            output_name=output_name,
            insertions=insertions,
            protected_xml=protected_xml,
        )
        temp = output_dir / "SF_26_BD_1_TASK022_VALIDATION.prproj"
        temp.write_bytes(
            gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        )
        saved_root = load_premiere_project_root(temp)
        _actual(saved_root, temp, output_name, expected)
        if backup.exists():
            raise PremiereProjectError(f"BLOCKED: backup already exists: {backup}")
        shutil.copy2(project_path, backup)
        if _sha256(backup) != source_hash:
            raise PremiereProjectError("TASK_022 backup hash mismatch.")
        os.replace(temp, project_path)
    saved_root = load_premiere_project_root(project_path)
    actual, metadata = _actual(saved_root, project_path, output_name, expected)
    after_ids = build_project_object_id_lookup(saved_root)
    after_uids = build_project_object_uid_lookup(saved_root)
    from utils.premiere_sequence_timeline_assembly import _sequence_property_snapshot

    after_properties = {
        name: _sequence_property_snapshot(
            find_project_sequence_node(saved_root, name),
            ids=after_ids,
            uids=after_uids,
            project_path=project_path,
            fps=25,
        )
        for name in protected_names
    }
    if after_properties != protected_properties:
        raise PremiereProjectError("Protected sequence properties changed after save.")
    render_rows = _render_rows(saved_root, project_path, actual)
    preview_path = output_dir / "SF_26_BD_LONG_FAMILY_NURI_v04_640_360.mp4"
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
    joins = [1150, 1210, 2058, 2118, 2684, 2759]
    join_frames = [frame for boundary in joins for frame in (boundary - 1, boundary)]
    join_path = output_dir / "TASK_022_V04_JOIN_CONTACT_SHEET.jpg"
    join = _contact_sheet(preview_path, join_frames, join_path, "TASK_022 v04 — six new joins")
    overview_path = output_dir / "TASK_022_V04_OVERVIEW_CONTACT_SHEET.jpg"
    overview = _contact_sheet(
        preview_path,
        [0, 300, 687, 1149, 1150, 1209, 1210, 1690, 2058, 2117, 2400, 2684, 2758, 2759, 3000, 3070],
        overview_path,
        "TASK_022 v04 — overview",
    )
    probe = build_ffprobe_payload(preview_path)
    probe_path = output_dir / "TASK_022_V04_FFPROBE.json"
    probe_path.write_text(json.dumps(probe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    actual_path = output_dir / "TASK_022_TIMELINE_ACTUAL.json"
    actual_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_022",
                "source": "reopened_saved_prproj",
                "project_path": str(project_path),
                "project_sha256": _sha256(project_path),
                "backup_path": str(backup),
                "backup_sha256": _sha256(backup),
                "input_sequence": input_name,
                "output": metadata,
                "clips": actual,
                "insertions": [row for row in actual if row.get("kind") == "TASK_022_insertion"],
                "preview": preview,
                "join_contact_sheet": join,
                "overview_contact_sheet": overview,
                "protected_sequences_unchanged": True,
                "saved_project_reopened_and_reparsed": True,
                "status": "STRUCTURAL_PASS_WAITING_MUZA_VISUAL_QA",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    qa_path = output_dir / "TASK_022_QA.txt"
    qa_path.write_text(
        "\n".join(
            [
                "TASK_022 — LONG v04 ADD MISSING FAMILY/NURI",
                "",
                "STATUS: STRUCTURAL_PASS_WAITING_MUZA_VISUAL_QA",
                f"Project: {project_path}",
                f"Backup: {backup}",
                f"Input: {input_name} — 2876 frames, unchanged",
                f"Output: {output_name} — 3071 frames / 122.84 seconds",
                "Exactly three video-only nested sequence inserts: PASS",
                "Retained v03 order and source bounds: PASS",
                "Male-family / Ksenia-later / Sergey-holds-Nuri placement: PASS",
                f"Output video/audio clips: {metadata['video_clip_count']} / 0 — PASS",
                "Protected sequences XML and property snapshots unchanged: PASS",
                "Saved project reopened and reparsed: PASS",
                "Preview: 640x360 / 25 fps / 3071 frames / no audio stream — PASS",
                "Six new joins, both sides, no black frames: PASS",
                "Premiere desktop open-check: REQUIRED during visual QA",
                "",
                "TASK_022_DONE.txt was not created.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    waiting = output_dir / "TASK_022_WAITING_MUZA_QA.txt"
    waiting.write_text(
        "TASK_022 structural execution complete.\n"
        "Preview and TASK_022_TIMELINE_ACTUAL.json are ready.\n"
        "WAITING FOR MUZA VISUAL QA. TASK_022_DONE.txt must not exist yet.\n",
        encoding="utf-8",
    )
    return {
        "project": str(project_path),
        "backup": str(backup),
        "preview": str(preview_path),
        "actual": str(actual_path),
        "ffprobe": str(probe_path),
        "join_contact_sheet": str(join_path),
        "overview_contact_sheet": str(overview_path),
        "qa": str(qa_path),
        "waiting": str(waiting),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute TASK_022 insert-only Premiere edit.")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(execute(args.plan, args.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
