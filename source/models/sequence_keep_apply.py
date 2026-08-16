from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KeepRange:
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


@dataclass(frozen=True)
class MediaKeepSpec:
    file_name: str
    ranges: tuple[KeepRange, ...]
    duration_seconds: float | None = None

    @property
    def match_key(self) -> str:
        return Path(self.file_name).name.casefold()
