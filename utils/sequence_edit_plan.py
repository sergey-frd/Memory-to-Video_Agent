from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from models.video_sequence import (
    SequenceClipEditPlan,
    SequenceOptimizationResult,
    SequenceRecommendationEntry,
    SequenceTransitionPlan,
)
from utils.premiere_project import (
    is_supported_image_media_path,
    is_supported_video_media_path,
)
from utils.premiere_project_export import _choose_transition_duration
from utils.transition_recommendations import (
    _RECOMMENDED_TRANSITION_TYPES,
    _adjust_recommended_duration_for_transition_type,
    _select_recommended_transition_type,
)


def attach_sequence_edit_plan(
    result: SequenceOptimizationResult,
    *,
    enable_auto_durations: bool = False,
    transition_template_duration: int = 2,
) -> SequenceOptimizationResult:
    entries = result.entries
    for entry in entries:
        entry.edit_plan = build_clip_edit_plan(entry, enable_auto_durations=enable_auto_durations)

    for entry, next_entry in zip(entries, entries[1:]):
        entry.transition_to_next = build_transition_plan(
            entry,
            next_entry,
            transition_template_duration=transition_template_duration,
        )
    if entries:
        entries[-1].transition_to_next = None

    result.feature_flags["enable_auto_durations"] = enable_auto_durations
    result.feature_flags["sequence_edit_plan"] = True
    return result


def build_clip_edit_plan(
    entry: SequenceRecommendationEntry,
    *,
    enable_auto_durations: bool,
) -> SequenceClipEditPlan:
    candidate = entry.candidate
    original_duration = max(1, int(candidate.clip.duration or 1))
    media_kind = infer_media_kind(candidate.clip.source_path, candidate.clip.name)

    if not enable_auto_durations:
        return SequenceClipEditPlan(
            media_kind=media_kind,
            original_duration=original_duration,
            recommended_duration=original_duration,
            duration_reason="auto duration disabled; preserving the source timeline duration",
        )

    if media_kind == "image":
        duration, reason = _recommend_image_duration(entry)
    elif media_kind == "video":
        duration, reason = _recommend_video_duration(entry)
    else:
        duration, reason = original_duration, "unknown visual media type; preserving original duration"

    return SequenceClipEditPlan(
        media_kind=media_kind,
        original_duration=original_duration,
        recommended_duration=max(1, duration),
        duration_reason=reason,
    )


def build_transition_plan(
    entry: SequenceRecommendationEntry,
    next_entry: SequenceRecommendationEntry,
    *,
    transition_template_duration: int,
) -> SequenceTransitionPlan:
    previous_candidate = _candidate_namespace_from_entry(entry)
    current_candidate = _candidate_namespace_from_entry(next_entry)
    transition_type, transition_reason = _select_recommended_transition_type(
        previous_candidate,
        current_candidate,
    )
    media_pair = f"{infer_media_kind(entry.candidate.clip.source_path, entry.candidate.clip.name)}->{infer_media_kind(next_entry.candidate.clip.source_path, next_entry.candidate.clip.name)}"
    transition_type, transition_reason = _select_media_pair_fallback_transition(
        entry,
        next_entry,
        media_pair=media_pair,
        transition_type=transition_type,
        transition_reason=transition_reason,
    )
    previous_duration = _planned_duration(entry)
    current_duration = _planned_duration(next_entry)
    duration = _adjust_recommended_duration_for_transition_type(
        transition_type.key,
        _choose_transition_duration(
            previous_candidate,
            current_candidate,
            previous_duration=previous_duration,
            current_duration=current_duration,
            template_duration=max(2, transition_template_duration),
        ),
        template_duration=max(2, transition_template_duration),
    )
    return SequenceTransitionPlan(
        to_recommended_index=next_entry.recommended_index,
        to_original_index=next_entry.original_index,
        to_stage_id=next_entry.candidate.clip.stage_id,
        to_clip_name=Path(next_entry.candidate.clip.name).name or next_entry.candidate.clip.stage_id,
        media_pair=media_pair,
        transition_key=transition_type.key,
        transition_name=transition_type.display_name,
        recommended_duration=max(2, duration),
        reason=transition_reason,
    )


def infer_media_kind(source_path: str, clip_name: str) -> str:
    media_identity = source_path or clip_name
    if is_supported_image_media_path(media_identity):
        return "image"
    if is_supported_video_media_path(media_identity):
        return "video"
    if is_supported_image_media_path(clip_name):
        return "image"
    if is_supported_video_media_path(clip_name):
        return "video"
    return "visual"


def _recommend_image_duration(entry: SequenceRecommendationEntry) -> tuple[int, str]:
    candidate = entry.candidate
    original_duration = max(1, int(candidate.clip.duration or 1))
    multiplier = 1.0
    reasons: list[str] = []

    if candidate.shot_scale <= 0:
        multiplier += 0.20
        reasons.append("wide or establishing image needs more reading time")
    elif candidate.shot_scale >= 2:
        multiplier += 0.08
        reasons.append("portrait/detail image benefits from a slightly held beat")

    if candidate.people_count >= 3:
        multiplier += 0.12
        reasons.append("group image needs time to scan faces")
    elif candidate.people_count == 1:
        multiplier += 0.05
        reasons.append("single-subject image can hold a quiet portrait beat")

    if candidate.main_character_priority >= 2.0:
        multiplier += 0.08
        reasons.append("main character is visually important")

    if candidate.energy_level >= 2:
        multiplier -= 0.14
        reasons.append("higher visual energy supports a shorter hold")

    multiplier = max(0.80, min(1.45, multiplier))
    duration = _round_duration(original_duration * multiplier)
    return duration, "; ".join(reasons) or "balanced still-image hold"


def _recommend_video_duration(entry: SequenceRecommendationEntry) -> tuple[int, str]:
    candidate = entry.candidate
    original_duration = max(1, int(candidate.clip.duration or 1))
    multiplier = 1.0
    reasons: list[str] = []

    if candidate.energy_level >= 2:
        multiplier -= 0.08
        reasons.append("high-energy video fragment can be slightly tighter")
    if candidate.shot_scale <= 0 and candidate.assets.scene_analysis.get("background"):
        multiplier += 0.06
        reasons.append("establishing video fragment keeps context readable")
    if candidate.main_character_priority >= 2.0:
        multiplier += 0.03
        reasons.append("important character moment should not be over-trimmed")

    multiplier = max(0.85, min(1.0, multiplier))
    duration = _round_duration(original_duration * multiplier)
    return duration, "; ".join(reasons) or "preserving video fragment duration"


def _round_duration(value: float) -> int:
    rounded = int(round(value))
    if rounded > 2 and rounded % 2 != 0:
        rounded += 1
    return max(1, rounded)


def _planned_duration(entry: SequenceRecommendationEntry) -> int:
    if entry.edit_plan is not None:
        return max(1, int(entry.edit_plan.recommended_duration))
    return max(1, int(entry.candidate.clip.duration or 1))


def _candidate_namespace_from_entry(entry: SequenceRecommendationEntry) -> SimpleNamespace:
    scene_analysis = entry.candidate.assets.scene_analysis
    return SimpleNamespace(
        series_subject_tokens=list(entry.candidate.series_subject_tokens),
        series_appearance_tokens=list(entry.candidate.series_appearance_tokens),
        keywords=list(entry.candidate.keywords),
        shot_scale=int(entry.candidate.shot_scale),
        people_count=int(entry.candidate.people_count),
        energy_level=int(entry.candidate.energy_level),
        summary=str(scene_analysis.get("summary") or ""),
        background=str(scene_analysis.get("background") or ""),
        shot_type_text=str(scene_analysis.get("shot_type") or ""),
        main_action=str(scene_analysis.get("main_action") or ""),
        mood=[str(item) for item in (scene_analysis.get("mood") or []) if item],
        relationships=[str(item) for item in (scene_analysis.get("relationships") or []) if item],
        prompt_text=str(entry.candidate.assets.prompt_text or ""),
    )


def _select_media_pair_fallback_transition(
    entry: SequenceRecommendationEntry,
    next_entry: SequenceRecommendationEntry,
    *,
    media_pair: str,
    transition_type,
    transition_reason: str,
):
    if _has_scene_signal(entry) or _has_scene_signal(next_entry):
        return transition_type, transition_reason
    if transition_type.key != "cross_dissolve":
        return transition_type, transition_reason

    same_source = _same_source_media(entry, next_entry)
    if media_pair == "image->image":
        return (
            _RECOMMENDED_TRANSITION_TYPES["film_dissolve"],
            "rule: image-to-image slideshow pair without richer scene metadata uses a soft cinematic dissolve",
        )
    if media_pair == "image->video":
        return (
            _RECOMMENDED_TRANSITION_TYPES["cross_dissolve"],
            "rule: still image into motion should remain a clean continuity dissolve",
        )
    if media_pair == "video->image":
        return (
            _RECOMMENDED_TRANSITION_TYPES["dip_to_black"],
            "rule: motion-to-still pair without richer scene metadata benefits from a short visual reset",
        )
    if media_pair == "video->video" and same_source:
        return (
            _RECOMMENDED_TRANSITION_TYPES["morph_cut"],
            "rule: adjacent fragments from the same video source can use Morph Cut as a continuity bridge",
        )
    return transition_type, transition_reason


def _has_scene_signal(entry: SequenceRecommendationEntry) -> bool:
    candidate = entry.candidate
    scene_analysis = candidate.assets.scene_analysis
    return bool(
        candidate.keywords
        or scene_analysis.get("summary")
        or scene_analysis.get("background")
        or scene_analysis.get("shot_type")
        or scene_analysis.get("main_action")
        or scene_analysis.get("mood")
        or candidate.assets.prompt_text
    )


def _same_source_media(entry: SequenceRecommendationEntry, next_entry: SequenceRecommendationEntry) -> bool:
    previous_identity = entry.candidate.clip.source_path or entry.candidate.clip.name
    current_identity = next_entry.candidate.clip.source_path or next_entry.candidate.clip.name
    return bool(previous_identity and current_identity and previous_identity == current_identity)
