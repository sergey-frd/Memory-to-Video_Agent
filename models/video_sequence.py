from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class PremiereSequenceClip:
    sequence_name: str
    order_index: int
    track_index: int
    clipitem_id: str
    name: str
    source_path: str
    start: int
    end: int
    in_point: int
    out_point: int
    duration: int
    stage_id: str
    video_index: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ClipAssetBundle:
    stage_id: str
    bundle_dir: str
    manifest_path: str | None = None
    scene_analysis_path: str | None = None
    prompt_path: str | None = None
    manifest: dict[str, object] = field(default_factory=dict)
    scene_analysis: dict[str, object] = field(default_factory=dict)
    prompt_text: str = ""
    missing_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SequenceCandidate:
    clip: PremiereSequenceClip
    assets: ClipAssetBundle
    keywords: list[str]
    people_count: int
    shot_scale: int
    energy_level: int
    series_subject_tokens: list[str] = field(default_factory=list)
    series_appearance_tokens: list[str] = field(default_factory=list)
    series_pose_tokens: list[str] = field(default_factory=list)
    main_character_priority: float = 0.0
    opening_score: float = 0.0
    main_character_age_hint: float | None = None
    main_character_notes: list[str] = field(default_factory=list)
    continuity_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["clip"] = self.clip.to_dict()
        payload["assets"] = self.assets.to_dict()
        return payload


@dataclass
class SequenceClipEditPlan:
    media_kind: str
    original_duration: int
    recommended_duration: int
    duration_reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SequenceTransitionPlan:
    to_recommended_index: int
    to_original_index: int
    to_stage_id: str
    to_clip_name: str
    media_pair: str
    transition_key: str
    transition_name: str
    recommended_duration: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SequenceTransformPlan:
    media_kind: str
    transform_key: str
    transform_name: str
    effect_name: str
    fallback_effect_name: str
    start_scale: float
    end_scale: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SequenceRecommendationEntry:
    recommended_index: int
    original_index: int
    score: float
    reason: str
    candidate: SequenceCandidate
    edit_plan: SequenceClipEditPlan | None = None
    transition_to_next: SequenceTransitionPlan | None = None
    transform_plan: SequenceTransformPlan | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "recommended_index": self.recommended_index,
            "original_index": self.original_index,
            "score": self.score,
            "reason": self.reason,
            "candidate": self.candidate.to_dict(),
            "edit_plan": self.edit_plan.to_dict() if self.edit_plan else None,
            "transition_to_next": self.transition_to_next.to_dict() if self.transition_to_next else None,
            "transform_plan": self.transform_plan.to_dict() if self.transform_plan else None,
        }


@dataclass
class LostEffectIssue:
    sequence_name: str
    track_type: str
    track_index: int
    clip_name: str
    effect_name: str
    raw_message: str
    stage_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ClipLostEffectsSummary:
    clip_name: str
    effect_names: list[str]
    track_locations: list[str]
    stage_id: str | None = None
    original_index: int | None = None
    recommended_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SequenceOptimizationResult:
    source_xml: str
    selected_sequence_name: str
    engine_requested: str
    engine_used: str
    warnings: list[str]
    entries: list[SequenceRecommendationEntry]
    feature_flags: dict[str, bool] = field(default_factory=dict)
    translation_report_path: str | None = None
    translation_warnings: list[str] = field(default_factory=list)
    lost_effect_issues: list[LostEffectIssue] = field(default_factory=list)
    clips_with_lost_effects: list[ClipLostEffectsSummary] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_xml": self.source_xml,
            "selected_sequence_name": self.selected_sequence_name,
            "engine_requested": self.engine_requested,
            "engine_used": self.engine_used,
            "warnings": list(self.warnings),
            "entries": [entry.to_dict() for entry in self.entries],
            "feature_flags": dict(self.feature_flags),
            "translation_report_path": self.translation_report_path,
            "translation_warnings": list(self.translation_warnings),
            "lost_effect_issues": [issue.to_dict() for issue in self.lost_effect_issues],
            "clips_with_lost_effects": [item.to_dict() for item in self.clips_with_lost_effects],
        }
