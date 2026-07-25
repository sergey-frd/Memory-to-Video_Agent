from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from PIL import Image

from api.openai_hero_match import _normalize_level
from models.sequence_trim_review import TrimSegmentDecision
from models.video_sequence import PremiereSequenceClip
from utils.premiere_trim_review_export import _segment_label
from utils.sequence_trim_classifier import seconds_to_ticks
from utils.sequence_trim_hero import (
    _build_hero_decision,
    choose_hero_sample_timestamps,
    classify_sequence_trim_review_hero,
)


def _clip(path: Path, *, name: str, duration_seconds: float = 100.0, order_index: int = 1) -> PremiereSequenceClip:
    duration = seconds_to_ticks(duration_seconds)
    return PremiereSequenceClip(
        sequence_name="Source",
        order_index=order_index,
        track_index=0,
        clipitem_id=f"clip-{order_index}",
        name=name,
        source_path=str(path),
        start=0,
        end=duration,
        in_point=0,
        out_point=duration,
        duration=duration,
        stage_id=path.stem,
        video_index=order_index,
    )


def test_hero_windows_keep_ten_seconds_before_and_after_matches() -> None:
    clip = _clip(Path("video.mp4"), name="video.mp4")
    decision = _build_hero_decision(
        clip,
        frame_matches=[
            {"timestamp_sec": 30.0, "match_level": "high", "confidence": 0.94},
            {"timestamp_sec": 70.0, "match_level": "medium", "confidence": 0.72},
        ],
        is_still=False,
        pre_roll_seconds=10.0,
        post_roll_seconds=10.0,
        keep_medium_matches=True,
    )

    assert decision.decision == "mixed"
    assert decision.hero_match_level == "high"
    assert [segment.decision for segment in decision.segments] == ["drop", "keep", "drop", "keep", "drop"]
    keep_segments = [segment for segment in decision.segments if segment.decision == "keep"]
    assert [(item.duration_seconds, item.hero_match_level) for item in keep_segments] == [
        (20.0, "high"),
        (20.0, "medium"),
    ]


def test_medium_match_can_be_sent_to_drop() -> None:
    clip = _clip(Path("video.mp4"), name="video.mp4", duration_seconds=30)
    decision = _build_hero_decision(
        clip,
        frame_matches=[
            {"timestamp_sec": 15.0, "match_level": "medium", "confidence": 0.70},
        ],
        is_still=False,
        pre_roll_seconds=10.0,
        post_roll_seconds=10.0,
        keep_medium_matches=False,
    )
    assert decision.decision == "drop"
    assert decision.hero_match_level == "medium"


def test_hero_classifier_uses_definition_references_and_labels_confidence() -> None:
    root = Path("test_runtime") / f"hero_trim_{uuid4().hex}"
    root.mkdir(parents=True)
    hero_image = root / "hero.jpg"
    high_image = root / "high.jpg"
    absent_image = root / "absent.jpg"
    for path, color in ((hero_image, "red"), (high_image, "blue"), (absent_image, "green")):
        Image.new("RGB", (32, 32), color).save(path)
    hero_definition_path = root / "hero_def.json"
    hero_definition_path.write_text(
        json.dumps(
            {
                "sources": {"reference_images": [{"path": str(hero_image)}]},
                "definition": {"hero_name": "Алиса", "visual_summary": "test"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seen_reference_paths: list[Path] = []
    progress_messages: list[str] = []

    def fake_matcher(**kwargs: object) -> list[dict[str, object]]:
        seen_reference_paths.extend(kwargs["reference_image_paths"])  # type: ignore[arg-type]
        frame_paths = kwargs["frame_paths"]  # type: ignore[assignment]
        is_high = "high" in str(kwargs["clip_name"])
        return [
            {
                "index": index,
                "timestamp_sec": timestamp,
                "match_level": "high" if is_high else "absent",
                "confidence": 0.95,
                "reason": "test",
                "visible_cues": [],
            }
            for index, (timestamp, _path) in enumerate(frame_paths)  # type: ignore[union-attr]
        ]

    result = classify_sequence_trim_review_hero(
        [
            _clip(high_image, name="high.jpg", duration_seconds=8, order_index=1),
            _clip(absent_image, name="absent.jpg", duration_seconds=8, order_index=2),
        ],
        source_project_path=Path("project.prproj"),
        source_sequence_name="Source",
        new_sequence_name="HeroReview",
        hero_definition_path=hero_definition_path,
        frames_dir=root / "frames",
        matcher=fake_matcher,
        progress=progress_messages.append,
    )

    assert result.engine == "hero_presence_v1"
    assert result.decisions[0].decision == "keep"
    assert result.decisions[0].hero_match_level == "high"
    assert result.decisions[1].decision == "drop"
    assert seen_reference_paths == [hero_image, hero_image]

    resumed = classify_sequence_trim_review_hero(
        [
            _clip(high_image, name="high.jpg", duration_seconds=8, order_index=1),
            _clip(absent_image, name="absent.jpg", duration_seconds=8, order_index=2),
        ],
        source_project_path=Path("project.prproj"),
        source_sequence_name="Source",
        new_sequence_name="HeroReview",
        hero_definition_path=hero_definition_path,
        frames_dir=root / "frames",
        matcher=fake_matcher,
        progress=progress_messages.append,
    )
    assert resumed.decisions[0].hero_match_level == "high"
    assert seen_reference_paths == [hero_image, hero_image]
    assert sum("cache hit" in message for message in progress_messages) == 2


def test_confidence_levels_are_conservative_and_export_labels_are_visible() -> None:
    assert _normalize_level("high", confidence=0.80, high_threshold=0.85, medium_threshold=0.60) == "medium"
    assert _normalize_level("medium", confidence=0.40, high_threshold=0.85, medium_threshold=0.60) == "uncertain"

    base = dict(
        segment_index=1,
        decision="keep",
        local_start=0,
        local_end=1,
        timeline_start=0,
        timeline_end=1,
        source_in=0,
        source_out=1,
        duration=1,
        duration_seconds=1.0,
        reason="test",
        confidence=0.9,
    )
    assert _segment_label(TrimSegmentDecision(**base, hero_match_level="high")) == "KEEP-HIGH"
    assert _segment_label(TrimSegmentDecision(**base, hero_match_level="medium")) == "KEEP-MEDIUM"
    assert _segment_label(TrimSegmentDecision(**base, hero_match_level="uncertain")) == "KEEP-REVIEW"


def test_sampling_is_dense_but_respects_cap() -> None:
    assert len(choose_hero_sample_timestamps(100, frame_interval_seconds=5, max_frames=48)) == 20
    assert len(choose_hero_sample_timestamps(1000, frame_interval_seconds=5, max_frames=48)) == 48
