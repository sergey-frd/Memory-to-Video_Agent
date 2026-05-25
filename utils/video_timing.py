from __future__ import annotations

VALID_VIDEO_DURATION_SECONDS = (6, 10)


def video_segment_schedule(duration_seconds: int) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Return the three-shot timing schedule used by video prompt generation."""
    if duration_seconds == 6:
        return ((0, 2), (2, 4), (4, 6))
    if duration_seconds == 10:
        return ((0, 4), (4, 7), (7, 10))
    allowed = ", ".join(str(value) for value in VALID_VIDEO_DURATION_SECONDS)
    raise ValueError(f"video_duration_seconds must be one of: {allowed}.")


def video_segment_markers(duration_seconds: int) -> tuple[str, str, str]:
    return tuple(f"{start}-{end}s" for start, end in video_segment_schedule(duration_seconds))


def video_segment_duration_pattern(duration_seconds: int) -> str:
    return "-".join(str(end - start) for start, end in video_segment_schedule(duration_seconds))
