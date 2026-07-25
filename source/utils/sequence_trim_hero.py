from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Callable

from api.openai_hero_match import match_hero_in_frames_with_openai
from models.sequence_trim_review import SequenceTrimReviewResult, TrimClipDecision, TrimSegmentDecision
from models.video_sequence import PremiereSequenceClip
from utils.sequence_trim_classifier import is_still_image_clip, seconds_to_ticks, ticks_to_seconds
from utils.video_frame_extract import extract_video_frames


HeroMatcher = Callable[..., list[dict[str, object]]]
ProgressCallback = Callable[[str], None]
_LEVEL_PRIORITY = {"absent": 0, "uncertain": 1, "medium": 2, "high": 3}


def classify_sequence_trim_review_hero(
    clips: list[PremiereSequenceClip],
    *,
    source_project_path: Path,
    source_sequence_name: str,
    new_sequence_name: str,
    hero_definition_path: Path,
    frames_dir: Path,
    model: str | None = None,
    frame_interval_seconds: float = 5.0,
    max_frames_per_clip: int = 48,
    frames_per_request: int = 10,
    reference_image_limit: int = 6,
    pre_roll_seconds: float = 10.0,
    post_roll_seconds: float = 10.0,
    keep_medium_matches: bool = True,
    keep_clip_on_analysis_error: bool = True,
    high_confidence_threshold: float = 0.85,
    medium_confidence_threshold: float = 0.55,
    max_image_edge: int = 1024,
    request_timeout_seconds: float = 180.0,
    cache_dir: Path | None = None,
    resume_from_cache: bool = True,
    context_notes: str = "",
    matcher: HeroMatcher = match_hero_in_frames_with_openai,
    progress: ProgressCallback | None = None,
) -> SequenceTrimReviewResult:
    if not clips:
        raise ValueError("No clips available for hero trim review classification.")
    hero_definition = json.loads(hero_definition_path.read_text(encoding="utf-8"))
    reference_paths = _load_reference_paths(hero_definition, limit=reference_image_limit)
    frames_dir.mkdir(parents=True, exist_ok=True)
    effective_cache_dir = cache_dir or frames_dir.parent / "hero_match_cache"
    effective_cache_dir.mkdir(parents=True, exist_ok=True)
    hero_definition_sha256 = hashlib.sha256(hero_definition_path.read_bytes()).hexdigest()

    decisions: list[TrimClipDecision] = []
    warnings: list[str] = []
    _emit(
        progress,
        (
            f"Hero engine: {len(clips)} clips; model={model or 'default'}; "
            f"references={len(reference_paths)}; interval={frame_interval_seconds:.1f}s."
        ),
    )
    for clip_number, clip in enumerate(clips, start=1):
        duration_seconds = max(0.001, ticks_to_seconds(clip.duration))
        source_path = Path(clip.source_path)
        clip_prefix = f"[{clip_number}/{len(clips)}] {clip.name}"
        fingerprint = _clip_fingerprint(
            clip,
            source_path=source_path,
            hero_definition_sha256=hero_definition_sha256,
            settings={
                "model": model,
                "frame_interval_seconds": frame_interval_seconds,
                "max_frames_per_clip": max_frames_per_clip,
                "frames_per_request": frames_per_request,
                "reference_image_limit": reference_image_limit,
                "pre_roll_seconds": pre_roll_seconds,
                "post_roll_seconds": post_roll_seconds,
                "keep_medium_matches": keep_medium_matches,
                "high_confidence_threshold": high_confidence_threshold,
                "medium_confidence_threshold": medium_confidence_threshold,
                "max_image_edge": max_image_edge,
            },
        )
        cache_path = effective_cache_dir / f"{clip_number:04d}_{_safe_stem(clip.clipitem_id)}.json"
        if resume_from_cache:
            cached = _load_cached_decision(cache_path, fingerprint=fingerprint)
            if cached is not None:
                decisions.append(cached)
                _emit(
                    progress,
                    (
                        f"{clip_prefix}: cache hit; hero={cached.hero_match_level or '-'}; "
                        f"KEEP={cached.keep_seconds:.1f}s, DROP={cached.drop_seconds:.1f}s."
                    ),
                )
                continue

        if not source_path.exists():
            warning = f"Missing media for hero analysis; clip kept for manual review: {clip.name}"
            warnings.append(warning)
            decision = _analysis_error_decision(clip, warning)
            decisions.append(decision)
            _write_cached_decision(cache_path, fingerprint=fingerprint, decision=decision)
            _emit(progress, f"{clip_prefix}: media missing; marked KEEP-REVIEW.")
            continue

        try:
            local_timestamps = choose_hero_sample_timestamps(
                duration_seconds,
                frame_interval_seconds=frame_interval_seconds,
                max_frames=max_frames_per_clip,
            )
            if is_still_image_clip(clip):
                frame_paths = [(duration_seconds * 0.5, source_path)]
                _emit(progress, f"{clip_prefix}: analyzing one still image.")
            else:
                _emit(
                    progress,
                    f"{clip_prefix}: extracting {len(local_timestamps)} frames from {duration_seconds:.1f}s.",
                )
                source_in_seconds = max(0.0, ticks_to_seconds(clip.in_point))
                source_timestamps = [source_in_seconds + value for value in local_timestamps]
                extracted = extract_video_frames(
                    source_path,
                    output_dir=frames_dir / f"{_safe_stem(clip.name)}_{_safe_stem(clip.clipitem_id)}",
                    timestamps_sec=source_timestamps,
                    prefix=_safe_stem(clip.name),
                )
                frame_paths = [
                    (local_timestamp, extracted_frame_path)
                    for local_timestamp, (_source_timestamp, extracted_frame_path) in zip(
                        local_timestamps, extracted
                    )
                ]

            frame_matches: list[dict[str, object]] = []
            request_batch_size = max(1, frames_per_request)
            batch_count = math.ceil(len(frame_paths) / request_batch_size)
            for batch_number, start in enumerate(
                range(0, len(frame_paths), request_batch_size),
                start=1,
            ):
                batch = frame_paths[start : start + request_batch_size]
                _emit(
                    progress,
                    (
                        f"{clip_prefix}: OpenAI request {batch_number}/{batch_count} sent "
                        f"({len(batch)} frames); waiting up to {request_timeout_seconds:.0f}s..."
                    ),
                )
                request_started = time.monotonic()
                frame_matches.extend(
                    matcher(
                        frame_paths=batch,
                        reference_image_paths=reference_paths,
                        hero_definition=hero_definition,
                        clip_name=clip.name,
                        model=model,
                        high_confidence_threshold=high_confidence_threshold,
                        medium_confidence_threshold=medium_confidence_threshold,
                        max_image_edge=max_image_edge,
                        request_timeout_seconds=request_timeout_seconds,
                    )
                )
                _emit(
                    progress,
                    (
                        f"{clip_prefix}: OpenAI request {batch_number}/{batch_count} completed "
                        f"in {time.monotonic() - request_started:.1f}s."
                    ),
                )

            decision = _build_hero_decision(
                clip,
                frame_matches=frame_matches,
                is_still=is_still_image_clip(clip),
                pre_roll_seconds=pre_roll_seconds,
                post_roll_seconds=post_roll_seconds,
                keep_medium_matches=keep_medium_matches,
            )
            decisions.append(decision)
            _write_cached_decision(cache_path, fingerprint=fingerprint, decision=decision)
            _emit(
                progress,
                (
                    f"{clip_prefix}: done; hero={decision.hero_match_level or '-'}; "
                    f"KEEP={decision.keep_seconds:.1f}s, DROP={decision.drop_seconds:.1f}s."
                ),
            )
        except KeyboardInterrupt:
            _emit(progress, f"{clip_prefix}: interrupted; completed clips remain in cache.")
            raise
        except Exception as exc:  # noqa: BLE001 - preserve footage when analysis fails
            warning = f"Hero analysis failed; clip kept for manual review: {clip.name}: {exc}"
            warnings.append(warning)
            if keep_clip_on_analysis_error:
                decision = _analysis_error_decision(clip, warning)
            else:
                decision = _all_drop_decision(clip, warning, match_level="uncertain")
            decisions.append(decision)
            _write_cached_decision(cache_path, fingerprint=fingerprint, decision=decision)
            _emit(progress, f"{clip_prefix}: analysis error; marked {decision.decision.upper()}-REVIEW: {exc}")

    total_source_seconds = sum(item.duration_seconds for item in decisions)
    keep_seconds = sum(item.keep_seconds for item in decisions)
    drop_seconds = sum(item.drop_seconds for item in decisions)
    high_count = sum(item.hero_match_level == "high" for item in decisions)
    medium_count = sum(item.hero_match_level == "medium" for item in decisions)
    uncertain_count = sum(item.hero_match_level == "uncertain" for item in decisions)
    return SequenceTrimReviewResult(
        source_project_path=str(source_project_path),
        source_sequence_name=source_sequence_name,
        new_sequence_name=new_sequence_name,
        engine="hero_presence_v1",
        target_keep_seconds=0.0,
        min_keep_seconds=0.0,
        max_keep_seconds=round(total_source_seconds, 3),
        total_source_seconds=round(total_source_seconds, 3),
        keep_seconds=round(keep_seconds, 3),
        drop_seconds=round(drop_seconds, 3),
        context_notes=context_notes.strip(),
        engine_metadata={
            "hero_definition_path": str(hero_definition_path),
            "hero_name": _hero_name(hero_definition),
            "reference_image_count": len(reference_paths),
            "frame_interval_seconds": frame_interval_seconds,
            "max_frames_per_clip": max_frames_per_clip,
            "pre_roll_seconds": pre_roll_seconds,
            "post_roll_seconds": post_roll_seconds,
            "keep_medium_matches": keep_medium_matches,
            "high_confidence_threshold": high_confidence_threshold,
            "medium_confidence_threshold": medium_confidence_threshold,
            "request_timeout_seconds": request_timeout_seconds,
            "resume_from_cache": resume_from_cache,
            "cache_dir": str(effective_cache_dir),
            "high_match_clips": high_count,
            "medium_match_clips": medium_count,
            "uncertain_clips": uncertain_count,
        },
        open_questions=[],
        warnings=warnings,
        decisions=decisions,
    )


def choose_hero_sample_timestamps(
    duration_seconds: float,
    *,
    frame_interval_seconds: float,
    max_frames: int,
) -> list[float]:
    duration = max(0.1, float(duration_seconds))
    interval = max(0.5, float(frame_interval_seconds))
    count = max(1, min(max(1, int(max_frames)), int(math.ceil(duration / interval))))
    step = duration / count
    return [min(duration - 0.05, max(0.0, step * (index + 0.5))) for index in range(count)]


def _build_hero_decision(
    clip: PremiereSequenceClip,
    *,
    frame_matches: list[dict[str, object]],
    is_still: bool,
    pre_roll_seconds: float,
    post_roll_seconds: float,
    keep_medium_matches: bool,
) -> TrimClipDecision:
    duration_seconds = max(0.001, ticks_to_seconds(clip.duration))
    accepted_levels = {"high", "medium"} if keep_medium_matches else {"high"}
    accepted = [
        item for item in frame_matches if str(item.get("match_level") or "").casefold() in accepted_levels
    ]
    if is_still and accepted:
        best = max(accepted, key=lambda item: _LEVEL_PRIORITY.get(str(item.get("match_level")), 0))
        intervals = [(0.0, duration_seconds, str(best["match_level"]), float(best.get("confidence", 0.0)))]
    else:
        intervals = _merge_keep_intervals(
            [
                (
                    max(0.0, float(item["timestamp_sec"]) - max(0.0, pre_roll_seconds)),
                    min(duration_seconds, float(item["timestamp_sec"]) + max(0.0, post_roll_seconds)),
                    str(item["match_level"]),
                    float(item.get("confidence", 0.0)),
                )
                for item in accepted
            ]
        )

    if not intervals:
        strongest = _strongest_match(frame_matches)
        return _all_drop_decision(
            clip,
            "Герой не найден с требуемой уверенностью.",
            match_level=str(strongest.get("match_level") or "absent"),
            confidence=float(strongest.get("confidence", 0.85) or 0.85),
            frame_matches=frame_matches,
        )

    segments = _segments_from_intervals(clip, intervals)
    keep_seconds = sum(item.duration_seconds for item in segments if item.decision == "keep")
    drop_seconds = sum(item.duration_seconds for item in segments if item.decision == "drop")
    clip_level = max((item[2] for item in intervals), key=lambda level: _LEVEL_PRIORITY[level])
    confidence = max(item[3] for item in intervals)
    decision = "keep" if not drop_seconds else "mixed"
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
        score=round(confidence, 3),
        reason=(
            f"Герой найден ({clip_level}); сохранены окна с контекстом "
            f"{pre_roll_seconds:.1f}с до / {post_roll_seconds:.1f}с после."
        ),
        confidence=round(confidence, 3),
        decision=decision,
        segments=segments,
        hero_match_level=clip_level,
        hero_frame_matches=frame_matches,
    )


def _merge_keep_intervals(
    intervals: list[tuple[float, float, str, float]],
) -> list[tuple[float, float, str, float]]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda item: (item[0], item[1]))
    merged: list[tuple[float, float, str, float]] = [ordered[0]]
    for start, end, level, confidence in ordered[1:]:
        previous_start, previous_end, previous_level, previous_confidence = merged[-1]
        if start <= previous_end + 0.25:
            strongest_level = max((previous_level, level), key=lambda value: _LEVEL_PRIORITY[value])
            merged[-1] = (
                previous_start,
                max(previous_end, end),
                strongest_level,
                max(previous_confidence, confidence),
            )
        else:
            merged.append((start, end, level, confidence))
    return merged


def _segments_from_intervals(
    clip: PremiereSequenceClip,
    intervals: list[tuple[float, float, str, float]],
) -> list[TrimSegmentDecision]:
    duration_seconds = ticks_to_seconds(clip.duration)
    ranges: list[tuple[str, float, float, str, float]] = []
    cursor = 0.0
    for start, end, level, confidence in intervals:
        if start > cursor + 0.05:
            ranges.append(("drop", cursor, start, "absent", max(0.0, 1.0 - confidence)))
        ranges.append(("keep", start, end, level, confidence))
        cursor = max(cursor, end)
    if cursor < duration_seconds - 0.05:
        ranges.append(("drop", cursor, duration_seconds, "absent", 0.8))

    segments: list[TrimSegmentDecision] = []
    for index, (decision, start, end, level, confidence) in enumerate(ranges, start=1):
        local_start = max(0, min(clip.duration, seconds_to_ticks(start)))
        local_end = max(local_start + 1, min(clip.duration, seconds_to_ticks(end)))
        duration = local_end - local_start
        reason = (
            f"hero {level}; контекст вокруг обнаружения"
            if decision == "keep"
            else "hero absent; вне окна обнаружения"
        )
        segments.append(
            TrimSegmentDecision(
                segment_index=index,
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
                hero_match_level=level,
            )
        )
    return segments


def _analysis_error_decision(clip: PremiereSequenceClip, reason: str) -> TrimClipDecision:
    return _full_clip_decision(clip, decision="keep", reason=reason, match_level="uncertain", confidence=0.0)


def _all_drop_decision(
    clip: PremiereSequenceClip,
    reason: str,
    *,
    match_level: str,
    confidence: float = 0.8,
    frame_matches: list[dict[str, object]] | None = None,
) -> TrimClipDecision:
    return _full_clip_decision(
        clip,
        decision="drop",
        reason=reason,
        match_level=match_level,
        confidence=confidence,
        frame_matches=frame_matches,
    )


def _full_clip_decision(
    clip: PremiereSequenceClip,
    *,
    decision: str,
    reason: str,
    match_level: str,
    confidence: float,
    frame_matches: list[dict[str, object]] | None = None,
) -> TrimClipDecision:
    duration_seconds = ticks_to_seconds(clip.duration)
    segment = TrimSegmentDecision(
        segment_index=1,
        decision=decision,
        local_start=0,
        local_end=clip.duration,
        timeline_start=clip.start,
        timeline_end=clip.end,
        source_in=clip.in_point,
        source_out=clip.out_point,
        duration=clip.duration,
        duration_seconds=round(duration_seconds, 3),
        reason=reason,
        confidence=round(confidence, 3),
        hero_match_level=match_level,
    )
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
        keep_seconds=round(duration_seconds if decision == "keep" else 0.0, 3),
        drop_seconds=round(duration_seconds if decision == "drop" else 0.0, 3),
        score=round(confidence, 3),
        reason=reason,
        confidence=round(confidence, 3),
        decision=decision,
        segments=[segment],
        hero_match_level=match_level,
        hero_frame_matches=frame_matches or [],
    )


def _strongest_match(frame_matches: list[dict[str, object]]) -> dict[str, object]:
    if not frame_matches:
        return {"match_level": "uncertain", "confidence": 0.0}
    return max(
        frame_matches,
        key=lambda item: (
            _LEVEL_PRIORITY.get(str(item.get("match_level") or "uncertain"), 1),
            float(item.get("confidence", 0.0) or 0.0),
        ),
    )


def _load_reference_paths(hero_definition: dict[str, object], *, limit: int) -> list[Path]:
    sources = hero_definition.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("hero_def.json is missing sources.")
    raw_images = sources.get("reference_images")
    if not isinstance(raw_images, list):
        raise ValueError("hero_def.json is missing sources.reference_images.")
    paths = [
        Path(str(item["path"]))
        for item in raw_images
        if isinstance(item, dict) and item.get("path") and Path(str(item["path"])).is_file()
    ]
    if not paths:
        raise ValueError("No existing reference images were found in hero_def.json.")
    count = max(1, min(len(paths), int(limit)))
    if count == len(paths):
        return paths
    if count == 1:
        return [paths[len(paths) // 2]]
    indexes = [round(index * (len(paths) - 1) / (count - 1)) for index in range(count)]
    return [paths[index] for index in indexes]


def _hero_name(hero_definition: dict[str, object]) -> str:
    definition = hero_definition.get("definition")
    if isinstance(definition, dict):
        return str(definition.get("hero_name") or "")
    return str(hero_definition.get("hero_name") or "")


def _safe_stem(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in Path(value).stem)
    return cleaned.strip("_") or "clip"


def _clip_fingerprint(
    clip: PremiereSequenceClip,
    *,
    source_path: Path,
    hero_definition_sha256: str,
    settings: dict[str, object],
) -> str:
    source_stat = source_path.stat() if source_path.exists() else None
    payload = {
        "clipitem_id": clip.clipitem_id,
        "source_path": str(source_path),
        "source_size": source_stat.st_size if source_stat else None,
        "source_mtime_ns": source_stat.st_mtime_ns if source_stat else None,
        "in_point": clip.in_point,
        "out_point": clip.out_point,
        "duration": clip.duration,
        "hero_definition_sha256": hero_definition_sha256,
        "settings": settings,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_cached_decision(cache_path: Path, *, fingerprint: str) -> TrimClipDecision | None:
    if not cache_path.is_file():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if payload.get("fingerprint") != fingerprint or not isinstance(payload.get("decision"), dict):
            return None
        decision_payload = dict(payload["decision"])
        segment_payloads = decision_payload.pop("segments", [])
        segments = [
            TrimSegmentDecision(**item)
            for item in segment_payloads
            if isinstance(item, dict)
        ]
        decision_payload["segments"] = segments
        return TrimClipDecision(**decision_payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_cached_decision(
    cache_path: Path,
    *,
    fingerprint: str,
    decision: TrimClipDecision,
) -> None:
    payload = {
        "fingerprint": fingerprint,
        "decision": decision.to_dict(),
    }
    temp_path = cache_path.with_suffix(f"{cache_path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(cache_path)


def _emit(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)
