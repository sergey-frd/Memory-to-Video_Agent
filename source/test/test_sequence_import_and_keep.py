from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND,
    parse_premiere_project_sequence_visual_clips,
)
from utils.sequence_import_and_keep import (
    is_import_and_keep_config,
    run_sequence_import_and_keep_from_config,
)
from utils.sequence_keep_apply import is_keep_apply_config
from utils.sequence_media_import import is_media_import_config
from utils.sequence_trim_classifier import ticks_to_seconds

from test_sequence_media_import import (
    _write_empty_import_source_project,
    _write_import_source_project,
)


def test_is_import_and_keep_config_detects_mode_and_paired_paths() -> None:
    assert is_import_and_keep_config({"mode": "import_and_keep", "import_path": "a.json"})
    assert is_import_and_keep_config(
        {"import_path": "a.json", "keep_ranges_path": "b.json"}
    )
    assert is_import_and_keep_config(
        {
            "items": [{"order": 1, "source_path": r"<LOCAL_PATH>"}],
            "operations": [{"file": "a.mp4", "duration": "00:00:01.000"}],
        }
    )
    assert not is_import_and_keep_config(
        {"project_path": r"<LOCAL_PATH>", "items": [{"order": 1, "source_path": r"<LOCAL_PATH>"}]}
    )
    assert not is_import_and_keep_config(
        {"project_path": r"<LOCAL_PATH>", "operations": [{"file": "a.mp4", "duration": 1}]}
    )
    assert is_media_import_config(
        {"project_path": r"<LOCAL_PATH>", "items": [{"order": 1, "source_path": r"<LOCAL_PATH>"}]}
    )
    assert is_keep_apply_config(
        {"project_path": r"<LOCAL_PATH>", "operations": [{"file": "a.mp4", "duration": 1}]}
    )


def test_run_sequence_import_and_keep_imports_then_trims() -> None:
    root = Path("test_runtime") / f"import_keep_{uuid4().hex}"
    media_root = root / "media"
    media_root.mkdir(parents=True)
    video = media_root / "take.mp4"
    photo = media_root / "still.jpg"
    video.write_bytes(b"video")
    photo.write_bytes(b"photo")

    empty_project = root / "empty.prproj"
    donor_project = root / "donor_with_clips.prproj"
    _write_empty_import_source_project(empty_project)
    _write_import_source_project(donor_project)
    original_bytes = empty_project.read_bytes()

    import_job = root / "16_import.json"
    keep_job = root / "17_keep.json"
    wrapper = root / "18_import_keep.json"
    import_output = root / "empty_import.prproj"
    keep_output = root / "empty_keep.prproj"
    import_job.write_text(
        json.dumps(
            {
                "project_path": str(empty_project),
                "sequence_name": "EmptySequence",
                "create_sequence_if_missing": False,
                "items": [
                    {"order": 1, "source_path": str(video)},
                    {"order": 2, "source_path": str(photo)},
                ],
                "still_duration_seconds": 5,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    keep_job.write_text(
        json.dumps(
            {
                "project_path": str(root / "missing_keep_source.prproj"),
                "sequence_name": "EmptySequence",
                "output_project_path": str(keep_output),
                "operations": [
                    {"file": "take.mp4", "keep_ranges": [{"in": "00:00:00.000", "out": "00:00:02.000"}]},
                    {"file": "still.jpg", "duration": "00:00:01.200"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    wrapper.write_text(
        json.dumps(
            {
                "mode": "import_and_keep",
                "import_path": str(import_job),
                "keep_ranges_path": str(keep_job),
                "import_output_project_path": str(import_output),
                "output_project_path": str(keep_output),
                "reports_dir": str(root / "reports"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    json_path, txt_path, exported = run_sequence_import_and_keep_from_config(wrapper)

    assert exported == keep_output
    assert empty_project.read_bytes() == original_bytes
    assert import_output.is_file()
    assert json_path.exists()
    assert txt_path.exists()
    _seq, imported_clips = parse_premiere_project_sequence_visual_clips(import_output, "EmptySequence")
    assert [clip.name for clip in imported_clips] == ["take.mp4", "still.jpg"]
    assert ticks_to_seconds(imported_clips[1].duration) == 5.0
    _seq, kept_clips = parse_premiere_project_sequence_visual_clips(keep_output, "EmptySequence")
    assert [clip.name for clip in kept_clips] == ["take.mp4", "still.jpg"]
    assert ticks_to_seconds(kept_clips[0].duration) == 2.0
    assert ticks_to_seconds(kept_clips[1].duration) == 1.2
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "import_and_keep"
    assert payload["import_output_project_path"] == str(import_output)
    assert payload["output_project_path"] == str(keep_output)


def test_run_sequence_import_and_keep_accepts_inline_jobs() -> None:
    root = Path("test_runtime") / f"import_keep_inline_{uuid4().hex}"
    media_root = root / "media"
    media_root.mkdir(parents=True)
    photo = media_root / "still.jpg"
    photo.write_bytes(b"photo")
    empty_project = root / "empty.prproj"
    donor_project = root / "donor_with_clips.prproj"
    _write_empty_import_source_project(empty_project)
    _write_import_source_project(donor_project)
    keep_output = root / "inline_keep.prproj"
    config_path = root / "combined.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "import_and_keep",
                "project_path": str(empty_project),
                "sequence_name": "EmptySequence",
                "create_sequence_if_missing": False,
                "items": [{"order": 1, "source_path": str(photo)}],
                "operations": [{"file": "still.jpg", "duration": "00:00:01.500"}],
                "output_project_path": str(keep_output),
                "reports_dir": str(root / "reports"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _json_path, _txt_path, exported = run_sequence_import_and_keep_from_config(config_path)
    assert exported == keep_output
    _seq, clips = parse_premiere_project_sequence_visual_clips(keep_output, "EmptySequence")
    assert [clip.name for clip in clips] == ["still.jpg"]
    assert abs(clips[0].duration / PREMIERE_TICKS_PER_SECOND - 1.5) < 0.001
