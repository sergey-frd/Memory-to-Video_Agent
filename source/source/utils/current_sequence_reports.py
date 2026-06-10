from __future__ import annotations

import json
import re
from pathlib import Path

from models.video_sequence import (
    ClipAssetBundle,
    PremiereSequenceClip,
    SequenceCandidate,
    SequenceOptimizationResult,
    SequenceRecommendationEntry,
)
from utils.premiere_project import parse_premiere_project_sequence_clips
from utils.sequence_structure_report import write_sequence_music_report, write_sequence_structure_report
from utils.transition_recommendations import write_transition_recommendations_report


def build_current_sequence_result_from_report(
    *,
    project_path: Path,
    sequence_name: str,
    optimization_report_json: Path,
) -> SequenceOptimizationResult:
    optimization_payload = json.loads(optimization_report_json.read_text(encoding="utf-8"))
    selected_sequence_name, clips = parse_premiere_project_sequence_clips(project_path, sequence_name)
    entry_payload_by_stage_id = _entry_payloads_by_stage_id(optimization_payload)

    entries: list[SequenceRecommendationEntry] = []
    missing_stage_ids: list[str] = []
    for current_index, clip in enumerate(clips, start=1):
        entry_payload = entry_payload_by_stage_id.get(clip.stage_id)
        if entry_payload is None:
            missing_stage_ids.append(clip.stage_id)
            continue
        entries.append(
            _build_current_sequence_entry(
                clip=clip,
                entry_payload=entry_payload,
                recommended_index=current_index,
            )
        )

    if missing_stage_ids:
        missing_display = ", ".join(missing_stage_ids)
        raise ValueError(
            "Current sequence contains clips that are missing from the optimization report metadata: "
            f"{missing_display}"
        )

    warnings = [
        *(str(item) for item in optimization_payload.get("warnings") or []),
        "Reports rebuilt from the current manual order in the Premiere sequence.",
    ]
    engine_requested = str(optimization_payload.get("engine_requested") or optimization_payload.get("engine_used") or "heuristic")
    engine_used = str(optimization_payload.get("engine_used") or engine_requested)

    return SequenceOptimizationResult(
        source_xml=str(project_path),
        selected_sequence_name=selected_sequence_name,
        engine_requested=engine_requested,
        engine_used=engine_used,
        warnings=warnings,
        entries=entries,
        feature_flags=dict(optimization_payload.get("feature_flags") or {}),
        translation_report_path=_optional_str(optimization_payload.get("translation_report_path")),
        translation_warnings=[str(item) for item in optimization_payload.get("translation_warnings") or []],
    )


def write_current_sequence_reports(
    *,
    project_path: Path,
    sequence_name: str,
    optimization_report_json: Path,
    output_json: Path,
    output_structure_txt: Path,
    output_transition_txt: Path,
    output_music_txt: Path | None = None,
) -> tuple[Path, Path, Path]:
    bundle_music_path = output_music_txt or derive_current_sequence_music_report_path(
        sequence_name=sequence_name,
        optimization_report_json=optimization_report_json,
        output_dir=output_json.parent,
    )
    result_json, _music_txt, structure_txt, transition_txt = write_current_sequence_report_bundle(
        project_path=project_path,
        sequence_name=sequence_name,
        optimization_report_json=optimization_report_json,
        output_json=output_json,
        output_music_txt=bundle_music_path,
        output_structure_txt=output_structure_txt,
        output_transition_txt=output_transition_txt,
    )
    assert structure_txt is not None
    assert transition_txt is not None
    return result_json, structure_txt, transition_txt


def write_current_sequence_report_bundle(
    *,
    project_path: Path,
    sequence_name: str,
    optimization_report_json: Path,
    output_json: Path,
    output_music_txt: Path,
    output_structure_txt: Path | None,
    output_transition_txt: Path | None,
    include_music: bool = True,
    include_structure: bool = True,
    include_transition: bool = True,
) -> tuple[Path, Path | None, Path | None, Path | None]:
    result = build_current_sequence_result_from_report(
        project_path=project_path,
        sequence_name=sequence_name,
        optimization_report_json=optimization_report_json,
    )

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    written_music_txt: Path | None = None
    if include_music:
        output_music_txt.parent.mkdir(parents=True, exist_ok=True)
        written_music_txt = write_sequence_music_report(result, output_path=output_music_txt)

    written_structure_txt: Path | None = None
    if include_structure:
        if output_structure_txt is None:
            raise ValueError("output_structure_txt must be provided when include_structure=True")
        output_structure_txt.parent.mkdir(parents=True, exist_ok=True)
        written_structure_txt = write_sequence_structure_report(result, output_path=output_structure_txt)

    written_transition_txt: Path | None = None
    if include_transition:
        if output_transition_txt is None:
            raise ValueError("output_transition_txt must be provided when include_transition=True")
        output_transition_txt.parent.mkdir(parents=True, exist_ok=True)
        written_transition_txt = write_transition_recommendations_report(
            project_path=project_path,
            sequence_name=result.selected_sequence_name,
            optimization_report_json=output_json,
            output_path=output_transition_txt,
        )
    return output_json, written_music_txt, written_structure_txt, written_transition_txt


def derive_current_sequence_report_paths(
    *,
    sequence_name: str,
    optimization_report_json: Path,
    output_dir: Path | None = None,
) -> tuple[Path, Path, Path]:
    report_dir = output_dir or optimization_report_json.parent
    sequence_slug = _slugify_filename(sequence_name)
    base_name = f"{sequence_slug}_manual_order"
    return (
        report_dir / f"{base_name}.json",
        report_dir / f"{base_name}_structure.txt",
        report_dir / f"{base_name}_transition_recommendations.txt",
    )


def derive_current_sequence_music_report_path(
    *,
    sequence_name: str,
    optimization_report_json: Path,
    output_dir: Path | None = None,
) -> Path:
    report_dir = output_dir or optimization_report_json.parent
    sequence_slug = _slugify_filename(sequence_name)
    base_name = f"{sequence_slug}_manual_order"
    return report_dir / f"{base_name}_music.txt"


def derive_current_sequence_report_bundle_paths(
    *,
    sequence_name: str,
    optimization_report_json: Path,
    output_dir: Path | None = None,
) -> tuple[Path, Path, Path, Path]:
    output_json, output_structure_txt, output_transition_txt = derive_current_sequence_report_paths(
        sequence_name=sequence_name,
        optimization_report_json=optimization_report_json,
        output_dir=output_dir,
    )
    output_music_txt = derive_current_sequence_music_report_path(
        sequence_name=sequence_name,
        optimization_report_json=optimization_report_json,
        output_dir=output_dir,
    )
    return output_json, output_music_txt, output_structure_txt, output_transition_txt


def _build_current_sequence_entry(
    *,
    clip: PremiereSequenceClip,
    entry_payload: dict[str, object],
    recommended_index: int,
) -> SequenceRecommendationEntry:
    candidate_payload = _as_dict(entry_payload.get("candidate"))
    return SequenceRecommendationEntry(
        recommended_index=recommended_index,
        original_index=_safe_int(entry_payload.get("original_index"), default=clip.order_index),
        score=_safe_float(entry_payload.get("score"), default=0.0),
        reason="Current manual order from the Premiere sequence.",
        candidate=SequenceCandidate(
            clip=clip,
            assets=_build_asset_bundle(candidate_payload, clip.stage_id),
            keywords=_string_list(candidate_payload.get("keywords")),
            people_count=_safe_int(candidate_payload.get("people_count")),
            shot_scale=_safe_int(candidate_payload.get("shot_scale")),
            energy_level=_safe_int(candidate_payload.get("energy_level")),
            series_subject_tokens=_string_list(candidate_payload.get("series_subject_tokens")),
            series_appearance_tokens=_string_list(candidate_payload.get("series_appearance_tokens")),
            series_pose_tokens=_string_list(candidate_payload.get("series_pose_tokens")),
            main_character_priority=_safe_float(candidate_payload.get("main_character_priority"), default=0.0),
            opening_score=_safe_float(candidate_payload.get("opening_score"), default=0.0),
            main_character_age_hint=_optional_float(candidate_payload.get("main_character_age_hint")),
            main_character_notes=_string_list(candidate_payload.get("main_character_notes")),
            continuity_notes=_string_list(candidate_payload.get("continuity_notes")),
        ),
    )


def _build_asset_bundle(candidate_payload: dict[str, object], stage_id: str) -> ClipAssetBundle:
    assets_payload = _as_dict(candidate_payload.get("assets"))
    return ClipAssetBundle(
        stage_id=str(assets_payload.get("stage_id") or stage_id),
        bundle_dir=str(assets_payload.get("bundle_dir") or ""),
        manifest_path=_optional_str(assets_payload.get("manifest_path")),
        scene_analysis_path=_optional_str(assets_payload.get("scene_analysis_path")),
        prompt_path=_optional_str(assets_payload.get("prompt_path")),
        manifest=_as_dict(assets_payload.get("manifest")),
        scene_analysis=_as_dict(assets_payload.get("scene_analysis")),
        prompt_text=str(assets_payload.get("prompt_text") or ""),
        missing_files=_string_list(assets_payload.get("missing_files")),
    )


def _entry_payloads_by_stage_id(optimization_payload: dict[str, object]) -> dict[str, dict[str, object]]:
    payloads: dict[str, dict[str, object]] = {}
    for raw_entry in optimization_payload.get("entries") or []:
        if not isinstance(raw_entry, dict):
            continue
        candidate_payload = _as_dict(raw_entry.get("candidate"))
        clip_payload = _as_dict(candidate_payload.get("clip"))
        stage_id = str(clip_payload.get("stage_id") or "").strip()
        if not stage_id:
            continue
        payloads[stage_id] = raw_entry
    return payloads


def _slugify_filename(value: str) -> str:
    compact = re.sub(r"\s+", "_", value.strip())
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", compact)
    return normalized.strip("._-") or "sequence"


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _safe_int(value: object, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: object, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
