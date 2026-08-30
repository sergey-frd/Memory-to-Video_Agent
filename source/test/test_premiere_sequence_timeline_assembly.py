from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils.premiere_sequence_timeline_assembly import (
    _backup_path,
    _visible_item_for_range,
    finalize_uploaded_results,
    load_timeline_segments,
    validate_timeline_segments,
)


def _segments(count: int = 30) -> list[dict[str, object]]:
    result = []
    cursor = 0
    for order in range(1, count + 1):
        duration = 12 if order < count else 4646
        result.append(
            {
                "order": order,
                "segment_id": f"S{order:02d}",
                "source_sequence_name": "SOURCE",
                "source_in_frame": order * 100,
                "source_out_frame": order * 100 + duration,
                "timeline_in_frame": cursor,
                "timeline_out_frame": cursor + duration,
                "duration_frames": duration,
            }
        )
        cursor += duration
    return result


def test_task019_timeline_is_contiguous_and_frame_exact() -> None:
    segments = _segments()
    validate_timeline_segments(
        segments,
        expected_count=30,
        expected_frames=4994,
    )


def test_task019_timeline_rejects_gap() -> None:
    segments = _segments()
    segments[10]["timeline_in_frame"] = int(
        segments[10]["timeline_in_frame"]
    ) + 1
    with pytest.raises(ValueError, match="starts at"):
        validate_timeline_segments(
            segments,
            expected_count=30,
            expected_frames=4994,
        )


def test_plan_loader_orders_keep_family_and_nuri_segments() -> None:
    segments = _segments()
    plan = {
        "keep_segments": [segments[index] for index in [0, 1, 2, 3, 4, 29]],
        "family_montage": {"segments": segments[5:28]},
        "nuri_segment": segments[28],
    }
    loaded = load_timeline_segments(plan)
    assert [int(item["order"]) for item in loaded] == list(range(1, 31))


def test_backup_collision_uses_timestamped_name(tmp_path: Path) -> None:
    preferred = tmp_path / "project_before_TASK_019.prproj"
    preferred.write_bytes(b"original")
    selected = _backup_path(preferred)
    assert selected != preferred
    assert selected.parent == preferred.parent
    assert selected.name.startswith("project_before_TASK_019_")


def test_preview_range_uses_topmost_covering_visual(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lower = SimpleNamespace(
        track_index=0,
        start=0,
        end=100,
        source_path=str(tmp_path / "background.jpg"),
    )
    upper = SimpleNamespace(
        track_index=1,
        start=0,
        end=100,
        source_path=str(tmp_path / "overlay.png"),
    )
    monkeypatch.setattr(
        "utils.premiere_sequence_timeline_assembly._track_item_contexts",
        lambda *args, **kwargs: [lower, upper],
    )
    selected = _visible_item_for_range(
        object(),  # type: ignore[arg-type]
        source_in_ticks=10,
        source_out_ticks=20,
        ids={},
        uids={},
        project_path=tmp_path / "project.prproj",
    )
    assert selected is upper


def test_finalize_marks_upload_and_premiere_open_pass(tmp_path: Path) -> None:
    actual_path = tmp_path / "actual.json"
    qa_path = tmp_path / "qa.json"
    done_path = tmp_path / "done.txt"
    actual_path.write_text(
        json.dumps(
            {
                "status": "LOCAL_PASS_UPLOAD_PENDING",
                "project_path": "project.prproj",
                "target_sequence_name": "target",
                "actual_total_duration_frames": 4994,
                "actual_total_duration_seconds": 199.76,
                "preview_file_path": "preview.mp4",
            }
        ),
        encoding="utf-8",
    )
    qa_path.write_text(
        json.dumps(
            {
                "overall_status": "PENDING",
                "checks": {
                    "preview_and_actual_json_uploaded": "PENDING",
                    "premiere_project_reopens_without_repair_or_conversion": "PENDING",
                },
            }
        ),
        encoding="utf-8",
    )
    finalize_uploaded_results(
        actual_path=actual_path,
        qa_path=qa_path,
        done_path=done_path,
        preview_url="https://example.test/preview",
        result_json_url="https://example.test/json",
    )
    actual = json.loads(actual_path.read_text(encoding="utf-8"))
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    assert actual["status"] == "PASS"
    assert qa["overall_status"] == "PASS"
    assert all(value == "PASS" for value in qa["checks"].values())
    assert "audio: NONE" in done_path.read_text(encoding="utf-8")
