from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()


class MotionSource(str, Enum):
    TABLE = "table"
    AI = "ai"


class VideoFramingMode(str, Enum):
    IDENTITY_SAFE = "identity_safe"
    FACE_CLOSEUP = "face_closeup"
    AI_OPTIMAL = "ai_optimal"


class ConfigValidationError(ValueError):
    pass


CONFIG_BOOL_FIELDS = {
    "generate_video",
    "generate_source_background",
    "save_grok_debug_artifacts",
    "continue_after_failure",
    "write_description",
    "generate_final_frames",
    "read_input_list",
    "generate_music",
    "prefer_face_closeups",
    "use_ai_optimal_framing",
    "generate_dual_framing_videos",
    "hide_phone_in_selfie",
    "prefer_loving_kindness_tone",
}
CONFIG_INT_FIELDS = {
    "video_count",
    "camera_segments",
}
CONFIG_STR_FIELDS = {
    "motion_model",
    "final_videos_dir",
    "regeneration_assets_dir",
}
CONFIG_ENUM_FIELDS = {
    "motion_source",
}
CONFIG_KNOWN_FIELDS = CONFIG_BOOL_FIELDS | CONFIG_INT_FIELDS | CONFIG_STR_FIELDS | CONFIG_ENUM_FIELDS


def _config_object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    config_data: dict[str, Any] = {}
    duplicate_keys: list[str] = []
    for key, value in pairs:
        if key in config_data:
            duplicate_keys.append(key)
        config_data[key] = value
    if duplicate_keys:
        duplicates = ", ".join(sorted(set(duplicate_keys)))
        raise ConfigValidationError(f"Duplicate config key(s) found: {duplicates}")
    return config_data


def _validate_config_data(data: Dict[str, Any], path: Path | None) -> None:
    location = str(path) if path is not None else "config"
    unknown_fields = sorted(set(data) - CONFIG_KNOWN_FIELDS)
    if unknown_fields:
        raise ConfigValidationError(
            f"Unknown config key(s) in {location}: {', '.join(unknown_fields)}"
        )

    for field_name in CONFIG_BOOL_FIELDS:
        if field_name in data and not isinstance(data[field_name], bool):
            raise ConfigValidationError(
                f"Config key '{field_name}' in {location} must be true or false."
            )

    for field_name in CONFIG_INT_FIELDS:
        if field_name not in data:
            continue
        value = data[field_name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigValidationError(
                f"Config key '{field_name}' in {location} must be an integer."
            )
        if value < 1:
            raise ConfigValidationError(
                f"Config key '{field_name}' in {location} must be >= 1."
            )

    for field_name in CONFIG_STR_FIELDS:
        if field_name not in data:
            continue
        value = data[field_name]
        if not isinstance(value, str) or not value.strip():
            raise ConfigValidationError(
                f"Config key '{field_name}' in {location} must be a non-empty string."
            )

    if "motion_source" in data:
        try:
            MotionSource(str(data["motion_source"]))
        except ValueError as exc:
            allowed = ", ".join(member.value for member in MotionSource)
            raise ConfigValidationError(
                f"Config key 'motion_source' in {location} must be one of: {allowed}."
            ) from exc

    framing_flags = [
        field_name
        for field_name in (
            "prefer_face_closeups",
            "use_ai_optimal_framing",
            "generate_dual_framing_videos",
        )
        if data.get(field_name)
    ]
    if len(framing_flags) > 1:
        raise ConfigValidationError(
            "Only one framing-mode config key can be enabled at a time: "
            "prefer_face_closeups, use_ai_optimal_framing, generate_dual_framing_videos."
        )


@dataclass
class GenerationConfig:
    generate_video: bool = True
    video_count: int = 2
    camera_segments: int = 1
    motion_source: MotionSource = field(default_factory=lambda: MotionSource.TABLE)
    motion_model: str = "gpt-4.1"
    generate_source_background: bool = False
    save_grok_debug_artifacts: bool = False
    final_videos_dir: str = "final_project/videos"
    regeneration_assets_dir: str = "final_project/regeneration_assets"
    continue_after_failure: bool = False
    write_description: bool = True
    generate_final_frames: bool = False
    read_input_list: bool = True
    generate_music: bool = False
    prefer_face_closeups: bool = False
    use_ai_optimal_framing: bool = False
    generate_dual_framing_videos: bool = False
    hide_phone_in_selfie: bool = True
    prefer_loving_kindness_tone: bool = False

    def __post_init__(self) -> None:
        enabled = [
            flag
            for flag, active in (
                ("prefer_face_closeups", self.prefer_face_closeups),
                ("use_ai_optimal_framing", self.use_ai_optimal_framing),
                ("generate_dual_framing_videos", self.generate_dual_framing_videos),
            )
            if active
        ]
        if len(enabled) > 1:
            raise ConfigValidationError(
                "Only one framing-mode config key can be enabled at a time: "
                "prefer_face_closeups, use_ai_optimal_framing, generate_dual_framing_videos."
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GenerationConfig":
        default = cls()
        motion_value = data.get("motion_source", default.motion_source.value)
        motion_source = MotionSource(motion_value)
        return cls(
            generate_video=data.get("generate_video", default.generate_video),
            video_count=data.get("video_count", default.video_count),
            camera_segments=data.get("camera_segments", default.camera_segments),
            motion_source=motion_source,
            motion_model=str(data.get("motion_model", default.motion_model)),
            generate_source_background=data.get("generate_source_background", default.generate_source_background),
            save_grok_debug_artifacts=data.get("save_grok_debug_artifacts", default.save_grok_debug_artifacts),
            final_videos_dir=str(data.get("final_videos_dir", default.final_videos_dir)),
            regeneration_assets_dir=str(data.get("regeneration_assets_dir", default.regeneration_assets_dir)),
            continue_after_failure=data.get("continue_after_failure", default.continue_after_failure),
            write_description=data.get("write_description", default.write_description),
            generate_final_frames=data.get("generate_final_frames", default.generate_final_frames),
            read_input_list=data.get("read_input_list", default.read_input_list),
            generate_music=data.get("generate_music", default.generate_music),
            prefer_face_closeups=data.get("prefer_face_closeups", default.prefer_face_closeups),
            use_ai_optimal_framing=data.get("use_ai_optimal_framing", default.use_ai_optimal_framing),
            generate_dual_framing_videos=data.get(
                "generate_dual_framing_videos",
                default.generate_dual_framing_videos,
            ),
            hide_phone_in_selfie=data.get("hide_phone_in_selfie", default.hide_phone_in_selfie),
            prefer_loving_kindness_tone=data.get(
                "prefer_loving_kindness_tone",
                default.prefer_loving_kindness_tone,
            ),
        )

    def override(self, **kwargs: Any) -> "GenerationConfig":
        values = {
            "generate_video": self.generate_video,
            "video_count": self.video_count,
            "camera_segments": self.camera_segments,
            "motion_source": self.motion_source,
            "motion_model": self.motion_model,
            "generate_source_background": self.generate_source_background,
            "save_grok_debug_artifacts": self.save_grok_debug_artifacts,
            "final_videos_dir": self.final_videos_dir,
            "regeneration_assets_dir": self.regeneration_assets_dir,
            "continue_after_failure": self.continue_after_failure,
            "write_description": self.write_description,
            "generate_final_frames": self.generate_final_frames,
            "read_input_list": self.read_input_list,
            "generate_music": self.generate_music,
            "prefer_face_closeups": self.prefer_face_closeups,
            "use_ai_optimal_framing": self.use_ai_optimal_framing,
            "generate_dual_framing_videos": self.generate_dual_framing_videos,
            "hide_phone_in_selfie": self.hide_phone_in_selfie,
            "prefer_loving_kindness_tone": self.prefer_loving_kindness_tone,
        }
        values.update({k: v for k, v in kwargs.items() if v is not None})
        if isinstance(values["motion_source"], str):
            values["motion_source"] = MotionSource(values["motion_source"])
        return GenerationConfig(**values)

    def framing_modes(self) -> list[VideoFramingMode]:
        if self.generate_dual_framing_videos:
            return [VideoFramingMode.IDENTITY_SAFE, VideoFramingMode.AI_OPTIMAL]
        if self.prefer_face_closeups:
            return [VideoFramingMode.FACE_CLOSEUP]
        if self.use_ai_optimal_framing:
            return [VideoFramingMode.AI_OPTIMAL]
        return [VideoFramingMode.IDENTITY_SAFE]

    def primary_framing_mode(self) -> VideoFramingMode:
        return self.framing_modes()[0]

    def total_video_outputs(self) -> int:
        return self.video_count * len(self.framing_modes())


def load_generation_config(path: Path | None) -> GenerationConfig:
    config_data: Dict[str, Any] = {}
    if path and path.exists():
        with open(path, "r", encoding="utf-8-sig") as handle:
            config_data = json.load(handle, object_pairs_hook=_config_object_pairs_hook)
        _validate_config_data(config_data, path)
    return GenerationConfig.from_dict(config_data)


@dataclass
class Settings:
    """Project-wide settings and canonical paths."""

    project_root: Optional[Path] = None
    input_dir: Path = field(init=False)
    output_dir: Path = field(init=False)
    services_dir: Path = field(init=False)
    styles_file: Path = field(init=False)
    prompt_recipe_file: Path = field(init=False)

    def __post_init__(self) -> None:
        root = self.project_root or Path(__file__).resolve().parent
        self.project_root = root
        self.input_dir = root / "input"
        self.output_dir = root / "output"
        self.services_dir = root / "services"
        self.styles_file = root / "styles" / "styles.txt"
        self.prompt_recipe_file = self.services_dir / "PROMPT CREATION_260303_1.txt"

    def ensure_output(self) -> None:
        """Make sure the output directory exists."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
