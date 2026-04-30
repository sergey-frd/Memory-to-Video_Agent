from __future__ import annotations

import os
import time
from pathlib import Path
from shutil import copy2, copytree, rmtree

from config import GenerationConfig, Settings


def resolve_delivery_dir(settings: Settings, configured_dir: str) -> Path:
    path = Path(configured_dir)
    if not path.is_absolute():
        path = settings.project_root / path
    return path


def derive_reports_dir_from_regeneration_assets(regeneration_assets_dir: Path) -> Path:
    return regeneration_assets_dir.parent / "reports"


def path_is_within_directory(path: Path, directory: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(directory.resolve(strict=False))
        return True
    except ValueError:
        return False


def sync_final_media_file(settings: Settings, generation_config: GenerationConfig, artifact_path: Path) -> Path:
    if not artifact_path.exists():
        return artifact_path
    target_dir = resolve_delivery_dir(settings, generation_config.final_videos_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / artifact_path.name
    copy2(artifact_path, target_path)
    return target_path


def sync_video_file(settings: Settings, generation_config: GenerationConfig, video_path: Path) -> Path:
    return sync_final_media_file(settings, generation_config, video_path)


def sync_stage_non_video_assets(
    settings: Settings,
    generation_config: GenerationConfig,
    stage_id: str,
    *,
    extra_files: list[Path] | None = None,
) -> list[Path]:
    target_dir = resolve_delivery_dir(settings, generation_config.regeneration_assets_dir) / stage_id
    target_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    candidates: dict[Path, None] = {}
    for path in settings.output_dir.glob(f"{stage_id}_*"):
        candidates[path] = None
    for path in extra_files or []:
        candidates[path] = None

    for source_path in candidates:
        if not source_path.exists() or not source_path.is_file():
            continue
        if source_path.suffix.lower() == ".mp4":
            continue
        if "_bg_image_" in source_path.name:
            continue
        target_path = target_dir / source_path.name
        copy2(source_path, target_path)
        copied.append(target_path)
    return sorted(copied)


def clear_directory_contents(directory: Path) -> None:
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
        return
    for child in list(directory.iterdir()):
        _delete_path(child)
    directory.mkdir(parents=True, exist_ok=True)


def move_files_to_directory(files: list[Path], target_dir: Path) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for source_path in files:
        if not source_path.exists() or not source_path.is_file():
            continue
        target_path = target_dir / source_path.name
        if target_path.exists():
            _delete_path(target_path)
        try:
            os.replace(source_path, target_path)
        except OSError:
            copy2(source_path, target_path)
            _delete_path(source_path)
        moved.append(target_path)
    return moved


def move_directory_contents(source_dir: Path, target_dir: Path) -> list[Path]:
    if not source_dir.exists():
        return []
    if not source_dir.is_dir():
        raise NotADirectoryError(f"Expected directory, got file: {source_dir}")

    target_dir.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for source_path in list(source_dir.iterdir()):
        target_path = target_dir / source_path.name
        if target_path.exists():
            _delete_path(target_path)
        try:
            os.replace(source_path, target_path)
        except OSError:
            if source_path.is_dir():
                copytree(source_path, target_path)
            else:
                copy2(source_path, target_path)
        moved.append(target_path)
    return moved


def copy_file_to_path(source_path: Path, target_path: Path) -> Path:
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    copy2(source_path, target_path)
    return target_path


def move_output_stage_to_error(settings: Settings, stage_id: str) -> list[Path]:
    stage_files = [path for path in settings.output_dir.glob(f"{stage_id}_*") if path.is_file()]
    return move_files_to_directory(stage_files, settings.project_root / "error" / "output" / stage_id)


def move_input_files_to_error(settings: Settings, stage_id: str, files: list[Path]) -> list[Path]:
    return move_files_to_directory(files, settings.project_root / "error" / "input" / stage_id)


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    _delete_path(path)


def _delete_path(path: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(12):
        try:
            if path.is_dir():
                _remove_tree(path)
            else:
                path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            last_error = exc
            try:
                os.chmod(path, 0o666)
            except OSError:
                pass
            time.sleep(0.2 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _remove_tree(path: Path) -> None:
    def onexc(func: object, failing_path: str, exc: BaseException) -> None:
        try:
            os.chmod(failing_path, 0o777)
        except OSError:
            pass
        if callable(func):
            func(failing_path)

    rmtree(path, onexc=onexc)
