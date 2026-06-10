from config import GenerationConfig, MotionSource, VideoFramingMode
from api.openai_motion_selector import _motion_prompt
from api.openai_prompt_synthesizer import _synthesizer_prompt
from main import _materialize_motion_sequences
from models.scene_analysis import SceneAnalysis
from utils.camera_movements import CameraMovementSets
from utils.image_analysis import ImageMetadata


def _build_metadata() -> ImageMetadata:
    return ImageMetadata(
        width=1000,
        height=1500,
        orientation="portrait",
        format_description="1000x1500, portrait",
        brightness_label="balanced",
        contrast_label="moderate-contrast",
        palette_label="warm palette",
        depth_label="soft layered depth",
        composition_label="balanced center composition",
        atmosphere_label="warm open atmosphere",
        scene_summary="portrait frame with a central emotional event",
    )


def test_materialize_motion_sequences_uses_ai_selector_output() -> None:
    metadata = _build_metadata()
    scene = SceneAnalysis(
        summary="Two people exchange rings during a ceremony.",
        people_count=2,
        background="formal ceremony interior",
        shot_type="medium shot",
        main_action="ring exchange",
        mood=["joy", "solemnity"],
        relationships=["bride and groom"],
    )

    def fake_motion_selector(**kwargs: object) -> list[list[str]]:
        assert kwargs["scene_analysis"] is scene
        assert kwargs["metadata"] is metadata
        assert kwargs["camera_segments"] == 2
        return [
            ["slow side move toward the hands with the ring", "gentle push-in toward the couple's smiles"],
            ["soft half-orbit around the couple", "delicate crane down to eye level"],
        ]

    sequences = _materialize_motion_sequences(
        GenerationConfig(video_count=2, camera_segments=2, motion_source=MotionSource.AI),
        CameraMovementSets(nearby=["unused"], distance=["unused"]),
        metadata=metadata,
        scene_analysis=scene,
        motion_model="gpt-4.1",
        motion_selector=fake_motion_selector,
    )

    assert sequences == [
        ["slow side move toward the hands with the ring", "gentle push-in toward the couple's smiles"],
        ["gentle push-in toward the couple's smiles", "delicate crane down to eye level"],
    ]


def test_materialize_motion_sequences_preserves_continuity_between_videos() -> None:
    metadata = _build_metadata()
    scene = SceneAnalysis(
        summary="Guests are watching the ceremony.",
        people_count=3,
        background="ceremonial interior",
        shot_type="medium shot",
        main_action="watching the ceremony",
        mood=["warmth"],
        relationships=["friends and relatives"],
    )

    def fake_motion_selector(**kwargs: object) -> list[list[str]]:
        return [
            ["slow push in toward the couple"],
            ["sharp pull out from the scene"],
        ]

    sequences = _materialize_motion_sequences(
        GenerationConfig(video_count=2, camera_segments=1, motion_source=MotionSource.AI),
        CameraMovementSets(nearby=["unused"], distance=["unused"]),
        metadata=metadata,
        scene_analysis=scene,
        motion_model="gpt-4.1",
        motion_selector=fake_motion_selector,
    )

    assert sequences == [
        ["slow push in toward the couple"],
        ["slow push in toward the couple"],
    ]


def test_motion_selector_prompt_prefers_distant_angles_for_visible_people() -> None:
    metadata = _build_metadata()
    scene = SceneAnalysis(
        summary="Family portrait with two visible adults.",
        people_count=2,
        background="living room",
        shot_type="medium shot",
        main_action="the family sits together",
        mood=["warmth"],
        relationships=["family"],
    )

    prompt_text = _motion_prompt(
        metadata=metadata,
        scene_analysis=scene,
        video_count=1,
        camera_segments=2,
        framing_mode=VideoFramingMode.IDENTITY_SAFE,
    )

    assert "предпочитай более безопасные для идентичности ракурсы" in prompt_text
    assert "Избегай экстремальных крупных планов лица" in prompt_text
    assert "а не через резкое укрупнение лица" in prompt_text


def test_openai_synthesizer_prompt_prefers_identity_safe_framing_for_people() -> None:
    payload = {
        "stage_id": "stage_test",
        "prompt_index": 1,
        "total_videos": 1,
        "initial_frame_description": "frame A",
        "format_description": "1000x1500, portrait",
        "scene_summary": "family portrait",
        "composition_label": "centered",
        "brightness_label": "balanced",
        "contrast_label": "moderate",
        "palette_label": "warm palette",
        "depth_label": "soft layered depth",
        "atmosphere_label": "warm atmosphere",
        "scene_analysis": {
            "summary": "family portrait",
            "people_count": 2,
            "people": [{"label": "man"}, {"label": "woman"}],
            "background": "living room",
            "shot_type": "medium shot",
            "main_action": "family sitting together",
            "mood": ["warmth"],
            "relationships": ["family"],
        },
        "motion_sequence": ["slow side move"],
    }

    prompt_text = _synthesizer_prompt(payload, framing_mode=VideoFramingMode.IDENTITY_SAFE)

    assert "prefer identity-safe framing" in prompt_text
    assert "Avoid aggressive facial enlargement" in prompt_text
    assert "rather than through an oversized facial close-up" in prompt_text


def test_motion_selector_prompt_allows_close_face_framing_when_requested() -> None:
    metadata = _build_metadata()
    scene = SceneAnalysis(
        summary="Adult portrait with one clearly visible face.",
        people_count=1,
        background="neutral studio interior",
        shot_type="medium shot",
        main_action="the subject looks into the distance",
        mood=["focus"],
        relationships=[],
    )

    prompt_text = _motion_prompt(
        metadata=metadata,
        scene_analysis=scene,
        video_count=1,
        camera_segments=2,
        framing_mode=VideoFramingMode.FACE_CLOSEUP,
    )

    assert "close facial framing is allowed" in prompt_text
    assert "gentle push-ins toward the face are acceptable" in prompt_text


def test_openai_synthesizer_prompt_uses_ai_optimal_framing_when_requested() -> None:
    payload = {
        "stage_id": "stage_test",
        "prompt_index": 1,
        "total_videos": 1,
        "initial_frame_description": "frame A",
        "format_description": "1000x1500, portrait",
        "scene_summary": "adult portrait",
        "composition_label": "centered",
        "brightness_label": "balanced",
        "contrast_label": "moderate",
        "palette_label": "warm palette",
        "depth_label": "soft layered depth",
        "atmosphere_label": "warm atmosphere",
        "scene_analysis": {
            "summary": "adult portrait",
            "people_count": 1,
            "people": [{"label": "man"}],
            "background": "garden",
            "shot_type": "medium shot",
            "main_action": "the subject walks forward",
            "mood": ["hope"],
            "relationships": [],
        },
        "motion_sequence": ["slow push in"],
    }

    prompt_text = _synthesizer_prompt(payload, framing_mode=VideoFramingMode.AI_OPTIMAL)

    assert "most cinematic and effective for the source image" in prompt_text
    assert "Do not optimize for avoiding facial enlargement" in prompt_text
