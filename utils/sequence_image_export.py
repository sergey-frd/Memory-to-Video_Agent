from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from utils.premiere_project import (
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_group_indexes,
    get_project_track_nodes,
    is_supported_image_media_path,
    is_supported_video_media_path,
    iter_project_track_item_refs,
    load_premiere_project_root,
    resolve_project_track_item_name,
    resolve_project_track_item_source_path,
)

MEDIA_KIND_IMAGE = "image"
MEDIA_KIND_VIDEO = "video"
_ALL_MEDIA_KINDS = (MEDIA_KIND_IMAGE, MEDIA_KIND_VIDEO)
_VALID_CONFLICT_MODES = {"rename", "overwrite", "skip"}


@dataclass
class SequenceMediaReference:
    source_path: str
    clip_name: str
    media_kind: str
    track_group_index: int
    track_index: int
    clip_position: int
    exists: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class CopiedMediaRecord:
    source_path: str
    destination_path: str
    media_kind: str
    action: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SequenceMediaCopyResult:
    project_path: str
    sequence_name: str
    image_dest: str | None
    video_dest: str | None
    total_media_references: int
    image_reference_count: int
    video_reference_count: int
    unique_image_count: int
    unique_video_count: int
    copied: list[CopiedMediaRecord] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["copied"] = [record.to_dict() for record in self.copied]
        return payload

    def copied_count(self, media_kind: str | None = None) -> int:
        return sum(
            1
            for record in self.copied
            if record.action != "skip" and (media_kind is None or record.media_kind == media_kind)
        )


def classify_media_kind(source_path: str) -> str | None:
    if is_supported_image_media_path(source_path):
        return MEDIA_KIND_IMAGE
    if is_supported_video_media_path(source_path):
        return MEDIA_KIND_VIDEO
    return None


def collect_sequence_media_references(
    project_path: Path,
    sequence_name: str,
    *,
    kinds: tuple[str, ...] = _ALL_MEDIA_KINDS,
) -> tuple[str, list[SequenceMediaReference]]:
    root = load_premiere_project_root(project_path)
    sequence_node = find_project_sequence_node(root, sequence_name)
    if sequence_node is None:
        available = sorted(
            {
                (node.findtext("./Name") or "").strip()
                for node in root.iter("Sequence")
                if (node.findtext("./Name") or "").strip()
            }
        )
        available_hint = ", ".join(available) if available else "<none found>"
        raise PremiereProjectError(
            f"Sequence '{sequence_name}' was not found in project: {project_path}. "
            f"Available sequences: {available_hint}"
        )

    resolved_sequence_name = (sequence_node.findtext("./Name") or "").strip() or sequence_name
    object_id_lookup = build_project_object_id_lookup(root)
    object_uid_lookup = build_project_object_uid_lookup(root)

    references: list[SequenceMediaReference] = []
    for track_group_index in get_project_track_group_indexes(sequence_node):
        for track_index, track_node in get_project_track_nodes(
            sequence_node,
            track_group_index=track_group_index,
            object_id_lookup=object_id_lookup,
            object_uid_lookup=object_uid_lookup,
        ):
            for clip_position, track_item_ref in enumerate(
                iter_project_track_item_refs(track_node), start=1
            ):
                item_object_ref = track_item_ref.attrib.get("ObjectRef")
                if not item_object_ref:
                    continue
                track_item_node = object_id_lookup.get(item_object_ref)
                if track_item_node is None:
                    continue
                source_path = resolve_project_track_item_source_path(
                    track_item_node,
                    object_id_lookup,
                    object_uid_lookup,
                    project_path=project_path,
                )
                if not source_path:
                    continue
                media_kind = classify_media_kind(source_path)
                if media_kind is None or media_kind not in kinds:
                    continue
                clip_name = resolve_project_track_item_name(track_item_node, object_id_lookup)
                references.append(
                    SequenceMediaReference(
                        source_path=source_path,
                        clip_name=clip_name,
                        media_kind=media_kind,
                        track_group_index=track_group_index,
                        track_index=track_index,
                        clip_position=clip_position,
                        exists=Path(source_path).exists(),
                    )
                )

    return resolved_sequence_name, references


def copy_sequence_media(
    project_path: Path,
    sequence_name: str,
    *,
    image_dest: Path | None = None,
    video_dest: Path | None = None,
    on_conflict: str = "rename",
    dry_run: bool = False,
) -> SequenceMediaCopyResult:
    if on_conflict not in _VALID_CONFLICT_MODES:
        raise ValueError("on_conflict must be one of: rename, overwrite, skip")
    if image_dest is None and video_dest is None:
        raise ValueError("At least one destination (image_dest or video_dest) must be provided.")

    kinds: list[str] = []
    if image_dest is not None:
        kinds.append(MEDIA_KIND_IMAGE)
    if video_dest is not None:
        kinds.append(MEDIA_KIND_VIDEO)

    resolved_sequence_name, references = collect_sequence_media_references(
        project_path,
        sequence_name,
        kinds=tuple(kinds),
    )

    image_refs = [ref for ref in references if ref.media_kind == MEDIA_KIND_IMAGE]
    video_refs = [ref for ref in references if ref.media_kind == MEDIA_KIND_VIDEO]
    unique_images = _dedupe_references(image_refs)
    unique_videos = _dedupe_references(video_refs)

    result = SequenceMediaCopyResult(
        project_path=str(project_path),
        sequence_name=resolved_sequence_name,
        image_dest=str(image_dest) if image_dest is not None else None,
        video_dest=str(video_dest) if video_dest is not None else None,
        total_media_references=len(references),
        image_reference_count=len(image_refs),
        video_reference_count=len(video_refs),
        unique_image_count=len(unique_images),
        unique_video_count=len(unique_videos),
    )

    if image_dest is not None:
        _copy_unique_group(
            unique_images,
            destination_dir=image_dest,
            media_kind=MEDIA_KIND_IMAGE,
            on_conflict=on_conflict,
            dry_run=dry_run,
            result=result,
        )
    if video_dest is not None:
        _copy_unique_group(
            unique_videos,
            destination_dir=video_dest,
            media_kind=MEDIA_KIND_VIDEO,
            on_conflict=on_conflict,
            dry_run=dry_run,
            result=result,
        )

    return result


def run_copy_sequence_media_from_config(config_path: Path) -> tuple[list[SequenceMediaCopyResult], Path | None]:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    config = json.loads(config_path.read_text(encoding="utf-8"))

    project_path = _require_config_path(config, "project_path")
    on_conflict = str(config.get("on_conflict", "rename"))
    dry_run = bool(config.get("dry_run", False))
    copy_images = bool(config.get("copy_images", True))
    copy_videos = bool(config.get("copy_videos", False))

    image_dest = _optional_config_path(config, "image_dest") if copy_images else None
    video_dest = _optional_config_path(config, "video_dest") if copy_videos else None

    if copy_images and image_dest is None:
        raise ValueError("copy_images is enabled but 'image_dest' is missing from the config.")
    if copy_videos and video_dest is None:
        raise ValueError("copy_videos is enabled but 'video_dest' is missing from the config.")
    if image_dest is None and video_dest is None:
        raise ValueError("Nothing to copy: enable copy_images and/or copy_videos with matching destinations.")

    sequence_names = _resolve_config_sequence_names(config)

    results: list[SequenceMediaCopyResult] = []
    for sequence_name in sequence_names:
        results.append(
            copy_sequence_media(
                project_path,
                sequence_name,
                image_dest=image_dest,
                video_dest=video_dest,
                on_conflict=on_conflict,
                dry_run=dry_run,
            )
        )

    manifest_path = _optional_config_path(config, "manifest_path")
    if manifest_path is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_payload = {
            "config_path": str(config_path),
            "project_path": str(project_path),
            "prin_path": config.get("prin_path"),
            "copy_images": copy_images,
            "copy_videos": copy_videos,
            "on_conflict": on_conflict,
            "dry_run": dry_run,
            "sequences": [result.to_dict() for result in results],
        }
        manifest_path.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return results, manifest_path


def collect_sequence_image_references(
    project_path: Path,
    sequence_name: str,
) -> tuple[str, list[SequenceMediaReference]]:
    return collect_sequence_media_references(
        project_path,
        sequence_name,
        kinds=(MEDIA_KIND_IMAGE,),
    )


def copy_sequence_images_to_dir(
    project_path: Path,
    sequence_name: str,
    destination_dir: Path,
    *,
    on_conflict: str = "rename",
    dry_run: bool = False,
) -> SequenceMediaCopyResult:
    return copy_sequence_media(
        project_path,
        sequence_name,
        image_dest=destination_dir,
        on_conflict=on_conflict,
        dry_run=dry_run,
    )


def _copy_unique_group(
    references: list[SequenceMediaReference],
    *,
    destination_dir: Path,
    media_kind: str,
    on_conflict: str,
    dry_run: bool,
    result: SequenceMediaCopyResult,
) -> None:
    if references and not dry_run:
        destination_dir.mkdir(parents=True, exist_ok=True)

    used_destination_keys: set[str] = set()
    for reference in references:
        source = Path(reference.source_path)
        if not source.exists():
            result.missing_sources.append(reference.source_path)
            continue

        destination_path, action = _resolve_destination_path(
            source,
            destination_dir,
            on_conflict=on_conflict,
            used_destination_keys=used_destination_keys,
        )
        used_destination_keys.add(_normalize_path_key(str(destination_path)))

        if action == "skip":
            result.copied.append(
                CopiedMediaRecord(
                    source_path=str(source),
                    destination_path=str(destination_path),
                    media_kind=media_kind,
                    action="skip",
                )
            )
            continue

        if not dry_run:
            shutil.copy2(source, destination_path)

        result.copied.append(
            CopiedMediaRecord(
                source_path=str(source),
                destination_path=str(destination_path),
                media_kind=media_kind,
                action=("dry-run" if dry_run else action),
            )
        )


def _dedupe_references(references: list[SequenceMediaReference]) -> list[SequenceMediaReference]:
    unique: dict[str, SequenceMediaReference] = {}
    for reference in references:
        unique.setdefault(_normalize_path_key(reference.source_path), reference)
    return list(unique.values())


def _resolve_destination_path(
    source: Path,
    destination_dir: Path,
    *,
    on_conflict: str,
    used_destination_keys: set[str],
) -> tuple[Path, str]:
    candidate = destination_dir / source.name
    candidate_key = _normalize_path_key(str(candidate))
    already_used = candidate_key in used_destination_keys

    if not candidate.exists() and not already_used:
        return candidate, "copy"

    if on_conflict == "overwrite" and not already_used:
        return candidate, "overwrite"

    if on_conflict == "skip" and not already_used:
        return candidate, "skip"

    stem = source.stem
    suffix = source.suffix
    counter = 1
    while True:
        renamed = destination_dir / f"{stem}_{counter}{suffix}"
        renamed_key = _normalize_path_key(str(renamed))
        if not renamed.exists() and renamed_key not in used_destination_keys:
            return renamed, "copy-renamed"
        counter += 1


def _normalize_path_key(path: str) -> str:
    return str(Path(path)).casefold()


def _require_config_path(config: dict[str, object], key: str) -> Path:
    value = config.get(key)
    if not value:
        raise ValueError(f"Config is missing required key '{key}'.")
    return Path(str(value))


def _optional_config_path(config: dict[str, object], key: str) -> Path | None:
    value = config.get(key)
    if not value:
        return None
    return Path(str(value))


def _resolve_config_sequence_names(config: dict[str, object]) -> list[str]:
    names: list[str] = []
    sequence_jobs = config.get("sequence_jobs")
    if isinstance(sequence_jobs, list):
        for job in sequence_jobs:
            if isinstance(job, dict):
                name = job.get("sequence_name") or job.get("source_sequence_name")
            else:
                name = job
            if name:
                names.append(str(name))
    single_name = config.get("sequence") or config.get("sequence_name")
    if single_name:
        names.append(str(single_name))
    if not names:
        raise ValueError(
            "Config must define at least one sequence via 'sequence_jobs' or a 'sequence' key."
        )
    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(name)
    return deduped
