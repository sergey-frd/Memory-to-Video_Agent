from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from models.sequence_trim_review import SequenceTrimReviewResult, TrimClipDecision, TrimSegmentDecision
from models.video_sequence import PremiereSequenceClip
from utils.premiere_project import PREMIERE_TICKS_PER_SECOND, is_supported_image_media_path


_TOKEN_PATTERN = re.compile(r"[A-Za-zА-Яа-я0-9']{3,}")
_MIN_SEGMENT_SECONDS = 0.35


@dataclass(frozen=True)
class CompactKeepSettings:
    enabled: bool = True
    photo_keep_min_seconds: float = 1.5
    photo_keep_max_seconds: float = 3.0
    video_keep_min_seconds: float = 2.0
    video_keep_max_seconds: float = 8.0


DEFAULT_COMPACT_KEEP = CompactKeepSettings()


def ticks_to_seconds(ticks: int) -> float:
    return max(0.0, float(ticks) / float(PREMIERE_TICKS_PER_SECOND))


def seconds_to_ticks(seconds: float) -> int:
    return max(0, int(round(float(seconds) * float(PREMIERE_TICKS_PER_SECOND))))


def is_still_image_clip(clip: PremiereSequenceClip) -> bool:
    return is_supported_image_media_path(clip.name) or is_supported_image_media_path(clip.source_path)


def classify_sequence_trim_review(
    clips: list[PremiereSequenceClip],
    *,
    source_project_path: Path,
    source_sequence_name: str,
    new_sequence_name: str,
    target_keep_seconds: float = 300.0,
    min_keep_seconds: float = 180.0,
    max_keep_seconds: float | None = None,
    context_notes: str = "",
    force_keep_names: list[str] | None = None,
    force_drop_names: list[str] | None = None,
    compact_keep: CompactKeepSettings | None = None,
) -> SequenceTrimReviewResult:
    if not clips:
        raise ValueError("No clips available for trim review classification.")

    compact = compact_keep or DEFAULT_COMPACT_KEEP
    max_budget = float(max_keep_seconds if max_keep_seconds is not None else target_keep_seconds)
    max_budget = max(max_budget, float(min_keep_seconds), 1.0)
    target_keep_seconds = min(float(target_keep_seconds), max_budget)
    min_keep_seconds = min(float(min_keep_seconds), max_budget)

    force_keep = _normalize_name_filters(force_keep_names)
    force_drop = _normalize_name_filters(force_drop_names)
    scored = [_score_clip(clip, index=index, total=len(clips)) for index, clip in enumerate(clips)]
    allocations = _allocate_keep_seconds(
        scored,
        target_keep_seconds=target_keep_seconds,
        min_keep_seconds=min_keep_seconds,
        max_keep_seconds=max_budget,
        force_keep=force_keep,
        force_drop=force_drop,
        compact_keep=compact,
    )

    decisions: list[TrimClipDecision] = []
    for item in scored:
        clip = item["clip"]
        keep_alloc = float(allocations[clip.clipitem_id])
        clip_decision = _build_clip_decision(item, keep_alloc_seconds=keep_alloc)
        decisions.append(clip_decision)

    total_source_seconds = sum(item.duration_seconds for item in decisions)
    keep_seconds = sum(item.keep_seconds for item in decisions)
    drop_seconds = sum(item.drop_seconds for item in decisions)
    warnings: list[str] = []
    if keep_seconds > max_budget + 1.0:
        warnings.append(
            f"Keep duration {keep_seconds:.1f}s exceeds max budget {max_budget:.1f}s after per-clip segmentation."
        )
    if keep_seconds < min_keep_seconds - 1.0:
        warnings.append(
            f"Keep duration {keep_seconds:.1f}s is below min budget {min_keep_seconds:.1f}s."
        )
    if compact.enabled:
        warnings.append(
            "Compact keep enabled: stills aim for "
            f"{compact.photo_keep_min_seconds:.1f}-{compact.photo_keep_max_seconds:.1f}s; "
            f"video keep islands aim for {compact.video_keep_min_seconds:.1f}-{compact.video_keep_max_seconds:.1f}s."
        )

    engine = "heuristic_segment_budget_v1_compact" if compact.enabled else "heuristic_segment_budget_v1"
    return SequenceTrimReviewResult(
        source_project_path=str(source_project_path),
        source_sequence_name=source_sequence_name,
        new_sequence_name=new_sequence_name,
        engine=engine,
        target_keep_seconds=target_keep_seconds,
        min_keep_seconds=min_keep_seconds,
        max_keep_seconds=max_budget,
        total_source_seconds=round(total_source_seconds, 3),
        keep_seconds=round(keep_seconds, 3),
        drop_seconds=round(drop_seconds, 3),
        context_notes=context_notes.strip(),
        open_questions=_build_open_questions(decisions, context_notes=context_notes),
        warnings=warnings,
        decisions=decisions,
    )


def _allocate_keep_seconds(
    scored: list[dict[str, object]],
    *,
    target_keep_seconds: float,
    min_keep_seconds: float,
    max_keep_seconds: float,
    force_keep: list[str],
    force_drop: list[str],
    compact_keep: CompactKeepSettings = DEFAULT_COMPACT_KEEP,
) -> dict[str, float]:
    allocations: dict[str, float] = {}
    free_items: list[dict[str, object]] = []

    for item in scored:
        clip: PremiereSequenceClip = item["clip"]  # type: ignore[assignment]
        duration = float(item["duration_seconds"])
        if _name_matches(clip.name, force_drop):
            allocations[clip.clipitem_id] = 0.0
            continue
        if _name_matches(clip.name, force_keep):
            # Still shorten forced clips under compact mode; do not keep full long holds.
            floor, cap = _keep_bounds_for_clip(clip, duration, compact_keep=compact_keep)
            allocations[clip.clipitem_id] = cap if compact_keep.enabled else duration
            continue
        free_items.append(item)

    fixed_keep = sum(allocations.values())
    remaining_budget = max(0.0, max_keep_seconds - fixed_keep)
    if not free_items:
        return allocations

    weights: list[float] = []
    caps: list[float] = []
    floors: list[float] = []
    for item in free_items:
        clip = item["clip"]
        duration = float(item["duration_seconds"])
        score = max(0.15, float(item["score"]))
        floor, cap = _keep_bounds_for_clip(clip, duration, compact_keep=compact_keep)
        floors.append(floor)
        caps.append(cap)
        weights.append(score * (duration ** 0.5))

    # Compact policy: stay near floors (minimal keep). Grow only to reach sequence min budget.
    # Legacy policy: fill leftover toward caps up to max budget.
    floor_total = sum(floors)
    if compact_keep.enabled:
        values = list(floors)
        current_total = fixed_keep + sum(values)
        if current_total < min_keep_seconds:
            need = min(remaining_budget, min_keep_seconds - current_total)
            order = sorted(range(len(values)), key=lambda index: -weights[index])
            for index in order:
                if need <= 0:
                    break
                room = caps[index] - values[index]
                if room <= 0:
                    continue
                add = min(room, need)
                values[index] += add
                need -= add
    else:
        if floor_total >= remaining_budget:
            values = list(floors)
        else:
            leftover = remaining_budget - floor_total
            weight_sum = sum(weights) or 1.0
            values = []
            for floor, cap, weight in zip(floors, caps, weights):
                room = max(0.0, cap - floor)
                extra = leftover * (weight / weight_sum)
                values.append(min(cap, floor + min(room, extra)))

            total = sum(values)
            if total > remaining_budget and total > floor_total:
                surplus = total - floor_total
                allowed_surplus = max(0.0, remaining_budget - floor_total)
                scale = allowed_surplus / surplus if surplus > 0 else 0.0
                values = [floor + max(0.0, (value - floor) * scale) for value, floor in zip(values, floors)]

    for item, value in zip(free_items, values):
        clip = item["clip"]
        allocations[clip.clipitem_id] = float(value)
    return allocations


def _keep_bounds_for_clip(
    clip: PremiereSequenceClip,
    duration_seconds: float,
    *,
    compact_keep: CompactKeepSettings,
) -> tuple[float, float]:
    duration_seconds = max(0.0, float(duration_seconds))
    if not compact_keep.enabled:
        if duration_seconds <= 4.0:
            return duration_seconds, duration_seconds
        if duration_seconds <= 12.0:
            cap = min(duration_seconds, max(3.0, duration_seconds * 0.75))
            floor = min(cap, max(2.0, duration_seconds * 0.35))
            return floor, cap
        cap = min(duration_seconds * 0.55, 45.0)
        floor = min(cap, max(3.0, min(8.0, duration_seconds * 0.12)))
        return floor, cap

    if is_still_image_clip(clip):
        photo_min = max(0.5, float(compact_keep.photo_keep_min_seconds))
        photo_max = max(photo_min, float(compact_keep.photo_keep_max_seconds))
        if duration_seconds <= photo_min:
            return duration_seconds, duration_seconds
        floor = min(duration_seconds, photo_min)
        cap = min(duration_seconds, photo_max)
        return floor, max(floor, cap)

    video_min = max(0.5, float(compact_keep.video_keep_min_seconds))
    video_max = max(video_min, float(compact_keep.video_keep_max_seconds))
    if duration_seconds <= video_min:
        return duration_seconds, duration_seconds
    floor = min(duration_seconds, video_min)
    # Tight cap: catch the point, do not keep long narrative chunks.
    cap = min(duration_seconds, video_max, max(video_min, duration_seconds * 0.22))
    return floor, max(floor, cap)


def _build_clip_decision(item: dict[str, object], *, keep_alloc_seconds: float) -> TrimClipDecision:
    clip: PremiereSequenceClip = item["clip"]  # type: ignore[assignment]
    duration_seconds = float(item["duration_seconds"])
    keep_alloc_seconds = max(0.0, min(duration_seconds, keep_alloc_seconds))
    segments = _carve_keep_drop_segments(
        clip,
        keep_seconds=keep_alloc_seconds,
        base_reason=str(item["reason"]),
        confidence=float(item["confidence"]),
    )
    keep_seconds = sum(seg.duration_seconds for seg in segments if seg.decision == "keep")
    drop_seconds = sum(seg.duration_seconds for seg in segments if seg.decision == "drop")
    decisions = {seg.decision for seg in segments}
    if decisions == {"keep"}:
        summary = "keep"
    elif decisions == {"drop"}:
        summary = "drop"
    else:
        summary = "mixed"

    return TrimClipDecision(
        order_index=clip.order_index,
        clipitem_id=clip.clipitem_id,
        name=clip.name,
        source_path=clip.source_path,
        track_index=clip.track_index,
        start=clip.start,
        end=clip.end,
        duration=clip.duration,
        duration_seconds=round(duration_seconds, 3),
        source_in=clip.in_point,
        source_out=clip.out_point,
        keep_seconds=round(keep_seconds, 3),
        drop_seconds=round(drop_seconds, 3),
        score=round(float(item["score"]), 3),
        reason=str(item["reason"]),
        confidence=round(float(item["confidence"]), 3),
        decision=summary,
        segments=segments,
    )


def _carve_keep_drop_segments(
    clip: PremiereSequenceClip,
    *,
    keep_seconds: float,
    base_reason: str,
    confidence: float,
    keep_start_seconds: float | None = None,
) -> list[TrimSegmentDecision]:
    duration_ticks = max(1, clip.duration)
    duration_seconds = ticks_to_seconds(duration_ticks)
    keep_seconds = max(0.0, min(duration_seconds, keep_seconds))

    if keep_seconds < _MIN_SEGMENT_SECONDS:
        return [
            _make_segment(
                clip,
                segment_index=1,
                decision="drop",
                local_start=0,
                local_end=duration_ticks,
                reason=f"no keep budget; {base_reason}",
                confidence=confidence * 0.8,
            )
        ]

    if duration_seconds - keep_seconds < _MIN_SEGMENT_SECONDS:
        # Full keep, but on longer takes still peel tiny head/tail as DROP when possible.
        if duration_seconds >= 20.0 and keep_start_seconds is None:
            lead = min(2.0, duration_seconds * 0.05)
            tail = min(2.0, duration_seconds * 0.05)
            if lead + tail + _MIN_SEGMENT_SECONDS <= duration_seconds:
                lead_ticks = seconds_to_ticks(lead)
                tail_ticks = seconds_to_ticks(tail)
                mid_end = duration_ticks - tail_ticks
                return _segments_from_ranges(
                    clip,
                    ranges=[
                        ("drop", 0, lead_ticks, "lead-in trim candidate"),
                        ("keep", lead_ticks, mid_end, f"primary keep window; {base_reason}"),
                        ("drop", mid_end, duration_ticks, "tail trim candidate"),
                    ],
                    confidence=confidence,
                )
        return [
            _make_segment(
                clip,
                segment_index=1,
                decision="keep",
                local_start=0,
                local_end=duration_ticks,
                reason=f"full clip keep; {base_reason}",
                confidence=confidence,
            )
        ]

    if keep_start_seconds is None:
        lead_seconds = min(duration_seconds * 0.12, 10.0)
        if lead_seconds + keep_seconds > duration_seconds:
            lead_seconds = max(0.0, duration_seconds - keep_seconds)
        keep_start_seconds = lead_seconds
    else:
        keep_start_seconds = max(0.0, min(duration_seconds - keep_seconds, float(keep_start_seconds)))

    keep_start = seconds_to_ticks(keep_start_seconds)
    keep_end = min(duration_ticks, keep_start + seconds_to_ticks(keep_seconds))
    if keep_end <= keep_start:
        keep_end = min(duration_ticks, keep_start + max(1, seconds_to_ticks(_MIN_SEGMENT_SECONDS)))

    ranges: list[tuple[str, int, int, str]] = []
    if keep_start > seconds_to_ticks(_MIN_SEGMENT_SECONDS):
        ranges.append(("drop", 0, keep_start, "pre-keep discard window"))
        ranges.append(("keep", keep_start, keep_end, f"keep island; {base_reason}"))
    else:
        ranges.append(("keep", 0, keep_end, f"keep island from start; {base_reason}"))
    if duration_ticks - keep_end > seconds_to_ticks(_MIN_SEGMENT_SECONDS):
        ranges.append(("drop", keep_end, duration_ticks, "post-keep discard window"))
    elif keep_end < duration_ticks:
        # Absorb tiny remainder into keep.
        ranges[-1] = (ranges[-1][0], ranges[-1][1], duration_ticks, ranges[-1][3])

    return _segments_from_ranges(clip, ranges=ranges, confidence=confidence)


def _segments_from_ranges(
    clip: PremiereSequenceClip,
    *,
    ranges: list[tuple[str, int, int, str]],
    confidence: float,
) -> list[TrimSegmentDecision]:
    segments: list[TrimSegmentDecision] = []
    for index, (decision, local_start, local_end, reason) in enumerate(ranges, start=1):
        if local_end <= local_start:
            continue
        segments.append(
            _make_segment(
                clip,
                segment_index=index,
                decision=decision,
                local_start=local_start,
                local_end=local_end,
                reason=reason,
                confidence=confidence if decision == "keep" else confidence * 0.85,
            )
        )
    return segments


def _make_segment(
    clip: PremiereSequenceClip,
    *,
    segment_index: int,
    decision: str,
    local_start: int,
    local_end: int,
    reason: str,
    confidence: float,
) -> TrimSegmentDecision:
    local_start = max(0, min(clip.duration, local_start))
    local_end = max(local_start + 1, min(clip.duration, local_end))
    duration = local_end - local_start
    return TrimSegmentDecision(
        segment_index=segment_index,
        decision=decision,
        local_start=local_start,
        local_end=local_end,
        timeline_start=clip.start + local_start,
        timeline_end=clip.start + local_end,
        source_in=clip.in_point + local_start,
        source_out=clip.in_point + local_end,
        duration=duration,
        duration_seconds=round(ticks_to_seconds(duration), 3),
        reason=reason,
        confidence=round(confidence, 3),
    )


def _score_clip(clip: PremiereSequenceClip, *, index: int, total: int) -> dict[str, object]:
    duration_seconds = ticks_to_seconds(clip.duration)
    score = 1.0
    reasons: list[str] = []

    if 2.0 <= duration_seconds <= 25.0:
        score += 1.4
        reasons.append("usable clip length")
    elif 25.0 < duration_seconds <= 60.0:
        score += 0.5
        reasons.append("long take; segment keep island inside")
    elif duration_seconds > 60.0:
        score += 0.2
        reasons.append("very long take; extract a keep island")
    elif duration_seconds < 1.0:
        score -= 1.0
        reasons.append("too short")
    else:
        score += 0.2
        reasons.append("short but readable")

    position_ratio = index / max(total - 1, 1)
    if position_ratio <= 0.15:
        score += 0.35
        reasons.append("early establishing position")
    elif position_ratio >= 0.85:
        score += 0.45
        reasons.append("closing position")
    elif 0.35 <= position_ratio <= 0.7:
        score += 0.25
        reasons.append("mid-story position")

    tokens = _tokenize(clip.name)
    if any(token in {"party", "dance", "birthday", "праздник", "день", "рожден", "танец"} for token in tokens):
        score += 0.35
        reasons.append("event-like name cue")
    if any(token in {"test", "trash", "outtake", "черн", "отход", "тест"} for token in tokens):
        score -= 0.8
        reasons.append("discard-like name cue")

    confidence = 0.4
    if 2.0 <= duration_seconds <= 25.0:
        confidence += 0.15
    confidence += min(0.2, 0.04 * len(reasons))

    return {
        "clip": clip,
        "duration_seconds": duration_seconds,
        "score": score,
        "confidence": min(0.8, confidence),
        "reason": "; ".join(reasons) if reasons else "neutral heuristic",
    }


def _build_open_questions(decisions: list[TrimClipDecision], *, context_notes: str) -> list[str]:
    questions = [
        "Who is the main person or people this video should center on?",
        "What is the one sentence story or occasion (birthday, trip, home day)?",
        "Inside long takes, which moments are the real peak (gesture, reaction, speech)?",
    ]
    if not context_notes.strip():
        questions.insert(0, "Add context_notes in the config so keep-islands can follow story intent.")
    mixed = [item for item in decisions if item.decision == "mixed"]
    if mixed:
        questions.append(
            "For mixed clips, should keep-islands move earlier/later inside the take?"
        )
    return questions


def _normalize_name_filters(values: list[str] | None) -> list[str]:
    if not values:
        return []
    return [value.strip().casefold() for value in values if value and value.strip()]


def _name_matches(name: str, filters: list[str]) -> bool:
    if not filters:
        return False
    haystack = name.casefold()
    return any(token in haystack for token in filters)


def _tokenize(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_PATTERN.findall(value)}
