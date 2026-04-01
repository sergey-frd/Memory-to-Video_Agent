import json
from pathlib import Path

import main_change_impact

from utils.change_impact import build_impact_report


def test_build_impact_report_for_generation_flag_uses_registry() -> None:
    report = build_impact_report(
        Path("project_structure_registry.json"),
        change_type_ids=["generation_flag"],
        changed_files=["config.py", "utils/prompt_builder.py"],
        project_root=Path("."),
    )

    assert [item.id for item in report.selected_change_types] == ["generation_flag"]
    assert "config.py" in report.files_to_touch
    assert "USER_GUIDE.md" in report.files_to_touch
    assert "test/test_config_cli_defaults.py" in report.tests_to_run
    assert "PROJECT_STRUCTURE.md" in report.documents_to_review
    assert {item.id for item in report.matched_subsystems} >= {"config", "prompt_generation"}


def test_build_impact_report_can_infer_change_type_from_changed_file() -> None:
    report = build_impact_report(
        Path("project_structure_registry.json"),
        changed_files=["models/video_sequence.py"],
        project_root=Path("."),
    )

    assert "sequence_optimizer" in [item.id for item in report.selected_change_types]
    assert "test/test_sequence_optimizer_app.py" in report.tests_to_run
    assert any("автоматически" in note for note in report.notes)


def test_main_change_impact_prints_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "main_change_impact.py",
            "--change-type",
            "grok_runtime",
            "--changed-file",
            "main_grok_web.py",
            "--json",
        ],
    )

    main_change_impact.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_change_types"][0]["id"] == "grok_runtime"
    assert "main_grok_web.py" in payload["changed_files"]
