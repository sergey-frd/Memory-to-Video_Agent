from datetime import datetime
from argparse import Namespace
from pathlib import Path
from uuid import uuid4

import main
import main_desktop_pipeline
import pytest

from config import ConfigValidationError, GenerationConfig, Settings, VideoFramingMode, load_generation_config
from main import _resolve_input_images, _stage_identifier


def test_generation_config_defaults_match_requested_values() -> None:
    config = GenerationConfig.from_dict({})
    assert config.generate_final_frames is False
    assert config.read_input_list is True
    assert config.generate_music is False
    assert config.motion_model == "gpt-4.1"
    assert config.generate_source_background is False
    assert config.save_grok_debug_artifacts is False
    assert config.final_videos_dir == "final_project/videos"
    assert config.regeneration_assets_dir == "final_project/regeneration_assets"
    assert config.continue_after_failure is False
    assert config.prefer_face_closeups is False
    assert config.use_ai_optimal_framing is False
    assert config.generate_dual_framing_videos is False
    assert config.framing_modes() == [VideoFramingMode.IDENTITY_SAFE]


def test_resolve_input_images_reads_input_directory_by_default() -> None:
    root = Path("test_runtime") / f"input_list_{uuid4().hex}"
    input_dir = root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    first = input_dir / "a.png"
    second = input_dir / "b.jpg"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    settings = Settings(project_root=root)
    args = Namespace(image=None)
    config = GenerationConfig(read_input_list=True)

    images = _resolve_input_images(args, settings, config)

    assert images == [first, second]


def test_stage_identifier_uses_input_filename_prefix() -> None:
    stage_id = _stage_identifier(None, Path("frame_a.png"))
    assert stage_id.startswith("frame_a_")


def test_stage_identifier_uses_jerusalem_time(monkeypatch) -> None:
    fixed = datetime(2026, 3, 11, 14, 30, 45)
    monkeypatch.setattr(main, "_now_in_jerusalem", lambda: fixed)
    monkeypatch.setattr(main_desktop_pipeline, "_now_in_jerusalem", lambda: fixed)

    assert _stage_identifier(None, Path("frame_a.png")) == "frame_a_20260311_143045"
    assert main_desktop_pipeline.stage_identifier(None, Path("frame_b.png")) == "frame_b_20260311_143045"


def test_load_generation_config_rejects_duplicate_keys() -> None:
    root = Path("test_runtime") / f"duplicate_config_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    config_path.write_text(
        '{\n'
        '  "final_videos_dir": "videos_a",\n'
        '  "regeneration_assets_dir": "regen",\n'
        '  "final_videos_dir": "videos_b"\n'
        '}',
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError, match="Duplicate config key\\(s\\) found: final_videos_dir"):
        load_generation_config(config_path)


def test_load_generation_config_rejects_unknown_keys() -> None:
    root = Path("test_runtime") / f"unknown_config_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    config_path.write_text(
        '{\n'
        '  "generate_video": true,\n'
        '  "wrong_field": 123\n'
        '}',
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError, match="Unknown config key\\(s\\).*wrong_field"):
        load_generation_config(config_path)


def test_load_generation_config_rejects_conflicting_framing_flags() -> None:
    root = Path("test_runtime") / f"framing_conflict_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    config_path.write_text(
        '{\n'
        '  "prefer_face_closeups": true,\n'
        '  "use_ai_optimal_framing": true\n'
        '}',
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError, match="Only one framing-mode config key can be enabled"):
        load_generation_config(config_path)


def test_generation_config_dual_mode_doubles_total_outputs() -> None:
    config = GenerationConfig(video_count=1, generate_dual_framing_videos=True)

    assert config.framing_modes() == [VideoFramingMode.IDENTITY_SAFE, VideoFramingMode.AI_OPTIMAL]
    assert config.total_video_outputs() == 2
