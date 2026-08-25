from __future__ import annotations

import base64
import copy
import gzip
import os
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from models.sequence_media_import import MediaImportItem
from utils.premiere_project import (
    PremiereProjectError,
    PREMIERE_TICKS_PER_SECOND,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    is_supported_image_media_path,
    is_supported_visual_media_path,
    iter_project_track_item_refs,
    list_named_project_sequence_names,
    load_premiere_project_root,
    resolve_project_clip_media_node,
    resolve_project_track_item_clip,
    resolve_project_track_item_name,
    resolve_project_track_item_source_path,
    resolve_project_track_item_subclip,
    resolve_project_track_item_timeline,
)
from utils.premiere_project_export import (
    _ProjectObjectIdAllocator,
    _append_project_item_to_root,
    _find_sequence_project_item,
    _insert_project_object_near_same_type,
    _set_child_text,
    _set_project_item_grid_order,
    _set_track_item_boundary,
    _update_sequence_duration_metadata,
    clone_named_sequence,
)
from utils.premiere_trim_review_export import _ensure_track_items_container, _reindex_track_items
from utils.sequence_trim_classifier import seconds_to_ticks


_STILL_IN_POINT_SECONDS = 3600.0
_DEFAULT_STILL_SECONDS = 5.0
_DEFAULT_VIDEO_SECONDS = 5.0
_INTRINSIC_VIDEO_MATCH_NAMES = {
    "AE.ADBE Motion",
    "AE.ADBE Opacity",
}
_OPTIONAL_CLIP_METADATA_TAGS = frozenset({"SecondaryContentItem"})
_INTRINSIC_PARAM_DEFAULTS = {
    "Position": "0.5:0.5",
    "Scale": "100.",
    "Scale Width": "100.",
    "Rotation": "0.",
    "Anchor Point": "0.5:0.5",
    "Anti-flicker Filter": "0.",
    "Crop Left": "0.",
    "Crop Top": "0.",
    "Crop Right": "0.",
    "Crop Bottom": "0.",
    "Opacity": "100.",
}


@dataclass(frozen=True)
class _ClipTemplate:
    item: ET.Element
    object_id_lookup: dict[str, ET.Element]
    object_uid_lookup: dict[str, ET.Element]


def export_media_import_premiere_project(
    *,
    source_project_path: Path,
    output_project_path: Path,
    sequence_name: str,
    source_paths: list[Path],
    create_sequence_if_missing: bool = True,
    fail_if_sequence_exists: bool = False,
    still_duration_seconds: float = _DEFAULT_STILL_SECONDS,
    duration_resolver: Callable[[Path], float | None] | None = None,
    template_project_path: Path | None = None,
) -> tuple[Path, list[MediaImportItem], list[str]]:
    root = load_premiere_project_root(source_project_path)
    object_id_lookup = build_project_object_id_lookup(root)
    object_uid_lookup = build_project_object_uid_lookup(root)
    warnings: list[str] = []
    root, object_id_lookup, object_uid_lookup, adopted_template_project, adopt_warnings = (
        _maybe_adopt_template_project(
            root,
            source_project_path=source_project_path,
            output_project_path=output_project_path,
            template_project_path=template_project_path,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
        )
    )
    warnings.extend(adopt_warnings)

    sequence_node = find_project_sequence_node(root, sequence_name)
    created_sequence = False
    if sequence_node is not None and fail_if_sequence_exists:
        raise PremiereProjectError(f"Sequence '{sequence_name}' already exists in the project.")
    if sequence_node is None:
        if not create_sequence_if_missing and not adopted_template_project:
            raise PremiereProjectError(f"Sequence '{sequence_name}' was not found in the project.")
        sequence_node = _clone_empty_sequence(
            root,
            new_sequence_name=sequence_name,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
        )
        object_id_lookup = build_project_object_id_lookup(root)
        object_uid_lookup = build_project_object_uid_lookup(root)
        created_sequence = True

    video_track = _require_first_track(
        sequence_node,
        track_group_index=0,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
        label="video",
    )
    audio_tracks = get_project_track_nodes(
        sequence_node,
        track_group_index=1,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    )
    audio_track = audio_tracks[0][1] if audio_tracks else None
    cursor = _sequence_end_ticks(
        sequence_node,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    )
    media_by_path = _index_media_by_path(root)
    id_allocator = _ProjectObjectIdAllocator(root)
    imported: list[MediaImportItem] = []
    video_template, image_template, audio_template = _find_templates_in_root(
        root,
        preferred_sequence=sequence_node,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    )
    if video_template is None:
        raise PremiereProjectError(
            "No video clip template was found in the Premiere project. "
            "Pass template_project_path to a .prproj that already contains a video clip."
        )
    if image_template is None:
        image_template = video_template

    for source_path in source_paths:
        if not is_supported_visual_media_path(str(source_path)):
            warnings.append(f"Skipped unsupported media type: {source_path.name}")
            continue
        kind = "image" if is_supported_image_media_path(str(source_path)) else "video"
        existing_media = media_by_path.get(_media_path_key(source_path))
        duration_seconds = _resolve_import_duration(
            source_path,
            kind=kind,
            existing_media=existing_media,
            object_id_lookup=object_id_lookup,
            still_duration_seconds=still_duration_seconds,
            duration_resolver=duration_resolver,
        )
        duration_ticks = max(1, seconds_to_ticks(duration_seconds))
        source_in = seconds_to_ticks(_STILL_IN_POINT_SECONDS) if kind == "image" else 0
        source_out = source_in + duration_ticks
        clip_template = image_template if kind == "image" else video_template
        _append_imported_clip(
            root,
            video_track=video_track,
            audio_track=audio_track if kind == "video" else None,
            video_template=clip_template,
            audio_template=audio_template if kind == "video" else None,
            source_path=source_path,
            existing_media=existing_media,
            kind=kind,
            timeline_start=cursor,
            timeline_end=cursor + duration_ticks,
            source_in=source_in,
            source_out=source_out,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
            id_allocator=id_allocator,
            project_path=output_project_path,
        )
        imported.append(
            MediaImportItem(
                requested_name=source_path.name,
                source_path=source_path,
                reused_existing_media=existing_media is not None,
                duration_seconds=duration_seconds,
                kind=kind,
            )
        )
        if existing_media is None:
            latest = _latest_media_node_for_path(root, source_path)
            if latest is not None:
                media_by_path[_media_path_key(source_path)] = latest
        cursor += duration_ticks

    _update_sequence_duration_metadata(root, sequence_node, new_total_duration=cursor)
    if created_sequence:
        warnings.append(f"Created sequence '{sequence_name}'.")
    _rewire_dangling_sequence_urefs(root, sequence_node.attrib.get("ObjectUID"))
    _drop_unresolved_optional_refs(root)
    _assert_project_refs_resolved(root)

    output_project_path.parent.mkdir(parents=True, exist_ok=True)
    output_project_path.write_bytes(gzip.compress(ET.tostring(root, encoding="utf-8", xml_declaration=True)))
    return output_project_path, imported, warnings


def _clone_empty_sequence(
    root: ET.Element,
    *,
    new_sequence_name: str,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
) -> ET.Element:
    template_name = _choose_sequence_template_name(root)
    cloned_sequence = clone_named_sequence(
        root,
        source_sequence_name=template_name,
        new_sequence_name=new_sequence_name,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    )
    updated_id_lookup = build_project_object_id_lookup(root)
    updated_uid_lookup = build_project_object_uid_lookup(root)
    _clear_sequence_track_items(
        cloned_sequence,
        object_id_lookup=updated_id_lookup,
        object_uid_lookup=updated_uid_lookup,
    )
    _drop_orphan_track_items(root)
    return cloned_sequence


def _choose_sequence_template_name(root: ET.Element) -> str:
    names = list_named_project_sequence_names(root)
    if not names:
        raise PremiereProjectError("No named sequences were found in the Premiere project.")
    for preferred in ("lib", "Library"):
        for name in names:
            if name.casefold() == preferred.casefold():
                return name
    return names[0]


def _clear_sequence_track_items(
    sequence_node: ET.Element,
    *,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
) -> None:
    for track_group_index in (0, 1):
        for _index, track_node in get_project_track_nodes(
            sequence_node,
            track_group_index=track_group_index,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
        ):
            container = _ensure_track_items_container(track_node)
            if container is None:
                continue
            for ref in list(iter_project_track_item_refs(track_node)):
                container.remove(ref)
            _reindex_track_items(container)


def _drop_orphan_track_items(root: ET.Element) -> None:
    referenced = {
        element.attrib.get("ObjectRef")
        for element in root.iter("TrackItem")
        if element.attrib.get("ObjectRef")
    }
    for child in list(root):
        if "TrackItem" in child.tag and child.attrib.get("ObjectID") not in referenced:
            root.remove(child)


def _require_first_track(
    sequence_node: ET.Element,
    *,
    track_group_index: int,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
    label: str,
) -> ET.Element:
    tracks = get_project_track_nodes(
        sequence_node,
        track_group_index=track_group_index,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    )
    if not tracks:
        raise PremiereProjectError(f"Sequence has no {label} track for import.")
    return tracks[0][1]


def _sequence_end_ticks(
    sequence_node: ET.Element,
    *,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
) -> int:
    end = 0
    for _index, track_node in get_project_track_nodes(
        sequence_node,
        track_group_index=0,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    ):
        for ref in iter_project_track_item_refs(track_node):
            node = object_id_lookup.get(ref.attrib.get("ObjectRef", ""))
            if node is None:
                continue
            _start, item_end = resolve_project_track_item_timeline(node)
            end = max(end, item_end)
    return end


def _media_path_key(path: Path | str) -> str:
    text = str(path or "").strip().strip('"')
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


def _media_node_path_key(media_node: ET.Element) -> str:
    for tag_name in ("ActualMediaFilePath", "FilePath"):
        value = (media_node.findtext(f"./{tag_name}") or "").strip()
        if value:
            return _media_path_key(value)
    return ""


def _index_media_by_path(root: ET.Element) -> dict[str, ET.Element]:
    index: dict[str, ET.Element] = {}
    for media_node in root.iter("Media"):
        key = _media_node_path_key(media_node)
        if key:
            index[key] = media_node
    return index


def _latest_media_node_for_path(root: ET.Element, source_path: Path) -> ET.Element | None:
    key = _media_path_key(source_path)
    found: ET.Element | None = None
    for media_node in root.iter("Media"):
        if _media_node_path_key(media_node) == key:
            found = media_node
    return found


def _resolve_import_duration(
    source_path: Path,
    *,
    kind: str,
    existing_media: ET.Element | None,
    object_id_lookup: dict[str, ET.Element],
    still_duration_seconds: float,
    duration_resolver: Callable[[Path], float | None] | None,
) -> float:
    if kind == "image":
        return max(0.1, still_duration_seconds)
    if duration_resolver is not None:
        probed = duration_resolver(source_path)
        if probed and probed > 0:
            return probed
    if existing_media is not None:
        for source_node in object_id_lookup.values():
            media_ref = source_node.find("./MediaSource/Media")
            if media_ref is None:
                continue
            if media_ref.attrib.get("ObjectURef") != existing_media.attrib.get("ObjectUID"):
                continue
            original = source_node.findtext("./OriginalDuration")
            if original and original.isdigit():
                return max(0.1, int(original) / PREMIERE_TICKS_PER_SECOND)
    return _DEFAULT_VIDEO_SECONDS


def _maybe_adopt_template_project(
    root: ET.Element,
    *,
    source_project_path: Path,
    output_project_path: Path,
    template_project_path: Path | None,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
) -> tuple[ET.Element, dict[str, ET.Element], dict[str, ET.Element], bool, list[str]]:
    video_template, _image_template, _audio_template = _find_templates_in_root(
        root,
        preferred_sequence=None,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    )
    if video_template is not None:
        return root, object_id_lookup, object_uid_lookup, False, []

    donor_path = _discover_template_project(
        source_project_path=source_project_path,
        output_project_path=output_project_path,
        explicit_path=template_project_path,
    )
    if donor_path is None:
        return root, object_id_lookup, object_uid_lookup, False, []

    donor_root, donor_id_lookup, donor_uid_lookup = _load_template_lookups(donor_path)
    video_template, _image_template, _audio_template = _find_templates_in_root(
        donor_root,
        preferred_sequence=None,
        object_id_lookup=donor_id_lookup,
        object_uid_lookup=donor_uid_lookup,
    )
    if video_template is None and template_project_path is not None:
        donor_path = _discover_template_project(
            source_project_path=source_project_path,
            output_project_path=output_project_path,
            explicit_path=None,
        )
        if donor_path is None:
            return root, object_id_lookup, object_uid_lookup, False, []
        donor_root, donor_id_lookup, donor_uid_lookup = _load_template_lookups(donor_path)
        video_template, _image_template, _audio_template = _find_templates_in_root(
            donor_root,
            preferred_sequence=None,
            object_id_lookup=donor_id_lookup,
            object_uid_lookup=donor_uid_lookup,
        )
    if video_template is None:
        return root, object_id_lookup, object_uid_lookup, False, []

    return (
        donor_root,
        donor_id_lookup,
        donor_uid_lookup,
        True,
        [
            f"Source project has no timeline clips; using '{donor_path.name}' as the project base."
        ],
    )


def _load_template_lookups(path: Path) -> tuple[ET.Element, dict[str, ET.Element], dict[str, ET.Element]]:
    donor_root = load_premiere_project_root(path)
    return (
        donor_root,
        build_project_object_id_lookup(donor_root),
        build_project_object_uid_lookup(donor_root),
    )


def _discover_template_project(
    *,
    source_project_path: Path,
    output_project_path: Path,
    explicit_path: Path | None,
) -> Path | None:
    if explicit_path is not None:
        resolved = Path(explicit_path)
        if not resolved.exists():
            raise PremiereProjectError(f"template_project_path was not found: {resolved}")
        if resolved.resolve() != source_project_path.resolve():
            return resolved

    folder = source_project_path.parent
    if not folder.is_dir():
        return None
    skip = {source_project_path.resolve(), output_project_path.resolve()}
    prefix = source_project_path.stem.split("_")[0].casefold()
    candidates: list[Path] = []
    for path in folder.glob("*.prproj"):
        if path.resolve() in skip:
            continue
        if "damaged" in path.stem.casefold():
            continue
        candidates.append(path)
    candidates.sort(
        key=lambda path: (
            path.stem.casefold().startswith(prefix),
            path.stat().st_mtime,
        ),
        reverse=True,
    )
    for path in candidates:
        try:
            donor_root = load_premiere_project_root(path)
        except Exception:
            continue
        donor_id_lookup = build_project_object_id_lookup(donor_root)
        donor_uid_lookup = build_project_object_uid_lookup(donor_root)
        video_template, _image_template, _audio_template = _find_templates_in_root(
            donor_root,
            preferred_sequence=None,
            object_id_lookup=donor_id_lookup,
            object_uid_lookup=donor_uid_lookup,
        )
        if video_template is not None:
            return path
    return None


def _find_templates_in_root(
    root: ET.Element,
    *,
    preferred_sequence: ET.Element | None,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
) -> tuple[_ClipTemplate | None, _ClipTemplate | None, _ClipTemplate | None]:
    video_template: _ClipTemplate | None = None
    image_template: _ClipTemplate | None = None
    audio_template: _ClipTemplate | None = None
    names: list[str] = []
    if preferred_sequence is not None:
        names.append(preferred_sequence.findtext("./Name") or "")
    names.extend(list_named_project_sequence_names(root))
    seen: set[str] = set()
    for candidate_name in names:
        key = candidate_name.casefold()
        if key in seen:
            continue
        seen.add(key)
        candidate = find_project_sequence_node(root, candidate_name)
        if candidate is None:
            continue
        video_items = _collect_track_items(
            candidate,
            track_group_index=0,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
        )
        audio_items = _collect_track_items(
            candidate,
            track_group_index=1,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
        )
        for item in video_items:
            name = resolve_project_track_item_name(item, object_id_lookup)
            path = resolve_project_track_item_source_path(
                item,
                object_id_lookup,
                object_uid_lookup,
            )
            template = _ClipTemplate(item, object_id_lookup, object_uid_lookup)
            if is_supported_image_media_path(path or name):
                if image_template is None:
                    image_template = template
            elif video_template is None:
                video_template = template
                audio_template = _wrap_audio_template(
                    _matching_audio_item(item, audio_items, object_id_lookup),
                    object_id_lookup,
                    object_uid_lookup,
                )
            if video_template is not None and image_template is not None:
                return video_template, image_template, audio_template
    return video_template, image_template, audio_template


def _wrap_audio_template(
    item: ET.Element | None,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
) -> _ClipTemplate | None:
    if item is None:
        return None
    return _ClipTemplate(item, object_id_lookup, object_uid_lookup)


def _lookup_object(object_ref: str, *lookups: dict[str, ET.Element]) -> ET.Element | None:
    if not object_ref:
        return None
    for lookup in lookups:
        node = lookup.get(object_ref)
        if node is not None:
            return node
    return None


def _find_clip_project_item_in_lookup(
    uid_lookup: dict[str, ET.Element] | None,
    masterclip_uid: str,
) -> ET.Element | None:
    if not uid_lookup or not masterclip_uid:
        return None
    for node in uid_lookup.values():
        if node.tag != "ClipProjectItem":
            continue
        master_ref = node.find("./MasterClip")
        if master_ref is not None and master_ref.attrib.get("ObjectURef") == masterclip_uid:
            return node
    return None


def _project_object_ids(root: ET.Element) -> tuple[set[str], set[str]]:
    object_ids = {node.attrib.get("ObjectID") for node in root.iter() if node.attrib.get("ObjectID")}
    object_uids = {node.attrib.get("ObjectUID") for node in root.iter() if node.attrib.get("ObjectUID")}
    return object_ids, object_uids


def _drop_unresolved_optional_refs(
    node: ET.Element,
    *,
    object_ids: set[str] | None = None,
    object_uids: set[str] | None = None,
) -> None:
    known_ids = object_ids if object_ids is not None else _project_object_ids(node)[0]
    known_uids = object_uids if object_uids is not None else _project_object_ids(node)[1]
    for parent in node.iter():
        for child in list(parent):
            if child.tag not in _OPTIONAL_CLIP_METADATA_TAGS:
                continue
            object_ref = child.attrib.get("ObjectRef")
            object_uref = child.attrib.get("ObjectURef")
            dangling_ref = bool(object_ref) and object_ref not in known_ids
            dangling_uref = bool(object_uref) and object_uref not in known_uids
            if dangling_ref or dangling_uref:
                parent.remove(child)


def _rewire_dangling_sequence_urefs(root: ET.Element, sequence_uid: str | None) -> None:
    if not sequence_uid:
        return
    known_uids = {node.attrib.get("ObjectUID") for node in root.iter() if node.attrib.get("ObjectUID")}
    if sequence_uid not in known_uids:
        return
    for element in root.iter():
        if element.tag != "Sequence":
            continue
        object_uref = element.attrib.get("ObjectURef")
        if object_uref and object_uref not in known_uids:
            element.attrib["ObjectURef"] = sequence_uid


def _assert_project_refs_resolved(root: ET.Element) -> None:
    object_ids = {node.attrib.get("ObjectID") for node in root.iter() if node.attrib.get("ObjectID")}
    object_uids = {node.attrib.get("ObjectUID") for node in root.iter() if node.attrib.get("ObjectUID")}
    missing: list[str] = []
    for element in root.iter():
        object_ref = element.attrib.get("ObjectRef")
        if object_ref and object_ref not in object_ids:
            missing.append(f"{element.tag}@ObjectRef={object_ref}")
        object_uref = element.attrib.get("ObjectURef")
        if object_uref and object_uref not in object_uids:
            missing.append(f"{element.tag}@ObjectURef={object_uref}")
    if missing:
        preview = ", ".join(missing[:8])
        raise PremiereProjectError(f"Generated project has unresolved references: {preview}")


def _collect_track_items(
    sequence_node: ET.Element,
    *,
    track_group_index: int,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
) -> list[ET.Element]:
    items: list[ET.Element] = []
    for _index, track_node in get_project_track_nodes(
        sequence_node,
        track_group_index=track_group_index,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    ):
        for ref in iter_project_track_item_refs(track_node):
            node = object_id_lookup.get(ref.attrib.get("ObjectRef", ""))
            if node is not None:
                items.append(node)
    return items


def _matching_audio_item(
    video_item: ET.Element,
    audio_items: list[ET.Element],
    object_id_lookup: dict[str, ET.Element],
) -> ET.Element | None:
    video_name = resolve_project_track_item_name(video_item, object_id_lookup).casefold()
    video_start, video_end = resolve_project_track_item_timeline(video_item)
    for audio_item in audio_items:
        if resolve_project_track_item_name(audio_item, object_id_lookup).casefold() != video_name:
            continue
        audio_start, audio_end = resolve_project_track_item_timeline(audio_item)
        if audio_start == video_start and audio_end == video_end:
            return audio_item
    for audio_item in audio_items:
        if resolve_project_track_item_name(audio_item, object_id_lookup).casefold() == video_name:
            return audio_item
    return audio_items[0] if audio_items else None


def _append_imported_clip(
    root: ET.Element,
    *,
    video_track: ET.Element,
    audio_track: ET.Element | None,
    video_template: _ClipTemplate,
    audio_template: _ClipTemplate | None,
    source_path: Path,
    existing_media: ET.Element | None,
    kind: str,
    timeline_start: int,
    timeline_end: int,
    source_in: int,
    source_out: int,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
    project_path: Path,
) -> None:
    media_node = existing_media if existing_media is not None else _clone_media_node(
        root,
        template=video_template,
        source_path=source_path,
        kind=kind,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
        id_allocator=id_allocator,
        project_path=project_path,
    )
    master_clip = None
    if existing_media is not None:
        master_clip = _find_masterclip_for_media(root, existing_media.attrib.get("ObjectUID", ""))
        if master_clip is None:
            master_clip = _find_masterclip_by_name(root, source_path.name)
    if master_clip is None:
        master_clip = _clone_master_clip_for_import(
            root,
            template=video_template,
            media_node=media_node,
            source_path=source_path,
            source_in=source_in,
            source_out=source_out,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
            id_allocator=id_allocator,
        )
    _place_track_item(
        root,
        track_node=video_track,
        template=video_template,
        media_node=media_node,
        master_clip=master_clip,
        source_path=source_path,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
        source_in=source_in,
        source_out=source_out,
        object_id_lookup=object_id_lookup,
        id_allocator=id_allocator,
        clone_audio_source=False,
    )
    if audio_track is not None and audio_template is not None:
        _place_track_item(
            root,
            track_node=audio_track,
            template=audio_template,
            media_node=media_node,
            master_clip=master_clip,
            source_path=source_path,
            timeline_start=timeline_start,
            timeline_end=timeline_end,
            source_in=source_in,
            source_out=source_out,
            object_id_lookup=object_id_lookup,
            id_allocator=id_allocator,
            clone_audio_source=True,
        )


def _clone_media_node(
    root: ET.Element,
    *,
    template: _ClipTemplate,
    source_path: Path,
    kind: str,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
    project_path: Path,
) -> ET.Element:
    template_clip = resolve_project_track_item_clip(template.item, template.object_id_lookup)
    template_media = (
        resolve_project_clip_media_node(
            template_clip,
            template.object_id_lookup,
            template.object_uid_lookup,
        )
        if template_clip is not None
        else None
    )
    if template_media is None:
        raise PremiereProjectError(f"Could not clone media for '{source_path.name}'.")
    new_media = copy.deepcopy(template_media)
    new_media.attrib["ObjectUID"] = str(uuid4())
    _set_child_text(new_media, "Title", source_path.name)
    _set_child_text(new_media, "FilePath", str(source_path))
    _set_child_text(new_media, "ActualMediaFilePath", str(source_path))
    _set_all_direct_child_text(new_media, "RelativePath", _media_relative_path(source_path, project_path))
    file_key = new_media.find("./FileKey")
    if file_key is not None:
        file_key.text = str(uuid4())
    _refresh_media_state_ids(new_media)
    if kind == "image":
        _set_child_text(new_media, "Infinite", "true")
    else:
        infinite = new_media.find("./Infinite")
        if infinite is not None:
            infinite.text = "false"
    _clone_media_streams(
        root,
        new_media,
        source_path=source_path,
        kind=kind,
        object_id_lookup=object_id_lookup,
        template_object_id_lookup=template.object_id_lookup,
        id_allocator=id_allocator,
    )
    _insert_project_object_near_same_type(root, new_media)
    object_uid_lookup[new_media.attrib["ObjectUID"]] = new_media
    return new_media


def _media_relative_path(source_path: Path, project_path: Path) -> str:
    try:
        return os.path.relpath(str(source_path.resolve()), str(project_path.parent.resolve()))
    except ValueError:
        return str(source_path)


def _set_all_direct_child_text(node: ET.Element, tag_name: str, text_value: str) -> None:
    children = [child for child in list(node) if child.tag == tag_name]
    if not children:
        child = ET.SubElement(node, tag_name)
        child.text = text_value
        return
    for child in children:
        child.text = text_value


def _refresh_media_state_ids(media_node: ET.Element) -> None:
    state_id = str(uuid4())
    _set_child_text(media_node, "ContentAndMetadataState", state_id)
    modification = media_node.find("./ModificationState")
    if modification is not None:
        modification.attrib["BinaryHash"] = str(uuid4())
        modification.text = base64.b64encode(state_id.encode("utf-16-le")).decode("ascii")


def _clone_media_streams(
    root: ET.Element,
    media_node: ET.Element,
    *,
    source_path: Path,
    kind: str,
    object_id_lookup: dict[str, ET.Element],
    template_object_id_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
) -> None:
    for tag_name in ("VideoStream", "AudioStream"):
        stream_ref = media_node.find(f"./{tag_name}")
        if stream_ref is None:
            continue
        template_stream = _lookup_object(
            stream_ref.attrib.get("ObjectRef", ""),
            template_object_id_lookup,
            object_id_lookup,
        )
        if template_stream is None:
            continue
        cloned = copy.deepcopy(template_stream)
        cloned.attrib["ObjectID"] = id_allocator.allocate()
        if tag_name == "AudioStream":
            for peak_tag in ("ConformedAudioPath", "PeakFilePath"):
                peak = cloned.find(f"./{peak_tag}")
                if peak is not None:
                    peak.text = ""
        if tag_name == "VideoStream" and kind == "image":
            frame_rect = _probe_image_frame_rect(source_path)
            if frame_rect is not None:
                _set_child_text(cloned, "FrameRect", frame_rect)
        _insert_project_object_near_same_type(root, cloned)
        object_id_lookup[cloned.attrib["ObjectID"]] = cloned
        stream_ref.attrib["ObjectRef"] = cloned.attrib["ObjectID"]


def _probe_image_frame_rect(source_path: Path) -> str | None:
    try:
        width, height = _read_image_size(source_path)
    except (OSError, struct.error, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return f"0,0,{width},{height}"


def _read_image_size(source_path: Path) -> tuple[int, int]:
    data = source_path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data[:2] == b"\xff\xd8":
        return _jpeg_size(data)
    raise ValueError(f"Unsupported image header: {source_path.name}")


def _jpeg_size(data: bytes) -> tuple[int, int]:
    offset = 2
    while offset + 9 < len(data):
        if data[offset] != 0xFF:
            break
        marker = data[offset + 1]
        if marker in {0xC0, 0xC1, 0xC2}:
            height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
            return int(width), int(height)
        if marker == 0xD8:
            offset += 2
            continue
        if offset + 4 > len(data):
            break
        length = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
        offset += 2 + length
    raise ValueError("JPEG size marker was not found.")


def _find_masterclip_for_media(root: ET.Element, media_uid: str) -> ET.Element | None:
    if not media_uid:
        return None
    found: ET.Element | None = None
    for node in root.iter("MasterClip"):
        for element in node.iter():
            if element.attrib.get("ObjectURef") == media_uid:
                found = node
                break
    return found


def _find_masterclip_by_name(root: ET.Element, file_name: str) -> ET.Element | None:
    key = file_name.casefold()
    found: ET.Element | None = None
    for node in root.iter("MasterClip"):
        name = (node.findtext("./Name") or "").strip()
        if name.casefold() == key:
            found = node
    return found


def _clone_master_clip_for_import(
    root: ET.Element,
    *,
    template: _ClipTemplate,
    media_node: ET.Element,
    source_path: Path,
    source_in: int,
    source_out: int,
    object_id_lookup: dict[str, ET.Element],
    object_uid_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
) -> ET.Element | None:
    template_subclip = resolve_project_track_item_subclip(template.item, template.object_id_lookup)
    if template_subclip is None:
        return None
    master_ref = template_subclip.find("./MasterClip")
    if master_ref is None:
        return None
    template_master = template.object_uid_lookup.get(master_ref.attrib.get("ObjectURef", ""))
    if template_master is None:
        return None

    new_master = copy.deepcopy(template_master)
    new_master.attrib["ObjectUID"] = str(uuid4())
    _set_child_text(new_master, "Name", source_path.name)
    duration_ticks = max(1, source_out - source_in)
    for element in new_master.iter():
        object_ref = element.attrib.get("ObjectRef")
        if not object_ref:
            continue
        source_obj = template.object_id_lookup.get(object_ref)
        if source_obj is None:
            source_obj = object_id_lookup.get(object_ref)
        if source_obj is None:
            continue
        if source_obj.tag in {"VideoClip", "AudioClip"}:
            cloned_clip = copy.deepcopy(source_obj)
            cloned_clip.attrib["ObjectID"] = id_allocator.allocate()
            source_el = cloned_clip.find("./Clip/Source")
            if source_el is not None:
                new_source = _clone_media_source(
                    root,
                    template_clip=source_obj,
                    media_node=media_node,
                    template_object_id_lookup=template.object_id_lookup,
                    object_id_lookup=object_id_lookup,
                    id_allocator=id_allocator,
                    prefer_audio=source_obj.tag == "AudioClip",
                    duration_ticks=duration_ticks,
                )
                source_el.attrib["ObjectRef"] = new_source.attrib["ObjectID"]
            clip_id_node = cloned_clip.find("./Clip/ClipID")
            if clip_id_node is not None:
                clip_id_node.text = str(uuid4())
            _insert_project_object_near_same_type(root, cloned_clip)
            object_id_lookup[cloned_clip.attrib["ObjectID"]] = cloned_clip
            element.attrib["ObjectRef"] = cloned_clip.attrib["ObjectID"]
            continue
        cloned_obj = copy.deepcopy(source_obj)
        if cloned_obj.attrib.get("ObjectID"):
            cloned_obj.attrib["ObjectID"] = id_allocator.allocate()
            object_id_lookup[cloned_obj.attrib["ObjectID"]] = cloned_obj
        _insert_project_object_near_same_type(root, cloned_obj)
        if cloned_obj.attrib.get("ObjectID"):
            element.attrib["ObjectRef"] = cloned_obj.attrib["ObjectID"]

    known_ids, known_uids = _project_object_ids(root)
    _drop_unresolved_optional_refs(new_master, object_ids=known_ids, object_uids=known_uids)
    _insert_project_object_near_same_type(root, new_master)
    object_uid_lookup[new_master.attrib["ObjectUID"]] = new_master
    _clone_master_project_item(
        root,
        template_master=template_master,
        new_master=new_master,
        source_path=source_path,
        object_uid_lookup=object_uid_lookup,
        template_object_uid_lookup=template.object_uid_lookup,
    )
    return new_master


def _clone_master_project_item(
    root: ET.Element,
    *,
    template_master: ET.Element,
    new_master: ET.Element,
    source_path: Path,
    object_uid_lookup: dict[str, ET.Element],
    template_object_uid_lookup: dict[str, ET.Element] | None = None,
) -> None:
    master_uid = template_master.attrib.get("ObjectUID", "")
    template_item = _find_sequence_project_item(root, master_uid)
    if template_item is None:
        template_item = _find_clip_project_item_in_lookup(template_object_uid_lookup, master_uid)
    if template_item is None:
        template_item = next((node for node in root.iter("ClipProjectItem")), None)
    if template_item is None:
        return
    new_item = copy.deepcopy(template_item)
    new_item.attrib["ObjectUID"] = str(uuid4())
    item_master = new_item.find("./MasterClip")
    if item_master is not None:
        item_master.attrib["ObjectURef"] = new_master.attrib["ObjectUID"]
    payload = new_item.find("./ProjectItem")
    _set_child_text(payload if payload is not None else new_item, "Name", source_path.name)
    known_ids, known_uids = _project_object_ids(root)
    known_uids.update(uid for uid in (new_master.attrib.get("ObjectUID"), new_item.attrib.get("ObjectUID")) if uid)
    _drop_unresolved_optional_refs(new_item, object_ids=known_ids, object_uids=known_uids)
    _set_project_item_grid_order(root, new_item)
    _insert_project_object_near_same_type(root, new_item)
    object_uid_lookup[new_item.attrib["ObjectUID"]] = new_item
    _append_project_item_to_root(root, new_item.attrib["ObjectUID"])


def _place_track_item(
    root: ET.Element,
    *,
    track_node: ET.Element,
    template: _ClipTemplate,
    media_node: ET.Element,
    master_clip: ET.Element | None,
    source_path: Path,
    timeline_start: int,
    timeline_end: int,
    source_in: int,
    source_out: int,
    object_id_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
    clone_audio_source: bool,
) -> None:
    template_subclip = resolve_project_track_item_subclip(template.item, template.object_id_lookup)
    template_clip = resolve_project_track_item_clip(template.item, template.object_id_lookup)
    if template_subclip is None or template_clip is None:
        raise PremiereProjectError(f"Could not clone a track item for '{source_path.name}'.")

    new_item = copy.deepcopy(template.item)
    new_subclip = copy.deepcopy(template_subclip)
    new_clip = copy.deepcopy(template_clip)
    new_item.attrib["ObjectID"] = id_allocator.allocate()
    new_subclip.attrib["ObjectID"] = id_allocator.allocate()
    new_clip.attrib["ObjectID"] = id_allocator.allocate()

    subclip_ref = new_item.find("./ClipTrackItem/SubClip")
    if subclip_ref is None:
        raise PremiereProjectError("Cloned track item is missing SubClip.")
    subclip_ref.attrib["ObjectRef"] = new_subclip.attrib["ObjectID"]
    clip_ref = new_subclip.find("./Clip")
    if clip_ref is None:
        raise PremiereProjectError("Cloned SubClip is missing Clip.")
    clip_ref.attrib["ObjectRef"] = new_clip.attrib["ObjectID"]
    _set_child_text(new_subclip, "Name", source_path.name)
    if master_clip is not None:
        master_ref = new_subclip.find("./MasterClip")
        if master_ref is None:
            master_ref = ET.SubElement(new_subclip, "MasterClip")
        master_ref.attrib["ObjectURef"] = master_clip.attrib["ObjectUID"]

    source_ref = new_clip.find("./Clip/Source")
    if source_ref is None:
        raise PremiereProjectError("Cloned clip is missing Source.")
    new_source = _clone_media_source(
        root,
        template_clip=template_clip,
        media_node=media_node,
        template_object_id_lookup=template.object_id_lookup,
        object_id_lookup=object_id_lookup,
        id_allocator=id_allocator,
        prefer_audio=clone_audio_source,
        duration_ticks=source_out - source_in,
    )
    source_ref.attrib["ObjectRef"] = new_source.attrib["ObjectID"]

    clip_payload = new_clip.find("./Clip")
    if clip_payload is None:
        raise PremiereProjectError("Cloned clip is missing Clip payload.")
    _set_child_text(clip_payload, "InPoint", str(source_in))
    _set_child_text(clip_payload, "OutPoint", str(source_out))
    clip_id_node = clip_payload.find("./ClipID")
    if clip_id_node is not None:
        clip_id_node.text = str(uuid4())

    timeline_node = new_item.find("./ClipTrackItem/TrackItem")
    if timeline_node is None:
        raise PremiereProjectError("Cloned track item is missing timeline bounds.")
    _set_track_item_boundary(timeline_node, "Start", timeline_start)
    _set_track_item_boundary(timeline_node, "End", timeline_end)
    if not clone_audio_source:
        _clone_and_sanitize_video_components(
            root,
            new_item,
            object_id_lookup=object_id_lookup,
            template_object_id_lookup=template.object_id_lookup,
            id_allocator=id_allocator,
        )
        _apply_imported_frame_rect(new_item, source_path)

    _insert_project_object_near_same_type(root, new_item)
    _insert_project_object_near_same_type(root, new_subclip)
    _insert_project_object_near_same_type(root, new_clip)
    object_id_lookup[new_item.attrib["ObjectID"]] = new_item
    object_id_lookup[new_subclip.attrib["ObjectID"]] = new_subclip
    object_id_lookup[new_clip.attrib["ObjectID"]] = new_clip

    container = _ensure_track_items_container(track_node)
    if container is None:
        raise PremiereProjectError("Could not create a TrackItems container for import.")
    ref = ET.Element("TrackItem")
    ref.attrib["ObjectRef"] = new_item.attrib["ObjectID"]
    container.append(ref)
    _reindex_track_items(container)


def _clone_and_sanitize_video_components(
    root: ET.Element,
    track_item: ET.Element,
    *,
    object_id_lookup: dict[str, ET.Element],
    template_object_id_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
) -> None:
    owner_ref = track_item.find("./ClipTrackItem/ComponentOwner/Components")
    if owner_ref is None:
        return
    source_chain = _lookup_object(
        owner_ref.attrib.get("ObjectRef", ""),
        template_object_id_lookup,
        object_id_lookup,
    )
    if source_chain is None:
        return
    new_chain = copy.deepcopy(source_chain)
    new_chain.attrib["ObjectID"] = id_allocator.allocate()
    components_el = new_chain.find("./ComponentChain/Components")
    if components_el is None:
        _insert_project_object_near_same_type(root, new_chain)
        object_id_lookup[new_chain.attrib["ObjectID"]] = new_chain
        owner_ref.attrib["ObjectRef"] = new_chain.attrib["ObjectID"]
        return

    kept: list[ET.Element] = []
    for ref in list(components_el):
        source_component = _lookup_object(
            ref.attrib.get("ObjectRef", ""),
            template_object_id_lookup,
            object_id_lookup,
        )
        if source_component is None:
            continue
        if not _is_intrinsic_video_component(source_component):
            continue
        cloned = _clone_filter_component(
            root,
            source_component,
            object_id_lookup=object_id_lookup,
            template_object_id_lookup=template_object_id_lookup,
            id_allocator=id_allocator,
        )
        _reset_intrinsic_component_params(cloned, object_id_lookup)
        kept.append(cloned)

    if not kept:
        for ref in list(components_el):
            source_component = _lookup_object(
                ref.attrib.get("ObjectRef", ""),
                template_object_id_lookup,
                object_id_lookup,
            )
            if source_component is None:
                continue
            kept.append(
                _clone_filter_component(
                    root,
                    source_component,
                    object_id_lookup=object_id_lookup,
                    template_object_id_lookup=template_object_id_lookup,
                    id_allocator=id_allocator,
                )
            )

    for child in list(components_el):
        components_el.remove(child)
    motion_component_id = ""
    for index, component in enumerate(kept):
        entry = ET.SubElement(components_el, "Component")
        entry.attrib["Index"] = str(index)
        entry.attrib["ObjectRef"] = component.attrib["ObjectID"]
        if _component_match_name(component) == "AE.ADBE Motion":
            motion_component_id = (component.findtext("./Component/ID") or "").strip()

    if motion_component_id:
        active = new_chain.find("./ComponentChain/Node/Properties/MZ.ComponentChain.ActiveComponentID")
        if active is not None:
            active.text = motion_component_id

    _insert_project_object_near_same_type(root, new_chain)
    object_id_lookup[new_chain.attrib["ObjectID"]] = new_chain
    owner_ref.attrib["ObjectRef"] = new_chain.attrib["ObjectID"]


def _clone_filter_component(
    root: ET.Element,
    source_component: ET.Element,
    *,
    object_id_lookup: dict[str, ET.Element],
    template_object_id_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
) -> ET.Element:
    cloned = copy.deepcopy(source_component)
    cloned.attrib["ObjectID"] = id_allocator.allocate()
    params = cloned.find("./Component/Params")
    if params is not None:
        for param_ref in list(params):
            source_param = _lookup_object(
                param_ref.attrib.get("ObjectRef", ""),
                template_object_id_lookup,
                object_id_lookup,
            )
            if source_param is None:
                continue
            cloned_param = copy.deepcopy(source_param)
            cloned_param.attrib["ObjectID"] = id_allocator.allocate()
            _insert_project_object_near_same_type(root, cloned_param)
            object_id_lookup[cloned_param.attrib["ObjectID"]] = cloned_param
            param_ref.attrib["ObjectRef"] = cloned_param.attrib["ObjectID"]
    _insert_project_object_near_same_type(root, cloned)
    object_id_lookup[cloned.attrib["ObjectID"]] = cloned
    return cloned


def _reset_intrinsic_component_params(
    component: ET.Element,
    object_id_lookup: dict[str, ET.Element],
) -> None:
    params = component.find("./Component/Params")
    if params is None:
        return
    for param_ref in list(params):
        param = object_id_lookup.get(param_ref.attrib.get("ObjectRef", ""))
        if param is None:
            continue
        name = (param.findtext("./Name") or "").strip()
        default = _INTRINSIC_PARAM_DEFAULTS.get(name)
        if default is None:
            continue
        keyframes = param.find("./Keyframes")
        if keyframes is not None:
            param.remove(keyframes)
        varying = param.find("./IsTimeVarying")
        if varying is not None:
            varying.text = "false"
        _set_start_keyframe_value(param, default)
        current = param.find("./CurrentValue")
        if current is not None:
            current.text = default.rstrip(".")


def _set_start_keyframe_value(param: ET.Element, value: str) -> None:
    keyframe = param.find("./StartKeyframe")
    if keyframe is None or not (keyframe.text or "").strip():
        return
    parts = keyframe.text.strip().split(",")
    if len(parts) < 2:
        keyframe.text = value
        return
    parts[1] = value
    keyframe.text = ",".join(parts)


def _is_intrinsic_video_component(component: ET.Element) -> bool:
    if (component.findtext("./Component/Intrinsic") or "").strip().casefold() == "true":
        return True
    return _component_match_name(component) in _INTRINSIC_VIDEO_MATCH_NAMES


def _component_match_name(component: ET.Element) -> str:
    return (component.findtext("./MatchName") or component.findtext(".//MatchName") or "").strip()


def _apply_imported_frame_rect(track_item: ET.Element, source_path: Path) -> None:
    frame_rect = _probe_image_frame_rect(source_path)
    if frame_rect:
        _set_child_text(track_item, "FrameRect", frame_rect)


def _clone_media_source(
    root: ET.Element,
    *,
    template_clip: ET.Element,
    media_node: ET.Element,
    template_object_id_lookup: dict[str, ET.Element],
    object_id_lookup: dict[str, ET.Element],
    id_allocator: _ProjectObjectIdAllocator,
    prefer_audio: bool,
    duration_ticks: int,
) -> ET.Element:
    source_ref = template_clip.find("./Clip/Source")
    template_source = (
        template_object_id_lookup.get(source_ref.attrib.get("ObjectRef", "")) if source_ref is not None else None
    )
    if template_source is None:
        tag = "AudioMediaSource" if prefer_audio else "VideoMediaSource"
        template_source = next((node for node in template_object_id_lookup.values() if node.tag == tag), None)
    if template_source is None:
        tag = "AudioMediaSource" if prefer_audio else "VideoMediaSource"
        template_source = next((node for node in root.iter(tag)), None)
    if template_source is None:
        raise PremiereProjectError("Could not clone a media source for import.")
    new_source = copy.deepcopy(template_source)
    new_source.attrib["ObjectID"] = id_allocator.allocate()
    media_ref = new_source.find("./MediaSource/Media")
    if media_ref is None:
        media_source = new_source.find("./MediaSource")
        if media_source is None:
            media_source = ET.SubElement(new_source, "MediaSource")
        media_ref = ET.SubElement(media_source, "Media")
    media_ref.attrib["ObjectURef"] = media_node.attrib["ObjectUID"]
    _set_child_text(new_source, "OriginalDuration", str(max(1, duration_ticks)))
    _insert_project_object_near_same_type(root, new_source)
    object_id_lookup[new_source.attrib["ObjectID"]] = new_source
    return new_source
