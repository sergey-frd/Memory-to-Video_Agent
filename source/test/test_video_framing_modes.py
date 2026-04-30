from argparse import Namespace
from pathlib import Path
from uuid import uuid4

from PIL import Image

from config import GenerationConfig, MotionSource, Settings, VideoFramingMode
from main import _run_generation
from main_desktop_pipeline import write_pipeline_manifest
from models.scene_analysis import PersonInFrame, SceneAnalysis
from utils.prompt_builder import PromptBundle


def test_run_generation_creates_two_prompt_variants_in_dual_framing_mode() -> None:
    root = Path("test_runtime") / f"scene_pipeline_dual_{uuid4().hex}"
    input_dir = root / "input"
    output_dir = root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    image_path = input_dir / "frame.png"
    Image.new("RGB", (160, 240), (140, 110, 80)).save(image_path)

    settings = Settings()
    settings.output_dir = output_dir

    args = Namespace(
        image=image_path,
        stage_id="scene_stage_dual",
        scene_model="gpt-4.1",
        generate_video=False,
        generate_styled_images=False,
        prompt_model="gpt-4.1",
        motion_model="gpt-4.1",
    )

    def fake_scene_analyzer(_image_path: Path, _model: str | None) -> SceneAnalysis:
        return SceneAnalysis(
            summary="Adult portrait scene.",
            people_count=1,
            people=[PersonInFrame(label="man")],
            background="park",
            shot_type="medium shot",
            main_action="the subject walks forward",
            mood=["hope"],
            relationships=[],
        )

    seen_modes: list[VideoFramingMode] = []

    def fake_prompt_synthesizer(**kwargs: object) -> PromptBundle:
        framing_mode = kwargs["framing_mode"]
        assert isinstance(framing_mode, VideoFramingMode)
        seen_modes.append(framing_mode)
        return PromptBundle(
            video_prompt=f"mode={framing_mode.value}",
            video_prompt_ru=f"mode={framing_mode.value}",
            final_frame_prompt="final frame prompt",
        )

    _run_generation(
        args,
        GenerationConfig(
            video_count=1,
            camera_segments=1,
            motion_source=MotionSource.TABLE,
            generate_final_frames=False,
            generate_dual_framing_videos=True,
        ),
        settings=settings,
        scene_analyzer=fake_scene_analyzer,
        prompt_synthesizer=fake_prompt_synthesizer,
        generate_final_frames=False,
    )

    assert seen_modes == [VideoFramingMode.IDENTITY_SAFE, VideoFramingMode.AI_OPTIMAL]
    assert (output_dir / "scene_stage_dual_v_prompt_1.txt").read_text(encoding="utf-8") == "mode=identity_safe"
    assert (output_dir / "scene_stage_dual_v_prompt_2.txt").read_text(encoding="utf-8") == "mode=ai_optimal"


def test_write_pipeline_manifest_tracks_dual_framing_steps() -> None:
    root = Path("test_runtime") / f"framing_manifest_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    settings.output_dir = root / "output"
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    stage_id = "stage_test"
    image_path = root / "frame_a.png"
    image_path.write_bytes(b"img")

    manifest_path = write_pipeline_manifest(
        settings,
        stage_id,
        image_path,
        GenerationConfig(
            video_count=1,
            camera_segments=1,
            motion_source=MotionSource.TABLE,
            generate_dual_framing_videos=True,
        ),
        generate_final_frames=False,
        generate_styled_images=False,
        generate_video=False,
        model_name="dall-e-2",
        prompt_model="gpt-4.1-mini",
        motion_model="gpt-4.1-mini",
    )

    manifest = manifest_path.read_text(encoding="utf-8")
    assert '"total_video_outputs": 2' in manifest
    assert '"framing_modes": [' in manifest
    assert '"framing_mode": "identity_safe"' in manifest
    assert '"framing_mode": "ai_optimal"' in manifest
