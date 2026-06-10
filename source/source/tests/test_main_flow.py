from argparse import Namespace
from pathlib import Path

from PIL import Image

from config import GenerationConfig, MotionSource, Settings
from main import _run_generation


def _stub_image_editor(
    image_path: Path,
    style: str,
    output_path: Path,
    metadata,
    stage_id: str,
    prompt_override=None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    width = getattr(metadata, "width", 64)
    height = getattr(metadata, "height", 64)
    Image.new("RGBA", (width or 1, height or 1), (0, 0, 0, 0)).save(output_path)
    return output_path


def test_run_generation_writes_prompts(tmp_path: Path) -> None:
    dummy_image = tmp_path / "frame.png"
    Image.new("RGB", (128, 96)).save(dummy_image)

    args = Namespace(image=dummy_image, stage_id="teststage")
    settings = Settings()
    settings.output_dir = tmp_path / "output"
    generation_config = GenerationConfig(
        video_count=1,
        camera_segments=1,
        motion_source=MotionSource.TABLE,
    )

    _run_generation(
        args,
        generation_config,
        settings=settings,
        image_editor=_stub_image_editor,
        generate_video=False,
        generate_styled_images=False,
        generate_final_frames=True,
    )

    assert (settings.output_dir / "teststage_video_prompt_1.txt").exists()
    assert (settings.output_dir / "teststage_final_frame_prompt_1.txt").exists()
    assert (settings.output_dir / "teststage_final_frame_1.png").exists()
    assert (settings.output_dir / "teststage_music_prompt.txt").exists()
    assert (settings.output_dir / "teststage_source_description.txt").exists()
