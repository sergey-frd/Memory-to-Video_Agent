from pathlib import Path
import main_verify_installation as verify
import subprocess
from utils.project_publication import _is_publishable_source_file, _publication_gitignore


def test_publication_includes_installation_files_but_not_local_settings(tmp_path):
    for name in ["config.local.json", "art_task_032.local.json", ".env", ".env.backup"]:
        assert not _is_publishable_source_file(tmp_path/name, tmp_path), name
    for name in [".env.template", ".gitignore", "task032_native_finish.jsx", "requirements-lock-windows-py314.txt", "install_project.bat"]:
        assert _is_publishable_source_file(tmp_path/name, tmp_path), name


def test_dependency_mismatch_is_reported(tmp_path, monkeypatch):
    lock = tmp_path / "lock.txt"
    lock.write_text("package==2.0\n", encoding="utf8")
    monkeypatch.setattr(verify.importlib.metadata, "version", lambda _: "1.0")
    assert verify.package_differences(lock) == ["package: expected 2.0, found 1.0"]


def test_failed_git_check_cannot_claim_exact_release(tmp_path, monkeypatch, capsys):
    (tmp_path/"VERSION").write_text("2026.08.31.01\n",encoding="utf8")
    (tmp_path/"requirements-lock-windows-py314.txt").write_text("",encoding="utf8")
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(verify, "git", lambda *args: "")
    assert verify.main(["--require-tag", "--json"]) == 1
    assert "exactly at v2026.08.31.01" in capsys.readouterr().out


def test_installer_stops_on_failed_interpreter_without_creating_environment(tmp_path):
    source = Path(__file__).resolve().parents[1] / "setup_project.ps1"
    installer = tmp_path / source.name
    installer.write_bytes(source.read_bytes())
    failing_python = tmp_path / "failed-python.cmd"
    failing_python.write_text("@echo off\nexit /b 37\n", encoding="ascii")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer), "-PythonExe", str(failing_python)], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "exit 37" in result.stderr
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / ".env").exists()
