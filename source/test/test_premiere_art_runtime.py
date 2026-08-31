from pathlib import Path
import importlib
import json
import os
import subprocess
import sys

import pytest
from utils.premiere_art_runtime import load_profile, assert_fresh_outputs, configure_module
from tools.prepare_task032_native import prepare

ROOT = Path(__file__).resolve().parents[1]


def profile(tmp_path, task="032"):
    content = json.loads((ROOT / f"examples/premiere/art_task_{task}.example.json").read_text(encoding="utf8"))
    path = tmp_path / "settings.local.json"
    path.write_text(json.dumps(content), encoding="utf8")
    return path, content


def test_paths_resolve_from_config_not_process_directory(tmp_path, monkeypatch):
    path, _ = profile(tmp_path)
    before = load_profile(path, "032")
    monkeypatch.chdir(ROOT / "test")
    assert load_profile(path, "032") == before
    assert all(before[k].is_absolute() for k in ["SOURCE", "DEST", "OUT"])


@pytest.mark.parametrize("damage", ["unknown", "missing", "alias", "directory_alias", "task", "checksum"])
def test_invalid_configuration_is_blocked(tmp_path, damage):
    path, payload = profile(tmp_path)
    settings = payload["settings"]
    if damage == "unknown": settings["RUN_SHELL"] = "x"
    if damage == "missing": settings.pop("DEST")
    if damage == "alias": settings["DEST"] = settings["SOURCE"]
    if damage == "directory_alias": settings["REPORT_DIR"] = settings["OUT"]
    if damage == "task": payload["task"] = "031"
    if damage == "checksum": settings["SHA"] = "invalid"
    path.write_text(json.dumps(payload), encoding="utf8")
    with pytest.raises(ValueError): load_profile(path, "032")


def test_existing_result_is_never_overwritten(tmp_path):
    path, payload = profile(tmp_path, "033")
    for key in ["OUTPUT_PROJECT", "BACKUP_PROJECT", "COLOR_PREVIEW", "FINAL_PREVIEW", "COMPARISON_PREVIEW", "REPO_DIR", "LOCAL_DIR"]:
        payload["settings"][key] = str(tmp_path / key)
    path.write_text(json.dumps(payload), encoding="utf8")
    settings = load_profile(path, "033")
    settings["OUTPUT_PROJECT"].write_bytes(b"prior project")
    with pytest.raises(FileExistsError): assert_fresh_outputs(settings, "033")
    assert settings["OUTPUT_PROJECT"].read_bytes() == b"prior project"


def test_task034_uses_only_configured_source(tmp_path, monkeypatch):
    path, _ = profile(tmp_path, "034")
    monkeypatch.setenv("PREMIERE_ART_CONFIG", str(path))
    namespace = {"SOURCE_CANDIDATES": [Path("old-machine")], "OUTPUT_PROJECT": None}
    configure_module(namespace, "034")
    assert namespace["SOURCE_CANDIDATES"] == [load_profile(path, "034")["SOURCE_PROJECT"]]


def test_prepare_native_is_idempotent_but_does_not_replace_edits(tmp_path):
    settings = {"OUT": tmp_path / "native", "SOURCE": tmp_path / "source.prproj", "NAME": "source"}
    result = prepare(settings)
    assert prepare(settings) == result
    target = settings["OUT"] / "task032_native_finish.jsx"
    target.write_text("user edit", encoding="utf8")
    with pytest.raises(FileExistsError): prepare(settings)
    assert target.read_text(encoding="utf8") == "user edit"


def test_new_modules_import_without_task_io(tmp_path):
    modules = [p.stem for p in ROOT.glob("main_premiere_task_03*.py")]
    modules += ["tools." + p.stem for p in (ROOT / "tools").glob("task032_*.py")]
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    env.pop("PREMIERE_ART_CONFIG", None)
    code = "import importlib; [importlib.import_module(n) for n in " + repr(modules) + "]"
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("program", ["main_premiere_task_031_art_final.py", "main_premiere_task_033_fit_pulse_fill.py", "main_premiere_task_034_single_soft_impulse.py", "tools/task032_pipeline.py", "tools/task032_color_safety_revision.py"])
def test_help_is_read_only(program, tmp_path):
    result = subprocess.run([sys.executable, "-B", str(ROOT / program), "--help"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "--config" in result.stdout
    assert not list(tmp_path.iterdir())


def test_mutation_flag_required_before_input_is_opened(tmp_path):
    path, _ = profile(tmp_path, "033")
    result = subprocess.run([sys.executable, "-B", str(ROOT / "main_premiere_art_task.py"), "--task", "033", "--config", str(path), "--stage", "run"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "require --execute" in result.stderr
