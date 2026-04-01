from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from models.video_sequence import PremiereSequenceClip


_VIDEO_NAME_PATTERN = re.compile(r"(?P<stage_id>.+?)_video_(?P<video_index>\d+)$", re.IGNORECASE)
_STAGE_ITEM_PATTERN = re.compile(
    r"(?P<stage_id>.+?)_(?:bg_image(?:_[A-Za-z0-9]+)*|video_\d+)$",
    re.IGNORECASE,
)


class PremiereProjectError(ValueError):
    pass


def load_premiere_project_root(project_path: Path) -> ET.Element:
    if not project_path.exists():
        raise FileNotFoundError(f"Premiere project file not found: {project_path}")

    try:
        project_xml_bytes = gzip.decompress(project_path.read_bytes())
    except OSError as exc:
        raise PremiereProjectError(f"Premiere project is not a valid gzip-compressed .prproj file: {project_path}") from exc

    try:
        return ET.fromstring(project_xml_bytes)
    except ET.ParseError as exc:
        raise PremiereProjectError(f"Failed to parse Premiere project XML from: {project_path}") from exc


def parse_premiere_project_sequence_clips(
    project_path: Path,
    sequence_name: str | None = None,
) -> tuple[str, list[PremiereSequenceClip]]:
    root = load_premiere_project_root(project_path)
    object_id_lookup = build_project_object_id_lookup(root)
    object_uid_lookup = build_project_object_uid_lookup(root)

    parsed_sequences: list[tuple[str, list[PremiereSequenceClip]]] = []
    for sequence_node in root.iter("Sequence"):
        current_sequence_name = (sequence_node.findtext("./Name") or "").strip() or "<unnamed-sequence>"
        clips = _parse_project_sequence_node(sequence_node, current_sequence_name, object_id_lookup, object_uid_lookup)
        if clips:
            parsed_sequences.append((current_sequence_name, clips))

    if not parsed_sequences:
        raise PremiereProjectError(f"No .mp4 clips found in Premiere project: {project_path}")

    if sequence_name:
        normalized_requested = sequence_name.casefold()
        for current_name, clips in parsed_sequences:
            if current_name.casefold() == normalized_requested:
                return current_name, clips
        raise PremiereProjectError(f"Sequence '{sequence_name}' was not found or does not contain .mp4 clips.")

    best_name, best_clips = max(parsed_sequences, key=lambda item: (len(item[1]), item[0]))
    return best_name, best_clips


def build_project_object_id_lookup(root: ET.Element) -> dict[str, ET.Element]:
    return {
        node.attrib["ObjectID"]: node
        for node in root.iter()
        if node.attrib.get("ObjectID")
    }


def build_project_object_uid_lookup(root: ET.Element) -> dict[str, ET.Element]:
    return {
        node.attrib["ObjectUID"]: node
        for node in root.iter()
        if node.attrib.get("ObjectUID")
    }


def find_project_sequence_node(root: ET.Element, sequence_name: str) -> ET.Element | None:
    normalized_name = sequence_name.casefold()
    for sequence_node in root.iter("Sequence"):
        if ((sequence_node.findtext("./Name") or "").strip()).casefold() == normalized_name:
            return sequence_node
    return None


def extract_stage_id_from_project_media_name(media_name: str) -> str | None:
    stem = Path(media_name).stem
    match = _STAGE_ITEM_PATTERN.match(stem)
    if match is None:
        return None
    return match.group("stage_id")


def resolve_project_track_item_name(track_item_node: ET.Element, object_id_lookup: dict[str, ET.Element]) -> str:
    subclip_node = resolve_project_track_item_subclip(track_item_node, object_id_lookup)
    if subclip_node is None:
        return ""
    return (subclip_node.findtext("./Name") or "").strip()


def resolve_project_track_item_subclip(
    track_item_node: ET.Element,
    object_id_lookup: dict[str, ET.Element],
) -> ET.Element | None:
    subclip_ref = track_item_node.find("./ClipTrackItem/SubClip")
    if subclip_ref is None:
        return None
    object_ref = subclip_ref.attrib.get("ObjectRef")
    if not object_ref:
        return None
    return object_id_lookup.get(object_ref)


def resolve_project_track_item_clip(
    track_item_node: ET.Element,
    object_id_lookup: dict[str, ET.Element],
) -> ET.Element | None:
    subclip_node = resolve_project_track_item_subclip(track_item_node, object_id_lookup)
    if subclip_node is None:
        return None
    clip_ref_node = subclip_node.find("./Clip")
    if clip_ref_node is None:
        return None
    object_ref = clip_ref_node.attrib.get("ObjectRef")
    if not object_ref:
        return None
    return object_id_lookup.get(object_ref)


def resolve_project_track_item_stage_id(
    track_item_node: ET.Element,
    object_id_lookup: dict[str, ET.Element],
) -> str | None:
    return extract_stage_id_from_project_media_name(resolve_project_track_item_name(track_item_node, object_id_lookup))


def resolve_project_track_item_timeline(track_item_node: ET.Element) -> tuple[int, int]:
    start = _safe_int(track_item_node.findtext("./ClipTrackItem/TrackItem/Start"))
    end = _safe_int(track_item_node.findtext("./ClipTrackItem/TrackItem/End"))
    return start, end


def get_project_track_nodes(
    sequence_node: ET.Element,
    *,
    track_group_index: int,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
) -> list[tuple[int, ET.Element]]:
    group_ref_node = sequence_node.find(f"./TrackGroups/TrackGroup[@Index='{track_group_index}']/Second")
    if group_ref_node is None:
        return []

    group_ref = group_ref_node.attrib.get("ObjectRef")
    if not group_ref:
        return []
    group_node = object_id_lookup.get(group_ref)
    if group_node is None:
        return []

    tracks: list[tuple[int, ET.Element]] = []
    for track_ref in group_node.findall("./TrackGroup/Tracks/Track"):
        track_uid = track_ref.attrib.get("ObjectURef")
        if not track_uid:
            continue
        track_node = object_uid_lookup.get(track_uid)
        if track_node is None:
            continue
        tracks.append((_safe_int(track_ref.attrib.get("Index")), track_node))
    return sorted(tracks, key=lambda item: item[0])


def iter_project_track_item_refs(track_node: ET.Element) -> list[ET.Element]:
    container = track_node.find("./ClipTrack/ClipItems/TrackItems")
    if container is None:
        return []
    return list(container.findall("./TrackItem"))


def _parse_project_sequence_node(
    sequence_node: ET.Element,
    sequence_name: str,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
) -> list[PremiereSequenceClip]:
    track_payloads: list[tuple[int, list[dict[str, object]]]] = []

    for track_index, track_node in get_project_track_nodes(
        sequence_node,
        track_group_index=0,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    ):
        track_items: list[dict[str, object]] = []
        for clip_position, track_item_ref in enumerate(iter_project_track_item_refs(track_node), start=1):
            item_object_ref = track_item_ref.attrib.get("ObjectRef")
            if not item_object_ref:
                continue
            track_item_node = object_id_lookup.get(item_object_ref)
            if track_item_node is None:
                continue

            clip_name = resolve_project_track_item_name(track_item_node, object_id_lookup)
            if not clip_name.lower().endswith(".mp4"):
                continue

            match = _VIDEO_NAME_PATTERN.match(Path(clip_name).stem)
            if match is None:
                continue

            clip_node = resolve_project_track_item_clip(track_item_node, object_id_lookup)
            start, end = resolve_project_track_item_timeline(track_item_node)
            in_point = _safe_int(clip_node.findtext("./Clip/InPoint")) if clip_node is not None else 0
            out_point = _safe_int(clip_node.findtext("./Clip/OutPoint")) if clip_node is not None else end - start

            track_items.append(
                {
                    "track_index": track_index + 1,
                    "clip_position": clip_position,
                    "clipitem_id": item_object_ref,
                    "name": clip_name,
                    "source_path": "",
                    "start": start,
                    "end": end,
                    "in_point": in_point,
                    "out_point": out_point,
                    "duration": end - start,
                    "stage_id": match.group("stage_id"),
                    "video_index": int(match.group("video_index")),
                }
            )

        if track_items:
            track_payloads.append((track_index, track_items))

    if not track_payloads:
        return []

    primary_track_index, raw_items = _select_primary_mp4_track(track_payloads)
    raw_items.sort(key=lambda item: (int(item["start"]), int(item["track_index"]), int(item["clip_position"])))
    clips: list[PremiereSequenceClip] = []
    for order_index, item in enumerate(raw_items, start=1):
        clips.append(
            PremiereSequenceClip(
                sequence_name=sequence_name,
                order_index=order_index,
                track_index=primary_track_index + 1,
                clipitem_id=str(item["clipitem_id"]),
                name=str(item["name"]),
                source_path=str(item["source_path"]),
                start=int(item["start"]),
                end=int(item["end"]),
                in_point=int(item["in_point"]),
                out_point=int(item["out_point"]),
                duration=int(item["duration"]),
                stage_id=str(item["stage_id"]),
                video_index=int(item["video_index"]),
            )
        )
    return clips


def _select_primary_mp4_track(
    track_payloads: list[tuple[int, list[dict[str, object]]]],
) -> tuple[int, list[dict[str, object]]]:
    def sort_key(payload: tuple[int, list[dict[str, object]]]) -> tuple[int, int, int]:
        track_index, items = payload
        unique_stage_count = len({str(item["stage_id"]) for item in items})
        return unique_stage_count, len(items), track_index

    return max(track_payloads, key=sort_key)


def _safe_int(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0
