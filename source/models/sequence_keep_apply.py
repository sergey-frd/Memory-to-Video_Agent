from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def normalize_keep_media_path(path: str) -> str:
    text = str(path or "").strip().strip('"')
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


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
    source_path: str = ""
    order: int | None = None

    @property
    def name_key(self) -> str:
        return Path(self.file_name).name.casefold()

    @property
    def path_key(self) -> str:
        return normalize_keep_media_path(self.source_path)

    @property
    def group_key(self) -> str:
        return self.path_key or self.name_key

    @property
    def match_key(self) -> str:
        if self.order is not None:
            return f"order:{self.order}"
        return self.group_key


def keep_spec_queues(keep_specs: list[MediaKeepSpec]) -> dict[str, list[MediaKeepSpec]]:
    queues: dict[str, list[MediaKeepSpec]] = {}
    for spec in keep_specs:
        queues.setdefault(spec.group_key, []).append(spec)
    return queues


def take_keep_spec_for_media(
    queues: dict[str, list[MediaKeepSpec]],
    *,
    source_path: str,
    name: str,
) -> MediaKeepSpec | None:
    path_key = normalize_keep_media_path(source_path)
    if path_key:
        bucket = queues.get(path_key)
        if bucket:
            return bucket.pop(0)
    name_key = Path(source_path or name).name.casefold() if (source_path or name) else ""
    if name_key:
        bucket = queues.get(name_key)
        if bucket:
            return bucket.pop(0)
    return None
