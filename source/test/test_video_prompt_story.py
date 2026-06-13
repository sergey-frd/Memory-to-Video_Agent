from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from utils.video_prompt_story import (
    build_empty_story_draft,
    build_story_references,
    discover_story_image_candidates,
    load_story_draft_from_html,
    rescale_story_draft_duration,
    select_story_image_candidates,
    story_draft_to_video_prompt_config,
    write_story_html,
)
from video_prompt_story_config import VideoPromptStoryConfig, load_video_prompt_story_config


def _make_temp_root(prefix: str) -> Path:
    root = Path("test_runtime") / f"{prefix}_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_stage_assets(regeneration_assets_dir: Path, source_stem: str) -> None:
    stage_dir = regeneration_assets_dir / f"{source_stem}_20260609_120000"
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / f"{stage_dir.name}_description.txt").write_text(
        "Image format: horizontal landscape frame.\n"
        "Scene composition:\n"
        "- Narrative summary: warm family memory.\n"
        "Cinematic motion logic:\n"
        "- test",
        encoding="utf-8",
    )
    (stage_dir / f"{stage_dir.name}_scene_analysis_ru.json").write_text(
        json.dumps(
            {
                "summary": "Семейная сцена.",
                "people_count": 2,
                "background": "Дом",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_load_video_prompt_story_config_from_generation_config_file() -> None:
    root = _make_temp_root("story_config_generation")
    generation_config_path = root / "generation_config.json"
    generation_config_path.write_text(
        json.dumps(
            {
                "regeneration_assets_dir": str(root / "regeneration_assets"),
                "final_output_dir": str(root / "output"),
                "grok_multiscene_prompt_size": 2200,
                "prefer_loving_kindness_tone": True,
            }
        ),
        encoding="utf-8",
    )
    (root / "regeneration_assets").mkdir(parents=True, exist_ok=True)
    (root / "output" / "chatgpt_photo_restoration").mkdir(parents=True, exist_ok=True)
    config_path = root / "video_prompt_story_config.json"
    config_path.write_text(
        json.dumps(
            {
                "generation_config_file": str(generation_config_path),
                "story_title": "Test story",
                "image_count": 3,
                "scene_count": 2,
                "scene_duration_seconds": 2,
                "total_duration_seconds": 4,
            }
        ),
        encoding="utf-8",
    )

    config = load_video_prompt_story_config(config_path)

    assert config.max_prompt_chars == 2200
    assert config.restored_images_dir == root / "output" / "chatgpt_photo_restoration"
    assert config.total_duration_seconds == 4


def test_discover_and_select_story_images() -> None:
    root = _make_temp_root("story_discover")
    restored_images_dir = root / "output" / "chatgpt_photo_restoration"
    regeneration_assets_dir = root / "regeneration_assets"
    restored_images_dir.mkdir(parents=True, exist_ok=True)
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)

    for index in range(1, 4):
        source_file = f"frame_{index:02d}.png"
        (restored_images_dir / source_file).write_bytes(b"png")
        _write_stage_assets(regeneration_assets_dir, Path(source_file).stem)

    config = VideoPromptStoryConfig(
        regeneration_assets_dir=regeneration_assets_dir,
        restored_images_dir=restored_images_dir,
        image_count=2,
        scene_count=2,
        scene_duration_seconds=2,
        total_duration_seconds=4,
        source_files=("frame_02.png", "frame_03.png"),
    )

    candidates = discover_story_image_candidates(config)
    selected = select_story_image_candidates(config, candidates)
    references = build_story_references(selected)

    assert len(candidates) == 3
    assert [candidate.source_file for candidate in selected] == ["frame_02.png", "frame_03.png"]
    assert [reference.tag for reference in references] == ["@image1", "@image2"]


def test_story_html_roundtrip_and_export_config() -> None:
    root = _make_temp_root("story_html")
    restored_images_dir = root / "output" / "chatgpt_photo_restoration"
    regeneration_assets_dir = root / "regeneration_assets"
    restored_images_dir.mkdir(parents=True, exist_ok=True)
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)

    image_path = restored_images_dir / "frame_01.png"
    image_path.write_bytes(b"png")
    _write_stage_assets(regeneration_assets_dir, image_path.stem)

    config = VideoPromptStoryConfig(
        regeneration_assets_dir=regeneration_assets_dir,
        restored_images_dir=restored_images_dir,
        image_count=1,
        scene_count=2,
        scene_duration_seconds=2,
        total_duration_seconds=4,
        source_files=("frame_01.png",),
    )
    references = build_story_references(
        select_story_image_candidates(config, discover_story_image_candidates(config))
    )
    draft = build_empty_story_draft(
        config,
        references=references,
        image_paths=(image_path,),
        technical_preamble="Technical Preamble:\nТёплая семейная история.",
        scene_descriptions=[
            "Семья @image1 улыбается.",
            "Семья @image1 возвращается домой.",
        ],
    )

    html_path = root / "story.html"
    write_story_html(html_path, draft)
    html_text = html_path.read_text(encoding="utf-8")
    assert "frame_01.png" in html_text
    assert "@image1" in html_text
    assert "../output/chatgpt_photo_restoration/frame_01.png" in html_text

    loaded = load_story_draft_from_html(html_path)
    assert loaded.technical_preamble == draft.technical_preamble
    assert [scene.description for scene in loaded.scenes] == [
        scene.description for scene in draft.scenes
    ]

    exported = story_draft_to_video_prompt_config(loaded)
    assert exported["total_duration_seconds"] == 4
    assert exported["references"][0]["tag"] == "@image1"
    assert exported["scenes"][0]["description"].endswith("@image1.")


def test_rescale_story_draft_duration_to_fifteen_seconds() -> None:
    root = _make_temp_root("story_rescale")
    restored_images_dir = root / "output" / "chatgpt_photo_restoration"
    regeneration_assets_dir = root / "regeneration_assets"
    restored_images_dir.mkdir(parents=True, exist_ok=True)
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)

    image_path = restored_images_dir / "frame_01.png"
    image_path.write_bytes(b"png")
    _write_stage_assets(regeneration_assets_dir, image_path.stem)

    config = VideoPromptStoryConfig(
        regeneration_assets_dir=regeneration_assets_dir,
        restored_images_dir=restored_images_dir,
        image_count=1,
        scene_count=5,
        scene_duration_seconds=2,
        total_duration_seconds=10,
        source_files=("frame_01.png",),
    )
    references = build_story_references(
        select_story_image_candidates(config, discover_story_image_candidates(config))
    )
    draft = build_empty_story_draft(
        config,
        references=references,
        image_paths=(image_path,),
        technical_preamble="Technical Preamble:\nИстория.",
        scene_descriptions=[f"Сцена {index} @image1." for index in range(1, 6)],
    )

    rescaled = rescale_story_draft_duration(draft, scene_duration_seconds=3)

    assert rescaled.total_duration_seconds == 15
    assert all(scene.duration_seconds == 3 for scene in rescaled.scenes)
    assert rescaled.scenes[-1].end_seconds == 15


def test_load_video_prompt_story_config_rejects_invalid_total_duration() -> None:
    root = _make_temp_root("story_invalid_total")
    restored_images_dir = root / "output" / "chatgpt_photo_restoration"
    regeneration_assets_dir = root / "regeneration_assets"
    restored_images_dir.mkdir(parents=True, exist_ok=True)
    regeneration_assets_dir.mkdir(parents=True, exist_ok=True)
    config_path = root / "video_prompt_story_config.json"
    config_path.write_text(
        json.dumps(
            {
                "regeneration_assets_dir": str(regeneration_assets_dir),
                "restored_images_dir": str(restored_images_dir),
                "scene_count": 5,
                "scene_duration_seconds": 2,
                "total_duration_seconds": 15,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="total_duration_seconds must equal"):
        load_video_prompt_story_config(config_path)


def test_openai_story_prompt_requires_dynamic_video_not_slideshow() -> None:
    from api.openai_video_prompt_story import _story_prompt

    prompt = _story_prompt(
        {
            "story_brief": "Birthday tribute.",
            "scene_count": 5,
            "scenes_to_write": 5,
        }
    )

    lowered = prompt.casefold()
    assert "slideshow" in lowered
    assert "dissolve" in lowered
    assert "ken burns" in lowered
    assert "handheld" in lowered
    assert "match cut" in lowered
