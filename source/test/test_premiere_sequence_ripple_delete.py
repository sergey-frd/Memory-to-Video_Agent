from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils.premiere_project import PREMIERE_TICKS_PER_SECOND
from utils.premiere_sequence_ripple_delete import (
    build_expected_ripple_pieces,
    build_ripple_delete_proof,
    validate_ripple_delete_plan,
)


def _plan() -> dict[str, object]:
    return {
        "task_id": "TASK_021",
        "timeline": {
            "input_sequence": "SF_26_BD_LONG_FAMILY_NURI_v02",
            "output_sequence": "SF_26_BD_LONG_FAMILY_NURI_v03",
            "fps": 25,
            "input_frame_count_expected": 3483,
            "output_frame_count_expected": 2876,
        },
        "edit_policy": {
            "mode": "ripple_delete_only",
            "insertions": 0,
            "reordering": False,
            "fine_trimming": False,
        },
        "delete_ranges_on_original_v02": [
            {"id": "D05", "start_frame": 2973, "end_frame_exclusive": 3171, "duration_frames": 198},
            {"id": "D04", "start_frame": 2833, "end_frame_exclusive": 2893, "duration_frames": 60},
            {"id": "D03", "start_frame": 2709, "end_frame_exclusive": 2727, "duration_frames": 18},
            {"id": "D02", "start_frame": 2456, "end_frame_exclusive": 2676, "duration_frames": 220},
            {"id": "D01", "start_frame": 950, "end_frame_exclusive": 1061, "duration_frames": 111},
        ],
        "expected_keep_map": [
            {"source_v02_start": 0, "source_v02_end_exclusive": 950, "output_v03_start": 0, "output_v03_end_exclusive": 950},
            {"source_v02_start": 1061, "source_v02_end_exclusive": 2456, "output_v03_start": 950, "output_v03_end_exclusive": 2345},
            {"source_v02_start": 2676, "source_v02_end_exclusive": 2709, "output_v03_start": 2345, "output_v03_end_exclusive": 2378},
            {"source_v02_start": 2727, "source_v02_end_exclusive": 2833, "output_v03_start": 2378, "output_v03_end_exclusive": 2484},
            {"source_v02_start": 2893, "source_v02_end_exclusive": 2973, "output_v03_start": 2484, "output_v03_end_exclusive": 2564},
            {"source_v02_start": 3171, "source_v02_end_exclusive": 3483, "output_v03_start": 2564, "output_v03_end_exclusive": 2876},
        ],
    }


def test_task021_plan_is_exactly_five_deletes_and_607_frames() -> None:
    validate_ripple_delete_plan(_plan())


def test_task021_rejects_insertions_or_reordering() -> None:
    plan = _plan()
    plan["edit_policy"]["insertions"] = 1  # type: ignore[index]
    with pytest.raises(ValueError, match="ripple deletes only"):
        validate_ripple_delete_plan(plan)


def test_ripple_model_splits_clip_across_retained_islands() -> None:
    frame_ticks = PREMIERE_TICKS_PER_SECOND // 25
    source_items = [
        SimpleNamespace(
            name="SOURCE",
            start=0,
            end=3483 * frame_ticks,
            source_in=100 * frame_ticks,
            source_out=3583 * frame_ticks,
        )
    ]
    pieces = build_expected_ripple_pieces(
        source_items,
        _plan()["expected_keep_map"],  # type: ignore[arg-type]
        fps=25,
    )
    assert len(pieces) == 6
    assert [item["timeline_out_frame"] for item in pieces] == [
        950,
        2345,
        2378,
        2484,
        2564,
        2876,
    ]
    assert sum(int(item["duration_frames"]) for item in pieces) == 2876
    assert pieces[-1]["source_in_frame"] == 3271


def test_ripple_model_preserves_existing_clip_boundaries() -> None:
    frame_ticks = PREMIERE_TICKS_PER_SECOND // 25
    source_items = [
        SimpleNamespace(
            name="A",
            start=0,
            end=500 * frame_ticks,
            source_in=0,
            source_out=500 * frame_ticks,
        ),
        SimpleNamespace(
            name="B",
            start=500 * frame_ticks,
            end=1200 * frame_ticks,
            source_in=200 * frame_ticks,
            source_out=900 * frame_ticks,
        ),
    ]
    keep_map = [
        {
            "source_v02_start": 0,
            "source_v02_end_exclusive": 950,
            "output_v03_start": 0,
            "output_v03_end_exclusive": 950,
        }
    ]
    pieces = build_expected_ripple_pieces(source_items, keep_map, fps=25)
    assert [(item["source_sequence_name"], item["duration_frames"]) for item in pieces] == [
        ("A", 500),
        ("B", 450),
    ]
    assert pieces[-1]["source_out_frame"] == 650


def test_ripple_proof_has_no_deleted_overlap() -> None:
    plan = _plan()
    frame_ticks = PREMIERE_TICKS_PER_SECOND // 25
    source_items = [
        SimpleNamespace(
            name="SOURCE",
            start=0,
            end=3483 * frame_ticks,
            source_in=0,
            source_out=3483 * frame_ticks,
        )
    ]
    actual = build_expected_ripple_pieces(
        source_items,
        plan["expected_keep_map"],  # type: ignore[arg-type]
        fps=25,
    )
    proof = build_ripple_delete_proof(
        plan=plan,
        actual=actual,
        preview_frames=2876,
    )
    assert proof["total_removed_frames"] == 607
    assert proof["total_retained_frames"] == 2876
    assert proof["no_retained_overlap_with_deleted_ranges"] is True
    assert proof["preview_and_project_frames_match"] is True
