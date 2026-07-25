from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class TrimSegmentDecision:
    segment_index: int
    decision: str  # keep | drop
    local_start: int
    local_end: int
    timeline_start: int
    timeline_end: int
    source_in: int
    source_out: int
    duration: int
    duration_seconds: float
    reason: str
    confidence: float
    hero_match_level: str = ""  # high | medium | absent | uncertain | ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class TrimClipDecision:
    order_index: int
    clipitem_id: str
    name: str
    source_path: str
    track_index: int
    start: int
    end: int
    duration: int
    duration_seconds: float
    source_in: int
    source_out: int
    keep_seconds: float
    drop_seconds: float
    score: float
    reason: str
    confidence: float
    decision: str  # keep | drop | mixed
    segments: list[TrimSegmentDecision] = field(default_factory=list)
    hero_match_level: str = ""
    hero_frame_matches: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["segments"] = [segment.to_dict() for segment in self.segments]
        payload["hero_frame_matches"] = [dict(item) for item in self.hero_frame_matches]
        return payload


@dataclass
class SequenceTrimReviewResult:
    source_project_path: str
    source_sequence_name: str
    new_sequence_name: str
    engine: str
    target_keep_seconds: float
    min_keep_seconds: float
    max_keep_seconds: float
    total_source_seconds: float
    keep_seconds: float
    drop_seconds: float
    context_notes: str
    engine_metadata: dict[str, object] = field(default_factory=dict)
    open_questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    decisions: list[TrimClipDecision] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_project_path": self.source_project_path,
            "source_sequence_name": self.source_sequence_name,
            "new_sequence_name": self.new_sequence_name,
            "engine": self.engine,
            "target_keep_seconds": self.target_keep_seconds,
            "min_keep_seconds": self.min_keep_seconds,
            "max_keep_seconds": self.max_keep_seconds,
            "total_source_seconds": self.total_source_seconds,
            "keep_seconds": self.keep_seconds,
            "drop_seconds": self.drop_seconds,
            "context_notes": self.context_notes,
            "engine_metadata": dict(self.engine_metadata),
            "open_questions": list(self.open_questions),
            "warnings": list(self.warnings),
            "decisions": [item.to_dict() for item in self.decisions],
        }
