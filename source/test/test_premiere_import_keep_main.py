from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from main_premiere_import_keep import main, try_run_premiere_import_keep


def test_try_run_premiere_import_keep_skips_trim_review_payload() -> None:
    config_path = Path("missing.json")
    assert try_run_premiere_import_keep(config_path, {"engines": ["heuristic"]}) is None
    assert try_run_premiere_import_keep(config_path, {"mode": "report_replay"}) is None
    assert try_run_premiere_import_keep(config_path, {"project_path": r"<LOCAL_PATH>"}) is None


def test_main_premiere_import_keep_rejects_unsupported_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "trim.json"
    config_path.write_text(json.dumps({"engines": ["heuristic"]}), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["main_premiere_import_keep.py", "--config", str(config_path)],
    )
    with pytest.raises(ValueError, match="Unsupported mode"):
        main()


def test_main_premiere_import_keep_rejects_invalid_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "broken.json"
    config_path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["main_premiere_import_keep.py", "--config", str(config_path)],
    )
    with pytest.raises(ValueError, match="Invalid JSON"):
        main()
