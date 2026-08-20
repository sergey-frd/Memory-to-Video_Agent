from __future__ import annotations

import gzip
import json
from pathlib import Path
from uuid import uuid4

import pytest

from models.sequence_keep_apply import KeepRange
from utils.premiere_keep_apply_export import intersect_keep_ranges_ticks
from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    PremiereProjectError,
    build_project_object_id_lookup,
    build_project_object_uid_lookup,
    find_project_sequence_node,
    get_project_track_nodes,
    iter_project_track_item_refs,
    load_premiere_project_root,
    parse_premiere_project_sequence_visual_clips,
    resolve_project_track_item_name,
    resolve_project_track_item_source_bounds,
    resolve_project_track_item_timeline,
)
from utils.sequence_keep_apply import (
    is_keep_apply_config,
    load_media_keep_specs,
    parse_timecode_seconds,
    run_sequence_keep_apply_from_config,
)
from utils.sequence_trim_classifier import ticks_to_seconds


def _ticks(seconds: float) -> int:
    return int(round(seconds * PREMIERE_TICKS_PER_SECOND))


def test_parse_timecode_seconds_accepts_clock_and_numeric_values() -> None:
    assert parse_timecode_seconds("00:00:00.500") == pytest.approx(0.5)
    assert parse_timecode_seconds("00:00:02.250") == pytest.approx(2.25)
    assert parse_timecode_seconds("01:02:03.250") == pytest.approx(3723.25)
    assert parse_timecode_seconds("1:30") == pytest.approx(90.0)
    assert parse_timecode_seconds(2.5) == pytest.approx(2.5)
    assert parse_timecode_seconds("3") == pytest.approx(3.0)


def test_load_media_keep_specs_reads_operations_json() -> None:
    specs = load_media_keep_specs(
        {
            "project_path": r"<LOCAL_PATH>",
            "sequence_name": "Yotam26_2_min_vtr_2",
            "operations": [
                {
                    "file": "IMG_5104_3.mp4",
                    "keep_ranges": [
                        {"in": "00:00:00.350", "out": "00:00:02.300"},
                        {"in": "00:00:10.000", "out": "00:00:12.000"},
                    ],
                }
            ],
        }
    )
    assert [spec.file_name for spec in specs] == ["IMG_5104_3.mp4"]
    assert [range_item.duration_seconds for range_item in specs[0].ranges] == [
        pytest.approx(1.95),
        pytest.approx(2.0),
    ]


def test_load_media_keep_specs_reads_agent_trim_json() -> None:
    specs = load_media_keep_specs(
        {
            "clips": [
                {
                    "file": "IMG_4530.MP4",
                    "keep": [{"in": "00:00:00.500", "out": "00:00:02.250"}],
                },
                {
                    "file": "IMG_5104_3.mp4",
                    "keep": [{"in": "00:00:00.350", "out": "00:00:02.300"}],
                },
            ]
        }
    )
    assert [spec.file_name for spec in specs] == ["IMG_4530.MP4", "IMG_5104_3.mp4"]
    assert specs[0].ranges[0].start_seconds == pytest.approx(0.5)
    assert specs[0].ranges[0].end_seconds == pytest.approx(2.25)


def test_load_media_keep_specs_reads_still_duration() -> None:
    specs = load_media_keep_specs(
        {
            "operations": [
                {"file": "IMG_4784.jpg", "duration": "00:00:01.500"},
                {"file": "IMG_4530.MP4", "keep_ranges": [{"in": 0.5, "out": 2.25}]},
            ]
        }
    )
    assert specs[0].file_name == "IMG_4784.jpg"
    assert specs[0].ranges == ()
    assert specs[0].duration_seconds == pytest.approx(1.5)
    assert specs[1].duration_seconds is None
    assert specs[1].ranges[0].end_seconds == pytest.approx(2.25)


def test_load_media_keep_specs_accepts_source_path_hint_and_source_name() -> None:
    specs = load_media_keep_specs(
        {
            "mode": "keep_to_new_sequence",
            "operations": [
                {
                    "order": 1,
                    "source_name": "SQ_960_1.mp4",
                    "source_path_hint": r"<LOCAL_PATH>",
                    "keep_ranges": [{"start": "00:00:01.000", "end": "00:00:08.500"}],
                }
            ],
        }
    )
    assert specs[0].file_name == "SQ_960_1.mp4"
    assert specs[0].source_path == r"<LOCAL_PATH>"
    assert specs[0].ranges[0].start_seconds == pytest.approx(1.0)
    assert specs[0].ranges[0].end_seconds == pytest.approx(8.5)


def test_load_media_keep_specs_reads_source_path_duration() -> None:
    specs = load_media_keep_specs(
        {
            "mode": "keep_to_new_sequence",
            "operations": [
                {
                    "order": 1,
                    "source_path": r"<LOCAL_PATH>",
                    "duration": "00:00:0.800",
                }
            ],
        }
    )
    assert specs[0].file_name == "260806_01__wcp.png"
    assert specs[0].source_path == r"<LOCAL_PATH>"
    assert specs[0].duration_seconds == pytest.approx(0.8)
    assert is_keep_apply_config({"mode": "keep_to_new_sequence", "operations": []})


def test_load_media_keep_specs_allows_duplicate_names_with_different_source_paths() -> None:
    specs = load_media_keep_specs(
        {
            "operations": [
                {
                    "source_path": r"<LOCAL_PATH>",
                    "duration": "00:00:0.800",
                },
                {
                    "source_path": r"<LOCAL_PATH>",
                    "duration": "00:00:0.550",
                },
            ]
        }
    )
    assert [spec.file_name for spec in specs] == ["260806_01__wcp.png", "260806_01__wcp.png"]
    assert specs[0].duration_seconds == pytest.approx(0.8)
    assert specs[1].duration_seconds == pytest.approx(0.55)


def test_load_media_keep_specs_rejects_duplicate_source_path() -> None:
    with pytest.raises(ValueError, match="listed more than once"):
        load_media_keep_specs(
            {
                "operations": [
                    {"source_path": r"<LOCAL_PATH>", "duration": 0.8},
                    {"source_path": r"<LOCAL_PATH>", "duration": 0.5},
                ]
            }
        )


def test_load_media_keep_specs_allows_duplicate_source_path_with_order() -> None:
    specs = load_media_keep_specs(
        {
            "operations": [
                {
                    "order": 5,
                    "source_path": r"<LOCAL_PATH>",
                    "keep_ranges": [{"start": "00:00:01.000", "end": "00:00:08.500"}],
                },
                {
                    "order": 6,
                    "source_path": r"<LOCAL_PATH>",
                    "keep_ranges": [{"start": "00:00:11.000", "end": "00:00:16.000"}],
                },
            ]
        }
    )
    assert [spec.order for spec in specs] == [5, 6]
    assert specs[0].ranges[0].end_seconds == pytest.approx(8.5)
    assert specs[1].ranges[0].start_seconds == pytest.approx(11.0)


def test_load_media_keep_specs_rejects_duplicate_order() -> None:
    with pytest.raises(ValueError, match="order 1 is listed more than once"):
        load_media_keep_specs(
            {
                "operations": [
                    {"order": 1, "source_path": r"<LOCAL_PATH>", "duration": 0.8},
                    {"order": 1, "source_path": r"<LOCAL_PATH>", "duration": 0.5},
                ]
            }
        )


def test_load_media_keep_specs_rejects_duplicate_file_name_without_source_path() -> None:
    with pytest.raises(ValueError, match="listed more than once"):
        load_media_keep_specs(
            {
                "operations": [
                    {"file": "260806_01__wcp.png", "duration": 0.8},
                    {"file": "260806_01__wcp.png", "duration": 0.5},
                ]
            }
        )


def test_load_media_keep_specs_rejects_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        load_media_keep_specs(
            {
                "clips": [
                    {
                        "file": "clip.mp4",
                        "keep": [
                            {"in": 1.0, "out": 4.0},
                            {"in": 3.5, "out": 6.0},
                        ],
                    }
                ]
            }
        )


def test_intersect_keep_ranges_ticks_clips_to_current_source_window() -> None:
    ranges = (KeepRange(start_seconds=0.5, end_seconds=8.0),)
    intersections = intersect_keep_ranges_ticks(_ticks(1.0), _ticks(3.0), ranges)
    assert len(intersections) == 1
    assert ticks_to_seconds(intersections[0][0]) == pytest.approx(1.0)
    assert ticks_to_seconds(intersections[0][1]) == pytest.approx(3.0)


def test_run_sequence_keep_apply_trims_listed_files_and_ripples_others() -> None:
    root = Path("test_runtime") / f"keep_apply_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "raw.prproj"
    _write_mixed_av_project(project_path)
    original_bytes = project_path.read_bytes()

    keep_ranges_path = root / "keep.json"
    keep_ranges_path.write_text(
        json.dumps(
            {
                "clips": [
                    {
                        "file": "clip_b_birthday.mp4",
                        "keep": [{"in": "00:00:02.000", "out": "00:00:05.000"}],
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    output_project = root / "raw_keep.prproj"
    config_path = root / "keep_apply.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "apply_keep_ranges",
                "project_path": str(project_path),
                "prin_path": str(root / "raw.prin"),
                "keep_ranges_path": str(keep_ranges_path),
                "source_sequence_name": "RawSequence",
                "output_project_path": str(output_project),
                "reports_dir": str(root / "reports"),
                "ripple_compact": True,
                "write_project": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    json_path, txt_path, exported = run_sequence_keep_apply_from_config(config_path)

    assert json_path.exists()
    assert txt_path.exists()
    assert exported == output_project
    assert project_path.read_bytes() == original_bytes

    _sequence_name, clips = parse_premiere_project_sequence_visual_clips(output_project, "RawSequence")
    by_name = {clip.name: clip for clip in clips}
    assert ticks_to_seconds(by_name["clip_a.mp4"].start) == pytest.approx(0.0)
    assert ticks_to_seconds(by_name["clip_a.mp4"].duration) == pytest.approx(10.0)
    assert ticks_to_seconds(by_name["clip_b_birthday.mp4"].start) == pytest.approx(10.0)
    assert ticks_to_seconds(by_name["clip_b_birthday.mp4"].duration) == pytest.approx(3.0)
    assert ticks_to_seconds(by_name["clip_b_birthday.mp4"].in_point) == pytest.approx(2.0)
    assert ticks_to_seconds(by_name["clip_b_birthday.mp4"].out_point) == pytest.approx(5.0)
    assert ticks_to_seconds(by_name["clip_c_trash.mp4"].start) == pytest.approx(13.0)
    assert ticks_to_seconds(by_name["clip_c_trash.mp4"].duration) == pytest.approx(20.0)

    audio = _track_items(output_project, "RawSequence", track_group_index=1)
    assert [(item["name"], round(item["start_s"], 3), round(item["duration_s"], 3)) for item in audio] == [
        ("clip_a.mp4", 0.0, 10.0),
        ("clip_b_birthday.mp4", 10.0, 3.0),
    ]
    assert audio[1]["in_s"] == pytest.approx(2.0)
    assert audio[1]["out_s"] == pytest.approx(5.0)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "apply_keep_ranges"
    assert payload["ripple_compact"] is True
    assert payload["sequences"][0]["matched_clips"] == 1


def test_run_sequence_keep_apply_splits_multiple_keep_ranges() -> None:
    root = Path("test_runtime") / f"keep_apply_split_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "raw.prproj"
    _write_mixed_av_project(project_path)
    config_path = root / "keep_apply.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "apply_keep_ranges",
                "project_path": str(project_path),
                "source_sequence_name": "RawSequence",
                "output_project_path": str(root / "raw_keep.prproj"),
                "reports_dir": str(root / "reports"),
                "ripple_compact": True,
                "write_project": True,
                "clips": [
                    {
                        "file": "clip_b_birthday.mp4",
                        "keep": [
                            {"in": 1.0, "out": 2.0},
                            {"in": 8.0, "out": 10.0},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _json_path, _txt_path, exported = run_sequence_keep_apply_from_config(config_path)
    assert exported is not None
    video = _track_items(exported, "RawSequence", track_group_index=0)
    birthday = [item for item in video if item["name"] == "clip_b_birthday.mp4"]
    assert len(birthday) == 2
    assert [round(item["duration_s"], 3) for item in birthday] == [1.0, 2.0]
    assert [round(item["in_s"], 3) for item in birthday] == [1.0, 8.0]
    assert video[-1]["name"] == "clip_c_trash.mp4"
    assert video[-1]["start_s"] == pytest.approx(13.0)

    audio = [item for item in _track_items(exported, "RawSequence", track_group_index=1) if item["name"] == "clip_b_birthday.mp4"]
    assert len(audio) == 2
    assert [round(item["in_s"], 3) for item in audio] == [1.0, 8.0]


def test_run_sequence_keep_apply_warns_when_listed_file_is_missing() -> None:
    root = Path("test_runtime") / f"keep_apply_missing_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "raw.prproj"
    _write_mixed_av_project(project_path)
    config_path = root / "keep_apply.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "apply_keep_ranges",
                "project_path": str(project_path),
                "source_sequence_name": "RawSequence",
                "output_project_path": str(root / "raw_keep.prproj"),
                "reports_dir": str(root / "reports"),
                "clips": [{"file": "not_in_sequence.mp4", "keep": [{"in": 0.0, "out": 1.0}]}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    json_path, _txt_path, _exported = run_sequence_keep_apply_from_config(config_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert any("not_in_sequence.mp4" in warning for warning in payload["warnings"])


def test_run_sequence_keep_apply_uses_duration_for_stills() -> None:
    root = Path("test_runtime") / f"keep_apply_duration_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "raw.prproj"
    _write_still_and_video_project(project_path)
    output_project = root / "raw_keep.prproj"
    config_path = root / "keep_apply.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "apply_keep_ranges",
                "project_path": str(project_path),
                "sequence_name": "RawSequence",
                "output_project_path": str(output_project),
                "reports_dir": str(root / "reports"),
                "operations": [
                    {"file": "still.jpg", "duration": "00:00:01.500"},
                    {"file": "clip_a.mp4", "keep_ranges": [{"in": 0.0, "out": 2.0}]},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _json_path, _txt_path, exported = run_sequence_keep_apply_from_config(config_path)
    assert exported == output_project
    video = _track_items(exported, "RawSequence", track_group_index=0)
    assert [item["name"] for item in video] == ["clip_a.mp4", "still.jpg"]
    assert video[0]["duration_s"] == pytest.approx(2.0)
    assert video[1]["duration_s"] == pytest.approx(1.5)
    assert video[1]["in_s"] == pytest.approx(3600.0)
    assert video[1]["out_s"] == pytest.approx(3601.5)
    assert video[1]["start_s"] == pytest.approx(2.0)


def test_run_sequence_keep_apply_reads_project_and_sequence_from_operations_json() -> None:
    root = Path("test_runtime") / f"keep_apply_ops_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "raw.prproj"
    _write_mixed_av_project(project_path)
    first_output = root / "raw_keep.prproj"
    first_config = root / "first.json"
    first_config.write_text(
        json.dumps(
            {
                "mode": "apply_keep_ranges",
                "project_path": str(project_path),
                "source_sequence_name": "RawSequence",
                "output_project_path": str(first_output),
                "reports_dir": str(root / "reports_1"),
                "clips": [
                    {
                        "file": "clip_b_birthday.mp4",
                        "keep": [{"in": 2.0, "out": 5.0}],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    run_sequence_keep_apply_from_config(first_config)

    operations_path = root / "10_operations.json"
    operations_path.write_text(
        json.dumps(
            {
                "project_path": str(first_output),
                "sequence_name": "RawSequence",
                "operations": [
                    {
                        "file": "clip_b_birthday.mp4",
                        "keep_ranges": [
                            {"in": "00:00:02.000", "out": "00:00:05.000"},
                            {"in": "00:00:08.000", "out": "00:00:10.000"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    second_output = root / "raw_keep_vtr2.prproj"
    wrapper_path = root / "wrapper.json"
    wrapper_path.write_text(
        json.dumps(
            {
                "mode": "apply_keep_ranges",
                "keep_ranges_path": str(operations_path),
                "output_project_path": str(second_output),
                "reports_dir": str(root / "reports_2"),
                "ripple_compact": True,
                "write_project": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    json_path, _txt_path, exported = run_sequence_keep_apply_from_config(wrapper_path)
    assert exported == second_output
    assert first_output.read_bytes() != second_output.read_bytes()

    video = _track_items(second_output, "RawSequence", track_group_index=0)
    birthday = [item for item in video if item["name"] == "clip_b_birthday.mp4"]
    assert len(birthday) == 2
    assert [round(item["in_s"], 3) for item in birthday] == [2.0, 8.0]
    assert [round(item["duration_s"], 3) for item in birthday] == [3.0, 2.0]
    assert video[-1]["name"] == "clip_c_trash.mp4"
    assert video[-1]["start_s"] == pytest.approx(15.0)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["source_sequence_name"] == "RawSequence"
    assert any("8.000-10.000" in warning for warning in payload["warnings"])


def test_run_sequence_keep_apply_accepts_operations_json_as_config() -> None:
    root = Path("test_runtime") / f"keep_apply_ops_direct_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "raw.prproj"
    _write_mixed_av_project(project_path)
    operations_path = root / "operations.json"
    operations_path.write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "sequence_name": "RawSequence",
                "operations": [
                    {
                        "file": "clip_b_birthday.mp4",
                        "keep_ranges": [{"in": 2.0, "out": 5.0}],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _json_path, _txt_path, exported = run_sequence_keep_apply_from_config(operations_path)
    assert exported == project_path.parent / "raw_keep.prproj"
    video = _track_items(exported, "RawSequence", track_group_index=0)
    birthday = [item for item in video if item["name"] == "clip_b_birthday.mp4"]
    assert len(birthday) == 1
    assert birthday[0]["in_s"] == pytest.approx(2.0)
    assert birthday[0]["out_s"] == pytest.approx(5.0)


def test_run_sequence_keep_to_new_sequence_copies_and_preserves_source() -> None:
    root = Path("test_runtime") / f"keep_new_seq_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "ready.prproj"
    _write_mixed_av_project(project_path)
    source_before = _track_items(project_path, "RawSequence", track_group_index=0)
    config_path = root / "keep_new.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "keep_to_new_sequence",
                "project_path": str(project_path),
                "source_sequence_name": "RawSequence",
                "output_sequence_name": "KEEP_styles_v01",
                "create_output_sequence_from_source": True,
                "preserve_source_sequence": True,
                "fail_if_output_sequence_exists": True,
                "ripple_compact": True,
                "write_project": True,
                "operations": [
                    {
                        "order": 1,
                        "source_path": r"<LOCAL_PATH>",
                        "keep_ranges": [{"in": "00:00:02.000", "out": "00:00:05.000"}],
                    }
                ],
                "reports_dir": str(root / "reports"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    json_path, _txt_path, exported = run_sequence_keep_apply_from_config(config_path)

    assert exported == project_path
    assert not (root / "ready_keep.prproj").exists()
    source_after = _track_items(project_path, "RawSequence", track_group_index=0)
    assert source_after == source_before
    kept = _track_items(project_path, "KEEP_styles_v01", track_group_index=0)
    by_name = {item["name"]: item for item in kept}
    assert by_name["clip_a.mp4"]["duration_s"] == pytest.approx(10.0)
    assert by_name["clip_b_birthday.mp4"]["start_s"] == pytest.approx(10.0)
    assert by_name["clip_b_birthday.mp4"]["duration_s"] == pytest.approx(3.0)
    assert by_name["clip_b_birthday.mp4"]["in_s"] == pytest.approx(2.0)
    assert by_name["clip_c_trash.mp4"]["start_s"] == pytest.approx(13.0)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "keep_to_new_sequence"
    assert payload["wrote_in_place"] is True
    assert payload["output_sequence_name"] == "KEEP_styles_v01"

    try:
        run_sequence_keep_apply_from_config(config_path)
    except PremiereProjectError as exc:
        assert "KEEP_styles_v01" in str(exc)
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected PremiereProjectError when output sequence exists")


def test_run_sequence_keep_apply_matches_duplicate_names_by_source_path() -> None:
    root = Path("test_runtime") / f"keep_dup_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "ready.prproj"
    first = r"<LOCAL_PATH>"
    second = r"<LOCAL_PATH>"
    _write_duplicate_name_stills_project(project_path, first_path=first, second_path=second)
    config_path = root / "keep_dup.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "keep_to_new_sequence",
                "project_path": str(project_path),
                "source_sequence_name": "RawSequence",
                "output_sequence_name": "KEEP_styles_v01",
                "create_output_sequence_from_source": True,
                "preserve_source_sequence": True,
                "fail_if_output_sequence_exists": True,
                "ripple_compact": True,
                "write_project": True,
                "operations": [
                    {"order": 1, "source_path": first, "duration": "00:00:0.800"},
                    {"order": 2, "source_path": second, "duration": "00:00:0.550"},
                ],
                "reports_dir": str(root / "reports"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _json_path, _txt_path, exported = run_sequence_keep_apply_from_config(config_path)

    assert exported == project_path
    source_after = _track_items(project_path, "RawSequence", track_group_index=0)
    assert [item["duration_s"] for item in source_after] == [pytest.approx(5.0), pytest.approx(5.0)]
    kept = _track_items(project_path, "KEEP_styles_v01", track_group_index=0)
    assert [item["name"] for item in kept] == ["260806_01__wcp.png", "260806_01__wcp.png"]
    assert kept[0]["duration_s"] == pytest.approx(0.8)
    assert kept[1]["start_s"] == pytest.approx(0.8)
    assert kept[1]["duration_s"] == pytest.approx(0.55)


def test_run_sequence_keep_apply_trims_same_path_instances_by_order() -> None:
    root = Path("test_runtime") / f"keep_same_path_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    project_path = root / "ready.prproj"
    shared = r"<LOCAL_PATH>"
    _write_duplicate_name_stills_project(project_path, first_path=shared, second_path=shared)
    config_path = root / "keep_same_path.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "keep_to_new_sequence",
                "project_path": str(project_path),
                "source_sequence_name": "RawSequence",
                "output_sequence_name": "KEEP_styles_v01",
                "create_output_sequence_from_source": True,
                "preserve_source_sequence": True,
                "fail_if_output_sequence_exists": True,
                "ripple_compact": True,
                "write_project": True,
                "operations": [
                    {"order": 1, "source_path": shared, "duration": "00:00:0.800"},
                    {"order": 2, "source_path": shared, "duration": "00:00:1.100"},
                ],
                "reports_dir": str(root / "reports"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    json_path, _txt_path, exported = run_sequence_keep_apply_from_config(config_path)

    assert exported == project_path
    kept = _track_items(project_path, "KEEP_styles_v01", track_group_index=0)
    assert len(kept) == 2
    assert kept[0]["duration_s"] == pytest.approx(0.8)
    assert kept[1]["start_s"] == pytest.approx(0.8)
    assert kept[1]["duration_s"] == pytest.approx(1.1)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["sequences"][0]["matched_clips"] == 2


def _track_items(project_path: Path, sequence_name: str, *, track_group_index: int) -> list[dict[str, float | str]]:
    root = load_premiere_project_root(project_path)
    id_lookup = build_project_object_id_lookup(root)
    uid_lookup = build_project_object_uid_lookup(root)
    sequence_node = find_project_sequence_node(root, sequence_name)
    assert sequence_node is not None
    items: list[dict[str, float | str]] = []
    for _track_index, track_node in get_project_track_nodes(
        sequence_node,
        track_group_index=track_group_index,
        object_id_lookup=id_lookup,
        object_uid_lookup=uid_lookup,
    ):
        for ref in iter_project_track_item_refs(track_node):
            object_ref = ref.attrib.get("ObjectRef")
            if not object_ref or object_ref not in id_lookup:
                continue
            node = id_lookup[object_ref]
            start, end = resolve_project_track_item_timeline(node)
            source_in, source_out = resolve_project_track_item_source_bounds(node, id_lookup)
            items.append(
                {
                    "name": resolve_project_track_item_name(node, id_lookup),
                    "start_s": ticks_to_seconds(start),
                    "duration_s": ticks_to_seconds(end - start),
                    "in_s": ticks_to_seconds(source_in),
                    "out_s": ticks_to_seconds(source_out),
                }
            )
    return items


def _write_duplicate_name_stills_project(
    project_path: Path,
    *,
    first_path: str,
    second_path: str,
) -> None:
    first_start = _ticks(0)
    first_end = _ticks(5)
    second_start = _ticks(5)
    second_end = _ticks(10)
    still_in = _ticks(3600)
    still_out = _ticks(3605)
    project_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <RootProjectItem ObjectURef="root-project" />
  <RootProjectItem ObjectUID="root-project" ClassID="root-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>Root</Name>
    </ProjectItem>
    <ProjectItemContainer Version="1">
      <Items Version="1">
        <Item Index="0" ObjectURef="project-item-raw" />
      </Items>
    </ProjectItemContainer>
  </RootProjectItem>
  <ClipProjectItem ObjectUID="project-item-raw" ClassID="clip-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>RawSequence</Name>
    </ProjectItem>
    <MasterClip ObjectURef="master-raw" />
  </ClipProjectItem>
  <MasterClip ObjectUID="master-raw" ClassID="master-clip" Version="1">
    <Name>RawSequence</Name>
  </MasterClip>
  <Sequence ObjectUID="seq-raw" ClassID="sequence" Version="1">
    <TrackGroups Version="1">
      <TrackGroup Version="1" Index="0">
        <Second ObjectRef="1000" />
      </TrackGroup>
    </TrackGroups>
    <Name>RawSequence</Name>
  </Sequence>
  <VideoTrackGroup ObjectID="1000" ClassID="video-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="track-v1" />
      </Tracks>
    </TrackGroup>
  </VideoTrackGroup>
  <VideoClipTrack ObjectUID="track-v1" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
          <TrackItem Index="0" ObjectRef="2000" />
          <TrackItem Index="1" ObjectRef="2100" />
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <VideoClipTrackItem ObjectID="2000" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>{first_start}</Start>
        <End>{first_end}</End>
      </TrackItem>
      <SubClip ObjectRef="2001" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <SubClip ObjectID="2001" ClassID="subclip" Version="1">
    <Name>260806_01__wcp.png</Name>
    <Clip ObjectRef="2002" />
  </SubClip>
  <VideoClip ObjectID="2002" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>{still_in}</InPoint>
      <OutPoint>{still_out}</OutPoint>
      <Source ObjectRef="2003" />
    </Clip>
  </VideoClip>
  <VideoMediaSource ObjectID="2003" ClassID="video-media-source" Version="1">
    <MediaSource Version="1">
      <Media ObjectURef="media-first" />
    </MediaSource>
  </VideoMediaSource>
  <Media ObjectUID="media-first" ClassID="media" Version="1">
    <ActualMediaFilePath>{first_path}</ActualMediaFilePath>
    <Infinite>true</Infinite>
  </Media>
  <VideoClipTrackItem ObjectID="2100" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>{second_start}</Start>
        <End>{second_end}</End>
      </TrackItem>
      <SubClip ObjectRef="2101" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <SubClip ObjectID="2101" ClassID="subclip" Version="1">
    <Name>260806_01__wcp.png</Name>
    <Clip ObjectRef="2102" />
  </SubClip>
  <VideoClip ObjectID="2102" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>{still_in}</InPoint>
      <OutPoint>{still_out}</OutPoint>
      <Source ObjectRef="2103" />
    </Clip>
  </VideoClip>
  <VideoMediaSource ObjectID="2103" ClassID="video-media-source" Version="1">
    <MediaSource Version="1">
      <Media ObjectURef="media-second" />
    </MediaSource>
  </VideoMediaSource>
  <Media ObjectUID="media-second" ClassID="media" Version="1">
    <ActualMediaFilePath>{second_path}</ActualMediaFilePath>
    <Infinite>true</Infinite>
  </Media>
</PremiereData>
"""
    project_path.write_bytes(gzip.compress(project_xml.encode("utf-8")))


def _write_still_and_video_project(project_path: Path) -> None:
    video_start = _ticks(0)
    video_end = _ticks(10)
    photo_start = _ticks(10)
    photo_end = _ticks(15)
    still_in = _ticks(3600)
    still_out = _ticks(3605)
    project_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <RootProjectItem ObjectURef="root-project" />
  <RootProjectItem ObjectUID="root-project" ClassID="root-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>Root</Name>
    </ProjectItem>
    <ProjectItemContainer Version="1">
      <Items Version="1">
        <Item Index="0" ObjectURef="project-item-raw" />
      </Items>
    </ProjectItemContainer>
  </RootProjectItem>
  <ClipProjectItem ObjectUID="project-item-raw" ClassID="clip-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>RawSequence</Name>
    </ProjectItem>
    <MasterClip ObjectURef="master-raw" />
  </ClipProjectItem>
  <MasterClip ObjectUID="master-raw" ClassID="master-clip" Version="1">
    <Name>RawSequence</Name>
  </MasterClip>
  <Sequence ObjectUID="seq-raw" ClassID="sequence" Version="1">
    <TrackGroups Version="1">
      <TrackGroup Version="1" Index="0">
        <Second ObjectRef="1000" />
      </TrackGroup>
    </TrackGroups>
    <Name>RawSequence</Name>
  </Sequence>
  <VideoTrackGroup ObjectID="1000" ClassID="video-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="track-v1" />
      </Tracks>
    </TrackGroup>
  </VideoTrackGroup>
  <VideoClipTrack ObjectUID="track-v1" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
          <TrackItem Index="0" ObjectRef="2000" />
          <TrackItem Index="1" ObjectRef="2100" />
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <VideoClipTrackItem ObjectID="2000" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>{video_start}</Start>
        <End>{video_end}</End>
      </TrackItem>
      <SubClip ObjectRef="2001" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <SubClip ObjectID="2001" ClassID="subclip" Version="1">
    <Name>clip_a.mp4</Name>
    <Clip ObjectRef="2002" />
  </SubClip>
  <VideoClip ObjectID="2002" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>{video_end - video_start}</OutPoint>
      <Source ObjectRef="2003" />
    </Clip>
  </VideoClip>
  <VideoMediaSource ObjectID="2003" ClassID="video-media-source" Version="1">
    <MediaSource Version="1">
      <Media ObjectURef="media-video" />
    </MediaSource>
  </VideoMediaSource>
  <Media ObjectUID="media-video" ClassID="media" Version="1">
    <ActualMediaFilePath>E:/media/clip_a.mp4</ActualMediaFilePath>
  </Media>
  <VideoClipTrackItem ObjectID="2100" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>{photo_start}</Start>
        <End>{photo_end}</End>
      </TrackItem>
      <SubClip ObjectRef="2101" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <SubClip ObjectID="2101" ClassID="subclip" Version="1">
    <Name>still.jpg</Name>
    <Clip ObjectRef="2102" />
  </SubClip>
  <VideoClip ObjectID="2102" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>{still_in}</InPoint>
      <OutPoint>{still_out}</OutPoint>
      <Source ObjectRef="2103" />
    </Clip>
  </VideoClip>
  <VideoMediaSource ObjectID="2103" ClassID="video-media-source" Version="1">
    <MediaSource Version="1">
      <Media ObjectURef="media-photo" />
    </MediaSource>
  </VideoMediaSource>
  <Media ObjectUID="media-photo" ClassID="media" Version="1">
    <ActualMediaFilePath>E:/media/still.jpg</ActualMediaFilePath>
    <Infinite>true</Infinite>
  </Media>
</PremiereData>
"""
    project_path.write_bytes(gzip.compress(project_xml.encode("utf-8")))


def _write_mixed_av_project(project_path: Path) -> None:
    durations = [10, 40, 20]
    starts = [0, 10, 50]
    names = ["clip_a.mp4", "clip_b_birthday.mp4", "clip_c_trash.mp4"]
    video_refs: list[str] = []
    audio_refs: list[str] = []
    objects: list[str] = []
    object_id = 2000
    for index, (name, start_s, duration_s) in enumerate(zip(names, starts, durations)):
        start = _ticks(start_s)
        end = _ticks(start_s + duration_s)
        item_id = object_id
        subclip_id = object_id + 1
        clip_id = object_id + 2
        media_id = object_id + 3
        object_id += 10
        video_refs.append(f'          <TrackItem Index="{index}" ObjectRef="{item_id}" />')
        objects.append(
            f"""
  <VideoClipTrackItem ObjectID="{item_id}" ClassID="video-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>{start}</Start>
        <End>{end}</End>
      </TrackItem>
      <SubClip ObjectRef="{subclip_id}" />
    </ClipTrackItem>
  </VideoClipTrackItem>
  <SubClip ObjectID="{subclip_id}" ClassID="subclip" Version="1">
    <Name>{name}</Name>
    <Clip ObjectRef="{clip_id}" />
  </SubClip>
  <VideoClip ObjectID="{clip_id}" ClassID="video-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>{end - start}</OutPoint>
      <Source ObjectRef="{media_id}" />
      <ClipID>00000000-0000-0000-0000-00000000{index:04d}</ClipID>
    </Clip>
  </VideoClip>
  <VideoMediaSource ObjectID="{media_id}" ClassID="video-media-source" Version="1">
    <MediaSource Version="1">
      <Media ObjectURef="media-{index}" />
    </MediaSource>
  </VideoMediaSource>
  <Media ObjectUID="media-{index}" ClassID="media" Version="1">
    <ActualMediaFilePath>E:/media/{name}</ActualMediaFilePath>
  </Media>
"""
        )
        if index < 2:
            audio_item_id = object_id
            audio_subclip_id = object_id + 1
            audio_clip_id = object_id + 2
            audio_media_id = object_id + 3
            object_id += 10
            audio_refs.append(f'          <TrackItem Index="{index}" ObjectRef="{audio_item_id}" />')
            objects.append(
                f"""
  <AudioClipTrackItem ObjectID="{audio_item_id}" ClassID="audio-item" Version="1">
    <ClipTrackItem Version="1">
      <TrackItem Version="1">
        <Start>{start}</Start>
        <End>{end}</End>
      </TrackItem>
      <SubClip ObjectRef="{audio_subclip_id}" />
    </ClipTrackItem>
  </AudioClipTrackItem>
  <SubClip ObjectID="{audio_subclip_id}" ClassID="subclip" Version="1">
    <Name>{name}</Name>
    <Clip ObjectRef="{audio_clip_id}" />
  </SubClip>
  <AudioClip ObjectID="{audio_clip_id}" ClassID="audio-clip" Version="1">
    <Clip Version="1">
      <InPoint>0</InPoint>
      <OutPoint>{end - start}</OutPoint>
      <Source ObjectRef="{audio_media_id}" />
      <ClipID>10000000-0000-0000-0000-00000000{index:04d}</ClipID>
    </Clip>
  </AudioClip>
  <AudioMediaSource ObjectID="{audio_media_id}" ClassID="audio-media-source" Version="1">
    <MediaSource Version="1">
      <Media ObjectURef="media-{index}" />
    </MediaSource>
  </AudioMediaSource>
"""
            )

    project_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <RootProjectItem ObjectURef="root-project" />
  <RootProjectItem ObjectUID="root-project" ClassID="root-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>Root</Name>
    </ProjectItem>
    <ProjectItemContainer Version="1">
      <Items Version="1">
        <Item Index="0" ObjectURef="project-item-raw" />
      </Items>
    </ProjectItemContainer>
  </RootProjectItem>
  <ClipProjectItem ObjectUID="project-item-raw" ClassID="clip-project-item" Version="1">
    <ProjectItem Version="1">
      <Name>RawSequence</Name>
    </ProjectItem>
    <MasterClip ObjectURef="master-raw" />
  </ClipProjectItem>
  <MasterClip ObjectUID="master-raw" ClassID="master-clip" Version="1">
    <Name>RawSequence</Name>
  </MasterClip>
  <Sequence ObjectUID="seq-raw" ClassID="sequence" Version="1">
    <Node Version="1">
      <Properties Version="1">
        <MZ.WorkOutPoint>{_ticks(70)}</MZ.WorkOutPoint>
        <MZ.EditLine>{_ticks(70)}</MZ.EditLine>
      </Properties>
    </Node>
    <TrackGroups Version="1">
      <TrackGroup Version="1" Index="0">
        <Second ObjectRef="1000" />
      </TrackGroup>
      <TrackGroup Version="1" Index="1">
        <Second ObjectRef="1001" />
      </TrackGroup>
    </TrackGroups>
    <Name>RawSequence</Name>
  </Sequence>
  <VideoTrackGroup ObjectID="1000" ClassID="video-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="track-v1" />
      </Tracks>
    </TrackGroup>
  </VideoTrackGroup>
  <AudioTrackGroup ObjectID="1001" ClassID="audio-group" Version="1">
    <TrackGroup Version="1">
      <Tracks Version="1">
        <Track Index="0" ObjectURef="track-a1" />
      </Tracks>
    </TrackGroup>
  </AudioTrackGroup>
  <VideoClipTrack ObjectUID="track-v1" ClassID="video-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
{chr(10).join(video_refs)}
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </VideoClipTrack>
  <AudioClipTrack ObjectUID="track-a1" ClassID="audio-track" Version="1">
    <ClipTrack Version="1">
      <ClipItems Version="1">
        <TrackItems Version="1">
{chr(10).join(audio_refs)}
        </TrackItems>
      </ClipItems>
    </ClipTrack>
  </AudioClipTrack>
  {''.join(objects)}
</PremiereData>
"""
    project_path.write_bytes(gzip.compress(project_xml.encode("utf-8")))
