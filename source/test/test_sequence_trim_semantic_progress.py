from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from models.video_sequence import PremiereSequenceClip
from utils.sequence_trim_classifier import seconds_to_ticks
from utils.sequence_trim_semantic import classify_sequence_trim_review_semantic


def test_semantic_engine_reports_api_progress_and_timeout(monkeypatch) -> None:
    root = Path("test_runtime") / f"semantic_progress_{uuid4().hex}"
    root.mkdir(parents=True)
    source_path = root / "clip.mp4"
    source_path.write_bytes(b"video-placeholder")
    duration = seconds_to_ticks(30)
    clip = PremiereSequenceClip(
        sequence_name="Source",
        order_index=1,
        track_index=0,
        clipitem_id="clip-1",
        name="clip.mp4",
        source_path=str(source_path),
        start=0,
        end=duration,
        in_point=0,
        out_point=duration,
        duration=duration,
        stage_id="clip",
        video_index=1,
    )
    frame_path = root / "frame.jpg"
    frame_path.write_bytes(b"frame")
    observed: dict[str, object] = {}
    messages: list[str] = []

    def fake_extract(
        _video_path: Path,
        *,
        output_dir: Path,
        timestamps_sec: list[float],
        prefix: str,
    ) -> list[tuple[float, Path]]:
        del output_dir, prefix
        return [(timestamp, frame_path) for timestamp in timestamps_sec]

    def fake_choose(**kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {
            "keep_start_sec": 5.0,
            "keep_duration_sec": 6.0,
            "reason": "test",
            "confidence": 0.8,
        }

    monkeypatch.setattr("utils.sequence_trim_semantic.extract_video_frames", fake_extract)
    monkeypatch.setattr("utils.sequence_trim_semantic.choose_keep_window_with_openai", fake_choose)

    result = classify_sequence_trim_review_semantic(
        [clip],
        source_project_path=Path("project.prproj"),
        source_sequence_name="Source",
        new_sequence_name="Semantic",
        target_keep_seconds=10,
        min_keep_seconds=2,
        max_keep_seconds=10,
        frames_per_clip=2,
        frames_dir=root / "frames",
        request_timeout_seconds=75,
        progress=messages.append,
    )

    assert result.decisions[0].decision == "mixed"
    assert observed["request_timeout_seconds"] == 75
    assert any("OpenAI semantic request sent" in message for message in messages)
    assert any("OpenAI response received" in message for message in messages)
    assert any("done; decision=mixed" in message for message in messages)
