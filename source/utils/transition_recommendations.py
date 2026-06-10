from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from models.video_sequence import SequenceOptimizationResult, SequenceRecommendationEntry
from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    is_supported_visual_media_path,
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
    "additive_dissolve": RecommendedTransitionType(
        key="additive_dissolve",
        display_name="Additive Dissolve",
        summary="Use for bright emotional bridges, highlights, celebrations, or a luminous memory feel.",
    ),
    "blur_dissolve": RecommendedTransitionType(
        key="blur_dissolve",
        display_name="Blur Dissolve",
        summary="Use when related shots should blend softly without a hard edge.",
    ),
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
    "luma_fade": RecommendedTransitionType(
        key="luma_fade",
        display_name="Luma Fade",
        summary="Use for soft light-based transitions between similarly toned frames.",
    ),
    "non_additive_dissolve": RecommendedTransitionType(
        key="non_additive_dissolve",
        display_name="Non-Additive Dissolve",
        summary="Use as a restrained dissolve when Cross Dissolve feels too generic.",
    ),
    "dip_to_white": RecommendedTransitionType(
        key="dip_to_white",
        display_name="Dip to White",
        summary="Use for bright memory flashes, daylight scene resets, or airy emotional beats.",
    ),
    "linear_wipe": RecommendedTransitionType(
        key="linear_wipe",
        display_name="Linear Wipe",
        summary="Use for a clean directional change between different compositions.",
    ),
    "soft_wipe": RecommendedTransitionType(
        key="soft_wipe",
        display_name="Soft Wipe",
        summary="Use for gentle image-to-image movement where a dissolve is too static.",
    ),
    "radial_wipe": RecommendedTransitionType(
        key="radial_wipe",
        display_name="Radial Wipe",
        summary="Use for circular or central composition changes.",
    ),
    "iris_round": RecommendedTransitionType(
        key="iris_round",
        display_name="Iris Round",
        summary="Use for subject-centered reveals when the image has a clear central focus.",
    ),
    "push": RecommendedTransitionType(
        key="push",
        display_name="Push",
        summary="Use for forward motion, travel, or a clear narrative step into the next shot.",
    ),
    "slide": RecommendedTransitionType(
        key="slide",
        display_name="Slide",
        summary="Use for lateral movement between related stills or image/video pairs.",
    ),
    "whip": RecommendedTransitionType(
        key="whip",
        display_name="Whip",
        summary="Use sparingly for high-energy action or fast event changes.",
    ),
    "cross_zoom": RecommendedTransitionType(
        key="cross_zoom",
        display_name="Cross Zoom",
        summary="Use when moving from wide to close, close to wide, or between energetic beats.",
    ),
    "zoom_blur": RecommendedTransitionType(
        key="zoom_blur",
        display_name="Zoom Blur",
        summary="Use sparingly for energetic scale changes or dynamic emphasis.",
    ),
    "light_leak": RecommendedTransitionType(
        key="light_leak",
        display_name="Light Leak",
        summary="Use for warm memory, sunlight, travel, celebration, or nostalgic overlays.",
    ),
    "glow": RecommendedTransitionType(
        key="glow",
        display_name="Glow",
        summary="Use for soft luminous moments, beauty, celebration, or dreamy emotional peaks.",
    ),
    "glitch": RecommendedTransitionType(
        key="glitch",
        display_name="Glitch",
        summary="Use only for intentionally modern, digital, chaotic, or abrupt material.",
    ),
}

_ADDITIONAL_RECOMMENDED_TRANSITION_TYPE_SPECS = {
    "barn_doors": ("Barn Doors", "Use for a theatrical reveal or a clear two-panel composition change."),
    "center_split": ("Center Split", "Use for symmetric frames or a split from center-focused material."),
    "clock_wipe": ("Clock Wipe", "Use for time-passing, travel, or an intentional circular directional change."),
    "inset": ("Inset", "Use for a framed reveal when the next shot should enter as a clear insert."),
    "iris_box": ("Iris Box", "Use for box-shaped reveals around architecture, rooms, or framed subjects."),
    "iris_cross": ("Iris Cross", "Use sparingly for graphic, central, or stylized composition changes."),
    "iris_diamond": ("Iris Diamond", "Use for a more decorative central reveal."),
    "neon_wipe": ("Neon Wipe", "Use for modern, bright, digital, or party-like material."),
    "page_peel": ("Page Peel", "Use for album, document, memory-book, or chapter-turning moments."),
    "panel_wipe": ("Panel Wipe", "Use for structured or architectural scene changes."),
    "plateau_wipe": ("Plateau Wipe", "Use for a graphic wipe between clearly different compositions."),
    "shape_flow": ("Shape Flow", "Use for organic graphic movement in stylized sequences."),
    "slice": ("Slice", "Use for energetic or modern scene changes with clear motion."),
    "star_wipe": ("Star Wipe", "Use very sparingly for playful celebration or deliberately retro beats."),
    "stretch_wipe": ("Stretch Wipe", "Use for elastic movement between related action frames."),
    "wipe": ("Wipe", "Use for a clean directional transition when a dissolve is too passive."),
    "three_d_roll": ("3D Roll", "Use for strong motion, travel, or playful high-energy transitions."),
    "three_d_spin": ("3D Spin", "Use sparingly for playful or energetic high-motion moments."),
    "three_d_spinback": ("3D Spinback", "Use sparingly for energetic return/reversal moments."),
    "block_motion": ("Block Motion", "Use for graphic, modern, or rhythmic edits."),
    "film_roll": ("Film Roll", "Use for analog memory, archive, or playful film-strip movement."),
    "flip_motion": ("Flip Motion", "Use for energetic before/after or orientation-changing moments."),
    "motion_camera": ("Motion Camera", "Use when the edit should feel like a camera move between shots."),
    "motion_tween": ("Motion Tween", "Use for smooth graphic motion between related stills."),
    "pop_motion": ("Pop Motion", "Use sparingly for upbeat, playful, or celebratory edits."),
    "pull_motion": ("Pull Motion", "Use for pull-back, exit, or widening narrative movement."),
    "roll": ("Roll", "Use for fast directional motion or travel beats."),
    "spin_motion": ("Spin Motion", "Use sparingly for lively, playful, or party-like changes."),
    "split": ("Split", "Use for structured changes between similar layouts."),
    "spring_motion": ("Spring Motion", "Use for playful movement with elastic energy."),
    "stretch": ("Stretch", "Use for energetic motion where the image can tolerate distortion."),
    "travel_motion": ("Travel Motion", "Use for trip, movement, or location-shift sequences."),
    "burn_alpha": ("Burn Alpha", "Use for hot, dramatic, or memory-flash changes."),
    "burn_chroma": ("Burn Chroma", "Use for colorful, dramatic, or high-energy scene changes."),
    "chaos": ("Chaos", "Use only for intentionally chaotic or highly stylized material."),
    "chroma_leak": ("Chroma Leak", "Use for colorful modern transitions or digital memory effects."),
    "directional_blur": ("Directional Blur", "Use for fast motion where the transition should smear directionally."),
    "earthquake": ("Earthquake", "Use only for intense, unstable, or deliberately disruptive moments."),
    "flare": ("Flare", "Use for sunlight, celebration, or bright emotional changes."),
    "flash": ("Flash", "Use for camera-flash, memory jump, or bright scene reset moments."),
    "flicker": ("Flicker", "Use for archive, retro, or unstable-light material."),
    "glass": ("Glass", "Use for reflective or stylized scene changes."),
    "grunge": ("Grunge", "Use only for intentionally rough, distressed, or gritty material."),
    "kaleidoscope": ("Kaleidoscope", "Use only for playful, abstract, or highly stylized changes."),
    "lens_blur": ("Lens Blur", "Use for photographic refocus or soft dreamy motion."),
    "light_sweep": ("Light Sweep", "Use for bright, polished, or celebratory image changes."),
    "liquid_distortion": ("Liquid Distortion", "Use only for fluid or heavily stylized motion."),
    "mosaic": ("Mosaic", "Use for digital, privacy, or stylized blocky transitions."),
    "phosphor": ("Phosphor", "Use for retro-screen or electronic glow effects."),
    "radial_blur": ("Radial Blur", "Use for energetic center-focused zoom/motion changes."),
    "ray": ("Ray", "Use for luminous or spiritual-looking scene changes."),
    "solarize": ("Solarize", "Use only for strong experimental or graphic color shifts."),
    "stripe": ("Stripe", "Use for graphic, rhythmic, or patterned changes."),
    "tv_power": ("TV Power", "Use for screen-like shutdown/opening or retro electronic beats."),
    "vhs_damage": ("VHS Damage", "Use for archive, retro, tape, or intentionally damaged media."),
}

_RECOMMENDED_TRANSITION_TYPES.update(
    {
        key: RecommendedTransitionType(key=key, display_name=display_name, summary=summary)
        for key, (display_name, summary) in _ADDITIONAL_RECOMMENDED_TRANSITION_TYPE_SPECS.items()
    }
)

_DISABLED_AUTOMATIC_TRANSITIONS = {
    "Morph Cut": "disabled for automatic application because Premiere can fail with 'Can't apply to a single clip' when clip handles or analysis conditions are not suitable",
}

_SOFT_TRANSITION_POOL = (
    "film_dissolve",
    "blur_dissolve",
    "non_additive_dissolve",
    "additive_dissolve",
    "luma_fade",
)
_SCENE_RESET_TRANSITION_POOL = ("dip_to_black", "dip_to_white")
_CONTEXT_TRANSITION_POOL = (
    "soft_wipe",
    "linear_wipe",
    "radial_wipe",
    "iris_round",
    "iris_box",
    "iris_diamond",
    "barn_doors",
    "center_split",
    "panel_wipe",
    "wipe",
    "page_peel",
    "shape_flow",
)
_MOTION_TRANSITION_POOL = (
    "push",
    "slide",
    "cross_zoom",
    "whip",
    "zoom_blur",
    "travel_motion",
    "motion_camera",
    "motion_tween",
    "pull_motion",
    "roll",
    "flip_motion",
    "stretch_wipe",
    "slice",
    "radial_blur",
)
_STYLIZED_TRANSITION_POOL = (
    "light_leak",
    "glow",
    "glitch",
    "flare",
    "flash",
    "flicker",
    "chroma_leak",
    "light_sweep",
    "lens_blur",
    "burn_alpha",
    "burn_chroma",
    "vhs_damage",
    "neon_wipe",
    "film_roll",
)


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


def write_transition_recommendations_from_result(
    *,
    result: SequenceOptimizationResult,
    output_path: Path,
    project_path: Path | None = None,
) -> Path:
    report_text = build_transition_recommendations_from_result(
        result=result,
        project_path=project_path,
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
        pure_visual_track = True

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
            if not is_supported_visual_media_path(clip_name):
                pure_visual_track = False
                break
            stage_id = resolve_project_track_item_stage_id(track_item_node, object_id_lookup)
            candidate_key = stage_id if stage_id in candidate_by_stage_id else track_item_node.attrib.get("ObjectID")
            if not candidate_key or candidate_key not in candidate_by_stage_id:
                pure_visual_track = False
                break
            ordered_items.append((candidate_key, track_item_node, Path(clip_name).name))

        if not pure_visual_track or len(ordered_items) < 2:
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
            lines.append("- No contiguous clip pairs were found on this pure visual track.")
        lines.append("")

    if recommended_tracks == 0:
        lines.extend(
            [
                "No pure visual track with at least two clips was found for transition recommendations.",
                "",
            ]
        )

    return "\n".join(lines).strip() + "\n"


def build_transition_recommendations_from_result(
    *,
    result: SequenceOptimizationResult,
    project_path: Path | None = None,
) -> str:
    template_duration = _resolve_transition_template_duration(project_path)
    lines = [
        "RECOMMENDED TRANSITIONS FOR THE PROPOSED SEQUENCE ORDER",
        "",
        f"Sequence source: {result.source_xml}",
        f"Sequence: {result.selected_sequence_name}",
        "Mode: text recommendations only, based on the optimized order derived directly from the current Premiere sequence.",
        "",
        "Supported recommendation types",
        "",
    ]
    lines.extend(_format_transition_catalog_lines())
    lines.append("")
    lines.extend(
        [
            "Recommended clip pairs",
            "",
        ]
    )

    if len(result.entries) < 2:
        lines.extend(
            [
                "No transition recommendation can be produced because fewer than two analyzed clips were available.",
                "",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    for previous_entry, current_entry in zip(result.entries, result.entries[1:]):
        if previous_entry.transition_to_next is not None:
            transition_name = previous_entry.transition_to_next.transition_name
            duration = previous_entry.transition_to_next.recommended_duration
            transition_reason = previous_entry.transition_to_next.reason
            media_pair = previous_entry.transition_to_next.media_pair
        else:
            previous_candidate = _candidate_namespace_from_entry(previous_entry)
            current_candidate = _candidate_namespace_from_entry(current_entry)
            transition_type, transition_reason = _select_recommended_transition_type(
                previous_candidate,
                current_candidate,
            )
            duration = _adjust_recommended_duration_for_transition_type(
                transition_type.key,
                _choose_transition_duration(
                    previous_candidate,
                    current_candidate,
                    previous_duration=max(2, previous_entry.candidate.clip.duration),
                    current_duration=max(2, current_entry.candidate.clip.duration),
                    template_duration=template_duration,
                ),
                template_duration=template_duration,
            )
            transition_name = transition_type.display_name
            media_pair = "visual->visual"
        previous_name = Path(previous_entry.candidate.clip.name).name or previous_entry.candidate.clip.stage_id
        current_name = Path(current_entry.candidate.clip.name).name or current_entry.candidate.clip.stage_id
        lines.append(
            (
                f"- #{previous_entry.recommended_index} (orig {previous_entry.original_index}) {previous_name} -> "
                f"#{current_entry.recommended_index} (orig {current_entry.original_index}) {current_name}: "
                f"{transition_name}, recommended duration {duration}, media {media_pair}. {transition_reason}. "
                "Feasibility should be checked in Premiere after applying the recommended order."
            )
        )
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def _format_transition_catalog_lines() -> list[str]:
    lines: list[str] = []
    for transition_type in _RECOMMENDED_TRANSITION_TYPES.values():
        lines.append(f"- {transition_type.display_name}: {transition_type.summary}")
    if _DISABLED_AUTOMATIC_TRANSITIONS:
        lines.extend(["", "Disabled for automatic application"])
        for display_name, reason in _DISABLED_AUTOMATIC_TRANSITIONS.items():
            lines.append(f"- {display_name}: {reason}.")
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
    high_energy_bridge = (
        max(previous_candidate.energy_level, current_candidate.energy_level) >= 2
        or _text_has_any(
            merged_text,
            {
                "action",
                "dance",
                "dancing",
                "run",
                "running",
                "jump",
                "travel",
                "moving",
                "walking",
                "party",
                "celebrate",
                "celebrating",
                "танец",
                "танц",
                "бег",
                "движ",
                "празд",
            },
        )
    )
    stylized_bridge = _text_has_any(
        merged_text,
        {
            "glitch",
            "digital",
            "modern",
            "neon",
            "vhs",
            "light leak",
            "flash",
            "flare",
            "glow",
            "retro",
            "party",
        },
    )
    scale_shift = abs(previous_candidate.shot_scale - current_candidate.shot_scale) >= 2
    wide_context_bridge = (
        min(previous_candidate.shot_scale, current_candidate.shot_scale) <= 0
        or _text_has_any(
            merged_text,
            {
                "wide",
                "establishing",
                "landscape",
                "panorama",
                "sea",
                "beach",
                "street",
                "city",
                "park",
                "room",
                "garden",
            },
        )
    )
    hard_scene_break = (
        not strong_subject_continuity
        and len(keyword_overlap) <= 1
        and (
            scale_shift
            or abs(previous_candidate.people_count - current_candidate.people_count) >= 2
            or _text_has_any(merged_text, _SCENE_BREAK_HINTS)
        )
    )

    if portrait_face_continuity:
        return _pick_transition_from_pool(
            _SOFT_TRANSITION_POOL,
            previous_candidate,
            current_candidate,
            "rule: same-person or same-look continuity uses a safe soft transition; Morph Cut is intentionally excluded from automatic application",
        )
    if hard_scene_break:
        return _pick_transition_from_pool(
            _SCENE_RESET_TRANSITION_POOL,
            previous_candidate,
            current_candidate,
            "rule: strong scene or tone break suggests a reset transition instead of a neutral blend",
        )
    if stylized_bridge and high_energy_bridge:
        return _pick_transition_from_pool(
            _STYLIZED_TRANSITION_POOL,
            previous_candidate,
            current_candidate,
            "rule: modern, bright, or stylized high-energy language can use a stronger template transition",
        )
    if high_energy_bridge or scale_shift:
        return _pick_transition_from_pool(
            _MOTION_TRANSITION_POOL,
            previous_candidate,
            current_candidate,
            "rule: action or scale change benefits from a directional or zoom-family transition",
        )
    if wide_context_bridge and not strong_subject_continuity:
        return _pick_transition_from_pool(
            _CONTEXT_TRANSITION_POOL,
            previous_candidate,
            current_candidate,
            "rule: contextual or wide-frame change can use a gentle wipe/iris-family transition",
        )
    if soft_tonal_bridge:
        return _pick_transition_from_pool(
            _SOFT_TRANSITION_POOL,
            previous_candidate,
            current_candidate,
            "rule: dreamy, nostalgic, beauty, or soft-emotional language suggests a softer cinematic dissolve",
        )
    if strong_subject_continuity or len(keyword_overlap) >= 2:
        return _pick_transition_from_pool(
            ("cross_dissolve", "film_dissolve", "blur_dissolve", "non_additive_dissolve"),
            previous_candidate,
            current_candidate,
            "rule: related shots with readable continuity fit a safe dissolve-family transition",
        )
    return (
        _RECOMMENDED_TRANSITION_TYPES["cross_dissolve"],
        "rule: default to the safest neutral transition when no stronger style signal is present",
    )


def _pick_transition_from_pool(
    pool: tuple[str, ...],
    previous_candidate: SimpleNamespace,
    current_candidate: SimpleNamespace,
    reason: str,
) -> tuple[RecommendedTransitionType, str]:
    available = [key for key in pool if key in _RECOMMENDED_TRANSITION_TYPES]
    if not available:
        return _RECOMMENDED_TRANSITION_TYPES["cross_dissolve"], reason
    fingerprint = "|".join(
        (
            str(getattr(previous_candidate, "stage_id", "")),
            str(getattr(previous_candidate, "clip_name", "")),
            str(getattr(current_candidate, "stage_id", "")),
            str(getattr(current_candidate, "clip_name", "")),
            _candidate_text_blob(previous_candidate)[:160],
            _candidate_text_blob(current_candidate)[:160],
        )
    )
    index = sum(ord(ch) for ch in fingerprint) % len(available)
    selected = _RECOMMENDED_TRANSITION_TYPES[available[index]]
    return selected, f"{reason}; selected from template transition pool: {selected.display_name}"


def _adjust_recommended_duration_for_transition_type(
    transition_type_key: str,
    duration: int,
    *,
    template_duration: int,
) -> int:
    adjusted = max(2, duration)
    if transition_type_key in {"film_dissolve", "blur_dissolve", "luma_fade", "non_additive_dissolve"}:
        adjusted = max(adjusted, max(2, template_duration // 2))
    elif transition_type_key in {"dip_to_black", "dip_to_white"}:
        adjusted = max(adjusted, template_duration)
    elif transition_type_key in {"whip", "glitch", "zoom_blur"}:
        adjusted = min(adjusted, max(2, template_duration // 2))
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
        candidate_payload = SimpleNamespace(
            stage_id=stage_id,
            clip_name=str(clip.get("name") or ""),
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
        candidate_by_stage_id[stage_id] = candidate_payload
        clipitem_id = str(clip.get("clipitem_id") or "").strip()
        if clipitem_id:
            candidate_by_stage_id[clipitem_id] = candidate_payload
    return candidate_by_stage_id


def _candidate_namespace_from_entry(entry: SequenceRecommendationEntry) -> SimpleNamespace:
    scene_analysis = entry.candidate.assets.scene_analysis
    return SimpleNamespace(
        stage_id=entry.candidate.clip.stage_id,
        clip_name=entry.candidate.clip.name,
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


def _resolve_transition_template_duration(
    project_path: Path | None,
    *,
    transition_template_project_path: Path | None = None,
) -> int:
    if transition_template_project_path is not None:
        template_duration = _resolve_transition_template_duration(transition_template_project_path)
        if template_duration > 2:
            return template_duration
    if project_path is None:
        return 2
    try:
        root = load_premiere_project_root(project_path)
        object_id_lookup = build_project_object_id_lookup(root)
        transition_template = _find_video_transition_template(root, object_id_lookup)
        if transition_template is None:
            return 2
        duration = _transition_duration(transition_template)
        if duration > 1_000_000:
            return max(duration, PREMIERE_TICKS_PER_SECOND)
        return duration
    except Exception:
        return 2
