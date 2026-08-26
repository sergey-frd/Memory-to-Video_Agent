from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

import main_premiere_import_keep
from utils.premiere_project import PREMIERE_TICKS_PER_SECOND
from utils.premiere_sequence_insert_motion import (
    INSERT_MOTION_MODE,
    _build_motion_plan,
    is_premiere_sequence_insert_motion_config,
    plan_ripple_insert_signature,
    resolve_final_coda_boundary,
    resolve_insert_source_bounds,
    resolve_source_sequence_range,
    validate_premiere_sequence_insert_motion_config,
)


def _config() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_id": "TASK_015E_YOTAM_SHORT_INSERT_MOTION",
        "mode": INSERT_MOTION_MODE,
        "project": {
            "project_file": r"<LOCAL_PATH>",
            "save_as_project_file": r"<LOCAL_PATH>",
        },
        "sequences": {
            "main_source_sequence_name": "Yt_FINAL_KEEP_v09",
            "correction_source_sequence_name": "Yt_2300825_1",
            "output_sequence_name": "Yt_FINAL_KEEP_v10",
            "correction_source_is_sequence_not_media_file": True,
        },
        "sequence_versioning": {
            "automated_milestone_increment": 5,
            "current_output_milestone": 10,
        },
        "semantic_source_range_resolution": {
            "preferred_visible_duration_seconds": {
                "min": 2.5,
                "target": 3.5,
                "max": 4.5,
            }
        },
        "destination_insertion": {
            "policy": "INSERT_IMMEDIATELY_BEFORE_FINAL_STYLIZED_CODA"
        },
        "motion_animation": {
            "minimum_visible_duration_frames": 21,
            "motion_profiles": [
                {
                    "name": "SHORT_VISIBLE",
                    "visible_duration_frames_min": 21,
                    "visible_duration_frames_max": 37,
                    "scale_delta_percent_of_baseline": 3.0,
                    "max_position_delta_percent_of_frame": 0.8,
                },
                {
                    "name": "LONG_EXPRESSIVE",
                    "visible_duration_frames_min": 38,
                    "visible_duration_frames_max": None,
                    "scale_delta_percent_of_baseline": 8.0,
                    "max_position_delta_percent_of_frame": 2.5,
                },
            ],
            "direction_cycle": ["PUSH_IN"],
            "temporal_interpolation": "LINEAR",
        },
        "audio_policy": {"mode": "OUTPUT_SILENT"},
        "review_export": {"filename": "review.mp4"},
    }


def test_insert_motion_mode_resolves_correction_as_sequence() -> None:
    payload = _config()
    assert is_premiere_sequence_insert_motion_config(payload)
    assert validate_premiere_sequence_insert_motion_config(payload) is payload
    sequences = payload["sequences"]
    assert sequences["correction_source_sequence_name"] == "Yt_2300825_1"  # type: ignore[index]
    assert sequences["correction_source_is_sequence_not_media_file"] is True  # type: ignore[index]


def test_insert_motion_rejects_external_media_interpretation() -> None:
    payload = _config()
    payload["sequences"]["correction_source_is_sequence_not_media_file"] = False  # type: ignore[index]
    with pytest.raises(ValueError, match="in-project sequence"):
        validate_premiere_sequence_insert_motion_config(payload)


def test_semantic_range_is_unique_frame_exact_and_within_bounds() -> None:
    payload = _config()
    before = copy.deepcopy(payload)
    start, end, candidates = resolve_source_sequence_range(payload, fps=25)
    assert (start, end) == (166, 254)
    assert end - start == 88
    assert sum(bool(item["selected"]) for item in candidates) == 1
    assert payload == before


def test_explicit_json_source_range_overrides_task_default() -> None:
    payload = _config()
    payload["semantic_source_range_resolution"]["resolved_source_range_frames"] = [  # type: ignore[index]
        175,
        263,
    ]
    start, end, candidates = resolve_source_sequence_range(payload, fps=25)
    assert (start, end) == (175, 263)
    assert sum(bool(item["selected"]) for item in candidates) == 1


def test_insert_source_bounds_preserve_frame_exact_in_out_and_speed() -> None:
    source_in, source_out = resolve_insert_source_bounds(
        item_timeline_start=1000,
        item_source_in=5000,
        selected_timeline_start=1200,
        selected_timeline_end=2080,
    )
    assert (source_in, source_out) == (5200, 6080)
    assert source_out - source_in == 880


def test_ripple_insert_shifts_only_later_picture_items_exactly() -> None:
    original = [
        (1, 0, 100, "before", "before.png"),
        (1, 100, 200, "after", "after.png"),
    ]
    result = plan_ripple_insert_signature(
        original,
        insertion_ticks=100,
        duration_ticks=35,
        inserted=(1, 100, 135, "live", "live.mp4"),
    )
    assert original[1][1:3] == (100, 200)
    assert (1, 135, 235, "after", "after.png") in result
    assert (1, 100, 135, "live", "live.mp4") in result


def test_final_coda_resolver_selects_last_uninterrupted_image_run(
    tmp_path: Path,
) -> None:
    second = PREMIERE_TICKS_PER_SECOND
    items = [
        SimpleNamespace(start=0, end=second, source_path="live.mp4"),
        SimpleNamespace(start=second, end=2 * second, source_path="first.png"),
        SimpleNamespace(start=2 * second, end=3 * second, source_path="second.png"),
        SimpleNamespace(start=3 * second, end=4 * second, source_path="live2.mp4"),
        SimpleNamespace(start=4 * second, end=5 * second, source_path="third.png"),
        SimpleNamespace(start=5 * second, end=6 * second, source_path="fourth.png"),
    ]
    selected, candidates = resolve_final_coda_boundary(
        items,
        fps=25,
        project_path=tmp_path / "project.prproj",
    )
    assert selected == 4 * second
    assert len(candidates) == 2
    assert candidates[-1]["selected"] is True


def test_inserted_and_other_natural_motion_video_are_excluded_from_motion() -> None:
    payload = _config()
    node = ET.fromstring("<VideoClipTrackItem ObjectID='1' />")
    video = SimpleNamespace(
        track_index=1,
        name="existing.mp4",
        source_path=r"<LOCAL_PATH>",
        start=0,
        end=100,
        source_in=0,
        source_out=100,
        duration=100,
        track_item_node=node,
    )
    correction = SimpleNamespace(
        track_index=0,
        name="insert.mp4",
        source_path=r"<LOCAL_PATH>",
        start=0,
        end=1000,
        source_in=0,
        source_out=1000,
        duration=1000,
        track_item_node=node,
    )
    plan = _build_motion_plan(
        config=payload,
        main_items=[video],
        correction_item=correction,
        insertion_ticks=100,
        insert_duration_ticks=88,
        source_in_ticks=166,
        source_out_ticks=254,
        id_lookup={},
        frame_width=3840,
        fps=25,
    )
    assert plan.candidate_video_items == []
    assert len(plan.already_compliant_items) == 1
    assert len(plan.protected_items) == 1
    assert "Motion forbidden" in str(plan.protected_items[0]["reason"])


def test_milestone_v10_validation_leaves_intermediate_names_available() -> None:
    payload = _config()
    validate_premiere_sequence_insert_motion_config(payload)
    assert payload["sequences"]["output_sequence_name"] == "Yt_FINAL_KEEP_v10"  # type: ignore[index]
    assert payload["sequences"]["main_source_sequence_name"] == "Yt_FINAL_KEEP_v09"  # type: ignore[index]


def test_main_dispatches_insert_motion_and_forwards_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = (Path("dry.json"), Path("implementation.txt"), None)
    calls: list[tuple[Path, bool]] = []

    def fake_run(
        config_path: Path,
        *,
        dry_run_only: bool = False,
    ) -> tuple[Path, Path, Path | None]:
        calls.append((config_path, dry_run_only))
        return expected

    monkeypatch.setattr(
        main_premiere_import_keep,
        "run_premiere_sequence_insert_motion_from_config",
        fake_run,
    )
    result = main_premiere_import_keep.try_run_premiere_import_keep(
        Path("insert.json"),
        {"mode": INSERT_MOTION_MODE},
        dry_run=True,
    )
    assert result == ("Premiere sequence insert and motion", *expected)
    assert calls == [(Path("insert.json"), True)]
