"""Read-only Premiere source inventory and visual evidence for classification."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageOps
from config import load_generation_config
from utils.premiere_project import (
    PREMIERE_TICKS_PER_SECOND, load_premiere_project_root,
    find_project_sequence_node, build_project_object_id_lookup,
    build_project_object_uid_lookup, get_project_track_nodes, iter_project_track_item_refs,
)
from utils.premiere_sequence_motion import _track_item_contexts, IMAGE_SUFFIXES, VIDEO_SUFFIXES
from utils.video_frame_extract import resolve_ffmpeg_executable


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def gif_frame_at(path, seconds):
    """Return the displayed frame, including its hold until the next timestamp."""
    with Image.open(path) as im:
        end_ms = 0
        for frame in range(im.n_frames):
            im.seek(frame)
            end_ms += max(10, im.info.get('duration', 100))
            if seconds * 1000 < end_ms:
                return im.convert('RGB')
    raise ValueError(f'GIF sample outside source duration: {path} at {seconds}s')


def collect(project, sequence_name, *, hash_media=True):
    root = load_premiere_project_root(project)
    sequence = find_project_sequence_node(root, sequence_name)
    if sequence is None:
        raise ValueError(f'Sequence not found: {sequence_name}')
    ids, uids = build_project_object_id_lookup(root), build_project_object_uid_lookup(root)
    items = _track_item_contexts(sequence, group_index=0, id_lookup=ids,
                               uid_lookup=uids, project_path=project)
    expected = sum(len(list(iter_project_track_item_refs(track))) for _, track in
                   get_project_track_nodes(sequence, track_group_index=0,
                                           object_id_lookup=ids, object_uid_lookup=uids))
    if expected != len(items):
        raise ValueError('Unresolved video track items: refusing incomplete inventory')
    if not items:
        raise ValueError('Source sequence has no visual placements')
    rows, lookup = [], {}
    for number, item in enumerate(items, 1):
        if not item.source_path:
            raise ValueError(f'Unresolved media / nested sequence at placement {number}: {item.name}')
        path = Path(item.source_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        key = str(path).casefold()
        if key not in lookup:
            # GIF is temporal media: preserve animation and the Premiere source range.
            # Keep this workflow-specific support separate from motion-effect helpers.
            kind = 'image' if path.suffix.lower() in IMAGE_SUFFIXES else 'video' if path.suffix.lower() in VIDEO_SUFFIXES | {'.gif'} else None
            if kind is None:
                raise ValueError(f'Unsupported visual source: {path}')
            row = dict(id='M' + hashlib.sha256(key.encode()).hexdigest()[:16],
                       path=str(path), kind=kind, sha256=digest(path) if hash_media else None, bytes=path.stat().st_size,
                       placements=[], previews=[])
            lookup[key] = row
            rows.append(row)
        lookup[key]['placements'].append(dict(number=number, track_index=item.track_index,
            name=item.name, start_ticks=item.start, end_ticks=item.end,
            source_in_ticks=item.source_in, source_out_ticks=item.source_out))
    return rows


def prepare(config_path):
    config_path = config_path.resolve()
    cfg = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if cfg.get('schema_version') != 1:
        raise ValueError('Expected schema_version 1')
    def local(value):
        p = Path(value)
        return p.resolve() if p.is_absolute() else (config_path.parent / p).resolve()
    project = local(cfg['project'])
    before = digest(project)
    hero_path = local(cfg['hero_config'])
    hero = load_generation_config(hero_path)
    rows = collect(project, cfg['source_sequence'])
    out = local(cfg['output_root']) / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=False)
    (out / 'previews').mkdir()
    (out / 'context').mkdir()
    manifest = dict(schema_version=1, task_id=cfg['task_id'], status='BUILDING',
        project=str(project), project_sha256=before, source_sequence=cfg['source_sequence'],
        target_sequence=cfg.get('target_sequence'), inventory='inventory.json',
        ticks_per_second=PREMIERE_TICKS_PER_SECOND, context={}, contact_sheets=[],
        scope='Visual source media only; no Premiere effects, audio analysis or AI classification.',
        video_evidence='Three source samples per placement; review full used ranges for motion/audio.',
        ai_enabled_by_default=True)
    write_json(out / 'classification_input.json', manifest)
    try:
        for field in ('human_detail_txt', 'family_detail_txt', 'family_screenshot'):
            value = getattr(hero, field)
            if not value:
                continue
            source = Path(value)
            if not source.is_absolute():
                source = hero_path.parent / source
            target = out / 'context' / (field + source.suffix)
            h = digest(source)
            shutil.copyfile(source, target)
            if digest(target) != h:
                raise ValueError(f'Context changed while copying: {field}')
            manifest['context'][field] = dict(path=target.relative_to(out).as_posix(), sha256=h)
        ffmpeg = resolve_ffmpeg_executable() if any(r['kind'] == 'video' for r in rows) else None
        def previews(row):
            tasks = [(None, None)] if row['kind'] == 'image' else [
                (p['number'], (p['source_in_ticks'] + fraction *
                 (p['source_out_ticks'] - p['source_in_ticks'])) / PREMIERE_TICKS_PER_SECOND)
                for p in row['placements'] for fraction in (.1, .5, .9)]
            for n, (placement, seconds) in enumerate(tasks):
                rel = f"previews/{row['id']}_{n:04}.jpg"
                target = out / rel
                if seconds is None:
                    with Image.open(row['path']) as im:
                        row['size'] = list(im.size)
                        ImageOps.contain(ImageOps.exif_transpose(im).convert('RGB'), (640, 480)).save(target)
                elif Path(row['path']).suffix.lower() == '.gif':
                    # FFmpeg seeking can skip a final held frame (e.g. 1.8s in a
                    # 2s GIF whose last frame starts at 1.6s).
                    with gif_frame_at(row['path'], seconds) as im:
                        ImageOps.contain(im, (640, 480)).save(target)
                else:
                    subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-ss', str(max(0, seconds)),
                        '-i', row['path'], '-frames:v', '1', '-vf',
                        'scale=640:480:force_original_aspect_ratio=decrease', '-y', str(target)],
                        check=True, capture_output=True, timeout=90)
                    with Image.open(target) as im:
                        im.verify()
                row['previews'].append(dict(path=rel, placement=placement, source_seconds=seconds))
            if digest(row['path']) != row['sha256']:
                raise ValueError(f"Source changed: {row['path']}")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(previews, rows))
        evidence = [(r, p) for r in rows for p in r['previews']]
        for offset in range(0, len(evidence), 20):
            sheet = Image.new('RGB', (1400, 1120), 'white')
            draw = ImageDraw.Draw(sheet)
            for j, (row, preview) in enumerate(evidence[offset:offset + 20]):
                x, y = j % 5 * 280, j // 5 * 280
                with Image.open(out / preview['path']) as im:
                    thumb = ImageOps.contain(im, (274, 235))
                    sheet.paste(thumb, (x, y))
                label = row['id']
                if preview['placement'] is not None:
                    label += f"\nP{preview['placement']} {preview['source_seconds']:.2f}s"
                draw.text((x + 3, y + 238), label, fill='black')
            rel = f'contact_{offset // 20 + 1:03}.jpg'
            sheet.save(out / rel, quality=90)
            manifest['contact_sheets'].append(rel)
        if digest(project) != before:
            raise ValueError('Project changed during inventory; retry after saving')
        write_json(out / 'inventory.json', rows)
        manifest.update(status='READY', unique_media=len(rows),
            placements=sum(len(r['placements']) for r in rows),
            images=sum(r['kind'] == 'image' for r in rows),
            videos=sum(r['kind'] == 'video' for r in rows), previews=len(evidence))
    except Exception as exc:
        manifest.update(status='FAILED', error=str(exc))
        write_json(out / 'classification_input.json', manifest)
        raise
    write_json(out / 'classification_input.json', manifest)
    print(json.dumps(dict(output=str(out), **manifest), ensure_ascii=False, indent=2))
    return out


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    prepare(parser.parse_args().config)
