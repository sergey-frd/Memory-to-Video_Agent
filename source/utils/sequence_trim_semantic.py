from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from api.openai_trim_semantic import choose_keep_window_with_openai
from models.sequence_trim_review import SequenceTrimReviewResult, TrimClipDecision
from models.video_sequence import PremiereSequenceClip
from utils.sequence_trim_classifier import (
    DEFAULT_COMPACT_KEEP,
    CompactKeepSettings,
    _allocate_keep_seconds,
    _build_open_questions,
    _carve_keep_drop_segments,
    _keep_bounds_for_clip,
    _normalize_name_filters,
    _score_clip,
    is_still_image_clip,
)
from utils.video_frame_extract import choose_sample_timestamps, extract_video_frames


def classify_sequence_trim_review_semantic(
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
    frames_per_clip: int = 5,
    model: str | None = None,
    frames_dir: Path | None = None,
    compact_keep: CompactKeepSettings | None = None,
    request_timeout_seconds: float = 180.0,
    progress: Callable[[str], None] | None = None,
) -> SequenceTrimReviewResult:
    if not clips:
        raise ValueError("No clips available for semantic trim review classification.")

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

    work_dir = frames_dir or (Path("test_runtime") / "trim_semantic_frames")
    work_dir.mkdir(parents=True, exist_ok=True)

    decisions: list[TrimClipDecision] = []
    warnings: list[str] = []
    _emit(
        progress,
        f"Semantic engine: {len(scored)} clips; model={model or 'default'}; frames per video={frames_per_clip}.",
    )
    for clip_number, item in enumerate(scored, start=1):
        clip: PremiereSequenceClip = item["clip"]  # type: ignore[assignment]
        clip_prefix = f"[{clip_number}/{len(scored)}] {clip.name}"
        keep_alloc = float(allocations[clip.clipitem_id])
        duration_seconds = float(item["duration_seconds"])
        base_reason = str(item["reason"])
        confidence = float(item["confidence"])
        keep_start: float | None = None
        keep_seconds = keep_alloc
        reason = base_reason
        floor, cap = _keep_bounds_for_clip(clip, duration_seconds, compact_keep=compact)

        if keep_alloc <= 0.05:
            reason = f"forced/no keep budget; {base_reason}"
            keep_seconds = 0.0
            _emit(progress, f"{clip_prefix}: no KEEP budget; semantic request skipped.")
        elif is_still_image_clip(clip):
            keep_seconds = min(duration_seconds, max(floor, min(cap, keep_alloc)))
            keep_start = max(0.0, (duration_seconds - keep_seconds) * 0.5)
            reason = (
                f"compact still hold {keep_seconds:.1f}s "
                f"(range {floor:.1f}-{cap:.1f}s); {base_reason}"
            )
            _emit(progress, f"{clip_prefix}: still image; compact KEEP={keep_seconds:.1f}s, API skipped.")
        elif keep_alloc >= duration_seconds - 0.05 and not compact.enabled:
            reason = f"full-clip keep; {base_reason}"
            keep_seconds = duration_seconds
            _emit(progress, f"{clip_prefix}: full clip KEEP; semantic request skipped.")
        else:
            source_path = Path(clip.source_path)
            if not source_path.exists():
                warnings.append(f"Missing media for semantic analysis, compact heuristic used: {clip.name}")
                keep_seconds = min(duration_seconds, max(floor, min(cap, keep_alloc)))
                keep_start = None
                reason = f"semantic fallback (missing media); compact {keep_seconds:.1f}s; {base_reason}"
                _emit(progress, f"{clip_prefix}: media missing; using compact fallback.")
            else:
                try:
                    timestamps = choose_sample_timestamps(duration_seconds, frames_per_clip)
                    clip_frames_dir = work_dir / _safe_stem(clip.name)
                    _emit(
                        progress,
                        f"{clip_prefix}: extracting {len(timestamps)} frames from {duration_seconds:.1f}s.",
                    )
                    frames = extract_video_frames(
                        source_path,
                        output_dir=clip_frames_dir,
                        timestamps_sec=timestamps,
                        prefix=_safe_stem(clip.name),
                    )
                    _emit(
                        progress,
                        (
                            f"{clip_prefix}: OpenAI semantic request sent ({len(frames)} frames); "
                            f"waiting up to {request_timeout_seconds:.0f}s..."
                        ),
                    )
                    request_started = time.monotonic()
                    semantic = choose_keep_window_with_openai(
                        frame_paths=frames,
                        clip_name=clip.name,
                        duration_seconds=duration_seconds,
                        keep_seconds=min(cap, max(floor, keep_alloc)),
                        keep_seconds_min=floor,
                        keep_seconds_max=cap,
                        media_kind="video",
                        context_notes=context_notes,
                        model=model,
                        request_timeout_seconds=request_timeout_seconds,
                    )
                    _emit(
                        progress,
                        (
                            f"{clip_prefix}: OpenAI response received in "
                            f"{time.monotonic() - request_started:.1f}s."
                        ),
                    )
                    keep_start = float(semantic["keep_start_sec"])
                    keep_seconds = float(semantic["keep_duration_sec"])
                    reason = f"semantic compact: {semantic['reason']}"
                    confidence = float(semantic["confidence"])
                except Exception as exc:  # noqa: BLE001 - keep batch resilient
                    warnings.append(f"Semantic analysis failed for {clip.name}: {exc}")
                    keep_seconds = min(duration_seconds, max(floor, min(cap, keep_alloc)))
                    keep_start = None
                    reason = f"semantic fallback; compact {keep_seconds:.1f}s; {base_reason}"
                    _emit(progress, f"{clip_prefix}: semantic error; using compact fallback: {exc}")

        segments = _carve_keep_drop_segments(
            clip,
            keep_seconds=keep_seconds,
            base_reason=reason,
            confidence=confidence,
            keep_start_seconds=keep_start,
        )
        keep_total = sum(seg.duration_seconds for seg in segments if seg.decision == "keep")
        drop_total = sum(seg.duration_seconds for seg in segments if seg.decision == "drop")
        labels = {seg.decision for seg in segments}
        if labels == {"keep"}:
            summary = "keep"
        elif labels == {"drop"}:
            summary = "drop"
        else:
            summary = "mixed"
        decisions.append(
            TrimClipDecision(
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
                keep_seconds=round(keep_total, 3),
                drop_seconds=round(drop_total, 3),
                score=round(float(item["score"]), 3),
                reason=reason,
                confidence=round(confidence, 3),
                decision=summary,
                segments=segments,
            )
        )
        _emit(
            progress,
            (
                f"{clip_prefix}: done; decision={summary}; "
                f"KEEP={keep_total:.1f}s, DROP={drop_total:.1f}s."
            ),
        )

    total_source_seconds = sum(item.duration_seconds for item in decisions)
    keep_seconds_total = sum(item.keep_seconds for item in decisions)
    drop_seconds_total = sum(item.drop_seconds for item in decisions)
    if keep_seconds_total > max_budget + 1.0:
        warnings.append(
            f"Semantic keep duration {keep_seconds_total:.1f}s exceeds max budget {max_budget:.1f}s."
        )
    if compact.enabled:
        warnings.append(
            "Compact keep enabled: stills aim for "
            f"{compact.photo_keep_min_seconds:.1f}-{compact.photo_keep_max_seconds:.1f}s; "
            f"video keep islands aim for {compact.video_keep_min_seconds:.1f}-{compact.video_keep_max_seconds:.1f}s."
        )

    engine = "semantic_frame_budget_v1_compact" if compact.enabled else "semantic_frame_budget_v1"
    return SequenceTrimReviewResult(
        source_project_path=str(source_project_path),
        source_sequence_name=source_sequence_name,
        new_sequence_name=new_sequence_name,
        engine=engine,
        target_keep_seconds=target_keep_seconds,
        min_keep_seconds=min_keep_seconds,
        max_keep_seconds=max_budget,
        total_source_seconds=round(total_source_seconds, 3),
        keep_seconds=round(keep_seconds_total, 3),
        drop_seconds=round(drop_seconds_total, 3),
        context_notes=context_notes.strip(),
        open_questions=_build_open_questions(decisions, context_notes=context_notes),
        warnings=warnings,
        decisions=decisions,
    )


def _safe_stem(value: str) -> str:
    stem = Path(value).stem
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in stem)
    return cleaned.strip("_") or "clip"


def _emit(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)
