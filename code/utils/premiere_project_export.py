from __future__ import annotations

import copy
import gzip
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from models.video_sequence import SequenceOptimizationResult
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
    resolve_project_track_item_stage_id,
    resolve_project_track_item_timeline,
)

_UUID_TEXT_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_DEFAULT_VIDEO_TRANSITION_MEDIA_TYPE = "228cda18-3625-4d2d-951e-348879e4ed93"


def export_optimized_premiere_project(
    *,
    source_project_path: Path,
    optimization_result: SequenceOptimizationResult,
    output_project_path: Path,
    enable_auto_transitions: bool = False,
    allow_transition_handle_trimming: bool = False,
) -> Path:
    root = load_premiere_project_root(source_project_path)
    object_id_lookup = build_project_object_id_lookup(root)
    object_uid_lookup = build_project_object_uid_lookup(root)

    sequence_node = find_project_sequence_node(root, optimization_result.selected_sequence_name)
    if sequence_node is None:
        raise PremiereProjectError(
            f"Sequence '{optimization_result.selected_sequence_name}' was not found in project: {source_project_path}"
        )

    ordered_stage_ids = [entry.candidate.clip.stage_id for entry in optimization_result.entries]
    if not ordered_stage_ids:
        raise PremiereProjectError("Optimization result does not contain any sequence entries.")

    new_total_duration = _reorder_project_sequence_tracks(
        root,
        sequence_node,
        optimization_result=optimization_result,
        ordered_stage_ids=ordered_stage_ids,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
        enable_auto_transitions=enable_auto_transitions,
        allow_transition_handle_trimming=allow_transition_handle_trimming,
    )
    _update_sequence_duration_metadata(
        root,
        sequence_node,
        new_total_duration=new_total_duration,
    )

    output_project_path.parent.mkdir(parents=True, exist_ok=True)
    project_xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    output_project_path.write_bytes(gzip.compress(project_xml_bytes))
    return output_project_path


def export_optimized_premiere_project_sequence_copy(
    *,
    source_project_path: Path,
    optimization_result: SequenceOptimizationResult,
    output_project_path: Path,
    new_sequence_name: str,
    enable_auto_transitions: bool = False,
    allow_transition_handle_trimming: bool = False,
) -> Path:
    root = load_premiere_project_root(source_project_path)
    object_id_lookup = build_project_object_id_lookup(root)
    object_uid_lookup = build_project_object_uid_lookup(root)

    source_sequence = find_project_sequence_node(root, optimization_result.selected_sequence_name)
    if source_sequence is None:
        raise PremiereProjectError(
            f"Sequence '{optimization_result.selected_sequence_name}' was not found in project: {source_project_path}"
        )

    source_masterclip = _find_sequence_masterclip(root, optimization_result.selected_sequence_name)
    if source_masterclip is None:
        raise PremiereProjectError(
            f"MasterClip for sequence '{optimization_result.selected_sequence_name}' was not found in project."
        )

    source_project_item = _find_sequence_project_item(root, source_masterclip.attrib.get("ObjectUID", ""))
    if source_project_item is None:
        raise PremiereProjectError(
            f"ProjectItem for sequence '{optimization_result.selected_sequence_name}' was not found in project."
        )

    clone_state = _ProjectCloneState(
        root=root,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
        selected_sequence_uid=source_sequence.attrib.get("ObjectUID", ""),
        selected_masterclip_uid=source_masterclip.attrib.get("ObjectUID", ""),
    )

    cloned_sequence = clone_state.clone_object_by_uid(source_sequence.attrib["ObjectUID"])
    cloned_masterclip = clone_state.clone_object_by_uid(source_masterclip.attrib["ObjectUID"])
    cloned_project_item = clone_state.clone_object_by_uid(source_project_item.attrib["ObjectUID"])

    _set_child_text(cloned_sequence, "Name", new_sequence_name)
    _set_child_text(cloned_masterclip, "Name", new_sequence_name)
    _set_child_text(_ensure_child(cloned_project_item, "ProjectItem"), "Name", new_sequence_name)
    _set_project_item_grid_order(root, cloned_project_item)

    _append_project_item_to_root(root, cloned_project_item.attrib["ObjectUID"])

    updated_id_lookup = build_project_object_id_lookup(root)
    updated_uid_lookup = build_project_object_uid_lookup(root)
    new_total_duration = _reorder_project_sequence_tracks(
        root,
        cloned_sequence,
        optimization_result=optimization_result,
        ordered_stage_ids=[entry.candidate.clip.stage_id for entry in optimization_result.entries],
        object_id_lookup=updated_id_lookup,
        object_uid_lookup=updated_uid_lookup,
        enable_auto_transitions=enable_auto_transitions,
        allow_transition_handle_trimming=allow_transition_handle_trimming,
    )
    _update_sequence_duration_metadata(
        root,
        cloned_sequence,
        new_total_duration=new_total_duration,
    )

    output_project_path.parent.mkdir(parents=True, exist_ok=True)
    project_xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    output_project_path.write_bytes(gzip.compress(project_xml_bytes))
    return output_project_path


def _reorder_project_sequence_tracks(
    root: ET.Element,
    sequence_node: ET.Element,
    *,
    optimization_result: SequenceOptimizationResult,
    ordered_stage_ids: list[str],
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
    enable_auto_transitions: bool = False,
    allow_transition_handle_trimming: bool = False,
) -> int:
    stage_id_set = set(ordered_stage_ids)
    spans = _collect_project_stage_spans(
        sequence_node,
        stage_id_set=stage_id_set,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    )
    missing_stage_ids = [stage_id for stage_id in ordered_stage_ids if stage_id not in spans]
    if missing_stage_ids:
        missing_display = ", ".join(missing_stage_ids)
        raise PremiereProjectError(f"Could not locate all stage clips in project sequence. Missing: {missing_display}")

    new_bases: dict[str, int] = {}
    cursor = 0
    for stage_id in ordered_stage_ids:
        new_bases[stage_id] = cursor
        cursor += spans[stage_id]["duration"]

    candidate_by_stage_id = {
        entry.candidate.clip.stage_id: entry.candidate
        for entry in optimization_result.entries
    }
    transition_template = _find_video_transition_template(root, object_id_lookup)
    id_allocator = _ProjectObjectIdAllocator(root)
    max_track_end = 0

    for track_index, track_node in get_project_track_nodes(
        sequence_node,
        track_group_index=0,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    ):
        ordered_track_items, full_coverage, source_gap_pattern, is_mp4_track = _reorder_project_track(
            track_node,
            ordered_stage_ids=ordered_stage_ids,
            spans=spans,
            new_bases=new_bases,
            object_id_lookup=object_id_lookup,
        )
        _sync_project_track_transitions(
            root=root,
            track_node=track_node,
            track_index=track_index,
            ordered_track_items=ordered_track_items,
            full_coverage=full_coverage,
            source_gap_pattern=source_gap_pattern,
            is_mp4_track=is_mp4_track,
            candidate_by_stage_id=candidate_by_stage_id,
            object_id_lookup=object_id_lookup,
            transition_template=transition_template,
            id_allocator=id_allocator,
            enable_auto_transitions=enable_auto_transitions,
            allow_transition_handle_trimming=allow_transition_handle_trimming,
        )
        max_track_end = max(max_track_end, _project_track_max_end(track_node, object_id_lookup))

    for _track_index, track_node in get_project_track_nodes(
        sequence_node,
        track_group_index=1,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    ):
        _reorder_project_track(
            track_node,
            ordered_stage_ids=ordered_stage_ids,
            spans=spans,
            new_bases=new_bases,
            object_id_lookup=object_id_lookup,
        )
        max_track_end = max(max_track_end, _project_track_max_end(track_node, object_id_lookup))

    return max_track_end


def _collect_project_stage_spans(
    sequence_node: ET.Element,
    *,
    stage_id_set: set[str],
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
) -> dict[str, dict[str, int]]:
    spans: dict[str, dict[str, int]] = {}

    for track_group_index in (0, 1):
        for _track_index, track_node in get_project_track_nodes(
            sequence_node,
            track_group_index=track_group_index,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
        ):
            for track_item_ref in iter_project_track_item_refs(track_node):
                object_ref = track_item_ref.attrib.get("ObjectRef")
                if not object_ref:
                    continue
                track_item_node = object_id_lookup.get(object_ref)
                if track_item_node is None:
                    continue
                stage_id = resolve_project_track_item_stage_id(track_item_node, object_id_lookup)
                if stage_id is None or stage_id not in stage_id_set:
                    continue
                start, end = resolve_project_track_item_timeline(track_item_node)
                current = spans.get(stage_id)
                if current is None:
                    spans[stage_id] = {"start": start, "end": end, "duration": end - start}
                    continue
                current["start"] = min(current["start"], start)
                current["end"] = max(current["end"], end)
                current["duration"] = current["end"] - current["start"]

    return spans


def _reorder_project_track(
    track_node: ET.Element,
    *,
    ordered_stage_ids: list[str],
    spans: dict[str, dict[str, int]],
    new_bases: dict[str, int],
    object_id_lookup: dict[str, ET.Element],
) -> tuple[list[tuple[str, ET.Element]], bool, list[int], bool]:
    track_items_container = track_node.find("./ClipTrack/ClipItems/TrackItems")
    if track_items_container is None:
        return [], False, [], False

    track_item_refs = list(track_items_container.findall("./TrackItem"))
    if not track_item_refs:
        return [], False, [], False

    matched_items_by_stage: dict[str, list[tuple[int, ET.Element, ET.Element]]] = {}
    unmatched_items: list[ET.Element] = []

    for track_item_index, track_item_ref in enumerate(track_item_refs):
        object_ref = track_item_ref.attrib.get("ObjectRef")
        if not object_ref:
            unmatched_items.append(track_item_ref)
            continue
        track_item_node = object_id_lookup.get(object_ref)
        if track_item_node is None:
            unmatched_items.append(track_item_ref)
            continue
        stage_id = resolve_project_track_item_stage_id(track_item_node, object_id_lookup)
        if stage_id is None or stage_id not in new_bases:
            unmatched_items.append(track_item_ref)
            continue
        matched_items_by_stage.setdefault(stage_id, []).append(
            (track_item_index, track_item_ref, track_item_node)
        )

    if not matched_items_by_stage or unmatched_items:
        return [], False, [], False

    for track_item_ref in track_item_refs:
        track_items_container.remove(track_item_ref)

    full_coverage = all(stage_id in matched_items_by_stage for stage_id in ordered_stage_ids)
    insert_index = 0
    ordered_track_items: list[tuple[str, ET.Element]] = []
    for stage_id in ordered_stage_ids:
        stage_items = matched_items_by_stage.get(stage_id)
        if not stage_items:
            continue
        ordered_stage_items = sorted(
            stage_items,
            key=lambda item: (*resolve_project_track_item_timeline(item[2]), item[0]),
        )

        for item_index, (_source_index, track_item_ref, track_item_node) in enumerate(ordered_stage_items):
            track_item_ref.attrib["Index"] = str(insert_index)
            insert_index += 1
            _shift_project_track_item_timeline(
                track_item_node,
                original_base=spans[stage_id]["start"],
                new_base=new_bases[stage_id],
            )
            if item_index == 0:
                ordered_track_items.append((stage_id, track_item_node))
            track_items_container.append(track_item_ref)

    track_gap_pattern = [
        max(
            0,
            resolve_project_track_item_timeline(ordered_track_items[index + 1][1])[0]
            - resolve_project_track_item_timeline(ordered_track_items[index][1])[1],
        )
        for index in range(len(ordered_track_items) - 1)
    ]
    is_mp4_track = bool(ordered_track_items) and all(
        resolve_project_track_item_name(track_item_node, object_id_lookup).lower().endswith(".mp4")
        for _stage_id, track_item_node in ordered_track_items
    )

    return ordered_track_items, full_coverage, track_gap_pattern, is_mp4_track


def _project_track_max_end(
    track_node: ET.Element,
    object_id_lookup: dict[str, ET.Element],
) -> int:
    max_end = 0
    for track_item_ref in iter_project_track_item_refs(track_node):
        object_ref = track_item_ref.attrib.get("ObjectRef")
        if not object_ref:
            continue
        track_item_node = object_id_lookup.get(object_ref)
        if track_item_node is None:
            continue
        _start, end = resolve_project_track_item_timeline(track_item_node)
        max_end = max(max_end, end)
    return max_end


def _sync_project_track_transitions(
    *,
    root: ET.Element,
    track_node: ET.Element,
    track_index: int,
    ordered_track_items: list[tuple[str, ET.Element]],
    full_coverage: bool,
    source_gap_pattern: list[int],
    is_mp4_track: bool,
    candidate_by_stage_id: dict[str, object],
    object_id_lookup: dict[str, ET.Element],
    transition_template: ET.Element | None,
    id_allocator: "_ProjectObjectIdAllocator",
    enable_auto_transitions: bool = False,
    allow_transition_handle_trimming: bool = False,
) -> None:
    transition_container = _ensure_transition_items_container(track_node, track_index)
    for child in list(transition_container.findall("./TrackItems")):
        transition_container.remove(child)

    if (
        not enable_auto_transitions
        or transition_template is None
        or not full_coverage
        or not is_mp4_track
        or any(source_gap_pattern)
        or len(ordered_track_items) < 2
    ):
        return

    transition_refs = ET.Element("TrackItems")
    transition_refs.attrib["Version"] = "1"
    transition_index = 0
    template_duration = _transition_duration(transition_template)
    minimum_supported_duration = max(2, template_duration // 20)

    for item_index in range(len(ordered_track_items) - 1):
        previous_stage_id, previous_track_item = ordered_track_items[item_index]
        current_stage_id, current_track_item = ordered_track_items[item_index + 1]
        previous_start, previous_end = resolve_project_track_item_timeline(previous_track_item)
        current_start, current_end = resolve_project_track_item_timeline(current_track_item)
        if previous_end != current_start:
            continue

        previous_candidate = candidate_by_stage_id.get(previous_stage_id)
        current_candidate = candidate_by_stage_id.get(current_stage_id)
        if previous_candidate is None or current_candidate is None:
            continue

        transition_duration = _choose_transition_duration(
            previous_candidate,
            current_candidate,
            previous_duration=previous_end - previous_start,
            current_duration=current_end - current_start,
            template_duration=template_duration,
        )
        previous_tail_handle = _resolve_track_item_tail_handle(previous_track_item, object_id_lookup)
        if previous_tail_handle is not None and previous_tail_handle < transition_duration and allow_transition_handle_trimming:
            desired_trim = transition_duration - previous_tail_handle
            actual_trim = _trim_track_item_tail(
                previous_track_item,
                trim_amount=desired_trim,
                minimum_remaining_duration=minimum_supported_duration,
                object_id_lookup=object_id_lookup,
            )
            if actual_trim > 0:
                _shift_following_track_items(ordered_track_items[item_index + 1 :], shift_delta=-actual_trim)
                previous_end = resolve_project_track_item_timeline(previous_track_item)[1]
                current_start, current_end = resolve_project_track_item_timeline(current_track_item)
                previous_tail_handle = _resolve_track_item_tail_handle(previous_track_item, object_id_lookup)
        if previous_end != current_start:
            continue
        if previous_tail_handle is not None:
            transition_duration = min(transition_duration, previous_tail_handle)
        if transition_duration < minimum_supported_duration:
            continue

        transition_node, related_nodes = _clone_transition_package(
            transition_template,
            root=root,
            start=current_start,
            end=current_start + transition_duration,
            alignment=0,
            id_allocator=id_allocator,
            object_id_lookup=object_id_lookup,
        )
        root.append(transition_node)
        object_id_lookup[transition_node.attrib["ObjectID"]] = transition_node
        for related_node in related_nodes:
            root.append(related_node)
            object_id_lookup[related_node.attrib["ObjectID"]] = related_node

        transition_ref = ET.SubElement(transition_refs, "TrackItem")
        transition_ref.attrib["Index"] = str(transition_index)
        transition_ref.attrib["ObjectRef"] = transition_node.attrib["ObjectID"]
        transition_index += 1

    if list(transition_refs):
        transition_container.insert(0, transition_refs)


def _ensure_transition_items_container(track_node: ET.Element, track_index: int) -> ET.Element:
    clip_track_node = _ensure_child(track_node, "ClipTrack")
    transition_container = clip_track_node.find("./TransitionItems")
    if transition_container is None:
        transition_container = ET.Element("TransitionItems")
        transition_container.attrib["Version"] = "1"
        clip_track_node.append(transition_container)

    media_type = transition_container.find("./MediaType")
    if media_type is None:
        media_type = ET.SubElement(transition_container, "MediaType")
    if not (media_type.text or "").strip():
        media_type.text = _DEFAULT_VIDEO_TRANSITION_MEDIA_TYPE

    index_node = transition_container.find("./Index")
    if index_node is None:
        index_node = ET.SubElement(transition_container, "Index")
    index_node.text = str(track_index)
    return transition_container


def _find_video_transition_template(
    root: ET.Element,
    object_id_lookup: dict[str, ET.Element],
) -> ET.Element | None:
    for transition_node in root.iter("VideoTransitionTrackItem"):
        component_ref = transition_node.find("./VideoFilterComponent")
        if component_ref is None:
            continue
        if component_ref.attrib.get("ObjectRef") not in object_id_lookup:
            continue
        return transition_node
    return None


def _choose_transition_duration(
    previous_candidate: object,
    current_candidate: object,
    *,
    previous_duration: int,
    current_duration: int,
    template_duration: int,
) -> int:
    base_duration = max(template_duration, 2)
    subject_overlap = set(previous_candidate.series_subject_tokens) & set(current_candidate.series_subject_tokens)
    appearance_overlap = set(previous_candidate.series_appearance_tokens) & set(current_candidate.series_appearance_tokens)
    keyword_overlap = set(previous_candidate.keywords) & set(current_candidate.keywords)

    desired_duration = max(2, base_duration // 3)
    if len(appearance_overlap) >= 2:
        desired_duration = base_duration
    elif appearance_overlap or len(subject_overlap) >= 2:
        desired_duration = max(base_duration // 2, 2)
    elif keyword_overlap or (
        abs(previous_candidate.shot_scale - current_candidate.shot_scale) <= 1
        and abs(previous_candidate.people_count - current_candidate.people_count) <= 1
    ):
        desired_duration = max(base_duration // 2, 2)

    max_total_duration = min(
        desired_duration,
        max(2, previous_duration),
        max(2, current_duration),
    )
    if max_total_duration % 2 != 0:
        max_total_duration -= 1
    return max(max_total_duration, 0)


def _resolve_track_item_tail_handle(
    track_item_node: ET.Element,
    object_id_lookup: dict[str, ET.Element],
) -> int | None:
    clip_node = resolve_project_track_item_clip(track_item_node, object_id_lookup)
    if clip_node is None:
        return None

    in_point = _safe_int(clip_node.findtext("./Clip/InPoint"))
    out_point = _safe_int(clip_node.findtext("./Clip/OutPoint"))
    source_ref = clip_node.find("./Clip/Source")
    if source_ref is None:
        return None

    source_node = object_id_lookup.get(source_ref.attrib.get("ObjectRef", ""))
    if source_node is None:
        return None

    original_duration = _safe_int(source_node.findtext("./OriginalDuration"))
    if original_duration <= 0:
        return None

    used_duration = max(0, out_point - in_point)
    if used_duration <= 0:
        return None
    return max(0, original_duration - out_point)


def _trim_track_item_tail(
    track_item_node: ET.Element,
    *,
    trim_amount: int,
    minimum_remaining_duration: int,
    object_id_lookup: dict[str, ET.Element],
) -> int:
    if trim_amount <= 0:
        return 0

    clip_node = resolve_project_track_item_clip(track_item_node, object_id_lookup)
    if clip_node is None:
        return 0

    start, end = resolve_project_track_item_timeline(track_item_node)
    current_duration = max(0, end - start)
    max_trimmable = max(0, current_duration - minimum_remaining_duration)
    actual_trim = min(trim_amount, max_trimmable)
    if actual_trim <= 0:
        return 0

    clip_payload = clip_node.find("./Clip")
    if clip_payload is None:
        return 0

    out_point = _safe_int(clip_payload.findtext("./OutPoint"))
    in_point = _safe_int(clip_payload.findtext("./InPoint"))
    if out_point - actual_trim <= in_point:
        return 0

    timeline_node = track_item_node.find("./ClipTrackItem/TrackItem")
    if timeline_node is None:
        return 0

    _set_track_item_boundary(timeline_node, "End", end - actual_trim)
    _set_child_text(clip_payload, "OutPoint", str(out_point - actual_trim))
    return actual_trim


def _shift_following_track_items(
    ordered_track_items: list[tuple[str, ET.Element]],
    *,
    shift_delta: int,
) -> None:
    if shift_delta == 0:
        return

    for _stage_id, track_item_node in ordered_track_items:
        start, _end = resolve_project_track_item_timeline(track_item_node)
        _shift_project_track_item_timeline(
            track_item_node,
            original_base=start,
            new_base=start + shift_delta,
        )


def _clone_transition_package(
    template_node: ET.Element,
    *,
    root: ET.Element,
    start: int,
    end: int,
    alignment: int,
    id_allocator: "_ProjectObjectIdAllocator",
    object_id_lookup: dict[str, ET.Element],
) -> tuple[ET.Element, list[ET.Element]]:
    transition_node = copy.deepcopy(template_node)
    transition_node.attrib["ObjectID"] = id_allocator.allocate()
    _set_child_text(_ensure_child(transition_node, "TransitionTrackItem"), "DisplayName", template_node.findtext("./TransitionTrackItem/DisplayName") or "Cross Dissolve (Legacy)")
    _set_child_text(_ensure_child(transition_node, "TransitionTrackItem"), "MatchName", template_node.findtext("./TransitionTrackItem/MatchName") or "AE.ADBE Cross Dissolve New")

    transition_track_item = _ensure_child(transition_node, "TransitionTrackItem")
    track_item = _ensure_child(transition_track_item, "TrackItem")
    _set_child_text(track_item, "Start", str(start))
    _set_child_text(track_item, "End", str(end))
    _set_child_text(transition_track_item, "Alignment", str(alignment))
    _set_child_text(transition_track_item, "HasOutgoingClip", "true")
    _set_child_text(transition_track_item, "HasIncomingClip", "true")

    related_nodes: list[ET.Element] = []
    component_ref = transition_node.find("./VideoFilterComponent")
    if component_ref is None:
        return transition_node, related_nodes

    template_component_id = component_ref.attrib.get("ObjectRef")
    if not template_component_id:
        return transition_node, related_nodes

    template_component = object_id_lookup.get(template_component_id)
    if template_component is None:
        return transition_node, related_nodes

    component_node = copy.deepcopy(template_component)
    component_node.attrib["ObjectID"] = id_allocator.allocate()
    component_ref.attrib["ObjectRef"] = component_node.attrib["ObjectID"]

    params_node = component_node.find("./Component/Params")
    if params_node is not None:
        for param_ref in params_node.findall("./Param"):
            template_param_id = param_ref.attrib.get("ObjectRef")
            if not template_param_id:
                continue
            template_param = object_id_lookup.get(template_param_id)
            if template_param is None:
                continue
            cloned_param = copy.deepcopy(template_param)
            cloned_param.attrib["ObjectID"] = id_allocator.allocate()
            param_ref.attrib["ObjectRef"] = cloned_param.attrib["ObjectID"]
            related_nodes.append(cloned_param)

    related_nodes.insert(0, component_node)
    return transition_node, related_nodes


def _transition_duration(transition_node: ET.Element) -> int:
    start = _safe_int(transition_node.findtext("./TransitionTrackItem/TrackItem/Start"))
    end = _safe_int(transition_node.findtext("./TransitionTrackItem/TrackItem/End"))
    return max(end - start, 2)


def _shift_project_track_item_timeline(
    track_item_node: ET.Element,
    *,
    original_base: int,
    new_base: int,
) -> None:
    timeline_node = track_item_node.find("./ClipTrackItem/TrackItem")
    if timeline_node is None:
        return

    original_start, original_end = resolve_project_track_item_timeline(track_item_node)
    new_start = new_base + (original_start - original_base)
    new_end = new_base + (original_end - original_base)

    _set_track_item_boundary(timeline_node, "Start", new_start)
    _set_track_item_boundary(timeline_node, "End", new_end)


def _set_track_item_boundary(timeline_node: ET.Element, tag_name: str, value: int) -> None:
    child = timeline_node.find(f"./{tag_name}")
    if child is None and tag_name == "Start" and value == 0:
        return

    if child is None:
        child = ET.Element(tag_name)
        end_child = timeline_node.find("./End")
        if tag_name == "Start" and end_child is not None:
            end_index = list(timeline_node).index(end_child)
            timeline_node.insert(end_index, child)
        else:
            timeline_node.append(child)

    child.text = str(value)


def _update_sequence_duration_metadata(
    root: ET.Element,
    sequence_node: ET.Element,
    *,
    new_total_duration: int,
) -> None:
    properties_node = sequence_node.find("./Node/Properties")
    if properties_node is not None:
        _set_child_text(properties_node, "MZ.WorkOutPoint", str(new_total_duration))

        edit_line_node = properties_node.find("./MZ.EditLine")
        if edit_line_node is not None:
            current_edit_line = _safe_int(edit_line_node.text)
            edit_line_node.text = str(min(current_edit_line, new_total_duration))

    sequence_uid = sequence_node.attrib.get("ObjectUID")
    if not sequence_uid:
        return

    for source_tag in ("VideoSequenceSource", "AudioSequenceSource"):
        for source_node in root.iter(source_tag):
            source_sequence_ref = source_node.find("./SequenceSource/Sequence")
            if source_sequence_ref is None:
                continue
            if source_sequence_ref.attrib.get("ObjectURef") != sequence_uid:
                continue
            _set_child_text(source_node, "OriginalDuration", str(new_total_duration))


class _ProjectObjectIdAllocator:
    def __init__(self, root: ET.Element) -> None:
        self._next_object_id = max(
            (int(node.attrib["ObjectID"]) for node in root.iter() if node.attrib.get("ObjectID", "").isdigit()),
            default=0,
        ) + 1

    def allocate(self) -> str:
        object_id = str(self._next_object_id)
        self._next_object_id += 1
        return object_id


class _ProjectCloneState:
    def __init__(
        self,
        *,
        root: ET.Element,
        object_id_lookup: dict[str, ET.Element],
        object_uid_lookup: dict[str, ET.Element],
        selected_sequence_uid: str,
        selected_masterclip_uid: str,
    ) -> None:
        self.root = root
        self.object_id_lookup = dict(object_id_lookup)
        self.object_uid_lookup = dict(object_uid_lookup)
        self.selected_sequence_uid = selected_sequence_uid
        self.selected_masterclip_uid = selected_masterclip_uid
        self.id_mapping: dict[str, str] = {}
        self.uid_mapping: dict[str, str] = {}
        self._cloned_by_id: dict[str, ET.Element] = {}
        self._cloned_by_uid: dict[str, ET.Element] = {}
        self._next_object_id = max(
            (int(node.attrib["ObjectID"]) for node in root.iter() if node.attrib.get("ObjectID", "").isdigit()),
            default=0,
        ) + 1

    def clone_object_by_id(self, old_object_id: str) -> ET.Element:
        if old_object_id in self._cloned_by_id:
            return self._cloned_by_id[old_object_id]

        source_node = self.object_id_lookup.get(old_object_id)
        if source_node is None:
            raise PremiereProjectError(f"Referenced project object id '{old_object_id}' was not found.")

        cloned_node = self._clone_node(source_node)
        self._register_clone(source_node, cloned_node)
        self._rewire_cloned_refs(cloned_node)
        self.root.append(cloned_node)
        return cloned_node

    def clone_object_by_uid(self, old_object_uid: str) -> ET.Element:
        if old_object_uid in self._cloned_by_uid:
            return self._cloned_by_uid[old_object_uid]

        source_node = self.object_uid_lookup.get(old_object_uid)
        if source_node is None:
            raise PremiereProjectError(f"Referenced project object uid '{old_object_uid}' was not found.")

        cloned_node = self._clone_node(source_node)
        self._register_clone(source_node, cloned_node)
        self._rewire_cloned_refs(cloned_node)
        self.root.append(cloned_node)
        return cloned_node

    def _clone_node(self, source_node: ET.Element) -> ET.Element:
        cloned_node = copy.deepcopy(source_node)

        old_object_id = source_node.attrib.get("ObjectID")
        if old_object_id:
            cloned_node.attrib["ObjectID"] = self._allocate_object_id()

        old_object_uid = source_node.attrib.get("ObjectUID")
        if old_object_uid:
            cloned_node.attrib["ObjectUID"] = str(uuid4())

        self._refresh_uuid_like_texts(cloned_node)
        return cloned_node

    def _register_clone(self, source_node: ET.Element, cloned_node: ET.Element) -> None:
        old_object_id = source_node.attrib.get("ObjectID")
        if old_object_id and cloned_node.attrib.get("ObjectID"):
            new_object_id = cloned_node.attrib["ObjectID"]
            self.id_mapping[old_object_id] = new_object_id
            self._cloned_by_id[old_object_id] = cloned_node
            self.object_id_lookup[new_object_id] = cloned_node

        old_object_uid = source_node.attrib.get("ObjectUID")
        if old_object_uid and cloned_node.attrib.get("ObjectUID"):
            new_object_uid = cloned_node.attrib["ObjectUID"]
            self.uid_mapping[old_object_uid] = new_object_uid
            self._cloned_by_uid[old_object_uid] = cloned_node
            self.object_uid_lookup[new_object_uid] = cloned_node

    def _rewire_cloned_refs(self, cloned_node: ET.Element) -> None:
        for element in cloned_node.iter():
            object_ref = element.attrib.get("ObjectRef")
            if object_ref:
                target_node = self.object_id_lookup.get(object_ref)
                if target_node is not None and self._should_clone_target(target_node):
                    element.attrib["ObjectRef"] = self.clone_object_by_id(object_ref).attrib["ObjectID"]

            object_uref = element.attrib.get("ObjectURef")
            if object_uref:
                target_node = self.object_uid_lookup.get(object_uref)
                if target_node is not None and self._should_clone_target(target_node):
                    element.attrib["ObjectURef"] = self.clone_object_by_uid(object_uref).attrib["ObjectUID"]

    def _should_clone_target(self, target_node: ET.Element) -> bool:
        if target_node.tag in {
            "Project",
            "ProjectSettings",
            "RootProjectItem",
            "BinProjectItem",
            "VideoMediaSource",
            "AudioMediaSource",
            "Media",
        }:
            return False

        if target_node.tag == "Sequence":
            return target_node.attrib.get("ObjectUID") == self.selected_sequence_uid

        if target_node.tag == "MasterClip":
            return target_node.attrib.get("ObjectUID") == self.selected_masterclip_uid

        if target_node.tag in {"VideoSequenceSource", "AudioSequenceSource"}:
            sequence_ref = target_node.find("./SequenceSource/Sequence")
            return sequence_ref is not None and sequence_ref.attrib.get("ObjectURef") == self.selected_sequence_uid

        return True

    def _allocate_object_id(self) -> str:
        value = str(self._next_object_id)
        self._next_object_id += 1
        return value

    def _refresh_uuid_like_texts(self, cloned_node: ET.Element) -> None:
        for element in cloned_node.iter():
            if element.tag not in {"ID", "ClipID"}:
                continue
            if element.text and _UUID_TEXT_PATTERN.match(element.text.strip()):
                element.text = str(uuid4())


def _append_project_item_to_root(root: ET.Element, project_item_uid: str) -> None:
    root_project_item = _find_root_project_item_definition(root)
    if root_project_item is None:
        raise PremiereProjectError("RootProjectItem definition was not found in project.")

    items_node = root_project_item.find("./ProjectItemContainer/Items")
    if items_node is None:
        raise PremiereProjectError("RootProjectItem Items container was not found in project.")

    next_index = max((_safe_int(item.attrib.get("Index")) for item in items_node.findall("./Item")), default=-1) + 1
    new_item = ET.Element("Item")
    new_item.attrib["Index"] = str(next_index)
    new_item.attrib["ObjectURef"] = project_item_uid
    items_node.append(new_item)


def _find_root_project_item_definition(root: ET.Element) -> ET.Element | None:
    for node in root.iter("RootProjectItem"):
        if node.attrib.get("ObjectUID"):
            return node
    return None


def _find_sequence_masterclip(root: ET.Element, sequence_name: str) -> ET.Element | None:
    normalized_name = sequence_name.casefold()
    for node in root.iter("MasterClip"):
        if ((node.findtext("./Name") or "").strip()).casefold() == normalized_name:
            return node
    return None


def _find_sequence_project_item(root: ET.Element, masterclip_uid: str) -> ET.Element | None:
    for node in root.iter("ClipProjectItem"):
        master_ref = node.find("./MasterClip")
        if master_ref is None:
            continue
        if master_ref.attrib.get("ObjectURef") == masterclip_uid:
            return node
    return None


def _set_project_item_grid_order(root: ET.Element, project_item_node: ET.Element) -> None:
    max_grid_order = 0
    for node in root.iter("ClipProjectItem"):
        order_text = node.findtext("./ProjectItem/Node/Properties/project.icon.view.grid.order")
        max_grid_order = max(max_grid_order, _safe_int(order_text))

    properties_node = project_item_node.find("./ProjectItem/Node/Properties")
    if properties_node is None:
        project_item = _ensure_child(project_item_node, "ProjectItem")
        node = _ensure_child(project_item, "Node")
        properties_node = _ensure_child(node, "Properties")
    _set_child_text(properties_node, "project.icon.view.grid.order", str(max_grid_order + 1))


def _set_child_text(node: ET.Element, tag_name: str, text_value: str) -> None:
    child = node.find(f"./{tag_name}")
    if child is None:
        child = ET.SubElement(node, tag_name)
    child.text = text_value


def _ensure_child(node: ET.Element, tag_name: str) -> ET.Element:
    child = node.find(f"./{tag_name}")
    if child is None:
        child = ET.SubElement(node, tag_name)
    return child


def _safe_int(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0
