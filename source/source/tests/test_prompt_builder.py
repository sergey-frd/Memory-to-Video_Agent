from utils.prompt_builder import PromptBuilder


def test_builder_generates_prompts_without_camera_after_strip() -> None:
    builder = PromptBuilder(metadata_description="640x480, ландшафтная", stage_id="stage_test")
    bundle = builder.build_video_prompt(
        prompt_index=2,
        total_videos=3,
        initial_frame_description="кадр А",
        motion_sequence=["Медленное приближение", "Орбита 180"],
    )

    assert "stage_test" in bundle.video_prompt
    assert "КАМЕРА:" in bundle.video_prompt
    assert "- Первая половина видео — Медленное приближение" in bundle.video_prompt
    assert "- Вторая половина видео — Орбита 180" in bundle.video_prompt
    assert bundle.image_edit_prompt is not None
    assert "движение" not in bundle.image_edit_prompt.lower()
    assert "камера" not in bundle.image_edit_prompt.lower()
    assert "финальный кадр" in bundle.final_frame_prompt.lower()
    assert "камера" not in bundle.final_frame_prompt.lower()
    assert "движение" not in bundle.final_frame_prompt.lower()
    assert "визуально преобразовать исходную фотографию" in bundle.final_frame_prompt.lower()
    assert "увеличить героя или группу в кадре" in bundle.final_frame_prompt.lower()
