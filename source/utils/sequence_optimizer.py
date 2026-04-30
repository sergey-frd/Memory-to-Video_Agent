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
from utils.fcp_translation_results import (
    parse_fcp_translation_results,
    resolve_translation_results_path,
    summarize_lost_effects,
)


_TOKEN_PATTERN = re.compile(r"[A-Za-zА-Яа-я0-9']{3,}")
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


def build_sequence_candidates(clips: list[PremiereSequenceClip], regeneration_assets_dir: Path) -> tuple[list[SequenceCandidate], list[str]]:
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
        opening_score = _score_opening_candidate(
            clip=clip,
            assets=assets,
            people_count=people_count,
            shot_scale=shot_scale,
            energy_level=energy_level,
        )
        candidates.append(
            SequenceCandidate(
                clip=clip,
                assets=assets,
                keywords=keywords,
                people_count=people_count,
                shot_scale=shot_scale,
                energy_level=energy_level,
                opening_score=opening_score,
            )
        )
    return candidates, warnings


def load_clip_asset_bundle(regeneration_assets_dir: Path, clip: PremiereSequenceClip) -> ClipAssetBundle:
    bundle_dir = regeneration_assets_dir / clip.stage_id
    missing_files: list[str] = []
    manifest: dict[str, object] = {}
    scene_analysis: dict[str, object] = {}
    prompt_text = ""

    if not bundle_dir.exists():
        return ClipAssetBundle(
            stage_id=clip.stage_id,
            bundle_dir=str(bundle_dir),
            missing_files=["bundle_dir", "scene_analysis", "v_prompt", "manifest"],
        )

    manifest_path = bundle_dir / f"{clip.stage_id}_api_pipeline_manifest.json"
    scene_analysis_path = bundle_dir / f"{clip.stage_id}_scene_analysis.json"
    prompt_path = bundle_dir / f"{clip.stage_id}_v_prompt_{clip.video_index}.txt"

    if not prompt_path.exists():
        prompt_candidates = sorted(bundle_dir.glob(f"{clip.stage_id}_v_prompt_*.txt"))
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
        stage_id=clip.stage_id,
        bundle_dir=str(bundle_dir),
        manifest_path=str(manifest_path) if manifest_path.exists() else None,
        scene_analysis_path=str(scene_analysis_path) if scene_analysis_path.exists() else None,
        prompt_path=str(prompt_path) if prompt_path.exists() else None,
        manifest=manifest,
        scene_analysis=scene_analysis,
        prompt_text=prompt_text,
        missing_files=missing_files,
    )


def optimize_sequence(
    *,
    source_xml: Path,
    selected_sequence_name: str,
    clips: list[PremiereSequenceClip],
    regeneration_assets_dir: Path,
    engine: str = "heuristic",
    translation_results_path: Path | None = None,
) -> SequenceOptimizationResult:
    candidates, warnings = build_sequence_candidates(clips, regeneration_assets_dir)
    if not candidates:
        raise ValueError("No candidates available for optimization.")
    keyword_document_frequency = _keyword_document_frequency(candidates)

    engine_requested = engine
    if engine == "openai":
        ordered = optimize_sequence_with_llm(candidates, keyword_document_frequency)
        engine_used = "heuristic-fallback"
    else:
        ordered = optimize_sequence_with_heuristic(candidates, keyword_document_frequency)
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

    resolved_translation_report = resolve_translation_results_path(source_xml, translation_results_path)
    translation_warnings: list[str] = []
    lost_effect_issues = []
    clips_with_lost_effects = []
    if resolved_translation_report is not None:
        lost_effect_issues, translation_warnings = parse_fcp_translation_results(
            resolved_translation_report,
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
        translation_report_path=str(resolved_translation_report) if resolved_translation_report is not None else None,
        translation_warnings=translation_warnings,
        lost_effect_issues=lost_effect_issues,
        clips_with_lost_effects=clips_with_lost_effects,
    )


def optimize_sequence_with_heuristic(
    candidates: list[SequenceCandidate],
    keyword_document_frequency: dict[str, int],
) -> list[dict[str, object]]:
    remaining = list(candidates)
    ordered: list[dict[str, object]] = []

    opening_candidate = max(
        remaining,
        key=lambda candidate: (candidate.opening_score, -candidate.clip.order_index),
    )
    ordered.append(
        {
            "candidate": opening_candidate,
            "score": round(opening_candidate.opening_score, 3),
            "reason": _opening_reason(opening_candidate),
        }
    )
    remaining.remove(opening_candidate)
    previous = opening_candidate

    while remaining:
        scored_candidates = [
            _continuity_payload(previous, candidate, keyword_document_frequency)
            for candidate in remaining
        ]
        best_item = max(
            scored_candidates,
            key=lambda item: (float(item["score"]), -int(item["candidate"].clip.order_index)),
        )
        ordered.append(best_item)
        best_candidate = best_item["candidate"]
        remaining.remove(best_candidate)
        previous = best_candidate

    return ordered


def optimize_sequence_with_llm(
    candidates: list[SequenceCandidate],
    keyword_document_frequency: dict[str, int],
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
    return optimize_sequence_with_heuristic(candidates, keyword_document_frequency)


def format_sequence_report(result: SequenceOptimizationResult) -> str:
    lines = [
        "PREMIERE XML SEQUENCE OPTIMIZATION REPORT",
        "",
        f"Source XML: {result.source_xml}",
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
                "Translation results",
                "",
                f"Report: {result.translation_report_path}",
                f"Lost effect clips: {len(result.clips_with_lost_effects)}",
                "",
            ]
        )

    if result.clips_with_lost_effects:
        lines.extend(["Clips with lost effects", ""])
        for item in result.clips_with_lost_effects:
            lines.extend(
                [
                    f"- {item.clip_name}",
                    f"  Stage ID: {item.stage_id or '<unknown>'}",
                    f"  Original V: {item.original_index if item.original_index is not None else '<unknown>'}",
                    f"  Recommended order: {item.recommended_index if item.recommended_index is not None else '<unknown>'}",
                    f"  Effects: {', '.join(item.effect_names)}",
                    f"  Tracks: {', '.join(item.track_locations)}",
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
) -> dict[str, object]:
    previous_keywords = set(previous.keywords)
    current_keywords = set(current.keywords)
    overlap = previous_keywords & current_keywords
    overlap_weighted = sorted(
        overlap,
        key=lambda token: (-_keyword_weight(token, keyword_document_frequency), token),
    )
    overlap_score = sum(_keyword_weight(token, keyword_document_frequency) for token in overlap)
    shot_penalty = abs(previous.shot_scale - current.shot_scale) * 1.1
    people_penalty = abs(previous.people_count - current.people_count) * 0.35
    energy_penalty = abs(previous.energy_level - current.energy_level) * 0.4
    order_bonus = max(0.0, 1.2 - abs(previous.clip.order_index - current.clip.order_index) * 0.15)
    opening_bonus = current.opening_score * 0.2
    total_score = overlap_score + order_bonus + opening_bonus - shot_penalty - people_penalty - energy_penalty

    fragments: list[str] = []
    if overlap_weighted:
        fragments.append(f"shared context: {', '.join(overlap_weighted[:4])}")
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


def _opening_reason(candidate: SequenceCandidate) -> str:
    fragments: list[str] = []
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
    unique_tokens = sorted(set(tokens))
    return unique_tokens[:80]


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
) -> float:
    background_bonus = 1.0 if assets.scene_analysis.get("background") else 0.0
    people_bonus = 0.4 if 0 < people_count <= 3 else 0.0
    original_order_bonus = max(0.0, 1.5 - (clip.order_index - 1) * 0.2)
    return round((3 - shot_scale) * 1.6 + background_bonus + people_bonus + original_order_bonus - energy_level * 0.5, 3)


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


from utils.sequence_optimizer_runtime import *  # noqa: F401,F403
