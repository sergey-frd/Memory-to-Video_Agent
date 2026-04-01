from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class CameraMovementSets:
    nearby: list[str]
    distance: list[str]


def load_camera_movements(services_dir: Path) -> CameraMovementSets:
    """Собирает списки движений камеры из текстовых файлов в services."""
    nearby = _read_list(services_dir / "FULL LIST 3 NEARBY.txt")
    distance = _read_list(services_dir / "FULL LIST 3  DISTANCE.txt")
    return CameraMovementSets(nearby=nearby, distance=distance)


def _read_list(file_path: Path) -> list[str]:
    text = file_path.read_text(encoding="utf-8")
    lines = []
    for candidate in text.splitlines():
        cleaned = candidate.strip()
        if not cleaned or cleaned.upper().startswith("ПОЛНЫЙ") or cleaned.startswith("---"):
            continue
        if "." in cleaned:
            _, _, remainder = cleaned.partition(".")
            cleaned = remainder.strip()
        lines.append(cleaned)
    return lines
