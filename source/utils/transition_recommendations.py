from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from utils.premiere_project import (
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    iter_project_track_item_refs,
    load_premiere_project_root,
    resolve_project_track_item_name,
    resolve_project_track_item_stage_id,
    resolve_project_track_item_timeline,
)
from utils.premiere_project_export import (
    _choose_transition_duration,
    _find_video_transition_template,
    _resolve_track_item_tail_handle,
    _transition_duration,
)

_TRANSITION_MODES = {"disabled", "apply", "recommend_only"}
_SOFT_TONE_HINTS = {
    "beauty",
    "beautiful",
    "cinematic",
    "dream",
    "dreamy",
    "elegant",
    "gentle",
    "glamour",
    "graceful",
    "memory",
    "nostalgia",
    "nostalgic",
    "poetic",
    "romance",
    "romantic",
    "soft",
    "tender",
    "неж",
    "мечт",
    "памят",
    "романт",
}
_SCENE_BREAK_HINTS = {
    "alone",
    "black",
    "blackout",
    "break",
    "contrast",
    "contrastive",
    "dramatic",
    "empty",
    "ending",
    "final",
    "goodbye",
    "isolated",
    "night",
    "reset",
    "silhouette",
    "один",
    "прощ",
    "темн",
    "финал",
}
_FACE_CONTINUITY_HINTS = {
    "baby",
    "boy",
    "child",
    "close",
    "close-up",
    "eye",
    "eyes",
    "face",
    "girl",
    "look",
    "man",
    "portrait",
    "smile",
    "woman",
    "круп",
    "лицо",
    "портрет",
    "улыб",
}


@dataclass(frozen=True)
class RecommendedTransitionType:
    key: str
    display_name: str
    summary: str


_RECOMMENDED_TRANSITION_TYPES = {
    "cross_dissolve": RecommendedTransitionType(
        key="cross_dissolve",
        display_name="Cross Dissolve (Legacy)",
        summary="Default continuity transition for related shots when we want a neutral, safe blend.",
    ),
    "dip_to_black": RecommendedTransitionType(
        key="dip_to_black",
        display_name="Dip to Black",
        summary="Use for a hard scene break, tonal reset, ending beat, or a strong shift in place or energy.",
    ),
    "film_dissolve": RecommendedTransitionType(
        key="film_dissolve",
        display_name="Film Dissolve",
        summary="Use for dreamy, nostalgic, beauty, or poetic transitions where softness matters more than literal continuity.",
    ),
    "morph_cut": RecommendedTransitionType(
        key="morph_cut",
        display_name="Morph Cut",
        summary="Use for same-person facial continuity when framing and appearance stay very close but pose or expression changes.",
    ),
}


def normalize_transition_mode(raw_mode: object, *, enable_auto_transitions: bool = False) -> str:
    mode = str(raw_mode or "").strip().casefold()
    if not mode:
        return "apply" if enable_auto_transitions else "disabled"
    if mode not in _TRANSITION_MODES:
        allowed = ", ".join(sorted(_TRANSITION_MODES))
        raise ValueError(f"Unsupported transition_mode '{raw_mode}'. Expected one of: {allowed}")
    return mode


def write_transition_recommendations_report(
    *,
    project_path: Path,
    sequence_name: str,
    optimization_report_json: Path,
    output_path: Path,
) -> Path:
    optimization_payload = json.loads(optimization_report_json.read_text(encoding="utf-8"))
    report_text = build_transition_recommendations_report(
        project_path=project_path,
        sequence_name=sequence_name,
        optimization_payload=optimization_payload,
    )
    output_path.write_text(report_text, encoding="utf-8")
    return output_path


def build_transition_recommendations_report(
    *,
    project_path: Path,
    sequence_name: str,
    optimization_payload: dict[str, object],
) -> str:
    root = load_premiere_project_root(project_path)
    object_id_lookup = build_project_object_id_lookup(root)
    object_uid_lookup = build_project_object_uid_lookup(root)
    sequence_node = find_project_sequence_node(root, sequence_name)
    if sequence_node is None:
        raise ValueError(f"Sequence '{sequence_name}' was not found in project: {project_path}")

    transition_template = _find_video_transition_template(root, object_id_lookup)
    template_duration = _transition_duration(transition_template) if transition_template is not None else 2
    candidate_by_stage_id = _candidate_payload_by_stage_id(optimization_payload)

    lines = [
        "TRANSITION RECOMMENDATIONS",
        "",
        f"Project: {project_path}",
        f"Sequence: {sequence_name}",
        "Mode: text recommendations only, no transitions are inserted into .prproj in recommend_only mode.",
        "",
        "Supported recommendation types",
        "",
    ]
    lines.extend(_format_transition_catalog_lines())
    lines.append("")

    recommended_tracks = 0
    for track_index, track_node in get_project_track_nodes(
        sequence_node,
        track_group_index=0,
        object_id_lookup=object_id_lookup,
        object_uid_lookup=object_uid_lookup,
    ):
        ordered_items: list[tuple[str, object, str]] = []
        pure_mp4_track = True

        for track_item_ref in iter_project_track_item_refs(track_node):
            object_ref = track_item_ref.attrib.get("ObjectRef")
            if not object_ref:
                continue
            track_item_node = object_id_lookup.get(object_ref)
            if track_item_node is None:
                continue
            clip_name = resolve_project_track_item_name(track_item_node, object_id_lookup)
            if not clip_name:
                continue
            if not clip_name.lower().endswith(".mp4"):
                pure_mp4_track = False
                break
            stage_id = resolve_project_track_item_stage_id(track_item_node, object_id_lookup)
            if stage_id is None or stage_id not in candidate_by_stage_id:
                pure_mp4_track = False
                break
            ordered_items.append((stage_id, track_item_node, Path(clip_name).name))

        if not pure_mp4_track or len(ordered_items) < 2:
            continue

        recommended_tracks += 1
        lines.extend([f"Track {track_index + 1}", ""])

        contiguous_pairs = 0
        for item_index in range(len(ordered_items) - 1):
            previous_stage_id, previous_track_item, previous_name = ordered_items[item_index]
            current_stage_id, current_track_item, current_name = ordered_items[item_index + 1]
            previous_start, previous_end = resolve_project_track_item_timeline(previous_track_item)
            current_start, current_end = resolve_project_track_item_timeline(current_track_item)

            if previous_end != current_start:
                lines.append(
                    f"- {previous_name} -> {current_name}: no transition recommendation because a gap of {current_start - previous_end} remains."
                )
                continue

            contiguous_pairs += 1
            previous_candidate = candidate_by_stage_id[previous_stage_id]
            current_candidate = candidate_by_stage_id[current_stage_id]
            transition_type, transition_reason = _select_recommended_transition_type(
                previous_candidate,
                current_candidate,
            )
            duration = _adjust_recommended_duration_for_transition_type(
                transition_type.key,
                _choose_transition_duration(
                    previous_candidate,
                    current_candidate,
                    previous_duration=previous_end - previous_start,
                    current_duration=current_end - current_start,
                    template_duration=template_duration,
                ),
                template_duration=template_duration,
            )
            tail_handle = _resolve_track_item_tail_handle(previous_track_item, object_id_lookup)
            if tail_handle is None:
                feasibility = "tail handle unavailable in source metadata"
            elif tail_handle >= duration:
                feasibility = f"can be applied without trimming (tail handle {tail_handle})"
            else:
                feasibility = (
                    f"would require trimming or longer source media (tail handle {tail_handle}, recommended {duration})"
                )

            lines.append(
                f"- {previous_name} -> {current_name}: {transition_type.display_name}, recommended duration {duration}. {transition_reason}. {feasibility}."
            )

        if contiguous_pairs == 0:
            lines.append("- No contiguous clip pairs were found on this pure mp4 track.")
        lines.append("")

    if recommended_tracks == 0:
        lines.extend(
            [
                "No pure mp4 video track with at least two clips was found for transition recommendations.",
                "",
            ]
        )

    return "\n".join(lines).strip() + "\n"


def _format_transition_catalog_lines() -> list[str]:
    lines: list[str] = []
    for transition_type in _RECOMMENDED_TRANSITION_TYPES.values():
        lines.append(f"- {transition_type.display_name}: {transition_type.summary}")
    return lines


def _select_recommended_transition_type(
    previous_candidate: SimpleNamespace,
    current_candidate: SimpleNamespace,
) -> tuple[RecommendedTransitionType, str]:
    previous_keywords = set(previous_candidate.keywords)
    current_keywords = set(current_candidate.keywords)
    keyword_overlap = previous_keywords & current_keywords
    subject_overlap = set(previous_candidate.series_subject_tokens) & set(current_candidate.series_subject_tokens)
    appearance_overlap = set(previous_candidate.series_appearance_tokens) & set(current_candidate.series_appearance_tokens)
    merged_text = f"{_candidate_text_blob(previous_candidate)} {_candidate_text_blob(current_candidate)}".strip()

    strong_subject_continuity = (
        len(appearance_overlap) >= 2
        or len(subject_overlap) >= 2
        or (bool(appearance_overlap) and bool(subject_overlap))
    )
    portrait_face_continuity = (
        previous_candidate.people_count > 0
        and current_candidate.people_count > 0
        and max(previous_candidate.shot_scale, current_candidate.shot_scale) >= 1
        and abs(previous_candidate.shot_scale - current_candidate.shot_scale) <= 1
        and strong_subject_continuity
        and _text_has_any(merged_text, _FACE_CONTINUITY_HINTS)
    )
    soft_tonal_bridge = _text_has_any(merged_text, _SOFT_TONE_HINTS) and not _text_has_any(
        merged_text,
        _SCENE_BREAK_HINTS,
    )
    hard_scene_break = (
        not strong_subject_continuity
        and len(keyword_overlap) <= 1
        and (
            abs(previous_candidate.shot_scale - current_candidate.shot_scale) >= 2
            or abs(previous_candidate.people_count - current_candidate.people_count) >= 2
            or _text_has_any(merged_text, _SCENE_BREAK_HINTS)
        )
    )

    if portrait_face_continuity:
        return (
            _RECOMMENDED_TRANSITION_TYPES["morph_cut"],
            "rule: same-person or same-look continuity with similar framing suggests a face-preserving smoothing transition",
        )
    if hard_scene_break:
        return (
            _RECOMMENDED_TRANSITION_TYPES["dip_to_black"],
            "rule: strong scene or tone break suggests a reset transition instead of a neutral blend",
        )
    if soft_tonal_bridge:
        return (
            _RECOMMENDED_TRANSITION_TYPES["film_dissolve"],
            "rule: dreamy, nostalgic, beauty, or soft-emotional language suggests a softer cinematic dissolve",
        )
    if strong_subject_continuity or len(keyword_overlap) >= 2:
        return (
            _RECOMMENDED_TRANSITION_TYPES["cross_dissolve"],
            "rule: related shots with readable continuity fit a neutral dissolve best",
        )
    return (
        _RECOMMENDED_TRANSITION_TYPES["cross_dissolve"],
        "rule: default to the safest neutral transition when no stronger style signal is present",
    )


def _adjust_recommended_duration_for_transition_type(
    transition_type_key: str,
    duration: int,
    *,
    template_duration: int,
) -> int:
    adjusted = max(2, duration)
    if transition_type_key == "morph_cut":
        adjusted = min(adjusted, max(2, template_duration // 2))
    elif transition_type_key == "film_dissolve":
        adjusted = max(adjusted, max(2, template_duration // 2))
    elif transition_type_key == "dip_to_black":
        adjusted = max(adjusted, template_duration)
    if adjusted % 2 != 0:
        adjusted -= 1
    return max(adjusted, 2)


def _candidate_text_blob(candidate: SimpleNamespace) -> str:
    values = [
        candidate.summary,
        candidate.background,
        candidate.shot_type_text,
        candidate.main_action,
        " ".join(candidate.mood),
        " ".join(candidate.relationships),
        candidate.prompt_text,
        " ".join(candidate.keywords),
    ]
    return " ".join(str(value) for value in values if value).casefold()


def _text_has_any(text: str, variants: set[str]) -> bool:
    return any(variant in text for variant in variants)


def _candidate_payload_by_stage_id(optimization_payload: dict[str, object]) -> dict[str, SimpleNamespace]:
    candidate_by_stage_id: dict[str, SimpleNamespace] = {}
    for entry in optimization_payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        candidate = entry.get("candidate") or {}
        if not isinstance(candidate, dict):
            continue
        clip = candidate.get("clip") or {}
        if not isinstance(clip, dict):
            continue
        assets = candidate.get("assets") or {}
        if not isinstance(assets, dict):
            assets = {}
        scene_analysis = assets.get("scene_analysis") or {}
        if not isinstance(scene_analysis, dict):
            scene_analysis = {}
        stage_id = str(clip.get("stage_id") or "").strip()
        if not stage_id:
            continue
        candidate_by_stage_id[stage_id] = SimpleNamespace(
            series_subject_tokens=list(candidate.get("series_subject_tokens") or []),
            series_appearance_tokens=list(candidate.get("series_appearance_tokens") or []),
            keywords=list(candidate.get("keywords") or []),
            shot_scale=int(candidate.get("shot_scale") or 0),
            people_count=int(candidate.get("people_count") or 0),
            energy_level=int(candidate.get("energy_level") or 0),
            summary=str(scene_analysis.get("summary") or ""),
            background=str(scene_analysis.get("background") or ""),
            shot_type_text=str(scene_analysis.get("shot_type") or ""),
            main_action=str(scene_analysis.get("main_action") or ""),
            mood=[str(item) for item in (scene_analysis.get("mood") or []) if item],
            relationships=[str(item) for item in (scene_analysis.get("relationships") or []) if item],
            prompt_text=str(assets.get("prompt_text") or ""),
        )
    return candidate_by_stage_id
