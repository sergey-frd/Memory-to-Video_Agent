from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from utils.video_prompt_composer import (
    GeneratedVideoPromptBundle,
    load_video_prompt_request,
    resolve_reference_contexts,
    write_generated_prompt_files,
    write_generated_seedance_prompt_file,
)


def test_load_request_and_resolve_reference_contexts() -> None:
    root = Path("test_runtime") / f"video_prompt_{uuid4().hex}"
    regeneration_assets_dir = root / "regeneration_assets"
    stage_dir = regeneration_assets_dir / "source_frame_20260414_120000"
    stage_dir.mkdir(parents=True, exist_ok=True)

    (stage_dir / "source_frame_20260414_120000_description.txt").write_text(
        "Image format: horizontal landscape frame.\n"
        "Scene composition:\n"
        "- Narrative summary: calm river bank.\n"
        "Cinematic motion logic:\n"
        "- test",
        encoding="utf-8",
    )
    (stage_dir / "source_frame_20260414_120000_scene_analysis.json").write_text(
        json.dumps({"summary": "English river scene.", "people_count": 1, "background": "River bank"}),
        encoding="utf-8",
    )
    (stage_dir / "source_frame_20260414_120000_scene_analysis_ru.json").write_text(
        json.dumps(
            {
                "summary": "\u0420\u0443\u0441\u0441\u043a\u0430\u044f \u0441\u0446\u0435\u043d\u0430 \u0443 \u0440\u0435\u043a\u0438.",
                "people_count": 1,
                "background": "\u0411\u0435\u0440\u0435\u0433 \u0440\u0435\u043a\u0438",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    request_text = json.dumps(
        {
            "technical_preamble": "Theme: summer fishing on the Volga.",
            "total_duration_seconds": 6,
            "aspect_ratio": "16:9",
            "regeneration_assets_dir": str(regeneration_assets_dir),
            "references": [{"source_file": "source_frame.jpg", "tag": "@image1"}],
            "scenes": [
                {"duration_seconds": 3, "description": "Slava @image1 prepares the rod."},
                {"duration_seconds": 3, "description": "Slava @image1 smiles at the river."},
            ],
        },
        ensure_ascii=False,
    )

    request = load_video_prompt_request(request_text)
    contexts = resolve_reference_contexts(request)

    assert len(contexts) == 1
    assert contexts[0].tag == "@image1"
    assert contexts[0].scene_analysis_en["summary"] == "English river scene."
    assert (
        contexts[0].scene_analysis_ru["summary"]
        == "\u0420\u0443\u0441\u0441\u043a\u0430\u044f \u0441\u0446\u0435\u043d\u0430 \u0443 \u0440\u0435\u043a\u0438."
    )


def test_write_generated_prompt_files_creates_expected_names() -> None:
    root = Path("test_runtime") / f"video_prompt_output_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)

    en_path, ru_path = write_generated_prompt_files(
        root,
        GeneratedVideoPromptBundle(
            video_prompt_en="Shot 1: test",
            video_prompt_ru="Shot 1: \u0442\u0435\u0441\u0442",
        ),
    )

    assert en_path.name.startswith("Gen_Video_")
    assert ru_path.name.startswith("Gen_Video_RU_")
    assert en_path.read_text(encoding="utf-8") == "Shot 1: test"
    assert ru_path.read_text(encoding="utf-8") == "Shot 1: \u0442\u0435\u0441\u0442"


def test_write_generated_seedance_prompt_file_creates_json_output() -> None:
    root = Path("test_runtime") / f"video_prompt_seedance_output_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)

    seedance_path = write_generated_seedance_prompt_file(
        root,
        '[{"lang":"en","prompt":"Shot 1: test. Total: 3s / 1 shots / 16:9"}]',
    )

    assert seedance_path.name.startswith("Gen_Video_Seedance_")
    assert seedance_path.suffix == ".json"
    assert json.loads(seedance_path.read_text(encoding="utf-8")) == [
        {"lang": "en", "prompt": "Shot 1: test. Total: 3s / 1 shots / 16:9"}
    ]
