from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from models.premiere_sequence_motion import (
    MotionDryRunPlan,
    MotionPlanItem,
    MotionProfile,
)
from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    is_supported_image_media_path,
    iter_project_track_item_refs,
    load_premiere_project_root,
    resolve_project_track_item_name,
    resolve_project_track_item_source_bounds,
    resolve_project_track_item_source_path,
    resolve_project_track_item_timeline,
)
from utils.premiere_project_export import clone_named_sequence
from utils.premiere_trim_review_export import _reindex_track_items
from utils.video_frame_extract import resolve_ffmpeg_executable


MOTION_MODE = "premiere_sequence_motion_animation"
SUPPORTED_SCHEMA_VERSION = "1.0"
EXECUTOR_ENTRY_POINT = "main_premiere_import_keep.py"
AUTOMATION_MECHANISM = (
    "Direct transactional .prproj XML editing: clone the source sequence and its "
    "component graph, then write intrinsic Motion Scale/Position keyframes into only "
    "the cloned track-item parameters. This is deterministic and frame-exact, does "
    "not depend on Premiere GUI focus, and preserves unrelated effects."
)
IMPLEMENTATION_REPORT_NAME = "WORK_TO_MUZA_REPLY_015C_MOTION_IMPLEMENTATION.txt"
QA_REPORT_NAME = "WORK_TO_MUZA_REPLY_015C_MOTION_QA.txt"
DEFAULT_DRY_RUN_NAME = "WORK_TO_MUZA_REPLY_015C_MOTION_DRY_RUN.json"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mts", ".m2ts"}


@dataclass(frozen=True)
class _MotionParams:
    position: ET.Element
    scale: ET.Element


@dataclass(frozen=True)
class _TrackItemContext:
    track_index: int
    track_node: ET.Element
    track_item_ref: ET.Element
    track_item_node: ET.Element
    name: str
    source_path: str
    start: int
    end: int
    source_in: int
    source_out: int

    @property
    def duration(self) -> int:
        return max(0, self.end - self.start)


def is_premiere_sequence_motion_config(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and str(payload.get("mode") or "").strip().casefold() == MOTION_MODE
    )


def _require_dict(payload: object, label: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return payload


def validate_premiere_sequence_motion_config(payload: object) -> dict[str, object]:
    config = _require_dict(payload, "Premiere motion config")
    schema_version = str(config.get("schema_version") or "").strip()
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"premiere_sequence_motion_animation requires schema_version "
            f"{SUPPORTED_SCHEMA_VERSION!r}, got {schema_version!r}."
        )
    mode = str(config.get("mode") or "").strip().casefold()
    if mode != MOTION_MODE:
        raise ValueError(
            f"Expected mode={MOTION_MODE!r}, got {config.get('mode')!r}."
        )
    project = _require_dict(config.get("project"), "project")
    sequences = _require_dict(config.get("sequences"), "sequences")
    contract = _require_dict(config.get("sequence_contract"), "sequence_contract")
    target = _require_dict(config.get("target_selection"), "target_selection")
    motion = _require_dict(config.get("motion_animation"), "motion_animation")
    audio = _require_dict(config.get("audio_policy"), "audio_policy")
    dry_run = _require_dict(config.get("dry_run"), "dry_run")
    review = _require_dict(config.get("review_export"), "review_export")

    required_text = {
        "project.project_file": project.get("project_file"),
        "project.save_as_project_file": project.get("save_as_project_file"),
        "sequences.source_sequence_name": sequences.get("source_sequence_name"),
        "sequences.output_sequence_name": sequences.get("output_sequence_name"),
        "review_export.filename": review.get("filename"),
    }
    missing = [label for label, value in required_text.items() if not str(value or "").strip()]
    if missing:
        raise ValueError("Missing required Premiere motion fields: " + ", ".join(missing))
    if str(sequences["source_sequence_name"]).casefold() == str(
        sequences["output_sequence_name"]
    ).casefold():
        raise ValueError("Source and output sequence names must differ.")
    if int(contract.get("edit_timebase_fps") or 0) <= 0:
        raise ValueError("sequence_contract.edit_timebase_fps must be positive.")
    if int(contract.get("expected_frames") or 0) <= 0:
        raise ValueError("sequence_contract.expected_frames must be positive.")
    if int(target.get("minimum_visible_duration_frames") or 0) <= 0:
        raise ValueError("target_selection.minimum_visible_duration_frames must be positive.")
    profiles = motion.get("motion_profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("motion_animation.motion_profiles must be a non-empty list.")
    directions = motion.get("direction_cycle")
    if not isinstance(directions, list) or not directions:
        raise ValueError("motion_animation.direction_cycle must be a non-empty list.")
    if str(audio.get("mode") or "").strip().upper() != "OUTPUT_SILENT":
        raise ValueError("Only audio_policy.mode=OUTPUT_SILENT is supported.")
    if not bool(dry_run.get("required", True)):
        raise ValueError("premiere_sequence_motion_animation requires dry_run.required=true.")
    versioning = config.get("sequence_versioning")
    if isinstance(versioning, dict):
        validate_milestone_sequence_version(
            str(sequences["output_sequence_name"]),
            increment=int(versioning.get("automated_milestone_increment") or 0),
            expected_milestone=int(versioning.get("current_output_milestone") or 0),
        )
    return config


def validate_milestone_sequence_version(
    sequence_name: str,
    *,
    increment: int,
    expected_milestone: int,
) -> int:
    if increment <= 0 or expected_milestone <= 0:
        raise ValueError("Milestone increment and current milestone must be positive.")
    match = re.search(r"_v(\d+)$", sequence_name, flags=re.IGNORECASE)
    if match is None:
        raise ValueError(
            f"Milestone output sequence must end with _v<number>: {sequence_name!r}."
        )
    version = int(match.group(1))
    if version != expected_milestone or version % increment:
        raise ValueError(
            f"Output sequence {sequence_name!r} is v{version}; expected milestone "
            f"v{expected_milestone} on a step of {increment}."
        )
    return version


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sequence_nodes_exact(root: ET.Element, name: str) -> list[ET.Element]:
    return [
        node
        for node in root.iter("Sequence")
        if (node.findtext("./Name") or "").strip() == name
    ]


def _frame_ticks(fps: int) -> int:
    if PREMIERE_TICKS_PER_SECOND % fps:
        raise ValueError(f"Unsupported non-integral Premiere frame rate: {fps}")
    return PREMIERE_TICKS_PER_SECOND // fps


def _seconds_to_frame_ticks(seconds: float, fps: int) -> int:
    frames = round(float(seconds) * fps)
    if not math.isclose(frames / fps, float(seconds), abs_tol=1e-6):
        raise ValueError(f"Time {seconds} is not frame-aligned at {fps} fps.")
    return frames * _frame_ticks(fps)


def _video_settings(
    sequence: ET.Element,
    id_lookup: dict[str, ET.Element],
) -> dict[str, str]:
    ref = sequence.find("./TrackGroups/TrackGroup[@Index='0']/Second")
    if ref is None:
        raise PremiereProjectError("Sequence has no video track group.")
    group = id_lookup.get(ref.attrib.get("ObjectRef", ""))
    if group is None:
        raise PremiereProjectError("Sequence video track group could not be resolved.")
    return {
        "frame_rate": (group.findtext("./TrackGroup/FrameRate") or "").strip(),
        "frame_rect": (group.findtext("./FrameRect") or "").strip(),
        "pixel_aspect_ratio": (group.findtext("./PixelAspectRatio") or "").strip(),
        "field_type": (group.findtext("./FieldType") or "").strip(),
        "output_color_space": (group.findtext("./OutputColorSpace") or "").strip(),
        "color_management": (group.findtext("./ColorManagementSettings") or "").strip(),
    }


def _track_item_contexts(
    sequence: ET.Element,
    *,
    group_index: int,
    id_lookup: dict[str, ET.Element],
    uid_lookup: dict[str, ET.Element],
    project_path: Path,
) -> list[_TrackItemContext]:
    result: list[_TrackItemContext] = []
    for track_index, track in get_project_track_nodes(
        sequence,
        track_group_index=group_index,
        object_id_lookup=id_lookup,
        object_uid_lookup=uid_lookup,
    ):
        for ref in iter_project_track_item_refs(track):
            node = id_lookup.get(ref.attrib.get("ObjectRef", ""))
            if node is None:
                continue
            start, end = resolve_project_track_item_timeline(node)
            source_in, source_out = resolve_project_track_item_source_bounds(
                node, id_lookup
            )
            result.append(
                _TrackItemContext(
                    track_index=track_index,
                    track_node=track,
                    track_item_ref=ref,
                    track_item_node=node,
                    name=resolve_project_track_item_name(node, id_lookup),
                    source_path=resolve_project_track_item_source_path(
                        node,
                        id_lookup,
                        uid_lookup,
                        project_path=project_path,
                    ),
                    start=start,
                    end=end,
                    source_in=source_in,
                    source_out=source_out,
                )
            )
    return sorted(result, key=lambda item: (item.start, item.track_index, item.end, item.name))


def _sequence_duration(contexts: Iterable[_TrackItemContext]) -> int:
    return max((item.end for item in contexts), default=0)


def _ranges_ticks(raw_ranges: object, fps: int) -> list[tuple[int, int]]:
    if not isinstance(raw_ranges, list):
        raise ValueError("Timeline ranges must be a list.")
    ranges: list[tuple[int, int]] = []
    for index, value in enumerate(raw_ranges, start=1):
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError(f"Timeline range #{index} must be [start, end].")
        start = _seconds_to_frame_ticks(float(value[0]), fps)
        end = _seconds_to_frame_ticks(float(value[1]), fps)
        if end <= start:
            raise ValueError(f"Timeline range #{index} has end <= start.")
        ranges.append((start, end))
    return ranges


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < range_end and end > range_start for range_start, range_end in ranges)


def _inside_ranges(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start >= range_start and end <= range_end for range_start, range_end in ranges)


def _motion_params(
    track_item: ET.Element,
    id_lookup: dict[str, ET.Element],
) -> _MotionParams | None:
    chain_ref = track_item.find("./ClipTrackItem/ComponentOwner/Components")
    if chain_ref is None:
        return None
    chain = id_lookup.get(chain_ref.attrib.get("ObjectRef", ""))
    if chain is None:
        return None
    for component_ref in chain.findall("./ComponentChain/Components/Component"):
        component = id_lookup.get(component_ref.attrib.get("ObjectRef", ""))
        if component is None:
            continue
        display_name = (component.findtext("./Component/DisplayName") or "").strip()
        match_name = (component.findtext("./MatchName") or "").strip()
        if display_name != "Motion" and match_name != "AE.ADBE Motion":
            continue
        params: dict[str, ET.Element] = {}
        for param_ref in component.findall("./Component/Params/Param"):
            param = id_lookup.get(param_ref.attrib.get("ObjectRef", ""))
            if param is not None:
                params[(param.findtext("./Name") or "").strip()] = param
        if "Position" in params and "Scale" in params:
            return _MotionParams(position=params["Position"], scale=params["Scale"])
    return None


def _start_keyframe_value(param: ET.Element) -> str:
    text = (param.findtext("./StartKeyframe") or "").strip()
    parts = text.split(",")
    if len(parts) < 2:
        raise PremiereProjectError(
            f"Motion parameter {(param.findtext('./Name') or '').strip()!r} "
            "has no readable StartKeyframe."
        )
    return parts[1]


def _baseline_scale(param: ET.Element) -> float:
    try:
        return float(_start_keyframe_value(param).rstrip("."))
    except ValueError as exc:
        raise PremiereProjectError("Motion Scale baseline is not numeric.") from exc


def _baseline_position(param: ET.Element) -> tuple[float, float]:
    value = _start_keyframe_value(param)
    parts = value.split(":")
    if len(parts) != 2:
        raise PremiereProjectError("Motion Position baseline is not x:y.")
    try:
        return float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise PremiereProjectError("Motion Position baseline is not numeric.") from exc


def _keyframe_values(param: ET.Element) -> list[str]:
    text = (param.findtext("./Keyframes") or "").strip()
    values: list[str] = []
    for entry in text.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(",")
        if len(parts) >= 2:
            values.append(parts[1])
    return values


def _keyframes_use_linear_temporal_interpolation(param: ET.Element) -> bool:
    text = (param.findtext("./Keyframes") or "").strip()
    entries = [entry for entry in text.split(";") if entry.strip()]
    return bool(entries) and all(
        len(parts := entry.split(",")) >= 4
        and parts[2].strip() == "0"
        and parts[3].strip() == "0"
        for entry in entries
    )


def _meaningful_existing_motion(params: _MotionParams) -> tuple[bool, bool]:
    position_values = _keyframe_values(params.position)
    scale_values = _keyframe_values(params.scale)
    all_values = position_values + scale_values
    has_keyframes = bool(all_values)
    meaningful = (
        len(set(position_values)) >= 2
        or len(set(scale_values)) >= 2
    )
    return has_keyframes, meaningful


def _parse_profiles(motion: dict[str, object]) -> list[MotionProfile]:
    result: list[MotionProfile] = []
    for raw in motion["motion_profiles"]:  # type: ignore[index]
        profile = _require_dict(raw, "motion profile")
        max_value = profile.get("visible_duration_frames_max")
        result.append(
            MotionProfile(
                name=str(profile.get("name") or "").strip(),
                min_frames=int(profile.get("visible_duration_frames_min") or 0),
                max_frames=None if max_value is None else int(max_value),
                scale_delta_percent=float(
                    profile.get("scale_delta_percent_of_baseline") or 0.0
                ),
                max_position_delta_percent=float(
                    profile.get("max_position_delta_percent_of_frame") or 0.0
                ),
            )
        )
    return result


def select_motion_profile(
    visible_frames: int,
    profiles: list[MotionProfile],
) -> MotionProfile:
    for profile in profiles:
        if visible_frames < profile.min_frames:
            continue
        if profile.max_frames is None or visible_frames <= profile.max_frames:
            return profile
    raise ValueError(f"No motion profile covers {visible_frames} visible frames.")


def _detect_centered_faces(path: Path) -> tuple[int, bool, str]:
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        return 0, False, "semantic face safety is unavailable for non-image media"
    try:
        import cv2
    except ImportError:
        return 0, False, "OpenCV is unavailable; conservative centered-push fallback"
    if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
        return (
            0,
            False,
            "OpenCV build has no Haar cascade support; conservative centered-push fallback",
        )
    image = cv2.imread(str(path))
    if image is None:
        return 0, False, "image could not be decoded; conservative centered-push fallback"
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(str(cascade_path))
    faces = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(32, 32))
    if len(faces) == 0:
        return 0, False, "no face was confidently detected; conservative centered-push fallback"
    height, width = gray.shape[:2]
    centered = all(
        x / width >= 0.10
        and y / height >= 0.08
        and (x + face_width) / width <= 0.90
        and (y + face_height) / height <= 0.92
        for x, y, face_width, face_height in faces
    )
    if not centered:
        return len(faces), False, "a detected face is near an image edge"
    return len(faces), True, "detected faces remain inside conservative 10% safety bounds"


def _plan_direction(
    requested: str,
    *,
    candidate_index: int,
    source_path: Path,
    baseline_scale: float,
    profile: MotionProfile,
    frame_width: int,
) -> tuple[str, str, int]:
    requested = requested.strip().upper()
    requested = {
        "PAN_LEFT_TO_RIGHT_WITH_SAFE_OVERSCAN": (
            "GENTLE_PAN_LEFT_TO_RIGHT_WITH_SAFE_OVERSCAN"
        ),
        "PAN_RIGHT_TO_LEFT_WITH_SAFE_OVERSCAN": (
            "GENTLE_PAN_RIGHT_TO_LEFT_WITH_SAFE_OVERSCAN"
        ),
    }.get(requested, requested)
    if requested not in {
        "GENTLE_PAN_LEFT_TO_RIGHT_WITH_SAFE_OVERSCAN",
        "GENTLE_PAN_RIGHT_TO_LEFT_WITH_SAFE_OVERSCAN",
    }:
        return requested, "", 0
    try:
        import cv2
    except ImportError:
        cv2 = None
    image = cv2.imread(str(source_path)) if cv2 is not None else None
    if image is not None:
        _, media_width = image.shape[:2]
        scale_factor = (baseline_scale / 100.0) * (
            1.0 + profile.scale_delta_percent / 100.0
        )
        rendered_width = media_width * scale_factor
        endpoint_shift_x = (
            profile.max_position_delta_percent / 100.0 / 2.0 * frame_width
        )
        if (
            rendered_width + 2.0 * endpoint_shift_x <= frame_width + 0.5
        ):
            return (
                requested,
                (
                    "full-image geometry remains inside the sequence frame at both "
                    "pan endpoints; no subject pixels can be cropped"
                ),
                0,
            )
    face_count, safe, reason = _detect_centered_faces(source_path)
    if safe:
        return requested, "", face_count
    fallback = "PUSH_IN" if candidate_index % 2 else "PUSH_OUT"
    return fallback, reason, face_count


def _rounded(value: float) -> float:
    return round(value, 9)


def _motion_values(
    *,
    baseline_scale: float,
    baseline_x: float,
    baseline_y: float,
    profile: MotionProfile,
    direction: str,
) -> tuple[float, float, float, float, float, float]:
    factor = 1.0 + profile.scale_delta_percent / 100.0
    boosted = baseline_scale * factor
    position_delta = profile.max_position_delta_percent / 100.0
    if direction == "PUSH_IN":
        return baseline_scale, boosted, baseline_x, baseline_y, baseline_x, baseline_y
    if direction == "PUSH_OUT":
        return boosted, baseline_scale, baseline_x, baseline_y, baseline_x, baseline_y
    if direction == "GENTLE_PAN_LEFT_TO_RIGHT_WITH_SAFE_OVERSCAN":
        return (
            boosted,
            boosted,
            baseline_x - position_delta / 2.0,
            baseline_y,
            baseline_x + position_delta / 2.0,
            baseline_y,
        )
    if direction == "GENTLE_PAN_RIGHT_TO_LEFT_WITH_SAFE_OVERSCAN":
        return (
            boosted,
            boosted,
            baseline_x + position_delta / 2.0,
            baseline_y,
            baseline_x - position_delta / 2.0,
            baseline_y,
        )
    raise ValueError(f"Unsupported motion direction: {direction}")


def _item_dict(item: _TrackItemContext, fps: int, reason: str = "") -> dict[str, object]:
    return {
        "track_index": item.track_index,
        "track_item_id": item.track_item_node.attrib.get("ObjectID", ""),
        "clip_name": item.name,
        "source_path": item.source_path,
        "start_seconds": item.start / PREMIERE_TICKS_PER_SECOND,
        "end_seconds": item.end / PREMIERE_TICKS_PER_SECOND,
        "visible_frames": item.duration // _frame_ticks(fps),
        "reason": reason,
    }


def build_premiere_motion_dry_run(
    config: dict[str, object],
    *,
    project_path: Path,
) -> tuple[MotionDryRunPlan, bytes, str]:
    project = _require_dict(config["project"], "project")
    sequences = _require_dict(config["sequences"], "sequences")
    contract = _require_dict(config["sequence_contract"], "sequence_contract")
    target = _require_dict(config["target_selection"], "target_selection")
    motion = _require_dict(config["motion_animation"], "motion_animation")
    dry_run = _require_dict(config["dry_run"], "dry_run")
    source_name = str(sequences["source_sequence_name"])
    output_name = str(sequences["output_sequence_name"])
    fps = int(contract["edit_timebase_fps"])
    expected_frames = int(contract["expected_frames"])
    expected_duration = float(contract["expected_duration_seconds"])
    expected_ticks = expected_frames * _frame_ticks(fps)
    minimum_frames = int(target["minimum_visible_duration_frames"])
    include_ranges = _ranges_ticks(target["include_ranges_seconds"], fps)
    protected_ranges = _ranges_ticks(target["protected_ranges_seconds"], fps)
    profiles = _parse_profiles(motion)
    directions = [str(item).strip().upper() for item in motion["direction_cycle"]]  # type: ignore[index]

    if not project_path.is_file():
        raise PremiereProjectError(f"BLOCKED: source project not found: {project_path}")
    root = load_premiere_project_root(project_path)
    id_lookup = build_project_object_id_lookup(root)
    uid_lookup = build_project_object_uid_lookup(root)
    source_nodes = _sequence_nodes_exact(root, source_name)
    output_nodes = _sequence_nodes_exact(root, output_name)
    if len(source_nodes) != 1:
        raise PremiereProjectError(
            f"BLOCKED: expected exactly one sequence {source_name!r}, found {len(source_nodes)}."
        )
    if output_nodes:
        raise PremiereProjectError(
            f"BLOCKED: output sequence {output_name!r} already exists."
        )
    source_sequence = source_nodes[0]
    source_xml = ET.tostring(source_sequence, encoding="utf-8")
    settings = _video_settings(source_sequence, id_lookup)
    expected_rect = _require_dict(contract["expected_frame_size"], "expected_frame_size")
    expected_frame_rect = (
        f"0,0,{int(expected_rect['width'])},{int(expected_rect['height'])}"
    )
    frame_width = int(expected_rect["width"])
    expected_frame_rate = str(_frame_ticks(fps))
    video_items = _track_item_contexts(
        source_sequence,
        group_index=0,
        id_lookup=id_lookup,
        uid_lookup=uid_lookup,
        project_path=project_path,
    )
    audio_items = _track_item_contexts(
        source_sequence,
        group_index=1,
        id_lookup=id_lookup,
        uid_lookup=uid_lookup,
        project_path=project_path,
    )
    actual_duration = _sequence_duration(video_items + audio_items)
    if settings["frame_rate"] != expected_frame_rate:
        raise PremiereProjectError(
            f"BLOCKED: source sequence frame rate is {settings['frame_rate']}, "
            f"expected {expected_frame_rate} ({fps} fps)."
        )
    if settings["frame_rect"] != expected_frame_rect:
        raise PremiereProjectError(
            f"BLOCKED: source sequence frame rect is {settings['frame_rect']}, "
            f"expected {expected_frame_rect}."
        )
    if actual_duration != expected_ticks:
        raise PremiereProjectError(
            f"BLOCKED: source duration is "
            f"{actual_duration / PREMIERE_TICKS_PER_SECOND:.3f}s, "
            f"expected {expected_duration:.3f}s."
        )
    media_paths = sorted(
        {item.source_path for item in video_items + audio_items if item.source_path}
    )
    missing_media = [path for path in media_paths if not Path(path).is_file()]
    if missing_media:
        raise PremiereProjectError(
            "BLOCKED: source sequence has offline media:\n" + "\n".join(missing_media)
        )
    live_items = (
        [
            item
            for item in video_items
            if item.start == protected_ranges[0][0]
            and item.end == protected_ranges[0][1]
            and Path(item.source_path).suffix.lower() in VIDEO_SUFFIXES
        ]
        if protected_ranges
        else []
    )
    if protected_ranges and len(live_items) != 1:
        raise PremiereProjectError(
            "BLOCKED: the first protected natural-motion range was not found "
            "as exactly one video item."
        )

    plan = MotionDryRunPlan(
        task_id=str(config.get("task_id") or ""),
        discovered_executor_entry_point=EXECUTOR_ENTRY_POINT,
        selected_premiere_automation_mechanism=AUTOMATION_MECHANISM,
        source_sequence_validation={
            "project_file": str(project_path),
            "project_sha256": _sha256(project_path),
            "source_sequence_name": source_name,
            "source_sequence_match_count": len(source_nodes),
            "output_sequence_name": output_name,
            "output_sequence_preexisting_count": len(output_nodes),
            "frame_size": [int(expected_rect["width"]), int(expected_rect["height"])],
            "fps": fps,
            "duration_seconds": actual_duration / PREMIERE_TICKS_PER_SECOND,
            "frames": actual_duration // _frame_ticks(fps),
            "media_online_count": len(media_paths),
            "media_offline_count": 0,
            "protected_live_block_count": len(live_items),
            "source_project_read_only": bool(project.get("never_overwrite_source_project", True)),
        },
        planned_audio_clip_removal_count=len(audio_items),
        expected_output_frames=int(dry_run.get("expected_output_frames") or expected_frames),
        expected_output_duration_seconds=float(
            dry_run.get("expected_output_duration_seconds") or expected_duration
        ),
    )

    candidate_index = 0
    for item in video_items:
        if _overlaps(item.start, item.end, protected_ranges):
            protected_payload = _item_dict(
                item, fps, "overlaps protected natural-motion range"
            )
            protected_payload["property_snapshot"] = protected_property_snapshot(
                item, id_lookup
            )
            plan.protected_items.append(protected_payload)
            continue
        if not _inside_ranges(item.start, item.end, include_ranges):
            continue
        visible_frames = item.duration // _frame_ticks(fps)
        if visible_frames < minimum_frames:
            plan.skipped_short_items.append(
                _item_dict(item, fps, f"shorter than {minimum_frames} frames")
            )
            continue
        if not item.source_path:
            plan.blocked_items.append(
                _item_dict(item, fps, "eligible visual item has no resolvable media path")
            )
            continue
        params = _motion_params(item.track_item_node, id_lookup)
        if params is None:
            plan.blocked_items.append(
                _item_dict(item, fps, "intrinsic Motion Scale/Position parameters not found")
            )
            continue
        has_keyframes, meaningful = _meaningful_existing_motion(params)
        if meaningful:
            plan.already_compliant_items.append(
                _item_dict(item, fps, "existing meaningful Motion keyframes")
            )
            continue
        if has_keyframes:
            plan.blocked_items.append(
                _item_dict(item, fps, "Motion keyframes exist but show no visible value change")
            )
            continue
        try:
            profile = select_motion_profile(visible_frames, profiles)
            baseline_scale = _baseline_scale(params.scale)
            baseline_x, baseline_y = _baseline_position(params.position)
        except (ValueError, PremiereProjectError) as exc:
            plan.blocked_items.append(_item_dict(item, fps, str(exc)))
            continue
        if baseline_scale <= 0 or not (0.0 <= baseline_x <= 1.0 and 0.0 <= baseline_y <= 1.0):
            plan.blocked_items.append(
                _item_dict(item, fps, "invalid baseline Scale/Position")
            )
            continue

        requested = directions[candidate_index % len(directions)]
        applied, override_reason, detected_faces = _plan_direction(
            requested,
            candidate_index=candidate_index + 1,
            source_path=Path(item.source_path),
            baseline_scale=baseline_scale,
            profile=profile,
            frame_width=frame_width,
        )
        values = _motion_values(
            baseline_scale=baseline_scale,
            baseline_x=baseline_x,
            baseline_y=baseline_y,
            profile=profile,
            direction=applied,
        )
        start_scale, end_scale, start_x, start_y, end_x, end_y = values
        scale_factor = 1.0 + profile.scale_delta_percent / 100.0
        pan_delta = abs(end_x - start_x)
        overscan_margin = (scale_factor - 1.0) / (2.0 * scale_factor)
        if "PAN_" in applied and pan_delta / 2.0 > overscan_margin + 1e-9:
            plan.blocked_items.append(
                _item_dict(item, fps, "planned pan exceeds relative overscan margin")
            )
            continue
        framing_result = (
            "SAFE: Scale never drops below the existing baseline; centered push "
            "cannot expose new borders."
            if "PUSH_" in applied
            else "SAFE: relative Scale overscan exceeds the normalized pan delta; "
            "detected faces remain inside conservative safety bounds."
        )
        candidate_index += 1
        plan.candidate_video_items.append(
            MotionPlanItem(
                index=candidate_index,
                track_index=item.track_index,
                track_item_id=item.track_item_node.attrib.get("ObjectID", ""),
                clip_name=item.name,
                source_path=item.source_path,
                timeline_start_ticks=item.start,
                timeline_end_ticks=item.end,
                source_in_ticks=item.source_in,
                source_out_ticks=item.source_out,
                visible_frames=visible_frames,
                profile=profile.name,
                requested_direction=requested,
                applied_direction=applied,
                direction_override_reason=override_reason,
                baseline_scale=_rounded(baseline_scale),
                baseline_position_x=_rounded(baseline_x),
                baseline_position_y=_rounded(baseline_y),
                start_scale=_rounded(start_scale),
                end_scale=_rounded(end_scale),
                start_position_x=_rounded(start_x),
                start_position_y=_rounded(start_y),
                end_position_x=_rounded(end_x),
                end_position_y=_rounded(end_y),
                framing_safety_result=framing_result,
                detected_faces=detected_faces,
            )
        )

    return plan, source_xml, _sha256(project_path)


def _format_number(value: float) -> str:
    text = f"{value:.12f}".rstrip("0").rstrip(".")
    return text if "." in text else text + "."


def build_scale_keyframes(
    start_ticks: int,
    end_ticks: int,
    start_value: float,
    end_value: float,
    *,
    interpolation: str = "BEZIER_EASE_IN_OUT",
) -> str:
    easing = (
        "0,0,0,0,0,0"
        if "LINEAR" in interpolation.upper()
        else "5,4,0,0.33333333333333331,0,0.33333333333333331"
    )
    return (
        f"{start_ticks},{_format_number(start_value)},{easing};"
        f"{end_ticks},{_format_number(end_value)},{easing};"
    )


def build_position_keyframes(
    start_ticks: int,
    end_ticks: int,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    *,
    interpolation: str = "BEZIER_EASE_IN_OUT",
) -> str:
    easing = (
        "0,0,0,0,0,0,5,4,0,0,0,0"
        if "LINEAR" in interpolation.upper()
        else "5,4,0,0.33333333333333331,0,0.33333333333333331,5,4,0,0,0,0"
    )
    return (
        f"{start_ticks},{_format_number(start_x)}:{_format_number(start_y)},{easing};"
        f"{end_ticks},{_format_number(end_x)}:{_format_number(end_y)},{easing};"
    )


def _set_param_keyframes(
    param: ET.Element,
    *,
    keyframes: str,
    current_value: str | None = None,
) -> None:
    keyframes_node = param.find("./Keyframes")
    if keyframes_node is None:
        keyframes_node = ET.SubElement(param, "Keyframes")
    keyframes_node.text = keyframes
    varying_node = param.find("./IsTimeVarying")
    if varying_node is None:
        varying_node = ET.SubElement(param, "IsTimeVarying")
    varying_node.text = "true"
    name = (param.findtext("./Name") or "").strip()
    control_type = "6" if name == "Position" else "2"
    control_node = param.find("./ParameterControlType")
    if control_node is None:
        control_node = ET.SubElement(param, "ParameterControlType")
    control_node.text = control_type
    if current_value is not None:
        current_node = param.find("./CurrentValue")
        if current_node is None:
            current_node = ET.SubElement(param, "CurrentValue")
        current_node.text = current_value


def _find_output_item(
    plan_item: MotionPlanItem,
    contexts: list[_TrackItemContext],
) -> _TrackItemContext:
    matches = [
        item
        for item in contexts
        if item.track_index == plan_item.track_index
        and item.start == plan_item.timeline_start_ticks
        and item.end == plan_item.timeline_end_ticks
        and item.name == plan_item.clip_name
        and Path(item.source_path) == Path(plan_item.source_path)
    ]
    if len(matches) != 1:
        raise PremiereProjectError(
            f"Could not uniquely resolve cloned output item #{plan_item.index} "
            f"{plan_item.clip_name!r}; matches={len(matches)}."
        )
    return matches[0]


def _remove_all_audio_clips(
    sequence: ET.Element,
    *,
    id_lookup: dict[str, ET.Element],
    uid_lookup: dict[str, ET.Element],
) -> int:
    removed = 0
    for _, track in get_project_track_nodes(
        sequence,
        track_group_index=1,
        object_id_lookup=id_lookup,
        object_uid_lookup=uid_lookup,
    ):
        clip_container = track.find("./ClipTrack/ClipItems/TrackItems")
        if clip_container is not None:
            removed += len(list(clip_container.findall("./TrackItem")))
            for child in list(clip_container):
                clip_container.remove(child)
            _reindex_track_items(clip_container)
        transition_container = track.find("./ClipTrack/TransitionItems/TrackItems")
        if transition_container is not None:
            for child in list(transition_container):
                transition_container.remove(child)
            _reindex_track_items(transition_container)
    return removed


def _write_project(root: ET.Element, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    output_path.write_bytes(gzip.compress(payload))


def _picture_signature(
    contexts: list[_TrackItemContext],
) -> list[tuple[int, int, int, str, str]]:
    return [
        (item.track_index, item.start, item.end, item.name, item.source_path)
        for item in contexts
    ]


def _motion_signature(
    item: _TrackItemContext,
    id_lookup: dict[str, ET.Element],
) -> tuple[tuple[str, str, str], tuple[str, str, str]]:
    params = _motion_params(item.track_item_node, id_lookup)
    if params is None:
        return ("", "", ""), ("", "", "")
    def signature(param: ET.Element) -> tuple[str, str, str]:
        return (
            (param.findtext("./StartKeyframe") or "").strip(),
            (param.findtext("./Keyframes") or "").strip(),
            (param.findtext("./CurrentValue") or "").strip(),
        )
    return (
        signature(params.position),
        signature(params.scale),
    )


def protected_property_snapshot(
    item: _TrackItemContext,
    id_lookup: dict[str, ET.Element],
) -> dict[str, object]:
    components: list[dict[str, object]] = []
    chain_ref = item.track_item_node.find("./ClipTrackItem/ComponentOwner/Components")
    chain = (
        id_lookup.get(chain_ref.attrib.get("ObjectRef", ""))
        if chain_ref is not None
        else None
    )
    if chain is not None:
        for component_ref in chain.findall("./ComponentChain/Components/Component"):
            component = id_lookup.get(component_ref.attrib.get("ObjectRef", ""))
            if component is None:
                continue
            params: list[dict[str, str]] = []
            for param_ref in component.findall("./Component/Params/Param"):
                param = id_lookup.get(param_ref.attrib.get("ObjectRef", ""))
                if param is None:
                    continue
                params.append(
                    {
                        "name": (param.findtext("./Name") or "").strip(),
                        "parameter_id": (param.findtext("./ParameterID") or "").strip(),
                        "parameter_control_type": (
                            param.findtext("./ParameterControlType") or ""
                        ).strip(),
                        "start_keyframe": (
                            param.findtext("./StartKeyframe") or ""
                        ).strip(),
                        "keyframes": (param.findtext("./Keyframes") or "").strip(),
                        "is_time_varying": (
                            param.findtext("./IsTimeVarying") or ""
                        ).strip(),
                        "current_value": (
                            param.findtext("./CurrentValue") or ""
                        ).strip(),
                    }
                )
            components.append(
                {
                    "display_name": (
                        component.findtext("./Component/DisplayName") or ""
                    ).strip(),
                    "match_name": (component.findtext("./MatchName") or "").strip(),
                    "intrinsic": (
                        component.findtext("./Component/Intrinsic") or ""
                    ).strip(),
                    "params": params,
                }
            )
    return {
        "track_index": item.track_index,
        "clip_name": item.name,
        "source_path": item.source_path,
        "timeline_start_ticks": item.start,
        "timeline_end_ticks": item.end,
        "source_in_ticks": item.source_in,
        "source_out_ticks": item.source_out,
        "frame_rect": (item.track_item_node.findtext("./FrameRect") or "").strip(),
        "pixel_aspect_ratio": (
            item.track_item_node.findtext("./PixelAspectRatio") or ""
        ).strip(),
        "tone_map_settings": (
            item.track_item_node.findtext("./ToneMapSettings") or ""
        ).strip(),
        "components": components,
    }


def _verify_output_project(
    *,
    config: dict[str, object],
    project_path: Path,
    output_path: Path,
    source_xml: bytes,
    plan: MotionDryRunPlan,
) -> dict[str, object]:
    sequences = _require_dict(config["sequences"], "sequences")
    contract = _require_dict(config["sequence_contract"], "sequence_contract")
    target = _require_dict(config["target_selection"], "target_selection")
    motion_config = _require_dict(config["motion_animation"], "motion_animation")
    source_name = str(sequences["source_sequence_name"])
    output_name = str(sequences["output_sequence_name"])
    fps = int(contract["edit_timebase_fps"])
    expected_frames = int(contract["expected_frames"])

    source_root = load_premiere_project_root(project_path)
    source_ids = build_project_object_id_lookup(source_root)
    source_uids = build_project_object_uid_lookup(source_root)
    source_sequence = find_project_sequence_node(source_root, source_name)
    if source_sequence is None:
        raise PremiereProjectError("QA failed: source sequence disappeared.")
    source_video = _track_item_contexts(
        source_sequence,
        group_index=0,
        id_lookup=source_ids,
        uid_lookup=source_uids,
        project_path=project_path,
    )

    root = load_premiere_project_root(output_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    source_nodes = _sequence_nodes_exact(root, source_name)
    output_nodes = _sequence_nodes_exact(root, output_name)
    if len(source_nodes) != 1 or len(output_nodes) != 1:
        raise PremiereProjectError(
            f"QA failed: sequence counts source={len(source_nodes)}, output={len(output_nodes)}."
        )
    if ET.tostring(source_nodes[0], encoding="utf-8") != source_xml:
        raise PremiereProjectError("QA failed: source sequence changed in saved-as project.")
    preexisting_sequences_checked = 0
    for source_existing in source_root.iter("Sequence"):
        existing_name = (source_existing.findtext("./Name") or "").strip()
        if not existing_name:
            continue
        output_existing = _sequence_nodes_exact(root, existing_name)
        if len(output_existing) != 1 or ET.tostring(
            source_existing, encoding="utf-8"
        ) != ET.tostring(output_existing[0], encoding="utf-8"):
            raise PremiereProjectError(
                f"QA failed: pre-existing sequence {existing_name!r} changed."
            )
        preexisting_sequences_checked += 1
    output_sequence = output_nodes[0]
    output_video = _track_item_contexts(
        output_sequence,
        group_index=0,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=output_path,
    )
    output_audio = _track_item_contexts(
        output_sequence,
        group_index=1,
        id_lookup=ids,
        uid_lookup=uids,
        project_path=output_path,
    )
    if _picture_signature(source_video) != _picture_signature(output_video):
        raise PremiereProjectError("QA failed: output picture timing/order differs from source.")
    if output_audio:
        raise PremiereProjectError(
            f"QA failed: output still contains {len(output_audio)} audio clips."
        )
    settings = _video_settings(output_sequence, ids)
    if settings != _video_settings(source_sequence, source_ids):
        raise PremiereProjectError("QA failed: output sequence settings differ from source.")
    actual_duration = _sequence_duration(output_video + output_audio)
    if actual_duration != expected_frames * _frame_ticks(fps):
        raise PremiereProjectError(
            f"QA failed: output duration is "
            f"{actual_duration / PREMIERE_TICKS_PER_SECOND:.3f}s."
        )

    animated = 0
    for plan_item in plan.candidate_video_items:
        item = _find_output_item(plan_item, output_video)
        params = _motion_params(item.track_item_node, ids)
        if params is None:
            raise PremiereProjectError(
                f"QA failed: Motion params missing for {plan_item.clip_name!r}."
            )
        position_values = _keyframe_values(params.position)
        scale_values = _keyframe_values(params.scale)
        if len(position_values) != 2 or len(scale_values) != 2:
            raise PremiereProjectError(
                f"QA failed: {plan_item.clip_name!r} does not have exactly two "
                "Position and two Scale keyframes."
            )
        if len(set(position_values)) < 2 and len(set(scale_values)) < 2:
            raise PremiereProjectError(
                f"QA failed: {plan_item.clip_name!r} has no nonzero Motion change."
            )
        if any(
            (param.findtext("./IsTimeVarying") or "").strip().casefold() != "true"
            for param in (params.position, params.scale)
        ):
            raise PremiereProjectError(
                f"QA failed: {plan_item.clip_name!r} is missing IsTimeVarying=true."
            )
        if "LINEAR" in str(
            motion_config.get("temporal_interpolation") or ""
        ).upper() and not all(
            _keyframes_use_linear_temporal_interpolation(param)
            for param in (params.position, params.scale)
        ):
            raise PremiereProjectError(
                f"QA failed: {plan_item.clip_name!r} does not use linear temporal interpolation."
            )
        animated += 1

    protected_ranges = _ranges_ticks(target["protected_ranges_seconds"], fps)
    protected_source = [
        item for item in source_video if _overlaps(item.start, item.end, protected_ranges)
    ]
    protected_output = [
        item for item in output_video if _overlaps(item.start, item.end, protected_ranges)
    ]
    if len(protected_source) != len(protected_output):
        raise PremiereProjectError("QA failed: protected item count changed.")
    for source_item, output_item in zip(protected_source, protected_output, strict=True):
        if protected_property_snapshot(
            source_item, source_ids
        ) != protected_property_snapshot(output_item, ids):
            raise PremiereProjectError(
                f"QA failed: protected property snapshot changed for {source_item.name!r}."
            )

    raw_priority = config.get("priority_review_ranges")
    priority_expectation = next(
        (
            str(item.get("profile_expectation") or "")
            for item in raw_priority
            if isinstance(item, dict)
            and math.isclose(
                float(item.get("start_seconds") or -1), 62.4, abs_tol=1e-6
            )
            and math.isclose(
                float(item.get("end_seconds") or -1), 66.72, abs_tol=1e-6
            )
        ),
        "",
    ) if isinstance(raw_priority, list) else ""
    priority_long = [
        item
        for item in plan.candidate_video_items
        if item.timeline_start_ticks <= _seconds_to_frame_ticks(62.4, fps)
        and item.timeline_end_ticks >= _seconds_to_frame_ticks(66.72, fps)
    ]
    if priority_expectation and (
        len(priority_long) != 1 or priority_long[0].profile != priority_expectation
    ):
        raise PremiereProjectError(
            f"QA failed: range 62.40-66.72 does not have {priority_expectation}."
        )
    return {
        "source_sequence_count": len(source_nodes),
        "output_sequence_count": len(output_nodes),
        "picture_item_count": len(output_video),
        "animated_item_count": animated,
        "already_compliant_item_count": len(plan.already_compliant_items),
        "skipped_short_item_count": len(plan.skipped_short_items),
        "protected_item_count": len(protected_output),
        "preexisting_sequences_unchanged_count": preexisting_sequences_checked,
        "audio_clip_count": len(output_audio),
        "duration_seconds": actual_duration / PREMIERE_TICKS_PER_SECOND,
        "frames": actual_duration // _frame_ticks(fps),
        "settings": settings,
        "priority_62_40_66_72_profile": (
            priority_long[0].profile if priority_long else "not_required"
        ),
    }


def _zoompan_expression(plan_item: MotionPlanItem | None, frames: int) -> str:
    denominator = max(frames - 1, 1)
    if plan_item is None:
        return "z='1':x='0':y='0'"
    ratio = max(plan_item.start_scale, plan_item.end_scale) / max(
        plan_item.baseline_scale, 1e-9
    )
    ratio = max(1.0, ratio)
    if plan_item.applied_direction == "PUSH_IN":
        return (
            f"z='1+({ratio - 1:.9f})*on/{denominator}':"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        )
    if plan_item.applied_direction == "PUSH_OUT":
        return (
            f"z='{ratio:.9f}-({ratio - 1:.9f})*on/{denominator}':"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        )
    if "LEFT_TO_RIGHT" in plan_item.applied_direction:
        return (
            f"z='{ratio:.9f}':"
            f"x='(iw-iw/zoom)*on/{denominator}':y='ih/2-(ih/zoom/2)'"
        )
    return (
        f"z='{ratio:.9f}':"
        f"x='(iw-iw/zoom)*(1-on/{denominator})':y='ih/2-(ih/zoom/2)'"
    )


def _run_ffmpeg(command: list[str], label: str) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}: {detail}")


def _render_review(
    *,
    config: dict[str, object],
    output_project_path: Path,
    output_path: Path,
    plan: MotionDryRunPlan,
) -> dict[str, object]:
    sequences = _require_dict(config["sequences"], "sequences")
    contract = _require_dict(config["sequence_contract"], "sequence_contract")
    review = _require_dict(config["review_export"], "review_export")
    output_name = str(sequences["output_sequence_name"])
    fps = int(review["frame_rate_fps"])
    expected_frames = int(review["expected_frames"])
    width = int(_require_dict(review["actual_frame_size"], "actual_frame_size")["width"])
    height = int(_require_dict(review["actual_frame_size"], "actual_frame_size")["height"])
    frame_ticks = _frame_ticks(int(contract["edit_timebase_fps"]))
    ffmpeg = resolve_ffmpeg_executable()

    root = load_premiere_project_root(output_project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    sequence = find_project_sequence_node(root, output_name)
    if sequence is None:
        raise PremiereProjectError(f"Review source sequence {output_name!r} not found.")
    visual_items = [
        item
        for item in _track_item_contexts(
            sequence,
            group_index=0,
            id_lookup=ids,
            uid_lookup=uids,
            project_path=output_project_path,
        )
        if item.track_index == 1
    ]
    if not visual_items:
        raise PremiereProjectError("Review source has no primary picture items.")
    plan_lookup = {
        (
            item.track_index,
            item.timeline_start_ticks,
            item.timeline_end_ticks,
            item.clip_name,
        ): item
        for item in plan.candidate_video_items
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="premiere_motion_review_") as temp_text:
        temp_dir = Path(temp_text)
        segments: list[Path] = []
        total_frames = 0
        for index, item in enumerate(visual_items, start=1):
            frames = item.duration // frame_ticks
            if frames <= 0:
                continue
            total_frames += frames
            segment_path = temp_dir / f"segment_{index:03d}.mp4"
            source_path = Path(item.source_path)
            suffix = source_path.suffix.lower()
            if is_supported_image_media_path(str(source_path)):
                motion_item = plan_lookup.get(
                    (item.track_index, item.start, item.end, item.name)
                )
                zoompan = _zoompan_expression(motion_item, frames)
                filter_graph = (
                    f"split=2[bg][fg];"
                    f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},gblur=sigma=18[bg2];"
                    f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg2];"
                    f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2[base];"
                    f"[base]zoompan={zoompan}:d={frames}:s={width}x{height}:fps={fps},"
                    f"trim=end_frame={frames},setpts=N/({fps}*TB),format=yuv420p"
                )
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
                    filter_graph,
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
                    "22",
                    str(segment_path),
                ]
            elif suffix in VIDEO_SUFFIXES:
                media_start_seconds = item.source_in / PREMIERE_TICKS_PER_SECOND
                command = [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{media_start_seconds:.6f}",
                    "-i",
                    str(source_path),
                            "-map",
                            "0:v:0",
                    "-vf",
                    (
                                "setparams=colorspace=bt709:color_primaries=bt709:"
                                f"color_trc=bt709,fps={fps},scale={width}:{height}:"
                                "in_color_matrix=bt709:out_color_matrix=bt709:"
                                f"force_original_aspect_ratio=increase,crop={width}:{height},"
                        f"trim=end_frame={frames},setpts=N/({fps}*TB),format=yuv420p"
                    ),
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
                    "22",
                    str(segment_path),
                ]
            else:
                raise RuntimeError(f"Unsupported review media: {source_path}")
            _run_ffmpeg(command, f"Review segment {index}/{len(visual_items)}")
            if not segment_path.is_file() or segment_path.stat().st_size <= 0:
                raise RuntimeError(f"Review segment was not created: {segment_path}")
            segments.append(segment_path)
        if total_frames != expected_frames:
            raise RuntimeError(
                f"Review plan has {total_frames} frames, expected {expected_frames}."
            )
        concat_path = temp_dir / "concat.txt"
        concat_path.write_text(
            "\n".join(
                f"file '{str(path).replace(chr(39), chr(39) * 2)}'" for path in segments
            )
            + "\n",
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
                str(concat_path),
                "-an",
                "-r",
                str(fps),
                "-frames:v",
                str(expected_frames),
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
            "Final silent review concat",
        )

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for review QA.") from exc
    capture = cv2.VideoCapture(str(output_path))
    if not capture.isOpened():
        raise RuntimeError(f"Review MP4 could not be opened: {output_path}")
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
        actual_frames != expected_frames
        or not math.isclose(actual_fps, fps, abs_tol=0.01)
        or actual_width != width
        or actual_height != height
        or has_audio
    ):
        raise RuntimeError(
            "Review QA failed: "
            f"frames={actual_frames}, fps={actual_fps}, size={actual_width}x{actual_height}, "
            f"has_audio={has_audio}."
        )
    return {
        "path": str(output_path),
        "frames": actual_frames,
        "fps": actual_fps,
        "width": actual_width,
        "height": actual_height,
        "has_audio_stream": has_audio,
        "source_sequence": output_name,
        "renderer": (
            f"ffmpeg review renderer reading the completed {output_name} timeline and the same "
            "Motion dry-run values written into intrinsic Premiere Motion"
        ),
    }


def _render_protected_segment(
    *,
    project_path: Path,
    sequence_name: str,
    range_start: int,
    range_end: int,
    fps: int,
    width: int,
    height: int,
    output_path: Path,
) -> dict[str, object]:
    root = load_premiere_project_root(project_path)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(
            f"Protected comparison sequence {sequence_name!r} was not found."
        )
    items = [
        item
        for item in _track_item_contexts(
            sequence,
            group_index=0,
            id_lookup=ids,
            uid_lookup=uids,
            project_path=project_path,
        )
        if item.track_index == 1
        and item.start <= range_start
        and item.end >= range_end
        and Path(item.source_path).suffix.lower() in VIDEO_SUFFIXES
    ]
    if len(items) != 1:
        raise PremiereProjectError(
            f"Protected range in {sequence_name!r} must resolve to one video item; "
            f"found {len(items)}."
        )
    item = items[0]
    frames = (range_end - range_start) // _frame_ticks(fps)
    media_in = item.source_in + (range_start - item.start)
    ffmpeg = resolve_ffmpeg_executable()
    _run_ffmpeg(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{media_in / PREMIERE_TICKS_PER_SECOND:.9f}",
            "-i",
            item.source_path,
            "-vf",
            (
                f"fps={fps},scale={width}:{height}:"
                f"force_original_aspect_ratio=increase,crop={width}:{height},"
                f"trim=end_frame={frames},setpts=N/({fps}*TB),format=yuv420p"
            ),
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
            "18",
            str(output_path),
        ],
        f"Protected segment export for {sequence_name}",
    )
    return {
        "sequence_name": sequence_name,
        "clip_name": item.name,
        "source_path": item.source_path,
        "frames": frames,
        "property_snapshot": protected_property_snapshot(item, ids),
    }


def _compare_protected_block(
    *,
    config: dict[str, object],
    source_project_path: Path,
    output_project_path: Path,
    report_path: Path,
) -> dict[str, object]:
    verification = _require_dict(
        config["protected_block_verification"], "protected_block_verification"
    )
    review = _require_dict(config["review_export"], "review_export")
    fps = int(review["frame_rate_fps"])
    size = _require_dict(review["actual_frame_size"], "actual_frame_size")
    width = int(size["width"])
    height = int(size["height"])
    raw_range = verification["range_frames"]
    if not isinstance(raw_range, list) or len(raw_range) != 2:
        raise ValueError("protected_block_verification.range_frames must be [start, end].")
    range_start_frame, range_end_frame = int(raw_range[0]), int(raw_range[1])
    range_start = range_start_frame * _frame_ticks(fps)
    range_end = range_end_frame * _frame_ticks(fps)
    minimum_ssim = float(verification["minimum_ssim_for_same_settings_test_exports"])
    ffmpeg = resolve_ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="premiere_protected_compare_") as temp_text:
        temp_dir = Path(temp_text)
        source_segment = temp_dir / "source.mp4"
        output_segment = temp_dir / "output.mp4"
        source_payload = _render_protected_segment(
            project_path=source_project_path,
            sequence_name=str(verification["source_sequence"]),
            range_start=range_start,
            range_end=range_end,
            fps=fps,
            width=width,
            height=height,
            output_path=source_segment,
        )
        output_payload = _render_protected_segment(
            project_path=output_project_path,
            sequence_name=str(verification["output_sequence"]),
            range_start=range_start,
            range_end=range_end,
            fps=fps,
            width=width,
            height=height,
            output_path=output_segment,
        )
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-i",
                str(source_segment),
                "-i",
                str(output_segment),
                "-lavfi",
                "ssim",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        probe_text = (completed.stderr or "") + (completed.stdout or "")
        match = re.search(r"All:([0-9.]+)", probe_text)
        if match is None:
            raise RuntimeError(
                "Protected-block SSIM output could not be parsed: " + probe_text[-1000:]
            )
        ssim = float(match.group(1))
    snapshots_equal = (
        source_payload["property_snapshot"] == output_payload["property_snapshot"]
    )
    passed = snapshots_equal and ssim >= minimum_ssim
    payload = {
        "task_id": config.get("task_id"),
        "source_project": str(source_project_path),
        "output_project": str(output_project_path),
        "source_sequence": source_payload["sequence_name"],
        "output_sequence": output_payload["sequence_name"],
        "range_frames_exclusive": [range_start_frame, range_end_frame],
        "range_seconds": [
            range_start / PREMIERE_TICKS_PER_SECOND,
            range_end / PREMIERE_TICKS_PER_SECOND,
        ],
        "export_settings": {
            "width": width,
            "height": height,
            "fps": fps,
            "audio": False,
        },
        "source_segment": source_payload,
        "output_segment": output_payload,
        "property_snapshots_equal": snapshots_equal,
        "ssim": ssim,
        "minimum_ssim": minimum_ssim,
        "passed": passed,
    }
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not passed:
        raise PremiereProjectError(
            f"Protected-block comparison failed: snapshots_equal={snapshots_equal}, "
            f"SSIM={ssim:.6f}, required={minimum_ssim:.6f}."
        )
    return payload


def _write_implementation_report(
    path: Path,
    *,
    config_path: Path,
    config: dict[str, object],
) -> None:
    task_id = str(config.get("task_id") or "PREMIERE_SEQUENCE_MOTION")
    lines = [
        f"{task_id}_IMPLEMENTATION",
        "",
        "STATUS: IMPLEMENTED",
        f"Config: {config_path}",
        f"Executor entry point: {EXECUTOR_ENTRY_POINT}",
        f"Automation mechanism: {AUTOMATION_MECHANISM}",
        "",
        "Implemented files:",
        "- models/premiere_sequence_motion.py",
        "- utils/premiere_sequence_motion.py",
        "- main_premiere_import_keep.py",
        "- run_premiere_sequence_motion.bat",
        "- premiere_sequence_motion_template.json",
        "- test/test_premiere_sequence_motion.py",
        "- docs/USER_GUIDE_RU.md",
        "- docs/USER_GUIDE_EN.md",
        "- docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md",
        "- docs/PROJECT_STRUCTURE.md",
        "- .cursor/skills/sequence-optimization-batch/SKILL.md",
        "",
        "Capabilities:",
        "- validates schema_version=1.0 and mode=premiere_sequence_motion_animation",
        "- exact source/output sequence matching and transactional Save As",
        "- deterministic dry-run with per-item profiles and framing safety",
        "- relative intrinsic Motion Scale/Position keyframes",
        "- protected-range property snapshots and frame-exact overlap exclusion",
        "- output-only non-ripple audio clip removal while keeping empty tracks",
        "- JSON-selected motion strength and temporal interpolation",
        "- milestone sequence validation and protected-block SSIM comparison",
        "- structural QA and silent 640x360 review rendering",
        "- existing import/keep dispatch remains unchanged for legacy modes",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_qa_report(
    path: Path,
    *,
    config: dict[str, object],
    source_path: Path,
    output_path: Path,
    source_hash_before: str,
    source_hash_after: str,
    plan: MotionDryRunPlan,
    project_qa: dict[str, object],
    review_qa: dict[str, object],
    protected_qa: dict[str, object] | None,
) -> None:
    sequences = _require_dict(config["sequences"], "sequences")
    task_id = str(config.get("task_id") or "PREMIERE_SEQUENCE_MOTION")
    protected_lines = (
        [
            f"Protected snapshots equal: {protected_qa['property_snapshots_equal']}",
            f"Protected SSIM: {protected_qa['ssim']:.6f} "
            f"(minimum {protected_qa['minimum_ssim']:.6f})",
        ]
        if protected_qa is not None
        else []
    )
    lines = [
        f"{task_id}_QA",
        "",
        "STATUS: STRUCTURAL_PASS_PREMIERE_OPEN_CHECK_REQUIRED",
        f"Task: {config.get('task_id')}",
        f"Source project: {source_path}",
        f"Saved project: {output_path}",
        f"Source SHA256 before/after: {source_hash_before} / {source_hash_after}",
        f"Source sequence: {sequences['source_sequence_name']} (unchanged)",
        f"Output sequence: {sequences['output_sequence_name']} (count=1)",
        f"Picture items: {project_qa['picture_item_count']}",
        f"Animated eligible items: {project_qa['animated_item_count']}",
        f"Already compliant items: {project_qa['already_compliant_item_count']}",
        f"Skipped short items: {project_qa['skipped_short_item_count']}",
        f"Protected items: {project_qa['protected_item_count']}",
        f"Blocked items: {len(plan.blocked_items)}",
        f"Output audio clips: {project_qa['audio_clip_count']}",
        f"Duration: {project_qa['duration_seconds']:.2f} s / {project_qa['frames']} frames",
        f"62.40-66.72 profile: {project_qa['priority_62_40_66_72_profile']}",
        f"Review MP4: {review_qa['path']}",
        f"Review: {review_qa['width']}x{review_qa['height']}, "
        f"{review_qa['fps']:.2f} fps, {review_qa['frames']} frames, "
        f"audio_stream={review_qa['has_audio_stream']}",
        *protected_lines,
        "Premiere open check: requires an interactive Premiere desktop session.",
        "",
        "STRUCTURAL PASS: code support is implemented and tested; the saved-as project contains "
        f"one frame-exact silent {sequences['output_sequence_name']} with relative intrinsic Motion animation, "
        f"{sequences['source_sequence_name']} and "
        "the source project are unchanged, and the silent review has exactly 1996 frames. "
        "Final PASS requires opening the saved project in Premiere without repair prompts.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_premiere_sequence_motion_from_config(
    config_path: Path,
    *,
    dry_run_only: bool = False,
) -> tuple[Path, Path, Path | None]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    config = validate_premiere_sequence_motion_config(payload)
    project = _require_dict(config["project"], "project")
    dry_run_config = _require_dict(config["dry_run"], "dry_run")
    review_config = _require_dict(config["review_export"], "review_export")
    sequences = _require_dict(config["sequences"], "sequences")
    deliverables = (
        _require_dict(config["deliverables"], "deliverables")
        if isinstance(config.get("deliverables"), dict)
        else {}
    )
    project_path = Path(str(project["project_file"]))
    output_path = Path(str(project["save_as_project_file"]))
    reports_dir = output_path.parent
    dry_run_path = reports_dir / str(
        dry_run_config.get("required_plan_filename") or DEFAULT_DRY_RUN_NAME
    )
    implementation_path = reports_dir / str(
        deliverables.get("required_implementation_report")
        or deliverables.get("required_code_change_summary_filename")
        or IMPLEMENTATION_REPORT_NAME
    )
    qa_path = reports_dir / str(
        deliverables.get("required_qa_report")
        or deliverables.get("required_qa_report_filename")
        or QA_REPORT_NAME
    )
    protected_report_path = reports_dir / str(
        deliverables.get("required_protected_comparison_report")
        or "premiere_sequence_motion_protected_compare.json"
    )
    review_path = reports_dir / str(review_config["filename"])

    plan, source_xml, source_hash_before = build_premiere_motion_dry_run(
        config, project_path=project_path
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    dry_run_path.write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_implementation_report(
        implementation_path,
        config_path=config_path,
        config=config,
    )
    if plan.blocked_items:
        raise PremiereProjectError(
            f"BLOCKED: dry-run contains {len(plan.blocked_items)} blocked items. "
            f"See {dry_run_path}"
        )
    if dry_run_only:
        return dry_run_path, implementation_path, None
    if output_path.exists():
        raise PremiereProjectError(
            f"BLOCKED: Save As project already exists and will not be overwritten: {output_path}"
        )
    if review_path.exists():
        raise PremiereProjectError(
            f"BLOCKED: review export already exists and will not be overwritten: {review_path}"
        )

    root = load_premiere_project_root(project_path)
    id_lookup = build_project_object_id_lookup(root)
    uid_lookup = build_project_object_uid_lookup(root)
    source_name = str(sequences["source_sequence_name"])
    output_name = str(sequences["output_sequence_name"])
    clone_named_sequence(
        root,
        source_sequence_name=source_name,
        new_sequence_name=output_name,
        object_id_lookup=id_lookup,
        object_uid_lookup=uid_lookup,
    )
    id_lookup = build_project_object_id_lookup(root)
    uid_lookup = build_project_object_uid_lookup(root)
    output_sequence = find_project_sequence_node(root, output_name)
    if output_sequence is None:
        raise PremiereProjectError("Output sequence clone was not created.")
    output_contexts = _track_item_contexts(
        output_sequence,
        group_index=0,
        id_lookup=id_lookup,
        uid_lookup=uid_lookup,
        project_path=project_path,
    )
    fps = int(_require_dict(config["sequence_contract"], "sequence_contract")["edit_timebase_fps"])
    frame_ticks = _frame_ticks(fps)
    motion_config = _require_dict(config["motion_animation"], "motion_animation")
    interpolation = str(
        motion_config.get("temporal_interpolation") or "BEZIER_EASE_IN_OUT"
    )
    for plan_item in plan.candidate_video_items:
        item = _find_output_item(plan_item, output_contexts)
        params = _motion_params(item.track_item_node, id_lookup)
        if params is None:
            raise PremiereProjectError(
                f"Intrinsic Motion disappeared from cloned item {plan_item.clip_name!r}."
            )
        first_visible = plan_item.source_in_ticks
        last_visible = max(first_visible, plan_item.source_out_ticks - frame_ticks)
        _set_param_keyframes(
            params.scale,
            keyframes=build_scale_keyframes(
                first_visible,
                last_visible,
                plan_item.start_scale,
                plan_item.end_scale,
                interpolation=interpolation,
            ),
            current_value=_format_number(plan_item.end_scale),
        )
        _set_param_keyframes(
            params.position,
            keyframes=build_position_keyframes(
                first_visible,
                last_visible,
                plan_item.start_position_x,
                plan_item.start_position_y,
                plan_item.end_position_x,
                plan_item.end_position_y,
                interpolation=interpolation,
            ),
        )
    removed_audio = _remove_all_audio_clips(
        output_sequence,
        id_lookup=id_lookup,
        uid_lookup=uid_lookup,
    )
    if removed_audio != plan.planned_audio_clip_removal_count:
        raise PremiereProjectError(
            f"Planned to remove {plan.planned_audio_clip_removal_count} audio clips, "
            f"removed {removed_audio}."
        )
    source_node_after = find_project_sequence_node(root, source_name)
    if source_node_after is None or ET.tostring(
        source_node_after, encoding="utf-8"
    ) != source_xml:
        raise PremiereProjectError("Source sequence changed before project write.")
    _write_project(root, output_path)

    source_hash_after = _sha256(project_path)
    if source_hash_after != source_hash_before:
        output_path.unlink(missing_ok=True)
        raise PremiereProjectError("Source project changed during motion execution.")
    project_qa = _verify_output_project(
        config=config,
        project_path=project_path,
        output_path=output_path,
        source_xml=source_xml,
        plan=plan,
    )
    review_qa = _render_review(
        config=config,
        output_project_path=output_path,
        output_path=review_path,
        plan=plan,
    )
    protected_qa = (
        _compare_protected_block(
            config=config,
            source_project_path=project_path,
            output_project_path=output_path,
            report_path=protected_report_path,
        )
        if isinstance(config.get("protected_block_verification"), dict)
        else None
    )
    _write_qa_report(
        qa_path,
        config=config,
        source_path=project_path,
        output_path=output_path,
        source_hash_before=source_hash_before,
        source_hash_after=source_hash_after,
        plan=plan,
        project_qa=project_qa,
        review_qa=review_qa,
        protected_qa=protected_qa,
    )
    return dry_run_path, qa_path, output_path

