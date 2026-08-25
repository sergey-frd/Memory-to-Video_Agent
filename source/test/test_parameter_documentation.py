from __future__ import annotations

import json
import re
from pathlib import Path


MATRIX_PATH = Path("docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md")
CONFIG_PATHS = (
    Path("hero_definition_Alice.json"),
    Path("sequence_trim_review_template.json"),
    Path("sequence_trim_review_Alice_replay_levels.json"),
    Path("sequence_keep_apply_template.json"),
    Path("sequence_keep_to_new_sequence_template.json"),
    Path("sequence_media_import_template.json"),
    Path("sequence_media_import_to_new_sequence_template.json"),
    Path("sequence_import_and_keep_template.json"),
    Path("sequence_music_recommendation_Alice.json"),
    Path("project_sequence_batch_template.json"),
)
ROOT_DOCUMENTS = {"README.md", "CHANGELOG.md"}
CANONICAL_DOCS = (
    Path("docs/README.md"),
    Path("docs/USER_GUIDE_EN.md"),
    Path("docs/USER_GUIDE_RU.md"),
    Path("docs/PROJECT_STRUCTURE.md"),
    Path("docs/PUBLISHING.md"),
    Path("docs/BATCH_RUN_HISTORY.md"),
    Path("docs/MINI_LAPTOP_WATERCOLOR.md"),
    Path("docs/PARAMETER_PROGRAM_BATCH_MATRIX_RU.md"),
    Path("docs/Seedance_2.0_Director.md"),
    Path("docs/portrait_styles_tables.md"),
)


def test_parameter_matrix_covers_all_workflow_config_leaf_keys() -> None:
    matrix = MATRIX_PATH.read_text(encoding="utf-8")
    missing: list[str] = []
    for config_path in CONFIG_PATHS:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        for key_path in _leaf_key_paths(payload):
            if f"`{key_path}`" not in matrix:
                missing.append(f"{config_path}: {key_path}")
    assert not missing, "Undocumented config parameters:\n" + "\n".join(missing)


def test_parameter_matrix_names_every_entry_program_and_batch() -> None:
    matrix = MATRIX_PATH.read_text(encoding="utf-8")
    required_names = (
        "main_hero_definition.py",
        "run_hero_definition.bat",
        "main_sequence_trim_review.py",
        "run_sequence_trim_review.bat",
        "main_premiere_import_keep.py",
        "run_sequence_keep_apply.bat",
        "run_sequence_keep_apply_standalone.bat",
        "run_sequence_media_import.bat",
        "run_sequence_media_import_standalone.bat",
        "run_sequence_import_and_keep.bat",
        "run_sequence_import_and_keep_standalone.bat",
        "main_project_sequence_batch.py",
        "run_project_sequence_batch.bat",
        "main_human_sequence_report.py",
        "main_sequence_reports.py",
        "main_sequence_music_first.py",
        "run_sequence_music_recommendation.bat",
    )
    assert all(f"`{name}`" in matrix for name in required_names)


def test_canonical_documentation_is_consolidated_under_docs() -> None:
    root_documents = {
        path.name
        for pattern in ("*.md", "*.html")
        for path in Path(".").glob(pattern)
    }
    assert root_documents == ROOT_DOCUMENTS
    assert all(path.is_file() for path in CANONICAL_DOCS)


def _h2_section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$", text, re.M)
    assert match is not None, f"Missing heading: {heading}"
    start = match.start()
    rest = text[match.end() :]
    next_h2 = re.search(r"^## ", rest, re.M)
    end = match.end() + next_h2.start() if next_h2 else len(text)
    return text[start:end]


def _bat_names(text: str) -> set[str]:
    return set(re.findall(r"(?:login_|run_|copy_|open_|install_)[\w.-]*\.bat", text))


def test_all_root_bat_files_are_listed_and_exemplified() -> None:
    bats = {path.name for path in Path(".").glob("*.bat")}
    assert bats, "No root .bat files found"

    en = Path("docs/USER_GUIDE_EN.md").read_text(encoding="utf-8")
    ru = Path("docs/USER_GUIDE_RU.md").read_text(encoding="utf-8")
    history = Path("docs/BATCH_RUN_HISTORY.md").read_text(encoding="utf-8")

    en_list = _bat_names(_h2_section(en, "BAT Files"))
    ru_list = _bat_names(_h2_section(ru, "BAT-файлы"))
    en_examples = _bat_names(_h2_section(en, "Typical Commands"))
    ru_examples = _bat_names(_h2_section(ru, "Типовые команды"))
    history_table = set()
    for line in history.splitlines():
        if line.startswith("| B"):
            match = re.search(r"`((?:login_|run_|copy_|open_|install_)[\w.-]*\.bat)`", line)
            if match:
                history_table.add(match.group(1))

    missing = {
        "USER_GUIDE_EN BAT Files": sorted(bats - en_list),
        "USER_GUIDE_RU BAT-файлы": sorted(bats - ru_list),
        "USER_GUIDE_EN Typical Commands": sorted(bats - en_examples),
        "USER_GUIDE_RU Типовые команды": sorted(bats - ru_examples),
        "BATCH_RUN_HISTORY table": sorted(bats - history_table),
    }
    missing = {place: names for place, names in missing.items() if names}
    assert not missing, "Root .bat files missing from docs:\n" + "\n".join(
        f"{place}: {', '.join(names)}" for place, names in missing.items()
    )


def _leaf_key_paths(value: object, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, nested_value in value.items():
            key_path = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_leaf_key_paths(nested_value, key_path))
        return paths
    if isinstance(value, list) and value and isinstance(value[0], dict):
        list_prefix = f"{prefix}[]"
        paths: list[str] = []
        for key, nested_value in value[0].items():
            paths.extend(_leaf_key_paths(nested_value, f"{list_prefix}.{key}"))
        return paths
    return [prefix]
