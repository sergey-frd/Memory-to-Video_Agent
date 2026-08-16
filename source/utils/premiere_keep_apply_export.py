from __future__ import annotations

import copy
import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from models.sequence_keep_apply import KeepRange, MediaKeepSpec
from utils.premiere_project import (
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    iter_project_track_item_refs,
    list_named_project_sequence_names,
    load_premiere_project_root,
    resolve_project_track_item_clip,
    resolve_project_track_item_name,
    resolve_project_track_item_source_bounds,
    resolve_project_track_item_source_path,
    resolve_project_track_item_subclip,
    resolve_project_track_item_timeline,
)
from utils.premiere_project_export import (
    _ProjectObjectIdAllocator,
    _insert_project_object_near_same_type,
    _set_child_text,
    _set_track_item_boundary,
    _update_sequence_duration_metadata,
)
from utils.premiere_trim_review_export import (
    _ensure_track_items_container,
    _reindex_track_items,
)
from utils.sequence_trim_classifier import seconds_to_ticks


def resolve_keep_windows_ticks(spec: MediaKeepSpec, source_in: int) -> list[tuple[int, int]]:
    if spec.ranges:
        return [
            (seconds_to_ticks(item.start_seconds), seconds_to_ticks(item.end_seconds))
            for item in spec.ranges
        ]
    if spec.duration_seconds is not None:
        duration_ticks = max(1, seconds_to_ticks(spec.duration_seconds))
        return [(source_in, source_in + duration_ticks)]
    return []


def intersect_keep_ranges_ticks(
    source_in: int,
    source_out: int,
    ranges: tuple[KeepRange, ...] | list[KeepRange],
) -> list[tuple[int, int]]:
    intersections: list[tuple[int, int]] = []
    for keep_range in ranges:
        keep_in = seconds_to_ticks(keep_range.start_seconds)
        keep_out = seconds_to_ticks(keep_range.end_seconds)
        start = max(source_in, keep_in)
        end = min(source_out, keep_out)
        if end > start:
            intersections.append((start, end))
    intersections.sort()
    return intersections


@dataclass
class _TrackItemView:
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

    @property
    def match_key(self) -> str:
        identity = self.source_path or self.name
        return Path(identity).name.casefold() if identity else ""


@dataclass
class _KeepSegment:
    timeline_start: int
    timeline_end: int
    source_in: int
    source_out: int


@dataclass
class _ItemPlan:
    view: _TrackItemView
    action: str  # unchanged | trim | remove
    segments: list[_KeepSegment]


def export_keep_apply_premiere_project(
    *,
    source_project_path: Path,
    output_project_path: Path,
    keep_specs: list[MediaKeepSpec],
    sequence_names: list[str] | None = None,
    ripple_compact: bool = True,
) -> tuple[Path, list[str]]:
    if not keep_specs:
        raise PremiereProjectError("No keep-range specs were provided.")

    root = load_premiere_project_root(source_project_path)
    target_names = sequence_names or list_named_project_sequence_names(root)
    if not target_names:
        raise PremiereProjectError(f"No named sequences were found in project: {source_project_path}")

    all_warnings: list[str] = []
    for sequence_name in target_names:
        sequence_node = find_project_sequence_node(root, sequence_name)
        if sequence_node is None:
            all_warnings.append(f"Sequence '{sequence_name}' was not found and was skipped.")
            continue
        object_id_lookup = build_project_object_id_lookup(root)
        object_uid_lookup = build_project_object_uid_lookup(root)
        warnings = _apply_keep_ranges_to_sequence(
            root,
            sequence_node,
            keep_specs=keep_specs,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
            project_path=source_project_path,
            ripple_compact=ripple_compact,
        )
        all_warnings.extend(warnings)

    output_project_path.parent.mkdir(parents=True, exist_ok=True)
    project_xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    output_project_path.write_bytes(gzip.compress(project_xml_bytes))
    return output_project_path, all_warnings


def _apply_keep_ranges_to_sequence(
    root: ET.Element,
    sequence_node: ET.Element,
    *,
    keep_specs: list[MediaKeepSpec],
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
    project_path: Path,
    ripple_compact: bool,
) -> list[str]:
    warnings: list[str] = []
    specs_by_key = {spec.match_key: spec for spec in keep_specs}
    video_views = _collect_track_item_views(
        sequence_node,
        track_group_index=0,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
        project_path=project_path,
    )
    audio_views = _collect_track_item_views(
        sequence_node,
        track_group_index=1,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
        project_path=project_path,
    )
    if not video_views:
        warnings.append(
            f"Sequence '{sequence_node.findtext('./Name') or '<unnamed>'}' has no video clips to trim."
        )
        return warnings

    video_plans = _build_item_plans(video_views, specs_by_key, ripple_compact=ripple_compact)
    audio_plans = _align_secondary_plans(audio_views, video_plans, ripple_compact=ripple_compact)
    id_allocator = _ProjectObjectIdAllocator(root)
    matched_keys = {plan.view.match_key for plan in video_plans if plan.action != "unchanged"}
    missing = [spec.file_name for spec in keep_specs if spec.match_key not in matched_keys]
    if missing:
        warnings.append(
            "Keep specs were not matched in this sequence: " + ", ".join(missing)
        )

    _apply_item_plans(
        root,
        video_plans,
        object_id_lookup=object_id_lookup,
        id_allocator=id_allocator,
    )
    _apply_item_plans(
        root,
        audio_plans,
        object_id_lookup=object_id_lookup,
        id_allocator=id_allocator,
    )

    new_duration = 0
    for plan in video_plans:
        if plan.action == "remove":
            continue
        if plan.segments:
            new_duration = max(new_duration, plan.segments[-1].timeline_end)
        else:
            new_duration = max(new_duration, plan.view.end)
    _update_sequence_duration_metadata(root, sequence_node, new_total_duration=new_duration)
    return warnings


def _collect_track_item_views(
    sequence_node: ET.Element,
    *,
    track_group_index: int,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
    project_path: Path,
) -> list[_TrackItemView]:
    views: list[_TrackItemView] = []
    for track_index, track_node in get_project_track_nodes(
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
            start, end = resolve_project_track_item_timeline(track_item_node)
            source_in, source_out = resolve_project_track_item_source_bounds(
                track_item_node,
                object_id_lookup,
            )
            views.append(
                _TrackItemView(
                    track_index=track_index,
                    track_node=track_node,
                    track_item_ref=track_item_ref,
                    track_item_node=track_item_node,
                    name=resolve_project_track_item_name(track_item_node, object_id_lookup),
                    source_path=resolve_project_track_item_source_path(
                        track_item_node,
                        object_id_lookup,
                        object_uid_lookup,
                        project_path=project_path,
                    ),
                    start=start,
                    end=end,
                    source_in=source_in,
                    source_out=source_out,
                )
            )
    views.sort(key=lambda item: (item.start, item.track_index, item.name.casefold()))
    return views


def _build_item_plans(
    views: list[_TrackItemView],
    specs_by_key: dict[str, MediaKeepSpec],
    *,
    ripple_compact: bool,
) -> list[_ItemPlan]:
    plans: list[_ItemPlan] = []
    cursor_shift = 0
    for view in views:
        spec = specs_by_key.get(view.match_key)
        if spec is None:
            if ripple_compact:
                new_start = view.start - cursor_shift
                new_end = view.end - cursor_shift
            else:
                new_start, new_end = view.start, view.end
            plans.append(
                _ItemPlan(
                    view=view,
                    action="unchanged",
                    segments=[
                        _KeepSegment(
                            timeline_start=new_start,
                            timeline_end=new_end,
                            source_in=view.source_in,
                            source_out=view.source_out,
                        )
                    ],
                )
            )
            continue

        intersections = resolve_keep_windows_ticks(spec, view.source_in)
        if not intersections:
            if ripple_compact:
                cursor_shift += view.duration
            plans.append(_ItemPlan(view=view, action="remove", segments=[]))
            continue

        keep_duration = sum(end - start for start, end in intersections)
        if ripple_compact:
            new_start = view.start - cursor_shift
            cursor_shift += view.duration - keep_duration
        else:
            new_start = view.start
        cursor = new_start
        segments = []
        for source_in, source_out in intersections:
            duration = source_out - source_in
            segments.append(
                _KeepSegment(
                    timeline_start=cursor,
                    timeline_end=cursor + duration,
                    source_in=source_in,
                    source_out=source_out,
                )
            )
            cursor += duration
        plans.append(_ItemPlan(view=view, action="trim", segments=segments))
    return plans


def _align_secondary_plans(
    views: list[_TrackItemView],
    master_plans: list[_ItemPlan],
    *,
    ripple_compact: bool,
) -> list[_ItemPlan]:
    remaining = list(master_plans)
    aligned: list[_ItemPlan] = []
    for view in views:
        match = _take_matching_plan(view, remaining)
        if match is None:
            shift = _shift_at(view.start, master_plans) if ripple_compact else 0
            aligned.append(
                _ItemPlan(
                    view=view,
                    action="unchanged",
                    segments=[
                        _KeepSegment(
                            timeline_start=view.start - shift,
                            timeline_end=view.end - shift,
                            source_in=view.source_in,
                            source_out=view.source_out,
                        )
                    ],
                )
            )
            continue
        aligned.append(
            _ItemPlan(
                view=view,
                action=match.action,
                segments=list(match.segments),
            )
        )
    return aligned


def _take_matching_plan(view: _TrackItemView, remaining: list[_ItemPlan]) -> _ItemPlan | None:
    for index, plan in enumerate(remaining):
        if plan.view.match_key != view.match_key:
            continue
        if plan.view.start == view.start and plan.view.end == view.end:
            return remaining.pop(index)
    for index, plan in enumerate(remaining):
        if plan.view.match_key == view.match_key:
            return remaining.pop(index)
    return None


def _shift_at(old_start: int, master_plans: list[_ItemPlan]) -> int:
    shift = 0
    for plan in master_plans:
        if plan.view.start <= old_start:
            if plan.segments:
                shift = plan.view.start - plan.segments[0].timeline_start
            elif plan.action == "remove":
                shift = plan.view.duration if old_start >= plan.view.end else shift
        else:
            break
    return shift


def _apply_item_plans(
    root: ET.Element,
    plans: list[_ItemPlan],
    *,
    object_id_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
) -> None:
    for plan in plans:
        container = _ensure_track_items_container(plan.view.track_node)
        if container is None:
            continue
        if plan.action == "remove" or not plan.segments:
            if plan.view.track_item_ref in list(container):
                container.remove(plan.view.track_item_ref)
            _reindex_track_items(container)
            continue

        first_segment, extra_segments = plan.segments[0], plan.segments[1:]
        _assign_bounds_to_track_item(
            root,
            plan.view.track_item_node,
            segment=first_segment,
            object_id_lookup=object_id_lookup,
            id_allocator=id_allocator,
            clone_clip_if_shared=True,
        )
        insert_after = plan.view.track_item_ref
        for segment in extra_segments:
            new_item_node, new_ref = _clone_track_item_with_bounds(
                root,
                template_track_item=plan.view.track_item_node,
                segment=segment,
                object_id_lookup=object_id_lookup,
                id_allocator=id_allocator,
            )
            object_id_lookup[new_item_node.attrib["ObjectID"]] = new_item_node
            insert_index = list(container).index(insert_after) + 1
            container.insert(insert_index, new_ref)
            insert_after = new_ref
        _reindex_track_items(container)


def _assign_bounds_to_track_item(
    root: ET.Element,
    track_item_node: ET.Element,
    *,
    segment: _KeepSegment,
    object_id_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
    clone_clip_if_shared: bool,
) -> None:
    clip_node = resolve_project_track_item_clip(track_item_node, object_id_lookup)
    subclip_node = resolve_project_track_item_subclip(track_item_node, object_id_lookup)
    if clip_node is None or subclip_node is None:
        raise PremiereProjectError("Could not resolve clip objects while applying keep ranges.")
    if clone_clip_if_shared and _clip_reference_count(root, clip_node.attrib.get("ObjectID", "")) > 1:
        clip_node = _clone_clip_for_exclusive_edit(
            root,
            subclip_node,
            clip_node,
            object_id_lookup=object_id_lookup,
            id_allocator=id_allocator,
        )
    clip_payload = clip_node.find("./Clip")
    if clip_payload is None:
        raise PremiereProjectError("Clip payload is missing InPoint/OutPoint.")
    _set_child_text(clip_payload, "InPoint", str(segment.source_in))
    _set_child_text(clip_payload, "OutPoint", str(segment.source_out))
    timeline_node = track_item_node.find("./ClipTrackItem/TrackItem")
    if timeline_node is None:
        raise PremiereProjectError("Track item is missing timeline bounds.")
    _set_track_item_boundary(timeline_node, "Start", segment.timeline_start)
    _set_track_item_boundary(timeline_node, "End", segment.timeline_end)


def _clone_track_item_with_bounds(
    root: ET.Element,
    *,
    template_track_item: ET.Element,
    segment: _KeepSegment,
    object_id_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
) -> tuple[ET.Element, ET.Element]:
    template_subclip = resolve_project_track_item_subclip(template_track_item, object_id_lookup)
    template_clip = resolve_project_track_item_clip(template_track_item, object_id_lookup)
    if template_subclip is None or template_clip is None:
        raise PremiereProjectError("Could not clone a keep-range segment because clip objects are missing.")

    new_track_item = copy.deepcopy(template_track_item)
    new_subclip = copy.deepcopy(template_subclip)
    new_clip = copy.deepcopy(template_clip)
    new_track_item.attrib["ObjectID"] = id_allocator.allocate()
    new_subclip.attrib["ObjectID"] = id_allocator.allocate()
    new_clip.attrib["ObjectID"] = id_allocator.allocate()

    subclip_ref = new_track_item.find("./ClipTrackItem/SubClip")
    if subclip_ref is None:
        raise PremiereProjectError("Cloned track item is missing SubClip reference.")
    subclip_ref.attrib["ObjectRef"] = new_subclip.attrib["ObjectID"]
    clip_ref = new_subclip.find("./Clip")
    if clip_ref is None:
        raise PremiereProjectError("Cloned SubClip is missing Clip reference.")
    clip_ref.attrib["ObjectRef"] = new_clip.attrib["ObjectID"]

    clip_payload = new_clip.find("./Clip")
    if clip_payload is None:
        raise PremiereProjectError("Cloned clip is missing Clip payload.")
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


def _clone_clip_for_exclusive_edit(
    root: ET.Element,
    subclip_node: ET.Element,
    clip_node: ET.Element,
    *,
    object_id_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
) -> ET.Element:
    new_clip = copy.deepcopy(clip_node)
    new_clip.attrib["ObjectID"] = id_allocator.allocate()
    clip_ref = subclip_node.find("./Clip")
    if clip_ref is None:
        raise PremiereProjectError("SubClip is missing Clip reference while cloning a shared clip.")
    clip_ref.attrib["ObjectRef"] = new_clip.attrib["ObjectID"]
    clip_payload = new_clip.find("./Clip")
    if clip_payload is not None:
        clip_id_node = clip_payload.find("./ClipID")
        if clip_id_node is not None:
            clip_id_node.text = str(uuid4())
    _insert_project_object_near_same_type(root, new_clip)
    object_id_lookup[new_clip.attrib["ObjectID"]] = new_clip
    return new_clip


def _clip_reference_count(root: ET.Element, clip_object_id: str) -> int:
    if not clip_object_id:
        return 0
    return sum(
        1
        for node in root.iter("Clip")
        if node.attrib.get("ObjectRef") == clip_object_id
    )
