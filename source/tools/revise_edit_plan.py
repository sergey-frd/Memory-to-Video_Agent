"""Apply explicit source trims to an existing plan; never select media with AI."""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.prepare_classification_input import digest, write_json


def validate_plan(plan):
    fps = plan['fps']
    if type(fps) is not int or fps <= 0 or not plan['clips']:
        raise ValueError('Invalid fps or empty plan')
    cursor = 0
    for c in plan['clips']:
        n = c['frames']
        if type(n) is not int or n <= 0 or c['timeline_start_frame'] != cursor:
            raise ValueError('Invalid frames or non-contiguous timeline')
        if not math.isclose(c['duration_seconds'], n / fps, abs_tol=1e-8):
            raise ValueError('Duration/frame mismatch')
        if c['kind'] == 'video':
            start, end = c['source_in_seconds'], c['source_out_seconds']
            p = c['source_placement']
            if not (math.isfinite(start) and math.isfinite(end) and start >= 0
                    and math.isclose(end-start, n/fps, abs_tol=1e-8)
                    and start >= p['source_in_ticks']/254016000000-1e-8
                    and end <= p['source_out_ticks']/254016000000+1e-8):
                raise ValueError('Invalid source range: ' + c['id'])
        elif c['kind'] != 'image' or c['source_in_seconds'] != 0 or c['source_out_seconds'] is not None:
            raise ValueError('Invalid image/kind')
        cursor += n
    if plan['frames'] != cursor or not math.isclose(plan['duration_seconds'], cursor/fps, abs_tol=1e-8):
        raise ValueError('Invalid plan totals')


def revise(source, cfg):
    validate_plan(source)
    if 'timeline_ranges' in cfg:
        if 'clips' in cfg:
            raise ValueError('Use clips OR timeline_ranges')
        return revise_timeline(source, cfg)
    lookup = {c['id']: c for c in source['clips']}
    edits = cfg['clips']
    if len(lookup) != len(source['clips']) or len(edits) != len(lookup) or {e['id'] for e in edits} != set(lookup):
        raise ValueError('Specify every source ID exactly once, including explicit drops')
    plan = deepcopy(source)
    plan['clips'] = []
    cursor = 0
    for e in edits:
        if e.get('drop', False):
            continue
        c = deepcopy(lookup[e['id']])
        if c['kind'] == 'video':
            c['source_in_seconds'] = e['source_in_seconds']
            c['source_out_seconds'] = e['source_out_seconds']
            duration = c['source_out_seconds'] - c['source_in_seconds']
        else:
            duration = e['duration_seconds']
        frames = duration * plan['fps']
        if not math.isfinite(frames) or not math.isclose(frames, round(frames), abs_tol=1e-7):
            raise ValueError('Duration must align to frames')
        c.update(frames=round(frames), duration_seconds=round(frames)/plan['fps'],
                 timeline_start_frame=cursor, editorial_note=e.get('note', ''))
        plan['clips'].append(c)
        cursor += c['frames']
    plan.update(frames=cursor, duration_seconds=cursor/plan['fps'], revision=cfg.get('revision', 'v2'),
                status='DRAFT', range_policy='Explicit revision configuration; IN inclusive, OUT exclusive',
                human_review='PENDING')
    validate_plan(plan)
    return plan


def revise_timeline(source, cfg):
    """Keep explicit intervals of the input timeline, mapped back to originals."""
    fps = source['fps']
    plan = deepcopy(source)
    plan['clips'] = []
    cursor, previous = 0, 0
    for interval in cfg['timeline_ranges']:
        bounds = [interval['in_seconds'] * fps, interval['out_seconds'] * fps]
        if any(not math.isfinite(v) or not math.isclose(v, round(v), abs_tol=1e-7) for v in bounds):
            raise ValueError('Timeline ranges must align to frames')
        start, end = map(round, bounds)
        if not (previous <= start < end <= source['frames']):
            raise ValueError('Ranges must be ordered, non-overlapping and within input')
        previous = end
        for old in source['clips']:
            origin = old['timeline_start_frame']
            a, b = max(start, origin), min(end, origin + old['frames'])
            if a >= b:
                continue
            c = deepcopy(old)
            if c['kind'] == 'video':
                c['source_in_seconds'] = old['source_in_seconds'] + (a-origin)/fps
                c['source_out_seconds'] = c['source_in_seconds'] + (b-a)/fps
            c.update(frames=b-a, duration_seconds=(b-a)/fps, timeline_start_frame=cursor,
                     parent_timeline_in_seconds=a/fps, parent_timeline_out_seconds=b/fps,
                     clip_instance_id=f"{c['id']}_{len(plan['clips'])+1:03}",
                     editorial_note=interval.get('note', ''))
            plan['clips'].append(c)
            cursor += b-a
    plan.update(frames=cursor, duration_seconds=cursor/fps, revision=cfg.get('revision', 'v3'),
                status='DRAFT', human_review='PENDING',
                range_policy='Explicit input timeline intervals mapped to original source media')
    validate_plan(plan)
    return plan


def run(path):
    cfg = json.loads(path.read_text(encoding='utf-8-sig'))
    if cfg.get('schema_version') != 1:
        raise ValueError('Expected schema_version 1')
    source = (path.resolve().parent / cfg['source_edit_plan']).resolve()
    out = (path.resolve().parent / cfg['output_dir']).resolve()
    plan = revise(json.loads(source.read_text(encoding='utf-8')), cfg)
    plan.update(source_edit_plan=str(source), source_plan_sha256=digest(source), revision_config_sha256=digest(path))
    # Fixed, reviewable output path: refuse to replace an earlier revision.
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / 'edit_plan.json', plan)
    rows = ['# Edit plan '+plan['revision'], '', '| File | IN (s) | OUT (s) | Duration (s) |', '|---|---:|---:|---:|']
    for c in plan['clips']:
        rows.append(f"| {Path(c['path']).name} | {c['source_in_seconds']} | {c['source_out_seconds']} | {c['duration_seconds']} |")
    (out / 'review.md').write_text('\n'.join(rows), encoding='utf-8')
    print(f"REVISED_REVIEW_REQUIRED: {out / 'edit_plan.json'}; {plan['duration_seconds']}s")


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    run(p.parse_args().config)
