from __future__ import annotations

import copy

import pytest

from utils.premiere_sequence_delete_only import (
    build_delete_only_segments,
    build_removal_proof,
    validate_delete_only_plan,
)


def _plan() -> dict[str, object]:
    return {
        "task_id": "TASK_020",
        "stage": "A_DELETE_ONLY",
        "stop_after_stage": True,
        "source_sequence": "SF_26_BD_Keep_08",
        "target_sequence": {
            "name": "SF_26_BD_KEEP_DELETE_ONLY_TEST_v01",
            "fps": 25,
        },
        "keep_ranges": [
            {
                "id": "K1",
                "source_in_frame": 0,
                "source_out_frame": 1550,
                "duration_frames": 1550,
            },
            {
                "id": "K2",
                "source_in_frame": 2040,
                "source_out_frame": 2495,
                "duration_frames": 455,
            },
            {
                "id": "K3",
                "source_in_frame": 2878,
                "source_out_frame": 4015,
                "duration_frames": 1137,
            },
            {
                "id": "K4",
                "source_in_frame": 4315,
                "source_out_frame": 5000,
                "duration_frames": 685,
            },
        ],
        "removed_ranges": [
            {
                "id": "R1",
                "source_in_frame": 1550,
                "source_out_frame": 2040,
                "duration_frames": 490,
            },
            {
                "id": "R2",
                "source_in_frame": 2495,
                "source_out_frame": 2878,
                "duration_frames": 383,
            },
            {
                "id": "R3",
                "source_in_frame": 4015,
                "source_out_frame": 4315,
                "duration_frames": 300,
            },
        ],
        "expected_actual": {
            "video_clip_count": 4,
            "audio_clip_count": 0,
            "duration_frames": 3827,
            "removed_frames": 1173,
            "forbidden_source_sequences": [
                "SF_26_BD_Family_1",
                "SF_26_BD_Nuri_1",
            ],
        },
    }


def test_delete_only_ranges_ripple_close_to_3827_frames() -> None:
    segments = build_delete_only_segments(_plan())
    assert [item["timeline_in_frame"] for item in segments] == [0, 1550, 2005, 3142]
    assert [item["timeline_out_frame"] for item in segments] == [
        1550,
        2005,
        3142,
        3827,
    ]
    assert sum(int(item["duration_frames"]) for item in segments) == 3827


def test_stage_a_contract_rejects_near_200_second_output() -> None:
    plan = _plan()
    plan["expected_actual"]["duration_frames"] = 5000  # type: ignore[index]
    with pytest.raises(ValueError, match="fixed expected"):
        validate_delete_only_plan(plan)


def test_stage_a_contract_rejects_family_or_nuri_policy_change() -> None:
    plan = _plan()
    plan["expected_actual"]["forbidden_source_sequences"] = []  # type: ignore[index]
    with pytest.raises(ValueError, match="forbidden source"):
        validate_delete_only_plan(plan)


def test_stage_a_contract_is_immutable_and_valid() -> None:
    plan = _plan()
    before = copy.deepcopy(plan)
    segments = validate_delete_only_plan(plan)
    assert len(segments) == 4
    assert plan == before


def test_removal_proof_uses_actual_ranges_and_has_zero_overlap() -> None:
    plan = _plan()
    actual = [
        {
            **segment,
            "source_sequence_name": "SF_26_BD_Keep_08",
        }
        for segment in build_delete_only_segments(plan)
    ]
    proof = build_removal_proof(
        plan=plan,
        actual=actual,
        preview_frames=3827,
    )
    assert proof["removed_intervals_total_frames"] == 1173
    assert proof["retained_intervals_total_frames"] == 3827
    assert proof["no_overlap_between_retained_and_removed"] is True
    assert proof["preview_and_sequence_duration_match"] is True
