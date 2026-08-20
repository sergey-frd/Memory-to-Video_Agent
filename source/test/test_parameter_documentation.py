from __future__ import annotations

import json
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
        "run_sequence_keep_apply.bat",
        "run_sequence_media_import.bat",
        "run_sequence_import_and_keep.bat",
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
