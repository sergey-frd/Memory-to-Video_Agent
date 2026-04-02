from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from api.openai_scene import analyze_scene_with_openai
from config import Settings
from models.scene_analysis import SceneAnalysis
from models.video_sequence import (
    ClipAssetBundle,
    PremiereSequenceClip,
    SequenceCandidate,
    SequenceOptimizationResult,
    SequenceRecommendationEntry,
)
from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    is_supported_image_media_path,
    is_supported_video_media_path,
    parse_premiere_project_sequence_visual_clips,
)
from utils.sequence_optimizer_runtime import (
    _collect_keywords as _runtime_collect_keywords,
    _infer_energy_level as _runtime_infer_energy_level,
    _infer_main_character_priority as _runtime_infer_main_character_priority,
    _infer_series_subject_features as _runtime_infer_series_subject_features,
    _infer_shot_scale as _runtime_infer_shot_scale,
    _keyword_document_frequency,
    _score_opening_candidate as _runtime_score_opening_candidate,
    optimize_sequence_with_heuristic,
)
from utils.sequence_structure_report import (
    write_sequence_music_report,
    write_sequence_structure_report,
)
from utils.transition_recommendations import write_transition_recommendations_from_result

try:
    import cv2
except ImportError:  # pragma: no cover - runtime guard
    cv2 = None  # type: ignore[assignment]


SceneAnalyzer = Callable[[Path, str | None], SceneAnalysis]


@dataclass(frozen=True)
class AnalyzedSequenceClipRecord:
    analyzed_index: int
    original_clip_index: int
    clip_name: str
    source_path: str
    analysis_frame_path: str
    sample_seconds: float | None
    sample_mode: str

    def to_dict(self) -> dict[str, object]:
        return {
            "analyzed_index": self.analyzed_index,
            "original_clip_index": self.original_clip_index,
            "clip_name": self.clip_name,
            "source_path": self.source_path,
            "analysis_frame_path": self.analysis_frame_path,
            "sample_seconds": self.sample_seconds,
            "sample_mode": self.sample_mode,
        }


def derive_project_sequence_music_first_paths(
    *,
    project_path: Path,
    sequence_name: str,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    report_dir = output_dir or project_path.parent
    base_name = f"{project_path.stem}_{_slugify_filename(sequence_name)}_music_first"
    return (
        report_dir / f"{base_name}.json",
        report_dir / f"{base_name}.txt",
    )


def derive_project_sequence_music_first_bundle_paths(
    *,
    project_path: Path,
    sequence_name: str,
    output_dir: Path | None = None,
) -> tuple[Path, Path, Path, Path]:
    output_json, output_music_txt = derive_project_sequence_music_first_paths(
        project_path=project_path,
        sequence_name=sequence_name,
        output_dir=output_dir,
    )
    base_name = output_json.stem
    return (
        output_json,
        output_music_txt,
        output_json.parent / f"{base_name}_structure.txt",
        output_json.parent / f"{base_name}_transition_recommendations.txt",
    )


def write_project_sequence_music_first_bundle(
    *,
    project_path: Path,
    sequence_name: str,
    output_json: Path,
    output_music_txt: Path,
    output_structure_txt: Path | None = None,
    output_transition_txt: Path | None = None,
    include_structure: bool = False,
    include_transition: bool = False,
    max_sampled_clips: int = 12,
    max_analyzed_clips: int | None = None,
    scene_model: str | None = None,
    settings: Settings | None = None,
    analyzer: SceneAnalyzer | None = None,
) -> tuple[Path, Path, Path | None, Path | None]:
    result, payload = build_project_sequence_music_first_payload(
        project_path=project_path,
        sequence_name=sequence_name,
        output_dir=output_json.parent,
        recommend_sequence=include_structure or include_transition,
        max_sampled_clips=max_sampled_clips,
        max_analyzed_clips=max_analyzed_clips,
        scene_model=scene_model,
        settings=settings,
        analyzer=analyzer,
    )

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

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
        written_transition_txt = write_transition_recommendations_from_result(
            result=result,
            project_path=project_path,
            output_path=output_transition_txt,
        )

    return output_json, written_music_txt, written_structure_txt, written_transition_txt


def build_project_sequence_music_first_payload(
    *,
    project_path: Path,
    sequence_name: str,
    output_dir: Path,
    recommend_sequence: bool = False,
    max_sampled_clips: int = 12,
    max_analyzed_clips: int | None = None,
    scene_model: str | None = None,
    settings: Settings | None = None,
    analyzer: SceneAnalyzer | None = None,
) -> tuple[SequenceOptimizationResult, dict[str, object]]:
    settings = settings or Settings()
    settings.ensure_output()
    scene_analyzer = analyzer or _default_scene_analyzer

    selected_sequence_name, clips = parse_premiere_project_sequence_visual_clips(project_path, sequence_name)
    analysis_clips, selection_strategy = _select_analysis_clips(
        clips,
        recommend_sequence=recommend_sequence,
        max_sampled_clips=max_sampled_clips,
        max_analyzed_clips=max_analyzed_clips,
    )
    sample_dir = output_dir / f"{project_path.stem}_{_slugify_filename(selected_sequence_name)}_sample_frames"
    sample_dir.mkdir(parents=True, exist_ok=True)

    analyzed_records: list[AnalyzedSequenceClipRecord] = []
    candidates: list[SequenceCandidate] = []
    warnings: list[str] = [
        (
            "Recommendations were built directly from the Premiere project and sequence because no prior optimization JSON "
            "or stage-based regeneration assets were provided."
        ),
        f"Total visual clips in sequence: {len(clips)}.",
        f"Visual clips selected for scene analysis: {len(analysis_clips)}.",
    ]
    if recommend_sequence:
        warnings.append(
            "Sequence order and transition recommendations are based on the analyzed clips collected directly from the Premiere sequence."
        )
        if max_analyzed_clips is None:
            warnings.append("Full recommendation mode analyzed all currently discoverable visual clips in the sequence.")
        elif len(clips) > len(analysis_clips):
            warnings.append(
                "Sequence order and transition recommendations were capped to a representative clip subset. "
                "Use a higher --max-analyzed-clips value or omit it to analyze the full sequence."
            )
    else:
        warnings.append(
            "Music-first mode used representative sampling so it can describe a new sequence quickly before deeper analysis."
        )

    for analyzed_index, clip in enumerate(analysis_clips, start=1):
        source_path = Path(clip.source_path) if clip.source_path else None
        if source_path is None or not source_path.exists():
            warnings.append(
                f"Skipped clip {clip.order_index} ({clip.name}) because the source media file was not found: {clip.source_path or '<missing>'}."
            )
            continue

        try:
            analysis_frame_path, sample_seconds, sample_mode = extract_representative_media_frame(
                clip,
                sample_dir=sample_dir,
            )
        except Exception as exc:
            warnings.append(
                f"Skipped clip {clip.order_index} ({clip.name}) because frame extraction failed: {exc}"
            )
            continue

        try:
            analysis = scene_analyzer(analysis_frame_path, scene_model)
        except Exception as exc:
            warnings.append(
                f"Skipped clip {clip.order_index} ({clip.name}) because scene analysis failed: {exc}"
            )
            continue

        candidate = _build_sequence_candidate_from_scene_analysis(
            clip=clip,
            analysis=analysis,
            sample_mode=sample_mode,
        )
        candidates.append(candidate)
        analyzed_records.append(
            AnalyzedSequenceClipRecord(
                analyzed_index=analyzed_index,
                original_clip_index=clip.order_index,
                clip_name=clip.name,
                source_path=str(source_path),
                analysis_frame_path=str(analysis_frame_path),
                sample_seconds=sample_seconds,
                sample_mode=sample_mode,
            )
        )

    if not candidates:
        raise ValueError(
            "Unable to build a project+sequence recommendation because none of the selected clips could be sampled and analyzed."
        )

    entries = (
        _build_recommended_sequence_entries(candidates)
        if recommend_sequence
        else _build_current_sequence_entries(candidates)
    )
    engine_name = "project-sequence-only-full-recommendations" if recommend_sequence else "project-sequence-only-music-first"
    result = SequenceOptimizationResult(
        source_xml=str(project_path),
        selected_sequence_name=selected_sequence_name,
        engine_requested=engine_name,
        engine_used=engine_name,
        warnings=warnings,
        entries=entries,
        feature_flags={
            "project_sequence_only": True,
            "music_first": True,
            "representative_sampling": not recommend_sequence,
            "sequence_recommendations": recommend_sequence,
            "transition_recommendations": recommend_sequence,
        },
    )
    record_payload = [record.to_dict() for record in analyzed_records]
    payload = result.to_dict()
    payload.update(
        {
            "mode": "project_sequence_music_first",
            "total_sequence_clip_count": len(clips),
            "sampled_clip_count": len(record_payload),
            "sampled_clips": record_payload,
            "analyzed_clip_count": len(record_payload),
            "analyzed_clips": record_payload,
            "sampled_clip_selection_strategy": selection_strategy,
            "analyzed_clip_selection_strategy": selection_strategy,
            "scene_model": scene_model or "",
            "sequence_recommendations_included": recommend_sequence,
            "recommended_sequence_order": _serialize_recommended_sequence_order(entries),
        }
    )
    return result, payload


def extract_representative_media_frame(
    clip: PremiereSequenceClip,
    *,
    sample_dir: Path,
) -> tuple[Path, float | None, str]:
    source_path = Path(clip.source_path)
    if not source_path.exists():
        raise FileNotFoundError(f"Source media file not found: {source_path}")

    output_path = sample_dir / f"{clip.stage_id}_sample.jpg"
    if is_supported_image_media_path(str(source_path)):
        _prepare_still_image_for_analysis(source_path, output_path)
        return output_path, None, "image_source"
    if is_supported_video_media_path(str(source_path)):
        sample_seconds = _extract_video_frame_for_analysis(source_path, output_path, clip=clip)
        return output_path, sample_seconds, "video_frame"
    raise ValueError(f"Unsupported visual media format for sampling: {source_path.suffix or '<none>'}")


def _prepare_still_image_for_analysis(source_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as image:
        prepared = image.convert("RGB")
        prepared.thumbnail((1600, 1600))
        prepared.save(output_path, format="JPEG", quality=90)


def _extract_video_frame_for_analysis(
    source_path: Path,
    output_path: Path,
    *,
    clip: PremiereSequenceClip,
) -> float:
    if cv2 is None:
        raise RuntimeError(
            "opencv-python-headless is required to sample representative frames from video files."
        )

    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video source: {source_path}")

    try:
        sample_seconds = _derive_clip_sample_seconds(clip)
        capture.set(cv2.CAP_PROP_POS_MSEC, sample_seconds * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frame_count > 0:
                capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_count // 2))
                ok, frame = capture.read()
        if not ok or frame is None:
            raise RuntimeError(f"Could not decode a representative frame from: {source_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output_path), frame):
            raise RuntimeError(f"Could not write sampled frame to: {output_path}")
        return sample_seconds
    finally:
        capture.release()


def _derive_clip_sample_seconds(clip: PremiereSequenceClip) -> float:
    in_point = max(0, clip.in_point)
    out_point = max(in_point, clip.out_point)
    if out_point > in_point:
        midpoint = in_point + ((out_point - in_point) / 2.0)
        return max(0.0, midpoint / PREMIERE_TICKS_PER_SECOND)
    if clip.duration > 0:
        return max(0.0, (in_point + (clip.duration / 2.0)) / PREMIERE_TICKS_PER_SECOND)
    return max(0.0, in_point / PREMIERE_TICKS_PER_SECOND)


def _build_sequence_candidate_from_scene_analysis(
    *,
    clip: PremiereSequenceClip,
    analysis: SceneAnalysis,
    sample_mode: str,
) -> SequenceCandidate:
    scene_payload = analysis.to_dict()
    assets = ClipAssetBundle(
        stage_id=clip.stage_id,
        bundle_dir=str(Path(clip.source_path).parent if clip.source_path else ""),
        scene_analysis=scene_payload,
        prompt_text="",
        manifest={
            "source_path": clip.source_path,
            "mode": "project_sequence_only",
            "sample_mode": sample_mode,
        },
    )
    keywords = _runtime_collect_keywords(clip, assets)
    people_count = max(0, int(analysis.people_count))
    shot_scale = _runtime_infer_shot_scale(assets)
    energy_level = _runtime_infer_energy_level(keywords)
    series_subject_tokens, series_appearance_tokens, series_pose_tokens = _runtime_infer_series_subject_features(
        clip,
        assets,
    )
    main_character_priority, main_character_age_hint, main_character_notes = _runtime_infer_main_character_priority(assets)
    opening_score = _runtime_score_opening_candidate(
        clip=clip,
        assets=assets,
        people_count=people_count,
        shot_scale=shot_scale,
        energy_level=energy_level,
        main_character_priority=main_character_priority,
    )
    return SequenceCandidate(
        clip=clip,
        assets=assets,
        keywords=keywords,
        people_count=people_count,
        shot_scale=shot_scale,
        energy_level=energy_level,
        series_subject_tokens=series_subject_tokens,
        series_appearance_tokens=series_appearance_tokens,
        series_pose_tokens=series_pose_tokens,
        main_character_priority=main_character_priority,
        opening_score=opening_score,
        main_character_age_hint=main_character_age_hint,
        main_character_notes=main_character_notes,
        continuity_notes=[f"Analyzed directly from Premiere sequence position {clip.order_index} via {sample_mode}."],
    )


def _build_current_sequence_entries(
    candidates: list[SequenceCandidate],
) -> list[SequenceRecommendationEntry]:
    ordered_candidates = sorted(candidates, key=lambda candidate: candidate.clip.order_index)
    return [
        SequenceRecommendationEntry(
            recommended_index=index,
            original_index=candidate.clip.order_index,
            score=1.0,
            reason="Current sequence clip analyzed directly from the Premiere project.",
            candidate=candidate,
        )
        for index, candidate in enumerate(ordered_candidates, start=1)
    ]


def _build_recommended_sequence_entries(
    candidates: list[SequenceCandidate],
) -> list[SequenceRecommendationEntry]:
    keyword_document_frequency = _keyword_document_frequency(candidates)
    ordered_payload = optimize_sequence_with_heuristic(
        candidates,
        keyword_document_frequency,
        enable_subject_series_grouping=True,
    )
    entries: list[SequenceRecommendationEntry] = []
    for recommended_index, item in enumerate(ordered_payload, start=1):
        candidate = item["candidate"]
        entries.append(
            SequenceRecommendationEntry(
                recommended_index=recommended_index,
                original_index=candidate.clip.order_index,
                score=float(item["score"]),
                reason=str(item["reason"]),
                candidate=candidate,
            )
        )
    return entries


def _serialize_recommended_sequence_order(
    entries: list[SequenceRecommendationEntry],
) -> list[dict[str, object]]:
    return [
        {
            "recommended_index": entry.recommended_index,
            "original_index": entry.original_index,
            "score": entry.score,
            "reason": entry.reason,
            "clip_name": entry.candidate.clip.name,
            "source_path": entry.candidate.clip.source_path,
            "stage_id": entry.candidate.clip.stage_id,
        }
        for entry in entries
    ]


def _select_analysis_clips(
    clips: list[PremiereSequenceClip],
    *,
    recommend_sequence: bool,
    max_sampled_clips: int,
    max_analyzed_clips: int | None,
) -> tuple[list[PremiereSequenceClip], str]:
    if recommend_sequence:
        if max_analyzed_clips is None:
            return list(clips), "all-current-order-clips"
        return (
            _select_uniform_clips(clips, max_selected_clips=max_analyzed_clips),
            "uniform-current-order-sampling",
        )
    return (
        _select_uniform_clips(clips, max_selected_clips=max_sampled_clips),
        "uniform-current-order-sampling" if len(clips) > max_sampled_clips else "all-current-order-clips",
    )


def _select_uniform_clips(
    clips: list[PremiereSequenceClip],
    *,
    max_selected_clips: int,
) -> list[PremiereSequenceClip]:
    if max_selected_clips < 1:
        raise ValueError("max_selected_clips must be >= 1.")
    if len(clips) <= max_selected_clips:
        return list(clips)
    selected_indexes: list[int] = []
    for position in range(max_selected_clips):
        raw_index = round(position * (len(clips) - 1) / max(1, max_selected_clips - 1))
        if raw_index not in selected_indexes:
            selected_indexes.append(raw_index)
    cursor = 0
    while len(selected_indexes) < max_selected_clips and cursor < len(clips):
        if cursor not in selected_indexes:
            selected_indexes.append(cursor)
        cursor += 1
    selected_indexes.sort()
    return [clips[index] for index in selected_indexes]


def _default_scene_analyzer(image_path: Path, model: str | None) -> SceneAnalysis:
    return analyze_scene_with_openai(image_path, model=model, language="ru")


def _slugify_filename(value: str) -> str:
    compact = re.sub(r"\s+", "_", value.strip())
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", compact)
    return normalized.strip("._-") or "sequence"
