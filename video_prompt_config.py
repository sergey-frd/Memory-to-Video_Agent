from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.video_prompt_composer import VideoPromptRequest, load_video_prompt_request


VIDEO_PROMPT_REQUEST_FIELDS = {
    "technical_preamble",
    "total_duration_seconds",
    "max_prompt_chars",
    "aspect_ratio",
    "regeneration_assets_dir",
    "references",
    "scenes",
    "scenario_variants",
}
VIDEO_PROMPT_CONFIG_BOOL_FIELDS = {
    "seedance_json",
    "seedance_json_only",
}
VIDEO_PROMPT_CONFIG_OPTIONAL_STR_FIELDS = {
    "model",
    "output_dir",
    "seedance_director_file",
}
VIDEO_PROMPT_CONFIG_KNOWN_FIELDS = (
    VIDEO_PROMPT_REQUEST_FIELDS
    | VIDEO_PROMPT_CONFIG_BOOL_FIELDS
    | VIDEO_PROMPT_CONFIG_OPTIONAL_STR_FIELDS
)


class VideoPromptConfigValidationError(ValueError):
    pass


def _config_object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    config_data: dict[str, Any] = {}
    duplicate_keys: list[str] = []
    for key, value in pairs:
        if key in config_data:
            duplicate_keys.append(key)
        config_data[key] = value
    if duplicate_keys:
        duplicates = ", ".join(sorted(set(duplicate_keys)))
        raise VideoPromptConfigValidationError(f"Duplicate config key(s) found: {duplicates}")
    return config_data


def _strip_json_comments(text: str) -> str:
    output: list[str] = []
    in_string = False
    in_line_comment = False
    in_block_comment = False
    escape = False
    index = 0

    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                output.append(char)
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
                continue
            if char in "\r\n":
                output.append(char)
            index += 1
            continue

        if in_string:
            output.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue

        output.append(char)
        if char == '"':
            in_string = True
        index += 1

    return "".join(output)


def _validate_config_data(data: dict[str, Any], path: Path | None) -> None:
    location = str(path) if path is not None else "video_prompt_config"
    unknown_fields = sorted(set(data) - VIDEO_PROMPT_CONFIG_KNOWN_FIELDS)
    if unknown_fields:
        raise VideoPromptConfigValidationError(
            f"Unknown config key(s) in {location}: {', '.join(unknown_fields)}"
        )

    for field_name in VIDEO_PROMPT_CONFIG_BOOL_FIELDS:
        if field_name in data and not isinstance(data[field_name], bool):
            raise VideoPromptConfigValidationError(
                f"Config key '{field_name}' in {location} must be true or false."
            )

    for field_name in VIDEO_PROMPT_CONFIG_OPTIONAL_STR_FIELDS:
        if field_name not in data or data[field_name] is None:
            continue
        value = data[field_name]
        if not isinstance(value, str) or not value.strip():
            raise VideoPromptConfigValidationError(
                f"Config key '{field_name}' in {location} must be null or a non-empty string."
            )


@dataclass(frozen=True)
class VideoPromptComposerConfig:
    request: VideoPromptRequest
    model: str | None = None
    output_dir: Path | None = None
    seedance_json: bool = False
    seedance_json_only: bool = False
    seedance_director_file: Path = field(
        default_factory=lambda: Path("services") / "Seedance_2.0_Director.md"
    )

    def __post_init__(self) -> None:
        if self.seedance_json_only and not self.seedance_json:
            object.__setattr__(self, "seedance_json", True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VideoPromptComposerConfig":
        request_payload = {
            key: value
            for key, value in data.items()
            if key in VIDEO_PROMPT_REQUEST_FIELDS
        }
        request = load_video_prompt_request(json.dumps(request_payload, ensure_ascii=False))

        output_dir_raw = data.get("output_dir")
        output_dir = Path(str(output_dir_raw)) if output_dir_raw is not None else None

        seedance_director_file_raw = data.get("seedance_director_file")
        seedance_director_file = (
            Path(str(seedance_director_file_raw))
            if seedance_director_file_raw is not None
            else Path("services") / "Seedance_2.0_Director.md"
        )

        model_raw = data.get("model")
        model = str(model_raw).strip() if model_raw is not None else None
        if model == "":
            model = None

        return cls(
            request=request,
            model=model,
            output_dir=output_dir,
            seedance_json=data.get("seedance_json", False),
            seedance_json_only=data.get("seedance_json_only", False),
            seedance_director_file=seedance_director_file,
        )

    def override(
        self,
        *,
        model: str | None = None,
        output_dir: Path | None = None,
        seedance_json: bool | None = None,
        seedance_json_only: bool | None = None,
        seedance_director_file: Path | None = None,
    ) -> "VideoPromptComposerConfig":
        return VideoPromptComposerConfig(
            request=self.request,
            model=self.model if model is None else model,
            output_dir=self.output_dir if output_dir is None else output_dir,
            seedance_json=self.seedance_json if seedance_json is None else seedance_json,
            seedance_json_only=(
                self.seedance_json_only if seedance_json_only is None else seedance_json_only
            ),
            seedance_director_file=(
                self.seedance_director_file
                if seedance_director_file is None
                else seedance_director_file
            ),
        )


def load_video_prompt_composer_config(path: Path) -> VideoPromptComposerConfig:
    with open(path, "r", encoding="utf-8-sig") as handle:
        raw_text = handle.read()
    stripped_text = _strip_json_comments(raw_text)
    try:
        config_data = json.loads(stripped_text, object_pairs_hook=_config_object_pairs_hook)
    except json.JSONDecodeError as exc:
        raise VideoPromptConfigValidationError(
            f"Video prompt config is not valid JSON/JSONC in {path}: {exc}"
        ) from exc
    if not isinstance(config_data, dict):
        raise VideoPromptConfigValidationError(
            f"Video prompt config in {path} must be a JSON object."
        )
    _validate_config_data(config_data, path)
    try:
        return VideoPromptComposerConfig.from_dict(config_data)
    except (ValueError, FileNotFoundError) as exc:
        raise VideoPromptConfigValidationError(str(exc)) from exc
