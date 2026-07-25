from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import load_generation_config
from video_prompt_config import (
    VideoPromptConfigValidationError,
    _config_object_pairs_hook,
    _strip_json_comments,
)


VIDEO_PROMPT_STORY_BOOL_FIELDS = {
    "seedance_json",
    "seedance_json_only",
    "prefer_loving_kindness_tone",
}
VIDEO_PROMPT_STORY_INT_FIELDS = {
    "image_count",
    "scene_count",
    "scene_duration_seconds",
    "total_duration_seconds",
    "max_prompt_chars",
}
VIDEO_PROMPT_STORY_OPTIONAL_STR_FIELDS = {
    "generation_config_file",
    "regeneration_assets_dir",
    "restored_images_dir",
    "final_output_dir",
    "story_brief",
    "story_title",
    "story_output_stem",
    "model",
    "output_dir",
    "seedance_director_file",
    "aspect_ratio",
}
VIDEO_PROMPT_STORY_LIST_FIELDS = {
    "source_files",
    "scenario_variants",
}
VIDEO_PROMPT_STORY_KNOWN_FIELDS = (
    VIDEO_PROMPT_STORY_BOOL_FIELDS
    | VIDEO_PROMPT_STORY_INT_FIELDS
    | VIDEO_PROMPT_STORY_OPTIONAL_STR_FIELDS
    | VIDEO_PROMPT_STORY_LIST_FIELDS
)


@dataclass(frozen=True)
class VideoPromptStoryConfig:
    regeneration_assets_dir: Path
    restored_images_dir: Path
    image_count: int = 7
    scene_count: int = 5
    scene_duration_seconds: int = 2
    total_duration_seconds: int = 10
    max_prompt_chars: int = 2000
    aspect_ratio: str = "16:9"
    story_brief: str = ""
    story_title: str = "Multi-Scene Video Story"
    story_output_stem: str = "video_prompt_story"
    source_files: tuple[str, ...] = ()
    scenario_variants: tuple[dict[str, str], ...] = ()
    model: str | None = None
    output_dir: Path | None = None
    seedance_json: bool = True
    seedance_json_only: bool = True
    seedance_director_file: Path = field(
        default_factory=lambda: Path("docs") / "Seedance_2.0_Director.md"
    )
    prefer_loving_kindness_tone: bool = True

    def __post_init__(self) -> None:
        if self.image_count <= 0:
            raise ValueError("image_count must be > 0.")
        if self.scene_count <= 0:
            raise ValueError("scene_count must be > 0.")
        if self.scene_duration_seconds <= 0:
            raise ValueError("scene_duration_seconds must be > 0.")
        if self.total_duration_seconds <= 0:
            raise ValueError("total_duration_seconds must be > 0.")
        expected_total = self.scene_count * self.scene_duration_seconds
        if self.total_duration_seconds != expected_total:
            raise ValueError(
                "total_duration_seconds must equal scene_count * scene_duration_seconds: "
                f"{self.total_duration_seconds} != {expected_total}."
            )
        if not self.regeneration_assets_dir.exists():
            raise FileNotFoundError(
                f"regeneration_assets directory not found: {self.regeneration_assets_dir}"
            )
        if not self.restored_images_dir.exists():
            raise FileNotFoundError(
                f"restored_images directory not found: {self.restored_images_dir}"
            )
        if self.seedance_json_only and not self.seedance_json:
            object.__setattr__(self, "seedance_json", True)

    @property
    def effective_output_dir(self) -> Path:
        return self.output_dir or self.regeneration_assets_dir

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, base_dir: Path | None = None) -> "VideoPromptStoryConfig":
        merged = dict(data)
        generation_config_file = merged.get("generation_config_file")
        if generation_config_file:
            generation_path = Path(str(generation_config_file))
            if not generation_path.is_absolute() and base_dir is not None:
                generation_path = base_dir / generation_path
            generation_config = load_generation_config(generation_path)
            merged.setdefault(
                "regeneration_assets_dir",
                generation_config.regeneration_assets_dir,
            )
            merged.setdefault(
                "final_output_dir",
                generation_config.final_output_dir,
            )
            merged.setdefault(
                "max_prompt_chars",
                generation_config.grok_multiscene_prompt_size,
            )
            merged.setdefault(
                "prefer_loving_kindness_tone",
                generation_config.prefer_loving_kindness_tone,
            )

        regeneration_assets_dir_raw = merged.get("regeneration_assets_dir")
        if not regeneration_assets_dir_raw:
            raise ValueError("Video prompt story config must include regeneration_assets_dir.")
        regeneration_assets_dir = Path(str(regeneration_assets_dir_raw))

        restored_images_dir_raw = merged.get("restored_images_dir")
        final_output_dir_raw = merged.get("final_output_dir")
        if restored_images_dir_raw:
            restored_images_dir = Path(str(restored_images_dir_raw))
        elif final_output_dir_raw:
            restored_images_dir = Path(str(final_output_dir_raw)) / "chatgpt_photo_restoration"
        else:
            restored_images_dir = regeneration_assets_dir.parent / "output" / "chatgpt_photo_restoration"

        source_files_raw = merged.get("source_files")
        source_files: tuple[str, ...] = ()
        if source_files_raw is not None:
            if not isinstance(source_files_raw, list):
                raise ValueError("source_files must be a list of file names when provided.")
            source_files = tuple(str(item).strip() for item in source_files_raw if str(item).strip())

        scenario_variants_raw = merged.get("scenario_variants")
        scenario_variants: tuple[dict[str, str], ...] = ()
        if scenario_variants_raw is not None:
            if not isinstance(scenario_variants_raw, list) or not scenario_variants_raw:
                raise ValueError("scenario_variants must be a non-empty list when provided.")
            scenario_variants = tuple(
                {
                    "variant_id": str(item["variant_id"]).strip(),
                    "label": str(item["label"]).strip(),
                    "instruction": str(item["instruction"]).strip(),
                }
                for item in scenario_variants_raw
            )

        output_dir_raw = merged.get("output_dir")
        output_dir = Path(str(output_dir_raw)) if output_dir_raw is not None else None

        seedance_director_file_raw = merged.get("seedance_director_file")
        seedance_director_file = (
            Path(str(seedance_director_file_raw))
            if seedance_director_file_raw is not None
            else Path("docs") / "Seedance_2.0_Director.md"
        )

        model_raw = merged.get("model")
        model = str(model_raw).strip() if model_raw is not None else None
        if model == "":
            model = None

        return cls(
            regeneration_assets_dir=regeneration_assets_dir,
            restored_images_dir=restored_images_dir,
            image_count=int(merged.get("image_count", 7)),
            scene_count=int(merged.get("scene_count", 5)),
            scene_duration_seconds=int(merged.get("scene_duration_seconds", 2)),
            total_duration_seconds=int(merged.get("total_duration_seconds", 10)),
            max_prompt_chars=int(merged.get("max_prompt_chars", 2000)),
            aspect_ratio=str(merged.get("aspect_ratio", "16:9")).strip() or "16:9",
            story_brief=str(merged.get("story_brief", "")).strip(),
            story_title=str(merged.get("story_title", "Multi-Scene Video Story")).strip()
            or "Multi-Scene Video Story",
            story_output_stem=str(merged.get("story_output_stem", "video_prompt_story")).strip()
            or "video_prompt_story",
            source_files=source_files,
            scenario_variants=scenario_variants,
            model=model,
            output_dir=output_dir,
            seedance_json=bool(merged.get("seedance_json", True)),
            seedance_json_only=bool(merged.get("seedance_json_only", True)),
            seedance_director_file=seedance_director_file,
            prefer_loving_kindness_tone=bool(merged.get("prefer_loving_kindness_tone", True)),
        )

    def override(
        self,
        *,
        model: str | None = None,
        output_dir: Path | None = None,
    ) -> "VideoPromptStoryConfig":
        return VideoPromptStoryConfig(
            regeneration_assets_dir=self.regeneration_assets_dir,
            restored_images_dir=self.restored_images_dir,
            image_count=self.image_count,
            scene_count=self.scene_count,
            scene_duration_seconds=self.scene_duration_seconds,
            total_duration_seconds=self.total_duration_seconds,
            max_prompt_chars=self.max_prompt_chars,
            aspect_ratio=self.aspect_ratio,
            story_brief=self.story_brief,
            story_title=self.story_title,
            story_output_stem=self.story_output_stem,
            source_files=self.source_files,
            scenario_variants=self.scenario_variants,
            model=self.model if model is None else model,
            output_dir=self.output_dir if output_dir is None else output_dir,
            seedance_json=self.seedance_json,
            seedance_json_only=self.seedance_json_only,
            seedance_director_file=self.seedance_director_file,
            prefer_loving_kindness_tone=self.prefer_loving_kindness_tone,
        )


def _validate_story_config_data(data: dict[str, Any], path: Path | None) -> None:
    location = str(path) if path is not None else "video_prompt_story_config"
    unknown_fields = sorted(set(data) - VIDEO_PROMPT_STORY_KNOWN_FIELDS)
    if unknown_fields:
        raise VideoPromptConfigValidationError(
            f"Unknown config key(s) in {location}: {', '.join(unknown_fields)}"
        )

    for field_name in VIDEO_PROMPT_STORY_BOOL_FIELDS:
        if field_name in data and not isinstance(data[field_name], bool):
            raise VideoPromptConfigValidationError(
                f"Config key '{field_name}' in {location} must be true or false."
            )

    for field_name in VIDEO_PROMPT_STORY_INT_FIELDS:
        if field_name not in data:
            continue
        value = data[field_name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise VideoPromptConfigValidationError(
                f"Config key '{field_name}' in {location} must be an integer."
            )

    for field_name in VIDEO_PROMPT_STORY_OPTIONAL_STR_FIELDS:
        if field_name not in data or data[field_name] is None:
            continue
        value = data[field_name]
        if not isinstance(value, str) or not value.strip():
            raise VideoPromptConfigValidationError(
                f"Config key '{field_name}' in {location} must be null or a non-empty string."
            )

    for field_name in VIDEO_PROMPT_STORY_LIST_FIELDS:
        if field_name not in data or data[field_name] is None:
            continue
        if not isinstance(data[field_name], list):
            raise VideoPromptConfigValidationError(
                f"Config key '{field_name}' in {location} must be a list."
            )


def load_video_prompt_story_config(path: Path) -> VideoPromptStoryConfig:
    with open(path, "r", encoding="utf-8-sig") as handle:
        raw_text = handle.read()
    stripped_text = _strip_json_comments(raw_text)
    try:
        config_data = json.loads(stripped_text, object_pairs_hook=_config_object_pairs_hook)
    except json.JSONDecodeError as exc:
        raise VideoPromptConfigValidationError(
            f"Video prompt story config is not valid JSON/JSONC in {path}: {exc}"
        ) from exc
    if not isinstance(config_data, dict):
        raise VideoPromptConfigValidationError(
            f"Video prompt story config in {path} must be a JSON object."
        )
    _validate_story_config_data(config_data, path)
    try:
        return VideoPromptStoryConfig.from_dict(config_data, base_dir=path.parent)
    except (ValueError, FileNotFoundError) as exc:
        raise VideoPromptConfigValidationError(str(exc)) from exc
