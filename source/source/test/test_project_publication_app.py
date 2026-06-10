import json
from pathlib import Path
from uuid import uuid4

import main_project_publication

from utils.project_publication import DOC_TARGETS, PublicationResult, write_publication_bundle


def _write_required_docs(root: Path) -> None:
    for source_name in DOC_TARGETS:
        title = Path(source_name).stem
        (root / source_name).write_text(f"# {title}\n", encoding="utf-8")


def _fake_openai_project_key() -> str:
    return "sk-proj-" + ("a" * 32)


def test_write_publication_bundle_creates_full_safe_source_mirror() -> None:
    root = Path("test_runtime") / f"publication_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "api").mkdir()
    (root / "utils").mkdir()
    (root / "utils" / "__pycache__").mkdir(parents=True)
    (root / "models").mkdir()
    (root / "services").mkdir()
    (root / "styles").mkdir()
    (root / "test").mkdir()
    (root / "tests").mkdir()
    (root / "project_publication").mkdir()
    (root / "config.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "config.json").write_text('{"key": "value"}\n', encoding="utf-8")
    (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "run_sample.bat").write_text("@echo off\n", encoding="utf-8")
    _write_required_docs(root)
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "api" / "x.py").write_text("x = 1\n", encoding="utf-8")
    (root / "utils" / "y.py").write_text("y = 2\n", encoding="utf-8")
    (root / "utils" / "__pycache__" / "skip.pyc").write_bytes(b"pyc")
    (root / "models" / "z.py").write_text("z = 3\n", encoding="utf-8")
    (root / "services" / "prompt.txt").write_text("service\n", encoding="utf-8")
    (root / "styles" / "styles.txt").write_text("style\n", encoding="utf-8")
    (root / "test" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (root / "tests" / "test_extra.py").write_text("def test_extra():\n    assert True\n", encoding="utf-8")
    (root / "project_publication" / "should_skip.txt").write_text("skip\n", encoding="utf-8")
    (root / ".env").write_text(f"OPENAI_API_KEY={_fake_openai_project_key()}\n", encoding="utf-8")
    (root / "Gemini_Generated_Image_test.jpg").write_bytes(b"jpg")
    (root / "project_structure_registry.json").write_text(
        json.dumps(
            {
                "project": "sample",
                "canonical_docs": ["PROJECT_STRUCTURE.md"],
                "core_invariants": ["inv"],
                "subsystems": [{"id": "config", "purpose": "cfg", "files": ["config.py"], "tests": []}],
                "change_types": [
                    {
                        "id": "generation_flag",
                        "description": "desc",
                        "must_touch": ["config.py"],
                        "must_review": ["main.py"],
                        "minimum_checks": ["check"],
                        "recommended_tests": ["test/test_x.py"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    target = root / "out_repo"
    result = write_publication_bundle(root, target)

    assert (target / "README.md").exists()
    assert (target / "VERSION").exists()
    assert (target / ".gitignore").exists()
    assert (target / "PUBLISHING.md").exists()
    assert (target / "source" / "main.py").exists()
    assert (target / "source" / "config.py").exists()
    assert (target / "source" / "config.json").exists()
    assert (target / "source" / "api" / "x.py").exists()
    assert (target / "source" / "utils" / "y.py").exists()
    assert (target / "source" / "models" / "z.py").exists()
    assert (target / "source" / "services" / "prompt.txt").exists()
    assert (target / "source" / "styles" / "styles.txt").exists()
    assert (target / "source" / "test" / "test_x.py").exists()
    assert (target / "source" / "tests" / "test_extra.py").exists()
    assert (target / "source" / "run_sample.bat").exists()
    assert (target / "source" / "requirements.txt").exists()
    assert (target / "source" / "pytest.ini").exists()
    assert not (target / "source" / ".env").exists()
    assert not (target / "source" / "Gemini_Generated_Image_test.jpg").exists()
    assert not (target / "source" / "utils" / "__pycache__" / "skip.pyc").exists()
    assert not (target / "source" / "project_publication" / "should_skip.txt").exists()
    assert (target / "docs" / "PROJECT_STRUCTURE.md").exists()
    assert (target / "docs" / "USER_GUIDE_EN.md").exists()
    assert (target / "docs" / "USER_GUIDE_RU.md").exists()
    assert (target / "docs" / "PROJECT_OVERVIEW.md").exists()
    assert (target / "docs" / "CHANGE_IMPACT.md").exists()
    assert (target / "data" / "project_snapshot.json").exists()
    assert (target / "data" / "project_structure_registry.json").exists()
    assert (target / "data" / "publication_manifest.json").exists()
    assert "README.md" in result.written_files

    snapshot = json.loads((target / "data" / "project_snapshot.json").read_text(encoding="utf-8"))
    manifest = json.loads((target / "data" / "publication_manifest.json").read_text(encoding="utf-8"))
    version_text = (target / "VERSION").read_text(encoding="utf-8").strip()
    assert "source_root" not in snapshot
    assert "source_root" not in manifest
    assert snapshot["source_workspace"] == root.name
    assert result.publication_version == version_text
    assert result.git_tag == f"v{version_text}"
    assert snapshot["publication_version"] == version_text
    assert snapshot["publication_git_tag"] == f"v{version_text}"
    assert snapshot["publication_signature"]
    assert manifest["publication_version"] == version_text
    assert manifest["publication_git_tag"] == f"v{version_text}"
    assert "VERSION" in manifest["managed_files"]
    assert "source/api/x.py" in manifest["managed_files"]
    assert "source/utils/y.py" in manifest["managed_files"]
    assert "source/models/z.py" in manifest["managed_files"]
    assert "source/services/prompt.txt" in manifest["managed_files"]
    assert "source/styles/styles.txt" in manifest["managed_files"]
    assert "source/.env" not in manifest["managed_files"]
    assert "source/Gemini_Generated_Image_test.jpg" not in manifest["managed_files"]
    assert "source/project_publication/should_skip.txt" not in manifest["managed_files"]
    assert "source/utils/__pycache__/skip.pyc" not in manifest["managed_files"]


def test_write_publication_bundle_rejects_secret_like_content() -> None:
    root = Path("test_runtime") / f"publication_secret_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "config.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "config.json").write_text('{"key": "value"}\n', encoding="utf-8")
    (root / "PROJECT_STRUCTURE.md").write_text(f"token {_fake_openai_project_key()}\n", encoding="utf-8")
    for source_name in DOC_TARGETS:
        if source_name == "PROJECT_STRUCTURE.md":
            continue
        (root / source_name).write_text(f"# {Path(source_name).stem}\n", encoding="utf-8")
    (root / "project_structure_registry.json").write_text(
        json.dumps(
            {
                "project": "sample",
                "canonical_docs": ["PROJECT_STRUCTURE.md"],
                "core_invariants": [],
                "subsystems": [],
                "change_types": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    try:
        write_publication_bundle(root, root / "out_repo")
    except ValueError as exc:
        assert "Secret-like content detected" in str(exc)
    else:
        raise AssertionError("Expected publication bundle generation to reject secret-like content")


def test_write_publication_bundle_sanitizes_paths_inside_source_files() -> None:
    root = Path("test_runtime") / f"publication_paths_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "api").mkdir()
    (root / "utils").mkdir()
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "config.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "config.json").write_text('{"key": "value"}\n', encoding="utf-8")
    _write_required_docs(root)
    (root / "api" / "paths.py").write_text('SOURCE = "<LOCAL_PATH>"\n', encoding="utf-8")
    (root / "utils" / "paths.py").write_text("TARGET = <LOCAL_PATH> encoding="utf-8")
    (root / "project_structure_registry.json").write_text(
        json.dumps(
            {
                "project": "sample",
                "canonical_docs": ["PROJECT_STRUCTURE.md"],
                "core_invariants": [],
                "subsystems": [],
                "change_types": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    target = root / "out_repo"
    write_publication_bundle(root, target)

    api_text = (target / "source" / "api" / "paths.py").read_text(encoding="utf-8")
    utils_text = (target / "source" / "utils" / "paths.py").read_text(encoding="utf-8")
    assert "<LOCAL_PATH>" in api_text
    assert "<LOCAL_PATH>" in utils_text
    assert "<LOCAL_PATH>" not in api_text
    assert "<LOCAL_PATH>" not in utils_text


def test_main_project_publication_prints_json(monkeypatch, capsys) -> None:
    target = Path("test_runtime") / f"publication_cli_{uuid4().hex}" / "repo"
    monkeypatch.setattr(
        "sys.argv",
        [
            "main_project_publication.py",
            "--source-root",
            ".",
            "--target-dir",
            str(target),
            "--json",
        ],
    )
    monkeypatch.setattr(
        main_project_publication,
        "write_publication_bundle",
        lambda **_kwargs: PublicationResult(
            target_dir=str(target),
            manifest_path=str(target / "data" / "publication_manifest.json"),
            snapshot_path=str(target / "data" / "project_snapshot.json"),
            publication_version="2026.04.01.01",
            git_tag="v2026.04.01.01",
            written_files=["README.md", "source/main.py"],
        ),
    )

    main_project_publication.main()

    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["target_dir"]).name == "repo"
    assert payload["manifest_path"].endswith("publication_manifest.json")
    assert payload["publication_version"] == "2026.04.01.01"
