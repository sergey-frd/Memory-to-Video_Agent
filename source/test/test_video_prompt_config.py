from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from video_prompt_config import (
    VideoPromptConfigValidationError,
    load_video_prompt_composer_config,
)


def _make_temp_root(prefix: str) -> Path:
    root = Path("test_runtime") / f"{prefix}_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_load_video_prompt_composer_config_supports_jsonc_and_defaults() -> None:
    root = _make_temp_root("video_prompt_config_jsonc")
    regeneration_assets_dir = root / "regeneration_assets"
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)
    config_path = root / "video_prompt_config.jsonc"
    config_path.write_text(
        "{\n"
        '  // Scenario payload\n'
        '  "technical_preamble": "Warm family travel story.",\n'
        '  "total_duration_seconds": 6,\n'
        '  "regeneration_assets_dir": "'
        + str(regeneration_assets_dir).replace("\\", "\\\\")
        + '",\n'
        '  "references": [\n'
        '    {"source_file": "frame_a.jpg", "tag": "@image1"}\n'
        "  ],\n"
        '  "scenes": [\n'
        '    {"duration_seconds": 3, "description": "The couple @image1 leaves home."},\n'
        '    {"duration_seconds": 3, "description": "The couple @image1 returns home."}\n'
        "  ],\n"
        '  "seedance_json_only": true,\n'
        '  "output_dir": "'
        + str(root / "custom_output").replace("\\", "\\\\")
        + '"\n'
        "}\n",
        encoding="utf-8",
    )

    config = load_video_prompt_composer_config(config_path)

    assert config.seedance_json is True
    assert config.seedance_json_only is True
    assert config.output_dir == root / "custom_output"
    assert config.request.aspect_ratio == "16:9"
    assert config.request.max_prompt_chars == 2000
    assert [variant.variant_id for variant in config.request.scenario_variants] == ["Variant_1"]


def test_load_video_prompt_composer_config_rejects_duplicate_keys() -> None:
    root = _make_temp_root("video_prompt_config_duplicate")
    regeneration_assets_dir = root / "regeneration_assets"
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)
    config_path = root / "video_prompt_config.json"
    config_path.write_text(
        "{\n"
        '  "technical_preamble": "One",\n'
        '  "technical_preamble": "Two",\n'
        '  "total_duration_seconds": 3,\n'
        '  "regeneration_assets_dir": "'
        + str(regeneration_assets_dir).replace("\\", "\\\\")
        + '",\n'
        '  "references": [{"source_file": "frame_a.jpg", "tag": "@image1"}],\n'
        '  "scenes": [{"duration_seconds": 3, "description": "The man @image1 walks."}]\n'
        "}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        VideoPromptConfigValidationError,
        match="Duplicate config key\\(s\\) found: technical_preamble",
    ):
        load_video_prompt_composer_config(config_path)


def test_load_video_prompt_composer_config_rejects_unknown_keys() -> None:
    root = _make_temp_root("video_prompt_config_unknown")
    regeneration_assets_dir = root / "regeneration_assets"
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)
    config_path = root / "video_prompt_config.json"
    config_path.write_text(
        "{\n"
        '  "technical_preamble": "Family scene.",\n'
        '  "total_duration_seconds": 3,\n'
        '  "regeneration_assets_dir": "'
        + str(regeneration_assets_dir).replace("\\", "\\\\")
        + '",\n'
        '  "references": [{"source_file": "frame_a.jpg", "tag": "@image1"}],\n'
        '  "scenes": [{"duration_seconds": 3, "description": "The man @image1 walks."}],\n'
        '  "wrong_field": 123\n'
        "}\n",
        encoding="utf-8",
    )

    with pytest.raises(VideoPromptConfigValidationError, match="wrong_field"):
        load_video_prompt_composer_config(config_path)
