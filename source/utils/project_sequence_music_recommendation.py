from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from config import Settings
from utils.human_profile_sequence_report import write_human_profile_sequence_report_from_json
from utils.project_sequence_reports_from_project import (
    derive_project_sequence_music_first_bundle_paths,
    write_project_sequence_music_first_bundle,
)


ProgressReporter = Callable[[str], None]


@dataclass(frozen=True)
class ProjectSequenceMusicRecommendationConfig:
    config_path: Path
    project_config_path: Path
    sequence_config_path: Path
    project_path: Path
    source_sequence_name: str
    hero_definition_path: Path
    human_detail_txt: Path
    reports_dir: Path
    output_json: Path
    output_music_txt: Path
    output_personalized_music_txt: Path
    output_structure_txt: Path
    output_transition_txt: Path
    max_sampled_clips: int
    max_analyzed_clips: int | None
    full_recommendations: bool
    scene_model: str | None


@dataclass(frozen=True)
class ProjectSequenceMusicRecommendationResult:
    output_json: Path
    output_music_txt: Path
    output_personalized_music_txt: Path
    output_structure_txt: Path | None
    output_transition_txt: Path | None


def load_project_sequence_music_recommendation_config(
    config_path: Path,
) -> ProjectSequenceMusicRecommendationConfig:
    config_path = config_path.resolve()
    payload = _load_json_object(config_path)
    base_dir = config_path.parent

    project_config_path = _required_config_reference(payload, "project_config_path", base_dir)
    sequence_config_path = _required_config_reference(payload, "sequence_config_path", base_dir)
    project_payload = _load_json_object(project_config_path)
    sequence_payload = _load_json_object(sequence_config_path)

    project_path = _choose_consistent_path(
        "project_path",
        _path_from_payload(payload, "project_path", base_dir),
        _path_from_payload(sequence_payload, "project_path", sequence_config_path.parent),
    )
    if project_path is None:
        raise ValueError("project_path is required in the music or sequence config.")

    source_sequence_name = _choose_consistent_text(
        "source_sequence_name",
        payload.get("source_sequence_name"),
        sequence_payload.get("source_sequence_name"),
    )
    if not source_sequence_name:
        raise ValueError("source_sequence_name is required in the music or sequence config.")

    hero_definition_path = _choose_consistent_path(
        "hero_definition_path",
        _path_from_payload(payload, "hero_definition_path", base_dir),
        _path_from_payload(sequence_payload, "hero_definition_path", sequence_config_path.parent),
    )
    if hero_definition_path is None:
        raise ValueError("hero_definition_path is required in the music or sequence config.")
    hero_payload = _load_json_object(hero_definition_path)
    hero_sources = hero_payload.get("sources")
    if not isinstance(hero_sources, dict):
        raise ValueError(f"Hero definition has no sources object: {hero_definition_path}")

    human_detail_txt = _choose_consistent_path(
        "human_detail_txt",
        _path_from_payload(payload, "human_detail_txt", base_dir),
        _path_from_payload(project_payload, "human_detail_txt", project_config_path.parent),
        _path_from_payload(hero_sources, "human_detail_txt", hero_definition_path.parent),
    )
    if human_detail_txt is None:
        raise ValueError("human_detail_txt was not found in the music config, project config, or hero definition.")

    explicit_reports_dir = _path_from_payload(payload, "reports_dir", base_dir)
    if explicit_reports_dir is not None and explicit_reports_dir.suffix.lower() == ".json":
        raise ValueError(
            "reports_dir must point to a directory, not hero_def.json; "
            "use hero_definition_path for the hero definition file."
        )
    reports_dir = _choose_consistent_path(
        "reports_dir",
        explicit_reports_dir,
        _path_from_payload(project_payload, "reports_dir", project_config_path.parent),
    )
    if reports_dir is None:
        raise ValueError("reports_dir is required in the music or project config.")
    if not project_path.is_file():
        raise FileNotFoundError(f"Premiere project does not exist: {project_path}")
    if not human_detail_txt.is_file():
        raise FileNotFoundError(f"Human detail text does not exist: {human_detail_txt}")
    _verify_human_detail_hash(hero_sources, human_detail_txt, hero_definition_path)

    default_json, default_music, default_structure, default_transition = (
        derive_project_sequence_music_first_bundle_paths(
            project_path=project_path,
            sequence_name=source_sequence_name,
            output_dir=reports_dir,
        )
    )
    output_json = _path_from_payload(payload, "output_json", base_dir) or default_json
    output_music_txt = _path_from_payload(payload, "output_music_txt", base_dir) or default_music
    output_personalized_music_txt = (
        _path_from_payload(payload, "output_personalized_music_txt", base_dir)
        or reports_dir / f"{output_json.stem}_personalized_music.txt"
    )
    output_structure_txt = _path_from_payload(payload, "output_structure_txt", base_dir) or default_structure
    output_transition_txt = _path_from_payload(payload, "output_transition_txt", base_dir) or default_transition

    max_sampled_clips = int(payload.get("max_sampled_clips", 12))
    if max_sampled_clips <= 0:
        raise ValueError("max_sampled_clips must be > 0.")
    max_analyzed_value = payload.get("max_analyzed_clips")
    max_analyzed_clips = int(max_analyzed_value) if max_analyzed_value is not None else None
    if max_analyzed_clips is not None and max_analyzed_clips <= 0:
        raise ValueError("max_analyzed_clips must be > 0 when provided.")
    scene_model_value = payload.get("scene_model")
    scene_model = str(scene_model_value).strip() if scene_model_value else None

    return ProjectSequenceMusicRecommendationConfig(
        config_path=config_path,
        project_config_path=project_config_path,
        sequence_config_path=sequence_config_path,
        project_path=project_path,
        source_sequence_name=source_sequence_name,
        hero_definition_path=hero_definition_path,
        human_detail_txt=human_detail_txt,
        reports_dir=reports_dir,
        output_json=output_json,
        output_music_txt=output_music_txt,
        output_personalized_music_txt=output_personalized_music_txt,
        output_structure_txt=output_structure_txt,
        output_transition_txt=output_transition_txt,
        max_sampled_clips=max_sampled_clips,
        max_analyzed_clips=max_analyzed_clips,
        full_recommendations=bool(payload.get("full_recommendations", False)),
        scene_model=scene_model,
    )


def run_project_sequence_music_recommendation_from_config(
    config_path: Path,
    *,
    settings: Settings | None = None,
    progress_reporter: ProgressReporter | None = None,
    bundle_writer=write_project_sequence_music_first_bundle,
    personalized_writer=write_human_profile_sequence_report_from_json,
) -> ProjectSequenceMusicRecommendationResult:
    config = load_project_sequence_music_recommendation_config(config_path)
    progress = progress_reporter or (lambda _message: None)
    config.reports_dir.mkdir(parents=True, exist_ok=True)

    progress(
        f"Music report: sequence '{config.source_sequence_name}' from {config.project_path.name}; "
        f"sampling up to {config.max_sampled_clips} clips."
    )
    written_json, written_music, written_structure, written_transition = bundle_writer(
        project_path=config.project_path,
        sequence_name=config.source_sequence_name,
        output_json=config.output_json,
        output_music_txt=config.output_music_txt,
        output_structure_txt=config.output_structure_txt if config.full_recommendations else None,
        output_transition_txt=config.output_transition_txt if config.full_recommendations else None,
        include_structure=config.full_recommendations,
        include_transition=config.full_recommendations,
        max_sampled_clips=config.max_sampled_clips,
        max_analyzed_clips=config.max_analyzed_clips,
        scene_model=config.scene_model,
        settings=settings,
        progress_reporter=progress,
    )
    progress("Video-only music report completed; applying the human profile.")
    personalized_path = personalized_writer(
        optimization_report_json=written_json,
        human_detail_txt=config.human_detail_txt,
        output_path=config.output_personalized_music_txt,
    )
    _append_music_context(
        written_json,
        config=config,
        personalized_music_path=personalized_path,
    )
    progress(f"Personalized music report completed: {personalized_path}")
    return ProjectSequenceMusicRecommendationResult(
        output_json=written_json,
        output_music_txt=written_music,
        output_personalized_music_txt=personalized_path,
        output_structure_txt=written_structure,
        output_transition_txt=written_transition,
    )


def _append_music_context(
    output_json: Path,
    *,
    config: ProjectSequenceMusicRecommendationConfig,
    personalized_music_path: Path,
) -> None:
    payload = _load_json_object(output_json)
    payload["personalized_music_context"] = {
        "project_config_path": str(config.project_config_path),
        "sequence_config_path": str(config.sequence_config_path),
        "hero_definition_path": str(config.hero_definition_path),
        "human_detail_txt": str(config.human_detail_txt),
        "reports_dir": str(config.reports_dir),
        "personalized_music_report": str(personalized_music_path),
    }
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json_object(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(f"JSON config does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in: {path}")
    return payload


def _required_config_reference(payload: dict[str, object], key: str, base_dir: Path) -> Path:
    path = _path_from_payload(payload, key, base_dir)
    if path is None:
        raise ValueError(f"{key} is required.")
    return path


def _path_from_payload(payload: dict[str, object], key: str, base_dir: Path) -> Path | None:
    value = payload.get(key)
    if value is None or not str(value).strip():
        return None
    path = Path(str(value))
    return path if path.is_absolute() else (base_dir / path).resolve()


def _choose_consistent_path(label: str, *values: Path | None) -> Path | None:
    paths = [value.resolve() for value in values if value is not None]
    if not paths:
        return None
    if any(path != paths[0] for path in paths[1:]):
        raise ValueError(f"Conflicting {label} values: {', '.join(str(path) for path in paths)}")
    return paths[0]


def _choose_consistent_text(label: str, *values: object) -> str:
    texts = [str(value).strip() for value in values if value is not None and str(value).strip()]
    if not texts:
        return ""
    if any(text != texts[0] for text in texts[1:]):
        raise ValueError(f"Conflicting {label} values: {', '.join(texts)}")
    return texts[0]


def _verify_human_detail_hash(
    hero_sources: dict[str, object],
    human_detail_txt: Path,
    hero_definition_path: Path,
) -> None:
    expected_hash = str(hero_sources.get("human_detail_sha256") or "").strip().lower()
    if not expected_hash:
        return
    actual_hash = hashlib.sha256(human_detail_txt.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(
            "human_detail_txt no longer matches the text used to create "
            f"{hero_definition_path}; regenerate hero_def.json or select the matching text file."
        )
