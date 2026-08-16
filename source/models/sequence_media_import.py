from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImportFileRequest:
    file_name: str
    relative_path: str | None = None
    source_path: Path | None = None
    order: int | None = None


@dataclass(frozen=True)
class MediaImportItem:
    requested_name: str
    source_path: Path
    reused_existing_media: bool
    duration_seconds: float
    kind: str
