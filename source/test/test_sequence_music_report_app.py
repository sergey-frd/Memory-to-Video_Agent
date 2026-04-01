from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import main_sequence_reports
import utils.current_sequence_reports as current_reports_module

from models.video_sequence import (
    ClipAssetBundle,
    PremiereSequenceClip,
    SequenceCandidate,
    SequenceOptimizationResult,
    SequenceRecommendationEntry,
)
from utils.current_sequence_reports import (
    derive_current_sequence_music_report_path,
    derive_current_sequence_report_bundle_paths,
    write_current_sequence_report_bundle,
)
from utils.sequence_structure_report import build_sequence_music_report


def _make_entry(
    *,
    index: int,
    name: str,
    stage_id: str,
    summary: str,
    background: str,
    main_action: str,
    mood: list[str],
    relationships: list[str],
    keywords: list[str],
    people_count: int = 2,
    shot_scale: int = 1,
    energy_level: int = 1,
) -> SequenceRecommendationEntry:
    clip = PremiereSequenceClip(
        sequence_name="MainProjectSequence",
        order_index=index,
        track_index=3,
        clipitem_id=f"clip-{index}",
        name=name,
        source_path=name,
        start=(index - 1) * 100,
        end=index * 100,
        in_point=0,
        out_point=100,
        duration=100,
        stage_id=stage_id,
        video_index=1,
    )
    assets = ClipAssetBundle(
        stage_id=stage_id,
        bundle_dir=f"bundle/{stage_id}",
        scene_analysis={
            "summary": summary,
            "background": background,
            "shot_type": "medium shot",
            "main_action": main_action,
            "mood": mood,
            "relationships": relationships,
        },
        prompt_text="warm family travel montage",
    )
    candidate = SequenceCandidate(
        clip=clip,
        assets=assets,
        keywords=keywords,
        people_count=people_count,
        shot_scale=shot_scale,
        energy_level=energy_level,
        series_subject_tokens=["семья"],
        series_appearance_tokens=["улица"],
        series_pose_tokens=["walk"],
        main_character_notes=["youngest child anchor"],
        continuity_notes=["same family in travel context"],
    )
    return SequenceRecommendationEntry(
        recommended_index=index,
        original_index=index,
        score=1.0,
        reason="Current manual order from the Premiere sequence.",
        candidate=candidate,
    )


def _make_result() -> SequenceOptimizationResult:
    entries = [
        _make_entry(
            index=1,
            name="family_trip_start_20260401_video_1.mp4",
            stage_id="family_trip_start_20260401",
            summary="Семья начинает прогулку в новом месте и улыбается.",
            background="Терраса и открытый городской вид.",
            main_action="walking together",
            mood=["тепло", "радость"],
            relationships=["семья"],
            keywords=["travel", "family", "warm"],
        ),
        _make_entry(
            index=2,
            name="family_trip_view_20260401_video_1.mp4",
            stage_id="family_trip_view_20260401",
            summary="Семья смотрит на панораму и продолжает маршрут.",
            background="Панорамный вид и прогулочная зона.",
            main_action="looking at the view",
            mood=["светло", "спокойно"],
            relationships=["семья"],
            keywords=["travel", "scenic", "family"],
        ),
    ]
    return SequenceOptimizationResult(
        source_xml="project.prproj",
        selected_sequence_name="MainProjectSequence",
        engine_requested="heuristic",
        engine_used="heuristic",
        warnings=[],
        entries=entries,
    )


def test_build_sequence_music_report_contains_music_first_sections() -> None:
    report = build_sequence_music_report(_make_result())

    assert "МУЗЫКАЛЬНАЯ РЕКОМЕНДАЦИЯ ДЛЯ SEQUENCE" in report
    assert "Главный музыкальный вектор" in report
    assert "Музыкальная драматургия по блокам" in report
    assert "Рекомендуемая музыка" in report
    assert "1. Не из мировой классической музыки" in report


def test_derive_current_sequence_report_bundle_paths_includes_music_file() -> None:
    root = Path("test_runtime") / f"sequence_music_paths_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    optimization_json = root / "sequence.json"
    optimization_json.write_text("{}", encoding="utf-8")

    output_json, output_music, output_structure, output_transition = derive_current_sequence_report_bundle_paths(
        sequence_name="My Main Sequence",
        optimization_report_json=optimization_json,
        output_dir=root,
    )

    assert output_json.name == "My_Main_Sequence_manual_order.json"
    assert output_music.name == "My_Main_Sequence_manual_order_music.txt"
    assert output_structure.name == "My_Main_Sequence_manual_order_structure.txt"
    assert output_transition.name == "My_Main_Sequence_manual_order_transition_recommendations.txt"
    assert derive_current_sequence_music_report_path(
        sequence_name="My Main Sequence",
        optimization_report_json=optimization_json,
        output_dir=root,
    ) == output_music


def test_write_current_sequence_report_bundle_can_generate_music_only(monkeypatch) -> None:
    root = Path("test_runtime") / f"sequence_music_bundle_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    result = _make_result()

    monkeypatch.setattr(
        current_reports_module,
        "build_current_sequence_result_from_report",
        lambda **_kwargs: result,
    )

    output_json = root / "manual_order.json"
    output_music = root / "manual_order_music.txt"
    written_json, written_music, written_structure, written_transition = write_current_sequence_report_bundle(
        project_path=Path("project.prproj"),
        sequence_name="MainProjectSequence",
        optimization_report_json=root / "sequence.json",
        output_json=output_json,
        output_music_txt=output_music,
        output_structure_txt=None,
        output_transition_txt=None,
        include_music=True,
        include_structure=False,
        include_transition=False,
    )

    assert written_json == output_json
    assert written_music == output_music
    assert written_structure is None
    assert written_transition is None
    assert "МУЗЫКАЛЬНАЯ РЕКОМЕНДАЦИЯ ДЛЯ SEQUENCE" in output_music.read_text(encoding="utf-8")


def test_main_sequence_reports_supports_music_only(monkeypatch, capsys) -> None:
    root = Path("test_runtime") / f"sequence_music_cli_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    output_json = root / "manual_order.json"
    output_music = root / "manual_order_music.txt"
    output_structure = root / "manual_order_structure.txt"
    output_transition = root / "manual_order_transition.txt"

    monkeypatch.setattr(
        "sys.argv",
        [
            "main_sequence_reports.py",
            "--prproj",
            "project.prproj",
            "--sequence-name",
            "MainProjectSequence",
            "--optimization-report-json",
            "sequence.json",
            "--music-only",
        ],
    )
    monkeypatch.setattr(
        main_sequence_reports,
        "derive_current_sequence_report_bundle_paths",
        lambda **_kwargs: (output_json, output_music, output_structure, output_transition),
    )

    def fake_write_bundle(**_kwargs):
        output_json.write_text("{}", encoding="utf-8")
        output_music.write_text("music", encoding="utf-8")
        return output_json, output_music, None, None

    monkeypatch.setattr(main_sequence_reports, "write_current_sequence_report_bundle", fake_write_bundle)

    main_sequence_reports.main()

    output = capsys.readouterr().out
    assert "Current-order JSON saved to:" in output
    assert "Current-order music recommendation report saved to:" in output
    assert "Structure and transition reports were skipped due to: --music-only" in output
