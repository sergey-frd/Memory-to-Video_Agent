from pathlib import Path
from uuid import uuid4

from config import GenerationConfig, Settings
from utils.project_delivery import resolve_delivery_dir, sync_stage_non_video_assets, sync_video_file


def _settings_for(root: Path) -> Settings:
    settings = Settings(project_root=root)
    settings.input_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    return settings


def test_sync_stage_non_video_assets_copies_input_and_stage_outputs() -> None:
    root = Path("test_runtime") / f"delivery_{uuid4().hex}"
    settings = _settings_for(root)
    config = GenerationConfig(
        final_videos_dir="final/videos",
        regeneration_assets_dir="final/assets",
    )

    image_path = settings.input_dir / "frame_a.png"
    image_path.write_bytes(b"image")
    description_path = settings.output_dir / "frame_a_20260311_120000_description.txt"
    description_path.write_text("description", encoding="utf-8")
    prompt_path = settings.output_dir / "frame_a_20260311_120000_v_prompt_1.txt"
    prompt_path.write_text("prompt", encoding="utf-8")
    video_path = settings.output_dir / "frame_a_20260311_120000_video_1.mp4"
    video_path.write_bytes(b"video")
    background_image_path = settings.output_dir / "frame_a_20260311_120000_bg_image_16x9.png"
    background_image_path.write_bytes(b"background")

    copied = sync_stage_non_video_assets(settings, config, "frame_a_20260311_120000")

    target_dir = resolve_delivery_dir(settings, config.regeneration_assets_dir) / "frame_a_20260311_120000"
    assert target_dir.exists()
    assert not (target_dir / image_path.name).exists()
    assert (target_dir / description_path.name).exists()
    assert (target_dir / prompt_path.name).exists()
    assert not (target_dir / video_path.name).exists()
    assert not (target_dir / background_image_path.name).exists()
    assert copied


def test_sync_video_file_copies_mp4_into_final_video_dir() -> None:
    root = Path("test_runtime") / f"delivery_{uuid4().hex}"
    settings = _settings_for(root)
    config = GenerationConfig(
        final_videos_dir="final/videos",
        regeneration_assets_dir="final/assets",
    )

    video_path = settings.output_dir / "frame_a_20260311_120000_video_1.mp4"
    video_path.write_bytes(b"video")

    synced = sync_video_file(settings, config, video_path)

    assert synced == resolve_delivery_dir(settings, config.final_videos_dir) / video_path.name
    assert synced.exists()
