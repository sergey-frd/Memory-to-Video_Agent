from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

from utils.premiere_project import (
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    load_premiere_project_root,
)
from utils.premiere_sequence_delete_only import build_ffprobe_payload
from utils.premiere_sequence_motion import (
    _frame_ticks,
    _sequence_duration,
    _sha256,
    _track_item_contexts,
    _video_settings,
)
from utils.premiere_sequence_timeline_assembly import (
    _sequence_property_snapshot,
    assemble_target_sequence,
    build_join_contact_sheet,
    render_timeline_preview,
    validate_timeline_segments,
    verify_assembled_project,
)


INPUT_SEQUENCE = "SF_26_BD_LONG_FAMILY_NURI_v05"
OUTPUT_SEQUENCE = "SF_26_BD_SHORT_CORE_v01"
PROTECTED = [
    INPUT_SEQUENCE,
    "SF_26_BD_Keep_08",
    "SF_26_BD_Family_1",
    "SF_26_BD_Nuri_1",
]


def _contexts(root: ET.Element, name: str, project: Path, group: int) -> list[object]:
    sequence = find_project_sequence_node(root, name)
    if sequence is None:
        raise PremiereProjectError(f"Required sequence {name!r} is missing.")
    return _track_item_contexts(
        sequence,
        group_index=group,
        id_lookup=build_project_object_id_lookup(root),
        uid_lookup=build_project_object_uid_lookup(root),
        project_path=project,
    )


def _resolved_segments() -> list[dict[str, object]]:
    choices = [
        ("NURI_A", "SF_26_BD_Nuri_1", 1351, 1426, "Sergey holds Nuri"),
        ("NURI_B", "SF_26_BD_Keep_08", 4518, 4608, "Sergey and Nuri continuation"),
        ("CAR_DRIVER", "SF_26_BD_Keep_08", 1081, 1161, "Sergey driving"),
        ("CAR_STOP", "SF_26_BD_Keep_08", 1350, 1425, "car movement"),
        ("CHILDHOOD", "SF_26_BD_Keep_08", 0, 75, "boy with animal"),
        ("SERGEY_MEMORY", "SF_26_BD_Keep_08", 275, 350, "memory portrait"),
        ("KSENIA_LATE", "SF_26_BD_Family_1", 1235, 1295, "Ksenia family image"),
        ("MALE_FAMILY_PORTRAIT", "SF_26_BD_Family_1", 10, 70, "male family portrait"),
        (
            "BAR_MITZVAH_THREE_MEN",
            "SF_26_BD_Family_1",
            435,
            495,
            "first retained image of Sergey with Sasha and Dima",
        ),
        (
            "GRANDCHILDREN_ONE_IMAGE",
            "SF_26_BD_Family_1",
            535,
            595,
            "clear retained image of Sergey with grandchild",
        ),
        ("DESERT_ROAD", "SF_26_BD_Keep_08", 3870, 3903, "desert road"),
        ("OPEN_SPACE", "SF_26_BD_Keep_08", 3921, 3996, "open space, no camels"),
        ("FINAL_WALK", "SF_26_BD_Keep_08", 3587, 3650, "Sergey walking toward camera"),
    ]
    result = []
    cursor = 0
    for order, (segment_id, source, source_in, source_out, role) in enumerate(
        choices, 1
    ):
        duration = source_out - source_in
        result.append(
            {
                "order": order,
                "segment_id": segment_id,
                "source_sequence_name": source,
                "source_in_frame": source_in,
                "source_out_frame": source_out,
                "timeline_in_frame": cursor,
                "timeline_out_frame": cursor + duration,
                "duration_frames": duration,
                "content_role": role,
            }
        )
        cursor += duration
    return result


def _plan() -> dict[str, object]:
    return {
        "timebase_fps": 25,
        "target_sequence": {
            "name": OUTPUT_SEQUENCE,
            "settings_source_sequence": INPUT_SEQUENCE,
        },
        "expected_result": {
            "visual_segment_count": 13,
            "total_duration_frames": 881,
            "preview_width": 640,
            "preview_height": 360,
        },
        "segment_defaults": {
            "transitions": "none",
            "effects": "none",
            "speed_percent": 100,
        },
    }


def _overview(preview: Path, segments: list[dict[str, object]], output: Path) -> dict[str, object]:
    import cv2

    capture = cv2.VideoCapture(str(preview))
    if not capture.isOpened():
        raise RuntimeError("Could not open TASK_024 preview.")
    cells = []
    black = []
    for segment in segments:
        start = int(segment["timeline_in_frame"])
        end = int(segment["timeline_out_frame"])
        for marker, frame_number in (
            ("A", start),
            ("M", (start + end - 1) // 2),
            ("Z", end - 1),
        ):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"Could not decode TASK_024 frame {frame_number}.")
            if float(frame.mean()) < 2:
                black.append(frame_number)
            cells.append(
                (
                    Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)),
                    f"{segment['segment_id']} {marker} frame {frame_number}",
                )
            )
    capture.release()
    if black:
        raise RuntimeError(f"Black frames in TASK_024 overview: {black}")
    width, height, label = 320, 180, 26
    columns = 3
    rows = math.ceil(len(cells) / columns)
    sheet = Image.new(
        "RGB", (columns * width, rows * (height + label) + 34), "white"
    )
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 8), "TASK_024 SHORT CORE — first/middle/last", fill="black")
    for index, (image, text) in enumerate(cells):
        x = (index % columns) * width
        y = 34 + (index // columns) * (height + label)
        image.thumbnail((width, height))
        sheet.paste(image, (x, y))
        draw.text((x + 4, y + height + 3), text, fill="black")
    sheet.save(output, quality=92)
    return {
        "path": str(output),
        "clip_count": len(segments),
        "sample_count": len(cells),
        "black_frames": [],
        "status": "PASS",
    }


def execute(spec_path: Path, dry_run_only: bool = False) -> dict[str, str]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or spec.get("task_id") != "TASK_024":
        raise ValueError("Expected TASK_024 JSON.")
    project = Path(str(spec["premiere_project"]["project_path"]))
    output_dir = project.parent / "TASK_024_OUTPUT"
    output_dir.mkdir(parents=True, exist_ok=True)
    segments = _resolved_segments()
    validate_timeline_segments(segments, expected_count=13, expected_frames=881)
    root = load_premiere_project_root(project)
    if find_project_sequence_node(root, OUTPUT_SEQUENCE) is not None:
        raise PremiereProjectError(f"BLOCKED: {OUTPUT_SEQUENCE} already exists.")
    input_video = _contexts(root, INPUT_SEQUENCE, project, 0)
    input_audio = _contexts(root, INPUT_SEQUENCE, project, 1)
    input_node = find_project_sequence_node(root, INPUT_SEQUENCE)
    assert input_node is not None
    ids = build_project_object_id_lookup(root)
    if (
        len(input_video) != 35
        or input_audio
        or _sequence_duration(input_video) // _frame_ticks(25) != 3071
        or _video_settings(input_node, ids)["frame_rect"] != "0,0,3840,2160"
        or _video_settings(input_node, ids)["frame_rate"] != str(_frame_ticks(25))
    ):
        raise PremiereProjectError("BLOCKED: accepted v05 contract failed.")
    uids = build_project_object_uid_lookup(root)
    source_xml: dict[str, bytes] = {}
    source_properties: dict[str, object] = {}
    for name in PROTECTED:
        node = find_project_sequence_node(root, name)
        if node is None:
            raise PremiereProjectError(f"BLOCKED: protected sequence {name!r} missing.")
        source_xml[name] = ET.tostring(node, encoding="utf-8")
        source_properties[name] = _sequence_property_snapshot(
            node, ids=ids, uids=uids, project_path=project, fps=25
        )
    resolved_path = output_dir / "TASK_024_RESOLVED_SELECTIONS.json"
    resolved_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_024",
                "selection_basis": "visual audit of accepted v05 preview",
                "semantic_resolutions": {
                    "BAR_MITZVAH_THREE_MEN": {
                        "source_sequence": "SF_26_BD_Family_1",
                        "source_range": [435, 495],
                        "visual_confirmation": "three adult men, first retained occurrence",
                    },
                    "GRANDCHILDREN_ONE_IMAGE": {
                        "source_sequence": "SF_26_BD_Family_1",
                        "source_range": [535, 595],
                        "visual_confirmation": "Sergey with one grandchild; not Ksenia or unrelated children",
                    },
                    "OPEN_SPACE": {
                        "source_sequence": "SF_26_BD_Keep_08",
                        "source_range": [3921, 3996],
                        "visual_confirmation": "open landscape with Sergey; no camels",
                    },
                },
                "segments": segments,
                "total_frames": 881,
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    dry_path = output_dir / "TASK_024_DRY_RUN.json"
    dry_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_024",
                "project_path": str(project),
                "project_sha256": _sha256(project),
                "input_sequence": INPUT_SEQUENCE,
                "output_sequence": OUTPUT_SEQUENCE,
                "segments": segments,
                "frames": 881,
                "video_clips": 13,
                "audio_clips": 0,
                "status": "PASS_READY_TO_EXECUTE",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if dry_run_only:
        return {"dry_run": str(dry_path), "resolved": str(resolved_path)}
    plan = _plan()
    assemble_target_sequence(
        plan,
        root=root,
        segments=segments,
        source_xml=source_xml,
        project_path=project,
    )
    temp = output_dir / "SF_26_BD_1_TASK024_VALIDATION.prproj"
    temp.write_bytes(
        gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    )
    verify_assembled_project(
        plan,
        project_path=temp,
        source_xml=source_xml,
        source_properties_before=source_properties,
        segments=segments,
    )
    project_hash = _sha256(project)
    backup = project.with_name(f"{project.stem}_before_TASK_024{project.suffix}")
    if backup.exists():
        raise PremiereProjectError(f"BLOCKED: backup exists: {backup}")
    shutil.copy2(project, backup)
    if _sha256(backup) != project_hash:
        raise PremiereProjectError("TASK_024 backup hash mismatch.")
    os.replace(temp, project)
    qa, actual_segments = verify_assembled_project(
        plan,
        project_path=project,
        source_xml=source_xml,
        source_properties_before=source_properties,
        segments=segments,
    )
    preview_path = output_dir / "SF_26_BD_SHORT_CORE_v01_640_360.mp4"
    preview = render_timeline_preview(
        plan, project_path=project, segments=actual_segments, output_path=preview_path
    )
    overview_path = output_dir / "TASK_024_SHORT_CORE_OVERVIEW_CONTACT_SHEET.jpg"
    overview = _overview(preview_path, actual_segments, overview_path)
    joins_path = output_dir / "TASK_024_SHORT_CORE_JOINS_CONTACT_SHEET.jpg"
    joins = build_join_contact_sheet(
        preview_path=preview_path,
        segments=actual_segments,
        output_path=joins_path,
        fps=25,
    )
    probe = build_ffprobe_payload(preview_path)
    probe_path = output_dir / "TASK_024_FFPROBE.json"
    probe_path.write_text(
        json.dumps(probe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    actual_path = output_dir / "TASK_024_TIMELINE_ACTUAL.json"
    actual_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_024",
                "source": "reopened_saved_prproj",
                "project_path": str(project),
                "project_sha256": _sha256(project),
                "backup_path": str(backup),
                "backup_sha256": _sha256(backup),
                "input_sequence": INPUT_SEQUENCE,
                "output_sequence": OUTPUT_SEQUENCE,
                "qa": qa,
                "segments": actual_segments,
                "preview": preview,
                "overview_contact_sheet": overview,
                "joins_contact_sheet": joins,
                "status": "STRUCTURAL_PASS_WAITING_MUZA_VISUAL_QA_AND_OPEN_CHECK",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    qa_path = output_dir / "TASK_024_QA.txt"
    qa_path.write_text(
        "\n".join(
            [
                "TASK_024 — SERGEY 76 MINIMAL SHORT CORE v01",
                "",
                "STATUS: STRUCTURAL_PASS_WAITING_MUZA_VISUAL_QA_AND_OPEN_CHECK",
                f"Input: {INPUT_SEQUENCE} — 3071 frames / 35 video clips / 0 audio",
                f"Output: {OUTPUT_SEQUENCE} — 881 frames / 35.24 seconds",
                "New empty sequence assembled in a new dramatic order: PASS",
                "13 exact video-only nested sequence clips: PASS",
                "Nuri -> car -> memory -> family -> road -> final walk: PASS",
                "Bar mitzvah and grandchild choices visually resolved: PASS",
                "OPEN_SPACE contains no camels: PASS",
                "Final clip is FINAL_WALK, not Nuri or LONG coda: PASS",
                "Protected v05 and source sequences unchanged: PASS",
                "Saved project reopened and reparsed: PASS",
                "Preview 640x360 / 25 fps / 881 frames / no audio stream: PASS",
                "12 joins, both sides, no black frames: PASS",
                "Premiere desktop open-check and Muza visual QA: REQUIRED",
                "",
                "TASK_024_DONE.txt was not created.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    waiting = output_dir / "TASK_024_WAITING_MUZA_QA.txt"
    waiting.write_text(
        "TASK_024 structural execution complete.\n"
        "Preview and TASK_024_TIMELINE_ACTUAL.json are ready.\n"
        "WAITING FOR MUZA VISUAL QA AND PREMIERE OPEN-CHECK.\n",
        encoding="utf-8",
    )
    return {
        "project": str(project),
        "backup": str(backup),
        "preview": str(preview_path),
        "resolved": str(resolved_path),
        "actual": str(actual_path),
        "overview": str(overview_path),
        "joins": str(joins_path),
        "ffprobe": str(probe_path),
        "qa": str(qa_path),
        "waiting": str(waiting),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute TASK_024 SHORT CORE.")
    parser.add_argument("spec", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(execute(args.spec, args.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
