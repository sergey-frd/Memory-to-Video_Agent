from __future__ import annotations

import json
import os
import shutil
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from api.openai_video_prompt_composer import (
    _validate_generated_prompt_bundle,
    _validate_seedance_control_json_prompt,
    _validate_seedance_json_prompt,
)
from utils.video_prompt_composer import (
    GeneratedSeedanceJsonBundle,
    GeneratedVideoPromptBundle,
    ScenarioVariantSpec,
    load_video_prompt_request,
    resolve_reference_contexts,
    write_generated_prompt_files,
    write_generated_seedance_prompt_file,
    write_generated_seedance_prompt_files,
)


def _rmtree_with_permissions(path: Path) -> None:
    def _retry(function, target_path, excinfo):
        last_error = excinfo if isinstance(excinfo, BaseException) else excinfo[1]
        for _ in range(10):
            try:
                os.chmod(target_path, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
            try:
                function(target_path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.1)
        raise last_error

    last_error: Exception | None = None
    for _ in range(10):
        try:
            shutil.rmtree(path, onexc=_retry)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.1)
    if last_error is not None:
        raise last_error


@contextmanager
def _temporary_test_root(prefix: str):
    base_dir = Path(".tmp_video_prompt_tests")
    base_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = base_dir / prefix
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield temp_dir
    finally:
        if temp_dir.exists():
            try:
                _rmtree_with_permissions(temp_dir)
            except PermissionError:
                pass
        try:
            next(base_dir.iterdir())
        except StopIteration:
            base_dir.rmdir()


def test_load_request_and_resolve_reference_contexts() -> None:
    with _temporary_test_root(f"video_prompt_{uuid4().hex}_") as root:
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
        assert request.max_prompt_chars == 2000
        assert request.scenario_variants == [
            ScenarioVariantSpec(
                variant_id="Variant_1",
                label="Variant 1",
                instruction="Create the most likely, most suitable, and best-fitting cinematic interpretation.",
            )
        ]
        assert contexts[0].scene_analysis_en["summary"] == "English river scene."
        assert (
            contexts[0].scene_analysis_ru["summary"]
            == "\u0420\u0443\u0441\u0441\u043a\u0430\u044f \u0441\u0446\u0435\u043d\u0430 \u0443 \u0440\u0435\u043a\u0438."
        )


def test_resolve_reference_contexts_searches_across_regeneration_assets_siblings() -> None:
    with _temporary_test_root(f"video_prompt_sibling_roots_{uuid4().hex}_") as root:
        regeneration_assets_dir = root / "regeneration_assets"
        regeneration_assets_dir.mkdir(parents=True, exist_ok=True)

        older_stage_dir = root / "regeneration_assets_2" / "source_frame_20260414_120000"
        older_stage_dir.mkdir(parents=True, exist_ok=True)
        (older_stage_dir / "source_frame_20260414_120000_description.txt").write_text(
            "Image format: horizontal landscape frame.\n"
            "Scene composition:\n"
            "- Narrative summary: older stage.\n",
            encoding="utf-8",
        )
        (older_stage_dir / "source_frame_20260414_120000_scene_analysis.json").write_text(
            json.dumps({"summary": "Older sibling summary.", "people_count": 1}),
            encoding="utf-8",
        )
        (older_stage_dir / "source_frame_20260414_120000_scene_analysis_ru.json").write_text(
            json.dumps(
                {
                    "summary": "\u0421\u0442\u0430\u0440\u043e\u0435 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435.",
                    "people_count": 1,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        newest_stage_dir = root / "regeneration_assets_4" / "source_frame_20260414_130000"
        newest_stage_dir.mkdir(parents=True, exist_ok=True)
        (newest_stage_dir / "source_frame_20260414_130000_description.txt").write_text(
            "Image format: horizontal landscape frame.\n"
            "Scene composition:\n"
            "- Narrative summary: newest stage.\n",
            encoding="utf-8",
        )
        (newest_stage_dir / "source_frame_20260414_130000_scene_analysis.json").write_text(
            json.dumps({"summary": "Newest sibling summary.", "people_count": 2}),
            encoding="utf-8",
        )
        (newest_stage_dir / "source_frame_20260414_130000_scene_analysis_ru.json").write_text(
            json.dumps(
                {
                    "summary": "\u041d\u043e\u0432\u043e\u0435 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435.",
                    "people_count": 2,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        request_text = json.dumps(
            {
                "technical_preamble": "Theme: walking in the mountains.",
                "total_duration_seconds": 3,
                "aspect_ratio": "16:9",
                "regeneration_assets_dir": str(regeneration_assets_dir),
                "references": [{"source_file": "source_frame.jpg", "tag": "@image1"}],
                "scenes": [
                    {"duration_seconds": 3, "description": "The man @image1 walks forward."},
                ],
            },
            ensure_ascii=False,
        )

        request = load_video_prompt_request(request_text)
        contexts = resolve_reference_contexts(request)

        assert len(contexts) == 1
        assert contexts[0].stage_dir == newest_stage_dir
        assert contexts[0].stage_id == "source_frame_20260414_130000"
        assert contexts[0].scene_analysis_en["summary"] == "Newest sibling summary."
        assert (
            contexts[0].scene_analysis_ru["summary"]
            == "\u041d\u043e\u0432\u043e\u0435 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435."
        )


def test_load_request_accepts_multiple_scenario_variants_and_max_chars() -> None:
    with _temporary_test_root(f"video_prompt_variants_{uuid4().hex}_") as root:
        regeneration_assets_dir = root / "regeneration_assets"
        regeneration_assets_dir.mkdir(parents=True, exist_ok=True)

        request_text = json.dumps(
            {
                "technical_preamble": "Theme: long summer fishing story.",
                "total_duration_seconds": 6,
                "max_prompt_chars": 2500,
                "aspect_ratio": "16:9",
                "regeneration_assets_dir": str(regeneration_assets_dir),
                "references": [{"source_file": "source_frame.jpg", "tag": "@image1"}],
                "scenes": [
                    {"duration_seconds": 3, "description": "The man @image1 prepares the rod."},
                    {"duration_seconds": 3, "description": "The man @image1 watches the river."},
                ],
                "scenario_variants": [
                    {
                        "variant_id": "Variant_1",
                        "label": "Variant 1",
                        "instruction": "Most likely, best-fitting version.",
                    },
                    {
                        "variant_id": "Variant_2",
                        "label": "Variant 2",
                        "instruction": "Alternative, fully distinct version.",
                    },
                ],
            },
            ensure_ascii=False,
        )

        request = load_video_prompt_request(request_text)

        assert request.max_prompt_chars == 2500
        assert [variant.variant_id for variant in request.scenario_variants] == ["Variant_1", "Variant_2"]


def test_write_generated_prompt_files_creates_expected_names() -> None:
    with _temporary_test_root(f"video_prompt_output_{uuid4().hex}_") as root:

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
    with _temporary_test_root(f"video_prompt_seedance_output_{uuid4().hex}_") as root:

        seedance_path = write_generated_seedance_prompt_file(
            root,
            '[{"lang":"en","prompt":"Shot 1: test. Total: 3s / 1 shots / 16:9"}]',
        )

        assert seedance_path.name.startswith("Gen_Video_Seedance_")
        assert seedance_path.suffix == ".json"
        assert json.loads(seedance_path.read_text(encoding="utf-8")) == [
            {"lang": "en", "prompt": "Shot 1: test. Total: 3s / 1 shots / 16:9"}
        ]


def test_write_generated_seedance_prompt_files_creates_en_and_ru_json_outputs() -> None:
    with _temporary_test_root(f"video_prompt_seedance_bundle_output_{uuid4().hex}_") as root:

        en_path, ru_path = write_generated_seedance_prompt_files(
            root,
            GeneratedSeedanceJsonBundle(
                seedance_prompt_json_en='[{"lang":"en","prompt":"Shot 1: test. Total: 3s / 1 shots / 16:9"}]',
                seedance_prompt_json_ru='[{"lang":"ru","prompt":"Shot 1: \\u0442\\u0435\\u0441\\u0442. Total: 3s / 1 shots / 16:9"}]',
            ),
        )

        assert en_path.name.startswith("Gen_Video_Seedance_")
        assert ru_path.name.startswith("Gen_Video_Seedance_RU_")
        assert json.loads(en_path.read_text(encoding="utf-8")) == [
            {"lang": "en", "prompt": "Shot 1: test. Total: 3s / 1 shots / 16:9"}
        ]
        assert json.loads(ru_path.read_text(encoding="utf-8")) == [
            {"lang": "ru", "prompt": "Shot 1: \u0442\u0435\u0441\u0442. Total: 3s / 1 shots / 16:9"}
        ]


def test_write_generated_seedance_prompt_files_includes_variant_suffix_in_names() -> None:
    with _temporary_test_root(f"video_prompt_seedance_variant_output_{uuid4().hex}_") as root:

        en_path, ru_path = write_generated_seedance_prompt_files(
            root,
            GeneratedSeedanceJsonBundle(
                seedance_prompt_json_en='[{"lang":"en","prompt":"Shot 1: test. Total: 3s / 1 shots / 16:9"}]',
                seedance_prompt_json_ru='[{"lang":"ru","prompt":"Shot 1: \\u0442\\u0435\\u0441\\u0442. Total: 3s / 1 shots / 16:9"}]',
            ),
            variant_suffix="Variant_2",
        )

        assert "Variant_2" in en_path.name
        assert "Variant_2" in ru_path.name


def test_validate_seedance_control_json_prompt_rejects_english_shot_bodies() -> None:
    with _temporary_test_root(f"video_prompt_seedance_ru_validation_{uuid4().hex}_") as root:
        regeneration_assets_dir = root / "regeneration_assets"
        regeneration_assets_dir.mkdir(parents=True, exist_ok=True)
        request = load_video_prompt_request(
            json.dumps(
                {
                    "technical_preamble": "Theme: travel story.",
                    "total_duration_seconds": 6,
                    "aspect_ratio": "16:9",
                    "regeneration_assets_dir": str(regeneration_assets_dir),
                    "references": [{"source_file": "source_frame.jpg", "tag": "@image1"}],
                    "scenes": [
                        {"duration_seconds": 3, "description": "The man @image1 walks."},
                        {"duration_seconds": 3, "description": "The man @image1 returns home."},
                    ],
                }
            )
        )
        seedance_json_ru = json.dumps(
            [
                {
                    "lang": "ru",
                    "prompt": (
                        "\u043c\u043e\u043d\u0442\u0430\u0436, \u0434\u043e\u0440\u043e\u0433\u0430. "
                        "Shot 1: Wide aerial shot of the man @image1 walking in the mountains. "
                        "Shot 2: Wide shot of the man @image1 returning home. "
                        "Total: 6s / 2 shots / 16:9"
                    ),
                }
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )

        errors = _validate_seedance_control_json_prompt(seedance_json_ru, request)

        assert (
            "Seedance RU control prompt Shot 1 body does not appear translated into Russian."
            in errors
        )
        assert (
            "Seedance RU control prompt Shot 2 body does not appear translated into Russian."
            in errors
        )


def test_validate_seedance_control_json_prompt_accepts_russian_shot_bodies() -> None:
    with _temporary_test_root(f"video_prompt_seedance_ru_ok_{uuid4().hex}_") as root:
        regeneration_assets_dir = root / "regeneration_assets"
        regeneration_assets_dir.mkdir(parents=True, exist_ok=True)
        request = load_video_prompt_request(
            json.dumps(
                {
                    "technical_preamble": "Theme: travel story.",
                    "total_duration_seconds": 6,
                    "aspect_ratio": "16:9",
                    "regeneration_assets_dir": str(regeneration_assets_dir),
                    "references": [{"source_file": "source_frame.jpg", "tag": "@image1"}],
                    "scenes": [
                        {"duration_seconds": 3, "description": "The man @image1 walks."},
                        {"duration_seconds": 3, "description": "The man @image1 returns home."},
                    ],
                }
            )
        )
        seedance_json_ru = json.dumps(
            [
                {
                    "lang": "ru",
                    "prompt": (
                        "\u043c\u043e\u043d\u0442\u0430\u0436, \u0434\u043e\u0440\u043e\u0433\u0430. "
                        "Shot 1: \u041c\u0443\u0436\u0447\u0438\u043d\u0430 @image1 \u0438\u0434\u0435\u0442 \u043f\u043e "
                        "\u0433\u043e\u0440\u0430\u043c. "
                        "Shot 2: \u041c\u0443\u0436\u0447\u0438\u043d\u0430 @image1 \u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0430\u0435\u0442\u0441\u044f "
                        "\u0434\u043e\u043c\u043e\u0439. "
                        "Total: 6s / 2 shots / 16:9"
                    ),
                }
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )

        errors = _validate_seedance_control_json_prompt(seedance_json_ru, request)

        assert errors == []


def test_validate_seedance_json_prompt_rejects_excessive_distance_viewpoints() -> None:
    with _temporary_test_root(f"video_prompt_seedance_distance_{uuid4().hex}_") as root:
        regeneration_assets_dir = root / "regeneration_assets"
        regeneration_assets_dir.mkdir(parents=True, exist_ok=True)
        request = load_video_prompt_request(
            json.dumps(
                {
                    "technical_preamble": "Theme: travel story.",
                    "total_duration_seconds": 3,
                    "aspect_ratio": "16:9",
                    "regeneration_assets_dir": str(regeneration_assets_dir),
                    "references": [{"source_file": "source_frame.jpg", "tag": "@image1"}],
                    "scenes": [
                        {"duration_seconds": 3, "description": "The man @image1 walks in the mountains."},
                    ],
                }
            )
        )
        seedance_json_en = json.dumps(
            [
                {
                    "lang": "en",
                    "prompt": (
                        "montage, multi-shot action Hollywood movie, Don't use one camera angle or single cut, "
                        "cinematic lighting, photorealistic, 35mm film quality, professional color grading, "
                        "sharp focus, high detail texture, film grain, depth of field mastery, ARRI ALEXA aesthetic. "
                        "Shot 1: Wide aerial establishing shot, stabilized drone pulls high above the man @image1 as he "
                        "becomes a tiny figure in the mountains. Total: 3s / 1 shots / 16:9"
                    ),
                }
            ],
            separators=(",", ":"),
        )

        errors = _validate_seedance_json_prompt(seedance_json_en, request)

        assert any("excessively distant viewpoint" in error for error in errors)


def test_validate_generated_prompt_bundle_rejects_excessive_distance_viewpoints() -> None:
    with _temporary_test_root(f"video_prompt_bundle_distance_{uuid4().hex}_") as root:
        regeneration_assets_dir = root / "regeneration_assets"
        regeneration_assets_dir.mkdir(parents=True, exist_ok=True)
        request = load_video_prompt_request(
            json.dumps(
                {
                    "technical_preamble": "Theme: travel story.",
                    "total_duration_seconds": 3,
                    "aspect_ratio": "16:9",
                    "regeneration_assets_dir": str(regeneration_assets_dir),
                    "references": [{"source_file": "source_frame.jpg", "tag": "@image1"}],
                    "scenes": [
                        {"duration_seconds": 3, "description": "The man @image1 walks in the mountains."},
                    ],
                }
            )
        )
        bundle = GeneratedVideoPromptBundle(
            video_prompt_en=(
                "Shot 1: (0-3s / 3s, 3s total, 16:9) Wide aerial shot from far above, the man @image1 is a tiny "
                "figure in the landscape."
            ),
            video_prompt_ru=(
                "Shot 1: (0-3s / 3s, 3s total, 16:9) Мужчина @image1 идет по горной тропе."
            ),
        )

        errors = _validate_generated_prompt_bundle(bundle, request)

        assert any("video_prompt_en uses an excessively distant viewpoint" in error for error in errors)
