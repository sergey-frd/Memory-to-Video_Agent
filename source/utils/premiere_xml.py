from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlparse

from models.video_sequence import PremiereSequenceClip


_VIDEO_NAME_PATTERN = re.compile(r"(?P<stage_id>.+?)_video_(?P<video_index>\d+)$", re.IGNORECASE)


class PremiereXmlError(ValueError):
    pass


def parse_premiere_sequence_clips(xml_path: Path, sequence_name: str | None = None) -> tuple[str, list[PremiereSequenceClip]]:
    if not xml_path.exists():
        raise FileNotFoundError(f"Premiere XML file not found: {xml_path}")

    tree = ET.parse(xml_path)
    root = tree.getroot()
    file_lookup = _build_file_lookup(root)

    parsed_sequences: list[tuple[str, list[PremiereSequenceClip]]] = []
    for sequence_node in root.findall(".//sequence"):
        current_sequence_name = _direct_child_text(sequence_node, "name") or "<unnamed-sequence>"
        clips = _parse_sequence_node(sequence_node, current_sequence_name, file_lookup)
        if clips:
            parsed_sequences.append((current_sequence_name, clips))

    if not parsed_sequences:
        raise PremiereXmlError(f"No .mp4 clips found in Premiere XML: {xml_path}")

    if sequence_name:
        normalized_requested = sequence_name.casefold()
        for current_name, clips in parsed_sequences:
            if current_name.casefold() == normalized_requested:
                return current_name, clips
        raise PremiereXmlError(f"Sequence '{sequence_name}' was not found or does not contain .mp4 clips.")

    best_name, best_clips = max(parsed_sequences, key=lambda item: (len(item[1]), item[0]))
    return best_name, best_clips


def _parse_sequence_node(
    sequence_node: ET.Element,
    sequence_name: str,
    file_lookup: dict[str, tuple[str, str]],
) -> list[PremiereSequenceClip]:
    raw_items: list[dict[str, object]] = []
    for track_index, track_node in enumerate(sequence_node.findall("./media/video/track"), start=1):
        for clip_position, clipitem in enumerate(track_node.findall("./clipitem"), start=1):
            file_node = clipitem.find("./file")
            clip_name = (_direct_child_text(clipitem, "name") or "").strip()
            file_name = ""
            source_path = ""

            if file_node is not None:
                file_name = (_direct_child_text(file_node, "name") or "").strip()
                pathurl = (_direct_child_text(file_node, "pathurl") or "").strip()
                if pathurl:
                    source_path = str(_decode_pathurl(pathurl))
                file_id = file_node.attrib.get("id")
                if (not file_name or not source_path) and file_id and file_id in file_lookup:
                    lookup_name, lookup_path = file_lookup[file_id]
                    file_name = file_name or lookup_name
                    source_path = source_path or lookup_path

            resolved_name = clip_name or file_name
            if not resolved_name.lower().endswith(".mp4"):
                continue

            match = _VIDEO_NAME_PATTERN.match(Path(resolved_name).stem)
            if match is None:
                continue

            raw_items.append(
                {
                    "track_index": track_index,
                    "clip_position": clip_position,
                    "clipitem_id": clipitem.attrib.get("id", ""),
                    "name": resolved_name,
                    "source_path": source_path,
                    "start": _safe_int(_direct_child_text(clipitem, "start")),
                    "end": _safe_int(_direct_child_text(clipitem, "end")),
                    "in_point": _safe_int(_direct_child_text(clipitem, "in")),
                    "out_point": _safe_int(_direct_child_text(clipitem, "out")),
                    "duration": _safe_int(_direct_child_text(clipitem, "duration")),
                    "stage_id": match.group("stage_id"),
                    "video_index": int(match.group("video_index")),
                }
            )

    raw_items.sort(key=lambda item: (int(item["start"]), int(item["track_index"]), int(item["clip_position"])))
    clips: list[PremiereSequenceClip] = []
    for order_index, item in enumerate(raw_items, start=1):
        clips.append(
            PremiereSequenceClip(
                sequence_name=sequence_name,
                order_index=order_index,
                track_index=int(item["track_index"]),
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


def _build_file_lookup(root: ET.Element) -> dict[str, tuple[str, str]]:
    lookup: dict[str, tuple[str, str]] = {}
    for file_node in root.findall(".//file"):
        file_id = file_node.attrib.get("id")
        if not file_id:
            continue
        file_name = (_direct_child_text(file_node, "name") or "").strip()
        pathurl = (_direct_child_text(file_node, "pathurl") or "").strip()
        if not pathurl:
            continue
        lookup[file_id] = (file_name, str(_decode_pathurl(pathurl)))
    return lookup


def _decode_pathurl(pathurl: str) -> Path:
    parsed = urlparse(pathurl)
    decoded_path = unquote(parsed.path or pathurl)
    if decoded_path.startswith("/") and re.match(r"^/[A-Za-z]:", decoded_path):
        decoded_path = decoded_path[1:]
    return Path(decoded_path)


def _direct_child_text(node: ET.Element, tag_name: str) -> str | None:
    child = node.find(f"./{tag_name}")
    if child is None or child.text is None:
        return None
    return child.text


def _safe_int(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0
