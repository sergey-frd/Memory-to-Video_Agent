"""Read-only project audit. Does not save or mutate any Premiere project."""
from __future__ import annotations
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from utils.premiere_project import (load_premiere_project_root, build_project_object_id_lookup,
    build_project_object_uid_lookup, find_project_sequence_node, list_named_project_sequence_names,
    PREMIERE_TICKS_PER_SECOND)
from utils.premiere_sequence_motion import _track_item_contexts, _video_settings, _motion_params

SOURCE = REPO / 'input/SF_26_Bd_Art_3_TASK031.prproj'
NAME = 'SF_26_Bd_Art_5'
OUT = REPO / 'TASK_032_ART_STRONG_MOTION_EXTREME_COLOR_UNIFIED_BACKGROUND'

from utils.premiere_art_runtime import configure_module
configure_module(globals(), "032")

def write(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def main():
    root = load_premiere_project_root(SOURCE)
    ids = build_project_object_id_lookup(root)
    uids = build_project_object_uid_lookup(root)
    names = list_named_project_sequence_names(root)
    seq = find_project_sequence_node(root, NAME)
    if seq is None:
        raise RuntimeError(f'Нет sequence {NAME}; найдено {names}')
    settings = _video_settings(seq, ids)
    frame_ticks = int(settings['frame_rate'])
    missing = []
    for n in root.iter():
        for key, lookup in [('ObjectRef', ids), ('ObjectURef', uids)]:
            if key in n.attrib and n.attrib[key] not in lookup:
                missing.append({'tag': n.tag, 'kind': key, 'value': n.attrib[key]})
    timeline, media = {}, {}
    pending = [NAME]
    while pending:
        name = pending.pop(0)
        if name in timeline:
            continue
        node = find_project_sequence_node(root, name)
        rows = []
        for group in [0, 1]:
            for i in _track_item_contexts(node, group_index=group, id_lookup=ids, uid_lookup=uids, project_path=SOURCE):
                row = {'identity': i.track_item_node.attrib, 'group': 'video' if group==0 else 'audio',
                    'track': i.track_index, 'name': i.name, 'in': i.start/frame_ticks, 'out': i.end/frame_ticks,
                    'source_in': i.source_in/frame_ticks, 'source_out': i.source_out/frame_ticks,
                    'path': i.source_path, 'effects': []}
                seen, stack = set(), [i.track_item_node]
                while stack:
                    n = stack.pop()
                    if id(n) in seen:
                        continue
                    seen.add(id(n))
                    if n.tag.endswith('Component') or n.tag.endswith('ComponentParam'):
                        row['effects'].append(ET.tostring(n, encoding='unicode'))
                    for child in n:
                        stack.append(child)
                        ref = child.get('ObjectRef')
                        if ref and ref in ids and child.tag not in ['Clip', 'SubClip', 'Source', 'MasterClip']:
                            stack.append(ids[ref])
                if i.source_path:
                    p = Path(i.source_path)
                    if str(p) not in media:
                        info = {'path': str(p), 'online': p.is_file(), 'suffix': p.suffix.lower()}
                        if info['online']:
                            info['size'] = p.stat().st_size
                            if p.suffix.lower() in ['.jpg','.jpeg','.png','.tif','.tiff','.webp','.bmp']:
                                from PIL import Image
                                with Image.open(p) as im:
                                    info['dimensions'] = list(im.size)
                        media[str(p)] = info
                elif i.name in names:
                    row['nested_sequence'] = i.name
                    pending.append(i.name)
                else:
                    row['unresolved_source'] = True
                rows.append(row)
        timeline[name] = rows
    outputs = [SOURCE.parent / s for s in ['SF_26_Bd_Art_3_TASK031_before_TASK_032.prproj',
        'SF_26_Bd_Art_4_TASK032_EXTREME_FINAL.prproj','SF_26_Bd_Art_4_TASK032_EDIT_CHECKPOINT.prproj',
        'SF_26_Bd_Art_6_TASK032_EXTREME_FINAL_640_360.mp4']]
    preflight = {'source_project': str(SOURCE), 'source_sequence': NAME,
        'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(), 'source_bytes': SOURCE.stat().st_size,
        'settings': settings, 'fps': PREMIERE_TICKS_PER_SECOND/frame_ticks,
        'duration_frames': max(r['out'] for r in timeline[NAME]),
        'source_sequence_xml_sha256': hashlib.sha256(ET.tostring(seq)).hexdigest(),
        'sequences': names, 'nested_sequences': [n for n in timeline if n != NAME],
        'missing_references': missing, 'offline_media': [r for r in media.values() if not r['online']],
        'output_conflicts': [str(p) for p in outputs if p.exists()],
        'source_unresolved': [r for rows in timeline.values() for r in rows if r.get('unresolved_source')],
        'desktop_check': 'Ещё не выполнен', 'status': 'ПРЕДВАРИТЕЛЬНАЯ ПРОВЕРКА'}
    write('TASK_032_PREFLIGHT.json', preflight)
    write('TASK_032_TIMELINE_MANIFEST.json', timeline)
    write('TASK_032_MEDIA_MANIFEST.json', list(media.values()))
    print(json.dumps(preflight, ensure_ascii=False, indent=2))
    print('TIMELINE')
    for r in timeline[NAME]:
        print(json.dumps({k:v for k,v in r.items() if k!='effects'}, ensure_ascii=False))

if __name__ == '__main__':
    from main_premiere_art_task import main as launch
    launch(['--task', '032'] + sys.argv[1:])
