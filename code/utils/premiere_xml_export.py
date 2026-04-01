from __future__ import annotations

import copy
import re
from pathlib import Path
from uuid import uuid4
import xml.etree.ElementTree as ET

from models.video_sequence import SequenceOptimizationResult
from utils.premiere_xml import PremiereXmlError


_STAGE_ITEM_PATTERN = re.compile(
    r"(?P<stage_id>.+?)_(?:bg_image(?:_[A-Za-z0-9]+)*|video_\d+)$",
    re.IGNORECASE,
)


def export_optimized_premiere_xml(
    *,
    source_xml_path: Path,
    optimization_result: SequenceOptimizationResult,
    output_xml_path: Path,
    sequence_suffix: str = "_optimized",
) -> Path:
    if not source_xml_path.exists():
        raise FileNotFoundError(f"Premiere XML file not found: {source_xml_path}")

    tree = ET.parse(source_xml_path)
    root = tree.getroot()
    sequence_node = _find_sequence_node(root, optimization_result.selected_sequence_name)
    if sequence_node is None:
        raise PremiereXmlError(
            f"Sequence '{optimization_result.selected_sequence_name}' was not found in XML: {source_xml_path}"
        )

    ordered_stage_ids = [entry.candidate.clip.stage_id for entry in optimization_result.entries]
    if not ordered_stage_ids:
        raise PremiereXmlError("Optimization result does not contain any sequence entries.")

    optimized_sequence = copy.deepcopy(sequence_node)
    optimized_name = f"{optimization_result.selected_sequence_name}{sequence_suffix}"
    _set_direct_child_text(optimized_sequence, "name", optimized_name)
    _assign_new_sequence_identity(root, optimized_sequence)
    _hydrate_sequence_file_references(root, optimized_sequence)
    _reorder_sequence_tracks(optimized_sequence, ordered_stage_ids)

    replacement_parent = _find_sequence_parent(root, optimization_result.selected_sequence_name)
    if replacement_parent is None:
        raise PremiereXmlError(
            f"Could not find parent node for sequence '{optimization_result.selected_sequence_name}'."
        )
    replacement_index = list(replacement_parent).index(sequence_node)
    replacement_parent.remove(sequence_node)
    replacement_parent.insert(replacement_index, optimized_sequence)

    output_xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_body = ET.tostring(root, encoding="unicode")
    output_xml_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + xml_body,
        encoding="utf-8",
    )
    return output_xml_path


def _reorder_sequence_tracks(sequence_node: ET.Element, ordered_stage_ids: list[str]) -> None:
    stage_id_set = set(ordered_stage_ids)
    spans = _collect_stage_spans(sequence_node, stage_id_set)
    missing_stage_ids = [stage_id for stage_id in ordered_stage_ids if stage_id not in spans]
    if missing_stage_ids:
        missing_display = ", ".join(missing_stage_ids)
        raise PremiereXmlError(f"Could not locate all stage clips in sequence XML. Missing: {missing_display}")

    new_bases: dict[str, int] = {}
    cursor = 0
    for stage_id in ordered_stage_ids:
        new_bases[stage_id] = cursor
        cursor += spans[stage_id]["duration"]

    track_paths = [
        "./media/video/track",
        "./media/audio/track",
    ]
    for track_path in track_paths:
        for track_node in sequence_node.findall(track_path):
            _reorder_track_clipitems(track_node, ordered_stage_ids, spans, new_bases)

    _set_direct_child_text(sequence_node, "duration", str(cursor))


def _reorder_track_clipitems(
    track_node: ET.Element,
    ordered_stage_ids: list[str],
    spans: dict[str, dict[str, int]],
    new_bases: dict[str, int],
) -> None:
    clipitems = list(track_node.findall("./clipitem"))
    if not clipitems:
        return

    matched_items: list[tuple[str, ET.Element]] = []
    unmatched_items: list[ET.Element] = []
    seen_stage_ids: set[str] = set()

    for clipitem in clipitems:
        stage_id = _extract_stage_id_from_clipitem(clipitem)
        if stage_id is None or stage_id not in new_bases:
            unmatched_items.append(clipitem)
            continue
        if stage_id in seen_stage_ids:
            unmatched_items.append(clipitem)
            continue
        matched_items.append((stage_id, clipitem))
        seen_stage_ids.add(stage_id)

    if not matched_items or unmatched_items:
        return

    for clipitem in clipitems:
        track_node.remove(clipitem)

    insert_order: list[ET.Element] = []
    for stage_id in ordered_stage_ids:
        clipitem = next((node for current_stage_id, node in matched_items if current_stage_id == stage_id), None)
        if clipitem is None:
            raise PremiereXmlError(f"Track is missing clipitem for stage '{stage_id}'.")
        original_base = spans[stage_id]["start"]
        new_base = new_bases[stage_id]
        _shift_clipitem_timeline(clipitem, original_base, new_base)
        insert_order.append(clipitem)

    for index, clipitem in enumerate(insert_order):
        track_node.insert(index, clipitem)


def _collect_stage_spans(sequence_node: ET.Element, stage_id_set: set[str]) -> dict[str, dict[str, int]]:
    spans: dict[str, dict[str, int]] = {}
    for track_path in ("./media/video/track", "./media/audio/track"):
        for track_node in sequence_node.findall(track_path):
            for clipitem in track_node.findall("./clipitem"):
                stage_id = _extract_stage_id_from_clipitem(clipitem)
                if stage_id is None or stage_id not in stage_id_set:
                    continue
                start = _safe_int(clipitem.findtext("./start"))
                end = _safe_int(clipitem.findtext("./end"))
                current = spans.get(stage_id)
                if current is None:
                    spans[stage_id] = {"start": start, "end": end, "duration": end - start}
                    continue
                current["start"] = min(current["start"], start)
                current["end"] = max(current["end"], end)
                current["duration"] = current["end"] - current["start"]
    return spans


def _shift_clipitem_timeline(clipitem: ET.Element, original_base: int, new_base: int) -> None:
    for tag_name in ("start", "end"):
        child = clipitem.find(f"./{tag_name}")
        if child is None or child.text is None:
            continue
        original_value = _safe_int(child.text)
        child.text = str(new_base + (original_value - original_base))


def _extract_stage_id_from_clipitem(clipitem: ET.Element) -> str | None:
    name_text = (clipitem.findtext("./name") or "").strip()
    if not name_text:
        file_node = clipitem.find("./file")
        if file_node is not None:
            name_text = (file_node.findtext("./name") or "").strip()
    if not name_text:
        return None
    match = _STAGE_ITEM_PATTERN.match(Path(name_text).stem)
    if match is None:
        return None
    return match.group("stage_id")


def _find_sequence_node(root: ET.Element, sequence_name: str) -> ET.Element | None:
    normalized = sequence_name.casefold()
    for sequence_node in root.findall(".//sequence"):
        if (sequence_node.findtext("./name") or "").casefold() == normalized:
            return sequence_node
    return None


def _find_sequence_parent(root: ET.Element, sequence_name: str) -> ET.Element | None:
    normalized = sequence_name.casefold()
    for parent in root.iter():
        for child in list(parent):
            if child.tag != "sequence":
                continue
            if (child.findtext("./name") or "").casefold() == normalized:
                return parent
    return None


def _hydrate_sequence_file_references(root: ET.Element, sequence_node: ET.Element) -> None:
    file_definitions: dict[str, ET.Element] = {}
    for file_node in root.findall(".//file"):
        file_id = file_node.attrib.get("id")
        if not file_id or not list(file_node):
            continue
        file_definitions.setdefault(file_id, copy.deepcopy(file_node))

    hydrated_ids = {
        file_node.attrib["id"]
        for file_node in sequence_node.findall(".//file")
        if file_node.attrib.get("id") and list(file_node)
    }

    for parent in sequence_node.iter():
        children = list(parent)
        for index, child in enumerate(children):
            if child.tag != "file":
                continue
            file_id = child.attrib.get("id")
            if not file_id or list(child) or file_id in hydrated_ids:
                continue
            definition = file_definitions.get(file_id)
            if definition is None:
                continue
            parent.remove(child)
            parent.insert(index, copy.deepcopy(definition))
            hydrated_ids.add(file_id)


def _assign_new_sequence_identity(root: ET.Element, sequence_node: ET.Element) -> None:
    existing_sequence_numbers = _collect_existing_numeric_ids(root, "sequence")
    next_sequence_number = max(existing_sequence_numbers, default=0) + 1
    if sequence_node.attrib.get("id"):
        sequence_node.attrib["id"] = f"sequence-{next_sequence_number}"
    _remap_element_ids(root, sequence_node, ".//clipitem", "clipitem")
    uuid_node = sequence_node.find("./uuid")
    if uuid_node is not None:
        uuid_node.text = str(uuid4())


def _remap_element_ids(
    root: ET.Element,
    sequence_node: ET.Element,
    xpath: str,
    prefix: str,
) -> dict[str, str]:
    existing_numbers = _collect_existing_numeric_ids(root, prefix)
    next_number = max(existing_numbers, default=0) + 1
    mapping: dict[str, str] = {}

    for node in sequence_node.findall(xpath):
        old_id = node.attrib.get("id")
        if not old_id:
            continue
        if old_id in mapping:
            node.attrib["id"] = mapping[old_id]
            continue
        new_id = f"{prefix}-{next_number}"
        next_number += 1
        mapping[old_id] = new_id
        node.attrib["id"] = new_id

    if prefix == "clipitem":
        _remap_linkcliprefs(sequence_node, mapping)
    return mapping


def _remap_linkcliprefs(sequence_node: ET.Element, mapping: dict[str, str]) -> None:
    if not mapping:
        return
    for link_ref in sequence_node.findall(".//linkclipref"):
        if link_ref.text in mapping:
            link_ref.text = mapping[link_ref.text]


def _collect_existing_numeric_ids(root: ET.Element, prefix: str) -> set[int]:
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    numbers: set[int] = set()
    for node in root.iter():
        node_id = node.attrib.get("id")
        if not node_id:
            continue
        match = pattern.match(node_id)
        if match is None:
            continue
        numbers.add(int(match.group(1)))
    return numbers


def _set_direct_child_text(node: ET.Element, tag_name: str, text_value: str) -> None:
    child = node.find(f"./{tag_name}")
    if child is None:
        child = ET.SubElement(node, tag_name)
    child.text = text_value


def _safe_int(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0
