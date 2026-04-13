from pathlib import Path
from uuid import uuid4

from config import GenerationConfig, MotionSource, Settings
from main_desktop_pipeline import write_pipeline_manifest


def test_write_pipeline_manifest_tracks_generated_files() -> None:
    tmp_path = Path("test_runtime") / f"api_pipeline_{uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    settings.output_dir = tmp_path / "output"
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    stage_id = "stage_test"
    image_path = tmp_path / "frame_a.png"
    image_path.write_bytes(b"img")
    (settings.output_dir / f"{stage_id}_final_frame_1.png").write_bytes(b"frame1")
    (settings.output_dir / f"{stage_id}_final_frame_prompt_1.txt").write_text("prompt1", encoding="utf-8")
    (settings.output_dir / f"{stage_id}_final_frame_prompt_2.txt").write_text("prompt2", encoding="utf-8")

    manifest_path = write_pipeline_manifest(
        settings,
        stage_id,
        image_path,
        GenerationConfig(video_count=2, camera_segments=1, motion_source=MotionSource.TABLE),
        generate_final_frames=True,
        generate_styled_images=False,
        generate_video=False,
        model_name="dall-e-2",
        prompt_model="gpt-4.1-mini",
        motion_model="gpt-4.1-mini",
    )

    manifest = manifest_path.read_text(encoding="utf-8")
    assert '"pipeline": "api-final-frames"' in manifest
    assert '"final_frame_prompt_created": true' in manifest
    assert '"final_frame_exists": true' in manifest
    assert '"final_frame_exists": false' in manifest
    assert '"initial_image":' in manifest
    assert "frame_a.png" in manifest
    assert '"v_prompt_file":' in manifest
    assert '"v_prm_ru_file":' in manifest
    assert '"description":' in manifest
    assert '"scene_analysis":' in manifest
    assert '"bg_prompt":' in manifest
    assert '"bg_prm_ru":' in manifest
    assert '"bg_prompt": null' in manifest
    assert '"m_prompt":' in manifest
    assert '"prompt_model": "gpt-4.1-mini"' in manifest
    assert '"motion_model": "gpt-4.1-mini"' in manifest
