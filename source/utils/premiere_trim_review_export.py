from __future__ import annotations

import copy
import gzip
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from models.sequence_trim_review import SequenceTrimReviewResult, TrimClipDecision, TrimSegmentDecision
from utils.premiere_project import (
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
    resolve_project_track_item_subclip,
    resolve_project_track_item_timeline,
)
from utils.premiere_project_export import (
    _ProjectCloneState,
    _ProjectObjectIdAllocator,
    _append_project_item_to_root,
    _find_sequence_masterclip,
    _find_sequence_project_item,
    _insert_project_object_near_same_type,
    _set_child_text,
    _set_project_item_grid_order,
    _set_track_item_boundary,
)


_LABEL_PREFIX_PATTERN = re.compile(
    r"^\[(?:KEEP(?:-(?:HIGH|MEDIUM|REVIEW))?|DROP)\]\s*(?:s\d+\s+)?",
    re.IGNORECASE,
)


def export_trim_review_premiere_project(
    *,
    source_project_path: Path,
    review_result: SequenceTrimReviewResult,
    output_project_path: Path,
    keep_track_index: int = 0,
    drop_track_index: int = 1,
    split_tracks: bool = True,
    hero_level_track_indexes: dict[str, int] | None = None,
) -> tuple[Path, list[str]]:
    return export_trim_review_premiere_projects(
        source_project_path=source_project_path,
        review_results=[review_result],
        output_project_path=output_project_path,
        keep_track_index=keep_track_index,
        drop_track_index=drop_track_index,
        split_tracks=split_tracks,
        hero_level_track_indexes=hero_level_track_indexes,
    )


def export_trim_review_premiere_projects(
    *,
    source_project_path: Path,
    review_results: list[SequenceTrimReviewResult],
    output_project_path: Path,
    keep_track_index: int = 0,
    drop_track_index: int = 1,
    split_tracks: bool = True,
    hero_level_track_indexes: dict[str, int] | None = None,
) -> tuple[Path, list[str]]:
    if not review_results:
        raise PremiereProjectError("No trim-review results were provided for export.")

    root = load_premiere_project_root(source_project_path)
    object_id_lookup = build_project_object_id_lookup(root)
    object_uid_lookup = build_project_object_uid_lookup(root)

    source_sequence_name = review_results[0].source_sequence_name
    source_sequence = find_project_sequence_node(root, source_sequence_name)
    if source_sequence is None:
        raise PremiereProjectError(
            f"Sequence '{source_sequence_name}' was not found in project: {source_project_path}"
        )

    source_masterclip = _find_sequence_masterclip(root, source_sequence_name)
    if source_masterclip is None:
        raise PremiereProjectError(
            f"MasterClip for sequence '{source_sequence_name}' was not found in project."
        )

    source_project_item = _find_sequence_project_item(root, source_masterclip.attrib.get("ObjectUID", ""))
    if source_project_item is None:
        raise PremiereProjectError(
            f"ProjectItem for sequence '{source_sequence_name}' was not found in project."
        )

    source_sequence_uid = source_sequence.attrib.get("ObjectUID", "")
    source_masterclip_uid = source_masterclip.attrib.get("ObjectUID", "")
    source_project_item_uid = source_project_item.attrib.get("ObjectUID", "")
    if not source_sequence_uid or not source_masterclip_uid or not source_project_item_uid:
        raise PremiereProjectError("Source sequence/masterclip/project item is missing ObjectUID values.")

    all_warnings: list[str] = []
    for review_result in review_results:
        if review_result.source_sequence_name != source_sequence_name:
            raise PremiereProjectError(
                "All trim-review results must share the same source_sequence_name for one export."
            )
        object_id_lookup = build_project_object_id_lookup(root)
        object_uid_lookup = build_project_object_uid_lookup(root)
        clone_state = _ProjectCloneState(
            root=root,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
            selected_sequence_uid=source_sequence_uid,
            selected_masterclip_uid=source_masterclip_uid,
        )
        cloned_sequence = clone_state.clone_object_by_uid(source_sequence_uid)
        cloned_masterclip = clone_state.clone_object_by_uid(source_masterclip_uid)
        cloned_project_item = clone_state.clone_object_by_uid(source_project_item_uid)

        _set_child_text(cloned_sequence, "Name", review_result.new_sequence_name)
        _set_child_text(cloned_masterclip, "Name", review_result.new_sequence_name)
        project_item_payload = cloned_project_item.find("./ProjectItem")
        if project_item_payload is None:
            project_item_payload = ET.SubElement(cloned_project_item, "ProjectItem")
        _set_child_text(project_item_payload, "Name", review_result.new_sequence_name)
        _set_project_item_grid_order(root, cloned_project_item)
        _append_project_item_to_root(root, cloned_project_item.attrib["ObjectUID"])

        updated_id_lookup = build_project_object_id_lookup(root)
        updated_uid_lookup = build_project_object_uid_lookup(root)
        warnings = _apply_segment_split_to_sequence(
            root,
            cloned_sequence,
            review_result=review_result,
            object_id_lookup=updated_id_lookup,
            object_uid_lookup=updated_uid_lookup,
            project_path=source_project_path,
            keep_track_index=keep_track_index,
            drop_track_index=drop_track_index,
            split_tracks=split_tracks,
            hero_level_track_indexes=hero_level_track_indexes,
        )
        all_warnings.extend(warnings)

    output_project_path.parent.mkdir(parents=True, exist_ok=True)
    project_xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    output_project_path.write_bytes(gzip.compress(project_xml_bytes))
    return output_project_path, all_warnings


def _apply_segment_split_to_sequence(
    root: ET.Element,
    sequence_node: ET.Element,
    *,
    review_result: SequenceTrimReviewResult,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
    project_path: Path,
    keep_track_index: int,
    drop_track_index: int,
    split_tracks: bool,
    hero_level_track_indexes: dict[str, int] | None,
) -> list[str]:
    warnings: list[str] = []
    decisions_by_key = {
        _decision_match_key(item.name, item.source_path, item.start, item.end): item
        for item in review_result.decisions
    }
    if hero_level_track_indexes:
        _ensure_video_track_count(
            root,
            sequence_node,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
            required_count=max(hero_level_track_indexes.values()) + 1,
        )

    video_tracks = get_project_track_nodes(
        sequence_node,
        track_group_index=0,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    )
    if not video_tracks:
        raise PremiereProjectError("Cloned sequence does not contain video tracks.")

    track_nodes = {track_index: track_node for track_index, track_node in video_tracks}
    can_split_hero_levels = bool(hero_level_track_indexes) and all(
        track_index in track_nodes for track_index in (hero_level_track_indexes or {}).values()
    )
    if hero_level_track_indexes and not can_split_hero_levels:
        warnings.append(
            "Could not place hero levels on separate tracks; one or more requested tracks are missing."
        )
    can_split = split_tracks and keep_track_index in track_nodes and drop_track_index in track_nodes
    if split_tracks and not can_split:
        warnings.append(
            "Could not place KEEP/DROP segments on separate tracks "
            "(need both keep_track_index and drop_track_index). Segments still created on source track."
        )

    id_allocator = _ProjectObjectIdAllocator(root)
    matched_clips = 0
    created_segments = 0

    for track_index, track_node in video_tracks:
        for track_item_ref in list(iter_project_track_item_refs(track_node)):
            object_ref = track_item_ref.attrib.get("ObjectRef")
            if not object_ref:
                continue
            track_item_node = object_id_lookup.get(object_ref)
            if track_item_node is None:
                continue
            start, end = resolve_project_track_item_timeline(track_item_node)
            name = resolve_project_track_item_name(track_item_node, object_id_lookup)
            source_path = resolve_project_track_item_source_path(
                track_item_node,
                object_id_lookup,
                object_uid_lookup,
                project_path=project_path,
            )
            decision = decisions_by_key.get(_decision_match_key(name, source_path, start, end))
            if decision is None:
                continue
            matched_clips += 1

            source_container = _ensure_track_items_container(track_node)
            if source_container is None:
                continue
            if track_item_ref in list(source_container):
                source_container.remove(track_item_ref)

            # An empty segment list intentionally removes this source clip from a
            # filtered review sequence (for example HIGH-only or DROP-only).
            for segment in decision.segments:
                target_track_index = track_index
                if can_split_hero_levels and hero_level_track_indexes:
                    target_track_index = hero_level_track_indexes[_hero_level_key(segment)]
                elif can_split:
                    target_track_index = keep_track_index if segment.decision == "keep" else drop_track_index
                target_track = track_nodes[target_track_index]
                new_item_node, new_ref = _create_segment_track_item(
                    root,
                    template_track_item=track_item_node,
                    decision=decision,
                    segment=segment,
                    object_id_lookup=object_id_lookup,
                    id_allocator=id_allocator,
                )
                object_id_lookup[new_item_node.attrib["ObjectID"]] = new_item_node
                _append_track_item_ref(target_track, new_ref)
                created_segments += 1

            # Drop the old object from active track lists; leave orphan XML node (harmless in Premiere).
            _reindex_track_items(source_container)

    for _track_index, track_node in video_tracks:
        container = track_node.find("./ClipTrack/ClipItems/TrackItems")
        if container is not None:
            _reindex_track_items(container)

    if matched_clips < len(review_result.decisions):
        warnings.append(
            f"Segmented {matched_clips} of {len(review_result.decisions)} clips in the cloned sequence."
        )
    if created_segments == 0:
        warnings.append("No KEEP/DROP segments were written into the review sequence.")
    return warnings


def _ensure_video_track_count(
    root: ET.Element,
    sequence_node: ET.Element,
    *,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
    required_count: int,
) -> None:
    video_tracks = get_project_track_nodes(
        sequence_node,
        track_group_index=0,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    )
    if len(video_tracks) >= required_count:
        return
    if not video_tracks:
        raise PremiereProjectError("Cannot create video tracks without a template track.")

    group_ref_node = sequence_node.find("./TrackGroups/TrackGroup[@Index='0']/Second")
    if group_ref_node is None or not group_ref_node.attrib.get("ObjectRef"):
        raise PremiereProjectError("Video TrackGroup reference is missing.")
    group_node = object_id_lookup.get(group_ref_node.attrib["ObjectRef"])
    if group_node is None:
        raise PremiereProjectError("Video TrackGroup object could not be resolved.")
    tracks_container = group_node.find("./TrackGroup/Tracks")
    if tracks_container is None:
        raise PremiereProjectError("Video TrackGroup does not contain Tracks.")

    template_track = video_tracks[-1][1]
    existing_indexes = {index for index, _track in video_tracks}
    for track_index in range(required_count):
        if track_index in existing_indexes:
            continue
        new_track = copy.deepcopy(template_track)
        new_track_uid = str(uuid4())
        new_track.attrib["ObjectUID"] = new_track_uid
        _clear_track_items(new_track)
        for index_path in (
            "./ClipTrack/ClipItems/Index",
            "./ClipTrack/TransitionItems/Index",
        ):
            index_node = new_track.find(index_path)
            if index_node is not None:
                index_node.text = str(track_index)

        track_ref = ET.Element("Track")
        track_ref.attrib["Index"] = str(track_index)
        track_ref.attrib["ObjectURef"] = new_track_uid
        tracks_container.append(track_ref)
        _insert_project_object_near_same_type(root, new_track)
        object_uid_lookup[new_track_uid] = new_track
        existing_indexes.add(track_index)


def _clear_track_items(track_node: ET.Element) -> None:
    for container_path in (
        "./ClipTrack/ClipItems/TrackItems",
        "./ClipTrack/TransitionItems/TrackItems",
    ):
        container = track_node.find(container_path)
        if container is not None:
            for child in list(container):
                container.remove(child)


def _hero_level_key(segment: TrimSegmentDecision) -> str:
    if segment.decision == "drop":
        return "drop"
    level = segment.hero_match_level.casefold()
    if level == "high":
        return "high"
    if level == "medium":
        return "medium"
    return "review"


def _create_segment_track_item(
    root: ET.Element,
    *,
    template_track_item: ET.Element,
    decision: TrimClipDecision,
    segment: TrimSegmentDecision,
    object_id_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
) -> tuple[ET.Element, ET.Element]:
    template_subclip = resolve_project_track_item_subclip(template_track_item, object_id_lookup)
    template_clip = resolve_project_track_item_clip(template_track_item, object_id_lookup)
    if template_subclip is None or template_clip is None:
        raise PremiereProjectError(f"Could not resolve media objects for clip '{decision.name}'.")

    new_track_item = copy.deepcopy(template_track_item)
    new_subclip = copy.deepcopy(template_subclip)
    new_clip = copy.deepcopy(template_clip)

    new_track_item.attrib["ObjectID"] = id_allocator.allocate()
    new_subclip.attrib["ObjectID"] = id_allocator.allocate()
    new_clip.attrib["ObjectID"] = id_allocator.allocate()

    # Keep ComponentOwner pointing at the original chain (shared defaults are fine for review).
    subclip_ref = new_track_item.find("./ClipTrackItem/SubClip")
    if subclip_ref is None:
        raise PremiereProjectError("Cloned track item is missing SubClip reference.")
    subclip_ref.attrib["ObjectRef"] = new_subclip.attrib["ObjectID"]

    clip_ref = new_subclip.find("./Clip")
    if clip_ref is None:
        raise PremiereProjectError("Cloned SubClip is missing Clip reference.")
    clip_ref.attrib["ObjectRef"] = new_clip.attrib["ObjectID"]

    bare_name = _LABEL_PREFIX_PATTERN.sub("", decision.name).strip() or decision.name
    label = _segment_label(segment)
    _set_child_text(new_subclip, "Name", f"[{label}] s{segment.segment_index} {bare_name}")

    clip_payload = new_clip.find("./Clip")
    if clip_payload is None:
        raise PremiereProjectError("Cloned VideoClip is missing Clip payload.")
    _set_child_text(clip_payload, "InPoint", str(segment.source_in))
    _set_child_text(clip_payload, "OutPoint", str(segment.source_out))
    clip_id_node = clip_payload.find("./ClipID")
    if clip_id_node is not None:
        clip_id_node.text = str(uuid4())

    timeline_node = new_track_item.find("./ClipTrackItem/TrackItem")
    if timeline_node is None:
        raise PremiereProjectError("Cloned track item is missing timeline TrackItem node.")
    _set_track_item_boundary(timeline_node, "Start", segment.timeline_start)
    _set_track_item_boundary(timeline_node, "End", segment.timeline_end)

    _insert_project_object_near_same_type(root, new_track_item)
    _insert_project_object_near_same_type(root, new_subclip)
    _insert_project_object_near_same_type(root, new_clip)

    object_id_lookup[new_subclip.attrib["ObjectID"]] = new_subclip
    object_id_lookup[new_clip.attrib["ObjectID"]] = new_clip

    track_item_ref = ET.Element("TrackItem")
    track_item_ref.attrib["ObjectRef"] = new_track_item.attrib["ObjectID"]
    return new_track_item, track_item_ref


def _segment_label(segment: TrimSegmentDecision) -> str:
    if segment.decision == "keep" and segment.hero_match_level == "high":
        return "KEEP-HIGH"
    if segment.decision == "keep" and segment.hero_match_level == "medium":
        return "KEEP-MEDIUM"
    if segment.decision == "keep" and segment.hero_match_level in {"uncertain", "review"}:
        return "KEEP-REVIEW"
    return "KEEP" if segment.decision == "keep" else "DROP"


def _append_track_item_ref(track_node: ET.Element, track_item_ref: ET.Element) -> None:
    container = _ensure_track_items_container(track_node)
    if container is None:
        raise PremiereProjectError("Target track does not contain a TrackItems container.")
    next_index = max((_safe_int(item.attrib.get("Index")) for item in container.findall("./TrackItem")), default=-1) + 1
    track_item_ref.attrib["Index"] = str(next_index)
    container.append(track_item_ref)


def _ensure_track_items_container(track_node: ET.Element) -> ET.Element | None:
    clip_track = track_node.find("./ClipTrack")
    if clip_track is None:
        clip_track = ET.SubElement(track_node, "ClipTrack")
    clip_items = clip_track.find("./ClipItems")
    if clip_items is None:
        clip_items = ET.SubElement(clip_track, "ClipItems")
        clip_items.attrib["Version"] = "1"
    track_items = clip_items.find("./TrackItems")
    if track_items is None:
        track_items = ET.Element("TrackItems")
        track_items.attrib["Version"] = "1"
        insert_at = 0
        for index, child in enumerate(list(clip_items)):
            if child.tag in {"MediaType", "Index"}:
                insert_at = index
                break
            insert_at = index + 1
        clip_items.insert(insert_at, track_items)
    return track_items


def _reindex_track_items(container: ET.Element) -> None:
    for index, item in enumerate(container.findall("./TrackItem")):
        item.attrib["Index"] = str(index)


def _decision_match_key(name: str, source_path: str, start: int, end: int) -> tuple[str, str, int, int]:
    bare_name = _LABEL_PREFIX_PATTERN.sub("", (name or "").strip()).casefold()
    source_key = Path(source_path).name.casefold() if source_path else ""
    return bare_name, source_key, int(start), int(end)


def _safe_int(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0
