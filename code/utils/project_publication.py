from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


EXCLUDED_DIR_NAMES = {
    ".browser-profile",
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "cleanup_archive",
    "error",
    "final_project",
    "human_details_filtered",
    "input",
    "output",
    "test_runtime",
}

DOC_TARGETS = {
    "PROJECT_STRUCTURE.md": "docs/PROJECT_STRUCTURE.md",
    "USER_GUIDE.md": "docs/USER_GUIDE_EN.md",
    "Руководство_пользователя.md": "docs/USER_GUIDE_RU.md",
}
ROOT_CODE_TARGETS = {
    "main.py": "code/main.py",
    "config.py": "code/config.py",
    "config.json": "code/config.json",
}
CODE_DIR_TARGETS = ("api", "utils")
PUBLISHED_CODE_SUFFIXES = {".py"}

WINDOWS_QUOTED_PATH_RE = re.compile(r'"[A-Za-z]:\\[^"\n]+"')
WINDOWS_INLINE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z]:\\[^\s`]+)")
SECRET_PATTERNS = {
    "openai_project_key": re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "github_pat": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    "github_classic": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    "slack_token": re.compile(r"xox[baprs]-[0-9A-Za-z-]{20,}"),
    "private_key_rsa": re.compile(r"(?m)^-----BEGIN RSA PRIVATE KEY-----$"),
    "private_key_openssh": re.compile(r"(?m)^-----BEGIN OPENSSH PRIVATE KEY-----$"),
}


def _jerusalem_timezone():
    try:
        return ZoneInfo("Asia/Jerusalem")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=2), name="Asia/Jerusalem")


JERUSALEM_TZ = _jerusalem_timezone()


@dataclass
class PublicationResult:
    target_dir: str
    manifest_path: str
    snapshot_path: str
    written_files: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _iter_project_files(source_root: Path):
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        yield path


def _iter_published_code_targets(source_root: Path):
    for source_name, target_relpath in ROOT_CODE_TARGETS.items():
        yield source_root / source_name, target_relpath

    for dirname in CODE_DIR_TARGETS:
        base_dir = source_root / dirname
        if not base_dir.exists():
            continue
        for source_path in sorted(base_dir.rglob("*")):
            if not source_path.is_file():
                continue
            rel_parts = source_path.relative_to(source_root).parts
            if any(part in EXCLUDED_DIR_NAMES for part in rel_parts):
                continue
            if source_path.suffix not in PUBLISHED_CODE_SUFFIXES:
                continue
            target_relpath = Path("code") / source_path.relative_to(source_root)
            yield source_path, target_relpath.as_posix()


def _project_snapshot(source_root: Path, registry: dict[str, object]) -> dict[str, object]:
    files = list(_iter_project_files(source_root))
    relative_files = [path.relative_to(source_root).as_posix() for path in files]
    top_level = [path for path in files if path.parent == source_root]
    py_files = [path for path in relative_files if path.endswith(".py")]
    test_files = [path for path in relative_files if path.startswith("test/") or path.startswith("tests/")]
    config_files = [path for path in relative_files if Path(path).name.startswith("config") and path.endswith(".json")]
    markdown_files = [path for path in relative_files if path.endswith(".md")]
    entry_points = sorted(path for path in py_files if Path(path).name.startswith("main") and "/" not in path)

    def _child_files(dirname: str) -> list[str]:
        return sorted(path for path in relative_files if path.startswith(f"{dirname}/"))

    subsystems = registry.get("subsystems", [])
    change_types = registry.get("change_types", [])
    generated_at = datetime.now(JERUSALEM_TZ).isoformat(timespec="seconds")
    return {
        "generated_at": generated_at,
        "source_project": source_root.name,
        "source_workspace": source_root.name,
        "counts": {
            "all_files": len(relative_files),
            "top_level_files": len(top_level),
            "python_files": len(py_files),
            "test_files": len(test_files),
            "config_files": len(config_files),
            "markdown_files": len(markdown_files),
            "entry_points": len(entry_points),
            "api_modules": len(_child_files("api")),
            "utils_modules": len(_child_files("utils")),
            "model_modules": len(_child_files("models")),
        },
        "entry_points": entry_points,
        "key_dirs": {
            "api": _child_files("api"),
            "utils": _child_files("utils"),
            "models": _child_files("models"),
            "test": _child_files("test"),
            "tests": _child_files("tests"),
        },
        "docs": markdown_files,
        "configs": config_files,
        "subsystem_ids": [str(item.get("id", "")) for item in subsystems],
        "change_type_ids": [str(item.get("id", "")) for item in change_types],
    }


def _sanitize_public_text(text: str) -> str:
    text = WINDOWS_QUOTED_PATH_RE.sub('"<LOCAL_PATH>"', text)
    text = WINDOWS_INLINE_PATH_RE.sub("<LOCAL_PATH>", text)
    return text


def _secret_hits(text: str) -> list[str]:
    hits: list[str] = []
    for name, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            hits.append(name)
    return hits


def _overview_markdown(snapshot: dict[str, object], registry: dict[str, object]) -> str:
    counts = snapshot["counts"]
    entry_points = snapshot["entry_points"]
    subsystem_rows = [
        f"| `{item.get('id', '')}` | {item.get('purpose', '')} |"
        for item in registry.get("subsystems", [])
    ]
    change_rows = [
        f"| `{item.get('id', '')}` | {item.get('description', '')} |"
        for item in registry.get("change_types", [])
    ]
    lines = [
        "# Project Overview",
        "",
        "This document is generated from the source project and is intended for the external project-information repository.",
        "",
        f"- Generated at: `{snapshot['generated_at']}`",
        f"- Source project: `{snapshot['source_project']}`",
        "",
        "## Snapshot",
        "",
        f"- Files scanned: `{counts['all_files']}`",
        f"- Python files: `{counts['python_files']}`",
        f"- Test files: `{counts['test_files']}`",
        f"- Config JSON files: `{counts['config_files']}`",
        f"- Markdown docs: `{counts['markdown_files']}`",
        f"- Entry points: `{counts['entry_points']}`",
        f"- API modules: `{counts['api_modules']}`",
        f"- Utils modules: `{counts['utils_modules']}`",
        f"- Model modules: `{counts['model_modules']}`",
        "",
        "## Entry Points",
        "",
    ]
    if entry_points:
        lines.extend(f"- `{item}`" for item in entry_points)
    else:
        lines.append("- No entry points were detected.")
    lines.extend(
        [
            "",
            "## Subsystems",
            "",
            "| Id | Purpose |",
            "| --- | --- |",
            *subsystem_rows,
            "",
            "## Change Types",
            "",
            "| Id | Description |",
            "| --- | --- |",
            *change_rows,
        ]
    )
    return "\n".join(lines) + "\n"


def _change_impact_markdown(registry: dict[str, object]) -> str:
    lines = [
        "# Change Impact Guide",
        "",
        "This document is generated from `project_structure_registry.json` and helps operators understand what must be reviewed when the source project changes.",
        "",
        "## Core Invariants",
        "",
    ]
    lines.extend(f"- {item}" for item in registry.get("core_invariants", []))
    lines.extend(
        [
            "",
            "## Change Types",
            "",
        ]
    )
    for item in registry.get("change_types", []):
        lines.append(f"### `{item.get('id', '')}`")
        lines.append("")
        description = str(item.get("description", "")).strip()
        if description:
            lines.append(description)
            lines.append("")
        must_touch = [str(value) for value in item.get("must_touch", [])]
        must_review = [str(value) for value in item.get("must_review", [])]
        recommended_tests = [str(value) for value in item.get("recommended_tests", [])]
        minimum_checks = [str(value) for value in item.get("minimum_checks", [])]
        if must_touch:
            lines.append("Must update:")
            lines.extend(f"- `{value}`" for value in must_touch)
            lines.append("")
        if must_review:
            lines.append("Must review:")
            lines.extend(f"- `{value}`" for value in must_review)
            lines.append("")
        if recommended_tests:
            lines.append("Recommended tests:")
            lines.extend(f"- `{value}`" for value in recommended_tests)
            lines.append("")
        if minimum_checks:
            lines.append("Minimum checks:")
            lines.extend(f"- {value}" for value in minimum_checks)
            lines.append("")
    lines.extend(
        [
            "## Command Examples",
            "",
            "```powershell",
            "python .\\main_change_impact.py --change-type generation_flag --changed-file config.py",
            "python .\\main_change_impact.py --changed-file main_grok_web.py --json",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _readme_markdown(snapshot: dict[str, object], manifest_relpaths: list[str]) -> str:
    counts = snapshot["counts"]
    root_links = [path for path in manifest_relpaths if "/" not in path and path not in {"README.md"}]
    code_links = [path for path in manifest_relpaths if path.startswith("code/")]
    docs_links = [path for path in manifest_relpaths if path.startswith("docs/")]
    data_links = [path for path in manifest_relpaths if path.startswith("data/")]
    lines = [
        "# Memory-to-Video_Agent",
        "",
        "Living project information for the source workspace `img-style-ag_1`.",
        "",
        "This repository is intended to store the current architecture, guides, change-impact rules, and machine-readable project status exported from the working project.",
        "",
        f"- Last synchronized: `{snapshot['generated_at']}`",
        f"- Source project: `{snapshot['source_project']}`",
        f"- Python files: `{counts['python_files']}`",
        f"- Test files: `{counts['test_files']}`",
        f"- Entry points: `{counts['entry_points']}`",
        "",
        "## Published Code Snapshot",
        "",
    ]
    lines.extend(f"- `{item}`" for item in code_links)
    lines.extend(
        [
            "",
        "## Published Documents",
        "",
        ]
    )
    lines.extend(f"- `{item}`" for item in docs_links)
    lines.extend(
        [
            "",
            "## Repository Safety Files",
            "",
        ]
    )
    lines.extend(f"- `{item}`" for item in root_links)
    lines.extend(
        [
            "",
            "## Machine-Readable Data",
            "",
        ]
    )
    lines.extend(f"- `{item}`" for item in data_links)
    lines.extend(
        [
            "",
            "## Update Workflow",
            "",
            "Refresh this repository content from the source project with:",
            "",
            "```powershell",
            "python .\\main_project_publication.py --target-dir <path-to-local-Memory-to-Video_Agent-clone>",
            "```",
            "",
            "The source project also provides `main_change_impact.py` for impact analysis when the codebase changes.",
            "",
            "Generated files may be overwritten on the next sync, so direct edits in generated docs should be avoided unless the source project is updated as well.",
            "",
        ]
    )
    return "\n".join(lines)


def _publication_gitignore() -> str:
    return "\n".join(
        [
            "# Managed by main_project_publication.py",
            "*",
            "!.gitignore",
            "!README.md",
            "!PUBLISHING.md",
            "!code/",
            "!code/**",
            "!docs/",
            "!docs/**",
            "!data/",
            "!data/**",
            "",
        ]
    )


def _publication_push_guide() -> str:
    return "\n".join(
        [
            "# Publishing Workflow",
            "",
            "This repository is intended to contain only the managed publication bundle exported from the source project.",
            "The current bundle includes a limited public code snapshot: core entry/config files plus selected Python sources from `api/` and `utils/`.",
            "",
            "## Safe Update Flow",
            "",
            "1. Refresh the bundle into this local clone.",
            "2. Stage only the managed files from `data/publication_manifest.json`.",
            "3. Review `git diff --staged`.",
            "4. Commit and push only after the staged diff looks correct.",
            "",
            "## Commands",
            "",
            "```powershell",
            "python .\\main_project_publication_push.py --repo-dir <path-to-local-Memory-to-Video_Agent-clone> --stage",
            "python .\\main_project_publication_push.py --repo-dir <path-to-local-Memory-to-Video_Agent-clone> --commit-message \"Update project publication\" --push",
            "```",
            "",
            "## Safety Rules",
            "",
            "- Do not push the working project root directly.",
            "- Do not copy `.env`, `input`, `output`, browser profiles, or temporary directories into this repository.",
            "- Publish only the managed code snapshot in `code/`: root `main/config` files plus selected Python sources from `api/` and `utils/`.",
            "- The publication sync blocks secret-like content and sanitizes local absolute paths.",
            "- `.gitignore` in this repository is generated to keep the repo limited to the managed publication files.",
            "",
        ]
    )


def _validate_publication_texts(texts_by_relpath: dict[str, str]) -> None:
    problems: list[str] = []
    for relpath, content in texts_by_relpath.items():
        hits = _secret_hits(content)
        if hits:
            problems.append(f"{relpath}: {', '.join(hits)}")
    if problems:
        raise ValueError(
            "Secret-like content detected in publication bundle: "
            + "; ".join(problems)
        )


def write_publication_bundle(source_root: Path, target_dir: Path, registry_path: Path | None = None) -> PublicationResult:
    source_root = source_root.resolve()
    target_dir = target_dir.resolve()
    registry_path = (registry_path or source_root / "project_structure_registry.json").resolve()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    snapshot = _project_snapshot(source_root, registry)

    docs_dir = target_dir / "docs"
    code_dir = target_dir / "code"
    data_dir = target_dir / "data"
    docs_dir.mkdir(parents=True, exist_ok=True)
    code_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    written_files: list[str] = []
    texts_to_validate: dict[str, str] = {}

    for source_name, target_relpath in DOC_TARGETS.items():
        source_path = source_root / source_name
        target_path = target_dir / target_relpath
        target_path.parent.mkdir(parents=True, exist_ok=True)
        public_text = _sanitize_public_text(source_path.read_text(encoding="utf-8"))
        texts_to_validate[target_relpath] = public_text
        target_path.write_text(public_text, encoding="utf-8")
        written_files.append(target_path.relative_to(target_dir).as_posix())

    for source_path, target_relpath in _iter_published_code_targets(source_root):
        target_path = target_dir / target_relpath
        target_path.parent.mkdir(parents=True, exist_ok=True)
        public_text = _sanitize_public_text(source_path.read_text(encoding="utf-8"))
        texts_to_validate[target_relpath] = public_text
        target_path.write_text(public_text, encoding="utf-8")
        written_files.append(target_path.relative_to(target_dir).as_posix())

    generated_docs = {
        "docs/PROJECT_OVERVIEW.md": _overview_markdown(snapshot, registry),
        "docs/CHANGE_IMPACT.md": _change_impact_markdown(registry),
        "PUBLISHING.md": _publication_push_guide(),
        ".gitignore": _publication_gitignore(),
    }
    for relpath, content in generated_docs.items():
        target_path = target_dir / relpath
        target_path.parent.mkdir(parents=True, exist_ok=True)
        public_text = _sanitize_public_text(content)
        texts_to_validate[relpath] = public_text
        target_path.write_text(public_text, encoding="utf-8")
        written_files.append(relpath)

    snapshot_path = target_dir / "data" / "project_snapshot.json"
    snapshot_text = json.dumps(snapshot, ensure_ascii=False, indent=2)
    texts_to_validate[snapshot_path.relative_to(target_dir).as_posix()] = snapshot_text
    snapshot_path.write_text(snapshot_text, encoding="utf-8")
    written_files.append(snapshot_path.relative_to(target_dir).as_posix())

    registry_copy_path = target_dir / "data" / "project_structure_registry.json"
    registry_text = _sanitize_public_text(json.dumps(registry, ensure_ascii=False, indent=2))
    texts_to_validate[registry_copy_path.relative_to(target_dir).as_posix()] = registry_text
    registry_copy_path.write_text(registry_text, encoding="utf-8")
    written_files.append(registry_copy_path.relative_to(target_dir).as_posix())

    manifest_relpaths = sorted(written_files)
    readme_path = target_dir / "README.md"
    readme_text = _readme_markdown(snapshot, manifest_relpaths)
    texts_to_validate["README.md"] = readme_text
    readme_path.write_text(readme_text, encoding="utf-8")
    manifest_relpaths = ["README.md", *manifest_relpaths]

    manifest = {
        "generated_at": snapshot["generated_at"],
        "source_project": snapshot["source_project"],
        "source_workspace": snapshot["source_workspace"],
        "managed_files": manifest_relpaths,
    }
    manifest_path = target_dir / "data" / "publication_manifest.json"
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    texts_to_validate[manifest_path.relative_to(target_dir).as_posix()] = manifest_text
    _validate_publication_texts(texts_to_validate)
    manifest_path.write_text(manifest_text, encoding="utf-8")
    manifest_relpaths.append(manifest_path.relative_to(target_dir).as_posix())

    return PublicationResult(
        target_dir=str(target_dir),
        manifest_path=str(manifest_path),
        snapshot_path=str(snapshot_path),
        written_files=manifest_relpaths,
    )
