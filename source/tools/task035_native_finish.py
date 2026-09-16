"""TASK035 native Premiere conform: edit -> color -> animation.

The source project and source sequence are read-only.  The approved cloud plans
are conformed into three new sequences in a new .prproj.  All timeline, Lumetri,
Motion and audio operations are performed transactionally on the Premiere XML
graph; Adobe Premiere is used afterwards for the required native open-check and
render.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from utils.premiere_media_import_export import _clone_filter_component
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
    _ProjectObjectIdAllocator,
    _update_sequence_duration_metadata,
    clone_named_sequence,
)
from utils.premiere_sequence_motion import (
    _baseline_position,
    _baseline_scale,
    _motion_params,
    _track_item_contexts,
    _video_settings,
    build_scale_keyframes,
    _set_param_keyframes,
)
from utils.premiere_sequence_timeline_assembly import _validate_all_refs
from utils.premiere_trim_review_export import _reindex_track_items


TASK = "TASK035"
SOURCE = Path(r"<LOCAL_PATH>")
SOURCE_SHA256 = "5558398a02a3248757c52af5771f310ac8095970691d3d35e092f22be2162869"
SOURCE_SEQUENCE = "Felix_Hvr26_v03"
EDIT_SEQUENCE = "TASK035_Felix_Hvr26_EDIT_v01"
COLOR_SEQUENCE = "TASK035_Felix_Hvr26_COLOR_v01"
FINAL_SEQUENCE = "TASK035_Felix_Hvr26_FINAL_v01"
OUT = REPO / "output" / "TASK035_NATIVE_FINISH"
INPUT = OUT / "input"
REPORTS = OUT / "reports"
OUTPUT_PROJECT = OUT / "Felix_Hvr26_3_TASK035_FINAL_v01.prproj"
EDIT_PLAN_PATH = INPUT / "EDIT_PLAN_v01.json"
COLOR_PLAN_PATH = INPUT / "COLOR_PLAN_v01.json"
ANIMATION_PLAN_PATH = INPUT / "ANIMATION_PLAN_v01.json"
SOURCE_MANIFEST_PATH = REPO / "output" / "TASK035_SOURCE_PACKAGE_LOCAL" / "TASK035_SOURCE_MANIFEST_v01.json"
FPS = 25
FRAME_TICKS = PREMIERE_TICKS_PER_SECOND // FPS
WIDTH = 3840
HEIGHT = 2160
EXPECTED_FRAMES = 4116

LUMETRI_PID = {
    "temperature": "7",
    "tint": "8",
    "saturation": "20",
    "exposure": "11",
    "contrast": "12",
    "highlights": "13",
    "shadows": "14",
    "whites": "15",
    "blacks": "16",
    "sharpen": "29",
    "vibrance": "30",
    "vignette": "51",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(name: str, value: object) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def frame(value: float) -> int:
    result = int(round(float(value) * FPS))
    if not math.isclose(result / FPS, float(value), abs_tol=1e-7):
        raise PremiereProjectError(f"Non-frame-exact time at 25 fps: {value}")
    return result


def set_text(node: ET.Element, name: str, value: object) -> None:
    child = node.find(name)
    if child is None:
        child = ET.SubElement(node, name)
    child.text = str(value)


def set_static_param(param: ET.Element, value: object) -> None:
    start = param.find("StartKeyframe")
    if start is None or not (start.text or "").strip():
        raise PremiereProjectError(f"Unreadable StartKeyframe for {param.findtext('Name')!r}")
    parts = start.text.split(",")
    if len(parts) < 2:
        raise PremiereProjectError(f"Malformed StartKeyframe for {param.findtext('Name')!r}")
    parts[1] = str(value)
    start.text = ",".join(parts)
    for tag in ("Keyframes", "IsTimeVarying", "CurrentValue"):
        for child in list(param.findall(tag)):
            param.remove(child)


def contexts(root: ET.Element, sequence_name: str, group: int = 0):
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence not found: {sequence_name}")
    return _track_item_contexts(
        sequence,
        group_index=group,
        id_lookup=build_project_object_id_lookup(root),
        uid_lookup=build_project_object_uid_lookup(root),
        project_path=SOURCE,
    )


def component_pairs(item, ids: dict[str, ET.Element]):
    owner = item.track_item_node.find("ClipTrackItem/ComponentOwner/Components")
    if owner is None:
        return []
    chain = ids.get(owner.get("ObjectRef", ""))
    if chain is None:
        return []
    result = []
    for ref in chain.findall("ComponentChain/Components/Component"):
        component = ids.get(ref.get("ObjectRef", ""))
        if component is not None:
            result.append((ref, component))
    return result


def component_match_name(component: ET.Element) -> str:
    return (
        component.findtext("MatchName")
        or component.findtext("FilterMatchName")
        or component.findtext("AudioComponent/Component/FilterMatchName")
        or component.findtext("VideoComponent/Component/MatchName")
        or ""
    ).strip()


def component_params(component: ET.Element, ids: dict[str, ET.Element]) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    for ref in component.findall(".//Params/Param"):
        param = ids.get(ref.get("ObjectRef", ""))
        if param is not None:
            result[(param.findtext("ParameterID") or "").strip()] = param
    return result


def component_params_by_name(component: ET.Element, ids: dict[str, ET.Element]) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    for ref in component.findall(".//Params/Param"):
        param = ids.get(ref.get("ObjectRef", ""))
        if param is not None:
            result[(param.findtext("Name") or "").strip()] = param
    return result


def remove_components(item, ids: dict[str, ET.Element], match_names: set[str]) -> list[str]:
    owner = item.track_item_node.find("ClipTrackItem/ComponentOwner/Components")
    if owner is None:
        return []
    chain = ids.get(owner.get("ObjectRef", ""))
    if chain is None:
        return []
    container = chain.find("ComponentChain/Components")
    if container is None:
        return []
    removed: list[str] = []
    for ref in list(container):
        component = ids.get(ref.get("ObjectRef", ""))
        match = component_match_name(component) if component is not None else ""
        if match in match_names:
            container.remove(ref)
            removed.append(match)
    for index, ref in enumerate(container):
        ref.set("Index", str(index))
    return removed


def clone_component_to_item(
    root: ET.Element,
    item,
    template: ET.Element,
    ids: dict[str, ET.Element],
    allocator: _ProjectObjectIdAllocator,
) -> ET.Element:
    component = _clone_filter_component(
        root,
        template,
        object_id_lookup=ids,
        template_object_id_lookup=ids,
        id_allocator=allocator,
    )
    owner = item.track_item_node.find("ClipTrackItem/ComponentOwner/Components")
    if owner is None:
        raise PremiereProjectError(f"No component owner: {item.name}")
    chain = ids.get(owner.get("ObjectRef", ""))
    if chain is None:
        raise PremiereProjectError(f"No component chain: {item.name}")
    container = chain.find("ComponentChain/Components")
    if container is None:
        parent = chain.find("ComponentChain")
        if parent is None:
            raise PremiereProjectError(f"Malformed component chain: {item.name}")
        container = ET.SubElement(parent, "Components", {"Version": "1"})
    ET.SubElement(container, "Component", {"Index": str(len(container)), "ObjectRef": component.get("ObjectID", "")})
    return component


def drop_item(item) -> None:
    parent = next((node for node in item.track_node.iter() if item.track_item_ref in list(node)), None)
    if parent is None:
        raise PremiereProjectError(f"TrackItem ref not found for removal: {item.name}")
    parent.remove(item.track_item_ref)
    _reindex_track_items(parent)


def trim_item(item, ids: dict[str, ET.Element], start_frame: int, end_frame: int, source_in_frame: int, source_out_frame: int) -> None:
    track_item = item.track_item_node.find("ClipTrackItem/TrackItem")
    if track_item is None:
        raise PremiereProjectError(f"TrackItem body missing: {item.name}")
    set_text(track_item, "Start", start_frame * FRAME_TICKS)
    set_text(track_item, "End", end_frame * FRAME_TICKS)
    subclip_ref = item.track_item_node.find("ClipTrackItem/SubClip")
    if subclip_ref is None:
        raise PremiereProjectError(f"SubClip missing: {item.name}")
    subclip = ids.get(subclip_ref.get("ObjectRef", ""))
    clip_ref = subclip.find("Clip") if subclip is not None else None
    clip = ids.get(clip_ref.get("ObjectRef", "")) if clip_ref is not None else None
    clip_body = clip.find("Clip") if clip is not None else None
    if clip_body is None:
        raise PremiereProjectError(f"Clip body missing: {item.name}")
    set_text(clip_body, "InPoint", source_in_frame * FRAME_TICKS)
    set_text(clip_body, "OutPoint", source_out_frame * FRAME_TICKS)


def source_closure(root: ET.Element, sequence_name: str) -> dict[str, bytes]:
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence not found: {sequence_name}")
    result: dict[str, bytes] = {}
    pending = [sequence]
    while pending:
        node = pending.pop()
        key = node.get("ObjectID") or node.get("ObjectUID") or f"anon:{id(node)}"
        if key in result:
            continue
        result[key] = ET.tostring(node)
        for child in node.iter():
            ref = child.get("ObjectRef")
            uref = child.get("ObjectURef")
            if ref in ids:
                pending.append(ids[ref])
            if uref in uids:
                pending.append(uids[uref])
    return result


def find_component_template(root: ET.Element, match_name: str) -> ET.Element:
    candidates = [node for node in root.iter() if component_match_name(node) == match_name]
    if not candidates:
        raise PremiereProjectError(f"Component template not found: {match_name}")
    return candidates[0]


def fit_scale(path: Path) -> float:
    with Image.open(path) as image:
        width, height = image.size
    return min(WIDTH / width, HEIGHT / height) * 100.0


def ensure_static_still_motion(root: ET.Element, item, ids: dict[str, ET.Element], allocator: _ProjectObjectIdAllocator, motion_template: ET.Element) -> float:
    motion = _motion_params(item.track_item_node, ids)
    if motion is None:
        clone_component_to_item(root, item, motion_template, ids, allocator)
        motion = _motion_params(item.track_item_node, ids)
    if motion is None:
        raise PremiereProjectError(f"Could not create intrinsic Motion: {item.name}")
    baseline = fit_scale(Path(item.source_path))
    set_static_param(motion.scale, baseline)
    set_static_param(motion.position, "0.5:0.5")
    for _, component in component_pairs(item, ids):
        if component_match_name(component) != "AE.ADBE Motion":
            continue
        by_name = component_params_by_name(component, ids)
        if "Scale Width" in by_name:
            set_static_param(by_name["Scale Width"], baseline)
    return baseline


def conform_edit(root: ET.Element, edit_plan: dict) -> dict:
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    source_items = contexts(root, SOURCE_SEQUENCE, 0)
    if len(source_items) != 65:
        raise PremiereProjectError(f"Expected 65 source video items, found {len(source_items)}")
    plan_items = edit_plan.get("items") or []
    if len(plan_items) != 59:
        raise PremiereProjectError(f"Expected 59 approved edit items, found {len(plan_items)}")
    if frame(edit_plan.get("duration")) != EXPECTED_FRAMES:
        raise PremiereProjectError("Approved edit duration is not 4116 frames")

    by_index = {int(row["index"]): row for row in plan_items}
    for index, row in by_index.items():
        source = source_items[index]
        if source.track_item_node.get("ObjectID") != str(row["track_item_object_id"]):
            raise PremiereProjectError(f"Object ID mismatch at source index {index}")
        if source.name != row["source_name"]:
            raise PremiereProjectError(f"Name mismatch at source index {index}")
        if frame(row["timeline_in"]) != source.start // FRAME_TICKS or frame(row["timeline_out"]) != source.end // FRAME_TICKS:
            raise PremiereProjectError(f"Source timeline mismatch at index {index}")
        if not Path(source.source_path).is_file():
            raise PremiereProjectError(f"Offline source: {source.source_path}")

    clone_named_sequence(
        root,
        source_sequence_name=SOURCE_SEQUENCE,
        new_sequence_name=EDIT_SEQUENCE,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    ids = build_project_object_id_lookup(root)
    cloned = contexts(root, EDIT_SEQUENCE, 0)
    if len(cloned) != len(source_items):
        raise PremiereProjectError("Cloned sequence item count changed")

    operations = []
    for index, item in enumerate(cloned):
        row = by_index.get(index)
        if row is None:
            drop_item(item)
            operations.append({"source_index": index, "name": item.name, "operation": "DROP"})
            continue
        start = frame(row["edit_in"])
        end = frame(row["edit_out"])
        if Path(item.source_path).suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            source_in = item.source_in // FRAME_TICKS
            source_out = source_in + (end - start)
        else:
            source_in = frame(row["source_in"])
            source_out = frame(row["source_out"])
        if source_out - source_in != end - start:
            raise PremiereProjectError(f"Duration mismatch for edit item {row['edit_index']}")
        trim_item(item, ids, start, end, source_in, source_out)
        operations.append({
            "edit_index": int(row["edit_index"]),
            "source_index": index,
            "name": item.name,
            "timeline_frames": [start, end],
            "source_frames": [source_in, source_out],
            "operation": "KEEP" if (start == source_items[index].start // FRAME_TICKS and end == source_items[index].end // FRAME_TICKS) else "CONFORM",
        })

    ids = build_project_object_id_lookup(root)
    allocator = _ProjectObjectIdAllocator(root)
    motion_template = find_component_template(root, "AE.ADBE Motion")
    cleanup = []
    for item in contexts(root, EDIT_SEQUENCE, 0):
        removed = remove_components(item, ids, {"AE.ADBE Lumetri", "AE.Impact_Grow_FX"})
        baseline = None
        if Path(item.source_path).suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            baseline = ensure_static_still_motion(root, item, ids, allocator, motion_template)
        if removed or baseline is not None:
            cleanup.append({"name": item.name, "removed": removed, "static_fit_scale": baseline})

    audio_items = contexts(root, EDIT_SEQUENCE, 1)
    if len(audio_items) != 2:
        raise PremiereProjectError(f"Expected two source music uses, found {len(audio_items)}")
    music = next((item for item in audio_items if item.start == 0), None)
    if music is None:
        raise PremiereProjectError("Opening music clip not found")
    for item in audio_items:
        if item is not music:
            drop_item(item)
    trim_item(music, ids, 0, EXPECTED_FRAMES, 0, EXPECTED_FRAMES)
    gain = 10 ** (-5.0 / 20.0)
    level_param = None
    for _, component in component_pairs(music, ids):
        if component_match_name(component) != "Internal Volume Stereo":
            continue
        for param in component_params_by_name(component, ids).values():
            if (param.findtext("Name") or "").strip() == "Level":
                level_param = param
                break
    if level_param is None:
        raise PremiereProjectError("Music Level parameter not found")
    start_node = level_param.find("StartKeyframe")
    if start_node is None or not (start_node.text or ""):
        raise PremiereProjectError("Music Level StartKeyframe missing")
    start_parts = start_node.text.split(",")
    start_parts[1] = f"{gain:.12f}"
    start_node.text = ",".join(start_parts)
    floor = 0.0000001
    entries = [
        (0, floor),
        (FPS, gain),
        (EXPECTED_FRAMES - 2 * FPS, gain),
        (EXPECTED_FRAMES - 1, floor),
    ]
    keys = "".join(f"{at * FRAME_TICKS},{value:.12f},0,0,0,0,0,0;" for at, value in entries)
    key_node = level_param.find("Keyframes")
    if key_node is None:
        key_node = ET.SubElement(level_param, "Keyframes")
    key_node.text = keys
    varying = level_param.find("IsTimeVarying")
    if varying is None:
        varying = ET.SubElement(level_param, "IsTimeVarying")
    varying.text = "true"

    sequence = find_project_sequence_node(root, EDIT_SEQUENCE)
    _update_sequence_duration_metadata(root, sequence, new_total_duration=EXPECTED_FRAMES * FRAME_TICKS)
    return {
        "sequence": EDIT_SEQUENCE,
        "video_operations": operations,
        "cleanup": cleanup,
        "music": {
            "name": music.name,
            "timeline_frames": [0, EXPECTED_FRAMES],
            "source_frames": [0, EXPECTED_FRAMES],
            "clip_level_db": -5.0,
            "fade_in_frames": FPS,
            "fade_out_frames": 2 * FPS,
        },
    }


def apply_color(root: ET.Element, color_plan: dict) -> dict:
    clone_named_sequence(
        root,
        source_sequence_name=EDIT_SEQUENCE,
        new_sequence_name=COLOR_SEQUENCE,
        object_id_lookup=build_project_object_id_lookup(root),
        object_uid_lookup=build_project_object_uid_lookup(root),
    )
    ids = build_project_object_id_lookup(root)
    allocator = _ProjectObjectIdAllocator(root)
    template = find_component_template(root, "AE.ADBE Lumetri")
    requested = dict(color_plan["global"])
    values = {
        "temperature": float(requested["temperature"]),
        "tint": float(requested["tint"]),
        "saturation": float(requested["saturation"]),
        "exposure": float(requested["exposure"]),
        "contrast": float(requested["contrast"]),
        "highlights": float(requested["highlights"]),
        "shadows": float(requested["shadows"]),
        "whites": float(requested["whites"]),
        "blacks": float(requested["blacks"]),
        "sharpen": 0.0,
        "vibrance": 0.0,
        "vignette": 0.0,
    }
    log = []
    for item in contexts(root, COLOR_SEQUENCE, 0):
        removed = remove_components(item, ids, {"AE.ADBE Lumetri"})
        component = clone_component_to_item(root, item, template, ids, allocator)
        params = component_params(component, ids)
        actual = {}
        for name, value in values.items():
            pid = LUMETRI_PID[name]
            if pid not in params:
                raise PremiereProjectError(f"Lumetri parameter {name}/{pid} missing")
            set_static_param(params[pid], value)
            actual[name] = float((params[pid].findtext("StartKeyframe") or "0,0").split(",")[1])
        if actual != values:
            raise PremiereProjectError(f"Lumetri readback mismatch for {item.name}")
        log.append({"name": item.name, "timeline_frames": [item.start // FRAME_TICKS, item.end // FRAME_TICKS], "removed_existing": removed, "lumetri": actual})
    sequence = find_project_sequence_node(root, COLOR_SEQUENCE)
    _update_sequence_duration_metadata(root, sequence, new_total_duration=EXPECTED_FRAMES * FRAME_TICKS)
    return {"sequence": COLOR_SEQUENCE, "working_space": color_plan.get("working_space"), "items": log}


def apply_animation(root: ET.Element, animation_plan: dict) -> dict:
    clone_named_sequence(
        root,
        source_sequence_name=COLOR_SEQUENCE,
        new_sequence_name=FINAL_SEQUENCE,
        object_id_lookup=build_project_object_id_lookup(root),
        object_uid_lookup=build_project_object_uid_lookup(root),
    )
    ids = build_project_object_id_lookup(root)
    allocator = _ProjectObjectIdAllocator(root)
    motion_template = find_component_template(root, "AE.ADBE Motion")
    all_items = contexts(root, FINAL_SEQUENCE, 0)
    approved = animation_plan.get("items") or []
    if len(approved) != 7:
        raise PremiereProjectError(f"Expected seven approved still animations, found {len(approved)}")
    log = []
    for spec in approved:
        edit_index = int(spec["edit_index"])
        item = all_items[edit_index - 1]
        expected = [frame(spec["edit_in"]), frame(spec["edit_out"])]
        actual_range = [item.start // FRAME_TICKS, item.end // FRAME_TICKS]
        if item.name != spec["source_name"] or actual_range != expected:
            raise PremiereProjectError(f"Animation target mismatch at edit index {edit_index}")
        if Path(item.source_path).suffix.lower() not in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            raise PremiereProjectError(f"Animation target is not a still: {item.name}")
        remove_components(item, ids, {"AE.Impact_Grow_FX"})
        motion = _motion_params(item.track_item_node, ids)
        if motion is None:
            clone_component_to_item(root, item, motion_template, ids, allocator)
            motion = _motion_params(item.track_item_node, ids)
        if motion is None:
            raise PremiereProjectError(f"Motion missing for {item.name}")
        baseline = fit_scale(Path(item.source_path))
        start_scale = baseline * float(spec["scale_start"]) / 100.0
        end_scale = baseline * float(spec["scale_end"]) / 100.0
        source_start = item.source_in
        source_end = item.source_out - FRAME_TICKS
        set_static_param(motion.position, "0.5:0.5")
        set_static_param(motion.scale, start_scale)
        _set_param_keyframes(
            motion.scale,
            keyframes=build_scale_keyframes(
                source_start,
                source_end,
                start_scale,
                end_scale,
                interpolation="BEZIER_EASE_IN_OUT",
            ),
            current_value=str(start_scale),
        )
        for _, component in component_pairs(item, ids):
            if component_match_name(component) != "AE.ADBE Motion":
                continue
            by_name = component_params_by_name(component, ids)
            if "Scale Width" in by_name:
                set_static_param(by_name["Scale Width"], start_scale)
        log.append({
            "edit_index": edit_index,
            "name": item.name,
            "timeline_frames": actual_range,
            "source_keyframe_ticks": [source_start, source_end],
            "normalized_scale_percent": [float(spec["scale_start"]), float(spec["scale_end"])],
            "premiere_scale": [start_scale, end_scale],
            "motion": spec["motion"],
            "interpolation": "BEZIER_EASE_IN_OUT",
        })
    sequence = find_project_sequence_node(root, FINAL_SEQUENCE)
    _update_sequence_duration_metadata(root, sequence, new_total_duration=EXPECTED_FRAMES * FRAME_TICKS)
    return {"sequence": FINAL_SEQUENCE, "items": log}


def validate_media_hashes(edit_plan: dict) -> dict:
    manifest = read_json(SOURCE_MANIFEST_PATH)
    known = {str(row["SourcePath"]).casefold(): row for row in manifest["Media"]}
    root = load_premiere_project_root(SOURCE)
    source_items = contexts(root, SOURCE_SEQUENCE, 0)
    used = {str(Path(source_items[int(row["index"])].source_path)) for row in edit_plan["items"]}
    used.update(item.source_path for item in contexts(root, SOURCE_SEQUENCE, 1))
    results = []
    for raw in sorted(used, key=str.casefold):
        path = Path(raw)
        entry = known.get(str(path).casefold())
        if entry is None:
            raise PremiereProjectError(f"Media absent from source manifest: {path}")
        if not path.is_file():
            raise PremiereProjectError(f"Offline media: {path}")
        actual = sha256(path)
        expected = str(entry["SHA256"]).lower()
        if actual != expected:
            raise PremiereProjectError(f"Media SHA256 mismatch: {path}")
        results.append({"path": str(path), "bytes": path.stat().st_size, "sha256": actual, "status": "PASS"})
    return {"count": len(results), "total_bytes": sum(row["bytes"] for row in results), "items": results}


def sequence_summary(root: ET.Element, sequence_name: str) -> dict:
    video = contexts(root, sequence_name, 0)
    audio = contexts(root, sequence_name, 1)
    ids = build_project_object_id_lookup(root)
    effects = {"motion": 0, "lumetri": 0, "impact_grow": 0, "motion_keyframed": 0}
    offline = []
    for item in video + audio:
        if item.source_path and not Path(item.source_path).is_file():
            offline.append(item.source_path)
    for item in video:
        for _, component in component_pairs(item, ids):
            match = component_match_name(component)
            if match == "AE.ADBE Motion":
                effects["motion"] += 1
                motion = _motion_params(item.track_item_node, ids)
                if motion is not None and (motion.scale.findtext("Keyframes") or "").strip():
                    effects["motion_keyframed"] += 1
            elif match == "AE.ADBE Lumetri":
                effects["lumetri"] += 1
            elif match == "AE.Impact_Grow_FX":
                effects["impact_grow"] += 1
    return {
        "name": sequence_name,
        "video_items": len(video),
        "audio_items": len(audio),
        "video_tracks_used": sorted(set(item.track_index for item in video)),
        "audio_tracks_used": sorted(set(item.track_index for item in audio)),
        "duration_frames": max([item.end // FRAME_TICKS for item in video + audio] or [0]),
        "effects": effects,
        "offline_media": offline,
        "timeline": [
            {
                "index": index + 1,
                "name": item.name,
                "timeline_frames": [item.start // FRAME_TICKS, item.end // FRAME_TICKS],
                "source_frames": [item.source_in // FRAME_TICKS, item.source_out // FRAME_TICKS],
                "path": item.source_path,
            }
            for index, item in enumerate(video)
        ],
    }


def validate_result(
    root: ET.Element,
    source_before: dict[str, bytes],
    animation_plan: dict,
    source_semantic_before: dict | None = None,
) -> dict:
    _validate_all_refs(root)
    source_graph_preserved = source_closure(root, SOURCE_SEQUENCE) == source_before
    source_validation = "BYTE_EXACT"
    if not source_graph_preserved:
        # A native Premiere save rewrites internal object serialization even when
        # the source sequence is untouched.  Accept that normalization only when
        # every timeline/effect/media field in the semantic summary still matches.
        if source_semantic_before is None or sequence_summary(root, SOURCE_SEQUENCE) != source_semantic_before:
            raise PremiereProjectError("Source sequence graph changed")
        source_validation = "SEMANTIC_EXACT_AFTER_PREMIERE_NORMALIZATION"
    summaries = {name: sequence_summary(root, name) for name in (EDIT_SEQUENCE, COLOR_SEQUENCE, FINAL_SEQUENCE)}
    for name, summary in summaries.items():
        if summary["video_items"] != 59 or summary["audio_items"] != 1 or summary["duration_frames"] != EXPECTED_FRAMES:
            raise PremiereProjectError(f"Sequence contract failed: {name}")
        if summary["offline_media"]:
            raise PremiereProjectError(f"Offline media in {name}")
        timeline = summary["timeline"]
        if timeline[0]["timeline_frames"][0] != 0 or timeline[-1]["timeline_frames"][1] != EXPECTED_FRAMES:
            raise PremiereProjectError(f"Timeline boundaries failed: {name}")
        if any(left["timeline_frames"][1] != right["timeline_frames"][0] for left, right in zip(timeline, timeline[1:])):
            raise PremiereProjectError(f"Timeline gap/overlap: {name}")
    if summaries[EDIT_SEQUENCE]["effects"]["lumetri"] != 0 or summaries[EDIT_SEQUENCE]["effects"]["impact_grow"] != 0:
        raise PremiereProjectError("Edit checkpoint contains unapproved color/plugin effects")
    if summaries[COLOR_SEQUENCE]["effects"]["lumetri"] != 59 or summaries[COLOR_SEQUENCE]["effects"]["motion_keyframed"] != 0:
        raise PremiereProjectError("Color checkpoint effect contract failed")
    if summaries[FINAL_SEQUENCE]["effects"]["lumetri"] != 59 or summaries[FINAL_SEQUENCE]["effects"]["motion_keyframed"] != 7 or summaries[FINAL_SEQUENCE]["effects"]["impact_grow"] != 0:
        raise PremiereProjectError("Final effect contract failed")
    final_names = [row["name"] for row in summaries[FINAL_SEQUENCE]["timeline"]]
    expected_names = [row["source_name"] for row in read_json(EDIT_PLAN_PATH)["items"]]
    if final_names != expected_names:
        raise PremiereProjectError("Final clip order differs from approved edit")
    if final_names[-1] != "20250123_203449.jpg":
        raise PremiereProjectError("Final portrait is not M0027")
    return {
        "status": "PASS",
        "source_sequence_validation": source_validation,
        "sequences": summaries,
        "final_portrait": "M0027 / 20250123_203449.jpg",
        "approved_animation_items": len(animation_plan["items"]),
    }


def build_in_memory() -> tuple[ET.Element, dict, dict, dict, dict]:
    if not SOURCE.is_file():
        raise PremiereProjectError(f"Source project missing: {SOURCE}")
    if sha256(SOURCE) != SOURCE_SHA256:
        raise PremiereProjectError("Source project SHA256 changed")
    edit_plan = read_json(EDIT_PLAN_PATH)
    color_plan = read_json(COLOR_PLAN_PATH)
    animation_plan = read_json(ANIMATION_PLAN_PATH)
    root = load_premiere_project_root(SOURCE)
    ids = build_project_object_id_lookup(root)
    source_sequence = find_project_sequence_node(root, SOURCE_SEQUENCE)
    if source_sequence is None:
        raise PremiereProjectError(f"Source sequence missing: {SOURCE_SEQUENCE}")
    settings = _video_settings(source_sequence, ids)
    if settings.get("frame_rate") != str(FRAME_TICKS) or settings.get("frame_rect") != "0,0,3840,2160":
        raise PremiereProjectError(f"Unexpected source settings: {settings}")
    before = source_closure(root, SOURCE_SEQUENCE)
    edit_log = conform_edit(root, edit_plan)
    color_log = apply_color(root, color_plan)
    animation_log = apply_animation(root, animation_plan)
    validation = validate_result(root, before, animation_plan)
    return root, edit_log, color_log, animation_log, validation


def dry_run(verify_hashes: bool) -> dict:
    edit_plan = read_json(EDIT_PLAN_PATH)
    media = validate_media_hashes(edit_plan) if verify_hashes else {"status": "SKIPPED"}
    _, edit_log, color_log, animation_log, validation = build_in_memory()
    result = {
        "status": "PASS",
        "project_written": False,
        "source_project": str(SOURCE),
        "source_sha256": SOURCE_SHA256,
        "source_sequence": SOURCE_SEQUENCE,
        "approved_plan_hashes": {
            "edit": sha256(EDIT_PLAN_PATH),
            "color": sha256(COLOR_PLAN_PATH),
            "animation": sha256(ANIMATION_PLAN_PATH),
        },
        "media_validation": media,
        "edit_items": len([row for row in edit_log["video_operations"] if row["operation"] != "DROP"]),
        "color_items": len(color_log["items"]),
        "animation_items": len(animation_log["items"]),
        "validation": validation,
        "native_adobe_status": "NOT_RUN",
    }
    write_json("TASK035_NATIVE_DRY_RUN.json", result)
    return result


def apply() -> dict:
    if OUTPUT_PROJECT.exists():
        raise PremiereProjectError(f"Output project already exists: {OUTPUT_PROJECT}")
    prior = REPORTS / "TASK035_NATIVE_DRY_RUN.json"
    if not prior.is_file() or read_json(prior).get("status") != "PASS":
        raise PremiereProjectError("A PASS dry-run is required before apply")
    root, edit_log, color_log, animation_log, validation = build_in_memory()
    OUTPUT_PROJECT.parent.mkdir(parents=True, exist_ok=True)
    payload = gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    with OUTPUT_PROJECT.open("xb") as handle:
        handle.write(payload)
    result = {
        "status": "PASS_XML_NATIVE_PENDING",
        "output_project": str(OUTPUT_PROJECT),
        "output_sha256": sha256(OUTPUT_PROJECT),
        "source_project": str(SOURCE),
        "source_sha256_before": SOURCE_SHA256,
        "source_sha256_after": sha256(SOURCE),
        "source_preserved": sha256(SOURCE) == SOURCE_SHA256,
        "edit": edit_log,
        "color": color_log,
        "animation": animation_log,
        "validation": validation,
        "native_adobe_status": "NOT_RUN",
    }
    write_json("TASK035_NATIVE_APPLY.json", result)
    write_json("TASK035_NATIVE_STRUCTURAL_QA.json", validation)
    return result


def verify_existing() -> dict:
    if not OUTPUT_PROJECT.is_file():
        raise PremiereProjectError(f"Output project missing: {OUTPUT_PROJECT}")
    root = load_premiere_project_root(OUTPUT_PROJECT)
    source_root = load_premiere_project_root(SOURCE)
    validation = validate_result(
        root,
        source_closure(source_root, SOURCE_SEQUENCE),
        read_json(ANIMATION_PLAN_PATH),
        source_semantic_before=sequence_summary(source_root, SOURCE_SEQUENCE),
    )
    result = {
        "status": "PASS",
        "output_project": str(OUTPUT_PROJECT),
        "output_sha256": sha256(OUTPUT_PROJECT),
        "source_sha256": sha256(SOURCE),
        "source_preserved": sha256(SOURCE) == SOURCE_SHA256,
        "validation": validation,
    }
    write_json("TASK035_NATIVE_POSTWRITE_READBACK.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("dry-run", "apply", "verify"), required=True)
    parser.add_argument("--skip-media-hashes", action="store_true", help="Skip the 2.3 GB media SHA256 pass during dry-run")
    args = parser.parse_args()
    try:
        if args.stage == "dry-run":
            result = dry_run(not args.skip_media_hashes)
        elif args.stage == "apply":
            result = apply()
        else:
            result = verify_existing()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        failure = {"status": "BLOCKED", "stage": args.stage, "error": str(exc)}
        write_json(f"TASK035_NATIVE_{args.stage.upper().replace('-', '_')}_ERROR.json", failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
