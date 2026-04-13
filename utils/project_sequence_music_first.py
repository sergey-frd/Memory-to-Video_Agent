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
from utils.sequence_structure_report import write_sequence_music_report

try:
    import cv2
except ImportError:  # pragma: no cover - runtime guard
    cv2 = None  # type: ignore[assignment]


SceneAnalyzer = Callable[[Path, str | None], SceneAnalysis]


@dataclass(frozen=True)
class SampledSequenceClip:
    sampled_index: int
    original_clip_index: int
    clip_name: str
    source_path: str
    analysis_frame_path: str
    sample_seconds: float | None
    sample_mode: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sampled_index": self.sampled_index,
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


def write_project_sequence_music_first_bundle(
    *,
    project_path: Path,
    sequence_name: str,
    output_json: Path,
    output_music_txt: Path,
    max_sampled_clips: int = 12,
    scene_model: str | None = None,
    settings: Settings | None = None,
    analyzer: SceneAnalyzer | None = None,
) -> tuple[Path, Path]:
    result, payload = build_project_sequence_music_first_payload(
        project_path=project_path,
        sequence_name=sequence_name,
        output_dir=output_json.parent,
        max_sampled_clips=max_sampled_clips,
        scene_model=scene_model,
        settings=settings,
        analyzer=analyzer,
    )

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    output_music_txt.parent.mkdir(parents=True, exist_ok=True)
    write_sequence_music_report(result, output_path=output_music_txt)
    return output_json, output_music_txt


def build_project_sequence_music_first_payload(
    *,
    project_path: Path,
    sequence_name: str,
    output_dir: Path,
    max_sampled_clips: int = 12,
    scene_model: str | None = None,
    settings: Settings | None = None,
    analyzer: SceneAnalyzer | None = None,
) -> tuple[SequenceOptimizationResult, dict[str, object]]:
    settings = settings or Settings()
    settings.ensure_output()
    scene_analyzer = analyzer or _default_scene_analyzer

    selected_sequence_name, clips = parse_premiere_project_sequence_visual_clips(project_path, sequence_name)
    sampled_clips = _select_sampled_clips(clips, max_sampled_clips=max_sampled_clips)
    sample_dir = output_dir / f"{project_path.stem}_{_slugify_filename(selected_sequence_name)}_sample_frames"
    sample_dir.mkdir(parents=True, exist_ok=True)

    entries: list[SequenceRecommendationEntry] = []
    sampled_records: list[SampledSequenceClip] = []
    warnings: list[str] = [
        (
            "Music-first report built from representative sampled clips because no prior optimization JSON or "
            "stage-based regeneration assets were provided."
        ),
        f"Total visual clips in sequence: {len(clips)}.",
        f"Representative clips selected for scene analysis: {len(sampled_clips)}.",
    ]

    for sampled_index, clip in enumerate(sampled_clips, start=1):
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

        entries.append(
            _build_sequence_entry_from_scene_analysis(
                clip=clip,
                analysis=analysis,
                sampled_index=sampled_index,
            )
        )
        sampled_records.append(
            SampledSequenceClip(
                sampled_index=sampled_index,
                original_clip_index=clip.order_index,
                clip_name=clip.name,
                source_path=str(source_path),
                analysis_frame_path=str(analysis_frame_path),
                sample_seconds=sample_seconds,
                sample_mode=sample_mode,
            )
        )

    if not entries:
        raise ValueError(
            "Unable to build a music-first sequence report because none of the selected clips could be sampled and analyzed."
        )

    result = SequenceOptimizationResult(
        source_xml=str(project_path),
        selected_sequence_name=selected_sequence_name,
        engine_requested="project-sequence-only-music-first",
        engine_used="project-sequence-only-music-first",
        warnings=warnings,
        entries=entries,
        feature_flags={
            "project_sequence_only": True,
            "music_first": True,
            "representative_sampling": True,
        },
    )
    payload = result.to_dict()
    payload.update(
        {
            "mode": "project_sequence_music_first",
            "total_sequence_clip_count": len(clips),
            "sampled_clip_count": len(sampled_records),
            "sampled_clips": [record.to_dict() for record in sampled_records],
            "sampled_clip_selection_strategy": (
                "uniform-current-order-sampling"
                if len(clips) > len(sampled_records)
                else "all-current-order-clips"
            ),
            "scene_model": scene_model or "",
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


def _build_sequence_entry_from_scene_analysis(
    *,
    clip: PremiereSequenceClip,
    analysis: SceneAnalysis,
    sampled_index: int,
) -> SequenceRecommendationEntry:
    scene_payload = analysis.to_dict()
    assets = ClipAssetBundle(
        stage_id=clip.stage_id,
        bundle_dir=str(Path(clip.source_path).parent if clip.source_path else ""),
        scene_analysis=scene_payload,
        prompt_text="",
        manifest={
            "source_path": clip.source_path,
            "mode": "project_sequence_only",
        },
    )
    candidate = SequenceCandidate(
        clip=clip,
        assets=assets,
        keywords=_collect_keywords(clip, analysis),
        people_count=max(0, analysis.people_count),
        shot_scale=_infer_shot_scale(analysis),
        energy_level=_infer_energy_level(analysis),
        series_subject_tokens=_collect_series_subject_tokens(analysis),
        series_appearance_tokens=_collect_series_appearance_tokens(analysis),
        series_pose_tokens=_collect_series_pose_tokens(analysis),
        main_character_priority=_infer_main_character_priority(analysis),
        opening_score=_infer_opening_score(analysis),
        main_character_notes=_collect_main_character_notes(analysis),
        continuity_notes=[f"Representative sampled clip from sequence position {clip.order_index}."],
    )
    return SequenceRecommendationEntry(
        recommended_index=sampled_index,
        original_index=clip.order_index,
        score=1.0,
        reason="Representative current-order clip sampled directly from the Premiere sequence.",
        candidate=candidate,
    )


def _collect_keywords(clip: PremiereSequenceClip, analysis: SceneAnalysis) -> list[str]:
    parts: list[str] = [
        clip.name,
        analysis.summary,
        analysis.background,
        analysis.shot_type,
        analysis.main_action,
        *analysis.mood,
        *analysis.relationships,
    ]
    for person in analysis.people:
        parts.extend(
            [
                person.label,
                person.role_in_scene,
                person.apparent_age_group,
                person.apparent_gender_presentation,
                person.face_visibility,
                person.facial_expression,
                person.clothing,
                person.pose,
            ]
        )
    tokens = [
        token.lower()
        for part in parts
        for token in re.findall(r"[0-9A-Za-zА-Яа-яЁё']{3,}", part or "")
    ]
    deduped: list[str] = []
    for token in tokens:
        if token not in deduped:
            deduped.append(token)
    return deduped


def _infer_shot_scale(analysis: SceneAnalysis) -> int:
    shot = (analysis.shot_type or "").lower()
    if any(fragment in shot for fragment in ("detail", "macro", "extreme close", "деталь", "макро")):
        return 3
    if any(fragment in shot for fragment in ("close", "portrait", "close-up", "круп", "портрет")):
        return 2
    if any(fragment in shot for fragment in ("medium", "waist", "mid shot", "средн", "полусредн")):
        return 1
    return 0


def _infer_energy_level(analysis: SceneAnalysis) -> int:
    phrases = " ".join(
        [
            analysis.main_action,
            " ".join(analysis.mood),
            " ".join(person.pose for person in analysis.people if person.pose),
        ]
    ).lower()
    high_fragments = (
        "dance",
        "dancing",
        "run",
        "running",
        "jump",
        "party",
        "celebrat",
        "laugh",
        "sing",
        "танц",
        "бег",
        "прыж",
        "празд",
        "смех",
    )
    low_fragments = (
        "pose",
        "posing",
        "sit",
        "sitting",
        "stand",
        "standing",
        "looking",
        "portrait",
        "сид",
        "стоит",
        "смотр",
        "портрет",
        "спокой",
    )
    if any(fragment in phrases for fragment in high_fragments):
        return 3
    if any(fragment in phrases for fragment in low_fragments):
        return 1
    return 2


def _collect_series_subject_tokens(analysis: SceneAnalysis) -> list[str]:
    tokens: list[str] = []
    for person in analysis.people:
        for value in (person.label, person.role_in_scene, person.apparent_age_group):
            text = (value or "").strip()
            if text and text not in tokens:
                tokens.append(text)
    return tokens


def _collect_series_appearance_tokens(analysis: SceneAnalysis) -> list[str]:
    tokens: list[str] = []
    for person in analysis.people:
        for value in (person.clothing, person.facial_expression):
            text = (value or "").strip()
            if text and text not in tokens:
                tokens.append(text)
    background = (analysis.background or "").strip()
    if background:
        tokens.append(background)
    return tokens


def _collect_series_pose_tokens(analysis: SceneAnalysis) -> list[str]:
    tokens: list[str] = []
    for person in analysis.people:
        pose = (person.pose or "").strip()
        if pose and pose not in tokens:
            tokens.append(pose)
    action = (analysis.main_action or "").strip()
    if action and action not in tokens:
        tokens.append(action)
    return tokens


def _infer_main_character_priority(analysis: SceneAnalysis) -> float:
    if analysis.people_count <= 0:
        return 0.0
    if analysis.people_count == 1:
        return 1.0
    return 0.6


def _infer_opening_score(analysis: SceneAnalysis) -> float:
    shot_scale = _infer_shot_scale(analysis)
    score = 0.0
    if shot_scale == 0:
        score += 1.4
    if analysis.background:
        score += 0.8
    if analysis.people_count <= 2:
        score += 0.3
    return round(score, 3)


def _collect_main_character_notes(analysis: SceneAnalysis) -> list[str]:
    notes: list[str] = []
    if analysis.people_count == 1:
        notes.append("single person focus")
    if _infer_shot_scale(analysis) >= 2:
        notes.append("close portrait emphasis")
    for person in analysis.people:
        age = (person.apparent_age_group or "").lower()
        if any(fragment in age for fragment in ("child", "kid", "boy", "girl", "реб", "девоч", "мальч")):
            notes.append("child visible in frame")
            break
    return list(dict.fromkeys(notes))


def _select_sampled_clips(
    clips: list[PremiereSequenceClip],
    *,
    max_sampled_clips: int,
) -> list[PremiereSequenceClip]:
    if max_sampled_clips < 1:
        raise ValueError("max_sampled_clips must be >= 1.")
    if len(clips) <= max_sampled_clips:
        return list(clips)
    selected_indexes: list[int] = []
    for position in range(max_sampled_clips):
        raw_index = round(position * (len(clips) - 1) / max(1, max_sampled_clips - 1))
        if raw_index not in selected_indexes:
            selected_indexes.append(raw_index)
    cursor = 0
    while len(selected_indexes) < max_sampled_clips and cursor < len(clips):
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


from utils.project_sequence_reports_from_project import (
    build_project_sequence_music_first_payload as build_project_sequence_music_first_payload,
    derive_project_sequence_music_first_bundle_paths as derive_project_sequence_music_first_bundle_paths,
    derive_project_sequence_music_first_paths as derive_project_sequence_music_first_paths,
    extract_representative_media_frame as extract_representative_media_frame,
    write_project_sequence_music_first_bundle as write_project_sequence_music_first_bundle,
)
