import json
from pathlib import Path
from uuid import uuid4

import main_project_publication_push
import pytest
import utils.project_publication_push as publication_push_module

from utils.project_publication import DOC_TARGETS
from utils.project_publication_push import (
    DEFAULT_PUBLICATION_REMOTE,
    PublicationPushResult,
    prepare_publication_push,
)


def _write_required_docs(root: Path) -> None:
    for source_name in DOC_TARGETS:
        title = Path(source_name).stem
        (root / source_name).write_text(f"# {title}\n", encoding="utf-8")


def _fake_openai_project_key() -> str:
    return "sk-proj-" + ("a" * 32)


def _make_source_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "api").mkdir(parents=True, exist_ok=True)
    (root / "utils").mkdir(parents=True, exist_ok=True)
    (root / "services").mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "config.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "config.json").write_text('{"key": "value"}\n', encoding="utf-8")
    (root / "api" / "sample.py").write_text("API_VALUE = 1\n", encoding="utf-8")
    (root / "utils" / "sample.py").write_text("UTIL_VALUE = 2\n", encoding="utf-8")
    (root / "services" / "reference.txt").write_text("service\n", encoding="utf-8")
    (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    _write_required_docs(root)
    (root / ".env").write_text(f"OPENAI_API_KEY={_fake_openai_project_key()}\n", encoding="utf-8")
    (root / "project_structure_registry.json").write_text(
        json.dumps(
            {
                "project": "sample",
                "canonical_docs": ["PROJECT_STRUCTURE.md", "USER_GUIDE.md"],
                "core_invariants": ["inv"],
                "subsystems": [],
                "change_types": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_prepare_publication_push_stages_managed_files_and_removes_stale_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path("test_runtime") / f"publication_push_{uuid4().hex}"
    source_root = root / "source"
    repo_dir = root / "repo"
    _make_source_project(source_root)

    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir()

    data_dir = repo_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    stale_path = repo_dir / "old.txt"
    stale_path.write_text("obsolete\n", encoding="utf-8")
    (data_dir / "publication_manifest.json").write_text(
        json.dumps(
            {
                "managed_files": ["old.txt"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    staged_payload: dict[str, list[str]] = {}

    def fake_stage(repo_dir: Path, relpaths: list[str]) -> list[str]:
        staged_payload["relpaths"] = sorted(relpaths)
        return sorted(relpaths)

    monkeypatch.setattr(publication_push_module, "verify_expected_remote", lambda *_args, **_kwargs: DEFAULT_PUBLICATION_REMOTE)
    monkeypatch.setattr(publication_push_module, "remove_stale_managed_files", lambda *_args, **_kwargs: ["old.txt"])
    monkeypatch.setattr(publication_push_module, "stage_publication_files", fake_stage)
    monkeypatch.setattr(publication_push_module, "_current_branch", lambda _repo_dir: "main")

    result = prepare_publication_push(
        source_root=source_root,
        repo_dir=repo_dir,
        stage=True,
    )

    assert (repo_dir / ".gitignore").exists()
    assert (repo_dir / "PUBLISHING.md").exists()
    assert "old.txt" in result.removed_stale_files
    assert ".gitignore" in result.managed_files
    assert "PUBLISHING.md" in result.managed_files
    assert ".gitignore" in staged_payload["relpaths"]
    assert "PUBLISHING.md" in staged_payload["relpaths"]
    assert "README.md" in staged_payload["relpaths"]
    assert "source/main.py" in staged_payload["relpaths"]
    assert "source/api/sample.py" in staged_payload["relpaths"]
    assert "source/utils/sample.py" in staged_payload["relpaths"]
    assert "source/services/reference.txt" in staged_payload["relpaths"]
    assert "source/.env" not in staged_payload["relpaths"]
    assert "old.txt" in staged_payload["relpaths"]


def test_prepare_publication_push_rejects_remote_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path("test_runtime") / f"publication_push_{uuid4().hex}"
    source_root = root / "source"
    repo_dir = root / "repo"
    _make_source_project(source_root)

    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir()
    monkeypatch.setattr(
        publication_push_module,
        "get_remote_url",
        lambda *_args, **_kwargs: "https://github.com/sergey-frd/another-repo.git",
    )

    try:
        prepare_publication_push(
            source_root=source_root,
            repo_dir=repo_dir,
        )
    except ValueError as exc:
        assert "expected" in str(exc)
    else:
        raise AssertionError("Expected remote verification to reject a mismatched repository")


def test_main_project_publication_push_prints_json(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "main_project_publication_push.py",
            "--repo-dir",
            "E:/Git/Memory-to-Video_Agent",
            "--source-root",
            ".",
            "--json",
        ],
    )
    monkeypatch.setattr(
        main_project_publication_push,
        "prepare_publication_push",
        lambda **_kwargs: PublicationPushResult(
            repo_dir="E:/Git/Memory-to-Video_Agent",
            remote_url=DEFAULT_PUBLICATION_REMOTE,
            branch="main",
            managed_files=["README.md"],
            removed_stale_files=[],
            staged_files=[],
            committed=False,
            pushed=False,
        ),
    )

    main_project_publication_push.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["remote_url"] == DEFAULT_PUBLICATION_REMOTE
    assert payload["pushed"] is False
