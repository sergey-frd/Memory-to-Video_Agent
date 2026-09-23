"""Render an offline review MP4 from a structure, without modifying Premiere."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.prepare_classification_input import digest, write_json
from tools.classify_source_package import ProgressLog
from utils.video_frame_extract import resolve_ffmpeg_executable

TICKS = 254016000000


def make_plan(structure, rows, fps):
    if structure.get('status') != 'DRAFT_REVIEW_REQUIRED':
        raise ValueError('Expected reviewed-input draft structure status')
    lookup = {r['id']: r for r in rows}
    if len(lookup) != len(rows):
        raise ValueError('Duplicate catalog IDs')
    clips, seen, cursor = [], set(), 0
    for block in structure['blocks']:
        for item in block['items']:
            mid = item['material_id']
            if mid not in lookup or mid in seen:
                raise ValueError(f'Unknown/repeated ID: {mid}')
            seen.add(mid)
            row = lookup[mid]
            duration = item['duration_seconds']
            if type(duration) is not int or duration <= 0:
                raise ValueError('Positive integer durations required')
            start = 0
            placement = None
            if row['kind'] == 'video':
                candidates = [p for p in row['placements'] if
                              p['source_out_ticks'] - p['source_in_ticks'] >= duration * TICKS]
                if not candidates:
                    raise ValueError(f'No sufficiently long source range: {mid}')
                placement = min(candidates, key=lambda p: (p['start_ticks'], p['number']))
                start = placement['source_in_ticks'] / TICKS
            elif row['kind'] != 'image':
                raise ValueError(f'Unsupported kind: {row["kind"]}')
            clips.append(dict(id=mid, path=row['path'], sha256=row['sha256'], kind=row['kind'],
                              source_in_seconds=start, source_out_seconds=start + duration if row['kind'] == 'video' else None,
                              source_placement=placement, duration_seconds=duration,
                              timeline_start_frame=cursor, frames=duration * fps, block=block['title'],
                              editorial_reason=item.get('reason', ''), timing_review='PENDING'))
            cursor += duration * fps
    if not clips:
        raise ValueError('Empty structure')
    return dict(schema_version=1, status='DRAFT', fps=fps, frames=cursor,
                duration_seconds=cursor / fps, editorial_policy=structure.get('editorial_policy', 'legacy_unreviewed'), range_policy='First chronological placement long enough; start of its used range',
                clips=clips, limitations=['No Premiere effects', 'Automatic source trims require review',
                                        'No music or titles; original video audio, silence for photos'])


def run(config_path, dry_run=False):
    cfg = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if cfg.get('schema_version') != 1:
        raise ValueError('Expected schema version 1')
    for key in ('width', 'height', 'fps'):
        if type(cfg.get(key)) is not int or cfg[key] <= 0:
            raise ValueError(f'Invalid {key}')
    if cfg['width'] % 2 or cfg['height'] % 2:
        raise ValueError('Even dimensions required')
    base = config_path.resolve().parent
    if bool(cfg.get('structure')) == bool(cfg.get('edit_plan')):
        raise ValueError('Specify exactly one of structure or edit_plan')
    structure_path = (base / (cfg.get('edit_plan') or cfg['structure'])).resolve()
    if cfg.get('edit_plan'):
        from tools.revise_edit_plan import validate_plan
        plan = json.loads(structure_path.read_text(encoding='utf-8'))
        validate_plan(plan)
        if plan['fps'] != cfg['fps']:
            raise ValueError('Plan fps must match render fps')
    else:
        structure = json.loads(structure_path.read_text(encoding='utf-8'))
        catalog = Path(structure['classification_result']).parent / 'catalog.jsonl'
        rows = [json.loads(s) for s in catalog.read_text(encoding='utf-8').splitlines() if s.strip()]
        plan = make_plan(structure, rows, cfg['fps'])
    cap = cfg.get('max_duration_seconds')
    if cap is not None:
        if type(cap) is not int or cap <= 0:
            raise ValueError('Positive integer max_duration_seconds required')
        timeline_frames = sum(c['frames'] for c in plan['clips'])
        if timeline_frames > cap * plan['fps']:
            raise ValueError(f'Render forbidden: timeline_duration={timeline_frames / plan["fps"]} > {cap}s')
    out = (base / cfg['output_root']).resolve() / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8])
    out.mkdir(parents=True)
    log = ProgressLog(out / 'progress.log')
    state = dict(status='CHECKING', output=str(out), structure_sha256=digest(structure_path))
    def status(**kwargs):
        state.update(kwargs); write_json(out / 'render_result.json', state)
    status()
    log.emit('RUN', f'{out}; clips={len(plan["clips"])}; duration={plan["duration_seconds"]}s')
    try:
        for clip in plan['clips']:
            with log.activity('VERIFY ' + clip['id']):
                if digest(clip['path']) != clip['sha256']:
                    raise ValueError('Media checksum mismatch: ' + clip['id'])
        write_json(out / 'edit_plan.json', plan)
        if dry_run:
            status(status='VALIDATED_NO_RENDER'); return state
        ffmpeg = resolve_ffmpeg_executable()
        def execute(args, label):
            from tools.ffmpeg_progress import execute as monitored
            monitored(ffmpeg, args, label, log, out / 'ffmpeg.log')
        parts = out / 'segments'; parts.mkdir()
        for index, clip in enumerate(plan['clips'], 1):
            log.emit('ITEM', f'{index}/{len(plan["clips"])} {clip["id"]} {clip["path"]}')
            status(status='RENDERING', current=index)
            audio = False
            if clip['kind'] == 'video':
                probe = subprocess.run([ffmpeg, '-hide_banner', '-i', clip['path']], capture_output=True, text=True, errors='replace', timeout=60)
                audio = bool(re.search(r'Stream #.*Audio:', probe.stderr))
            input_path = clip['path']
            if Path(input_path).suffix.lower() in ('.heic', '.heif'):
                import pillow_heif
                from PIL import Image, ImageOps
                pillow_heif.register_heif_opener()
                normalized = parts / f'{index:04}_source.png'
                with Image.open(input_path) as im:
                    ImageOps.exif_transpose(im).convert('RGB').save(normalized)
                input_path = str(normalized)
            args = ['-loop', '1', '-framerate', str(cfg['fps']), '-i', input_path] if clip['kind'] == 'image' else ['-ss', str(clip['source_in_seconds']), '-i', clip['path']]
            if not audio:
                args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            video = (f"scale={cfg['width']}:{cfg['height']}:force_original_aspect_ratio=decrease,"
                     f"pad={cfg['width']}:{cfg['height']}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={cfg['fps']},format=yuv420p")
            # GIF's final frame may have a long hold, with no later packet for
            # fps to emit. Preserve that hold through the requested OUT point.
            if Path(clip['path']).suffix.lower() == '.gif':
                video = f"tpad=stop_mode=clone:stop_duration={clip['duration_seconds']}," + video
            args += ['-map', '0:v:0', '-map', '0:a:0' if audio else '1:a:0', '-vf', video,
                     '-af', 'aresample=48000,apad', '-t', str(clip['duration_seconds']),
                     '-c:v', 'libx264', '-preset', 'veryfast', '-b:v', cfg['video_bitrate'],
                     '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', cfg['audio_bitrate'], '-ar', '48000', '-ac', '2',
                     '-map_metadata', '-1', '-movflags', '+faststart', str(parts / f'{index:04}.mp4')]
            execute(args, f'ENCODE {index}/{len(plan["clips"])}')
            log.emit('DONE', clip['id'])
        listing = out / 'concat.txt'
        listing.write_text(''.join(f"file 'segments/{i:04}.mp4'\n" for i in range(1, len(plan['clips']) + 1)), encoding='utf-8')
        target = out / 'draft_720p.mp4'
        execute(['-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy', '-movflags', '+faststart', str(target)], 'ASSEMBLE MP4')
        execute(['-v', 'error', '-xerror', '-i', str(target), '-f', 'null', '-'], 'VERIFY FULL DECODE')
        if digest(structure_path) != state['structure_sha256']:
            raise ValueError('Structure changed during render')
        status(status='DRAFT_READY', video=str(target), bytes=target.stat().st_size,
               sha256=digest(target), planned_duration_seconds=plan['duration_seconds'],
               verification='Full FFmpeg decode passed; native Adobe QA not performed')
        log.emit('FINISH', f'{target}; {target.stat().st_size / 1024**2:.1f} MiB')
        return state
    except BaseException as exc:
        status(status='FAILED', error_type=type(exc).__name__)
        log.emit('ERROR', type(exc).__name__); raise


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args(); run(args.config, args.dry_run)
