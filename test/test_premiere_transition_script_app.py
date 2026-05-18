from __future__ import annotations

from pathlib import Path

from models.video_sequence import PremiereSequenceClip
from utils.premiere_project import PREMIERE_TICKS_PER_SECOND
from utils.premiere_transition_script import (
    build_premiere_transition_extendscript,
    build_transition_script_jobs,
)


def test_build_transition_script_jobs_uses_adjacent_edit_points() -> None:
    clips = [
        _clip("first.png", 0, 4),
        _clip("second.png", 4, 8),
        _clip("third.png", 8, 12),
    ]

    jobs = build_transition_script_jobs(clips, duration_seconds=1.0)

    assert [job.previous_clip_name for job in jobs] == ["first.png", "second.png"]
    assert [job.next_clip_name for job in jobs] == ["second.png", "third.png"]
    assert [job.edit_seconds for job in jobs] == [4.0, 8.0]


def test_build_transition_script_jobs_uses_optimizer_transition_plans() -> None:
    clips = [
        _clip("first.png", 0, 4),
        _clip("second.mp4", 4, 8),
    ]

    jobs = build_transition_script_jobs(
        clips,
        duration_seconds=1.0,
        transition_name="Cross Dissolve",
        transition_plans=[
            {
                "transition_name": "Film Dissolve",
                "recommended_duration": PREMIERE_TICKS_PER_SECOND // 2,
                "reason": "soft bridge",
            }
        ],
    )

    assert jobs[0].transition_name == "Film Dissolve"
    assert jobs[0].fallback_transition_name == "Cross Dissolve"
    assert jobs[0].duration_seconds == 0.5
    assert jobs[0].transition_reason == "soft bridge"


def test_build_premiere_transition_extendscript_contains_qe_transition_calls() -> None:
    jobs = build_transition_script_jobs([_clip("a.png", 0, 4), _clip("b.png", 4, 8)])

    script = build_premiere_transition_extendscript(
        sequence_name="Ivan26_o04",
        jobs=jobs,
        transition_name="Cross Dissolve",
        log_path=Path("C:/tmp/Ivan26_o04_apply_transitions.log"),
    )

    assert "app.enableQE()" in script
    assert "getVideoTransitionByName" in script
    assert "findVideoTransition" in script
    assert "addTransition(transition)" in script
    assert "Ivan26_o04" in script
    assert "a.png" in script
    assert "b.png" in script


def _clip(name: str, start_seconds: int, end_seconds: int) -> PremiereSequenceClip:
    start = start_seconds * PREMIERE_TICKS_PER_SECOND
    end = end_seconds * PREMIERE_TICKS_PER_SECOND
    return PremiereSequenceClip(
        sequence_name="Sequence",
        order_index=start_seconds + 1,
        track_index=1,
        clipitem_id=f"track-item-{name}",
        name=name,
        source_path=f"C:/media/{name}",
        start=start,
        end=end,
        in_point=0,
        out_point=end - start,
        duration=end - start,
        stage_id=Path(name).stem,
        video_index=1,
    )
