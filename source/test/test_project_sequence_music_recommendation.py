from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from utils.project_sequence_music_recommendation import (
    load_project_sequence_music_recommendation_config,
    run_project_sequence_music_recommendation_from_config,
)


def _build_config_tree(*, reports_dir_as_file: bool = False) -> tuple[Path, Path]:
    root = (Path("test_runtime") / f"music_recommendation_{uuid4().hex}").resolve()
    root.mkdir(parents=True)
    project_path = root / "Alice.prproj"
    project_path.write_text("sample", encoding="utf-8")
    human_detail = root / "Alice_detail.txt"
    human_detail.write_text("Алиса любит путешествия и легкую популярную музыку.", encoding="utf-8")
    reports_dir = root / "reports"

    project_config = root / "config_Alice.json"
    project_config.write_text(
        json.dumps(
            {
                "human_detail_txt": str(human_detail),
                "reports_dir": str(reports_dir),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    hero_definition = reports_dir / "hero_def.json"
    reports_dir.mkdir()
    hero_definition.write_text(
        json.dumps(
            {
                "sources": {
                    "human_detail_txt": str(human_detail),
                    "human_detail_sha256": hashlib.sha256(human_detail.read_bytes()).hexdigest(),
                },
                "definition": {"hero_name": "Алиса"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sequence_config = root / "sequence_trim_review_Alice_1.json"
    sequence_config.write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "source_sequence_name": "Alice_e04",
                "hero_definition_path": str(hero_definition),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    music_config = root / "sequence_music_recommendation_Alice.json"
    music_config.write_text(
        json.dumps(
            {
                "project_config_path": str(project_config),
                "sequence_config_path": str(sequence_config),
                "hero_definition_path": str(hero_definition),
                "reports_dir": str(hero_definition if reports_dir_as_file else reports_dir),
                "project_path": str(project_path),
                "source_sequence_name": "Alice_e04",
                "max_sampled_clips": 5,
                "scene_model": "gpt-4.1-mini",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return music_config, reports_dir


def test_load_music_recommendation_config_combines_project_sequence_and_hero() -> None:
    config_path, reports_dir = _build_config_tree()

    config = load_project_sequence_music_recommendation_config(config_path)

    assert config.project_path.name == "Alice.prproj"
    assert config.source_sequence_name == "Alice_e04"
    assert config.reports_dir == reports_dir
    assert config.human_detail_txt.name == "Alice_detail.txt"
    assert config.max_sampled_clips == 5
    assert config.output_personalized_music_txt.name.endswith("_personalized_music.txt")


def test_load_music_recommendation_config_rejects_hero_json_as_reports_dir() -> None:
    config_path, _reports_dir = _build_config_tree(reports_dir_as_file=True)

    with pytest.raises(ValueError, match="reports_dir must point to a directory"):
        load_project_sequence_music_recommendation_config(config_path)


def test_run_music_recommendation_writes_personalized_report_and_context() -> None:
    config_path, _reports_dir = _build_config_tree()
    progress_messages: list[str] = []

    def fake_bundle_writer(**kwargs):
        kwargs["progress_reporter"]("fake scene analysis")
        output_json = kwargs["output_json"]
        output_music = kwargs["output_music_txt"]
        output_json.write_text('{"mode": "project_sequence_music_first"}', encoding="utf-8")
        output_music.write_text("video-only music", encoding="utf-8")
        return output_json, output_music, None, None

    def fake_personalized_writer(**kwargs):
        output_path = kwargs["output_path"]
        output_path.write_text("personalized music", encoding="utf-8")
        return output_path

    result = run_project_sequence_music_recommendation_from_config(
        config_path,
        progress_reporter=progress_messages.append,
        bundle_writer=fake_bundle_writer,
        personalized_writer=fake_personalized_writer,
    )

    payload = json.loads(result.output_json.read_text(encoding="utf-8"))
    context = payload["personalized_music_context"]
    assert result.output_music_txt.read_text(encoding="utf-8") == "video-only music"
    assert result.output_personalized_music_txt.read_text(encoding="utf-8") == "personalized music"
    assert context["hero_definition_path"].endswith("hero_def.json")
    assert context["personalized_music_report"] == str(result.output_personalized_music_txt)
    assert "fake scene analysis" in progress_messages
