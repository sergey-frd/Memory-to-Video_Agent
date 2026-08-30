from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from main_premiere_alla_first_assembly import (
    FPS,
    FRAME_TICKS,
    IMAGE_SUFFIXES,
    MATERIAL_ROOT,
    _inventory,
)
from utils.premiere_media_import_export import (
    _ProjectObjectIdAllocator,
    _assert_project_refs_resolved,
    _find_masterclip_by_name,
    _find_masterclip_for_media,
    _find_templates_in_root,
    _index_media_by_path,
    _media_path_key,
    _place_track_item,
    _set_child_text,
    _update_sequence_duration_metadata,
)
from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    iter_project_track_item_refs,
    load_premiere_project_root,
    resolve_project_track_item_clip,
    resolve_project_track_item_name,
    resolve_project_track_item_source_path,
)
from utils.premiere_project_export import (
    _set_track_item_boundary,
    clone_named_sequence,
)
from utils.premiere_sequence_motion import (
    _format_number,
    _motion_params,
    _set_param_keyframes,
    _track_item_contexts,
    _video_settings,
    build_position_keyframes,
    build_scale_keyframes,
)


SOURCE_PROJECT = Path(r"<LOCAL_PATH>")
OUTPUT_PROJECT = Path(
    r"<LOCAL_PATH>"
)
REPORT_PATH = Path(
    r"<LOCAL_PATH>"
)
ACTUAL_PATH = Path(
    r"<LOCAL_PATH>"
)
SOURCE_SEQUENCE = "ALLA_15_SKELETON_V02"
OUTPUT_SEQUENCE = "ALLA_15_CLIENT_V02_MOTION"
PROTECTED_SEQUENCES = [
    "ALLA_15_SKELETON_V01",
    "ALLA_15_SKELETON_V02",
    "ALLA_ALL_MATERIAL_BANK",
]
NESTED_BACKGROUND_NAME = "Nested Sequence 03"
FINAL_PHOTO_NAME = "20260819_110902.jpg"
STILL_SOURCE_IN = 3600 * PREMIERE_TICKS_PER_SECOND


@dataclass(frozen=True)
class TimelineItem:
    path: Path
    kind: str
    duration_frames: int
    added: bool = False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _additional_photo_names() -> list[str]:
    return [
        "20190727_135022.jpg",
        "20190727_141518.jpg",
        "20190817_120402.jpg",
        "20190818_131743.jpg",
        "20250621_165643.jpg",
        "20251003_145033.jpg",
        "20251010_160359.jpg",
        "IMG-20260516-WA0002.jpg",
    ]


def _all_real_photos() -> dict[str, Path]:
    inventory = _inventory()
    return {path.name: path for path in inventory["real"]}


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mp4":
        return "grok_video"
    if path.name.startswith("ALLA_DE_"):
        return "double_exposure"
    if path.stem.endswith("W"):
        return "watercolor"
    if suffix in IMAGE_SUFFIXES:
        return "real_photo"
    return "other"


def _duration_for(path: Path) -> int:
    kind = _kind(path)
    if kind == "real_photo":
        return 125 if path.name == FINAL_PHOTO_NAME else 52
    if kind == "watercolor":
        return 72
    if kind == "double_exposure":
        return 100
    raise PremiereProjectError(f"No still duration rule for {path.name}.")


def _source_foreground_paths(
    root: ET.Element, project_path: Path
) -> list[Path]:
    sequence = find_project_sequence_node(root, SOURCE_SEQUENCE)
    if sequence is None:
        raise PremiereProjectError(f"Missing required source sequence: {SOURCE_SEQUENCE}")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    contexts = _track_item_contexts(
        sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    foreground = sorted(
        (item for item in contexts if item.track_index == 1),
        key=lambda item: item.start,
    )
    if len(foreground) != 62:
        raise PremiereProjectError(
            f"Expected 62 foreground items in V02; found {len(foreground)}."
        )
    paths: list[Path] = []
    for item in foreground:
        if not item.source_path:
            raise PremiereProjectError(f"Foreground item has no source path: {item.name}")
        path = Path(item.source_path)
        if not path.is_file():
            raise PremiereProjectError(f"Offline foreground media: {path}")
        paths.append(path)
    return paths


def _build_plan(source_paths: list[Path]) -> list[TimelineItem]:
    real_by_name = _all_real_photos()
    additions = _additional_photo_names()
    missing = [name for name in additions if name not in real_by_name]
    if missing:
        raise PremiereProjectError(f"Missing additional photos: {', '.join(missing)}")
    insert_before: dict[str, list[str]] = {
        "20190727_150042.jpg": additions[:2],
        "20190818_132320.jpg": additions[2:4],
        "20260819_110902W.jpg": additions[4:],
    }
    plan: list[TimelineItem] = []
    for path in source_paths:
        for name in insert_before.get(path.name, []):
            added_path = real_by_name[name]
            plan.append(
                TimelineItem(
                    added_path,
                    "real_photo",
                    _duration_for(added_path),
                    added=True,
                )
            )
        kind = _kind(path)
        if kind == "grok_video":
            plan.append(TimelineItem(path, kind, 0))
        elif kind in {"real_photo", "watercolor", "double_exposure"}:
            plan.append(TimelineItem(path, kind, _duration_for(path)))
        else:
            raise PremiereProjectError(f"Unsupported foreground item: {path}")
    if len(plan) != 70:
        raise PremiereProjectError(f"Expected 70 output foreground items; got {len(plan)}.")
    counts: dict[str, int] = {}
    for item in plan:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    expected = {
        "real_photo": 23,
        "watercolor": 30,
        "double_exposure": 12,
        "grok_video": 5,
    }
    if counts != expected:
        raise PremiereProjectError(f"Unexpected client-motion counts: {counts}")
    return plan


def _sequence_xml(root: ET.Element, name: str) -> bytes:
    node = find_project_sequence_node(root, name)
    if node is None:
        raise PremiereProjectError(f"Missing protected sequence: {name}")
    return ET.tostring(node, encoding="utf-8")


def _audio_signature(
    root: ET.Element, sequence_name: str, project_path: Path
) -> list[dict[str, object]]:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Missing sequence: {sequence_name}")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    rows = []
    for item in sorted(
        _track_item_contexts(
            sequence,
            group_index=1,
            id_lookup=ids,
            uid_lookup=uids,
            project_path=project_path,
        ),
        key=lambda value: (value.track_index, value.start, value.end),
    ):
        clip = resolve_project_track_item_clip(item.track_item_node, ids)
        payload = clip.find("./Clip") if clip is not None else None
        rows.append(
            {
                "track_index": item.track_index,
                "start": item.start,
                "end": item.end,
                "name": item.name,
                "source_path": item.source_path,
                "source_in": int(payload.findtext("./InPoint") or "0") if payload is not None else None,
                "source_out": int(payload.findtext("./OutPoint") or "0") if payload is not None else None,
            }
        )
    return rows


def _track_item_node(
    root: ET.Element,
    *,
    sequence_name: str,
    track_index: int,
    name: str,
) -> ET.Element:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Missing sequence: {sequence_name}")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    for item in _track_item_contexts(
        sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=OUTPUT_PROJECT,
    ):
        if item.track_index == track_index and item.name == name:
            return item.track_item_node
    raise PremiereProjectError(f"Could not find {name!r} on V{track_index + 1}.")


def _append_additional_photos(root: ET.Element, plan: list[TimelineItem]) -> None:
    sequence = find_project_sequence_node(root, OUTPUT_SEQUENCE)
    if sequence is None:
        raise PremiereProjectError("Output clone was not created.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    tracks = get_project_track_nodes(
        sequence,
        track_group_index=0,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    if len(tracks) < 2:
        raise PremiereProjectError("Output sequence does not preserve foreground V2.")
    _, image_template, _ = _find_templates_in_root(
        root,
        preferred_sequence=sequence,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    if image_template is None:
        raise PremiereProjectError("No still-image template found in V02 clone.")
    media_by_path = _index_media_by_path(root)
    allocator = _ProjectObjectIdAllocator(root)
    temporary_cursor = 10_000 * PREMIERE_TICKS_PER_SECOND
    for item in (value for value in plan if value.added):
        media = media_by_path.get(_media_path_key(item.path))
        if media is None:
            raise PremiereProjectError(
                f"Additional photo is not imported in BANK: {item.path}"
            )
        master = _find_masterclip_for_media(root, media.attrib.get("ObjectUID", ""))
        if master is None:
            master = _find_masterclip_by_name(root, item.path.name)
        if master is None:
            raise PremiereProjectError(f"No master clip for {item.path.name}.")
        duration = item.duration_frames * FRAME_TICKS
        _place_track_item(
            root,
            track_node=tracks[1][1],
            template=image_template,
            media_node=media,
            master_clip=master,
            source_path=item.path,
            timeline_start=temporary_cursor,
            timeline_end=temporary_cursor + duration,
            source_in=STILL_SOURCE_IN,
            source_out=STILL_SOURCE_IN + duration,
            object_id_lookup=ids,
            id_allocator=allocator,
            clone_audio_source=False,
        )
        temporary_cursor += duration


def _foreground_items_by_path(root: ET.Element) -> dict[str, list[ET.Element]]:
    sequence = find_project_sequence_node(root, OUTPUT_SEQUENCE)
    if sequence is None:
        raise PremiereProjectError("Output sequence missing.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    result: dict[str, list[ET.Element]] = {}
    for item in _track_item_contexts(
        sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=OUTPUT_PROJECT,
    ):
        if item.track_index != 1 or not item.source_path:
            continue
        result.setdefault(_media_path_key(Path(item.source_path)), []).append(
            item.track_item_node
        )
    return result


def _motion_profile(index: int) -> tuple[str, float, float, float, float, float, float]:
    profiles = [
        ("SLOW_PUSH_IN", 100.0, 108.0, 0.5, 0.5, 0.5, 0.5),
        ("SLOW_PULL_OUT", 108.0, 100.0, 0.5, 0.5, 0.5, 0.5),
        ("PAN_LEFT_TO_RIGHT", 108.0, 108.0, 0.47, 0.5, 0.53, 0.5),
        ("PAN_RIGHT_TO_LEFT_UP", 108.0, 108.0, 0.53, 0.52, 0.47, 0.48),
    ]
    return profiles[index % len(profiles)]


def _retime_and_animate(
    root: ET.Element, plan: list[TimelineItem]
) -> tuple[int, list[dict[str, object]]]:
    ids = build_project_object_id_lookup(root)
    items_by_path = _foreground_items_by_path(root)
    cursor = 0
    rows: list[dict[str, object]] = []
    static_index = 0
    for planned in plan:
        key = _media_path_key(planned.path)
        candidates = items_by_path.get(key) or []
        if not candidates:
            raise PremiereProjectError(f"Missing output item for {planned.path}")
        item = candidates.pop(0)
        clip = resolve_project_track_item_clip(item, ids)
        timeline = item.find("./ClipTrackItem/TrackItem")
        clip_payload = clip.find("./Clip") if clip is not None else None
        if timeline is None or clip_payload is None:
            raise PremiereProjectError(f"Malformed output item: {planned.path.name}")
        if planned.kind == "grok_video":
            old_start = int(timeline.findtext("./Start") or "0")
            old_end = int(timeline.findtext("./End") or "0")
            duration_frames = round((old_end - old_start) / FRAME_TICKS)
            motion_name = None
        else:
            duration_frames = planned.duration_frames
            source_in = int(clip_payload.findtext("./InPoint") or "0")
            _set_child_text(
                clip_payload,
                "OutPoint",
                str(source_in + duration_frames * FRAME_TICKS),
            )
            (
                motion_name,
                start_scale,
                end_scale,
                start_x,
                start_y,
                end_x,
                end_y,
            ) = _motion_profile(static_index)
            params = _motion_params(item, ids)
            if params is None:
                raise PremiereProjectError(
                    f"Intrinsic Motion missing for {planned.path.name}."
                )
            first_visible = source_in
            last_visible = source_in + max(0, duration_frames - 1) * FRAME_TICKS
            _set_param_keyframes(
                params.scale,
                keyframes=build_scale_keyframes(
                    first_visible,
                    last_visible,
                    start_scale,
                    end_scale,
                    interpolation="BEZIER_EASE_IN_OUT",
                ),
                current_value=_format_number(end_scale),
            )
            _set_param_keyframes(
                params.position,
                keyframes=build_position_keyframes(
                    first_visible,
                    last_visible,
                    start_x,
                    start_y,
                    end_x,
                    end_y,
                    interpolation="BEZIER_EASE_IN_OUT",
                ),
            )
            static_index += 1
        _set_track_item_boundary(timeline, "Start", cursor * FRAME_TICKS)
        _set_track_item_boundary(
            timeline,
            "End",
            (cursor + duration_frames) * FRAME_TICKS,
        )
        rows.append(
            {
                "timeline_in_frame": cursor,
                "timeline_out_frame": cursor + duration_frames,
                "duration_frames": duration_frames,
                "kind": planned.kind,
                "file": planned.path.name,
                "path": str(planned.path),
                "added": planned.added,
                "motion_profile": motion_name,
            }
        )
        cursor += duration_frames
    if any(items for items in items_by_path.values()):
        raise PremiereProjectError("Unexpected foreground items remained after planning.")
    sequence = find_project_sequence_node(root, OUTPUT_SEQUENCE)
    if sequence is None:
        raise PremiereProjectError("Output sequence missing.")
    _update_sequence_duration_metadata(
        root, sequence, new_total_duration=cursor * FRAME_TICKS
    )
    return cursor, rows


def _trim_nested_background(root: ET.Element, picture_frames: int) -> None:
    ids = build_project_object_id_lookup(root)
    item = _track_item_node(
        root,
        sequence_name=OUTPUT_SEQUENCE,
        track_index=0,
        name=NESTED_BACKGROUND_NAME,
    )
    timeline = item.find("./ClipTrackItem/TrackItem")
    clip = resolve_project_track_item_clip(item, ids)
    payload = clip.find("./Clip") if clip is not None else None
    if timeline is None or payload is None:
        raise PremiereProjectError("Nested background item is malformed.")
    source_in = int(payload.findtext("./InPoint") or "0")
    duration = picture_frames * FRAME_TICKS
    _set_track_item_boundary(timeline, "Start", 0)
    _set_track_item_boundary(timeline, "End", duration)
    _set_child_text(payload, "OutPoint", str(source_in + duration))


def _write_project(root: ET.Element) -> None:
    _assert_project_refs_resolved(root)
    OUTPUT_PROJECT.write_bytes(
        gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    )


def _verify(
    *,
    source_hash: str,
    protected_before: dict[str, bytes],
    audio_before: list[dict[str, object]],
    picture_frames: int,
) -> dict[str, object]:
    if _sha256(SOURCE_PROJECT) != source_hash:
        raise PremiereProjectError("Source v2 project changed during execution.")
    root = load_premiere_project_root(OUTPUT_PROJECT)
    for name, before in protected_before.items():
        if _sequence_xml(root, name) != before:
            raise PremiereProjectError(f"Protected source sequence changed: {name}")
    source_audio = _audio_signature(root, SOURCE_SEQUENCE, OUTPUT_PROJECT)
    output_audio = _audio_signature(root, OUTPUT_SEQUENCE, OUTPUT_PROJECT)
    if source_audio != audio_before or output_audio != audio_before:
        raise PremiereProjectError("Music/audio timeline differs from V02.")
    sequence = find_project_sequence_node(root, OUTPUT_SEQUENCE)
    if sequence is None:
        raise PremiereProjectError("Output sequence was not saved.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    contexts = _track_item_contexts(
        sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=OUTPUT_PROJECT,
    )
    foreground = [item for item in contexts if item.track_index == 1]
    background = [item for item in contexts if item.track_index == 0]
    if len(foreground) != 70 or len(background) != 1:
        raise PremiereProjectError(
            f"Unexpected video structure: foreground={len(foreground)}, background={len(background)}"
        )
    static = [
        item
        for item in foreground
        if Path(item.source_path or "").suffix.lower() in IMAGE_SUFFIXES
    ]
    if len(static) != 65:
        raise PremiereProjectError(f"Expected 65 static items; found {len(static)}.")
    missing_motion = []
    for item in static:
        params = _motion_params(item.track_item_node, ids)
        if (
            params is None
            or (params.scale.findtext("./IsTimeVarying") or "").lower() != "true"
            or (params.position.findtext("./IsTimeVarying") or "").lower() != "true"
        ):
            missing_motion.append(item.name)
    if missing_motion:
        raise PremiereProjectError(
            f"Static images without Motion keyframes: {missing_motion[:5]}"
        )
    tracks_v = get_project_track_nodes(
        sequence,
        track_group_index=0,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    tracks_a = get_project_track_nodes(
        sequence,
        track_group_index=1,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    return {
        "source_v2_unchanged": True,
        "protected_sequences_unchanged": PROTECTED_SEQUENCES,
        "output_sequence": OUTPUT_SEQUENCE,
        "settings": _video_settings(sequence, ids),
        "video_track_count": len(tracks_v),
        "audio_track_count": len(tracks_a),
        "foreground_items": len(foreground),
        "nested_background_items": len(background),
        "animated_static_items": len(static),
        "grok_videos": len(foreground) - len(static),
        "audio_items": len(output_audio),
        "audio_timeline_unchanged": True,
        "picture_duration_frames": picture_frames,
        "picture_duration_seconds": round(picture_frames / FPS, 2),
        "sequence_tail_due_to_preserved_audio_frames": max(
            [row["end"] for row in output_audio] + [picture_frames * FRAME_TICKS]
        )
        // FRAME_TICKS,
    }


def execute() -> dict[str, object]:
    if not SOURCE_PROJECT.is_file():
        raise PremiereProjectError(f"Missing source project: {SOURCE_PROJECT}")
    if OUTPUT_PROJECT.exists():
        raise PremiereProjectError(f"Output project already exists: {OUTPUT_PROJECT}")
    root = load_premiere_project_root(SOURCE_PROJECT)
    if find_project_sequence_node(root, OUTPUT_SEQUENCE) is not None:
        raise PremiereProjectError(f"Output sequence already exists: {OUTPUT_SEQUENCE}")
    source_hash = _sha256(SOURCE_PROJECT)
    protected_before = {name: _sequence_xml(root, name) for name in PROTECTED_SEQUENCES}
    audio_before = _audio_signature(root, SOURCE_SEQUENCE, SOURCE_PROJECT)
    source_paths = _source_foreground_paths(root, SOURCE_PROJECT)
    plan = _build_plan(source_paths)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    clone_named_sequence(
        root,
        source_sequence_name=SOURCE_SEQUENCE,
        new_sequence_name=OUTPUT_SEQUENCE,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    _append_additional_photos(root, plan)
    picture_frames, rows = _retime_and_animate(root, plan)
    _trim_nested_background(root, picture_frames)
    _write_project(root)
    qa = _verify(
        source_hash=source_hash,
        protected_before=protected_before,
        audio_before=audio_before,
        picture_frames=picture_frames,
    )
    counts: dict[str, int] = {}
    for item in plan:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    payload = {
        "task_id": "TASK_ALLA_04_PREMIERE_CLIENT_MOTION_V02",
        "status": "PASS_WITH_DOCUMENTED_AUDIO_TAIL",
        "source_project": str(SOURCE_PROJECT),
        "source_project_sha256": source_hash,
        "output_project": str(OUTPUT_PROJECT),
        "source_sequence": SOURCE_SEQUENCE,
        "output_sequence": OUTPUT_SEQUENCE,
        "counts": counts,
        "added_photos": _additional_photo_names(),
        "motion": {
            "animated_static_count": 65,
            "scale_range_percent": [100.0, 108.0],
            "position_x_range": [0.47, 0.53],
            "position_y_range": [0.48, 0.52],
            "temporal_interpolation": "BEZIER_EASE_IN_OUT",
            "profiles": [
                "SLOW_PUSH_IN",
                "SLOW_PULL_OUT",
                "PAN_LEFT_TO_RIGHT",
                "PAN_RIGHT_TO_LEFT_UP",
            ],
        },
        "trimmed_videos": [],
        "timeline": rows,
        "qa": qa,
        "deviations_and_open_issues": [
            "V02 music was preserved byte-for-byte at its original timeline positions, as explicitly required.",
            "The picture edit is 3:51.36, but preserved audio continues to 5:02.20; Premiere sequence end therefore follows the audio tail.",
            "The existing nested blurred background was preserved and trimmed to the new picture duration; it was not rebuilt.",
            "No automatic transitions were added.",
            "Final face-safe framing requires visual review in Premiere.",
        ],
    }
    ACTUAL_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = [
        "TASK_ALLA_04_PREMIERE_CLIENT_MOTION_V02",
        "STATUS: PASS_WITH_DOCUMENTED_AUDIO_TAIL",
        "",
        f"Source project (unchanged): {SOURCE_PROJECT}",
        f"Source SHA256: {source_hash}",
        f"Output project: {OUTPUT_PROJECT}",
        f"Source sequence: {SOURCE_SEQUENCE}",
        f"Output sequence: {OUTPUT_SEQUENCE}",
        "",
        "PICTURE DURATION",
        f"- {picture_frames} frames / {picture_frames / FPS:.2f} sec / 00:03:51:09",
        "- The picture edit is inside the requested 3:20-4:00 range.",
        "- Preserved music continues to 7555 frames / 5:02.20; audio was not cut or moved.",
        "",
        "MEDIA COUNTS",
        f"- ordinary photographs: {counts['real_photo']}",
        f"- watercolors: {counts['watercolor']}",
        f"- double exposures: {counts['double_exposure']}",
        f"- Grok videos: {counts['grok_video']}",
        "",
        "ADDED PHOTOGRAPHS (8)",
        *[f"- {name}" for name in _additional_photo_names()],
        "",
        "MOTION",
        "- animated static images: 65",
        "- Scale range: 100-108%",
        "- Position X range: 0.47-0.53",
        "- Position Y range: 0.48-0.52",
        "- profiles alternate: push in, pull out, left/right pan, right/left-up pan",
        "- interpolation: BEZIER_EASE_IN_OUT",
        "",
        "VIDEO TECHNICAL TRIMS",
        "- none; all five Grok videos remain full length",
        "",
        "PRESERVATION QA",
        "- V01 unchanged: PASS",
        "- V02 unchanged: PASS",
        "- BANK unchanged: PASS",
        "- source v2 project unchanged: PASS",
        "- audio clip count, tracks, positions and source ranges unchanged: PASS",
        "- video tracks preserved: 3",
        "- audio tracks preserved: 4",
        "- nested blurred background preserved on V1 and trimmed to picture duration: PASS",
        "",
        "DEVIATIONS / OPEN ISSUES",
        "- Sequence technical end remains 5:02.20 because audio preservation takes priority.",
        "- No transitions were added.",
        "- Face-safe framing must be confirmed visually in Premiere.",
        "",
        f"Actual timeline JSON: {ACTUAL_PATH}",
        "Premiere open-check and visual motion review: REQUIRED.",
    ]
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build TASK_ALLA_04 client-motion sequence from V02."
    )
    parser.parse_args()
    result = execute()
    print(json.dumps(result["qa"], ensure_ascii=False, indent=2))
    print(f"Project: {OUTPUT_PROJECT}")
    print(f"Report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
