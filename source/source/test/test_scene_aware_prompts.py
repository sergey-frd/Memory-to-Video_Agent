from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw

from config import VideoFramingMode
from models.scene_analysis import PersonInFrame, SceneAnalysis
from utils.image_analysis import analyze_image
from utils.prompt_builder import PromptBuilder


def _make_test_image(path: Path, *, background: tuple[int, int, int], center: tuple[int, int, int]) -> None:
    image = Image.new("RGB", (160, 240), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 60, 120, 180), fill=center)
    image.save(path)


def test_analyze_image_derives_different_scene_summaries() -> None:
    root = Path("test_runtime") / f"scene_analysis_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    warm_path = root / "warm.png"
    cool_path = root / "cool.png"

    _make_test_image(warm_path, background=(220, 180, 120), center=(250, 220, 180))
    _make_test_image(cool_path, background=(35, 55, 110), center=(120, 180, 255))

    warm_metadata = analyze_image(warm_path)
    cool_metadata = analyze_image(cool_path)

    assert warm_metadata.scene_summary != cool_metadata.scene_summary
    assert warm_metadata.palette_label != cool_metadata.palette_label
    assert warm_metadata.atmosphere_label != cool_metadata.atmosphere_label


def test_prompt_builder_produces_english_and_russian_video_prompts() -> None:
    root = Path("test_runtime") / f"scene_prompt_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / "detail_rich.png"

    image = Image.new("RGB", (240, 160), (20, 20, 20))
    draw = ImageDraw.Draw(image)
    for index in range(0, 240, 12):
        draw.line((index, 0, 239 - index // 2, 159), fill=(240, 180, 90), width=3)
    image.save(image_path)

    metadata = analyze_image(image_path)
    bundle = PromptBuilder(metadata, "stage_scene").build_video_prompt(
        prompt_index=1,
        total_videos=2,
        initial_frame_description="???? A (???????? ????)",
        motion_sequence=["????????? ???????????"],
    )

    assert "SCENE:" in bundle.video_prompt
    assert "SUBJECTS:" in bundle.video_prompt
    assert "CAMERA:" in bundle.video_prompt
    assert "?????:" in bundle.video_prompt_ru
    assert "?????????:" in bundle.video_prompt_ru
    assert "??????:" in bundle.video_prompt_ru
    assert "????????? ????" in bundle.final_frame_prompt


def test_prompt_builder_prefers_identity_safe_camera_for_visible_people() -> None:
    root = Path("test_runtime") / f"scene_prompt_people_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / "family.png"

    image = Image.new("RGB", (240, 160), (60, 70, 80))
    draw = ImageDraw.Draw(image)
    draw.ellipse((70, 20, 170, 140), fill=(220, 180, 160))
    image.save(image_path)

    metadata = analyze_image(image_path)
    scene = SceneAnalysis(
        summary="A family scene with visible people in a warm interior.",
        people_count=2,
        people=[PersonInFrame(label="man"), PersonInFrame(label="woman")],
        background="warm interior",
        shot_type="medium shot",
        main_action="two people sit together and smile",
        mood=["warmth", "calm"],
        relationships=["family closeness"],
    )

    bundle = PromptBuilder(metadata, "stage_people", scene_analysis=scene).build_video_prompt(
        prompt_index=1,
        total_videos=1,
        initial_frame_description="frame A (source frame)",
        motion_sequence=["slow side move"],
    )

    assert "identity-safe framing from a respectful distance" in bundle.video_prompt
    assert "Avoid aggressive face push-ins and extreme frontal close-ups" in bundle.video_prompt
    assert "При видимых людях предпочитать наблюдение с дистанции" in bundle.video_prompt_ru
    assert "Не делать агрессивный наезд в лицо" in bundle.video_prompt_ru


def test_background_prompt_prefers_non_copy_result() -> None:
    root = Path("test_runtime") / f"scene_bg_prompt_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / "scene.png"

    image = Image.new("RGB", (240, 160), (80, 90, 120))
    draw = ImageDraw.Draw(image)
    draw.ellipse((60, 30, 180, 140), fill=(220, 190, 160))
    image.save(image_path)

    metadata = analyze_image(image_path)
    bundle = PromptBuilder(metadata, "stage_scene").build_background_prompt_bundle(["slow push in"])

    assert "Prefer a background plate without the main visible people" in bundle.background_prompt
    assert "stronger background blur" in bundle.background_prompt
    assert "No dragons, wolves, monsters, storms" in bundle.background_prompt
    assert "same real-world scene" in bundle.background_prompt
    assert "realistic associative environmental image" in bundle.background_prompt
    assert "balanced fusion" in bundle.background_prompt
    assert "blurred, transformed echo of the source image" in bundle.background_prompt
    assert "clearly readable and realistic" in bundle.background_prompt
    assert "enlarged in scale" in bundle.background_prompt
    assert "realistic associative environmental image" in bundle.association_prompt
    assert "serve as a background plate" in bundle.association_prompt


def test_prompt_builder_can_allow_close_face_framing_when_requested() -> None:
    root = Path("test_runtime") / f"scene_prompt_closeup_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / "portrait.png"

    image = Image.new("RGB", (240, 160), (60, 70, 80))
    draw = ImageDraw.Draw(image)
    draw.ellipse((70, 20, 170, 140), fill=(220, 180, 160))
    image.save(image_path)

    metadata = analyze_image(image_path)
    scene = SceneAnalysis(
        summary="Single adult portrait.",
        people_count=1,
        people=[PersonInFrame(label="woman")],
        background="soft neutral background",
        shot_type="medium shot",
        main_action="the subject smiles softly",
        mood=["warmth"],
        relationships=[],
    )

    bundle = PromptBuilder(
        metadata,
        "stage_closeup",
        scene_analysis=scene,
        framing_mode=VideoFramingMode.FACE_CLOSEUP,
    ).build_video_prompt(
        prompt_index=1,
        total_videos=1,
        initial_frame_description="frame A (source frame)",
        motion_sequence=["slow push in"],
    )

    assert "close facial framing is allowed" in bundle.video_prompt
    assert "gentle push-ins toward the face are acceptable" in bundle.video_prompt


def test_prompt_builder_can_use_ai_optimal_framing_when_requested() -> None:
    root = Path("test_runtime") / f"scene_prompt_optimal_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / "portrait.png"

    image = Image.new("RGB", (240, 160), (80, 70, 90))
    draw = ImageDraw.Draw(image)
    draw.ellipse((70, 20, 170, 140), fill=(220, 180, 160))
    image.save(image_path)

    metadata = analyze_image(image_path)
    scene = SceneAnalysis(
        summary="Single adult portrait.",
        people_count=1,
        people=[PersonInFrame(label="man")],
        background="city background",
        shot_type="medium shot",
        main_action="the subject turns toward the camera",
        mood=["focus"],
        relationships=[],
    )

    bundle = PromptBuilder(
        metadata,
        "stage_optimal",
        scene_analysis=scene,
        framing_mode=VideoFramingMode.AI_OPTIMAL,
    ).build_video_prompt(
        prompt_index=1,
        total_videos=1,
        initial_frame_description="frame A (source frame)",
        motion_sequence=["slow push in"],
    )

    assert "strongest cinematic reading of the scene" in bundle.video_prompt
    assert "Do not optimize for avoiding facial enlargement" in bundle.video_prompt
