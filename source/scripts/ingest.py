"""Read-only task sequence inventory, using the existing Premiere XML reader."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
import threading
import tempfile
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import premiere_project as pp


def say(message):
    print(message, flush=True)


@contextmanager
def heartbeat(label):
    stop = threading.Event()
    def report():
        started = time.monotonic()
        while not stop.wait(3):
            say(f'{label} ... {int(time.monotonic()-started)}s')
    worker = threading.Thread(target=report, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each writer owns its temporary file; readers only see complete JSON.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                prefix=path.name+'.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(20):
            try:
                temporary.replace(path)
                break
            except PermissionError as exc:
                # Windows readers may briefly hold a handle without delete sharing.
                if os.name != "nt" or getattr(exc, "winerror", None) not in (5,32,33) or attempt == 19:raise
                time.sleep(.01*(attempt+1))
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def number(node, field):
    value = node.findtext(field) if node is not None else None
    return int(value) if value is not None and value.strip() else None


def inventory(sequence, ids, uids, project, limit=None, context=None):
    """One item extraction pass; do not filter or deduplicate missing/nonvisual clips."""
    records = []
    for group in pp.get_project_track_group_indexes(sequence):
        for track_index, track in pp.get_project_track_nodes(sequence, track_group_index=group,
                object_id_lookup=ids, object_uid_lookup=uids):
            for position, reference in enumerate(pp.iter_project_track_item_refs(track), 1):
                if limit is not None and len(records) >= limit:
                    return records
                ref = reference.get('ObjectRef')
                if context is not None:
                    context['item'] = ref
                item = ids.get(ref)
                clip = pp.resolve_project_track_item_clip(item, ids) if item is not None else None
                media = pp.resolve_project_clip_media_node(clip, ids, uids) if clip is not None else None
                raw_paths = {tag: media.findtext('./'+tag) for tag in
                    ('ActualMediaFilePath', 'FilePath', 'RelativePath') if media is not None and media.findtext('./'+tag)}
                path = pp.resolve_project_track_item_source_path(item, ids, uids, project_path=project) if item is not None else ''
                start = number(item, './ClipTrackItem/TrackItem/Start')
                end = number(item, './ClipTrackItem/TrackItem/End')
                name = pp.resolve_project_track_item_name(item, ids) if item is not None else ''
                kind = ('image' if pp.is_supported_image_media_path(path) or Path(path).suffix.lower()=='.gif' else
                        'video' if pp.is_supported_video_media_path(path) else
                        'audio' if Path(path).suffix.lower() in {'.wav','.mp3','.aac','.m4a','.flac','.aif','.aiff','.ogg'} else 'UNRESOLVED')
                record = dict(order=len(records)+1, track_group_index=group, track_index=track_index,
                    track_type=track.tag, clip_position=position, clip_name=name or None,
                    item_type=item.tag if item is not None else None, media_type=kind,
                    source_path=path or None, raw_source_paths=raw_paths,
                    media_status=('PRESENT' if Path(path).is_file() else 'MISSING') if path else 'UNRESOLVED',
                    timeline_start_ticks=start, timeline_end_ticks=end,
                    duration_ticks=end-start if start is not None and end is not None else None,
                    source_in_ticks=number(clip,'./Clip/InPoint'), source_out_ticks=number(clip,'./Clip/OutPoint'),
                    item_id=ref, clip_id=clip.get('ObjectID') if clip is not None else None,
                    media_uid=media.get('ObjectUID') if media is not None else None,
                    offline_metadata={n.tag:n.text for n in media.iter() if 'offline' in n.tag.lower()} if media is not None else {},
                    unresolved_reference=item is None)
                records.append(record)
                say(f'[{len(records)}] {name or ref or "UNRESOLVED"}')
    records.sort(key=lambda r:(r['timeline_start_ticks'] is None, r['timeline_start_ticks'] or 0,
                              r['track_group_index'],r['track_index'],r['clip_position']))
    for index, record in enumerate(records,1):
        record['order']=index
    return records


def save_state(task, result, check):
    path=task/'state.json'
    state=json.loads(path.read_text(encoding='utf-8'))
    state.setdefault('init_snapshot', {k:v for k,v in state.items() if k not in ('history','init_snapshot')})
    state.setdefault('stages', {})['INIT']='COMPLETE'
    state['stages']['PREPARE_INGEST' if check else 'INGEST']=result['status']
    state.setdefault('history', []).append(result)
    state['stage']='PREPARE INGEST' if check else 'INGEST'
    state['status']=result['status']
    state['execution_state']='STOP'
    state['next_stage']='USER MANUALLY RUNS INGEST' if check else 'UNRESOLVED'
    state['ingest_check' if check else 'ingest']=result
    write_json(path,state)
    report=task/('PREPARE_INGEST_STATUS.md' if check else 'INGEST_STATUS.md')
    report.write_text('# '+state['stage']+' STATUS\n\n```json\n'+json.dumps(result,ensure_ascii=False,indent=2)+'\n```\n\nSTOP\n',encoding='utf-8')


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_id')
    parser.add_argument('--check',action='store_true',help='Read at most 3 items; never write canonical manifest or mark INGEST complete')
    args=parser.parse_args()
    task=None
    context={'step':'TASK','item':None}
    result={'timestamp':datetime.now(timezone.utc).isoformat(),'task_id':args.task_id,'mode':'CHECK' if args.check else 'FULL',
            'status':'FAILED','warnings':[],'errors':[],'unresolved':[],'total_items':0,'manifest_path':None}
    try:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',args.task_id) or args.task_id.lower()=='legacy':
            raise ValueError('Invalid task ID')
        candidate=ROOT/'tasks'/args.task_id
        if candidate.resolve()!=candidate.absolute() or not (candidate/'task.json').is_file():
            raise ValueError('Task directory missing or redirected')
        task=candidate
        config=json.loads((task/'task.json').read_text(encoding='utf-8'))
        if config['task_id']!=args.task_id:raise ValueError('Task ID mismatch')
        project=Path(config['paths']['premiere_project'])
        sequence_name=config['sequences']['source']['name']
        result.update(source_project=str(project),source_sequence=sequence_name)
        say(f'{args.task_id} — INGEST'+(' CHECK (max 3 items)' if args.check else ''))
        context['step']='OPEN PROJECT'
        say('Opening saved Premiere project (read-only)...')
        with heartbeat('Reading project'):
            before=digest(project)
            root=pp.load_premiere_project_root(project)
        say('Project OK')
        context['step']='FIND SEQUENCE'
        say('Finding sequence...')
        with heartbeat('Finding sequence and indexing objects'):
            sequence=pp.find_project_sequence_node(root,sequence_name)
            if sequence is None:raise ValueError(f'Sequence not found: {sequence_name}')
            matches=[n for n in root.iter('Sequence') if (n.findtext('./Name') or '').strip().casefold()==sequence_name.casefold()]
            if len(matches)!=1:raise ValueError('Ambiguous sequence name')
            ids=pp.build_project_object_id_lookup(root)
            uids=pp.build_project_object_uid_lookup(root)
        say(f'Sequence: {sequence_name}\nSequence OK')
        context['step']='EXTRACT ITEMS'
        with heartbeat('Extracting items'):
            items=inventory(sequence,ids,uids,project,3 if args.check else None,context)
        if not items:raise ValueError('Sequence has no extractable track items')
        result['total_items']=len(items)
        result['count_scope']='sample only' if args.check else 'complete inventory'
        result['warnings']=[{'order':i['order'],'media_status':i['media_status'],'source_path':i['source_path']} for i in items if i['media_status']!='PRESENT' or i['offline_metadata']]
        result['unresolved']=[{'order':i['order'],'field':k} for i in items for k in ('source_path','timeline_start_ticks','timeline_end_ticks','source_in_ticks','source_out_ticks') if i[k] is None]
        context['step']='VERIFY PROJECT'
        with heartbeat('Verifying project unchanged'):
            if digest(project)!=before:raise ValueError('Project changed during reading; retry after saving in Premiere')
        result['project_sha256']=before
        result['project_unchanged']=True
        if not args.check:
            context['step']='WRITE AND VALIDATE MANIFEST'
            destination=task/'ingest'/'manifest.json'
            if destination.parent.resolve()!=destination.parent.absolute():raise ValueError('Redirected ingest output directory')
            payload=dict(schema_version=1,task_id=args.task_id,source_project=str(project),source_sequence=sequence_name,
                project_sha256=before,timestamp=result['timestamp'],ticks_per_second=pp.PREMIERE_TICKS_PER_SECOND,
                ordering='timeline start, track group, track index, clip position; missing starts last',
                scope='Direct ClipTrack/ClipItems/TrackItems references; nested sequences not expanded; transitions and effects not inventoried',
                total_items=len(items),items=items,warnings=result['warnings'])
            say('Writing manifest...')
            write_json(destination,payload)
            say('Validating manifest...')
            loaded=json.loads(destination.read_text(encoding='utf-8'))
            if loaded!=payload or len(loaded['items'])!=len(items):raise ValueError('Manifest readback mismatch')
            result['manifest_path']=str(destination)
        result['status']='INGEST READY FOR USER RUN' if args.check else 'COMPLETE'
        context['step']='SAVE STATE'
        save_state(task,result,args.check)
        say(result['status'] if args.check else 'INGEST COMPLETE')
        say('STOP')
        return 0
    except (Exception,KeyboardInterrupt) as exc:
        result['status']='FAILED'
        result['errors'].append(dict(step=context['step'],item=context['item'],error=f'{type(exc).__name__}: {exc}'))
        say(f"INGEST FAILED\nTASK: {args.task_id}\nSTEP: {context['step']}\nITEM: {context['item']}\nERROR: {exc}")
        if task is not None:
            try:save_state(task,result,args.check)
            except Exception as state_error:say(f'Cannot save failure state: {state_error}')
        say('STOP')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
