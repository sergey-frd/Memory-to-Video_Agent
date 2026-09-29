"""Restartable, descriptive media classification from a task INGEST manifest."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from PIL import Image, ImageOps, ImageDraw
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'scripts'))
from ingest import heartbeat, say, write_json, digest
from api import openai_scene as scene
from utils.image_analysis import analyze_image
from utils.video_frame_extract import choose_sample_timestamps, extract_video_frames
from dotenv import load_dotenv

VERSION='descriptive-v1'
def stamp():return datetime.now(timezone.utc).isoformat()
def key(path):return hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode()).hexdigest()
def copy_verified(source,destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    data=source.read_bytes()
    temporary=destination.with_suffix(destination.suffix+'.tmp')
    temporary.write_bytes(data);temporary.replace(destination)
    if digest(source)!=digest(destination):raise ValueError('Permanent copy hash mismatch')

def preview(path, occurrences, directory, ticks, count):
    suffix=path.suffix.lower()
    if suffix in {'.jpg','.jpeg','.png','.bmp','.tif','.tiff','.webp','.heic'}:
        frames=[(0.,path)]
        sampling='single image'
    else:
        # Sample the union of actually used source ranges, not assumed full-media duration.
        ranges=sorted(set((i.get('source_in_ticks'),i.get('source_out_ticks')) for i in occurrences
                         if i.get('source_in_ticks') is not None and i.get('source_out_ticks') is not None))
        valid=[(a/ticks,b/ticks) for a,b in ranges if b>a]
        if not valid:raise ValueError('No known source bounds for visual sampling')
        times=sorted(set(a+t for a,b in valid for t in choose_sample_timestamps(b-a,count)))
        frames=extract_video_frames(path,output_dir=directory,timestamps_sec=times,prefix='sample')
        sampling='sampled source intervals only; no audio, not continuous video analysis'
    tiles=[]; metadata=[]
    for time,path_in in frames:
        with Image.open(path_in) as image:
            image=ImageOps.exif_transpose(image).convert('RGB')
            metadata.append({'timestamp_seconds':time,'width':image.width,'height':image.height})
            image.thumbnail((768,640))
            tile=Image.new('RGB',(768,675),'#eeeeee');tile.paste(image,((768-image.width)//2,30))
            ImageDraw.Draw(tile).text((12,10),f't={time:.2f}s',fill='black');tiles.append(tile)
    sheet=Image.new('RGB',(768*min(3,len(tiles)),675*((len(tiles)+2)//3)),'white')
    for n,tile in enumerate(tiles):sheet.paste(tile,((n%3)*768,(n//3)*675))
    out=directory/'preview.jpg';out.parent.mkdir(parents=True,exist_ok=True);sheet.save(out,quality=88)
    technical=asdict(analyze_image(out))
    # Keep measured image properties only: heuristic semantic claims are not observations.
    technical={k:v for k,v in technical.items() if k in ('brightness_label','contrast_label')}
    return out,metadata,sampling,technical


def analyze_visual(client, image, model, directory, frames, sampling, technical, media_type, additional_instructions=""):
    """Shared scene/quality analysis for original media and derived ART."""
    instructions=(
        'This is descriptive inventory, not editing. Input may be a contact sheet of temporal samples from ONE media. '
        'Do not add people counts across panels; describe temporal variation. No KEEP/DROP, cuts, durations, music or story decisions. '
        'Do not identify or match real people by face, do not infer names or family relationships. '
        'Describe visible interactions and facial expressions only; do not infer internal feelings or sensitive attributes. '
        'Add fields to the JSON: quality {assessment:string,problems:[string]}, '
        'usefulness {observed_material_value:string,limitations:[string]}, '
        'uncertainty {status:KNOWN|UNCERTAIN|UNKNOWN,confidence:number|null,reason:string}. '
        'Confidence is a subjective model estimate, not calibrated. Say UNKNOWN when not observable. '
        'For multiple panels people_count means maximum visible in any single panel. '
        'Do not treat panel labels, borders or montage layout as source defects. Russian descriptive text.')
    instructions += "\n" + additional_instructions
    for attempt in range(1, 3):
        raw=scene._request_scene_analysis(client,image,model,'ru',additional_instructions=instructions)
        try:
            parsed=scene._extract_json_object(raw)
            scene_data=scene.parse_scene_analysis_response(raw).to_dict()
            if not scene_data['summary'] or not isinstance(parsed.get('quality'),dict) or not isinstance(parsed.get('usefulness'),dict):
                raise ValueError('Incomplete semantic response: summary, quality and usefulness required')
            break
        except (ValueError, TypeError) as response_error:
            # Keep the failed response for diagnosis without marking it successful.
            (directory / f'incomplete_response_{attempt}.txt').write_text(raw, encoding='utf-8')
            if attempt == 2:
                raise
            say(f'Incomplete semantic response; retry {attempt}/1: {response_error}')
    return dict(status='CLASSIFIED',scene=scene_data,quality=parsed['quality'],usefulness=parsed['usefulness'],
        uncertainty=parsed.get('uncertainty',{'status':'UNCERTAIN','confidence':None,'reason':'Model omitted confidence'}),
        identity={'category':'UNKNOWN','names':[],'confidence':None,'reason':'No explicit per-media identity annotation; no facial identification performed.'},
        people_group='group' if scene_data['people_count']>2 else 'two people' if scene_data['people_count']==2 else 'one person' if scene_data['people_count']==1 else 'no people observed',
        media_type=media_type,sample_frames=frames,sampling_scope=sampling,technical=technical,
        semantic_raw=parsed)


def validate(payload,manifest):
    if len(payload['timeline_items'])!=len(manifest['items']):raise ValueError('Timeline count mismatch')
    if [x['ingest_record'] for x in payload['timeline_items']]!=manifest['items']:raise ValueError('Timeline records changed')
    ids={m['media_id'] for m in payload['media']}
    if len(ids)!=len(payload['media']) or any(t['media_id'] not in ids for t in payload['timeline_items']):raise ValueError('Broken media links')


def run(task_id):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',task_id) or task_id.lower()=='legacy':raise ValueError('Invalid task ID')
    task=ROOT/'tasks'/task_id
    config=json.loads((task/'task.json').read_text(encoding='utf-8'))
    settings=config['classify'];manifest_path=task/'ingest/manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    expected=settings.get('expected_timeline_items')
    if (expected is not None and len(manifest['items'])!=expected) or manifest['total_items']!=len(manifest['items']):raise ValueError('Manifest count mismatch; CLASSIFY NOT STARTED')
    permanent_root=Path(settings['permanent_project_dir']).resolve()
    if not permanent_root.is_dir() or permanent_root.is_relative_to(ROOT.resolve()):raise ValueError('Permanent folder unresolved or inside repository')
    permanent=(permanent_root/settings['permanent_relative_dir']).resolve()
    if not permanent.is_relative_to(permanent_root):raise ValueError('Permanent destination escapes project folder')
    work=task/'classify';work.mkdir(exist_ok=True)
    permanent.mkdir(parents=True,exist_ok=True)
    # Persist recovery inputs before any paid work; these writes also test storage permissions.
    copy_verified(manifest_path,permanent/'ingest_manifest.json')
    copy_verified(task/'task.json',permanent/'task_metadata.json')
    copy_verified(task/'request.md',permanent/'request.md')
    groups={};timeline=[]
    for item in manifest['items']:
        path=item.get('source_path')
        media_id=key(path) if path else hashlib.sha256(('unresolved:'+str(item['item_id'])).encode()).hexdigest()
        groups.setdefault(media_id,{'path':path,'items':[]})['items'].append(item)
        timeline.append({'media_id':media_id,'ingest_record':item})
    say(f'{task_id} — CLASSIFY\nTimeline items: {len(timeline)}\nUnique media: {len(groups)}')
    load_dotenv(ROOT/'.env')
    client=scene._get_client().with_options(timeout=120,max_retries=1)
    records=[]
    for index,(media_id,group) in enumerate(groups.items(),1):
        path=Path(group['path']) if group['path'] else None
        checkpoint=work/'checkpoints'/f'{media_id}.json'
        remote=permanent/'checkpoints'/checkpoint.name
        say(f'[{index}/{len(groups)}] {path.name if path else "UNRESOLVED"}')
        fingerprint=None
        if path and path.is_file():
            stat=path.stat();fingerprint={'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}
        sample_bounds=sorted(set((i.get('source_in_ticks'),i.get('source_out_ticks')) for i in group['items']),key=str)
        signature=hashlib.sha256(json.dumps([VERSION,settings['model'],fingerprint,sample_bounds],sort_keys=True).encode()).hexdigest()
        cached=None
        for candidate in (checkpoint,remote):
            if candidate.exists():
                value=json.loads(candidate.read_text(encoding='utf-8'))
                if value.get('signature')==signature and value.get('status')=='CLASSIFIED':cached=value;break
        if cached:
            record=cached;write_json(checkpoint,record);copy_verified(checkpoint,remote);say('SKIP ALREADY CLASSIFIED')
        else:
            record={'media_id':media_id,'source_path':group['path'],'signature':signature,'fingerprint':fingerprint,
                    'model':settings['model'],'version':VERSION,'timestamp':stamp(),'status':'FAILED'}
            try:
                if path is None or not path.is_file():raise FileNotFoundError('Missing source media')
                with heartbeat(f'{index}/{len(groups)} visual analysis'):
                    directory=work/'previews'/media_id
                    image,frames,sampling,technical=preview(path,group['items'],directory,manifest['ticks_per_second'],settings['frames_per_video'])
                    record.update(analyze_visual(client,image,settings['model'],directory,frames,sampling,technical,group['items'][0]['media_type']))
                    if path.stat().st_size!=fingerprint['size'] or path.stat().st_mtime_ns!=fingerprint['mtime_ns']:raise ValueError('Source media changed during analysis')
                write_json(checkpoint,record);copy_verified(checkpoint,remote)
                say('CLASSIFIED — checkpoint and permanent copy verified')
            except Exception as exc:
                record.update(status='FAILED',error=f'{type(exc).__name__}: {exc}')
                write_json(checkpoint,record);copy_verified(checkpoint,remote);say('FAILED: '+record['error'])
                if os.environ.get('FAMILY_PIPELINE_AUTO')=='1':raise
        records.append(record)
    payload={'schema_version':1,'task_id':task_id,'timestamp':stamp(),'manifest_sha256':digest(manifest_path),
             'classification_version':VERSION,'media':records,'timeline_items':timeline,
             'identity_policy':'No face identification. Identities require explicit per-media metadata.',
             'confidence_policy':'Model self estimates are uncalibrated; null means unknown.',
             'scope':'Descriptive only; videos sampled at recorded timestamps, audio not analysed.'}
    validate(payload,manifest)
    output=work/'classification.json';write_json(output,payload)
    readback=json.loads(output.read_text(encoding='utf-8'));validate(readback,manifest)
    if readback!=payload:raise ValueError('Classification readback mismatch')
    say('Creating permanent copy...');copy_verified(output,permanent/output.name)
    preserved=json.loads((permanent/output.name).read_text(encoding='utf-8'));validate(preserved,manifest)
    failed=sum(r['status']!='CLASSIFIED' for r in records)
    status={'task_id':task_id,'stage':'CLASSIFY','status':'COMPLETE' if failed==0 else 'FAILED','timestamp':stamp(),
            'timeline_items':len(timeline),'unique_media':len(records),'classified_media':len(records)-failed,
            'uncertain':sum(r.get('identity',{}).get('category')=='UNKNOWN' or r.get('uncertainty',{}).get('status') in ('UNKNOWN','UNCERTAIN') for r in records),
            'semantic_uncertain':sum(r.get('uncertainty',{}).get('status') in ('UNKNOWN','UNCERTAIN') for r in records),
            'failed':failed,'working_classification_path':str(output),'permanent_classification_path':str(permanent/output.name),
            'working_sha256':digest(output),'permanent_sha256':digest(permanent/output.name),'permanent_copy_verified':True,
            'premiere_project_modified':False,'source_media_modified':False,'execution_state':'STOP','next_stage':'NOT STARTED'}
    report=work/'CLASSIFY_STATUS.md'
    report.write_text('# CLASSIFY STATUS\n\n```json\n'+json.dumps(status,ensure_ascii=False,indent=2)+'\n```\n\nIdentity UNKNOWN is included in uncertain. Video findings describe sampled frames, not all frames or audio.\n\nSTOP\n',encoding='utf-8')
    copy_verified(report,permanent/report.name)
    state_path=task/'state.json';state=json.loads(state_path.read_text(encoding='utf-8'))
    state.setdefault('stages',{})['CLASSIFY']=status['status'];state['classify']=status
    state.setdefault('history',[]).append(status);state.update(stage='CLASSIFY',status='CLASSIFY '+status['status'],execution_state='STOP',next_stage='NOT STARTED')
    write_json(state_path,state)
    copy_verified(state_path,permanent/'state_snapshot.json')
    say('CLASSIFY '+status['status']+'\nSTOP')
    return 0 if not failed else 1

if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('task_id');args=parser.parse_args()
    try:raise SystemExit(run(args.task_id))
    except Exception as exc:
        say(f'CLASSIFY FAILED: {type(exc).__name__}: {exc}\nSTOP');raise SystemExit(1)
