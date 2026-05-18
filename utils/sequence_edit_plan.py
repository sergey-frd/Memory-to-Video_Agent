from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from models.video_sequence import (
    SequenceClipEditPlan,
    SequenceOptimizationResult,
    SequenceRecommendationEntry,
    SequenceTransformPlan,
    SequenceTransitionPlan,
)
from utils.premiere_project import (
    is_supported_image_media_path,
    is_supported_video_media_path,
)
from utils.premiere_project_export import _choose_transition_duration
from utils.image_analysis import analyze_image
from utils.transition_recommendations import (
    _RECOMMENDED_TRANSITION_TYPES,
    _adjust_recommended_duration_for_transition_type,
    _select_recommended_transition_type,
)

_AUTOMATIC_TRANSFORM_NAMES = ("Grow", "Shrink", "Move")
_DISABLED_AUTOMATIC_TRANSFORMS = {
    "Offset": "manual-only because Adobe Premiere Offset often needs frame-by-frame position tuning",
}


def attach_sequence_edit_plan(
    result: SequenceOptimizationResult,
    *,
    enable_auto_durations: bool = False,
    enable_auto_transforms: bool = False,
    transition_template_duration: int = 2,
) -> SequenceOptimizationResult:
    entries = result.entries
    for entry in entries:
        entry.edit_plan = build_clip_edit_plan(entry, enable_auto_durations=enable_auto_durations)

    for index, entry in enumerate(entries):
        previous_entry = entries[index - 1] if index > 0 else None
        next_entry = entries[index + 1] if index + 1 < len(entries) else None
        entry.transform_plan = build_transform_plan(
            entry,
            enable_auto_transforms=enable_auto_transforms,
            previous_entry=previous_entry,
            next_entry=next_entry,
        )

    for entry, next_entry in zip(entries, entries[1:]):
        entry.transition_to_next = build_transition_plan(
            entry,
            next_entry,
            transition_template_duration=transition_template_duration,
        )
    if entries:
        entries[-1].transition_to_next = None

    result.feature_flags["enable_auto_durations"] = enable_auto_durations
    result.feature_flags["enable_auto_transforms"] = enable_auto_transforms
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


def build_transform_plan(
    entry: SequenceRecommendationEntry,
    *,
    enable_auto_transforms: bool,
    previous_entry: SequenceRecommendationEntry | None = None,
    next_entry: SequenceRecommendationEntry | None = None,
) -> SequenceTransformPlan | None:
    media_kind = infer_media_kind(entry.candidate.clip.source_path, entry.candidate.clip.name)
    if not enable_auto_transforms or media_kind != "image":
        return None

    candidate = entry.candidate
    scene_text = _scene_signal_text(entry)
    image_metadata = _load_image_metadata(candidate.clip.source_path or candidate.clip.name)
    scores, reasons = _score_transform_options(entry, previous_entry, next_entry, scene_text)
    if image_metadata is not None:
        _score_image_metadata(scores, reasons, image_metadata)
    selected_name = max(
        _AUTOMATIC_TRANSFORM_NAMES,
        key=lambda name: (scores[name], _transform_tie_break_rank(name)),
    )
    return _transform_plan_from_name(
        selected_name,
        media_kind=media_kind,
        transform_key=f"{selected_name.casefold()}_content_neighbor",
        reason="; ".join(reasons[selected_name]) or "content-and-neighbor transform selection",
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


def _score_transform_options(
    entry: SequenceRecommendationEntry,
    previous_entry: SequenceRecommendationEntry | None,
    next_entry: SequenceRecommendationEntry | None,
    scene_text: str,
) -> tuple[dict[str, float], dict[str, list[str]]]:
    candidate = entry.candidate
    scores = {name: 1.0 for name in _AUTOMATIC_TRANSFORM_NAMES}
    reasons: dict[str, list[str]] = {name: [] for name in scores}

    if candidate.people_count >= 3:
        scores["Shrink"] += 8.0
        reasons["Shrink"].append("group photo: gentle pull-back keeps all faces readable")
    elif candidate.people_count == 1:
        scores["Grow"] += 3.5
        reasons["Grow"].append("single-subject photo: slow push-in supports portrait focus")
    elif candidate.people_count == 2:
        scores["Grow"] += 1.5
        scores["Move"] += 1.0
        reasons["Grow"].append("two-person photo: mild push-in keeps the relationship central")

    if candidate.shot_scale >= 2 or (candidate.people_count <= 2 and _has_portrait_signal(scene_text)):
        scores["Grow"] += 4.0
        reasons["Grow"].append("close or portrait framing benefits from subtle emphasis")
    elif candidate.shot_scale <= 0 or _has_wide_context_signal(scene_text):
        scores["Shrink"] += 3.0
        scores["Move"] += 2.0
        reasons["Shrink"].append("wide/contextual framing uses a gentle pull-back because Offset is manual-only")
        reasons["Move"].append("wide/contextual framing can use small movement without Offset position tuning")

    if candidate.energy_level >= 2 or _has_motion_signal(scene_text):
        scores["Move"] += 5.0
        reasons["Move"].append("action or high-energy still gets a small movement cue")
    elif candidate.energy_level == 0 and _has_calm_signal(scene_text):
        scores["Move"] += 0.8
        reasons["Move"].append("calm scene favors a quiet movement cue while Offset remains manual-only")

    if candidate.main_character_priority >= 2.0:
        scores["Grow"] += 2.0
        reasons["Grow"].append("main character priority suggests drawing attention inward")

    if not _has_scene_signal(entry):
        scores["Grow"] += 0.8
        reasons["Grow"].append("limited metadata: opening/neutral still can accept a gentle push-in")
        if previous_entry is not None:
            scores["Move"] += 1.2
            reasons["Move"].append("limited metadata: neighbor context favors variation over fixed cycling")

    _score_neighbor_context(
        scores,
        reasons,
        entry=entry,
        neighbor=previous_entry,
        direction="previous",
    )
    _score_neighbor_context(
        scores,
        reasons,
        entry=entry,
        neighbor=next_entry,
        direction="next",
    )

    previous_transform_name = (
        previous_entry.transform_plan.transform_name
        if previous_entry is not None and previous_entry.transform_plan is not None
        else ""
    )
    if previous_transform_name in scores:
        scores[previous_transform_name] -= 4.0
        reasons[_complement_transform_name(previous_transform_name)].append(
            f"neighbor rhythm: avoid repeating previous {previous_transform_name}"
        )
        scores[_complement_transform_name(previous_transform_name)] += 3.0
        for alternate_name in _secondary_complements(previous_transform_name):
            scores[alternate_name] += 1.0
            reasons[alternate_name].append(f"neighbor rhythm: alternate after previous {previous_transform_name}")

    return scores, reasons


def _score_image_metadata(
    scores: dict[str, float],
    reasons: dict[str, list[str]],
    image_metadata,
) -> None:
    orientation = str(getattr(image_metadata, "orientation", "") or "").casefold()
    composition = str(getattr(image_metadata, "composition_label", "") or "").casefold()
    depth = str(getattr(image_metadata, "depth_label", "") or "").casefold()

    if orientation == "portrait":
        scores["Grow"] += 2.0
        reasons["Grow"].append("image pixels: portrait orientation supports a gentle push-in")
    elif orientation == "landscape":
        scores["Move"] += 1.2
        scores["Shrink"] += 0.8
        reasons["Move"].append("image pixels: landscape orientation gets subtle movement because Offset is manual-only")

    if "subject-forward" in composition:
        scores["Grow"] += 2.5
        reasons["Grow"].append("image pixels: subject-forward composition suggests portrait emphasis")
    elif "environment-forward" in composition:
        scores["Shrink"] += 2.0
        scores["Move"] += 1.0
        reasons["Shrink"].append("image pixels: environment-forward composition should preserve context without Offset")
    elif "detail-rich" in composition:
        scores["Move"] += 1.2
        reasons["Move"].append("image pixels: detail-rich frame benefits from small movement")

    if "dense textured" in depth:
        scores["Move"] += 0.8
        reasons["Move"].append("image pixels: textured depth can carry subtle movement")


@lru_cache(maxsize=512)
def _load_image_metadata(path_text: str):
    if not path_text:
        return None
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        return None
    try:
        return analyze_image(path)
    except Exception:
        return None


def _score_neighbor_context(
    scores: dict[str, float],
    reasons: dict[str, list[str]],
    *,
    entry: SequenceRecommendationEntry,
    neighbor: SequenceRecommendationEntry | None,
    direction: str,
) -> None:
    if neighbor is None:
        return
    neighbor_kind = infer_media_kind(neighbor.candidate.clip.source_path, neighbor.candidate.clip.name)
    current = entry.candidate
    other = neighbor.candidate
    if neighbor_kind == "video":
        scores["Move"] += 1.8
        reasons["Move"].append(f"{direction} neighbor is video: still image should bridge into/out of motion")
    elif neighbor_kind == "image":
        scores["Move"] += 0.6
        reasons["Move"].append(f"{direction} neighbor is also an image: quiet movement helps slideshow continuity")

    shot_delta = current.shot_scale - other.shot_scale
    if shot_delta >= 2:
        scores["Grow"] += 1.8
        reasons["Grow"].append(f"current image is tighter than {direction} neighbor: emphasize the closer beat")
    elif shot_delta <= -2:
        scores["Shrink"] += 1.8
        scores["Move"] += 0.8
        reasons["Shrink"].append(f"current image is wider than {direction} neighbor: preserve surrounding context")

    if _entries_are_visually_related(entry, neighbor):
        scores["Move"] += 1.6
        scores["Grow"] -= 0.8
        reasons["Move"].append(f"visually related {direction} neighbor: small movement avoids repeated zooms")

    if abs(current.people_count - other.people_count) >= 2:
        scores["Shrink"] += 1.2
        reasons["Shrink"].append(f"people-count contrast with {direction} neighbor: pull-back helps reset scale")


def _entries_are_visually_related(
    entry: SequenceRecommendationEntry,
    other_entry: SequenceRecommendationEntry,
) -> bool:
    current = entry.candidate
    other = other_entry.candidate
    subject_overlap = set(current.series_subject_tokens) & set(other.series_subject_tokens)
    appearance_overlap = set(current.series_appearance_tokens) & set(other.series_appearance_tokens)
    keyword_overlap = set(current.keywords) & set(other.keywords)
    if subject_overlap or appearance_overlap or len(keyword_overlap) >= 2:
        return True
    current_scene = _scene_signal_text(entry)
    other_scene = _scene_signal_text(other_entry)
    if not current_scene or not other_scene:
        return False
    context_tokens = {"park", "beach", "sea", "room", "street", "family", "wedding", "birthday", "garden"}
    return any(token in current_scene and token in other_scene for token in context_tokens)


def _complement_transform_name(previous_transform_name: str) -> str:
    return {
        "Grow": "Shrink",
        "Shrink": "Grow",
        "Move": "Grow",
        "Offset": "Move",
    }.get(previous_transform_name, "Move")


def _secondary_complements(previous_transform_name: str) -> tuple[str, ...]:
    return {
        "Grow": ("Move",),
        "Shrink": ("Move",),
        "Move": ("Grow",),
        "Offset": ("Grow",),
    }.get(previous_transform_name, ("Move",))


def _transform_tie_break_rank(name: str) -> int:
    return {
        "Grow": 4,
        "Shrink": 3,
        "Move": 2,
        "Offset": 1,
    }.get(name, 0)


def _transform_plan_from_name(
    name: str,
    *,
    media_kind: str,
    transform_key: str,
    reason: str,
) -> SequenceTransformPlan:
    if name == "Shrink":
        start_scale, end_scale = 106.0, 100.0
    elif name == "Move":
        start_scale, end_scale = 103.0, 105.0
    elif name == "Offset":
        start_scale, end_scale = 104.0, 104.0
    else:
        start_scale, end_scale = 100.0, 105.0
    return SequenceTransformPlan(
        media_kind=media_kind,
        transform_key=transform_key,
        transform_name=name,
        effect_name=name,
        fallback_effect_name="Transform",
        start_scale=start_scale,
        end_scale=end_scale,
        reason=reason,
    )


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
            _RECOMMENDED_TRANSITION_TYPES["cross_dissolve"],
            "rule: adjacent fragments from the same video source use a safe dissolve; Morph Cut is excluded from automatic application",
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


def _scene_signal_text(entry: SequenceRecommendationEntry) -> str:
    scene_analysis = entry.candidate.assets.scene_analysis
    parts = [
        str(scene_analysis.get("summary") or ""),
        str(scene_analysis.get("background") or ""),
        str(scene_analysis.get("shot_type") or ""),
        str(scene_analysis.get("main_action") or ""),
        " ".join(str(item) for item in (scene_analysis.get("mood") or []) if item),
        str(entry.candidate.assets.prompt_text or ""),
    ]
    return " ".join(parts).casefold()


def _has_wide_context_signal(text: str) -> bool:
    return any(
        token in text
        for token in (
            "wide",
            "establishing",
            "landscape",
            "panorama",
            "street",
            "sea",
            "beach",
            "mountain",
            "room",
            "background",
            "outdoor",
            "city",
            "park",
        )
    )


def _has_portrait_signal(text: str) -> bool:
    return any(
        token in text
        for token in (
            "portrait",
            "close-up",
            "close up",
            "face",
            "headshot",
            "selfie",
            "круп",
            "портрет",
            "лицо",
        )
    )


def _has_motion_signal(text: str) -> bool:
    return any(
        token in text
        for token in (
            "dance",
            "dancing",
            "run",
            "running",
            "jump",
            "walking",
            "laughing",
            "celebrating",
            "action",
            "movement",
            "танец",
            "танц",
            "бег",
            "идет",
            "идут",
            "движ",
            "сме",
            "празд",
        )
    )


def _has_calm_signal(text: str) -> bool:
    return any(
        token in text
        for token in (
            "calm",
            "quiet",
            "soft",
            "gentle",
            "still",
            "posed",
            "posing",
            "спокой",
            "тих",
            "мягк",
            "позир",
        )
    )
