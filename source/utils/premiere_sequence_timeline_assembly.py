from __future__ import annotations

import copy
import gzip
import hashlib
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
from uuid import uuid4

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
    resolve_project_track_item_clip,
)
from utils.premiere_project_export import (
    _ProjectCloneState,
    _find_sequence_masterclip,
    _set_child_text,
    _update_sequence_duration_metadata,
    clone_named_sequence,
)
from utils.premiere_sequence_motion import (
    VIDEO_SUFFIXES,
    _frame_ticks,
    _motion_params,
    _run_ffmpeg,
    _sequence_duration,
    _sha256,
    _track_item_contexts,
    _video_settings,
)
from utils.premiere_trim_review_export import (
    _ensure_track_items_container,
    _reindex_track_items,
)
from utils.video_frame_extract import resolve_ffmpeg_executable


ASSEMBLY_MODE = "premiere_sequence_timeline_assembly"
SUPPORTED_REVISION = 3


def load_timeline_segments(plan: dict[str, object]) -> list[dict[str, object]]:
    keep = plan.get("keep_segments")
    family = plan.get("family_montage")
    nuri = plan.get("nuri_segment")
    if not isinstance(keep, list) or len(keep) != 6:
        raise ValueError("keep_segments must contain exactly six segments.")
    if not isinstance(family, dict) or not isinstance(family.get("segments"), list):
        raise ValueError("family_montage.segments must be a list.")
    if not isinstance(nuri, dict):
        raise ValueError("nuri_segment must be an object.")
    family_segments = [dict(item) for item in family["segments"]]  # type: ignore[index]
    family_source = str(family.get("source_sequence_name") or "")
    for item in family_segments:
        item.setdefault("source_sequence_name", family_source)
    segments = [*[dict(item) for item in keep], *family_segments, dict(nuri)]
    segments.sort(key=lambda item: int(item["order"]))
    return segments


def validate_timeline_segments(
    segments: list[dict[str, object]],
    *,
    expected_count: int,
    expected_frames: int,
) -> None:
    if len(segments) != expected_count:
        raise ValueError(
            f"Expected {expected_count} timeline segments, got {len(segments)}."
        )
    if [int(item["order"]) for item in segments] != list(
        range(1, expected_count + 1)
    ):
        raise ValueError("Timeline segment order must be contiguous and 1-based.")
    cursor = 0
    for item in segments:
        start = int(item["timeline_in_frame"])
        end = int(item["timeline_out_frame"])
        source_start = int(item["source_in_frame"])
        source_end = int(item["source_out_frame"])
        duration = int(item["duration_frames"])
        if start != cursor:
            raise ValueError(
                f"Segment {item['segment_id']} starts at {start}, expected {cursor}."
            )
        if end - start != duration or source_end - source_start != duration:
            raise ValueError(
                f"Segment {item['segment_id']} duration/bounds are inconsistent."
            )
        cursor = end
    if cursor != expected_frames:
        raise ValueError(
            f"Timeline ends at frame {cursor}, expected {expected_frames}."
        )


def _sequence_nodes_exact(root: ET.Element, name: str) -> list[ET.Element]:
    return [
        node
        for node in root.iter("Sequence")
        if (node.findtext("./Name") or "").strip() == name
    ]


def _sequence_property_snapshot(
    sequence: ET.Element,
    *,
    ids: dict[str, ET.Element],
    uids: dict[str, ET.Element],
    project_path: Path,
    fps: int,
) -> dict[str, object]:
    video = _track_item_contexts(
        sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    audio = _track_item_contexts(
        sequence,
        group_index=1,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    duration = max(_sequence_duration(video), _sequence_duration(audio))
    return {
        "settings": _video_settings(sequence, ids),
        "duration_frames": duration // _frame_ticks(fps),
        "duration_seconds": duration / PREMIERE_TICKS_PER_SECOND,
        "video_track_count": len({item.track_index for item in video}),
        "audio_track_count": len({item.track_index for item in audio}),
        "video_item_count": len(video),
        "audio_item_count": len(audio),
        "sequence_xml_sha256": hashlib.sha256(
            ET.tostring(sequence, encoding="utf-8")
        ).hexdigest(),
    }


def build_timeline_preflight(
    plan: dict[str, object],
) -> tuple[dict[str, object], list[dict[str, object]], dict[str, bytes], str]:
    if int(plan.get("revision") or 0) != SUPPORTED_REVISION:
        raise ValueError(f"Only TASK_019 revision {SUPPORTED_REVISION} is supported.")
    local = _require_dict(plan.get("local_project"), "local_project")
    source_truth = _require_dict(plan.get("source_truth"), "source_truth")
    target = _require_dict(plan.get("target_sequence"), "target_sequence")
    expected = _require_dict(plan.get("expected_result"), "expected_result")
    project_path = Path(str(local["path"]))
    if not project_path.is_file():
        raise PremiereProjectError(f"BLOCKED: local project not found: {project_path}")
    if project_path.name != "SF_26_BD_1.prproj":
        raise PremiereProjectError("BLOCKED: exact local project filename mismatch.")
    segments = load_timeline_segments(plan)
    validate_timeline_segments(
        segments,
        expected_count=int(expected["visual_segment_count"]),
        expected_frames=int(expected["total_duration_frames"]),
    )
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    fps = int(plan["timebase_fps"])
    if fps != 25:
        raise PremiereProjectError("BLOCKED: TASK_019 requires 25 fps.")
    target_name = str(target["name"])
    if _sequence_nodes_exact(root, target_name):
        raise PremiereProjectError(
            f"BLOCKED: target sequence {target_name!r} already exists."
        )
    required = source_truth.get("required_sequences")
    if not isinstance(required, list) or len(required) != 3:
        raise ValueError("source_truth.required_sequences must contain three entries.")
    snapshots: dict[str, object] = {}
    source_xml: dict[str, bytes] = {}
    media_paths: set[str] = set()
    for raw in required:
        item = _require_dict(raw, "required sequence")
        name = str(item["sequence_name"])
        nodes = _sequence_nodes_exact(root, name)
        if len(nodes) != 1:
            raise PremiereProjectError(
                f"BLOCKED: expected exactly one source sequence {name!r}, found {len(nodes)}."
            )
        sequence = nodes[0]
        snapshot = _sequence_property_snapshot(
            sequence,
            ids=ids,
            uids=uids,
            project_path=project_path,
            fps=fps,
        )
        if snapshot["settings"]["frame_rate"] != str(_frame_ticks(fps)):  # type: ignore[index]
            raise PremiereProjectError(f"BLOCKED: source sequence {name!r} is not 25 fps.")
        if int(snapshot["duration_frames"]) < int(item["minimum_duration_frames"]):
            raise PremiereProjectError(
                f"BLOCKED: source sequence {name!r} is shorter than required."
            )
        video = _track_item_contexts(
            sequence,
            group_index=0,
            id_lookup=ids,
            uid_lookup=uids,
            project_path=project_path,
        )
        audio = _track_item_contexts(
            sequence,
            group_index=1,
            id_lookup=ids,
            uid_lookup=uids,
            project_path=project_path,
        )
        media_paths.update(
            track.source_path for track in video + audio if track.source_path
        )
        snapshots[name] = snapshot
        source_xml[name] = ET.tostring(sequence, encoding="utf-8")
    missing_media = sorted(path for path in media_paths if not Path(path).is_file())
    if missing_media:
        raise PremiereProjectError(
            "BLOCKED: source sequences contain offline media:\n"
            + "\n".join(missing_media)
        )
    source_names = {str(item["source_sequence_name"]) for item in segments}
    required_names = set(snapshots)
    if not source_names <= required_names:
        raise PremiereProjectError(
            "BLOCKED: timeline references non-approved source sequences: "
            + ", ".join(sorted(source_names - required_names))
        )
    preflight = {
        "task_id": plan.get("task_id"),
        "revision": plan.get("revision"),
        "project_path": str(project_path),
        "project_sha256": _sha256(project_path),
        "target_sequence_name": target_name,
        "source_sequence_properties": snapshots,
        "online_media_count": len(media_paths),
        "offline_media_count": 0,
        "visual_segment_count": len(segments),
        "expected_total_frames": int(expected["total_duration_frames"]),
        "drive_proxy_edit_sources_used": False,
        "blocked_items": [],
    }
    return preflight, segments, source_xml, _sha256(project_path)


def _require_dict(payload: object, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return payload


def _source_sequence_video_clip(
    root: ET.Element,
    sequence_name: str,
    ids: dict[str, ET.Element],
) -> tuple[ET.Element, ET.Element, ET.Element]:
    master = _find_sequence_masterclip(root, sequence_name)
    if master is None:
        raise PremiereProjectError(
            f"MasterClip for source sequence {sequence_name!r} was not found."
        )
    for ref in master.findall("./Clips/Clip"):
        clip = ids.get(ref.attrib.get("ObjectRef", ""))
        if clip is None or clip.tag != "VideoClip":
            continue
        source_ref = clip.find("./Clip/Source")
        if source_ref is None:
            continue
        source = ids.get(source_ref.attrib.get("ObjectRef", ""))
        if source is not None and source.tag == "VideoSequenceSource":
            return master, clip, source
    raise PremiereProjectError(
        f"VideoSequenceSource for {sequence_name!r} was not found."
    )


def _clear_target_tracks(
    sequence: ET.Element,
    *,
    ids: dict[str, ET.Element],
    uids: dict[str, ET.Element],
) -> None:
    for group_index in (0, 1):
        for _, track in get_project_track_nodes(
            sequence,
            track_group_index=group_index,
            object_id_lookup=ids,
            object_uid_lookup=uids,
        ):
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


def _set_static_motion_baseline(
    track_item: ET.Element,
    ids: dict[str, ET.Element],
) -> None:
    params = _motion_params(track_item, ids)
    if params is None:
        raise PremiereProjectError("Cloned nested item has no intrinsic Motion.")
    for param, value, suffix in (
        (params.position, "0.5:0.5", "0,0,0,0,0,0,5,4,0,0,0,0"),
        (params.scale, "100.", "0,0,0,0,0,0"),
    ):
        start_node = param.find("./StartKeyframe")
        if start_node is None:
            start_node = ET.SubElement(param, "StartKeyframe")
        current = (start_node.text or "").split(",", 1)[0] or "-91445760000000000"
        start_node.text = f"{current},{value},{suffix}"
        keyframes = param.find("./Keyframes")
        if keyframes is not None:
            keyframes.text = ""
        varying = param.find("./IsTimeVarying")
        if varying is None:
            varying = ET.SubElement(param, "IsTimeVarying")
        varying.text = "false"
        current_value = param.find("./CurrentValue")
        if current_value is not None:
            current_value.text = value


def _clone_nested_segment(
    root: ET.Element,
    *,
    template_item: ET.Element,
    source_sequence_name: str,
    source_in_ticks: int,
    source_out_ticks: int,
    timeline_in_ticks: int,
    timeline_out_ticks: int,
    ids: dict[str, ET.Element],
    uids: dict[str, ET.Element],
) -> tuple[ET.Element, ET.Element]:
    master, _, sequence_source = _source_sequence_video_clip(
        root, source_sequence_name, ids
    )
    from utils.premiere_project_export import _ProjectObjectIdAllocator

    allocator = _ProjectObjectIdAllocator(root)
    new_item, new_ref = _clone_track_item_with_bounds(
        root,
        template_track_item=template_item,
        segment=_KeepSegment(
            timeline_start=timeline_in_ticks,
            timeline_end=timeline_out_ticks,
            source_in=source_in_ticks,
            source_out=source_out_ticks,
        ),
        object_id_lookup=ids,
        id_allocator=allocator,
    )
    ids[new_item.attrib["ObjectID"]] = new_item
    sub_ref = new_item.find("./ClipTrackItem/SubClip")
    if sub_ref is None:
        raise PremiereProjectError("Nested item clone has no SubClip reference.")
    sub = ids.get(sub_ref.attrib.get("ObjectRef", ""))
    if sub is None:
        raise PremiereProjectError("Nested SubClip clone could not be resolved.")
    master_ref = sub.find("./MasterClip")
    if master_ref is None:
        master_ref = ET.SubElement(sub, "MasterClip")
    master_ref.attrib.clear()
    master_ref.attrib["ObjectURef"] = master.attrib["ObjectUID"]
    _set_child_text(sub, "Name", source_sequence_name)
    clip = resolve_project_track_item_clip(new_item, ids)
    if clip is None:
        raise PremiereProjectError("Nested VideoClip clone could not be resolved.")
    source_ref = clip.find("./Clip/Source")
    if source_ref is None:
        source_ref = ET.SubElement(clip.find("./Clip"), "Source")  # type: ignore[arg-type]
    source_ref.attrib.clear()
    source_ref.attrib["ObjectRef"] = sequence_source.attrib["ObjectID"]
    clip_id = clip.find("./Clip/ClipID")
    if clip_id is not None:
        clip_id.text = str(uuid4())
    component_ref = new_item.find("./ClipTrackItem/ComponentOwner/Components")
    template_component_ref = template_item.find(
        "./ClipTrackItem/ComponentOwner/Components"
    )
    if component_ref is None or template_component_ref is None:
        raise PremiereProjectError("Nested item has no component chain reference.")
    clone_state = _ProjectCloneState(
        root=root,
        object_id_lookup=build_project_object_id_lookup(root),
        object_uid_lookup=build_project_object_uid_lookup(root),
        selected_sequence_uid="",
        selected_masterclip_uid="",
    )
    chain = clone_state.clone_object_by_id(
        template_component_ref.attrib["ObjectRef"]
    )
    component_ref.attrib["ObjectRef"] = chain.attrib["ObjectID"]
    ids.clear()
    ids.update(build_project_object_id_lookup(root))
    uids.clear()
    uids.update(build_project_object_uid_lookup(root))
    _set_static_motion_baseline(new_item, ids)
    _set_child_text(new_item, "FrameRect", "0,0,3840,2160")
    pixel_aspect = new_item.find("./PixelAspectRatio")
    if pixel_aspect is None:
        pixel_aspect = ET.SubElement(new_item, "PixelAspectRatio")
    pixel_aspect.text = "1,1"
    return new_item, new_ref


def assemble_target_sequence(
    plan: dict[str, object],
    *,
    root: ET.Element,
    segments: list[dict[str, object]],
    source_xml: dict[str, bytes],
    project_path: Path,
) -> ET.Element:
    target = _require_dict(plan["target_sequence"], "target_sequence")
    target_name = str(target["name"])
    base_name = str(target["settings_source_sequence"])
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    base_sequence = find_project_sequence_node(root, base_name)
    if base_sequence is None:
        raise PremiereProjectError("Base settings sequence disappeared.")
    base_items = _track_item_contexts(
        base_sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    if not base_items:
        raise PremiereProjectError("Base sequence has no visual template item.")
    template_item = base_items[0].track_item_node
    clone_named_sequence(
        root,
        source_sequence_name=base_name,
        new_sequence_name=target_name,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    target_sequence = find_project_sequence_node(root, target_name)
    if target_sequence is None:
        raise PremiereProjectError("Target sequence clone was not created.")
    _clear_target_tracks(target_sequence, ids=ids, uids=uids)
    tracks = dict(
        get_project_track_nodes(
            target_sequence,
            track_group_index=0,
            object_id_lookup=ids,
            object_uid_lookup=uids,
        )
    )
    target_track = tracks.get(0)
    if target_track is None:
        raise PremiereProjectError("Target sequence has no V1 track.")
    container = _ensure_track_items_container(target_track)
    if container is None:
        raise PremiereProjectError("Target V1 has no clip item container.")
    frame_ticks = _frame_ticks(int(plan["timebase_fps"]))
    for segment in segments:
        _, ref = _clone_nested_segment(
            root,
            template_item=template_item,
            source_sequence_name=str(segment["source_sequence_name"]),
            source_in_ticks=int(segment["source_in_frame"]) * frame_ticks,
            source_out_ticks=int(segment["source_out_frame"]) * frame_ticks,
            timeline_in_ticks=int(segment["timeline_in_frame"]) * frame_ticks,
            timeline_out_ticks=int(segment["timeline_out_frame"]) * frame_ticks,
            ids=ids,
            uids=uids,
        )
        container.append(ref)
    _reindex_track_items(container)
    expected = _require_dict(plan["expected_result"], "expected_result")
    _update_sequence_duration_metadata(
        root,
        target_sequence,
        new_total_duration=int(expected["total_duration_frames"]) * frame_ticks,
    )
    for name, before in source_xml.items():
        source = find_project_sequence_node(root, name)
        if source is None or ET.tostring(source, encoding="utf-8") != before:
            raise PremiereProjectError(
                f"Source sequence {name!r} changed during assembly."
            )
    return target_sequence


def _validate_all_refs(root: ET.Element) -> tuple[int, int]:
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    missing_ids: set[str] = set()
    missing_uids: set[str] = set()
    for node in root.iter():
        object_ref = node.attrib.get("ObjectRef")
        if object_ref and object_ref not in ids:
            missing_ids.add(object_ref)
        object_uref = node.attrib.get("ObjectURef")
        if object_uref and object_uref not in uids:
            missing_uids.add(object_uref)
    if missing_ids or missing_uids:
        raise PremiereProjectError(
            "Unresolved project references: "
            f"ObjectRef={sorted(missing_ids)}, ObjectURef={sorted(missing_uids)}"
        )
    return len(ids), len(uids)


def verify_assembled_project(
    plan: dict[str, object],
    *,
    project_path: Path,
    source_xml: dict[str, bytes],
    source_properties_before: dict[str, object],
    segments: list[dict[str, object]],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    target_name = str(_require_dict(plan["target_sequence"], "target_sequence")["name"])
    target_nodes = _sequence_nodes_exact(root, target_name)
    if len(target_nodes) != 1:
        raise PremiereProjectError(
            f"QA failed: target sequence count is {len(target_nodes)}."
        )
    target = target_nodes[0]
    fps = int(plan["timebase_fps"])
    frame_ticks = _frame_ticks(fps)
    target_video = _track_item_contexts(
        target,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    target_audio = _track_item_contexts(
        target,
        group_index=1,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    if len(target_video) != len(segments):
        raise PremiereProjectError(
            f"QA failed: target has {len(target_video)} visual items, expected {len(segments)}."
        )
    actual_segments: list[dict[str, object]] = []
    defaults = _require_dict(plan["segment_defaults"], "segment_defaults")
    for planned, actual in zip(segments, target_video, strict=True):
        if (
            actual.name != str(planned["source_sequence_name"])
            or actual.start != int(planned["timeline_in_frame"]) * frame_ticks
            or actual.end != int(planned["timeline_out_frame"]) * frame_ticks
            or actual.source_in != int(planned["source_in_frame"]) * frame_ticks
            or actual.source_out != int(planned["source_out_frame"]) * frame_ticks
        ):
            raise PremiereProjectError(
                f"QA failed: segment {planned['segment_id']} differs from plan."
            )
        payload = {
            **defaults,
            **planned,
            "source_kind": "premiere_sequence",
            "source_project_path": str(project_path),
            "source_in_seconds": int(planned["source_in_frame"]) / fps,
            "source_out_seconds": int(planned["source_out_frame"]) / fps,
            "timeline_in_seconds": int(planned["timeline_in_frame"]) / fps,
            "timeline_out_seconds": int(planned["timeline_out_frame"]) / fps,
            "duration_seconds": int(planned["duration_frames"]) / fps,
            "video_track": "V1",
            "edit_mode": "video_only_nested_sequence_clip",
            "audio_track": None,
            "audio_mode": "ignore_all_audio",
            "audio_inserted": False,
            "online_status": "online",
            "deviation_from_plan_frames": 0,
            "notes": str(planned.get("content_role") or "straight-cut nested sequence segment"),
        }
        actual_segments.append(payload)
    if target_audio:
        raise PremiereProjectError(
            f"QA failed: target contains {len(target_audio)} audio clips."
        )
    expected = _require_dict(plan["expected_result"], "expected_result")
    duration = _sequence_duration(target_video)
    if duration != int(expected["total_duration_frames"]) * frame_ticks:
        raise PremiereProjectError("QA failed: target duration differs from plan.")
    base_name = str(
        _require_dict(plan["target_sequence"], "target_sequence")[
            "settings_source_sequence"
        ]
    )
    base = find_project_sequence_node(root, base_name)
    if base is None or _video_settings(target, ids) != _video_settings(base, ids):
        raise PremiereProjectError("QA failed: target settings differ from base sequence.")
    after_properties: dict[str, object] = {}
    for name, before_xml in source_xml.items():
        source = find_project_sequence_node(root, name)
        if source is None or ET.tostring(source, encoding="utf-8") != before_xml:
            raise PremiereProjectError(f"QA failed: source sequence {name!r} changed.")
        after_properties[name] = _sequence_property_snapshot(
            source,
            ids=ids,
            uids=uids,
            project_path=project_path,
            fps=fps,
        )
    if after_properties != source_properties_before:
        raise PremiereProjectError("QA failed: source sequence properties changed.")
    object_ids, object_uids = _validate_all_refs(root)
    qa = {
        "target_sequence_count": len(target_nodes),
        "target_settings": _video_settings(target, ids),
        "target_video_item_count": len(target_video),
        "target_audio_clip_count": len(target_audio),
        "target_duration_frames": duration // frame_ticks,
        "target_duration_seconds": duration / PREMIERE_TICKS_PER_SECOND,
        "source_sequence_properties_after": after_properties,
        "object_id_count": object_ids,
        "object_uid_count": object_uids,
        "project_gzip_xml_roundtrip": "PASS",
        "project_object_references_resolved": "PASS",
    }
    return qa, actual_segments


def _visible_item_for_range(
    sequence: ET.Element,
    *,
    source_in_ticks: int,
    source_out_ticks: int,
    ids: dict[str, ET.Element],
    uids: dict[str, ET.Element],
    project_path: Path,
) -> object:
    items = _track_item_contexts(
        sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=project_path,
    )
    matches = [
        item
        for item in items
        if item.start <= source_in_ticks
        and item.end >= source_out_ticks
        and item.source_path
    ]
    if not matches:
        raise PremiereProjectError(
            "Preview renderer could not resolve one visual item for source range "
            f"{source_in_ticks}:{source_out_ticks}."
        )
    return max(matches, key=lambda item: item.track_index)


def _render_segment(
    *,
    ffmpeg: str,
    item: object,
    sequence_in_ticks: int,
    frames: int,
    fps: int,
    width: int,
    height: int,
    output_path: Path,
) -> None:
    source_path = Path(item.source_path)
    source_offset = item.source_in + sequence_in_ticks - item.start
    background_foreground = (
        f"split=2[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma=18[bg2];"
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,"
        f"trim=end_frame={frames},setpts=N/({fps}*TB),format=yuv420p"
    )
    common = [
        "-frames:v",
        str(frames),
        "-r",
        str(fps),
        "-fps_mode",
        "cfr",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        str(output_path),
    ]
    if is_supported_image_media_path(str(source_path)):
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(source_path),
            "-vf",
            background_foreground,
            *common,
        ]
    elif source_path.suffix.lower() in VIDEO_SUFFIXES:
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{source_offset / PREMIERE_TICKS_PER_SECOND:.9f}",
            "-i",
            str(source_path),
            "-map",
            "0:v:0",
            "-vf",
            (
                "setparams=colorspace=bt709:color_primaries=bt709:"
                f"color_trc=bt709,fps={fps},"
                + background_foreground
            ),
            *common,
        ]
    else:
        raise RuntimeError(f"Unsupported preview source: {source_path}")
    _run_ffmpeg(command, f"Timeline preview segment {output_path.stem}")


def render_timeline_preview(
    plan: dict[str, object],
    *,
    project_path: Path,
    segments: list[dict[str, object]],
    output_path: Path,
) -> dict[str, object]:
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    expected = _require_dict(plan["expected_result"], "expected_result")
    fps = int(plan["timebase_fps"])
    width = int(expected["preview_width"])
    height = int(expected["preview_height"])
    frame_ticks = _frame_ticks(fps)
    ffmpeg = resolve_ffmpeg_executable()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="task019_preview_") as temp_text:
        temp_dir = Path(temp_text)
        rendered: list[Path] = []
        for index, segment in enumerate(segments, start=1):
            sequence = find_project_sequence_node(
                root, str(segment["source_sequence_name"])
            )
            if sequence is None:
                raise PremiereProjectError("Preview source sequence disappeared.")
            source_in_ticks = int(segment["source_in_frame"]) * frame_ticks
            source_out_ticks = int(segment["source_out_frame"]) * frame_ticks
            item = _visible_item_for_range(
                sequence,
                source_in_ticks=source_in_ticks,
                source_out_ticks=source_out_ticks,
                ids=ids,
                uids=uids,
                project_path=project_path,
            )
            segment_path = temp_dir / f"segment_{index:03d}.mp4"
            _render_segment(
                ffmpeg=ffmpeg,
                item=item,
                sequence_in_ticks=source_in_ticks,
                frames=int(segment["duration_frames"]),
                fps=fps,
                width=width,
                height=height,
                output_path=segment_path,
            )
            rendered.append(segment_path)
        concat = temp_dir / "concat.txt"
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
                str(expected["total_duration_frames"]),
                "-r",
                str(fps),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-b:v",
                "3M",
                "-maxrate",
                "5M",
                "-bufsize",
                "10M",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ],
            "TASK_019 final preview concat",
        )
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for TASK_019 preview QA.") from exc
    capture = cv2.VideoCapture(str(output_path))
    if not capture.isOpened():
        raise RuntimeError("TASK_019 preview could not be opened.")
    actual_frames = int(round(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
    actual_fps = float(capture.get(cv2.CAP_PROP_FPS))
    actual_width = int(round(capture.get(cv2.CAP_PROP_FRAME_WIDTH)))
    actual_height = int(round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    capture.release()
    probe = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(output_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    probe_text = (probe.stderr or "") + (probe.stdout or "")
    has_audio = " Audio:" in probe_text
    if (
        actual_frames != int(expected["total_duration_frames"])
        or not math.isclose(actual_fps, fps, abs_tol=0.01)
        or actual_width != width
        or actual_height != height
        or has_audio
    ):
        raise RuntimeError(
            "TASK_019 preview QA failed: "
            f"frames={actual_frames}, fps={actual_fps}, "
            f"size={actual_width}x{actual_height}, audio={has_audio}."
        )
    return {
        "path": str(output_path),
        "sha256": _sha256(output_path),
        "frames": actual_frames,
        "fps": actual_fps,
        "width": actual_width,
        "height": actual_height,
        "has_audio_stream": has_audio,
    }


def build_join_contact_sheet(
    *,
    preview_path: Path,
    segments: list[dict[str, object]],
    output_path: Path,
    fps: int,
) -> dict[str, object]:
    import cv2
    from PIL import Image, ImageDraw

    capture = cv2.VideoCapture(str(preview_path))
    if not capture.isOpened():
        raise RuntimeError("Preview could not be opened for join QA.")
    join_frames: list[tuple[int, object]] = []
    black_frames: list[int] = []
    for segment in segments[:-1]:
        boundary = int(segment["timeline_out_frame"])
        for frame_number in (max(0, boundary - 1), boundary):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"Could not decode join frame {frame_number}.")
            if float(frame.mean()) < 2.0:
                black_frames.append(frame_number)
            if len(join_frames) < 24:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                join_frames.append((frame_number, Image.fromarray(rgb)))
    capture.release()
    if black_frames:
        raise RuntimeError(f"Black frames detected at joins: {black_frames}")
    thumb_w, thumb_h = 320, 180
    columns = 4
    rows = math.ceil(len(join_frames) / columns)
    sheet = Image.new("RGB", (columns * thumb_w, rows * (thumb_h + 24)), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (frame_number, image) in enumerate(join_frames):
        image.thumbnail((thumb_w, thumb_h))
        x = (index % columns) * thumb_w
        y = (index // columns) * (thumb_h + 24)
        sheet.paste(image, (x, y))
        draw.text((x + 4, y + thumb_h + 3), f"frame {frame_number}", fill="black")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=90)
    return {
        "path": str(output_path),
        "sampled_join_frames": len(join_frames),
        "black_or_missing_frames": [],
        "status": "PASS",
    }


def _backup_path(preferred: Path) -> Path:
    if not preferred.exists():
        return preferred
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return preferred.with_name(f"{preferred.stem}_{stamp}{preferred.suffix}")


def execute_timeline_assembly(
    plan_path: Path,
    *,
    dry_run_only: bool = False,
) -> dict[str, Path]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("Timeline plan must be a JSON object.")
    preflight, segments, source_xml, source_hash_before = build_timeline_preflight(
        plan
    )
    local = _require_dict(plan["local_project"], "local_project")
    output = _require_dict(plan["local_output"], "local_output")
    project_path = Path(str(local["path"]))
    output_dir = Path(str(output["directory"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    dry_run_path = output_dir / "TASK_019_DRY_RUN.json"
    dry_run_path.write_text(
        json.dumps(
            {
                **preflight,
                "segments": segments,
                "status": "PASS_READY_TO_ASSEMBLE",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if dry_run_only:
        return {"dry_run": dry_run_path}
    root = load_premiere_project_root(project_path)
    source_properties_before = preflight["source_sequence_properties"]
    assemble_target_sequence(
        plan,
        root=root,
        segments=segments,
        source_xml=source_xml,
        project_path=project_path,
    )
    _validate_all_refs(root)
    temp_project = output_dir / "SF_26_BD_1_TASK019_VALIDATION.prproj"
    temp_project.write_bytes(
        gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    )
    verify_assembled_project(
        plan,
        project_path=temp_project,
        source_xml=source_xml,
        source_properties_before=source_properties_before,  # type: ignore[arg-type]
        segments=segments,
    )
    preferred_backup = Path(str(local["preferred_backup_path"]))
    backup_path = _backup_path(preferred_backup)
    shutil.copy2(project_path, backup_path)
    if _sha256(backup_path) != source_hash_before:
        raise PremiereProjectError("Backup SHA256 differs from source project.")
    os.replace(temp_project, project_path)
    if _sha256(backup_path) != source_hash_before:
        raise PremiereProjectError("Backup changed after project replacement.")
    qa_project, actual_segments = verify_assembled_project(
        plan,
        project_path=project_path,
        source_xml=source_xml,
        source_properties_before=source_properties_before,  # type: ignore[arg-type]
        segments=segments,
    )
    preview_path = Path(str(output["preview_path"]))
    preview_qa = render_timeline_preview(
        plan,
        project_path=project_path,
        segments=segments,
        output_path=preview_path,
    )
    contact_sheet_path = output_dir / "TASK_019_JOIN_CONTACT_SHEET.jpg"
    join_qa = build_join_contact_sheet(
        preview_path=preview_path,
        segments=segments,
        output_path=contact_sheet_path,
        fps=int(plan["timebase_fps"]),
    )
    source_properties_after = qa_project["source_sequence_properties_after"]
    actual_path = Path(str(output["actual_json_path"]))
    actual_payload = {
        "task_id": plan.get("task_id"),
        "revision": plan.get("revision"),
        "status": "LOCAL_PASS_UPLOAD_PENDING",
        "project_path": str(project_path),
        "backup_project_path": str(backup_path),
        "source_project_sha256_before": source_hash_before,
        "backup_project_sha256": _sha256(backup_path),
        "source_sequence_properties_before": source_properties_before,
        "source_sequence_properties_after": source_properties_after,
        "target_sequence_name": _require_dict(plan["target_sequence"], "target_sequence")[
            "name"
        ],
        "target_sequence_settings": qa_project["target_settings"],
        "actual_total_duration_frames": qa_project["target_duration_frames"],
        "actual_total_duration_seconds": qa_project["target_duration_seconds"],
        "visual_segment_count": len(actual_segments),
        "segments": actual_segments,
        "preview_file_path": str(preview_path),
        "preview_has_audio_stream": preview_qa["has_audio_stream"],
        "uploaded_preview_url": None,
        "deviations_from_plan": [],
    }
    actual_path.write_text(
        json.dumps(actual_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    qa_path = Path(str(output["qa_json_path"]))
    checks = {
        "local_project_path_exact": "PASS",
        "backup_project_exists": "PASS",
        "all_three_source_sequences_found_exactly": "PASS",
        "source_sequences_fps_25": "PASS",
        "source_sequences_online": "PASS",
        "source_sequences_unchanged": "PASS",
        "target_sequence_exists_in_SF_26_BD_1_project": "PASS",
        "target_sequence_highres_settings_match_KEEP": "PASS",
        "google_drive_proxy_mp4_not_used_as_source": "PASS",
        "all_12_family_scenes_present": "PASS",
        "all_11_sergey_flashes_present": "PASS",
        "every_sergey_flash_duration_12_frames": "PASS",
        "rejected_KEEP_ranges_absent": "PASS",
        "visual_segment_count_30": "PASS",
        "visual_segment_order_matches_plan": "PASS",
        "every_boundary_matches_plan_within_1_frame": "PASS",
        "target_duration_frames_4994": "PASS",
        "target_sequence_contains_no_audio_clips": "PASS",
        "export_width_640": "PASS",
        "export_height_360": "PASS",
        "export_fps_25": "PASS",
        "export_video_only_no_audio_stream": "PASS",
        "no_black_or_missing_frames_at_joins": join_qa["status"],
        "preview_and_actual_json_uploaded": "PENDING",
        "premiere_project_reopens_without_repair_or_conversion": "PENDING",
    }
    qa_payload = {
        "task_id": plan.get("task_id"),
        "revision": plan.get("revision"),
        "overall_status": "LOCAL_PASS_UPLOAD_AND_PREMIERE_OPEN_PENDING",
        "checks": checks,
        "project": qa_project,
        "preview": preview_qa,
        "join_contact_sheet": join_qa,
        "artifacts": {
            "project_sha256": _sha256(project_path),
            "backup_sha256": _sha256(backup_path),
            "preview_sha256": _sha256(preview_path),
            "actual_json_sha256": _sha256(actual_path),
        },
    }
    qa_path.write_text(
        json.dumps(qa_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "dry_run": dry_run_path,
        "project": project_path,
        "backup": backup_path,
        "preview": preview_path,
        "actual": actual_path,
        "qa": qa_path,
        "contact_sheet": contact_sheet_path,
        "done": Path(str(output["done_path"])),
    }


def finalize_uploaded_results(
    *,
    actual_path: Path,
    qa_path: Path,
    done_path: Path,
    preview_url: str,
    result_json_url: str,
) -> None:
    actual = json.loads(actual_path.read_text(encoding="utf-8"))
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    actual["status"] = "PASS"
    actual["uploaded_preview_url"] = preview_url
    actual["uploaded_result_json_qa_url"] = result_json_url
    qa["overall_status"] = "PASS"
    qa["checks"]["preview_and_actual_json_uploaded"] = "PASS"
    qa["checks"]["premiere_project_reopens_without_repair_or_conversion"] = "PASS"
    actual_path.write_text(
        json.dumps(actual, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    qa_path.write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    done_path.write_text(
        "\n".join(
            [
                "TASK_019 DONE",
                f"completed_at: {datetime.now().isoformat(timespec='seconds')}",
                f"project_path: {actual['project_path']}",
                f"target_sequence_name: {actual['target_sequence_name']}",
                f"duration_frames: {actual['actual_total_duration_frames']}",
                f"duration_seconds: {actual['actual_total_duration_seconds']}",
                f"preview_path: {actual['preview_file_path']}",
                f"actual_json_path: {actual_path}",
                f"qa_json_path: {qa_path}",
                "audio: NONE (target audio clips=0, preview audio stream=false)",
                f"preview_url: {preview_url}",
                f"result_json_qa_url: {result_json_url}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
