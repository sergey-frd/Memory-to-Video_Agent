from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class PersonInFrame:
    label: str
    position_in_frame: str = ""
    role_in_scene: str = ""
    apparent_age_group: str = ""
    apparent_gender_presentation: str = ""
    face_visibility: str = ""
    facial_expression: str = ""
    clothing: str = ""
    pose: str = ""


@dataclass
class SceneAnalysis:
    summary: str
    people_count: int
    people: list[PersonInFrame] = field(default_factory=list)
    background: str = ""
    shot_type: str = ""
    main_action: str = ""
    mood: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
