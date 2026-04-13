from __future__ import annotations

import re
from pathlib import Path

from models.video_sequence import ClipLostEffectsSummary, LostEffectIssue, SequenceRecommendationEntry


_TRANSLATION_RESULTS_GLOB = "FCP Translation Results*.txt"
_EFFECT_ISSUE_PATTERN = re.compile(
    r"Sequence <(?P<sequence>[^>]+)> at .*?,\s*(?P<track_type>video|audio) track (?P<track_index>\d+): "
    r"Effect <(?P<effect>[^>]+)> on Clip <(?P<clip>[^>]+)> not translated\.",
    re.IGNORECASE,
)
_STAGE_ITEM_PATTERN = re.compile(
    r"(?P<stage_id>.+?)_(?:bg_image(?:_[A-Za-z0-9]+)*|video_\d+)$",
    re.IGNORECASE,
)


def is_translation_results_report_path(path: Path) -> bool:
    return path.name.casefold().startswith("fcp translation results") and path.suffix.casefold() == ".txt"


def find_translation_results_for_xml(xml_path: Path) -> Path | None:
    return resolve_translation_results_path(xml_path)


def resolve_translation_results_path(source_path: Path, requested_path: Path | None = None) -> Path | None:
    if requested_path is not None and is_translation_results_report_path(requested_path):
        return requested_path
    return _find_nearest_translation_results(source_path, requested_path)


def parse_fcp_translation_results(
    report_path: Path,
    *,
    selected_sequence_name: str | None = None,
) -> tuple[list[LostEffectIssue], list[str]]:
    if not is_translation_results_report_path(report_path):
        raise ValueError(f"Expected FCP Translation Results*.txt report, got: {report_path}")

    raw_lines = report_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    issues: list[LostEffectIssue] = []
    warnings: list[str] = []
    normalized_sequence = selected_sequence_name.casefold() if selected_sequence_name else None

    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line or line == "Translation issue:":
            continue

        match = _EFFECT_ISSUE_PATTERN.search(line)
        if match is not None:
            sequence_name = match.group("sequence")
            if normalized_sequence and sequence_name.casefold() != normalized_sequence:
                continue
            clip_name = match.group("clip")
            issues.append(
                LostEffectIssue(
                    sequence_name=sequence_name,
                    track_type=match.group("track_type").lower(),
                    track_index=int(match.group("track_index")),
                    clip_name=clip_name,
                    effect_name=match.group("effect"),
                    raw_message=line,
                    stage_id=extract_stage_id_from_media_name(clip_name),
                )
            )
            continue

        if "not translated" in line.casefold():
            warnings.append(line)

    return issues, warnings


def summarize_lost_effects(
    issues: list[LostEffectIssue],
    entries: list[SequenceRecommendationEntry],
) -> list[ClipLostEffectsSummary]:
    sequence_order = {
        entry.candidate.clip.stage_id: (entry.original_index, entry.recommended_index)
        for entry in entries
    }
    aggregated: dict[str, ClipLostEffectsSummary] = {}

    for issue in issues:
        aggregation_key = issue.stage_id or issue.clip_name
        summary = aggregated.get(aggregation_key)
        if summary is None:
            original_index = None
            recommended_index = None
            if issue.stage_id in sequence_order:
                original_index, recommended_index = sequence_order[issue.stage_id]
            summary = ClipLostEffectsSummary(
                clip_name=issue.clip_name,
                stage_id=issue.stage_id,
                effect_names=[],
                track_locations=[],
                original_index=original_index,
                recommended_index=recommended_index,
            )
            aggregated[aggregation_key] = summary

        if issue.effect_name not in summary.effect_names:
            summary.effect_names.append(issue.effect_name)
        track_location = f"{issue.track_type} {issue.track_index}"
        if track_location not in summary.track_locations:
            summary.track_locations.append(track_location)

    items = list(aggregated.values())
    for item in items:
        item.effect_names.sort(key=str.casefold)
        item.track_locations.sort(key=str.casefold)

    return sorted(
        items,
        key=lambda item: (
            item.recommended_index is None,
            item.recommended_index if item.recommended_index is not None else 10**9,
            item.clip_name.casefold(),
        ),
    )


def extract_stage_id_from_media_name(media_name: str) -> str | None:
    stem = Path(media_name).stem
    match = _STAGE_ITEM_PATTERN.match(stem)
    if match is None:
        return None
    return match.group("stage_id")


def _find_nearest_translation_results(source_path: Path, requested_path: Path | None = None) -> Path | None:
    search_paths: list[Path] = []
    for candidate in (requested_path, source_path):
        if candidate is None:
            continue
        if candidate.parent not in search_paths:
            search_paths.append(candidate.parent)

    candidates: list[Path] = []
    seen_candidates: set[str] = set()
    for directory in search_paths:
        for candidate in directory.glob(_TRANSLATION_RESULTS_GLOB):
            if not candidate.is_file():
                continue
            candidate_key = str(candidate.resolve()).casefold()
            if candidate_key in seen_candidates:
                continue
            seen_candidates.add(candidate_key)
            candidates.append(candidate)

    if not candidates:
        return None

    time_anchor = requested_path if requested_path is not None and requested_path.exists() else source_path
    try:
        anchor_mtime = time_anchor.stat().st_mtime
    except OSError:
        anchor_mtime = None

    preferred_directory = requested_path.parent if requested_path is not None else source_path.parent
    if anchor_mtime is None:
        return min(
            candidates,
            key=lambda path: (path.parent != preferred_directory, -path.stat().st_mtime, path.name.casefold()),
        )

    return min(
        candidates,
        key=lambda path: (
            path.parent != preferred_directory,
            abs(path.stat().st_mtime - anchor_mtime),
            -path.stat().st_mtime,
            path.name.casefold(),
        ),
    )
