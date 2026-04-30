from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from models.video_sequence import (
    ClipAssetBundle,
    PremiereSequenceClip,
    SequenceCandidate,
    SequenceOptimizationResult,
    SequenceRecommendationEntry,
)
from utils.fcp_translation_results import (
    parse_fcp_translation_results,
    resolve_translation_results_path,
    summarize_lost_effects,
)
from utils.premiere_project import extract_stage_id_from_project_media_name


_TOKEN_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9']{3,}")
_AGE_NUMBER_PATTERN = re.compile(r"(\d{1,2})")
_STOPWORDS = {
    "and",
    "the",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "their",
    "about",
    "have",
    "they",
    "them",
    "there",
    "while",
    "were",
    "been",
    "isn't",
    "aren't",
    "это",
    "как",
    "для",
    "она",
    "они",
    "или",
    "при",
    "под",
    "над",
    "без",
    "его",
    "её",
    "themed",
    "video",
    "scene",
    "prompt",
    "camera",
    "background",
    "atmosphere",
    "balanced",
    "around",
    "before",
    "after",
    "slowly",
    "gently",
    "bright",
    "blue",
}
_SHOT_SCALE_RULES = [
    (0, ("establishing", "wide", "long shot", "panoramic", "общий", "дальний", "панорам")),
    (1, ("medium", "waist", "mid shot", "средний", "полусредний")),
    (2, ("close", "portrait", "close-up", "крупный", "портрет")),
    (3, ("detail", "macro", "extreme close", "деталь", "макро")),
]
_HIGH_ENERGY_KEYWORDS = {
    "dance",
    "dancing",
    "run",
    "running",
    "jump",
    "jumping",
    "party",
    "celebrate",
    "celebrating",
    "laugh",
    "laughing",
    "sing",
    "singing",
    "perform",
    "performance",
    "танец",
    "танц",
    "бег",
    "прыж",
    "смех",
    "поет",
    "поёт",
    "выступ",
    "празд",
}
_LOW_ENERGY_KEYWORDS = {
    "pose",
    "posing",
    "sit",
    "sitting",
    "stand",
    "standing",
    "look",
    "looking",
    "hold",
    "holding",
    "portrait",
    "сид",
    "стоит",
    "смотр",
    "держ",
    "портрет",
    "позир",
}
_CENTER_KEYWORDS = ("center", "central", "центр")
_FOREGROUND_KEYWORDS = ("foreground", "front", "передн")
_FULL_FACE_KEYWORDS = ("fully visible", "full face", "полностью виден", "лицом к камере")
_YOUNGEST_KEYWORDS = ("youngest", "younger", "младш", "infant", "baby", "младен")
_INFANT_KEYWORDS = ("infant", "baby", "newborn", "младен", "новорож")
_TODDLER_KEYWORDS = ("toddler", "preschool", "малыш", "дошкол", "раннего возраста")
_CHILD_KEYWORDS = ("child", "kid", "girl", "boy", "ребен", "девочк", "мальчик", "детств")
_TEEN_KEYWORDS = ("teen", "adolescent", "подрост", "юност")
_ADULT_KEYWORDS = ("adult", "woman", "man", "взросл", "женщин", "мужчин")


def build_sequence_candidates(
    clips: list[PremiereSequenceClip],
    regeneration_assets_dir: Path,
) -> tuple[list[SequenceCandidate], list[str]]:
    candidates: list[SequenceCandidate] = []
    warnings: list[str] = []

    for clip in clips:
        assets = load_clip_asset_bundle(regeneration_assets_dir, clip)
        if assets.missing_files:
            warnings.append(f"{clip.name}: missing {', '.join(assets.missing_files)}")

        keywords = _collect_keywords(clip, assets)
        people_count = _safe_int(assets.scene_analysis.get("people_count", 0))
        shot_scale = _infer_shot_scale(assets)
        energy_level = _infer_energy_level(keywords)
        series_subject_tokens, series_appearance_tokens, series_pose_tokens = _infer_series_subject_features(clip, assets)
        main_character_priority, main_character_age_hint, main_character_notes = _infer_main_character_priority(assets)
        opening_score = _score_opening_candidate(
            clip=clip,
            assets=assets,
            people_count=people_count,
            shot_scale=shot_scale,
            energy_level=energy_level,
            main_character_priority=main_character_priority,
        )

        candidates.append(
            SequenceCandidate(
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
            )
        )

    return candidates, warnings


def load_clip_asset_bundle(regeneration_assets_dir: Path, clip: PremiereSequenceClip) -> ClipAssetBundle:
    bundle_dir, resolved_stage_id = _resolve_clip_bundle_dir(regeneration_assets_dir, clip)
    missing_files: list[str] = []
    manifest: dict[str, object] = {}
    scene_analysis: dict[str, object] = {}
    prompt_text = ""

    if not bundle_dir.exists():
        return ClipAssetBundle(
            stage_id=resolved_stage_id,
            bundle_dir=str(bundle_dir),
            missing_files=["bundle_dir", "scene_analysis", "v_prompt", "manifest"],
        )

    manifest_path = bundle_dir / f"{resolved_stage_id}_api_pipeline_manifest.json"
    scene_analysis_path = bundle_dir / f"{resolved_stage_id}_scene_analysis.json"
    prompt_path = bundle_dir / f"{resolved_stage_id}_v_prompt_{clip.video_index}.txt"
    if not prompt_path.exists():
        prompt_candidates = sorted(bundle_dir.glob(f"{resolved_stage_id}_v_prompt_*.txt"))
        if prompt_candidates:
            prompt_path = prompt_candidates[0]

    if manifest_path.exists():
        manifest = _read_json_file(manifest_path)
    else:
        missing_files.append("manifest")

    if scene_analysis_path.exists():
        scene_analysis = _read_json_file(scene_analysis_path)
    else:
        missing_files.append("scene_analysis")

    if prompt_path.exists():
        prompt_text = prompt_path.read_text(encoding="utf-8")
    else:
        missing_files.append("v_prompt")

    return ClipAssetBundle(
        stage_id=resolved_stage_id,
        bundle_dir=str(bundle_dir),
        manifest_path=str(manifest_path) if manifest_path.exists() else None,
        scene_analysis_path=str(scene_analysis_path) if scene_analysis_path.exists() else None,
        prompt_path=str(prompt_path) if prompt_path.exists() else None,
        manifest=manifest,
        scene_analysis=scene_analysis,
        prompt_text=prompt_text,
        missing_files=missing_files,
    )


@lru_cache(maxsize=None)
def _candidate_regeneration_roots(regeneration_assets_dir: str) -> tuple[Path, ...]:
    base_dir = Path(regeneration_assets_dir)
    candidates = [base_dir]
    parent_dir = base_dir.parent
    if not parent_dir.exists():
        return tuple(candidates)

    normalized_base_name = base_dir.name.casefold()
    allowed_prefixes = (
        f"{normalized_base_name}_",
        f"{normalized_base_name}-",
    )
    try:
        sibling_dirs = sorted(
            (
                path
                for path in parent_dir.iterdir()
                if path.is_dir() and path != base_dir
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        return tuple(candidates)

    for path in sibling_dirs:
        normalized_name = path.name.casefold()
        if normalized_name == normalized_base_name or normalized_name.startswith(allowed_prefixes):
            candidates.append(path)
    return tuple(candidates)


def _candidate_stage_ids(clip: PremiereSequenceClip) -> tuple[str, ...]:
    candidates: list[str] = [clip.stage_id]
    for raw_value in (clip.name, clip.source_path):
        stage_id = extract_stage_id_from_project_media_name(raw_value)
        if stage_id and stage_id not in candidates:
            candidates.append(stage_id)
    return tuple(candidates)


def _resolve_clip_bundle_dir(regeneration_assets_dir: Path, clip: PremiereSequenceClip) -> tuple[Path, str]:
    for stage_id in _candidate_stage_ids(clip):
        for root_dir in _candidate_regeneration_roots(str(regeneration_assets_dir)):
            candidate_dir = root_dir / stage_id
            if candidate_dir.exists():
                return candidate_dir, stage_id
    return regeneration_assets_dir / clip.stage_id, clip.stage_id


def optimize_sequence(
    *,
    source_xml: Path,
    selected_sequence_name: str,
    clips: list[PremiereSequenceClip],
    regeneration_assets_dir: Path,
    engine: str = "heuristic",
    translation_results_path: Path | None = None,
    enable_subject_series_grouping: bool = False,
) -> SequenceOptimizationResult:
    candidates, warnings = build_sequence_candidates(clips, regeneration_assets_dir)
    if not candidates:
        raise ValueError("No candidates available for optimization.")

    keyword_document_frequency = _keyword_document_frequency(candidates)
    engine_requested = engine
    if engine == "openai":
        ordered = optimize_sequence_with_llm(
            candidates,
            keyword_document_frequency,
            enable_subject_series_grouping=enable_subject_series_grouping,
        )
        engine_used = "heuristic-fallback"
    else:
        ordered = optimize_sequence_with_heuristic(
            candidates,
            keyword_document_frequency,
            enable_subject_series_grouping=enable_subject_series_grouping,
        )
        engine_used = "heuristic"

    entries = [
        SequenceRecommendationEntry(
            recommended_index=index,
            original_index=item["candidate"].clip.order_index,
            score=float(item["score"]),
            reason=str(item["reason"]),
            candidate=item["candidate"],
        )
        for index, item in enumerate(ordered, start=1)
    ]

    resolved_translation_results_path = resolve_translation_results_path(source_xml, translation_results_path)
    translation_warnings: list[str] = []
    lost_effect_issues = []
    clips_with_lost_effects = []
    if resolved_translation_results_path is not None and resolved_translation_results_path.exists():
        lost_effect_issues, translation_warnings = parse_fcp_translation_results(
            resolved_translation_results_path,
            selected_sequence_name=selected_sequence_name,
        )
        clips_with_lost_effects = summarize_lost_effects(lost_effect_issues, entries)

    return SequenceOptimizationResult(
        source_xml=str(source_xml),
        selected_sequence_name=selected_sequence_name,
        engine_requested=engine_requested,
        engine_used=engine_used,
        warnings=warnings,
        entries=entries,
        feature_flags={
            "enable_subject_series_grouping": enable_subject_series_grouping,
        },
        translation_report_path=str(resolved_translation_results_path) if resolved_translation_results_path else None,
        translation_warnings=translation_warnings,
        lost_effect_issues=lost_effect_issues,
        clips_with_lost_effects=clips_with_lost_effects,
    )


def optimize_sequence_with_heuristic(
    candidates: list[SequenceCandidate],
    keyword_document_frequency: dict[str, int],
    *,
    enable_subject_series_grouping: bool = False,
) -> list[dict[str, object]]:
    remaining = list(candidates)
    ordered: list[dict[str, object]] = []

    opening_candidate = max(remaining, key=lambda candidate: (candidate.opening_score, -candidate.clip.order_index))
    ordered.append(
        {
            "candidate": opening_candidate,
            "score": round(opening_candidate.opening_score, 3),
            "reason": _opening_reason(opening_candidate),
        }
    )
    remaining.remove(opening_candidate)
    previous = opening_candidate
    age_progression_floor = opening_candidate.main_character_age_hint

    while remaining:
        remaining_known_ages = [
            candidate.main_character_age_hint
            for candidate in remaining
            if candidate.main_character_age_hint is not None
        ]
        youngest_remaining_age = min(remaining_known_ages) if remaining_known_ages else None
        scored_candidates = [
            _continuity_payload(
                previous,
                candidate,
                keyword_document_frequency,
                age_progression_floor=age_progression_floor,
                youngest_remaining_age=youngest_remaining_age,
                enable_subject_series_grouping=enable_subject_series_grouping,
            )
            for candidate in remaining
        ]
        best_item = max(scored_candidates, key=lambda item: (float(item["score"]), -int(item["candidate"].clip.order_index)))
        ordered.append(best_item)
        best_candidate = best_item["candidate"]
        remaining.remove(best_candidate)
        previous = best_candidate
        if best_candidate.main_character_age_hint is not None:
            if age_progression_floor is None:
                age_progression_floor = best_candidate.main_character_age_hint
            else:
                age_progression_floor = max(age_progression_floor, best_candidate.main_character_age_hint)

    stabilized = _stabilize_age_progression(ordered)
    if enable_subject_series_grouping:
        stabilized = _stabilize_subject_series(stabilized)
    return stabilized


def optimize_sequence_with_llm(
    candidates: list[SequenceCandidate],
    keyword_document_frequency: dict[str, int],
    *,
    enable_subject_series_grouping: bool = False,
) -> list[dict[str, object]]:
    """
    TODO_STUB: optimize_sequence_with_llm
    Future logic:
    - send the original clip order, scene_analysis payloads, and v_prompt files to an LLM;
    - ask for a stronger narrative reorder that balances continuity, escalation, and emotional rhythm;
    - validate the returned order against the available clip ids before writing the report.
    Parameters:
    - candidates: enriched clip records with XML order, scene analysis, prompt text, and heuristic features.
    Expected result:
    - a validated ordered list of recommendation payloads in the same format as optimize_sequence_with_heuristic.
    Temporary mock implementation:
    - fall back to the deterministic heuristic order so the MVP stays runnable without network access.
    """
    return optimize_sequence_with_heuristic(
        candidates,
        keyword_document_frequency,
        enable_subject_series_grouping=enable_subject_series_grouping,
    )


def format_sequence_report(result: SequenceOptimizationResult) -> str:
    source_label = "Source project" if str(result.source_xml).lower().endswith(".prproj") else "Source XML"
    lines = [
        "PREMIERE SEQUENCE OPTIMIZATION REPORT",
        "",
        f"{source_label}: {result.source_xml}",
        f"Selected sequence: {result.selected_sequence_name}",
        f"Engine requested: {result.engine_requested}",
        f"Engine used: {result.engine_used}",
        f"Clip count: {len(result.entries)}",
        "",
        "Recommended order",
        "",
    ]

    for entry in result.entries:
        clip = entry.candidate.clip
        lines.extend(
            [
                f"{entry.recommended_index}. Original V{entry.original_index}: {clip.name}",
                f"   Stage ID: {clip.stage_id}",
                f"   Source path: {clip.source_path or '<missing in XML>'}",
                f"   Heuristic score: {entry.score:.3f}",
                f"   Reason: {entry.reason}",
                "",
            ]
        )

    if result.translation_report_path:
        lines.extend(
            [
                "FCP Translation Results",
                "",
                f"Report path: {result.translation_report_path}",
                f"Clips with lost effects: {len(result.clips_with_lost_effects)}",
                "",
            ]
        )

    if result.clips_with_lost_effects:
        lines.extend(["Clips with lost effects", ""])
        for item in result.clips_with_lost_effects:
            sequence_position = "unknown sequence position"
            if item.recommended_index is not None and item.original_index is not None:
                sequence_position = f"Recommended {item.recommended_index} / Original V{item.original_index}"
            lines.extend(
                [
                    f"- {sequence_position}: {item.clip_name}",
                    f"  Effects: {', '.join(item.effect_names)}",
                    f"  Tracks: {', '.join(item.track_locations)}",
                    f"  Stage ID: {item.stage_id or '<unknown>'}",
                    "",
                ]
            )

    if result.translation_warnings:
        lines.extend(["Translation warnings", ""])
        lines.extend([f"- {warning}" for warning in result.translation_warnings])
        lines.append("")

    if result.warnings:
        lines.extend(["Warnings", ""])
        lines.extend([f"- {warning}" for warning in result.warnings])
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _continuity_payload(
    previous: SequenceCandidate,
    current: SequenceCandidate,
    keyword_document_frequency: dict[str, int],
    *,
    age_progression_floor: float | None = None,
    youngest_remaining_age: float | None = None,
    enable_subject_series_grouping: bool = False,
) -> dict[str, object]:
    previous_keywords = set(previous.keywords)
    current_keywords = set(current.keywords)
    overlap = previous_keywords & current_keywords
    overlap_weighted = sorted(overlap, key=lambda token: (-_keyword_weight(token, keyword_document_frequency), token))
    overlap_score = sum(_keyword_weight(token, keyword_document_frequency) for token in overlap)
    shot_penalty = abs(previous.shot_scale - current.shot_scale) * 1.1
    people_penalty = abs(previous.people_count - current.people_count) * 0.35
    energy_penalty = abs(previous.energy_level - current.energy_level) * 0.4
    order_bonus = max(0.0, 1.2 - abs(previous.clip.order_index - current.clip.order_index) * 0.15)
    opening_bonus = current.opening_score * 0.2
    protagonist_bonus = min(previous.main_character_priority, current.main_character_priority) * 0.35
    age_adjustment, age_fragments = _age_progression_adjustment(
        previous.main_character_age_hint,
        current.main_character_age_hint,
        age_progression_floor=age_progression_floor,
        youngest_remaining_age=youngest_remaining_age,
    )
    if enable_subject_series_grouping:
        series_bonus, series_fragments = _subject_series_adjustment(previous, current)
    else:
        series_bonus, series_fragments = 0.0, []
    total_score = (
        overlap_score
        + order_bonus
        + opening_bonus
        + protagonist_bonus
        + age_adjustment
        + series_bonus
        - shot_penalty
        - people_penalty
        - energy_penalty
    )

    fragments: list[str] = []
    if overlap_weighted:
        fragments.append(f"shared context: {', '.join(overlap_weighted[:4])}")
    if protagonist_bonus >= 0.7:
        fragments.append("keeps the main character in focus")
    fragments.extend(age_fragments)
    fragments.extend(series_fragments)
    if abs(previous.shot_scale - current.shot_scale) <= 1:
        fragments.append("similar shot scale")
    if abs(previous.people_count - current.people_count) <= 1:
        fragments.append("similar people count")
    if abs(previous.clip.order_index - current.clip.order_index) <= 2:
        fragments.append("close to the original timeline")
    if not fragments:
        fragments.append("best available continuity balance")

    return {
        "candidate": current,
        "score": round(total_score, 3),
        "reason": "; ".join(fragments),
    }


def _age_progression_adjustment(
    previous_age: float | None,
    current_age: float | None,
    *,
    age_progression_floor: float | None,
    youngest_remaining_age: float | None,
) -> tuple[float, list[str]]:
    if current_age is None:
        return 0.0, []

    adjustment = 0.0
    fragments: list[str] = []

    if previous_age is not None:
        age_delta = current_age - previous_age
        if -1.0 <= age_delta <= 6.0:
            adjustment += 1.35
            if age_delta >= 0:
                fragments.append("keeps age progression moving forward")
        elif age_delta < -1.0:
            adjustment -= min(9.5, (abs(age_delta) - 1.0) * 1.35 + 1.6)
        else:
            adjustment -= min(7.0, (age_delta - 6.0) * 0.65)

    if age_progression_floor is not None:
        floor_delta = current_age - age_progression_floor
        if floor_delta < -1.5:
            adjustment -= min(11.0, (abs(floor_delta) - 1.5) * 1.1 + 1.8)
        elif floor_delta >= 0:
            adjustment += min(1.1, 0.25 + floor_delta * 0.08)

    if youngest_remaining_age is not None:
        pool_delta = current_age - youngest_remaining_age
        if pool_delta > 5.5:
            adjustment -= min(12.0, (pool_delta - 5.5) * 0.7)
        elif 0 <= pool_delta <= 1.5:
            adjustment += 2.25
            if "keeps age progression moving forward" not in fragments:
                fragments.append("preserves the younger-to-older character arc")
        elif 0 <= pool_delta <= 3.5:
            adjustment += 0.6
            if "keeps age progression moving forward" not in fragments:
                fragments.append("preserves the younger-to-older character arc")

    return round(adjustment, 3), fragments[:1]


def _subject_series_adjustment(
    previous: SequenceCandidate,
    current: SequenceCandidate,
) -> tuple[float, list[str]]:
    generic_subject_tokens = _series_generic_subject_tokens()
    previous_subject_tokens = set(previous.series_subject_tokens)
    current_subject_tokens = set(current.series_subject_tokens)
    if not previous_subject_tokens or not current_subject_tokens:
        return 0.0, []

    subject_overlap = previous_subject_tokens & current_subject_tokens
    if not subject_overlap:
        return 0.0, []

    informative_overlap = subject_overlap - generic_subject_tokens
    subject_score = 0.0
    fragments: list[str] = []
    appearance_overlap = _appearance_series_overlap(previous, current)

    if informative_overlap:
        subject_score += 2.5 + min(1.6, len(informative_overlap) * 0.65)
    elif len(subject_overlap) >= 2:
        subject_score += 1.8
    else:
        subject_score += 0.95

    previous_pose_tokens = set(previous.series_pose_tokens)
    current_pose_tokens = set(current.series_pose_tokens)
    pose_overlap = previous_pose_tokens & current_pose_tokens
    if len(appearance_overlap) >= 2:
        subject_score += 3.15 + min(1.8, len(appearance_overlap) * 0.45)
        fragments.append("keeps the same person or object in the same look")
    elif appearance_overlap:
        subject_score += 1.85
        fragments.append("keeps the same person or object nearby")

    if previous_pose_tokens and current_pose_tokens and not pose_overlap:
        subject_score += 1.35
        if "keeps the same person or object in the same look" in fragments:
            fragments[0] = "keeps the same person or object in the same look across new poses like a visual series"
        else:
            fragments.append("keeps the same subject in a new pose like a visual series")
    elif len(previous_pose_tokens | current_pose_tokens) >= 2:
        subject_score += 0.55
        if not fragments:
            fragments.append("keeps the same subject nearby")
    else:
        if not fragments:
            fragments.append("keeps the same subject nearby")

    return round(subject_score, 3), fragments[:1]


def _series_generic_subject_tokens() -> set[str]:
    return {
        "adult",
        "baby",
        "boy",
        "character",
        "child",
        "children",
        "family",
        "female",
        "figure",
        "girl",
        "human",
        "infant",
        "kid",
        "main",
        "male",
        "man",
        "mother",
        "person",
        "portrait",
        "subject",
        "toddler",
        "woman",
        "young",
        "youngest",
    }


def _appearance_series_overlap(previous: SequenceCandidate, current: SequenceCandidate) -> set[str]:
    generic_subject_tokens = _series_generic_subject_tokens()
    return (set(previous.series_appearance_tokens) & set(current.series_appearance_tokens)) - generic_subject_tokens


def _stabilize_age_progression(ordered: list[dict[str, object]]) -> list[dict[str, object]]:
    stabilized = list(ordered)
    changed = True

    while changed:
        changed = False
        max_seen_age: float | None = None

        for index, item in enumerate(stabilized):
            candidate = item["candidate"]
            current_age = candidate.main_character_age_hint
            if current_age is None:
                continue

            if max_seen_age is not None and current_age < max_seen_age - 6.0:
                target_index = 0
                for probe_index in range(index):
                    probe_age = stabilized[probe_index]["candidate"].main_character_age_hint
                    if probe_age is not None and probe_age <= current_age + 3.0:
                        target_index = probe_index + 1
                if target_index < index:
                    moved_item = stabilized.pop(index)
                    stabilized.insert(target_index, moved_item)
                    changed = True
                    break

            max_seen_age = current_age if max_seen_age is None else max(max_seen_age, current_age)

    return stabilized


def _stabilize_subject_series(ordered: list[dict[str, object]]) -> list[dict[str, object]]:
    stabilized = list(ordered)
    changed = True

    while changed:
        changed = False

        for index in range(len(stabilized) - 2):
            current_candidate = stabilized[index]["candidate"]
            next_candidate = stabilized[index + 1]["candidate"]
            next_score, _ = _subject_series_adjustment(current_candidate, next_candidate)
            next_appearance_overlap = len(_appearance_series_overlap(current_candidate, next_candidate))
            best_index: int | None = None
            best_score = next_score
            best_appearance_overlap = next_appearance_overlap

            for probe_index in range(index + 2, len(stabilized)):
                probe_candidate = stabilized[probe_index]["candidate"]
                probe_score, _ = _subject_series_adjustment(current_candidate, probe_candidate)
                probe_appearance_overlap = len(_appearance_series_overlap(current_candidate, probe_candidate))
                if probe_appearance_overlap >= 2 and probe_appearance_overlap > best_appearance_overlap:
                    best_index = probe_index
                    best_score = probe_score
                    best_appearance_overlap = probe_appearance_overlap
                    continue
                if probe_score >= 4.2 and probe_score > best_score + 0.8:
                    best_index = probe_index
                    best_score = probe_score
                    best_appearance_overlap = probe_appearance_overlap

            if best_index is not None and (best_appearance_overlap >= 2 or next_score < 3.0):
                moved_item = stabilized.pop(best_index)
                stabilized.insert(index + 1, moved_item)
                changed = True
                break

    return stabilized


def _opening_reason(candidate: SequenceCandidate) -> str:
    fragments: list[str] = []
    if candidate.main_character_notes:
        fragments.append(candidate.main_character_notes[0])
    if candidate.shot_scale <= 1:
        fragments.append("works as an establishing or medium-wide opener")
    if candidate.energy_level <= 1:
        fragments.append("starts with calmer visual energy")
    if candidate.assets.scene_analysis.get("background"):
        fragments.append("has a readable background anchor")
    if not fragments:
        fragments.append("best opening score across the available clips")
    return "; ".join(fragments)


def _collect_keywords(clip: PremiereSequenceClip, assets: ClipAssetBundle) -> list[str]:
    values: list[str] = [
        clip.stage_id,
        clip.name,
        str(assets.scene_analysis.get("summary", "")),
        str(assets.scene_analysis.get("background", "")),
        str(assets.scene_analysis.get("shot_type", "")),
        str(assets.scene_analysis.get("main_action", "")),
        " ".join(str(item) for item in assets.scene_analysis.get("mood", []) if item),
        " ".join(str(item) for item in assets.scene_analysis.get("relationships", []) if item),
        assets.prompt_text,
    ]
    tokens: list[str] = []
    for value in values:
        for token in _TOKEN_PATTERN.findall(value.casefold()):
            if token in _STOPWORDS:
                continue
            if token.isdigit():
                continue
            if len(token) < 4:
                continue
            tokens.append(token)
    return sorted(set(tokens))[:80]


def _infer_series_subject_features(
    clip: PremiereSequenceClip,
    assets: ClipAssetBundle,
) -> tuple[list[str], list[str], list[str]]:
    people_raw = assets.scene_analysis.get("people", [])
    selected_person: dict[str, object] | None = None

    if isinstance(people_raw, list):
        people = [item for item in people_raw if isinstance(item, dict)]
        if people:
            scored_people = [
                (_series_subject_focus_score(person), index, person)
                for index, person in enumerate(people)
            ]
            selected_person = max(scored_people, key=lambda item: (item[0], -item[1]))[2]

    if selected_person is not None:
        subject_tokens = _collect_series_tokens(
            selected_person.get("label", ""),
            selected_person.get("role_in_scene", ""),
            selected_person.get("apparent_gender_presentation", ""),
        )
        appearance_tokens = _collect_series_tokens(
            selected_person.get("label", ""),
            selected_person.get("role_in_scene", ""),
            selected_person.get("clothing", ""),
            selected_person.get("apparent_age_group", ""),
        )
        pose_tokens = _collect_series_tokens(
            selected_person.get("pose", ""),
            selected_person.get("facial_expression", ""),
            assets.scene_analysis.get("main_action", ""),
        )
        if subject_tokens:
            return subject_tokens[:12], appearance_tokens[:16], pose_tokens[:12]

    fallback_subject_tokens = _collect_series_tokens(
        clip.name,
        assets.scene_analysis.get("summary", ""),
        assets.scene_analysis.get("main_action", ""),
        assets.scene_analysis.get("relationships", ""),
    )
    fallback_appearance_tokens = _collect_series_tokens(
        clip.name,
        assets.scene_analysis.get("summary", ""),
    )
    fallback_pose_tokens = _collect_series_tokens(assets.scene_analysis.get("main_action", ""))
    return fallback_subject_tokens[:12], fallback_appearance_tokens[:16], fallback_pose_tokens[:12]


def _series_subject_focus_score(person: dict[str, object]) -> float:
    position_text = str(person.get("position_in_frame", "")).casefold()
    face_text = str(person.get("face_visibility", "")).casefold()
    label_text = str(person.get("label", "")).strip()
    role_text = str(person.get("role_in_scene", "")).strip()

    score = 0.0
    if any(keyword in position_text for keyword in _CENTER_KEYWORDS):
        score += 1.0
    if any(keyword in position_text for keyword in _FOREGROUND_KEYWORDS):
        score += 0.8
    if any(keyword in face_text for keyword in _FULL_FACE_KEYWORDS):
        score += 0.6
    if label_text:
        score += 0.25
    if role_text:
        score += 0.2
    return score


def _collect_series_tokens(*values: object) -> list[str]:
    tokens: list[str] = []
    for value in values:
        for token in _TOKEN_PATTERN.findall(str(value).casefold()):
            if token in _STOPWORDS:
                continue
            if token.isdigit():
                continue
            if len(token) < 3:
                continue
            tokens.append(token)
    return sorted(set(tokens))


def _infer_shot_scale(assets: ClipAssetBundle) -> int:
    shot_text = " ".join(
        [
            str(assets.scene_analysis.get("shot_type", "")),
            str(assets.scene_analysis.get("summary", "")),
        ]
    ).casefold()
    for scale, variants in _SHOT_SCALE_RULES:
        if any(variant in shot_text for variant in variants):
            return scale
    return 1


def _infer_energy_level(keywords: list[str]) -> int:
    score = 0
    for keyword in keywords:
        if keyword in _HIGH_ENERGY_KEYWORDS:
            score += 2
        if keyword in _LOW_ENERGY_KEYWORDS:
            score -= 1
    return max(0, min(3, score))


def _score_opening_candidate(
    *,
    clip: PremiereSequenceClip,
    assets: ClipAssetBundle,
    people_count: int,
    shot_scale: int,
    energy_level: int,
    main_character_priority: float,
) -> float:
    background_bonus = 1.0 if assets.scene_analysis.get("background") else 0.0
    people_bonus = 0.4 if 0 < people_count <= 3 else 0.0
    original_order_bonus = max(0.0, 1.5 - (clip.order_index - 1) * 0.2)
    main_character_bonus = main_character_priority * 1.15
    return round(
        (3 - shot_scale) * 1.6
        + background_bonus
        + people_bonus
        + original_order_bonus
        + main_character_bonus
        - energy_level * 0.5,
        3,
    )


def _read_json_file(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        return payload
    return {}


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _keyword_document_frequency(candidates: list[SequenceCandidate]) -> dict[str, int]:
    document_frequency: dict[str, int] = {}
    for candidate in candidates:
        for token in set(candidate.keywords):
            document_frequency[token] = document_frequency.get(token, 0) + 1
    return document_frequency


def _keyword_weight(token: str, keyword_document_frequency: dict[str, int]) -> float:
    frequency = keyword_document_frequency.get(token, 1)
    return round(3.0 / frequency, 3)


def _infer_main_character_priority(assets: ClipAssetBundle) -> tuple[float, float | None, list[str]]:
    people_raw = assets.scene_analysis.get("people", [])
    best_score = 0.0
    best_age: float | None = None
    best_notes: list[str] = []

    if isinstance(people_raw, list):
        for item in people_raw:
            if not isinstance(item, dict):
                continue
            score, age_hint, notes = _score_person_as_main_character(item)
            if score > best_score:
                best_score = score
                best_age = age_hint
                best_notes = notes

    if best_score <= 0:
        summary_score, summary_notes = _score_summary_main_character_hint(assets.scene_analysis)
        if summary_score > best_score:
            best_score = summary_score
            best_notes = summary_notes

    return round(best_score, 3), best_age, best_notes


def _score_person_as_main_character(person: dict[str, object]) -> tuple[float, float | None, list[str]]:
    age_hint = _infer_person_age_hint(person)
    combined_text = " ".join(
        str(person.get(field, ""))
        for field in ("label", "role_in_scene", "apparent_age_group", "position_in_frame", "face_visibility")
    ).casefold()
    position_text = str(person.get("position_in_frame", "")).casefold()
    face_text = str(person.get("face_visibility", "")).casefold()

    score = 0.0
    notes: list[str] = []

    if age_hint is not None and age_hint <= 1:
        score += 3.4
        notes.append("prominently features the youngest child / infant")
    elif age_hint is not None and age_hint <= 4:
        score += 2.6
        notes.append("keeps the youngest child near the front of the story")
    elif age_hint is not None and age_hint <= 8:
        score += 1.8
        notes.append("features a young child who can anchor the story")
    elif age_hint is not None and age_hint <= 12:
        score += 0.9

    if _is_explicit_infant_description(person):
        score += 0.95
        if not notes:
            notes.append("prominently features the youngest child / infant")

    if any(keyword in combined_text for keyword in _YOUNGEST_KEYWORDS):
        score += 0.9
        if not notes:
            notes.append("gives priority to the youngest visible character")

    if any(keyword in position_text for keyword in _CENTER_KEYWORDS):
        score += 0.8
    if any(keyword in position_text for keyword in _FOREGROUND_KEYWORDS):
        score += 0.45
    if any(keyword in face_text for keyword in _FULL_FACE_KEYWORDS):
        score += 0.45

    return score, age_hint, notes


def _score_summary_main_character_hint(scene_analysis: dict[str, object]) -> tuple[float, list[str]]:
    summary_text = " ".join(
        str(scene_analysis.get(field, ""))
        for field in ("summary", "main_action", "relationships")
    ).casefold()
    if any(keyword in summary_text for keyword in _INFANT_KEYWORDS):
        return 2.4, ["prominently features the youngest child / infant"]
    if any(keyword in summary_text for keyword in _TODDLER_KEYWORDS):
        return 1.7, ["keeps the youngest child near the front of the story"]
    if any(keyword in summary_text for keyword in _CHILD_KEYWORDS):
        return 0.8, ["features a young child who can anchor the story"]
    return 0.0, []


def _infer_person_age_hint(person: dict[str, object]) -> float | None:
    age_text = " ".join(
        str(person.get(field, ""))
        for field in ("label", "role_in_scene", "apparent_age_group")
    ).casefold()
    numeric_hints = [int(value) for value in _AGE_NUMBER_PATTERN.findall(age_text)]
    if numeric_hints:
        return float(min(numeric_hints))
    if any(separator in age_text for separator in ("/", "|", ",")):
        has_infant = any(keyword in age_text for keyword in _INFANT_KEYWORDS)
        has_child = any(keyword in age_text for keyword in _TODDLER_KEYWORDS + _CHILD_KEYWORDS)
        if has_infant and has_child:
            return 2.5
    if any(keyword in age_text for keyword in _INFANT_KEYWORDS):
        return 0.0
    if any(keyword in age_text for keyword in _TODDLER_KEYWORDS):
        return 3.0
    if any(keyword in age_text for keyword in _CHILD_KEYWORDS):
        return 7.0
    if any(keyword in age_text for keyword in _TEEN_KEYWORDS):
        return 15.0
    if any(keyword in age_text for keyword in _ADULT_KEYWORDS):
        return 25.0
    return None


def _is_explicit_infant_description(person: dict[str, object]) -> bool:
    label_text = str(person.get("label", "")).casefold()
    age_group_text = str(person.get("apparent_age_group", "")).casefold()
    if any(separator in label_text for separator in ("/", "|", ",")):
        return False
    if any(separator in age_group_text for separator in ("/", "|", ",")):
        return False
    label_is_infant = any(keyword in label_text for keyword in _INFANT_KEYWORDS)
    age_group_is_infant = any(keyword in age_group_text for keyword in _INFANT_KEYWORDS)
    return label_is_infant or age_group_is_infant


__all__ = [
    "build_sequence_candidates",
    "format_sequence_report",
    "load_clip_asset_bundle",
    "optimize_sequence",
    "optimize_sequence_with_heuristic",
    "optimize_sequence_with_llm",
]
