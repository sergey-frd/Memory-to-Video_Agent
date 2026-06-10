from config import GenerationConfig
from models.scene_analysis import PersonInFrame, SceneAnalysis
from utils.image_analysis import ImageMetadata
from utils.prompt_builder import PromptBuilder


def _metadata() -> ImageMetadata:
    return ImageMetadata(
        width=1080,
        height=1350,
        orientation="portrait",
        format_description="1080x1350, portrait",
        brightness_label="balanced",
        contrast_label="moderate-contrast",
        palette_label="balanced natural palette",
        depth_label="clear mid-depth separation",
        composition_label="subject-forward composition",
        atmosphere_label="grounded natural atmosphere",
        scene_summary="portrait frame with a person-centered composition",
    )


def _selfie_scene() -> SceneAnalysis:
    return SceneAnalysis(
        summary="\u0414\u0435\u0432\u0443\u0448\u043a\u0430 \u0434\u0435\u043b\u0430\u0435\u0442 \u0441\u0435\u043b\u0444\u0438 \u0432 \u0437\u0435\u0440\u043a\u0430\u043b\u0435",
        people_count=1,
        people=[
            PersonInFrame(
                label="\u0434\u0435\u0432\u0443\u0448\u043a\u0430",
                role_in_scene="\u0430\u0432\u0442\u043e\u043f\u043e\u0440\u0442\u0440\u0435\u0442",
                pose="\u0434\u0435\u0440\u0436\u0438\u0442 \u0442\u0435\u043b\u0435\u0444\u043e\u043d \u043f\u0435\u0440\u0435\u0434 \u043b\u0438\u0446\u043e\u043c",
            )
        ],
        background="\u0441\u043f\u0430\u043b\u044c\u043d\u044f \u0441 \u0437\u0435\u0440\u043a\u0430\u043b\u043e\u043c",
        shot_type="mirror selfie",
        main_action="\u0434\u0435\u043b\u0430\u0435\u0442 \u0441\u0435\u043b\u0444\u0438 \u043d\u0430 \u0442\u0435\u043b\u0435\u0444\u043e\u043d",
        mood=["calm"],
    )


def test_generation_config_hides_phone_in_selfie_by_default() -> None:
    assert GenerationConfig().hide_phone_in_selfie is True


def test_generation_config_keeps_loving_kindness_disabled_by_default() -> None:
    assert GenerationConfig().prefer_loving_kindness_tone is False


def test_prompt_builder_adds_phone_avoidance_for_selfie_scene() -> None:
    builder = PromptBuilder(
        metadata=_metadata(),
        stage_id="stage_selfie",
        scene_analysis=_selfie_scene(),
    )

    bundle = builder.build_video_prompt(
        prompt_index=1,
        total_videos=1,
        initial_frame_description="frame A (source frame)",
        motion_sequence=["Slow push in"],
    )

    video_prompt_lower = bundle.video_prompt.lower()
    video_prompt_ru_lower = bundle.video_prompt_ru.lower()
    final_frame_prompt_lower = bundle.final_frame_prompt.lower()

    assert "selfie" in video_prompt_lower
    assert "avoid showing the phone" in video_prompt_lower
    assert "\u0441\u0435\u043b\u0444\u0438" in video_prompt_ru_lower
    assert "\u0442\u0435\u043b\u0435\u0444\u043e\u043d" in video_prompt_ru_lower
    assert "\u0442\u0435\u043b\u0435\u0444\u043e\u043d" in final_frame_prompt_lower


def test_prompt_builder_adds_loving_kindness_tone_when_enabled() -> None:
    builder = PromptBuilder(
        metadata=_metadata(),
        stage_id="stage_kindness",
        scene_analysis=_selfie_scene(),
        prefer_loving_kindness_tone=True,
    )

    bundle = builder.build_video_prompt(
        prompt_index=1,
        total_videos=1,
        initial_frame_description="frame A (source frame)",
        motion_sequence=["Slow push in"],
    )
    bg_bundle = builder.build_background_prompt_bundle(["Wide reveal"])

    assert "loving-kindness tone" in bundle.video_prompt.lower()
    assert "goodwill" in bundle.video_prompt.lower()
    assert "loving-kindness mood" in bg_bundle.background_prompt.lower()
    assert "loving-kindness" in bg_bundle.association_prompt.lower()
