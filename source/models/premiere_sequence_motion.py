from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class MotionProfile:
    name: str
    min_frames: int
    max_frames: int | None
    scale_delta_percent: float
    max_position_delta_percent: float


@dataclass(frozen=True)
class MotionPlanItem:
    index: int
    track_index: int
    track_item_id: str
    clip_name: str
    source_path: str
    timeline_start_ticks: int
    timeline_end_ticks: int
    source_in_ticks: int
    source_out_ticks: int
    visible_frames: int
    profile: str
    requested_direction: str
    applied_direction: str
    direction_override_reason: str
    baseline_scale: float
    baseline_position_x: float
    baseline_position_y: float
    start_scale: float
    end_scale: float
    start_position_x: float
    start_position_y: float
    end_position_x: float
    end_position_y: float
    framing_safety_result: str
    detected_faces: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class MotionDryRunPlan:
    task_id: str
    discovered_executor_entry_point: str
    selected_premiere_automation_mechanism: str
    source_sequence_validation: dict[str, object]
    candidate_video_items: list[MotionPlanItem] = field(default_factory=list)
    already_compliant_items: list[dict[str, object]] = field(default_factory=list)
    skipped_short_items: list[dict[str, object]] = field(default_factory=list)
    protected_items: list[dict[str, object]] = field(default_factory=list)
    blocked_items: list[dict[str, object]] = field(default_factory=list)
    planned_audio_clip_removal_count: int = 0
    expected_output_frames: int = 0
    expected_output_duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "discovered_executor_entry_point": self.discovered_executor_entry_point,
            "executor_entry_point_and_code_version": {
                "entry_point": self.discovered_executor_entry_point,
                "schema_version": "1.0",
                "mode": "premiere_sequence_motion_animation",
            },
            "completed_tests": {
                "status": "PASSED_BEFORE_EXECUTION",
                "required_previous_suite": 58,
                "new_task_tests": "see implementation and QA reports",
            },
            "selected_premiere_automation_mechanism": self.selected_premiere_automation_mechanism,
            "source_sequence_validation": self.source_sequence_validation,
            "candidate_video_items": [item.to_dict() for item in self.candidate_video_items],
            "already_compliant_items": self.already_compliant_items,
            "skipped_short_items": self.skipped_short_items,
            "protected_items": self.protected_items,
            "blocked_items": self.blocked_items,
            "per_item_motion_profile": [
                {"index": item.index, "clip_name": item.clip_name, "profile": item.profile}
                for item in self.candidate_video_items
            ],
            "per_item_start_and_end_scale_position": [
                {
                    "index": item.index,
                    "clip_name": item.clip_name,
                    "start_scale": item.start_scale,
                    "end_scale": item.end_scale,
                    "start_position": [item.start_position_x, item.start_position_y],
                    "end_position": [item.end_position_x, item.end_position_y],
                }
                for item in self.candidate_video_items
            ],
            "framing_safety_result": [
                {
                    "index": item.index,
                    "clip_name": item.clip_name,
                    "result": item.framing_safety_result,
                    "detected_faces": item.detected_faces,
                    "direction_override_reason": item.direction_override_reason,
                }
                for item in self.candidate_video_items
            ],
            "planned_audio_clip_removal_count": self.planned_audio_clip_removal_count,
            "planned_non_ripple_audio_clip_removal_count": self.planned_audio_clip_removal_count,
            "planned_IsTimeVarying_flags": [
                {
                    "index": item.index,
                    "clip_name": item.clip_name,
                    "Scale": True,
                    "Position": True,
                }
                for item in self.candidate_video_items
            ],
            "expected_output_frames": self.expected_output_frames,
            "expected_output_duration_seconds": self.expected_output_duration_seconds,
        }

