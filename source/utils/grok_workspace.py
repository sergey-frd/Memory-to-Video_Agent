"""Resumable, isolated workspaces. Never delete the shared output directory."""
from __future__ import annotations

import hashlib
import json
from copy import copy
from pathlib import Path

from config import Settings


def completion_path(settings: Settings, image: Path, delivery_dir: Path) -> Path:
    """Project destination + image bytes, independent of queue order or filename."""
    scope = hashlib.sha256(str(delivery_dir.resolve()).casefold().encode()).hexdigest()
    directory = settings.output_dir.resolve() / '.grok-completed' / scope
    for path in (directory.parent, directory):
        if path.resolve() != path:
            raise ValueError(f'Redirected completion directory: {path}')
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f'{digest(image)}.json'


def completed_image(settings: Settings, image: Path, delivery_dir: Path) -> list[Path] | None:
    path = completion_path(settings, image, delivery_dir)
    if not path.exists():
        return None
    return delivered_outputs(json.loads(path.read_text(encoding='utf-8')))


def record_completed(settings: Settings, image: Path, delivery_dir: Path, outputs: list[Path]) -> Path:
    if not outputs or any(not p.is_file() or p.stat().st_size == 0 for p in outputs):
        raise ValueError('Cannot record missing or empty delivered results')
    if any(not p.resolve().is_relative_to(delivery_dir.resolve()) for p in outputs):
        raise ValueError('Completed results must be in the configured delivery directory')
    path = completion_path(settings, image, delivery_dir)
    save_state(path, {'complete': True, 'input_sha256': digest(image),
        'source_path': str(image.resolve()),
        'outputs': [{'path': str(p.resolve()), 'sha256': digest(p)} for p in outputs]})
    return path


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save_state(path: Path, state: dict) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def workspace(settings: Settings, image: Path, options: dict) -> tuple[Settings, Path, dict]:
    identity = json.dumps([str(image.resolve()), digest(image), options], sort_keys=True, default=str)
    key = hashlib.sha256(identity.encode()).hexdigest()
    root = settings.output_dir.resolve()
    folder = root / '.grok-sessions' / key
    work = folder / 'work'
    # Refuse junctions/symlinks that redirect writes or cleanup outside this workspace.
    for path in (root / '.grok-sessions', folder, work):
        if path.resolve() != path:
            raise ValueError(f'Redirected Grok workspace is not allowed: {path}')
    work.mkdir(parents=True, exist_ok=True)
    state_path = folder / 'state.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    scoped = copy(settings)
    scoped.output_dir = work
    return scoped, state_path, state


def delivered_outputs(state: dict) -> list[Path] | None:
    if not state.get('complete'):
        return None
    outputs = state.get('outputs', [])
    if not outputs:
        return None
    for record in outputs:
        path = Path(record['path'])
        if not path.is_file() or digest(path) != record['sha256']:
            return None
    return [Path(record['path']) for record in outputs]


def clean_work(work: Path, state_path: Path) -> None:
    expected = state_path.parent / 'work'
    if work != expected or work.resolve() != expected:
        raise ValueError('Refusing cleanup outside the Grok workspace')
    paths = list(work.rglob('*'))
    if any(path.is_symlink() or path.resolve() != path for path in paths):
        raise ValueError('Refusing cleanup of a redirected workspace entry')
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
