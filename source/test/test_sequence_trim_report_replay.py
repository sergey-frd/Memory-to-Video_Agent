from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from models.sequence_trim_review import SequenceTrimReviewResult, TrimClipDecision, TrimSegmentDecision
from utils.sequence_trim_report_replay import (
    run_sequence_trim_report_replay_from_config,
    split_hero_result_by_level,
)


def _segment(
    index: int,
    *,
    decision: str,
    level: str,
    start: int,
    end: int,
) -> TrimSegmentDecision:
    return TrimSegmentDecision(
        segment_index=index,
        decision=decision,
        local_start=start,
        local_end=end,
        timeline_start=start,
        timeline_end=end,
        source_in=start,
        source_out=end,
        duration=end - start,
        duration_seconds=float(end - start),
        reason=f"hero {level}",
        confidence=0.8,
        hero_match_level=level,
    )


def _source_result(project_path: Path) -> SequenceTrimReviewResult:
    segments = [
        _segment(1, decision="keep", level="high", start=0, end=10),
        _segment(2, decision="keep", level="medium", start=10, end=20),
        _segment(3, decision="keep", level="uncertain", start=20, end=30),
        _segment(4, decision="drop", level="absent", start=30, end=40),
    ]
    decision = TrimClipDecision(
        order_index=1,
        clipitem_id="clip-1",
        name="clip.mp4",
        source_path="clip.mp4",
        track_index=0,
        start=0,
        end=40,
        duration=40,
        duration_seconds=40.0,
        source_in=0,
        source_out=40,
        keep_seconds=30.0,
        drop_seconds=10.0,
        score=0.9,
        reason="test",
        confidence=0.9,
        decision="mixed",
        segments=segments,
        hero_match_level="high",
    )
    return SequenceTrimReviewResult(
        source_project_path=str(project_path),
        source_sequence_name="Source",
        new_sequence_name="Hero",
        engine="hero_presence_v1",
        target_keep_seconds=0,
        min_keep_seconds=0,
        max_keep_seconds=40,
        total_source_seconds=40,
        keep_seconds=30,
        drop_seconds=10,
        context_notes="test",
        decisions=[decision],
    )


def test_split_hero_result_creates_four_level_sequences() -> None:
    results = split_hero_result_by_level(
        _source_result(Path("project.prproj")),
        sequence_names={
            "high": "HIGH",
            "medium": "MEDIUM",
            "review": "REVIEW",
            "drop": "DROP",
        },
        source_report_path=Path("report.json"),
    )

    assert [result.new_sequence_name for result in results] == ["HIGH", "MEDIUM", "REVIEW", "DROP"]
    assert [result.keep_seconds + result.drop_seconds for result in results] == [10, 10, 10, 10]
    assert [result.decisions[0].segments[0].hero_match_level for result in results] == [
        "high",
        "medium",
        "uncertain",
        "absent",
    ]
    assert all(result.engine_metadata["openai_requests"] == 0 for result in results)


def test_report_replay_exports_without_openai(monkeypatch) -> None:
    root = Path("test_runtime") / f"report_replay_{uuid4().hex}"
    root.mkdir(parents=True)
    project_path = root / "source.prproj"
    project_path.write_bytes(b"placeholder")
    report_path = root / "hero_report.json"
    report_path.write_text(
        json.dumps(_source_result(project_path).to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    output_path = root / "levels.prproj"
    config_path = root / "replay.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "report_replay",
                "review_json_path": str(report_path),
                "output_project_path": str(output_path),
                "reports_dir": str(root / "reports"),
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_export(**kwargs: object) -> tuple[Path, list[str]]:
        captured.update(kwargs)
        target = kwargs["output_project_path"]
        assert isinstance(target, Path)
        target.write_bytes(b"exported")
        return target, []

    monkeypatch.setattr(
        "utils.sequence_trim_report_replay.export_trim_review_premiere_projects",
        fake_export,
    )

    json_path, txt_path, exported_path = run_sequence_trim_report_replay_from_config(config_path)

    assert exported_path == output_path
    assert json_path.exists() and txt_path.exists()
    assert len(captured["review_results"]) == 1  # type: ignore[arg-type]
    assert captured["split_tracks"] is False
    assert captured["hero_level_track_indexes"] == {
        "high": 0,
        "medium": 1,
        "review": 2,
        "drop": 3,
    }
    summary = json.loads(json_path.read_text(encoding="utf-8"))
    assert summary["openai_requests"] == 0
    assert summary["sequence_name"] == "Hero_LEVEL_TRACKS"
    assert [item["premiere_track"] for item in summary["tracks"]] == ["V1", "V2", "V3", "V4"]
