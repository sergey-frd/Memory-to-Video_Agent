from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from config import Settings
from main_sequence_optimizer import run_project_sequence_optimizer
from utils.human_profile_sequence_report import write_human_profile_sequence_report_from_json
from utils.project_delivery import (
    clear_directory_contents,
    copy_file_to_path,
    derive_reports_dir_from_regeneration_assets,
    move_directory_contents,
    path_is_within_directory,
    remove_path,
)
from utils.sequence_structure_report import derive_structure_report_path
from utils.transition_recommendations import normalize_transition_mode, write_transition_recommendations_report


def run_project_sequence_batch_from_config(
    config_path: Path,
    *,
    settings: Settings | None = None,
) -> tuple[Path, Path]:
    settings = settings or Settings()
    settings.ensure_output()

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    project_path = Path(str(payload["project_path"]))
    regeneration_assets_dir = Path(str(payload["regeneration_assets_dir"]))
    configured_output_project_path = Path(str(payload["output_project_path"]))
    staging_reports_dir, final_reports_dir = _resolve_batch_reports_dirs(
        payload,
        settings=settings,
        config_path=config_path,
        project_path=project_path,
        regeneration_assets_dir=regeneration_assets_dir,
    )
    staged_output_project_path = staging_reports_dir / configured_output_project_path.name
    engine = str(payload.get("engine") or "heuristic")
    enable_auto_transitions = bool(payload.get("enable_auto_transitions", False))
    transition_mode = normalize_transition_mode(payload.get("transition_mode"), enable_auto_transitions=enable_auto_transitions)
    enable_subject_series_grouping = bool(payload.get("enable_subject_series_grouping", False))
    allow_transition_handle_trimming = bool(payload.get("allow_transition_handle_trimming", False))
    generate_personalized_report = bool(payload.get("generate_personalized_report", False))
    human_detail_txt_raw = payload.get("human_detail_txt")
    human_detail_txt = Path(str(human_detail_txt_raw)) if human_detail_txt_raw else None
    translation_results_path = payload.get("translation_results_path")
    legacy_prin_path = payload.get("prin_path")
    translation_results_hint = translation_results_path or legacy_prin_path
    translation_results = Path(str(translation_results_hint)) if translation_results_hint else None
    sequence_jobs = payload.get("sequence_jobs") or []

    if not sequence_jobs:
        raise ValueError(f"Batch config does not contain sequence_jobs: {config_path}")
    if generate_personalized_report and human_detail_txt is None:
        raise ValueError(
            f"Batch config requested generate_personalized_report but did not provide human_detail_txt: {config_path}"
        )

    clear_directory_contents(staging_reports_dir)
    staging_reports_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = staging_reports_dir / "temp_projects" if len(sequence_jobs) > 1 else None
    if temp_dir is not None:
        temp_dir.mkdir(parents=True, exist_ok=True)

    current_project_path = project_path
    completed_jobs: list[dict[str, object]] = []

    for index, raw_job in enumerate(sequence_jobs, start=1):
        source_sequence_name = str(raw_job["source_sequence_name"])
        new_sequence_name = str(raw_job.get("new_sequence_name") or f"{source_sequence_name}__optimized")
        sequence_slug = _slugify_filename(new_sequence_name)
        job_output_json = staging_reports_dir / f"{index:02d}_{sequence_slug}.json"
        job_output_txt = staging_reports_dir / f"{index:02d}_{sequence_slug}.txt"
        job_structure_txt = derive_structure_report_path(job_output_txt)
        is_last_job = index == len(sequence_jobs)
        if is_last_job:
            job_output_project = staged_output_project_path
        elif temp_dir is not None:
            job_output_project = temp_dir / f"{index:02d}_{sequence_slug}.prproj"
        else:
            job_output_project = staging_reports_dir / f"{index:02d}_{sequence_slug}.prproj"
        transition_recommendations_txt: Path | None = None
        human_profile_report_txt: Path | None = None

        run_project_sequence_optimizer(
            current_project_path,
            regeneration_assets_dir=regeneration_assets_dir,
            sequence_name=source_sequence_name,
            engine=engine,
            output_json=job_output_json,
            output_txt=job_output_txt,
            output_prproj=job_output_project,
            new_sequence_name=new_sequence_name,
            translation_results_path=translation_results,
            enable_auto_transitions=transition_mode == "apply",
            enable_subject_series_grouping=enable_subject_series_grouping,
            allow_transition_handle_trimming=allow_transition_handle_trimming and transition_mode == "apply",
        )

        if transition_mode == "recommend_only":
            transition_recommendations_txt = staging_reports_dir / f"{index:02d}_{sequence_slug}_transition_recommendations.txt"
            write_transition_recommendations_report(
                project_path=job_output_project,
                sequence_name=new_sequence_name,
                optimization_report_json=job_output_json,
                output_path=transition_recommendations_txt,
            )

        if generate_personalized_report and human_detail_txt is not None:
            human_profile_report_txt = staging_reports_dir / f"{index:02d}_{sequence_slug}_human_profile_report.txt"
            write_human_profile_sequence_report_from_json(
                optimization_report_json=job_output_json,
                human_detail_txt=human_detail_txt,
                output_path=human_profile_report_txt,
            )

        completed_jobs.append(
            {
                "source_sequence_name": source_sequence_name,
                "new_sequence_name": new_sequence_name,
                "report_json_path": job_output_json,
                "report_txt_path": job_output_txt,
                "structure_report_txt_path": job_structure_txt if job_structure_txt.exists() else None,
                "transition_recommendations_txt_path": transition_recommendations_txt,
                "human_profile_report_txt_path": human_profile_report_txt,
                "project_after_job_path": job_output_project,
            }
        )
        current_project_path = job_output_project

    delivered_jobs = _deliver_batch_outputs(
        completed_jobs,
        settings=settings,
        staging_reports_dir=staging_reports_dir,
        final_reports_dir=final_reports_dir,
        staged_output_project_path=staged_output_project_path,
        configured_output_project_path=configured_output_project_path,
    )
    batch_transition_recommendations_txt = _write_batch_transition_recommendations_report(
        delivered_jobs,
        reports_dir=final_reports_dir,
        transition_mode=transition_mode,
    )

    summary = {
        "config_path": str(config_path),
        "project_path": str(project_path),
        "prin_path": legacy_prin_path,
        "translation_results_path": translation_results_path,
        "regeneration_assets_dir": str(regeneration_assets_dir),
        "output_project_path": str(configured_output_project_path),
        "reports_output_project_path": str(final_reports_dir / staged_output_project_path.name),
        "reports_dir": str(final_reports_dir),
        "staging_reports_dir": str(staging_reports_dir),
        "engine_requested": engine,
        "transition_mode": transition_mode,
        "enable_auto_transitions": transition_mode == "apply",
        "enable_subject_series_grouping": enable_subject_series_grouping,
        "allow_transition_handle_trimming": allow_transition_handle_trimming and transition_mode == "apply",
        "generate_personalized_report": generate_personalized_report,
        "human_detail_txt": str(human_detail_txt) if human_detail_txt else None,
        "batch_transition_recommendations_txt": (
            str(batch_transition_recommendations_txt) if batch_transition_recommendations_txt else None
        ),
        "sequence_jobs": [_serialize_completed_job(item) for item in delivered_jobs],
    }

    summary_json_path = final_reports_dir / "batch_summary.json"
    summary_txt_path = final_reports_dir / "batch_summary.txt"
    summary_json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_txt_path.write_text(_format_batch_summary(summary), encoding="utf-8")
    return summary_json_path, summary_txt_path


def _resolve_batch_reports_dirs(
    payload: dict[str, object],
    *,
    settings: Settings,
    config_path: Path,
    project_path: Path,
    regeneration_assets_dir: Path,
) -> tuple[Path, Path]:
    configured_reports_dir_raw = payload.get("reports_dir")
    configured_staging_dir_raw = payload.get("staging_reports_dir")
    configured_final_reports_dir_raw = payload.get("final_reports_dir")
    derived_final_reports_dir = Path(
        str(configured_final_reports_dir_raw or derive_reports_dir_from_regeneration_assets(regeneration_assets_dir))
    )
    if configured_reports_dir_raw and not path_is_within_directory(Path(str(configured_reports_dir_raw)), settings.output_dir):
        final_reports_dir = Path(str(configured_reports_dir_raw))
    else:
        final_reports_dir = derived_final_reports_dir

    config_hash = hashlib.sha1(str(config_path.resolve(strict=False)).encode("utf-8")).hexdigest()[:10]
    staging_slug = _slugify_filename(f".{final_reports_dir.name or project_path.stem}_{config_hash}_staging")
    default_staging_dir = final_reports_dir.parent / staging_slug

    if configured_staging_dir_raw:
        staging_reports_dir = Path(str(configured_staging_dir_raw))
        return staging_reports_dir, final_reports_dir

    return default_staging_dir, final_reports_dir


def _deliver_batch_outputs(
    completed_jobs: list[dict[str, object]],
    *,
    settings: Settings,
    staging_reports_dir: Path,
    final_reports_dir: Path,
    staged_output_project_path: Path,
    configured_output_project_path: Path,
) -> list[dict[str, object]]:
    staging_is_separate = staging_reports_dir.resolve(strict=False) != final_reports_dir.resolve(strict=False)
    staging_was_renamed = False

    if staging_is_separate:
        final_reports_has_existing_content = final_reports_dir.exists() and any(final_reports_dir.iterdir())
        if not final_reports_has_existing_content:
            final_reports_dir.parent.mkdir(parents=True, exist_ok=True)
            if final_reports_dir.exists():
                final_reports_dir.rmdir()
            try:
                os.replace(staging_reports_dir, final_reports_dir)
                staging_was_renamed = True
            except OSError:
                final_reports_dir.mkdir(parents=True, exist_ok=True)
                move_directory_contents(staging_reports_dir, final_reports_dir)
        else:
            move_directory_contents(staging_reports_dir, final_reports_dir)
    else:
        final_reports_dir.mkdir(parents=True, exist_ok=True)

    delivered_jobs: list[dict[str, object]] = []
    for item in completed_jobs:
        delivered_item = {
            "source_sequence_name": item["source_sequence_name"],
            "new_sequence_name": item["new_sequence_name"],
            "report_json_path": _resolve_delivered_path(Path(item["report_json_path"]), staging_reports_dir, final_reports_dir),
            "report_txt_path": _resolve_delivered_path(Path(item["report_txt_path"]), staging_reports_dir, final_reports_dir),
            "structure_report_txt_path": (
                _resolve_delivered_path(Path(item["structure_report_txt_path"]), staging_reports_dir, final_reports_dir)
                if item.get("structure_report_txt_path")
                else None
            ),
            "transition_recommendations_txt_path": (
                _resolve_delivered_path(Path(item["transition_recommendations_txt_path"]), staging_reports_dir, final_reports_dir)
                if item.get("transition_recommendations_txt_path")
                else None
            ),
            "human_profile_report_txt_path": (
                _resolve_delivered_path(Path(item["human_profile_report_txt_path"]), staging_reports_dir, final_reports_dir)
                if item.get("human_profile_report_txt_path")
                else None
            ),
            "project_after_job_path": _resolve_delivered_path(
                Path(item["project_after_job_path"]),
                staging_reports_dir,
                final_reports_dir,
            ),
        }
        delivered_jobs.append(delivered_item)

    delivered_output_project_path = _resolve_delivered_path(
        staged_output_project_path,
        staging_reports_dir,
        final_reports_dir,
    )
    if path_is_within_directory(configured_output_project_path, settings.output_dir):
        configured_copy_target: Path | None = None
    else:
        configured_copy_target = configured_output_project_path

    if configured_copy_target is not None and configured_copy_target != delivered_output_project_path:
        copy_file_to_path(delivered_output_project_path, configured_copy_target)

    for item in delivered_jobs:
        transition_report_path = item.get("transition_recommendations_txt_path")
        if not transition_report_path:
            continue
        _rewrite_transition_recommendations_project_path(
            report_path=Path(transition_report_path),
            old_project_path=Path(item["project_after_job_path"]) if staging_reports_dir == final_reports_dir else _resolve_staged_path(
                Path(item["project_after_job_path"]),
                staging_reports_dir=staging_reports_dir,
                final_reports_dir=final_reports_dir,
            ),
            new_project_path=Path(item["project_after_job_path"]),
        )

    if staging_is_separate and not staging_was_renamed and staging_reports_dir.exists():
        try:
            clear_directory_contents(staging_reports_dir)
            if not any(staging_reports_dir.iterdir()):
                remove_path(staging_reports_dir)
        except OSError:
            pass

    return delivered_jobs


def _resolve_delivered_path(path: Path, staging_reports_dir: Path, final_reports_dir: Path) -> Path:
    try:
        relative_path = path.relative_to(staging_reports_dir)
    except ValueError:
        return path
    return final_reports_dir / relative_path


def _resolve_staged_path(path: Path, *, staging_reports_dir: Path, final_reports_dir: Path) -> Path:
    try:
        relative_path = path.relative_to(final_reports_dir)
    except ValueError:
        return path
    return staging_reports_dir / relative_path


def _rewrite_transition_recommendations_project_path(
    *,
    report_path: Path,
    old_project_path: Path,
    new_project_path: Path,
) -> None:
    if not report_path.exists() or old_project_path == new_project_path:
        return
    report_text = report_path.read_text(encoding="utf-8")
    updated_text = report_text.replace(
        f"Project: {old_project_path}",
        f"Project: {new_project_path}",
        1,
    )
    if updated_text != report_text:
        report_path.write_text(updated_text, encoding="utf-8")


def _serialize_completed_job(item: dict[str, object]) -> dict[str, object]:
    return {
        "source_sequence_name": item["source_sequence_name"],
        "new_sequence_name": item["new_sequence_name"],
        "report_json": str(item["report_json_path"]),
        "report_txt": str(item["report_txt_path"]),
        "structure_report_txt": str(item["structure_report_txt_path"]) if item.get("structure_report_txt_path") else None,
        "transition_recommendations_txt": (
            str(item["transition_recommendations_txt_path"])
            if item.get("transition_recommendations_txt_path")
            else None
        ),
        "human_profile_report_txt": (
            str(item["human_profile_report_txt_path"])
            if item.get("human_profile_report_txt_path")
            else None
        ),
        "project_after_job": str(item["project_after_job_path"]),
    }


def _format_batch_summary(summary: dict[str, object]) -> str:
    lines = [
        "PREMIERE PROJECT SEQUENCE BATCH REPORT",
        "",
        f"Config path: {summary.get('config_path')}",
        f"Source project: {summary.get('project_path')}",
        f"Source prin: {summary.get('prin_path') or '<not provided>'}",
        f"Translation report path: {summary.get('translation_results_path') or '<auto/legacy lookup>'}",
        f"Regeneration assets: {summary.get('regeneration_assets_dir')}",
        f"Output project: {summary.get('output_project_path')}",
        f"Reports output project: {summary.get('reports_output_project_path')}",
        f"Reports dir: {summary.get('reports_dir')}",
        f"Staging reports dir: {summary.get('staging_reports_dir')}",
        f"Engine requested: {summary.get('engine_requested')}",
        f"Transition mode: {summary.get('transition_mode')}",
        f"Auto transitions: {summary.get('enable_auto_transitions')}",
        f"Subject series grouping: {summary.get('enable_subject_series_grouping')}",
        f"Allow transition handle trimming: {summary.get('allow_transition_handle_trimming')}",
        f"Generate personalized report: {summary.get('generate_personalized_report')}",
        f"Human detail TXT: {summary.get('human_detail_txt') or '<disabled>'}",
        f"Batch transition recommendations: {summary.get('batch_transition_recommendations_txt') or '<disabled>'}",
        "",
        "Sequence jobs",
        "",
    ]

    for index, item in enumerate(summary.get("sequence_jobs") or [], start=1):
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"{index}. {item.get('source_sequence_name')} -> {item.get('new_sequence_name')}",
                f"   JSON: {item.get('report_json')}",
                f"   TXT: {item.get('report_txt')}",
                f"   Structure TXT: {item.get('structure_report_txt') or '<not generated>'}",
                f"   Transition recommendations: {item.get('transition_recommendations_txt') or '<disabled>'}",
                f"   Personalized report: {item.get('human_profile_report_txt') or '<disabled>'}",
                f"   Project after job: {item.get('project_after_job')}",
                "",
            ]
        )

    return "\n".join(lines).strip() + "\n"


def _write_batch_transition_recommendations_report(
    completed_jobs: list[dict[str, object]],
    *,
    reports_dir: Path,
    transition_mode: str,
) -> Path | None:
    if transition_mode != "recommend_only":
        return None

    sections: list[str] = []
    for item in completed_jobs:
        recommendation_path_raw = item.get("transition_recommendations_txt_path") or item.get("transition_recommendations_txt")
        if not recommendation_path_raw:
            continue
        recommendation_path = Path(str(recommendation_path_raw))
        if not recommendation_path.exists():
            continue
        sections.extend(
            [
                f"=== {item.get('new_sequence_name')} ===",
                "",
                recommendation_path.read_text(encoding="utf-8").strip(),
                "",
            ]
        )

    if not sections:
        return None

    batch_path = reports_dir / "batch_transition_recommendations.txt"
    batch_path.write_text("\n".join(sections).strip() + "\n", encoding="utf-8")
    return batch_path


def _slugify_filename(value: str) -> str:
    compact = re.sub(r"\s+", "_", value.strip())
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", compact)
    return normalized.strip("._-") or "sequence"
