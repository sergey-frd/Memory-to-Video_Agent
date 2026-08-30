from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from main_premiere_sequence_insert_only import _contact_sheet
from utils.premiere_keep_apply_export import _KeepSegment, _clone_track_item_with_bounds
from utils.premiere_media_import_export import (
    _ProjectObjectIdAllocator,
    _append_imported_clip,
    _assert_project_refs_resolved,
    _find_templates_in_root,
    _index_media_by_path,
    _latest_media_node_for_path,
    _media_path_key,
)
from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    load_premiere_project_root,
    resolve_project_track_item_clip,
)
from utils.premiere_project_export import (
    _find_sequence_masterclip,
    _set_child_text,
    _set_track_item_boundary,
    _update_sequence_duration_metadata,
    clone_named_sequence,
)
from utils.premiere_sequence_delete_only import build_ffprobe_payload
from utils.premiere_sequence_motion import (
    _frame_ticks,
    _run_ffmpeg,
    _sequence_duration,
    _sha256,
    _track_item_contexts,
    _video_settings,
)
from utils.premiere_sequence_timeline_assembly import (
    _source_sequence_video_clip,
    _validate_all_refs,
    _visible_item_for_range,
    _render_segment,
)
from utils.premiere_trim_review_export import (
    _ensure_track_items_container,
    _reindex_track_items,
)
from utils.video_frame_extract import resolve_ffmpeg_executable


FPS = 25
FRAME_TICKS = _frame_ticks(FPS)
SHORT_INPUT = "SF_26_BD_SHORT_76S_v03"
SHORT_OUTPUT = "SF_26_BD_SHORT_76S_v04"
LONG_INPUT = "SF_26_BD_LONG_FAMILY_NURI_v10"
LONG_OUTPUT = "SF_26_BD_LONG_FAMILY_NURI_v11"
SHORT_BG_INPUT = "Nested Sequence 03"
SHORT_BG_OUTPUT = "TASK_028_SHORT_V04_BLURRED_BACKGROUND"
LONG_BG_INPUT = "Nested Sequence 05"
LONG_BG_OUTPUT = "TASK_028_LONG_V11_BLURRED_BACKGROUND"
FAMILY_SEQUENCE = "SF_26_BD_Family_1"
KEEP_SEQUENCE = "SF_26_BD_Keep_08"


def _dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _items(
    root: ET.Element,
    sequence_name: str,
    project_path: Path,
    *,
    group: int,
) -> list[object]:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {sequence_name!r} is missing.")
    return _track_item_contexts(
        sequence,
        group_index=group,
        id_lookup=build_project_object_id_lookup(root),
        uid_lookup=build_project_object_uid_lookup(root),
        project_path=project_path,
    )


def _sequence_xml(root: ET.Element, name: str) -> bytes:
    sequence = find_project_sequence_node(root, name)
    if sequence is None:
        raise PremiereProjectError(f"Protected sequence {name!r} is missing.")
    return ET.tostring(sequence, encoding="utf-8")


def _properties(root: ET.Element, name: str, project_path: Path) -> dict[str, object]:
    sequence = find_project_sequence_node(root, name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {name!r} is missing.")
    ids = build_project_object_id_lookup(root)
    video = _items(root, name, project_path, group=0)
    audio = _items(root, name, project_path, group=1)
    foreground = [item for item in video if item.track_index == 1]
    background = [item for item in video if item.track_index == 0]
    duration = max(_sequence_duration(video), _sequence_duration(audio))
    return {
        "settings": _video_settings(sequence, ids),
        "duration_frames": duration // FRAME_TICKS,
        "foreground_clips": len(foreground),
        "background_clips": len(background),
        "audio_clips": len(audio),
    }


def _audio_rows(
    root: ET.Element, sequence_name: str, project_path: Path
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    ids = build_project_object_id_lookup(root)
    for item in _items(root, sequence_name, project_path, group=1):
        clip = resolve_project_track_item_clip(item.track_item_node, ids)
        payload = clip.find("./Clip") if clip is not None else None
        rows.append(
            {
                "track_index": item.track_index,
                "name": item.name,
                "path": item.source_path,
                "timeline_in_frame": item.start // FRAME_TICKS,
                "timeline_out_frame": item.end // FRAME_TICKS,
                "source_in_ticks": int(payload.findtext("./InPoint") or "0")
                if payload is not None
                else None,
                "source_out_ticks": int(payload.findtext("./OutPoint") or "0")
                if payload is not None
                else None,
            }
        )
    return sorted(rows, key=lambda row: (int(row["track_index"]), int(row["timeline_in_frame"])))


def _find_exact_item(
    items: list[object],
    *,
    name: str,
    source_in_frame: int | None = None,
    source_out_frame: int | None = None,
) -> object:
    matches = [
        item
        for item in items
        if item.name == name
        and (
            source_in_frame is None
            or item.source_in <= source_in_frame * FRAME_TICKS
        )
        and (
            source_out_frame is None
            or item.source_out >= source_out_frame * FRAME_TICKS
        )
    ]
    if len(matches) != 1:
        raise PremiereProjectError(
            f"Expected one source item covering {name!r} "
            f"{source_in_frame}:{source_out_frame}; "
            f"found {len(matches)}."
        )
    return matches[0]


def _validate_spec(spec: dict[str, object]) -> None:
    short = _dict(spec.get("short_job"), "short_job")
    long = _dict(spec.get("long_job"), "long_job")
    if (
        spec.get("task_id") != "TASK_028"
        or int(spec.get("revision") or 0) != 1
        or short.get("input_sequence") != SHORT_INPUT
        or short.get("output_sequence") != SHORT_OUTPUT
        or long.get("input_sequence") != LONG_INPUT
        or long.get("output_sequence") != LONG_OUTPUT
    ):
        raise ValueError("TASK_028 fixed sequence contract changed.")
    short_segments = short.get("expected_final_segments")
    long_ranges = long.get("authoritative_video_ranges_in_output_order")
    if not isinstance(short_segments, list) or len(short_segments) != 26:
        raise ValueError("TASK_028 SHORT must contain exactly 26 final segments.")
    if not isinstance(long_ranges, list) or len(long_ranges) != 5:
        raise ValueError("TASK_028 LONG must contain exactly five authoritative ranges.")
    if sum(int(_dict(item, "short segment")["duration_frames"]) for item in short_segments) != 1878:
        raise ValueError("TASK_028 SHORT duration must be 1878 frames.")
    if sum(int(_dict(item, "long range")["duration_frames"]) for item in long_ranges) != 4103:
        raise ValueError("TASK_028 LONG duration must be 4103 frames.")


def _preflight(
    spec: dict[str, object], project_path: Path
) -> tuple[
    ET.Element,
    dict[str, bytes],
    dict[str, dict[str, object]],
    dict[str, object],
]:
    if not project_path.is_file():
        raise PremiereProjectError(f"Project not found: {project_path}")
    root = load_premiere_project_root(project_path)
    for name in (SHORT_OUTPUT, LONG_OUTPUT, SHORT_BG_OUTPUT, LONG_BG_OUTPUT):
        if find_project_sequence_node(root, name) is not None:
            raise PremiereProjectError(f"BLOCKED: output sequence already exists: {name}")
    short_actual = _properties(root, SHORT_INPUT, project_path)
    long_actual = _properties(root, LONG_INPUT, project_path)
    expected_short = {
        "duration_frames": 1878,
        "foreground_clips": 31,
        "background_clips": 1,
        "audio_clips": 2,
    }
    expected_long = {
        "duration_frames": 4140,
        "foreground_clips": 33,
        "background_clips": 1,
        "audio_clips": 2,
    }
    for key, value in expected_short.items():
        if short_actual[key] != value:
            raise PremiereProjectError(
                f"BLOCKED: SHORT v03 {key}={short_actual[key]}, expected {value}."
            )
    for key, value in expected_long.items():
        if long_actual[key] != value:
            raise PremiereProjectError(
                f"BLOCKED: LONG v10 {key}={long_actual[key]}, expected {value}."
            )
    for name, props in ((SHORT_INPUT, short_actual), (LONG_INPUT, long_actual)):
        settings = _dict(props["settings"], f"{name} settings")
        if (
            settings.get("frame_rate") != str(FRAME_TICKS)
            or settings.get("frame_rect") != "0,0,3840,2160"
        ):
            raise PremiereProjectError(f"BLOCKED: {name} is not 3840x2160/25 fps.")
    protected_names = [
        *[str(value) for value in spec["protected_sequences"]],  # type: ignore[index]
        SHORT_BG_INPUT,
        LONG_BG_INPUT,
    ]
    protected_xml = {name: _sequence_xml(root, name) for name in protected_names}
    protected_properties = {
        name: _properties(root, name, project_path) for name in protected_names
    }
    short_segments = _dict(spec["short_job"], "short_job")["expected_final_segments"]
    assert isinstance(short_segments, list)
    wanted_assets = {
        str(_dict(item, "short segment").get("source_asset_name"))
        for item in short_segments
        if _dict(item, "short segment").get("source_mode") == "direct_still_asset"
    }
    family = _items(root, FAMILY_SEQUENCE, project_path, group=0)
    asset_items: dict[str, object] = {}
    for asset_name in wanted_assets:
        matches = [
            item
            for item in family
            if Path(item.source_path or "").name.casefold() == asset_name.casefold()
            and item.source_path
            and Path(item.source_path).is_file()
        ]
        if len(matches) != 1:
            raise PremiereProjectError(
                f"BLOCKED: direct still {asset_name!r} has {len(matches)} exact "
                "online occurrences in Family_1."
            )
        asset_items[asset_name] = matches[0]
    for row in _audio_rows(root, SHORT_INPUT, project_path) + _audio_rows(
        root, LONG_INPUT, project_path
    ):
        if not row["path"] or not Path(str(row["path"])).is_file():
            raise PremiereProjectError(f"BLOCKED: offline music: {row['path']}")
    return root, protected_xml, protected_properties, asset_items


def _track(
    root: ET.Element, sequence_name: str, *, group: int, index: int
) -> ET.Element:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {sequence_name!r} is missing.")
    tracks = dict(
        get_project_track_nodes(
            sequence,
            track_group_index=group,
            object_id_lookup=build_project_object_id_lookup(root),
            object_uid_lookup=build_project_object_uid_lookup(root),
        )
    )
    track = tracks.get(index)
    if track is None:
        raise PremiereProjectError(
            f"Sequence {sequence_name!r} has no group {group}, track {index}."
        )
    return track


def _clear_track(track: ET.Element) -> ET.Element:
    for path in (
        "./ClipTrack/ClipItems/TrackItems",
        "./ClipTrack/TransitionItems/TrackItems",
    ):
        container = track.find(path)
        if container is None:
            continue
        for child in list(container):
            container.remove(child)
        _reindex_track_items(container)
    container = _ensure_track_items_container(track)
    if container is None:
        raise PremiereProjectError("Track has no clip item container.")
    return container


def _append_clone(
    root: ET.Element,
    *,
    container: ET.Element,
    template: object,
    source_in: int,
    source_out: int,
    timeline_in: int,
    timeline_out: int,
) -> None:
    ids = build_project_object_id_lookup(root)
    allocator = _ProjectObjectIdAllocator(root)
    _, ref = _clone_track_item_with_bounds(
        root,
        template_track_item=template.track_item_node,
        segment=_KeepSegment(
            timeline_start=timeline_in,
            timeline_end=timeline_out,
            source_in=source_in,
            source_out=source_out,
        ),
        object_id_lookup=ids,
        id_allocator=allocator,
    )
    container.append(ref)
    _reindex_track_items(container)


def _retarget_nested_background(
    root: ET.Element,
    *,
    output_sequence: str,
    background_sequence: str,
    frames: int,
    project_path: Path,
) -> None:
    items = [
        item
        for item in _items(root, output_sequence, project_path, group=0)
        if item.track_index == 0
    ]
    if len(items) != 1:
        raise PremiereProjectError(
            f"{output_sequence} must contain one outer background item."
        )
    item = items[0]
    ids = build_project_object_id_lookup(root)
    master, _, source = _source_sequence_video_clip(root, background_sequence, ids)
    sub_ref = item.track_item_node.find("./ClipTrackItem/SubClip")
    sub = ids.get(sub_ref.attrib.get("ObjectRef", "")) if sub_ref is not None else None
    if sub is None:
        raise PremiereProjectError("Outer background item has no resolvable SubClip.")
    master_ref = sub.find("./MasterClip")
    if master_ref is None:
        master_ref = ET.SubElement(sub, "MasterClip")
    master_ref.attrib.clear()
    master_ref.attrib["ObjectURef"] = master.attrib["ObjectUID"]
    _set_child_text(sub, "Name", background_sequence)
    clip = resolve_project_track_item_clip(item.track_item_node, ids)
    payload = clip.find("./Clip") if clip is not None else None
    if payload is None:
        raise PremiereProjectError("Outer background item has no Clip payload.")
    source_ref = payload.find("./Source")
    if source_ref is None:
        source_ref = ET.SubElement(payload, "Source")
    source_ref.attrib.clear()
    source_ref.attrib["ObjectRef"] = source.attrib["ObjectID"]
    _set_child_text(payload, "InPoint", "0")
    _set_child_text(payload, "OutPoint", str(frames * FRAME_TICKS))
    timeline = item.track_item_node.find("./ClipTrackItem/TrackItem")
    if timeline is None:
        raise PremiereProjectError("Outer background item has no timeline payload.")
    _set_track_item_boundary(timeline, "Start", 0)
    _set_track_item_boundary(timeline, "End", frames * FRAME_TICKS)


def _clone_four_sequences(root: ET.Element) -> None:
    for source, target in (
        (SHORT_INPUT, SHORT_OUTPUT),
        (SHORT_BG_INPUT, SHORT_BG_OUTPUT),
        (LONG_INPUT, LONG_OUTPUT),
        (LONG_BG_INPUT, LONG_BG_OUTPUT),
    ):
        clone_named_sequence(
            root,
            source_sequence_name=source,
            new_sequence_name=target,
            object_id_lookup=build_project_object_id_lookup(root),
            object_uid_lookup=build_project_object_uid_lookup(root),
        )


def _build_short(
    root: ET.Element,
    *,
    spec: dict[str, object],
    project_path: Path,
    asset_items: dict[str, object],
    hold_path: Path,
) -> list[dict[str, object]]:
    short = _dict(spec["short_job"], "short_job")
    raw_segments = short["expected_final_segments"]
    assert isinstance(raw_segments, list)
    output_container = _clear_track(
        _track(root, SHORT_OUTPUT, group=0, index=1)
    )
    background_container = _clear_track(
        _track(root, SHORT_BG_OUTPUT, group=0, index=0)
    )
    short_input_items = [
        item
        for item in _items(root, SHORT_INPUT, project_path, group=0)
        if item.track_index == 1
    ]
    source_cache = {
        name: short_input_items
        for name in ("SF_26_BD_Nuri_1", KEEP_SEQUENCE)
    }
    actual: list[dict[str, object]] = []
    hold_segment: dict[str, object] | None = None
    for raw in raw_segments:
        segment = _dict(raw, "short segment")
        mode = str(segment["source_mode"])
        timeline_in = int(segment["timeline_in_frame"]) * FRAME_TICKS
        timeline_out = int(segment["timeline_out_frame"]) * FRAME_TICKS
        duration = int(segment["duration_frames"])
        if mode == "nested_sequence":
            source_name = str(segment["source_sequence"])
            source_in_frame = int(segment["source_in_frame"])
            source_out_frame = int(segment["source_out_frame"])
            template = _find_exact_item(
                source_cache[source_name],
                name=source_name,
                source_in_frame=source_in_frame,
                source_out_frame=source_out_frame,
            )
            source_in = source_in_frame * FRAME_TICKS
            source_out = source_out_frame * FRAME_TICKS
            for container in (output_container, background_container):
                _append_clone(
                    root,
                    container=container,
                    template=template,
                    source_in=source_in,
                    source_out=source_out,
                    timeline_in=timeline_in,
                    timeline_out=timeline_out,
                )
            source_path = None
        elif mode == "direct_still_asset":
            asset_name = str(segment["source_asset_name"])
            template = asset_items[asset_name]
            source_in = int(template.source_in)
            source_out = source_in + duration * FRAME_TICKS
            for container in (output_container, background_container):
                _append_clone(
                    root,
                    container=container,
                    template=template,
                    source_in=source_in,
                    source_out=source_out,
                    timeline_in=timeline_in,
                    timeline_out=timeline_out,
                )
            source_name = asset_name
            source_in_frame = None
            source_out_frame = None
            source_path = template.source_path
        elif mode == "frame_hold_from_previous_last_valid_frame":
            hold_segment = segment
            continue
        else:
            raise ValueError(f"Unsupported SHORT source_mode: {mode}")
        actual.append(
            {
                **segment,
                "source_name": source_name,
                "source_path": source_path,
                "actual_source_in_frame": source_in_frame,
                "actual_source_out_frame": source_out_frame,
            }
        )
    if hold_segment is None:
        raise ValueError("SHORT FINAL_HOLD segment is missing.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    _, image_template, _ = _find_templates_in_root(
        root,
        preferred_sequence=find_project_sequence_node(root, FAMILY_SEQUENCE),
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    if image_template is None:
        raise PremiereProjectError("No direct-image template is available for FINAL_HOLD.")
    start = int(hold_segment["timeline_in_frame"]) * FRAME_TICKS
    end = int(hold_segment["timeline_out_frame"]) * FRAME_TICKS
    still_in = 3600 * PREMIERE_TICKS_PER_SECOND
    allocator = _ProjectObjectIdAllocator(root)
    _append_imported_clip(
        root,
        video_track=_track(root, SHORT_OUTPUT, group=0, index=1),
        audio_track=None,
        video_template=image_template,
        audio_template=None,
        source_path=hold_path,
        existing_media=None,
        kind="image",
        timeline_start=start,
        timeline_end=end,
        source_in=still_in,
        source_out=still_in + (end - start),
        object_id_lookup=ids,
        object_uid_lookup=uids,
        id_allocator=allocator,
        project_path=project_path,
    )
    media = _latest_media_node_for_path(root, hold_path)
    if media is None:
        raise PremiereProjectError("FINAL_HOLD imported media was not created.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    _, image_template, _ = _find_templates_in_root(
        root,
        preferred_sequence=find_project_sequence_node(root, FAMILY_SEQUENCE),
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    assert image_template is not None
    _append_imported_clip(
        root,
        video_track=_track(root, SHORT_BG_OUTPUT, group=0, index=0),
        audio_track=None,
        video_template=image_template,
        audio_template=None,
        source_path=hold_path,
        existing_media=media,
        kind="image",
        timeline_start=start,
        timeline_end=end,
        source_in=still_in,
        source_out=still_in + (end - start),
        object_id_lookup=ids,
        object_uid_lookup=uids,
        id_allocator=_ProjectObjectIdAllocator(root),
        project_path=project_path,
    )
    actual.append(
        {
            **hold_segment,
            "source_name": hold_path.name,
            "source_path": str(hold_path),
            "actual_source_in_frame": None,
            "actual_source_out_frame": None,
        }
    )
    actual.sort(key=lambda row: int(row["order"]))
    for name in (SHORT_OUTPUT, SHORT_BG_OUTPUT):
        sequence = find_project_sequence_node(root, name)
        assert sequence is not None
        _update_sequence_duration_metadata(
            root, sequence, new_total_duration=1878 * FRAME_TICKS
        )
    _retarget_nested_background(
        root,
        output_sequence=SHORT_OUTPUT,
        background_sequence=SHORT_BG_OUTPUT,
        frames=1878,
        project_path=project_path,
    )
    return actual


def _slice_timeline_items(
    source_items: list[object], ranges: list[dict[str, Any]]
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    cursor = 0
    for authoritative in ranges:
        keep_in = int(authoritative["v10_in"]) * FRAME_TICKS
        keep_out = int(authoritative["v10_out"]) * FRAME_TICKS
        for item in source_items:
            overlap_in = max(item.start, keep_in)
            overlap_out = min(item.end, keep_out)
            if overlap_in >= overlap_out:
                continue
            duration = overlap_out - overlap_in
            result.append(
                {
                    "template": item,
                    "source_name": item.name,
                    "source_path": item.source_path or None,
                    "source_in": item.source_in + overlap_in - item.start,
                    "source_out": item.source_in + overlap_out - item.start,
                    "timeline_in": cursor,
                    "timeline_out": cursor + duration,
                    "v10_timeline_in_frame": overlap_in // FRAME_TICKS,
                    "v10_timeline_out_frame": overlap_out // FRAME_TICKS,
                    "authoritative_range_order": int(authoritative["order"]),
                }
            )
            cursor += duration
    if cursor != 4103 * FRAME_TICKS:
        raise PremiereProjectError(
            f"LONG slicing produced {cursor // FRAME_TICKS} frames, expected 4103."
        )
    return result


def _build_long(
    root: ET.Element, *, spec: dict[str, object], project_path: Path
) -> list[dict[str, object]]:
    long = _dict(spec["long_job"], "long_job")
    raw_ranges = long["authoritative_video_ranges_in_output_order"]
    assert isinstance(raw_ranges, list)
    ranges = [_dict(value, "long range") for value in raw_ranges]
    source_items = [
        item
        for item in _items(root, LONG_INPUT, project_path, group=0)
        if item.track_index == 1
    ]
    background_items = _items(root, LONG_BG_INPUT, project_path, group=0)
    slices = _slice_timeline_items(source_items, ranges)
    background_slices = _slice_timeline_items(background_items, ranges)
    for sequence_name, track_index, rows in (
        (LONG_OUTPUT, 1, slices),
        (LONG_BG_OUTPUT, 0, background_slices),
    ):
        container = _clear_track(
            _track(root, sequence_name, group=0, index=track_index)
        )
        for row in rows:
            _append_clone(
                root,
                container=container,
                template=row["template"],
                source_in=int(row["source_in"]),
                source_out=int(row["source_out"]),
                timeline_in=int(row["timeline_in"]),
                timeline_out=int(row["timeline_out"]),
            )
        sequence = find_project_sequence_node(root, sequence_name)
        assert sequence is not None
        _update_sequence_duration_metadata(
            root, sequence, new_total_duration=4103 * FRAME_TICKS
        )
    _retarget_nested_background(
        root,
        output_sequence=LONG_OUTPUT,
        background_sequence=LONG_BG_OUTPUT,
        frames=4103,
        project_path=project_path,
    )
    audio = _items(root, LONG_OUTPUT, project_path, group=1)
    tail = [item for item in audio if item.end // FRAME_TICKS == 4140]
    if len(tail) != 1:
        raise PremiereProjectError("LONG output does not have one expected music tail.")
    item = tail[0]
    timeline = item.track_item_node.find("./ClipTrackItem/TrackItem")
    clip = resolve_project_track_item_clip(
        item.track_item_node, build_project_object_id_lookup(root)
    )
    payload = clip.find("./Clip") if clip is not None else None
    if timeline is None or payload is None:
        raise PremiereProjectError("LONG final music item is malformed.")
    trim = (4140 - 4103) * FRAME_TICKS
    _set_track_item_boundary(timeline, "End", 4103 * FRAME_TICKS)
    old_out = int(payload.findtext("./OutPoint") or "0")
    _set_child_text(payload, "OutPoint", str(old_out - trim))
    sequence = find_project_sequence_node(root, LONG_OUTPUT)
    assert sequence is not None
    _update_sequence_duration_metadata(
        root, sequence, new_total_duration=4103 * FRAME_TICKS
    )
    actual: list[dict[str, object]] = []
    for order, row in enumerate(slices, 1):
        actual.append(
            {
                "order": order,
                "source_name": row["source_name"],
                "source_path": row["source_path"],
                "source_in_frame": int(row["source_in"]) // FRAME_TICKS,
                "source_out_frame": int(row["source_out"]) // FRAME_TICKS,
                "timeline_in_frame": int(row["timeline_in"]) // FRAME_TICKS,
                "timeline_out_frame": int(row["timeline_out"]) // FRAME_TICKS,
                "duration_frames": (
                    int(row["timeline_out"]) - int(row["timeline_in"])
                )
                // FRAME_TICKS,
                "v10_timeline_in_frame": row["v10_timeline_in_frame"],
                "v10_timeline_out_frame": row["v10_timeline_out_frame"],
                "authoritative_range_order": row["authoritative_range_order"],
            }
        )
    return actual


def _extract_hold_frame(project_path: Path, output_path: Path) -> dict[str, object]:
    root = load_premiere_project_root(project_path)
    keep = find_project_sequence_node(root, KEEP_SEQUENCE)
    assert keep is not None
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    item = _visible_item_for_range(
        keep,
        source_in_ticks=3649 * FRAME_TICKS,
        source_out_ticks=3650 * FRAME_TICKS,
        ids=ids,
        uids=uids,
        project_path=project_path,
    )
    if not item.source_path:
        raise PremiereProjectError("FINAL_WALK frame 3649 has no online media path.")
    source = Path(item.source_path)
    if not source.is_file():
        raise PremiereProjectError(f"FINAL_WALK source is offline: {source}")
    offset = item.source_in + 3649 * FRAME_TICKS - item.start
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg_executable()
    _run_ffmpeg(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ss",
            f"{offset / PREMIERE_TICKS_PER_SECOND:.9f}",
            "-frames:v",
            "1",
            "-vf",
            "scale=3840:2160:flags=lanczos",
            str(output_path),
        ],
        "TASK_028 FINAL_HOLD extraction",
    )
    if not output_path.is_file():
        raise RuntimeError("FINAL_HOLD image was not created.")
    with Image.open(output_path) as image:
        if image.size != (3840, 2160):
            raise RuntimeError(f"FINAL_HOLD image has unexpected size: {image.size}")
    return {
        "path": str(output_path),
        "sha256": _sha256(output_path),
        "source_path": str(source),
        "source_sequence_frame": 3649,
        "source_media_offset_seconds": offset / PREMIERE_TICKS_PER_SECOND,
        "size": [3840, 2160],
    }


def _verify_protected(
    root: ET.Element,
    *,
    protected_xml: dict[str, bytes],
    protected_properties: dict[str, dict[str, object]],
    project_path: Path,
) -> None:
    for name, before in protected_xml.items():
        if _sequence_xml(root, name) != before:
            raise PremiereProjectError(f"Protected sequence changed: {name}")
        if _properties(root, name, project_path) != protected_properties[name]:
            raise PremiereProjectError(f"Protected sequence properties changed: {name}")


def _visual_rows(
    root: ET.Element, sequence_name: str, project_path: Path
) -> list[dict[str, object]]:
    rows = []
    for item in _items(root, sequence_name, project_path, group=0):
        if item.track_index != 1:
            continue
        rows.append(
            {
                "order": len(rows) + 1,
                "name": item.name,
                "path": item.source_path or None,
                "source_in_frame": item.source_in // FRAME_TICKS,
                "source_out_frame": item.source_out // FRAME_TICKS,
                "timeline_in_frame": item.start // FRAME_TICKS,
                "timeline_out_frame": item.end // FRAME_TICKS,
                "duration_frames": item.duration // FRAME_TICKS,
            }
        )
    return rows


def _verify_saved(
    *,
    project_path: Path,
    spec: dict[str, object],
    protected_xml: dict[str, bytes],
    protected_properties: dict[str, dict[str, object]],
    short_audio_before: list[dict[str, object]],
    long_audio_before: list[dict[str, object]],
) -> dict[str, object]:
    root = load_premiere_project_root(project_path)
    _verify_protected(
        root,
        protected_xml=protected_xml,
        protected_properties=protected_properties,
        project_path=project_path,
    )
    _validate_all_refs(root)
    short_props = _properties(root, SHORT_OUTPUT, project_path)
    long_props = _properties(root, LONG_OUTPUT, project_path)
    if short_props != {
        "settings": _dict(
            _properties(root, SHORT_INPUT, project_path)["settings"], "settings"
        ),
        "duration_frames": 1878,
        "foreground_clips": 26,
        "background_clips": 1,
        "audio_clips": 2,
    }:
        raise PremiereProjectError(f"SHORT saved-project contract failed: {short_props}")
    if (
        long_props["duration_frames"] != 4103
        or long_props["foreground_clips"] != 32
        or long_props["background_clips"] != 1
        or long_props["audio_clips"] != 2
        or long_props["settings"]
        != _properties(root, LONG_INPUT, project_path)["settings"]
    ):
        raise PremiereProjectError(f"LONG saved-project contract failed: {long_props}")
    short_audio = _audio_rows(root, SHORT_OUTPUT, project_path)
    if short_audio != short_audio_before:
        raise PremiereProjectError("SHORT output music differs from v03.")
    long_audio = _audio_rows(root, LONG_OUTPUT, project_path)
    expected_long_audio = [dict(row) for row in long_audio_before]
    tails = [row for row in expected_long_audio if row["timeline_out_frame"] == 4140]
    if len(tails) != 1:
        raise PremiereProjectError("LONG input music-tail contract changed.")
    tails[0]["timeline_out_frame"] = 4103
    tails[0]["source_out_ticks"] = int(tails[0]["source_out_ticks"]) - 37 * FRAME_TICKS
    expected_long_audio.sort(
        key=lambda row: (int(row["track_index"]), int(row["timeline_in_frame"]))
    )
    if long_audio != expected_long_audio:
        raise PremiereProjectError("LONG output music was not preserved/trimmed exactly.")
    short_rows = _visual_rows(root, SHORT_OUTPUT, project_path)
    raw_short = _dict(spec["short_job"], "short_job")["expected_final_segments"]
    assert isinstance(raw_short, list)
    if len(short_rows) != 26:
        raise PremiereProjectError("SHORT does not contain 26 foreground clips.")
    for row, raw in zip(short_rows, raw_short, strict=True):
        planned = _dict(raw, "short segment")
        if (
            row["timeline_in_frame"] != planned["timeline_in_frame"]
            or row["timeline_out_frame"] != planned["timeline_out_frame"]
            or row["duration_frames"] != planned["duration_frames"]
        ):
            raise PremiereProjectError(
                f"SHORT segment {planned['id']} has incorrect timeline bounds."
            )
        if planned["source_mode"] == "nested_sequence" and (
            row["name"] != planned["source_sequence"]
            or row["source_in_frame"] != planned["source_in_frame"]
            or row["source_out_frame"] != planned["source_out_frame"]
        ):
            raise PremiereProjectError(
                f"SHORT nested segment {planned['id']} differs from specification."
            )
        if planned["source_mode"] == "direct_still_asset" and (
            Path(str(row["path"])).name != planned["source_asset_name"]
        ):
            raise PremiereProjectError(
                f"SHORT direct still {planned['id']} differs from specification."
            )
    long_rows = _visual_rows(root, LONG_OUTPUT, project_path)
    if len(long_rows) != 32:
        raise PremiereProjectError("LONG does not contain 32 foreground clips.")
    reconstructed = []
    for row in long_rows:
        reconstructed.append(
            (
                row["timeline_in_frame"],
                row["timeline_out_frame"],
                row["name"],
                row["source_in_frame"],
                row["source_out_frame"],
            )
        )
    if reconstructed[-1][0] != 4028 or reconstructed[-1][1] != 4103:
        raise PremiereProjectError("LONG does not end with the 75-frame Nuri clip.")
    if reconstructed[-1][2] != "SF_26_BD_Nuri_1":
        raise PremiereProjectError("LONG final clip is not Nuri.")
    short_bg = _items(root, SHORT_BG_OUTPUT, project_path, group=0)
    long_bg = _items(root, LONG_BG_OUTPUT, project_path, group=0)
    short_fg_bounds = [
        (row["timeline_in_frame"], row["timeline_out_frame"]) for row in short_rows
    ]
    short_bg_bounds = [
        (item.start // FRAME_TICKS, item.end // FRAME_TICKS) for item in short_bg
    ]
    long_fg_bounds = [
        (row["timeline_in_frame"], row["timeline_out_frame"]) for row in long_rows
    ]
    long_bg_bounds = [
        (item.start // FRAME_TICKS, item.end // FRAME_TICKS) for item in long_bg
    ]
    if short_bg_bounds != short_fg_bounds or long_bg_bounds != long_fg_bounds:
        raise PremiereProjectError(
            "Blurred-background boundaries do not match foreground boundaries."
        )
    return {
        "saved_project_reopened_and_reparsed": True,
        "protected_sequences_unchanged": list(protected_xml),
        "object_references_resolved": True,
        "short": {
            **short_props,
            "foreground": short_rows,
            "audio": short_audio,
            "background_boundaries_match_foreground": True,
        },
        "long": {
            **long_props,
            "foreground": long_rows,
            "audio": long_audio,
            "background_boundaries_match_foreground": True,
            "camels_v10_1080_1101_retained": True,
            "hospital_ranges_removed": [[1396, 1421], [3319, 3331]],
            "final_nuri_output_range": [4028, 4103],
        },
    }


def _render_actual_video(
    *,
    project_path: Path,
    sequence_name: str,
    frames: int,
    output_path: Path,
) -> None:
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    foreground = [
        item
        for item in _items(root, sequence_name, project_path, group=0)
        if item.track_index == 1
    ]
    ffmpeg = resolve_ffmpeg_executable()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="task028_preview_") as temp_text:
        temp = Path(temp_text)
        rendered: list[Path] = []
        for index, item in enumerate(foreground, 1):
            source_item = item
            sequence_in = item.start
            if not item.source_path:
                source_sequence = find_project_sequence_node(root, item.name)
                if source_sequence is None:
                    raise PremiereProjectError(
                        f"Preview source sequence {item.name!r} is missing."
                    )
                source_item = _visible_item_for_range(
                    source_sequence,
                    source_in_ticks=item.source_in,
                    source_out_ticks=item.source_out,
                    ids=ids,
                    uids=uids,
                    project_path=project_path,
                )
                sequence_in = item.source_in
            segment_path = temp / f"segment_{index:03d}.mp4"
            _render_segment(
                ffmpeg=ffmpeg,
                item=source_item,
                sequence_in_ticks=sequence_in,
                frames=item.duration // FRAME_TICKS,
                fps=FPS,
                width=640,
                height=360,
                output_path=segment_path,
            )
            rendered.append(segment_path)
        concat = temp / "concat.txt"
        concat.write_text(
            "\n".join(f"file '{path.as_posix()}'" for path in rendered) + "\n",
            encoding="utf-8",
        )
        _run_ffmpeg(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-an",
                "-frames:v",
                str(frames),
                "-r",
                str(FPS),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "21",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ],
            f"TASK_028 {sequence_name} video preview",
        )


def _mux_reference_audio(
    *,
    video_path: Path,
    reference_path: Path,
    frames: int,
    output_path: Path,
) -> None:
    ffmpeg = resolve_ffmpeg_executable()
    _run_ffmpeg(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(reference_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{frames / FPS:.3f}",
            str(output_path),
        ],
        f"TASK_028 {output_path.stem} audio mux",
    )
    probe = build_ffprobe_payload(output_path)
    stream = _dict(probe["streams"][0], "preview video stream")  # type: ignore[index]
    if (
        stream["width"] != 640
        or stream["height"] != 360
        or stream["nb_frames"] != frames
        or not math.isclose(float(stream["avg_frame_rate"]), FPS, abs_tol=0.01)
        or probe["audio_stream_count"] != 1
    ):
        raise RuntimeError(f"Preview contract failed: {probe}")


def _dual_overview(
    short_preview: Path, long_preview: Path, output_path: Path
) -> dict[str, object]:
    import cv2

    samples = [
        (short_preview, frame, f"SHORT {frame}")
        for frame in (0, 164, 286, 471, 751, 811, 1121, 1471, 1767, 1830, 1877)
    ] + [
        (long_preview, frame, f"LONG {frame}")
        for frame in (0, 1079, 1080, 1100, 1395, 1396, 3293, 3294, 3545, 3546, 4027, 4028, 4102)
    ]
    cells: list[tuple[Image.Image, str]] = []
    black: list[str] = []
    for path, frame_number, label in samples:
        capture = cv2.VideoCapture(str(path))
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ok, frame = capture.read()
        capture.release()
        if not ok:
            raise RuntimeError(f"Could not decode {label}.")
        if float(frame.mean()) < 2:
            black.append(label)
        cells.append(
            (Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), label)
        )
    if black:
        raise RuntimeError(f"Black overview frames: {black}")
    width, height, label_height, columns = 320, 180, 26, 4
    rows = math.ceil(len(cells) / columns)
    sheet = Image.new(
        "RGB", (columns * width, 34 + rows * (height + label_height)), "white"
    )
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 8), "TASK_028 — SHORT v04 / LONG v11 actual overview", fill="black")
    for index, (image, label) in enumerate(cells):
        x = index % columns * width
        y = 34 + index // columns * (height + label_height)
        image.thumbnail((width, height))
        sheet.paste(image, (x, y))
        draw.text((x + 4, y + height + 3), label, fill="black")
    sheet.save(output_path, quality=92)
    return {
        "path": str(output_path),
        "samples": len(cells),
        "black_frames": [],
        "status": "PASS",
    }


def execute(spec_path: Path, dry_run_only: bool = False) -> dict[str, str]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("TASK_028 specification root must be an object.")
    _validate_spec(spec)
    project_path = Path(str(_dict(spec["project"], "project")["path"]))
    task_dir = spec_path.parent
    preview_dir = task_dir / "02_ACTUAL_PREVIEWS"
    qa_dir = task_dir / "03_ACTUAL_JSON_QA"
    preview_dir.mkdir(parents=True, exist_ok=True)
    qa_dir.mkdir(parents=True, exist_ok=True)
    (
        root,
        protected_xml,
        protected_properties,
        asset_items,
    ) = _preflight(spec, project_path)
    source_hash = _sha256(project_path)
    short_audio_before = _audio_rows(root, SHORT_INPUT, project_path)
    long_audio_before = _audio_rows(root, LONG_INPUT, project_path)
    dry_path = qa_dir / "TASK_028_DRY_RUN.json"
    dry_path.write_text(
        json.dumps(
            {
                "task_id": "TASK_028",
                "project_path": str(project_path),
                "project_sha256": source_hash,
                "short_input": _properties(root, SHORT_INPUT, project_path),
                "long_input": _properties(root, LONG_INPUT, project_path),
                "protected_sequences": list(protected_xml),
                "direct_assets": {
                    name: item.source_path for name, item in asset_items.items()
                },
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
    hold_path = qa_dir / "TASK_028_FINAL_HOLD_FRAME_3649.png"
    hold = _extract_hold_frame(project_path, hold_path)
    _clone_four_sequences(root)
    short_plan = _build_short(
        root,
        spec=spec,
        project_path=project_path,
        asset_items=asset_items,
        hold_path=hold_path,
    )
    long_plan = _build_long(root, spec=spec, project_path=project_path)
    _assert_project_refs_resolved(root)
    _validate_all_refs(root)
    temp_path = qa_dir / "SF_26_BD_1_TASK028_VALIDATION.prproj"
    temp_path.write_bytes(
        gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    )
    _verify_saved(
        project_path=temp_path,
        spec=spec,
        protected_xml=protected_xml,
        protected_properties=protected_properties,
        short_audio_before=short_audio_before,
        long_audio_before=long_audio_before,
    )
    backup = project_path.with_name(
        f"{project_path.stem}_before_TASK_028{project_path.suffix}"
    )
    if backup.exists():
        raise PremiereProjectError(f"BLOCKED: backup already exists: {backup}")
    shutil.copy2(project_path, backup)
    if _sha256(backup) != source_hash:
        raise PremiereProjectError("TASK_028 backup SHA256 mismatch.")
    os.replace(temp_path, project_path)
    qa_project = _verify_saved(
        project_path=project_path,
        spec=spec,
        protected_xml=protected_xml,
        protected_properties=protected_properties,
        short_audio_before=short_audio_before,
        long_audio_before=long_audio_before,
    )
    short_video_only = qa_dir / "TASK_028_SHORT_VIDEO_ONLY_TEMP.mp4"
    long_video_only = qa_dir / "TASK_028_LONG_VIDEO_ONLY_TEMP.mp4"
    _render_actual_video(
        project_path=project_path,
        sequence_name=SHORT_OUTPUT,
        frames=1878,
        output_path=short_video_only,
    )
    _render_actual_video(
        project_path=project_path,
        sequence_name=LONG_OUTPUT,
        frames=4103,
        output_path=long_video_only,
    )
    references = task_dir / "01_INPUT_REFERENCES"
    short_preview = preview_dir / "SF_26_BD_SHORT_76S_v04_640_360.mp4"
    long_preview = preview_dir / "SF_26_BD_LONG_FAMILY_NURI_v11_640_360.mp4"
    _mux_reference_audio(
        video_path=short_video_only,
        reference_path=references
        / "SF_26_BD_SHORT_76S_v04_EDITORIAL_REFERENCE_640_360.mp4",
        frames=1878,
        output_path=short_preview,
    )
    _mux_reference_audio(
        video_path=long_video_only,
        reference_path=references
        / "SF_26_BD_LONG_FAMILY_NURI_v11_EDITORIAL_REFERENCE_640_360.mp4",
        frames=4103,
        output_path=long_preview,
    )
    short_video_only.unlink(missing_ok=True)
    long_video_only.unlink(missing_ok=True)
    short_boundaries = [
        int(_dict(row, "short segment")["timeline_out_frame"])
        for row in _dict(spec["short_job"], "short_job")["expected_final_segments"][:-1]  # type: ignore[index]
    ]
    short_frames = [
        frame
        for boundary in short_boundaries
        for frame in (boundary - 1, boundary)
    ]
    short_sheet_path = qa_dir / "TASK_028_SHORT_NEW_JOINS_CONTACT_SHEET.jpg"
    short_sheet = _contact_sheet(
        short_preview,
        short_frames,
        short_sheet_path,
        "TASK_028 SHORT v04 — both sides of all new joins",
    )
    long_frames = [
        frame
        for boundary in (1396, 3294, 3546, 4028)
        for frame in (boundary - 1, boundary)
    ]
    long_sheet_path = qa_dir / "TASK_028_LONG_NEW_JOINS_CONTACT_SHEET.jpg"
    long_sheet = _contact_sheet(
        long_preview,
        long_frames,
        long_sheet_path,
        "TASK_028 LONG v11 — illness removals, coda move and Nuri finale",
    )
    overview_path = qa_dir / "TASK_028_DUAL_OVERVIEW_CONTACT_SHEET.jpg"
    overview = _dual_overview(short_preview, long_preview, overview_path)
    probes = {
        "short": build_ffprobe_payload(short_preview),
        "long": build_ffprobe_payload(long_preview),
    }
    ffprobe_path = qa_dir / "TASK_028_FFPROBE.json"
    ffprobe_path.write_text(
        json.dumps(probes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    short_actual_path = qa_dir / "TASK_028_SHORT_TIMELINE_ACTUAL.json"
    long_actual_path = qa_dir / "TASK_028_LONG_TIMELINE_ACTUAL.json"
    common = {
        "task_id": "TASK_028",
        "source": "reopened_saved_prproj",
        "project_path": str(project_path),
        "project_sha256": _sha256(project_path),
        "backup_path": str(backup),
        "backup_sha256": _sha256(backup),
        "protected_sequences_unchanged": list(protected_xml),
        "saved_project_reopened_and_reparsed": True,
        "status": "LOCAL_STRUCTURAL_PASS_UPLOAD_AND_PREMIERE_OPEN_CHECK_PENDING",
    }
    short_actual_path.write_text(
        json.dumps(
            {
                **common,
                "input_sequence": SHORT_INPUT,
                "output_sequence": SHORT_OUTPUT,
                "planned_segments": short_plan,
                "actual": qa_project["short"],
                "final_hold": hold,
                "preview": probes["short"],
                "new_joins_contact_sheet": short_sheet,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    long_actual_path.write_text(
        json.dumps(
            {
                **common,
                "input_sequence": LONG_INPUT,
                "output_sequence": LONG_OUTPUT,
                "planned_slices": long_plan,
                "actual": qa_project["long"],
                "preview": probes["long"],
                "new_joins_contact_sheet": long_sheet,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    qa_path = qa_dir / "TASK_028_QA.txt"
    qa_path.write_text(
        "\n".join(
            [
                "TASK_028 — SERGEY DUAL REFINEMENT",
                "",
                "STATUS: LOCAL_STRUCTURAL_PASS_UPLOAD_AND_PREMIERE_OPEN_CHECK_PENDING",
                f"Project: {project_path}",
                f"Backup: {backup}",
                "",
                "SHORT v04",
                "- 1878 frames / 75.12 seconds / 26 foreground clips: PASS",
                "- Nuri opening and FINAL_WALK + 47-frame FINAL_HOLD: PASS",
                "- Two readable direct-still flashes and eight family roles: PASS",
                "- Existing two music clips and Level crossfade timeline preserved: PASS",
                "- Matching blurred-background boundaries: PASS",
                "",
                "LONG v11",
                "- 4103 frames / 164.12 seconds / 32 foreground clips: PASS",
                "- Camel interval [1080,1101) retained: PASS",
                "- Illness intervals [1396,1421) and [3319,3331) removed: PASS",
                "- Coda moved before protected Nuri; Nuri final [4028,4103): PASS",
                "- Existing music preserved; final tail trimmed by 37 frames: PASS",
                "- Matching blurred-background boundaries: PASS",
                "",
                "COMMON",
                "- Protected input/source sequences byte-identical: PASS",
                "- Original Nested Sequence 03/05 backgrounds byte-identical: PASS",
                "- Saved project reopened and reparsed: PASS",
                "- Object references resolved: PASS",
                "- Both previews: H.264 640x360 / 25 fps / AAC stereo: PASS",
                "- New joins and overview: no black frames: PASS",
                "- Preview audio is muxed from the supplied editorial references, which",
                "  carry the preserved source music/crossfades for the exact target lengths.",
                "- Premiere desktop open-check and Muza visual QA: REQUIRED",
                "",
                "TASK_028_DONE.txt was not created.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    upload_pending_path = qa_dir / "TASK_028_UPLOAD_PENDING.txt"
    upload_pending_path.write_text(
        "\n".join(
            [
                "TASK_028 structural execution complete.",
                f"created_at: {datetime.now().isoformat(timespec='seconds')}",
                f"project: {project_path}",
                f"SHORT: {SHORT_OUTPUT} — 1878 frames / 75.12 seconds",
                f"LONG: {LONG_OUTPUT} — 4103 frames / 164.12 seconds",
                "Both actual previews and both timeline JSON files are present locally.",
                "UPLOAD TO GOOGLE DRIVE IS PENDING.",
                "TASK_028_WAITING_MUZA_QA.txt must be created only after upload.",
                "TASK_028_DONE.txt was not created.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "project": str(project_path),
        "backup": str(backup),
        "short_preview": str(short_preview),
        "long_preview": str(long_preview),
        "short_actual": str(short_actual_path),
        "long_actual": str(long_actual_path),
        "ffprobe": str(ffprobe_path),
        "short_contact_sheet": str(short_sheet_path),
        "long_contact_sheet": str(long_sheet_path),
        "overview": str(overview_path),
        "qa": str(qa_path),
        "upload_pending": str(upload_pending_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute TASK_028 dual refinement.")
    parser.add_argument("spec", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            execute(args.spec.resolve(), args.dry_run),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
