from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


STAGE_DIR_TIMESTAMP_RE = re.compile(r"^(?P<stem>.+)_(?P<stamp>\d{8}_\d{6})$")
IMAGE_TAG_RE = re.compile(r"@image\d+")
SCENARIO_VARIANT_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
REGENERATION_ASSETS_FAMILY_PREFIX = "regeneration_assets"


def _jerusalem_timezone():
    try:
        return ZoneInfo("Asia/Jerusalem")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=2), name="Asia/Jerusalem")


JERUSALEM_TZ = _jerusalem_timezone()


@dataclass(frozen=True)
class VideoImageReference:
    source_file: str
    tag: str


@dataclass(frozen=True)
class VideoSceneSpec:
    index: int
    duration_seconds: float
    start_seconds: float
    end_seconds: float
    description: str


@dataclass(frozen=True)
class ScenarioVariantSpec:
    variant_id: str
    label: str
    instruction: str


@dataclass(frozen=True)
class VideoPromptRequest:
    technical_preamble: str
    total_duration_seconds: float
    max_prompt_chars: int
    aspect_ratio: str
    regeneration_assets_dir: Path
    references: list[VideoImageReference]
    scenes: list[VideoSceneSpec]
    scenario_variants: list[ScenarioVariantSpec]


@dataclass(frozen=True)
class ReferenceContext:
    source_file: str
    source_stem: str
    tag: str
    stage_dir: Path
    stage_id: str
    description_excerpt: str
    scene_analysis_en: dict[str, Any]
    scene_analysis_ru: dict[str, Any]


@dataclass(frozen=True)
class GeneratedVideoPromptBundle:
    video_prompt_en: str
    video_prompt_ru: str


@dataclass(frozen=True)
class GeneratedSeedanceJsonBundle:
    seedance_prompt_json_en: str
    seedance_prompt_json_ru: str


def load_video_prompt_request(request_text: str) -> VideoPromptRequest:
    try:
        payload = json.loads(request_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Video prompt request is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Video prompt request must be a JSON object.")

    technical_preamble = str(payload.get("technical_preamble", "")).strip()
    if not technical_preamble:
        raise ValueError("Video prompt request must include a non-empty technical_preamble.")

    total_duration_seconds = _parse_positive_number(payload.get("total_duration_seconds"), "total_duration_seconds")
    max_prompt_chars = _parse_positive_int(payload.get("max_prompt_chars", 2000), "max_prompt_chars")
    aspect_ratio = str(payload.get("aspect_ratio", "16:9")).strip() or "16:9"
    regeneration_assets_dir_raw = payload.get("regeneration_assets_dir")
    if not regeneration_assets_dir_raw:
        raise ValueError("Video prompt request must include regeneration_assets_dir.")
    regeneration_assets_dir = Path(str(regeneration_assets_dir_raw))
    if not regeneration_assets_dir.exists():
        raise FileNotFoundError(f"regeneration_assets directory not found: {regeneration_assets_dir}")

    references_payload = payload.get("references")
    if not isinstance(references_payload, list) or not references_payload:
        raise ValueError("Video prompt request must include a non-empty references list.")
    references: list[VideoImageReference] = []
    seen_tags: set[str] = set()
    for item in references_payload:
        if not isinstance(item, dict):
            raise ValueError("Every references item must be a JSON object.")
        source_file = str(item.get("source_file", "")).strip()
        tag = str(item.get("tag", "")).strip()
        if not source_file:
            raise ValueError("Each references item must include source_file.")
        if not IMAGE_TAG_RE.fullmatch(tag):
            raise ValueError(f"Invalid image tag '{tag}'. Expected format like @image1.")
        if tag in seen_tags:
            raise ValueError(f"Duplicate image tag found: {tag}")
        seen_tags.add(tag)
        references.append(VideoImageReference(source_file=source_file, tag=tag))

    scenes_payload = payload.get("scenes")
    if not isinstance(scenes_payload, list) or not scenes_payload:
        raise ValueError("Video prompt request must include a non-empty scenes list.")
    scenes: list[VideoSceneSpec] = []
    current_start = 0.0
    for index, item in enumerate(scenes_payload, start=1):
        if not isinstance(item, dict):
            raise ValueError("Every scenes item must be a JSON object.")
        duration_seconds = _parse_positive_number(item.get("duration_seconds"), f"scenes[{index}].duration_seconds")
        description = str(item.get("description", "")).strip()
        if not description:
            raise ValueError(f"scenes[{index}] must include a non-empty description.")
        current_end = current_start + duration_seconds
        scenes.append(
            VideoSceneSpec(
                index=index,
                duration_seconds=duration_seconds,
                start_seconds=current_start,
                end_seconds=current_end,
                description=description,
            )
        )
        current_start = current_end

    if abs(current_start - total_duration_seconds) > 0.25:
        raise ValueError(
            "Sum of scene durations does not match total_duration_seconds: "
            f"{current_start:.2f}s vs {total_duration_seconds:.2f}s."
        )

    scenario_variants_payload = payload.get("scenario_variants")
    scenario_variants = _parse_scenario_variants(scenario_variants_payload)

    return VideoPromptRequest(
        technical_preamble=technical_preamble,
        total_duration_seconds=total_duration_seconds,
        max_prompt_chars=max_prompt_chars,
        aspect_ratio=aspect_ratio,
        regeneration_assets_dir=regeneration_assets_dir,
        references=references,
        scenes=scenes,
        scenario_variants=scenario_variants,
    )


def resolve_reference_contexts(request: VideoPromptRequest) -> list[ReferenceContext]:
    return [resolve_reference_context(request.regeneration_assets_dir, reference) for reference in request.references]


def resolve_reference_context(regeneration_assets_dir: Path, reference: VideoImageReference) -> ReferenceContext:
    source_stem = Path(reference.source_file).stem
    stage_dir = _find_latest_stage_dir(regeneration_assets_dir, source_stem)
    description_path = _first_matching_file(stage_dir, "*_description.txt")
    scene_analysis_en_path = stage_dir / f"{stage_dir.name}_scene_analysis.json"
    scene_analysis_ru_path = stage_dir / f"{stage_dir.name}_scene_analysis_ru.json"

    description_excerpt = _extract_description_excerpt(description_path)
    scene_analysis_default = _load_json_if_exists(scene_analysis_en_path)
    scene_analysis_ru = _load_json_if_exists(scene_analysis_ru_path)

    if scene_analysis_ru:
        scene_analysis_en = scene_analysis_default
    else:
        scene_analysis_en = {}
        scene_analysis_ru = scene_analysis_default

    return ReferenceContext(
        source_file=reference.source_file,
        source_stem=source_stem,
        tag=reference.tag,
        stage_dir=stage_dir,
        stage_id=stage_dir.name,
        description_excerpt=description_excerpt,
        scene_analysis_en=_compact_scene_analysis(scene_analysis_en),
        scene_analysis_ru=_compact_scene_analysis(scene_analysis_ru),
    )


def write_generated_prompt_files(
    output_dir: Path,
    bundle: GeneratedVideoPromptBundle,
    *,
    timestamp: datetime | None = None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = (timestamp or datetime.now(JERUSALEM_TZ)).strftime("%Y%m%d_%H%M%S")
    video_prompt_path = output_dir / f"Gen_Video_{stamp}.txt"
    video_prompt_ru_path = output_dir / f"Gen_Video_RU_{stamp}.txt"
    video_prompt_path.write_text(bundle.video_prompt_en.strip(), encoding="utf-8")
    video_prompt_ru_path.write_text(bundle.video_prompt_ru.strip(), encoding="utf-8")
    return video_prompt_path, video_prompt_ru_path


def write_generated_seedance_prompt_file(
    output_dir: Path,
    seedance_prompt_json_text: str,
    *,
    timestamp: datetime | None = None,
    prefix: str = "Gen_Video_Seedance",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = (timestamp or datetime.now(JERUSALEM_TZ)).strftime("%Y%m%d_%H%M%S")
    seedance_prompt_path = output_dir / f"{prefix}_{stamp}.json"
    payload = json.loads(seedance_prompt_json_text)
    seedance_prompt_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return seedance_prompt_path


def write_generated_seedance_prompt_files(
    output_dir: Path,
    bundle: GeneratedSeedanceJsonBundle,
    *,
    timestamp: datetime | None = None,
    variant_suffix: str | None = None,
) -> tuple[Path, Path]:
    normalized_variant_suffix = _normalize_variant_suffix(variant_suffix)
    en_prefix = "Gen_Video_Seedance"
    ru_prefix = "Gen_Video_Seedance_RU"
    if normalized_variant_suffix:
        en_prefix = f"{en_prefix}_{normalized_variant_suffix}"
        ru_prefix = f"{ru_prefix}_{normalized_variant_suffix}"
    seedance_prompt_path = write_generated_seedance_prompt_file(
        output_dir,
        bundle.seedance_prompt_json_en,
        timestamp=timestamp,
        prefix=en_prefix,
    )
    seedance_prompt_ru_path = write_generated_seedance_prompt_file(
        output_dir,
        bundle.seedance_prompt_json_ru,
        timestamp=timestamp,
        prefix=ru_prefix,
    )
    return seedance_prompt_path, seedance_prompt_ru_path


def used_image_tags(request: VideoPromptRequest) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for scene in request.scenes:
        for match in IMAGE_TAG_RE.findall(scene.description):
            if match not in seen:
                seen.add(match)
                found.append(match)
    return found


def scene_specs_to_payload(request: VideoPromptRequest) -> list[dict[str, Any]]:
    return [
        {
            "shot": scene.index,
            "start_seconds": _format_seconds(scene.start_seconds),
            "end_seconds": _format_seconds(scene.end_seconds),
            "duration_seconds": _format_seconds(scene.duration_seconds),
            "description": scene.description,
        }
        for scene in request.scenes
    ]


def reference_contexts_to_payload(reference_contexts: list[ReferenceContext]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for context in reference_contexts:
        payload.append(
            {
                "tag": context.tag,
                "source_file": context.source_file,
                "stage_id": context.stage_id,
                "description_excerpt": context.description_excerpt,
                "scene_analysis_en": context.scene_analysis_en,
                "scene_analysis_ru": context.scene_analysis_ru,
            }
        )
    return payload


def scenario_variant_to_payload(variant: ScenarioVariantSpec) -> dict[str, str]:
    return {
        "variant_id": variant.variant_id,
        "label": variant.label,
        "instruction": variant.instruction,
    }


def _parse_positive_number(value: object, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive number.") from exc
    if number <= 0:
        raise ValueError(f"{field_name} must be > 0.")
    return number


def _parse_positive_int(value: object, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer.") from exc
    if number <= 0:
        raise ValueError(f"{field_name} must be > 0.")
    return number


def _parse_scenario_variants(value: object) -> list[ScenarioVariantSpec]:
    if value is None:
        return [
            ScenarioVariantSpec(
                variant_id="Variant_1",
                label="Variant 1",
                instruction="Create the most likely, most suitable, and best-fitting cinematic interpretation.",
            )
        ]
    if not isinstance(value, list) or not value:
        raise ValueError("scenario_variants must be a non-empty list when provided.")
    variants: list[ScenarioVariantSpec] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError("Each scenario_variants item must be a JSON object.")
        variant_id = str(item.get("variant_id", "")).strip()
        label = str(item.get("label", "")).strip()
        instruction = str(item.get("instruction", "")).strip()
        if not variant_id or not SCENARIO_VARIANT_ID_RE.fullmatch(variant_id):
            raise ValueError(
                f"scenario_variants[{index}].variant_id must match {SCENARIO_VARIANT_ID_RE.pattern}."
            )
        if variant_id in seen_ids:
            raise ValueError(f"Duplicate scenario variant id found: {variant_id}")
        seen_ids.add(variant_id)
        if not label:
            raise ValueError(f"scenario_variants[{index}].label must be non-empty.")
        if not instruction:
            raise ValueError(f"scenario_variants[{index}].instruction must be non-empty.")
        variants.append(
            ScenarioVariantSpec(
                variant_id=variant_id,
                label=label,
                instruction=instruction,
            )
        )
    return variants


def _find_latest_stage_dir(regeneration_assets_dir: Path, source_stem: str) -> Path:
    pattern = re.compile(rf"^{re.escape(source_stem)}_(\d{{8}}_\d{{6}})$")
    candidates: list[tuple[datetime, Path]] = []
    searched_roots: list[Path] = []
    for root_dir in _candidate_regeneration_roots(str(regeneration_assets_dir)):
        searched_roots.append(root_dir)
        if not root_dir.exists():
            continue
        try:
            children = sorted(root_dir.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            match = pattern.match(child.name)
            if not match:
                continue
            stamp = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
            candidates.append((stamp, child))
    if not candidates:
        searched_roots_label = ", ".join(str(path) for path in searched_roots) or str(regeneration_assets_dir)
        raise FileNotFoundError(
            "Could not find a regeneration_assets stage directory for source file stem "
            f"'{source_stem}' in: {searched_roots_label}"
        )
    candidates.sort(key=lambda item: (item[0], item[1].stat().st_mtime), reverse=True)
    return candidates[0][1]


@lru_cache(maxsize=None)
def _candidate_regeneration_roots(regeneration_assets_dir: str) -> tuple[Path, ...]:
    base_dir = Path(regeneration_assets_dir)
    roots: list[Path] = [base_dir]
    parent_dir = base_dir.parent
    if not parent_dir.exists():
        return tuple(roots)

    family_prefix = _regeneration_assets_family_prefix(base_dir.name)
    try:
        sibling_dirs = sorted(
            (
                path
                for path in parent_dir.iterdir()
                if path.is_dir() and path != base_dir
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        return tuple(roots)

    for path in sibling_dirs:
        normalized_name = path.name.casefold()
        if _is_matching_regeneration_root(normalized_name, family_prefix):
            roots.append(path)
    return tuple(roots)


def _regeneration_assets_family_prefix(directory_name: str) -> str:
    normalized_name = directory_name.casefold()
    if normalized_name.startswith(REGENERATION_ASSETS_FAMILY_PREFIX):
        return REGENERATION_ASSETS_FAMILY_PREFIX
    return normalized_name


def _is_matching_regeneration_root(normalized_name: str, family_prefix: str) -> bool:
    if family_prefix == REGENERATION_ASSETS_FAMILY_PREFIX:
        return normalized_name.startswith(family_prefix)
    return (
        normalized_name == family_prefix
        or normalized_name.startswith(f"{family_prefix}_")
        or normalized_name.startswith(f"{family_prefix}-")
    )


def _first_matching_file(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern))
    if not matches:
        return None
    return matches[0]


def _extract_description_excerpt(description_path: Path | None) -> str:
    if description_path is None or not description_path.exists():
        return ""
    text = description_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    collected: list[str] = []
    for line in lines:
        if line.startswith("Cinematic motion logic:"):
            break
        collected.append(line)
    excerpt = re.sub(r"\s+", " ", " ".join(collected)).strip()
    return excerpt[:1200]


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _compact_scene_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    people = payload.get("people") if isinstance(payload.get("people"), list) else []
    compact_people = []
    for item in people[:4]:
        if not isinstance(item, dict):
            continue
        compact_people.append(
            {
                "label": str(item.get("label", "")).strip(),
                "role_in_scene": str(item.get("role_in_scene", "")).strip(),
                "clothing": str(item.get("clothing", "")).strip(),
                "pose": str(item.get("pose", "")).strip(),
            }
        )
    compact = {
        "summary": str(payload.get("summary", "")).strip(),
        "people_count": int(payload.get("people_count", len(compact_people) or 0)) if payload else 0,
        "people": compact_people,
        "background": str(payload.get("background", "")).strip(),
        "shot_type": str(payload.get("shot_type", "")).strip(),
        "main_action": str(payload.get("main_action", "")).strip(),
        "mood": [str(item).strip() for item in payload.get("mood", [])[:4]] if isinstance(payload.get("mood"), list) else [],
        "relationships": [str(item).strip() for item in payload.get("relationships", [])[:4]]
        if isinstance(payload.get("relationships"), list)
        else [],
    }
    return compact


def _format_seconds(value: float) -> str:
    rounded = int(value)
    if abs(value - rounded) < 1e-9:
        return str(rounded)
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _normalize_variant_suffix(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
