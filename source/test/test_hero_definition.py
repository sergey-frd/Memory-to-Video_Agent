from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from PIL import Image

from api.openai_hero_definition import _extract_json_object
from utils.hero_definition import run_hero_definition_from_config


def _definition() -> dict[str, object]:
    return {
        "hero_name": "Алиса",
        "visual_summary": "Описание",
        "stable_visual_features": {"hair": "каштановые волосы"},
        "appearance_variations": [],
        "strong_identity_evidence": ["несколько согласующихся черт лица"],
        "supporting_identity_evidence": [],
        "do_not_use_as_identity_evidence": ["одежда"],
        "high_confidence_rule": "Совпадают несколько устойчивых черт.",
        "medium_confidence_rule": "Есть сходство, но лицо видно не полностью.",
        "uncertainties": [],
    }


def test_extract_json_object_accepts_fenced_response() -> None:
    assert _extract_json_object('```json\n{"hero_name": "Алиса"}\n```') == {"hero_name": "Алиса"}


def test_run_hero_definition_from_config_writes_reproducible_sources() -> None:
    root = Path("test_runtime") / f"hero_definition_{uuid4().hex}"
    image_dir = root / "hero"
    reports_dir = root / "reports"
    image_dir.mkdir(parents=True)
    Image.new("RGB", (20, 20), "red").save(image_dir / "b.jpg")
    Image.new("RGB", (20, 20), "blue").save(image_dir / "a.png")
    detail_path = root / "detail.txt"
    detail_path.write_text("Алиса — главный герой.", encoding="utf-8")
    config_path = root / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "hero_name": "Алиса",
                "hero_image_dir": str(image_dir),
                "human_detail_txt": str(detail_path),
                "reports_dir": str(reports_dir),
                "model": "test-model",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    received: dict[str, object] = {}

    def fake_analyzer(**kwargs: object) -> dict[str, object]:
        received.update(kwargs)
        return _definition()

    output_path = run_hero_definition_from_config(config_path, analyzer=fake_analyzer)

    assert output_path == reports_dir / "hero_def.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["generator"]["model"] == "test-model"
    assert payload["sources"]["reference_image_count"] == 2
    assert [item["name"] for item in payload["sources"]["reference_images"]] == ["a.png", "b.jpg"]
    assert all(len(item["sha256"]) == 64 for item in payload["sources"]["reference_images"])
    assert received["hero_name"] == "Алиса"
    assert received["human_detail_text"] == "Алиса — главный герой."
