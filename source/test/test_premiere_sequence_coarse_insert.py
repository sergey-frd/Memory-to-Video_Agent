from __future__ import annotations

import copy

import pytest

from utils.premiere_sequence_coarse_insert import (
    FAMILY_SOURCE,
    KEEP_SOURCE,
    NURI_SOURCE,
    build_coarse_insert_segments,
    build_insertion_proof,
    validate_coarse_insert_plan,
)


def _plan() -> dict[str, object]:
    groups = {
        "G1": ["F01", "S01", "S02", "S03", "F02", "F03"],
        "G2": ["S04", "S05", "F04", "S06", "S07", "F05"],
        "G3": ["S08", "F06", "F07", "S09", "F08", "S10"],
        "G4": ["F09", "F10", "S11", "F11", "F12"],
    }
    order = [
        "K1",
        *groups["G1"],
        "K2",
        *groups["G2"],
        *groups["G3"],
        "K3",
        *groups["G4"],
        "N1",
        "K4",
    ]
    keep = {
        "K1": (0, 1550),
        "K2": (2040, 2495),
        "K3": (2878, 4015),
        "K4": (4315, 5000),
    }
    family_durations = {
        segment_id: (60 if segment_id.startswith("F") else 12)
        for segment_id in [value for group in groups.values() for value in group]
    }
    definitions: dict[str, object] = {}
    for segment_id, (source_in, source_out) in keep.items():
        definitions[segment_id] = {
            "source_sequence": KEEP_SOURCE,
            "source_in_frame": source_in,
            "source_out_frame": source_out,
            "duration_frames": source_out - source_in,
        }
    source_cursor = 0
    for segment_id, duration in family_durations.items():
        definitions[segment_id] = {
            "source_sequence": FAMILY_SOURCE,
            "source_in_frame": source_cursor,
            "source_out_frame": source_cursor + duration,
            "duration_frames": duration,
        }
        source_cursor += duration + 5
    definitions["N1"] = {
        "source_sequence": NURI_SOURCE,
        "source_in_frame": 510,
        "source_out_frame": 585,
        "duration_frames": 75,
    }
    return {
        "task_id": "TASK_020",
        "stage": "B_COARSE_FAMILY_NURI_INSERTION",
        "authorized_by_user": True,
        "target_sequence": {
            "name": "SF_26_BD_LONG_FAMILY_NURI_STAGE_B_v01",
            "fps": 25,
            "audio_clip_count": 0,
        },
        "segments": definitions,
        "groups": groups,
        "timeline_order": order,
        "expected": {
            "total_video_clips": 28,
            "keep_clip_count": 4,
            "family_clip_count": 23,
            "nuri_clip_count": 1,
            "keep_frames": 3827,
            "family_frames": 852,
            "nuri_frames": 75,
            "total_frames": 4754,
            "audio_clip_count": 0,
            "audio_stream_count": 0,
        },
        "known_rough_cut_remainders": ["coarse joins"],
    }


def test_stage_b_builds_28_contiguous_clips_and_4754_frames() -> None:
    segments = build_coarse_insert_segments(_plan())
    assert len(segments) == 28
    assert segments[0]["timeline_in_frame"] == 0
    assert segments[-1]["timeline_out_frame"] == 4754
    assert all(
        segments[index]["timeline_out_frame"]
        == segments[index + 1]["timeline_in_frame"]
        for index in range(27)
    )


def test_stage_b_contract_is_valid_and_immutable() -> None:
    plan = _plan()
    before = copy.deepcopy(plan)
    segments = validate_coarse_insert_plan(plan)
    assert len(segments) == 28
    assert plan == before


def test_stage_b_rejects_nuri_not_immediately_before_k4() -> None:
    plan = _plan()
    order = plan["timeline_order"]  # type: ignore[assignment]
    order[-3], order[-2] = order[-2], order[-3]  # type: ignore[index]
    with pytest.raises(ValueError, match="N1"):
        validate_coarse_insert_plan(plan)


def test_stage_b_rejects_missing_or_duplicated_family_segment() -> None:
    plan = _plan()
    plan["timeline_order"][1] = "F02"  # type: ignore[index]
    with pytest.raises(ValueError, match="order/group|each occur"):
        validate_coarse_insert_plan(plan)


def test_insertion_proof_confirms_counts_order_and_nuri_position() -> None:
    plan = _plan()
    segments = validate_coarse_insert_plan(plan)
    actual = [
        {
            **segment,
            "source_sequence_name": segment["source_sequence_name"],
        }
        for segment in segments
    ]
    proof = build_insertion_proof(
        plan=plan,
        actual=actual,
        project_frames=4754,
        preview_frames=4754,
    )
    assert proof["order_matches_exactly"] is True
    assert proof["n1_immediately_before_k4"] is True
    assert proof["preview_and_project_frame_counts_match"] is True
    assert proof["duration_frames_by_source_sequence"] == {
        KEEP_SOURCE: 3827,
        FAMILY_SOURCE: 852,
        NURI_SOURCE: 75,
    }
