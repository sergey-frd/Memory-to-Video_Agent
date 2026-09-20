import json
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from config import ConfigValidationError, GenerationConfig, load_generation_config


def test_production_paths_load_and_survive_override():
    paths = {
        "premiere_project_dir": r"<LOCAL_PATH>",
        "project_video_exports_dir": r"<LOCAL_PATH>",
        "source_materials_dir": r"<LOCAL_PATH>",
    }
    with patch.object(Path, "exists", return_value=True), patch(
        "builtins.open", mock_open(read_data=json.dumps(paths))
    ):
        config = load_generation_config(Path("config.json")).override(generate_music=True)
    for key, value in paths.items():
        assert getattr(config, key) == value
    assert config.final_output_dir == GenerationConfig().final_output_dir
    assert config.final_videos_dir == GenerationConfig().final_videos_dir


@pytest.mark.parametrize("key", ["premiere_project_dir", "source_materials_dir", "project_video_exports_dir"])
def test_production_paths_optional_but_validate_when_present(key):
    assert getattr(GenerationConfig.from_dict({}), key) is None
    with patch.object(Path, "exists", return_value=True), patch(
        "builtins.open", mock_open(read_data=json.dumps({key: 123}))
    ), pytest.raises(ConfigValidationError):
        load_generation_config(Path("config.json"))


@pytest.mark.parametrize("key", ["family_detail_txt", "family_screenshot"])
def test_family_context_optional_and_roundtrip(key):
    assert getattr(GenerationConfig.from_dict({}), key) is None
    value = "<LOCAL_PATH>" if key == "family_screenshot" else "<LOCAL_PATH>"
    with patch.object(Path, "exists", return_value=True), patch(
        "builtins.open", mock_open(read_data=json.dumps({key: value}))
    ):
        config = load_generation_config(Path("config.json"))
    assert getattr(config.override(generate_music=True), key) == value


@pytest.mark.parametrize("key", ["family_detail_txt", "family_screenshot"])
@pytest.mark.parametrize("value", [None, 123, "", "   ", [], {}])
def test_family_context_rejects_invalid_values(key, value):
    with patch.object(Path, "exists", return_value=True), patch(
        "builtins.open", mock_open(read_data=json.dumps({key: value}))
    ), pytest.raises(ConfigValidationError):
        load_generation_config(Path("config.json"))
