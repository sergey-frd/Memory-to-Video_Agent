"""Remove only archived video-generation metadata for a closed project."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import Settings, load_generation_config
from utils.grok_workspace import delivered_outputs, digest
from utils.project_delivery import resolve_delivery_dir
from scripts.run_grok_queue import queue_lock


def cleanup(settings, config_path, manifest_path, execute=False):
    config = load_generation_config(config_path)
    delivery = resolve_delivery_dir(settings, config.final_videos_dir).resolve()
    permanent = resolve_delivery_dir(settings, config.regeneration_assets_dir).resolve()
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_relative_to(permanent):
        raise ValueError('Closeout manifest must be in this project regeneration_assets_dir')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('status') != 'ARCHIVED_AND_CLEANED':
        raise ValueError('Only a completed, archived project may discard retry metadata')
    output = settings.output_dir.resolve()
    scope = hashlib.sha256(str(delivery).casefold().encode()).hexdigest()
    registry = output / '.grok-completed' / scope
    cache = output / '.grok-queues' / 'prompt-cache'
    # Do not remove a shared prompt cache while any queue still has a live plan.
    live_plans = list((output / '.grok-queues').glob('*/plan.json'))
    for path in live_plans:
        live = json.loads(path.read_text(encoding='utf-8'))
        configured = live.get('profile', {}).get('delivery_config', live.get('config_file', ''))
        if Path(configured).resolve() == config_path.resolve() and not live.get('queue_complete'):
            raise ValueError('This project still has an unfinished queue')
    allow_cache = not live_plans
    prompts = set()
    for entry in manifest['entries']:
        target = Path(entry['target'])
        if target.name == 'plan.json' and target.resolve().is_relative_to(manifest_path.parent):
            if not target.is_file() or digest(target) != entry['sha256']:
                raise ValueError('Archived queue plan changed')
            plan = json.loads(target.read_text(encoding='utf-8'))
            # An archived plan belongs to this config, either directly or via its profile.
            configured = plan.get('profile', {}).get('delivery_config', plan.get('config_file', ''))
            if Path(configured).resolve() == config_path.resolve():
                prompts.update(i.get('prompt') for i in plan.get('items', []) if i.get('prompt'))
    candidates = []
    for entry in manifest['entries']:
        source, target = Path(entry['source']), Path(entry['target'])
        is_record = source.parent == registry
        is_cache = source.parent == cache and allow_cache
        if not (is_record or is_cache) or not re.fullmatch(r'[a-f0-9]{64}\.json', source.name):
            continue
        if not source.exists():
            continue
        if source.is_symlink() or source.resolve() != source or not source.resolve().is_relative_to(output):
            raise ValueError('Redirected metadata path')
        if not target.resolve().is_relative_to(manifest_path.parent):
            raise ValueError('Archive target is outside the manifest directory')
        if not target.is_file() or digest(source) != entry['sha256'] or digest(target) != entry['sha256']:
            raise ValueError(f'Metadata changed or archive unavailable: {source}')
        data = json.loads(source.read_text(encoding='utf-8'))
        if is_record:
            videos = delivered_outputs(data)
            if not videos or any(not p.resolve().is_relative_to(delivery) for p in videos):
                raise ValueError('Delivered video is missing, changed or belongs to another project')
        elif json.loads(data.get('output', '{}')).get('prompt') not in prompts:
            continue  # unproven/shared cache ownership: leave it untouched
        candidates.append((source, target, entry['sha256']))
    # An old on-disk lock can be removed only when unlocked and no other Grok state remains.
    lock = output / '.grok-queue.lock'
    selected = {p for p, _, _ in candidates}
    remaining = [p for folder in (output / '.grok-completed', output / '.grok-queues', output / '.grok-sessions')
                 for p in folder.rglob('*') if p.is_file() and p not in selected]
    remove_lock = lock.is_file() and not remaining
    if remove_lock and (lock.is_symlink() or lock.resolve() != lock or lock.read_bytes() != b'0'):
        raise ValueError('Unexpected legacy lock contents or path')
    if execute:
        if remove_lock:
            import msvcrt
            with lock.open('r+b') as stream:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        for source, target, expected in candidates:
            if digest(source) != expected or digest(target) != expected:
                raise ValueError('Metadata changed before cleanup')
            source.unlink()
        if remove_lock:
            lock.unlink()
        # Only known metadata parents, and only when empty. Never recurse-delete output.
        for folder in (registry, registry.parent, cache, cache.parent):
            if folder.is_dir() and folder.resolve() == folder and not any(folder.iterdir()):
                folder.rmdir()
    return {'mode': 'executed' if execute else 'dry-run',
            'metadata_files': len(candidates), 'legacy_lock': remove_lock,
            'paths': [str(p) for p, _, _ in candidates],
            'shared_cache_preserved_for_live_queues': bool(live_plans)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-file', type=Path, required=True)
    parser.add_argument('--archive-manifest', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    settings = Settings()
    with queue_lock(settings.output_dir / '.grok-queue.lock'):
        result = cleanup(settings, args.config_file.resolve(), args.archive_manifest, args.execute)
    print(json.dumps(result, ensure_ascii=False, indent=2))
