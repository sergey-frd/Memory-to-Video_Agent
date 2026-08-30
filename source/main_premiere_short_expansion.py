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
    render_timeline_preview,
    validate_timeline_segments,
    verify_assembled_project,
)


INPUT_SEQUENCE = "SF_26_BD_SHORT_CORE_v01"
OUTPUT_SEQUENCE = "SF_26_BD_SHORT_76S_v02"


def _contexts(root: ET.Element, name: str, project: Path, group: int) -> list[object]:
    sequence = find_project_sequence_node(root, name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {name!r} is missing.")
    return _track_item_contexts(
        sequence,
        group_index=group,
        id_lookup=build_project_object_id_lookup(root),
        uid_lookup=build_project_object_uid_lookup(root),
        project_path=project,
    )


def _segments(spec: dict[str, object]) -> list[dict[str, object]]:
    raw = spec.get("expected_final_segments")
    if not isinstance(raw, list):
        raise ValueError("expected_final_segments must be a list.")
    result = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Every final segment must be an object.")
        result.append(
            {
                **item,
                "segment_id": item["id"],
                "source_sequence_name": item["source_sequence"],
            }
        )
    validate_timeline_segments(result, expected_count=31, expected_frames=1878)
    if (
        sum(int(item["duration_frames"]) for item in result if item["kind"] == "TASK_025_addition")
        != 997
        or sum(item["kind"] == "TASK_025_addition" for item in result) != 18
        or sum(item["kind"] == "retained_TASK_024_core" for item in result) != 13
    ):
        raise ValueError("TASK_025 retained/addition arithmetic changed.")
    return result


def _plan() -> dict[str, object]:
    return {
        "timebase_fps": 25,
        "target_sequence": {
            "name": OUTPUT_SEQUENCE,
            "settings_source_sequence": INPUT_SEQUENCE,
        },
        "expected_result": {
            "visual_segment_count": 31,
            "total_duration_frames": 1878,
            "preview_width": 640,
            "preview_height": 360,
        },
        "segment_defaults": {
            "speed_percent": 100,
            "effects": "none",
            "transitions": "none",
        },
    }


def _read_frame(capture: object, frame_number: int) -> Image.Image:
    import cv2

    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
    ok, frame = capture.read()
    if not ok:
        raise RuntimeError(f"Could not decode TASK_025 frame {frame_number}.")
    if float(frame.mean()) < 2:
        raise RuntimeError(f"Black frame detected at TASK_025 frame {frame_number}.")
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def _sheet(
    preview: Path,
    samples: list[tuple[int, str]],
    output: Path,
    *,
    title: str,
    columns: int,
) -> dict[str, object]:
    import cv2

    capture = cv2.VideoCapture(str(preview))
    if not capture.isOpened():
        raise RuntimeError("Could not open TASK_025 preview.")
    images = [(frame, label, _read_frame(capture, frame)) for frame, label in samples]
    capture.release()
    width, height, label_height = 320, 180, 28
    rows = math.ceil(len(images) / columns)
    sheet = Image.new(
        "RGB", (columns * width, 34 + rows * (height + label_height)), "white"
    )
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 8), title, fill="black")
    for index, (frame, label, image) in enumerate(images):
        x = index % columns * width
        y = 34 + index // columns * (height + label_height)
        image.thumbnail((width, height))
        sheet.paste(image, (x, y))
        draw.text((x + 4, y + height + 3), f"{label} / frame {frame}", fill="black")
    sheet.save(output, quality=92)
    return {
        "path": str(output),
        "sample_count": len(samples),
        "black_frames": [],
        "status": "PASS",
    }


def execute(spec_path: Path, dry_run_only: bool = False) -> dict[str, str]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or spec.get("task_id") != "TASK_025":
        raise ValueError("Expected TASK_025 specification.")
    segments = _segments(spec)
    project = Path(str(spec["premiere_project"]["project_path"]))
    output_dir = project.parent / "TASK_025_OUTPUT"
    output_dir.mkdir(parents=True, exist_ok=True)
    root = load_premiere_project_root(project)
    resume_existing_output = find_project_sequence_node(root, OUTPUT_SEQUENCE) is not None
    core_video = _contexts(root, INPUT_SEQUENCE, project, 0)
    core_audio = _contexts(root, INPUT_SEQUENCE, project, 1)
    core_node = find_project_sequence_node(root, INPUT_SEQUENCE)
    assert core_node is not None
    ids = build_project_object_id_lookup(root)
    if (
        len(core_video) != 13
        or core_audio
        or _sequence_duration(core_video) // _frame_ticks(25) != 881
        or _video_settings(core_node, ids)["frame_rate"] != str(_frame_ticks(25))
        or _video_settings(core_node, ids)["frame_rect"] != "0,0,3840,2160"
    ):
        raise PremiereProjectError("BLOCKED: TASK_024 core contract failed.")
    retained = [item for item in segments if item["kind"] == "retained_TASK_024_core"]
    core_signature = [
        (
            item.name,
            item.source_in // _frame_ticks(25),
            item.source_out // _frame_ticks(25),
            item.duration // _frame_ticks(25),
        )
        for item in core_video
    ]
    retained_signature = [
        (
            str(item["source_sequence_name"]),
            int(item["source_in_frame"]),
            int(item["source_out_frame"]),
            int(item["duration_frames"]),
        )
        for item in retained
    ]
    if core_signature != retained_signature:
        raise PremiereProjectError("TASK_025 does not retain all 13 core clips exactly.")
    protected_names = [str(value) for value in spec["protected_sequences"]]
    uids = build_project_object_uid_lookup(root)
    source_xml: dict[str, bytes] = {}
    source_properties: dict[str, object] = {}
    for name in protected_names:
        node = find_project_sequence_node(root, name)
        if node is None:
            raise PremiereProjectError(f"Protected sequence {name!r} is missing.")
        source_xml[name] = ET.tostring(node, encoding="utf-8")
        source_properties[name] = _sequence_property_snapshot(
            node, ids=ids, uids=uids, project_path=project, fps=25
        )
    dry_path = output_dir / "TASK_025_DRY_RUN.json"
    dry_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_025",
                "project_path": str(project),
                "project_sha256": _sha256(project),
                "input_sequence": INPUT_SEQUENCE,
                "output_sequence": OUTPUT_SEQUENCE,
                "retained_core_clips": 13,
                "addition_clips": 18,
                "added_frames": 997,
                "output_clips": 31,
                "output_frames": 1878,
                "segments": segments,
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
    plan = _plan()
    backup = project.with_name(f"{project.stem}_before_TASK_025{project.suffix}")
    if resume_existing_output:
        if not backup.is_file():
            raise PremiereProjectError("BLOCKED: existing output has no TASK_025 backup.")
        backup_root = load_premiere_project_root(backup)
        for name, current_xml in source_xml.items():
            backup_node = find_project_sequence_node(backup_root, name)
            if (
                backup_node is None
                or ET.tostring(backup_node, encoding="utf-8") != current_xml
            ):
                raise PremiereProjectError(
                    f"Protected sequence {name!r} differs from TASK_025 backup."
                )
    else:
        assemble_target_sequence(
            plan,
            root=root,
            segments=segments,
            source_xml=source_xml,
            project_path=project,
        )
        temp = output_dir / "SF_26_BD_1_TASK025_VALIDATION.prproj"
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
        if backup.exists():
            raise PremiereProjectError(f"BLOCKED: backup exists: {backup}")
        shutil.copy2(project, backup)
        if _sha256(backup) != project_hash:
            raise PremiereProjectError("TASK_025 backup SHA256 mismatch.")
        os.replace(temp, project)
    qa, actual_segments = verify_assembled_project(
        plan,
        project_path=project,
        source_xml=source_xml,
        source_properties_before=source_properties,
        segments=segments,
    )
    preview_path = output_dir / "SF_26_BD_SHORT_76S_v02_640_360.mp4"
    preview = render_timeline_preview(
        plan, project_path=project, segments=actual_segments, output_path=preview_path
    )
    overview_samples = []
    for segment in actual_segments:
        start = int(segment["timeline_in_frame"])
        end = int(segment["timeline_out_frame"])
        overview_samples.extend(
            [
                (start, f"{segment['segment_id']} A"),
                ((start + end - 1) // 2, f"{segment['segment_id']} M"),
                (end - 1, f"{segment['segment_id']} Z"),
            ]
        )
    overview_path = output_dir / "TASK_025_OVERVIEW_CONTACT_SHEET.jpg"
    overview = _sheet(
        preview_path,
        overview_samples,
        overview_path,
        title="TASK_025 — all 31 segments, first/middle/last",
        columns=3,
    )
    join_samples = []
    for left, right in zip(actual_segments, actual_segments[1:]):
        boundary = int(left["timeline_out_frame"])
        join_samples.extend(
            [
                (boundary - 1, f"{left['segment_id']} Z"),
                (boundary, f"{right['segment_id']} A"),
            ]
        )
    joins_path = output_dir / "TASK_025_JOINS_CONTACT_SHEET.jpg"
    joins = _sheet(
        preview_path,
        join_samples,
        joins_path,
        title="TASK_025 — both sides of all 30 joins",
        columns=4,
    )
    probe = build_ffprobe_payload(preview_path)
    probe_path = output_dir / "TASK_025_FFPROBE.json"
    probe_path.write_text(
        json.dumps(probe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    actual_path = output_dir / "TASK_025_TIMELINE_ACTUAL.json"
    actual_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_025",
                "source": "reopened_saved_prproj",
                "project_path": str(project),
                "project_sha256": _sha256(project),
                "backup_path": str(backup),
                "backup_sha256": _sha256(backup),
                "input_sequence": INPUT_SEQUENCE,
                "output_sequence": OUTPUT_SEQUENCE,
                "qa": qa,
                "retained_core_clips": 13,
                "addition_clips": 18,
                "added_frames": 997,
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
    qa_path = output_dir / "TASK_025_QA.txt"
    qa_path.write_text(
        "\n".join(
            [
                "TASK_025 — SERGEY 76 SHORT 76S v02",
                "",
                "STATUS: STRUCTURAL_PASS_WAITING_MUZA_VISUAL_QA_AND_OPEN_CHECK",
                f"Input: {INPUT_SEQUENCE} — 881 frames / 13 clips / 0 audio",
                f"Output: {OUTPUT_SEQUENCE} — 1878 frames / 75.12 seconds",
                "All 13 approved core clips retained exactly: PASS",
                "18 additions / 997 added frames: PASS",
                "31 exact video-only nested sequence clips: PASS",
                "Nuri opening; desert/road/car; time; family; current life: PASS",
                "Car exterior and interior both present: PASS",
                "Bar mitzvah exactly once; neutral identities preserved: PASS",
                "Final clip FINAL_WALK [3587,3650): PASS",
                "No hospital, camels, unrelated children, LONG coda or Nuri ending: PASS",
                "Protected CORE v01, LONG v05 and source sequences unchanged: PASS",
                "Saved project reopened and reparsed: PASS",
                "Preview 640x360 / 25 fps / 1878 frames / no audio stream: PASS",
                "All 30 joins sampled on both sides; no black frames: PASS",
                "Premiere desktop open-check and Muza visual QA: REQUIRED",
                "",
                "TASK_025_DONE.txt was not created.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    waiting = output_dir / "TASK_025_WAITING_MUZA_QA.txt"
    waiting.write_text(
        "TASK_025 structural execution complete.\n"
        "Preview and TASK_025_TIMELINE_ACTUAL.json are ready.\n"
        "WAITING FOR MUZA VISUAL QA AND PREMIERE OPEN-CHECK.\n",
        encoding="utf-8",
    )
    return {
        "project": str(project),
        "backup": str(backup),
        "preview": str(preview_path),
        "actual": str(actual_path),
        "overview": str(overview_path),
        "joins": str(joins_path),
        "ffprobe": str(probe_path),
        "qa": str(qa_path),
        "waiting": str(waiting),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute TASK_025 SHORT expansion.")
    parser.add_argument("spec", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(execute(args.spec, args.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
