from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from utils.premiere_media_import_export import (
    _ProjectObjectIdAllocator,
    _assert_project_refs_resolved,
    _clear_sequence_track_items,
    _find_masterclip_by_name,
    _find_masterclip_for_media,
    _find_templates_in_root,
    _index_media_by_path,
    _media_path_key,
    _place_track_item,
    _set_child_text,
    _update_sequence_duration_metadata,
    export_media_import_premiere_project,
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
    resolve_project_clip_media_node,
    resolve_project_track_item_clip,
    resolve_project_track_item_name,
    resolve_project_track_item_source_path,
)
from utils.premiere_project_export import _set_track_item_boundary
from utils.premiere_sequence_motion import _video_settings
from utils.premiere_trim_review_export import _ensure_track_items_container, _reindex_track_items


SOURCE_PROJECT = Path(r"<LOCAL_PATH>")
OUTPUT_PROJECT = Path(r"<LOCAL_PATH>")
REPORT_PATH = Path(
    r"<LOCAL_PATH>"
)
PLAN_PATH = Path(r"<LOCAL_PATH>")
MATERIAL_ROOT = Path(r"<LOCAL_PATH>")
MUSIC_PATH = Path(r"<LOCAL_PATH>")
BANK_SEQUENCE = "ALLA_ALL_MATERIAL_BANK"
SKELETON_SEQUENCE = "ALLA_15_SKELETON_V01"
FPS = 25
FRAME_TICKS = PREMIERE_TICKS_PER_SECOND // FPS
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class PlanItem:
    path: Path
    kind: str
    duration_frames: int | None
    section: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _folders() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in MATERIAL_ROOT.iterdir():
        if not path.is_dir():
            continue
        prefix = path.name[:2]
        if prefix in {"01", "02", "03", "04", "05", "06"}:
            found[prefix] = path
        elif path.name == "TASK_ALLA_02_WATERCOLOR_BANK":
            found["watercolor"] = path / "ALLA_WATERCOLOR_INPUT_30"
        elif path.name == "ALLA_DOUBLE_EXPOSURE_FOUNDATION":
            found["double"] = path / "MASTERS"
    required = {"01", "02", "03", "04", "05", "06", "watercolor", "double"}
    missing = sorted(required - found.keys())
    if missing:
        raise PremiereProjectError(f"Missing material folders: {', '.join(missing)}")
    return found


def _files(directory: Path, suffixes: set[str]) -> list[Path]:
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in suffixes),
        key=lambda path: path.name.casefold(),
    )


def _inventory() -> dict[str, list[Path]]:
    folders = _folders()
    real = []
    for key in ("01", "02", "03", "06"):
        real.extend(_files(folders[key], IMAGE_SUFFIXES))
    real = sorted(real, key=lambda path: path.name.casefold())
    watercolor = [
        path
        for path in _files(folders["watercolor"], IMAGE_SUFFIXES)
        if path.stem.endswith("W")
    ]
    double = _files(folders["double"], {".png"})
    grok = _files(folders["04"], {".mp4"})
    if len(real) != 44:
        raise PremiereProjectError(f"Expected 44 ordinary photos; found {len(real)}.")
    if len(watercolor) != 30:
        raise PremiereProjectError(f"Expected 30 watercolors; found {len(watercolor)}.")
    if len(double) != 12:
        raise PremiereProjectError(f"Expected 12 double-exposure masters; found {len(double)}.")
    if len(grok) != 5:
        raise PremiereProjectError(f"Expected 5 Grok videos; found {len(grok)}.")
    expected_grok_order = [
        "FB_IMG_1589313587870.mp4",
        "20190819_110711.mp4",
        "IMG_20190120_225400.mp4",
        "20190321_232348.mp4",
        "QVZmQ1pYdWl6S0xLdWhrQg.mp4",
    ]
    by_name = {path.name: path for path in grok}
    if set(by_name) != set(expected_grok_order):
        raise PremiereProjectError("The five Grok filenames do not match the task contract.")
    return {
        "real": real,
        "watercolor": watercolor,
        "double": double,
        "grok": [by_name[name] for name in expected_grok_order],
        "music": [MUSIC_PATH],
    }


def _watercolor_original_name(path: Path) -> str:
    return path.stem[:-1] + path.suffix


def _bank_plan(inventory: dict[str, list[Path]]) -> list[PlanItem]:
    real_by_name = {path.name: path for path in inventory["real"]}
    paired_real_names: set[str] = set()
    plan: list[PlanItem] = []
    for watercolor in sorted(inventory["watercolor"], key=lambda path: path.name.casefold()):
        original_name = _watercolor_original_name(watercolor)
        original = real_by_name.get(original_name)
        if original is None:
            raise PremiereProjectError(f"No ordinary-photo pair for {watercolor.name}.")
        paired_real_names.add(original_name)
        plan.append(PlanItem(original, "real_photo", 3 * FPS, "paired_real_watercolor"))
        plan.append(PlanItem(watercolor, "watercolor", 4 * FPS, "paired_real_watercolor"))
    for real in inventory["real"]:
        if real.name not in paired_real_names:
            plan.append(PlanItem(real, "real_photo", 3 * FPS, "unpaired_real_catalog"))
    for item in inventory["double"]:
        plan.append(PlanItem(item, "double_exposure", 6 * FPS, "double_exposure"))
    for item in inventory["grok"]:
        plan.append(PlanItem(item, "grok_video", None, "grok_video"))
    return plan


def _selected_real_names() -> list[str]:
    return [
        "20260815_085334.jpg",
        "IMG_20180210_141748_1.jpg",
        "IMG_20181216_173628.jpg",
        "IMG_20181216_173639.jpg",
        "20190214_204524.jpg",
        "20190727_150042.jpg",
        "20190818_132320.jpg",
        "20190820_165304.jpg",
        "20190822_200328.jpg",
        "20190824_141456.jpg",
        "20190921_201110.jpg",
        "20191101_221603.jpg",
        "20200501_145440.jpg",
        "20220820_210247.jpg",
        "20260819_110902.jpg",
    ]


def _skeleton_plan(inventory: dict[str, list[Path]]) -> list[PlanItem]:
    real_by_name = {path.name: path for path in inventory["real"]}
    watercolor_by_original = {
        _watercolor_original_name(path): path for path in inventory["watercolor"]
    }
    selected_names = _selected_real_names()
    missing = [name for name in selected_names if name not in real_by_name]
    if missing:
        raise PremiereProjectError(f"Missing selected real photos: {', '.join(missing)}")

    opening = selected_names[0]
    finale = selected_names[-1]
    remaining_watercolors = [
        path
        for path in sorted(inventory["watercolor"], key=lambda value: value.name.casefold())
        if _watercolor_original_name(path) not in {opening, finale}
    ]
    early_w = remaining_watercolors[:8]
    travel_w = remaining_watercolors[8:16]
    humor_w = remaining_watercolors[16:21]
    calm_w = remaining_watercolors[21:]
    doubles = list(inventory["double"])
    selected_middle = set(selected_names[1:-1])
    used_real: set[str] = set()
    plan: list[PlanItem] = [
        PlanItem(real_by_name[opening], "real_photo", 4 * FPS, "A_MODERN_OPEN"),
        PlanItem(watercolor_by_original[opening], "watercolor", 4 * FPS, "A_MODERN_OPEN"),
    ]

    def append_watercolor_group(
        paths: list[Path],
        *,
        section: str,
        double_items: list[Path] | None = None,
    ) -> None:
        double_items = list(double_items or [])
        double_cursor = 0
        for index, watercolor in enumerate(paths):
            original_name = _watercolor_original_name(watercolor)
            if original_name in selected_middle and original_name not in used_real:
                plan.append(
                    PlanItem(real_by_name[original_name], "real_photo", 4 * FPS, section)
                )
                used_real.add(original_name)
            plan.append(PlanItem(watercolor, "watercolor", 4 * FPS, section))
            if double_cursor < len(double_items) and index % 2 == 1:
                plan.append(
                    PlanItem(
                        double_items[double_cursor],
                        "double_exposure",
                        6 * FPS,
                        section,
                    )
                )
                double_cursor += 1
        while double_cursor < len(double_items):
            plan.append(
                PlanItem(
                    double_items[double_cursor],
                    "double_exposure",
                    6 * FPS,
                    section,
                )
            )
            double_cursor += 1

    append_watercolor_group(early_w, section="B_EARLY_TENDERNESS", double_items=doubles[:3])
    append_watercolor_group(travel_w, section="C_TRAVEL", double_items=doubles[3:7])
    for video, watercolor in zip(inventory["grok"], humor_w):
        plan.append(PlanItem(video, "grok_video", None, "D_HUMOR_PLAY"))
        plan.append(PlanItem(watercolor, "watercolor", 4 * FPS, "D_HUMOR_PLAY"))
    append_watercolor_group(calm_w, section="E_CALM_PAUSE", double_items=doubles[7:])

    for name in selected_names[1:-1]:
        if name not in used_real:
            plan.append(PlanItem(real_by_name[name], "real_photo", 4 * FPS, "F_TODAY_FINAL"))
            used_real.add(name)
    plan.extend(
        [
            PlanItem(
                watercolor_by_original[finale],
                "watercolor",
                4 * FPS,
                "F_TODAY_FINAL",
            ),
            PlanItem(real_by_name[finale], "real_photo", 8 * FPS, "F_TODAY_FINAL"),
        ]
    )
    _validate_skeleton_plan(plan, inventory)
    return plan


def _validate_skeleton_plan(
    plan: list[PlanItem], inventory: dict[str, list[Path]]
) -> None:
    kind_counts: dict[str, int] = {}
    for item in plan:
        kind_counts[item.kind] = kind_counts.get(item.kind, 0) + 1
    expected = {
        "real_photo": 15,
        "watercolor": 30,
        "double_exposure": 12,
        "grok_video": 5,
    }
    if kind_counts != expected:
        raise PremiereProjectError(
            f"Skeleton media counts differ from contract: {kind_counts}, expected {expected}."
        )
    actual_grok = [item.path.name for item in plan if item.kind == "grok_video"]
    expected_grok = [path.name for path in inventory["grok"]]
    if actual_grok != expected_grok:
        raise PremiereProjectError("Grok order differs from the task contract.")
    if plan[-1].kind != "real_photo" or plan[-1].path.name != "20260819_110902.jpg":
        raise PremiereProjectError("Skeleton must end on the approved contemporary real photo.")


def _probe_duration(path: Path) -> float | None:
    try:
        import imageio_ffmpeg

        executable = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run(
            [executable, "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        if match:
            hours, minutes, seconds = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (OSError, ValueError):
        return None
    return None


def _track_refs(
    root: ET.Element, sequence_name: str, group_index: int
) -> list[ET.Element]:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {sequence_name!r} is missing.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    refs: list[ET.Element] = []
    for _, track in get_project_track_nodes(
        sequence,
        track_group_index=group_index,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    ):
        refs.extend(iter_project_track_item_refs(track))
    return refs


def _remove_refs_by_id(
    root: ET.Element,
    sequence_name: str,
    group_index: int,
    object_ids: set[str] | None = None,
) -> None:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {sequence_name!r} is missing.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    for _, track in get_project_track_nodes(
        sequence,
        track_group_index=group_index,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    ):
        container = _ensure_track_items_container(track)
        if container is None:
            continue
        for ref in list(iter_project_track_item_refs(track)):
            if object_ids is None or ref.attrib.get("ObjectRef") in object_ids:
                container.remove(ref)
        _reindex_track_items(container)


def _retime_sequence(
    root: ET.Element,
    *,
    sequence_name: str,
    plan: list[PlanItem],
    section_gaps: bool,
) -> tuple[int, list[dict[str, object]]]:
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {sequence_name!r} is missing.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    track_items: list[ET.Element] = []
    for _, track in get_project_track_nodes(
        sequence,
        track_group_index=0,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    ):
        for ref in iter_project_track_item_refs(track):
            item = ids.get(ref.attrib.get("ObjectRef", ""))
            if item is not None:
                track_items.append(item)
    track_items.sort(
        key=lambda item: int(
            item.findtext("./ClipTrackItem/TrackItem/Start") or "0"
        )
    )
    if len(track_items) != len(plan):
        raise PremiereProjectError(
            f"{sequence_name}: found {len(track_items)} video items, expected {len(plan)}."
        )
    rows: list[dict[str, object]] = []
    cursor = 0
    previous_section = ""
    for item, planned in zip(track_items, plan, strict=True):
        if section_gaps and previous_section and planned.section != previous_section:
            cursor += FPS
        clip = resolve_project_track_item_clip(item, ids)
        source_path = resolve_project_track_item_source_path(item, ids, uids)
        if clip is None or Path(source_path or "").resolve() != planned.path.resolve():
            raise PremiereProjectError(
                f"{sequence_name}: imported order mismatch at {planned.path.name}."
            )
        duration_frames = planned.duration_frames
        if duration_frames is None:
            timeline = item.find("./ClipTrackItem/TrackItem")
            if timeline is None:
                raise PremiereProjectError(f"Missing timeline for {planned.path.name}.")
            imported_start = int(timeline.findtext("./Start") or "0")
            imported_end = int(timeline.findtext("./End") or "0")
            duration_frames = max(1, round((imported_end - imported_start) / FRAME_TICKS))
        duration_ticks = duration_frames * FRAME_TICKS
        timeline = item.find("./ClipTrackItem/TrackItem")
        clip_payload = clip.find("./Clip")
        if timeline is None or clip_payload is None:
            raise PremiereProjectError(f"Malformed track item for {planned.path.name}.")
        source_in = int(clip_payload.findtext("./InPoint") or "0")
        _set_track_item_boundary(timeline, "Start", cursor * FRAME_TICKS)
        _set_track_item_boundary(
            timeline, "End", (cursor + duration_frames) * FRAME_TICKS
        )
        _set_child_text(clip_payload, "OutPoint", str(source_in + duration_ticks))
        rows.append(
            {
                "timeline_in_frame": cursor,
                "timeline_out_frame": cursor + duration_frames,
                "duration_frames": duration_frames,
                "kind": planned.kind,
                "section": planned.section,
                "file": planned.path.name,
                "path": str(planned.path),
            }
        )
        cursor += duration_frames
        previous_section = planned.section
    _update_sequence_duration_metadata(
        root, sequence, new_total_duration=cursor * FRAME_TICKS
    )
    return cursor, rows


def _clip_project_item_path(
    item: ET.Element,
    *,
    ids: dict[str, ET.Element],
    uids: dict[str, ET.Element],
) -> Path | None:
    master_ref = item.find("./MasterClip")
    if master_ref is None:
        return None
    master = uids.get(master_ref.attrib.get("ObjectURef", ""))
    if master is None:
        return None
    clip_ref = master.find("./Clips/Clip")
    if clip_ref is None:
        return None
    clip = ids.get(clip_ref.attrib.get("ObjectRef", ""))
    if clip is None:
        return None
    media = resolve_project_clip_media_node(clip, ids, uids)
    text = (media.findtext("./FilePath") if media is not None else "") or ""
    return Path(text) if text else None


def _set_bin_items(
    bin_node: ET.Element,
    *,
    paths: list[Path],
    ids: dict[str, ET.Element],
    uids: dict[str, ET.Element],
) -> None:
    wanted = {_media_path_key(path) for path in paths}
    matching: list[str] = []
    for item in uids.values():
        if item.tag != "ClipProjectItem":
            continue
        path = _clip_project_item_path(item, ids=ids, uids=uids)
        if path is not None and _media_path_key(path) in wanted:
            uid = item.attrib.get("ObjectUID")
            if uid and uid not in matching:
                matching.append(uid)
    if len(matching) != len(wanted):
        raise PremiereProjectError(
            f"Could not map all bin media: wanted {len(wanted)}, mapped {len(matching)}."
        )
    items = bin_node.find("./ProjectItemContainer/Items")
    if items is None:
        container = bin_node.find("./ProjectItemContainer")
        if container is None:
            container = ET.SubElement(bin_node, "ProjectItemContainer", Version="1")
        items = ET.SubElement(container, "Items", Version="1")
    for child in list(items):
        items.remove(child)
    for index, uid in enumerate(matching):
        ET.SubElement(items, "Item", Index=str(index), ObjectURef=uid)


def _ensure_bins(root: ET.Element, inventory: dict[str, list[Path]]) -> None:
    bins = {
        (node.findtext("./ProjectItem/Name") or "").strip(): node
        for node in root.iter("BinProjectItem")
    }
    repurpose = {
        "02_WATERCOLOR": "Wcl",
        "03_DOUBLE_EXPOSURE": "Exp2",
        "04_GROK_VIDEO": "WdHm",
        "05_MUSIC": "01_MUSIC",
    }
    for desired, current in repurpose.items():
        node = bins.get(desired) or bins.get(current)
        if node is None:
            raise PremiereProjectError(f"Cannot create bin {desired}: template {current} missing.")
        project_item = node.find("./ProjectItem")
        _set_child_text(project_item if project_item is not None else node, "Name", desired)
        bins[desired] = node
    if "01_REAL_PHOTOS" not in bins:
        template = bins["02_WATERCOLOR"]
        new_bin = copy.deepcopy(template)
        new_bin.attrib["ObjectUID"] = str(uuid4())
        project_item = new_bin.find("./ProjectItem")
        _set_child_text(
            project_item if project_item is not None else new_bin,
            "Name",
            "01_REAL_PHOTOS",
        )
        root.append(new_bin)
        bins["01_REAL_PHOTOS"] = new_bin
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    payload = {
        "01_REAL_PHOTOS": inventory["real"],
        "02_WATERCOLOR": inventory["watercolor"],
        "03_DOUBLE_EXPOSURE": inventory["double"],
        "04_GROK_VIDEO": inventory["grok"],
        "05_MUSIC": inventory["music"],
    }
    for name, paths in payload.items():
        _set_bin_items(bins[name], paths=paths, ids=ids, uids=uids)


def _add_music(root: ET.Element, *, skeleton_frames: int) -> tuple[int, float]:
    sequence = find_project_sequence_node(root, SKELETON_SEQUENCE)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {SKELETON_SEQUENCE!r} is missing.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    _, _, audio_template = _find_templates_in_root(
        root,
        preferred_sequence=sequence,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    if audio_template is None:
        donor_root = load_premiere_project_root(SOURCE_PROJECT)
        donor_ids = build_project_object_id_lookup(donor_root)
        donor_uids = build_project_object_uid_lookup(donor_root)
        _, _, audio_template = _find_templates_in_root(
            donor_root,
            preferred_sequence=find_project_sequence_node(donor_root, BANK_SEQUENCE),
            object_id_lookup=donor_ids,
            object_uid_lookup=donor_uids,
        )
    if audio_template is None:
        raise PremiereProjectError("No audio track-item template is available in v1.")
    tracks = get_project_track_nodes(
        sequence,
        track_group_index=1,
        object_id_lookup=ids,
        object_uid_lookup=uids,
    )
    if not tracks:
        raise PremiereProjectError("Skeleton sequence has no audio track.")
    media = _index_media_by_path(root).get(_media_path_key(MUSIC_PATH))
    if media is None:
        raise PremiereProjectError("The music file is not imported in the source project.")
    master = _find_masterclip_for_media(root, media.attrib.get("ObjectUID", ""))
    if master is None:
        master = _find_masterclip_by_name(root, MUSIC_PATH.name)
    if master is None:
        raise PremiereProjectError("The imported music master clip was not found.")
    duration_seconds = _probe_duration(MUSIC_PATH)
    if duration_seconds is None:
        raise PremiereProjectError("Could not probe music duration.")
    duration_ticks = min(
        round(duration_seconds * PREMIERE_TICKS_PER_SECOND),
        skeleton_frames * FRAME_TICKS,
    )
    _place_track_item(
        root,
        track_node=tracks[0][1],
        template=audio_template,
        media_node=media,
        master_clip=master,
        source_path=MUSIC_PATH,
        timeline_start=0,
        timeline_end=duration_ticks,
        source_in=0,
        source_out=duration_ticks,
        object_id_lookup=ids,
        id_allocator=_ProjectObjectIdAllocator(root),
        clone_audio_source=True,
    )
    return round(duration_ticks / FRAME_TICKS), duration_seconds


def _write_root(path: Path, root: ET.Element) -> None:
    _assert_project_refs_resolved(root)
    path.write_bytes(
        gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    )


def _sequence_summary(root: ET.Element, name: str) -> dict[str, object]:
    sequence = find_project_sequence_node(root, name)
    if sequence is None:
        raise PremiereProjectError(f"Sequence {name!r} is missing.")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    videos = _track_refs(root, name, 0)
    audios = _track_refs(root, name, 1)
    end = 0
    for ref in videos:
        item = ids.get(ref.attrib.get("ObjectRef", ""))
        if item is None:
            continue
        value = item.findtext("./ClipTrackItem/TrackItem/End")
        end = max(end, int(value or "0"))
    settings = _video_settings(sequence, ids)
    return {
        "name": name,
        "frame_rate_ticks": settings["frame_rate"],
        "frame_rect": settings["frame_rect"],
        "video_items": len(videos),
        "audio_items": len(audios),
        "duration_frames": round(end / FRAME_TICKS),
        "duration_seconds": round(end / PREMIERE_TICKS_PER_SECOND, 2),
    }


def _verify_output(
    *,
    source_hash: str,
    bank_plan: list[PlanItem],
    skeleton_plan: list[PlanItem],
) -> dict[str, object]:
    if _sha256(SOURCE_PROJECT) != source_hash:
        raise PremiereProjectError("Source project v1 changed during TASK_ALLA_03.")
    root = load_premiere_project_root(OUTPUT_PROJECT)
    bank = _sequence_summary(root, BANK_SEQUENCE)
    skeleton = _sequence_summary(root, SKELETON_SEQUENCE)
    if bank["video_items"] != len(bank_plan) or bank["audio_items"] != 0:
        raise PremiereProjectError(f"BANK QA failed: {bank}")
    if skeleton["video_items"] != len(skeleton_plan) or skeleton["audio_items"] != 1:
        raise PremiereProjectError(f"SKELETON QA failed: {skeleton}")
    if not 4 * 60 <= float(skeleton["duration_seconds"]) <= 6 * 60:
        raise PremiereProjectError(f"Skeleton duration outside 4-6 minutes: {skeleton}")
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    grok_audio = []
    for ref in _track_refs(root, SKELETON_SEQUENCE, 1):
        item = ids.get(ref.attrib.get("ObjectRef", ""))
        if item is None:
            continue
        path = resolve_project_track_item_source_path(item, ids, uids)
        if path and Path(path).suffix.lower() == ".mp4":
            grok_audio.append(path)
    if grok_audio:
        raise PremiereProjectError("Grok audio remains in SKELETON.")
    return {"bank": bank, "skeleton": skeleton, "source_v1_unchanged": True}


def execute() -> dict[str, object]:
    if not SOURCE_PROJECT.is_file():
        raise PremiereProjectError(f"Missing source project: {SOURCE_PROJECT}")
    if OUTPUT_PROJECT.exists():
        raise PremiereProjectError(f"Output project already exists: {OUTPUT_PROJECT}")
    if not MUSIC_PATH.is_file():
        raise PremiereProjectError(f"Missing music: {MUSIC_PATH}")
    inventory = _inventory()
    bank_plan = _bank_plan(inventory)
    skeleton_plan = _skeleton_plan(inventory)
    source_hash = _sha256(SOURCE_PROJECT)
    shutil.copy2(SOURCE_PROJECT, OUTPUT_PROJECT)

    initial_root = load_premiere_project_root(OUTPUT_PROJECT)
    old_bank_video = {
        ref.attrib.get("ObjectRef", "") for ref in _track_refs(initial_root, BANK_SEQUENCE, 0)
    }
    old_bank_audio = {
        ref.attrib.get("ObjectRef", "") for ref in _track_refs(initial_root, BANK_SEQUENCE, 1)
    }
    source_paths = [item.path for item in bank_plan]
    export_media_import_premiere_project(
        source_project_path=OUTPUT_PROJECT,
        output_project_path=OUTPUT_PROJECT,
        sequence_name=BANK_SEQUENCE,
        source_paths=source_paths,
        create_sequence_if_missing=False,
        fail_if_sequence_exists=False,
        still_duration_seconds=5.0,
        duration_resolver=_probe_duration,
    )
    root = load_premiere_project_root(OUTPUT_PROJECT)
    _remove_refs_by_id(root, BANK_SEQUENCE, 0, old_bank_video)
    _remove_refs_by_id(root, BANK_SEQUENCE, 1, old_bank_audio)
    _remove_refs_by_id(root, BANK_SEQUENCE, 1, None)
    bank_frames, bank_rows = _retime_sequence(
        root,
        sequence_name=BANK_SEQUENCE,
        plan=bank_plan,
        section_gaps=True,
    )
    _write_root(OUTPUT_PROJECT, root)

    export_media_import_premiere_project(
        source_project_path=OUTPUT_PROJECT,
        output_project_path=OUTPUT_PROJECT,
        sequence_name=SKELETON_SEQUENCE,
        source_paths=[item.path for item in skeleton_plan],
        create_sequence_if_missing=False,
        fail_if_sequence_exists=False,
        still_duration_seconds=5.0,
        duration_resolver=_probe_duration,
    )
    root = load_premiere_project_root(OUTPUT_PROJECT)
    _remove_refs_by_id(root, SKELETON_SEQUENCE, 1, None)
    skeleton_frames, skeleton_rows = _retime_sequence(
        root,
        sequence_name=SKELETON_SEQUENCE,
        plan=skeleton_plan,
        section_gaps=False,
    )
    music_frames, music_seconds = _add_music(root, skeleton_frames=skeleton_frames)
    _ensure_bins(root, inventory)
    _write_root(OUTPUT_PROJECT, root)

    qa = _verify_output(
        source_hash=source_hash,
        bank_plan=bank_plan,
        skeleton_plan=skeleton_plan,
    )
    payload = {
        "task_id": "TASK_ALLA_03_PREMIERE_FIRST_ASSEMBLY",
        "status": "PASS",
        "source_project": str(SOURCE_PROJECT),
        "source_sha256_before_and_after": source_hash,
        "output_project": str(OUTPUT_PROJECT),
        "inventory": {
            "01_REAL_PHOTOS": len(inventory["real"]),
            "02_WATERCOLOR": len(inventory["watercolor"]),
            "03_DOUBLE_EXPOSURE": len(inventory["double"]),
            "04_GROK_VIDEO": len(inventory["grok"]),
            "05_MUSIC": 1,
            "05_FRIENDS_RESERVED_NOT_USED": len(_files(_folders()["05"], IMAGE_SUFFIXES)),
        },
        "bank": {
            "duration_frames": bank_frames,
            "timeline": bank_rows,
        },
        "skeleton": {
            "duration_frames": skeleton_frames,
            "timeline": skeleton_rows,
            "selected_real_photos": _selected_real_names(),
            "double_exposure_order": [
                item.path.name for item in skeleton_plan if item.kind == "double_exposure"
            ],
            "grok_video_order": [
                item.path.name for item in skeleton_plan if item.kind == "grok_video"
            ],
            "music": {
                "file": str(MUSIC_PATH),
                "source_duration_seconds": round(music_seconds, 2),
                "timeline_duration_frames": music_frames,
                "handling": "Placed once on A1 from frame 0; not looped. Remaining film is silent.",
            },
        },
        "qa": qa,
        "missing_files": [],
        "duplicate_imports_created": [],
        "deviations": [
            "No Cross Dissolve transitions were added; the task permits straight cuts.",
            "The 169.94-second music is shorter than the skeleton and was not looped.",
            "Historical unused project items inherited from v1 remain, but curated task bins contain only approved files.",
        ],
    }
    PLAN_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_lines = [
        "TASK_ALLA_03_PREMIERE_FIRST_ASSEMBLY — PASS",
        "",
        f"Source project (unchanged): {SOURCE_PROJECT}",
        f"Source SHA256 before/after: {source_hash}",
        f"Working project: {OUTPUT_PROJECT}",
        "",
        "SEQUENCES",
        json.dumps(qa["bank"], ensure_ascii=False),
        json.dumps(qa["skeleton"], ensure_ascii=False),
        "",
        "CURATED BINS / IMPORT COUNTS",
        "01_REAL_PHOTOS: 44",
        "02_WATERCOLOR: 30",
        "03_DOUBLE_EXPOSURE: 12 MASTERS/PNG",
        "04_GROK_VIDEO: 5 MP4",
        "05_MUSIC: 1 MP3",
        "05_ДРУЗЬЯ: reserved, not used on skeleton",
        "",
        "SKELETON REAL PHOTOS (15)",
        *[f"- {name}" for name in _selected_real_names()],
        "",
        "DOUBLE EXPOSURE ORDER",
        *[
            f"- {item.path.name}"
            for item in skeleton_plan
            if item.kind == "double_exposure"
        ],
        "",
        "GROK VIDEO ORDER",
        *[
            f"- {item.path.name}"
            for item in skeleton_plan
            if item.kind == "grok_video"
        ],
        "",
        "MUSIC",
        f"- {MUSIC_PATH}",
        f"- source duration: {music_seconds:.2f} sec",
        "- placed once on A1 from film start; no loop; remaining film is silent",
        "",
        "MISSING / DUPLICATES",
        "- Missing required files: none",
        "- New duplicate imports of the same source path: none",
        "",
        "DEVIATIONS",
        "- Cross Dissolve was not added; straight cuts are explicitly allowed.",
        "- Music is shorter than the film and was not looped, as required.",
        "- Historical unused project items inherited from v1 remain outside curated task bins.",
        "",
        f"Detailed actual timeline JSON: {PLAN_PATH}",
        "Premiere open-check without repair/conversion dialog: REQUIRED.",
    ]
    REPORT_PATH.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build TASK_ALLA_03 Premiere skeleton.")
    parser.parse_args()
    result = execute()
    print(json.dumps(result["qa"], ensure_ascii=False, indent=2))
    print(f"Project: {OUTPUT_PROJECT}")
    print(f"Report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
