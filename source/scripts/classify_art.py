"""Classify only completed ART and publish a versioned, unified media bank."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT))
import classify as common
from art import valid_image
from ingest import write_json,digest,say,heartbeat

VERSION='classify-art-v1'
def token(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def snapshot(source,target):
    if target.exists():
        if digest(source)!=digest(target):raise ValueError(f'Immutable snapshot conflict: {target}')
    else:common.copy_verified(source,target)

def inputs(task):
    config=read(task/'task.json');settings=config['classify_art']
    native=Path(config['classify']['permanent_project_dir']).resolve()
    if not native.is_dir() or native.is_relative_to(ROOT.resolve()):raise ValueError('PERMANENT TASK DIRECTORY UNRESOLVED')
    base=(native/config['classify']['permanent_relative_dir']).resolve()
    art_root=(native/read(task/'art_config.json')['permanent_relative_dir']).resolve()
    destination=(base/settings['permanent_relative_dir']).resolve()
    if not all(p.is_relative_to(native) for p in (base,art_root,destination)):raise ValueError('Unsafe permanent path')
    original_path=base/'classification.json';manifest_path=art_root/'art_manifest.json'
    original=read(original_path);manifest=read(manifest_path)
    if original['task_id']!=config['task_id'] or manifest['task_id']!=config['task_id']:raise ValueError('Task identity mismatch')
    if any(m['status']!='CLASSIFIED' for m in original['media']):raise ValueError('Original CLASSIFY incomplete')
    if manifest['classification_sha256']!=digest(original_path):raise ValueError('ART parent classification hash mismatch')
    parents={m['media_id']:m for m in original['media']}
    if len(parents)!=len(original['media']):raise ValueError('Original media IDs not unique')
    valid=[];invalid=[];seen=set()
    for entry in manifest['entries']:
        ident=entry['art_id']
        if ident in seen:raise ValueError('Duplicate ART ID in manifest')
        seen.add(ident)
        path=Path(entry['permanent_art_result_path']).resolve()
        errors=[]
        if entry['status']!='COMPLETE':errors.append('ART not COMPLETE')
        if entry['art_type'] not in ('watercolor','double_exposure'):errors.append('Unknown ART type')
        if not path.is_relative_to(art_root):errors.append('ART path outside permanent ART folder')
        if not valid_image(path):errors.append('Missing/invalid PNG')
        elif digest(path)!=entry.get('result_sha256'):errors.append('Result checksum mismatch')
        parent=parents.get(entry['source_media_id'])
        if parent is None:errors.append('Missing parent')
        elif parent['source_path']!=entry['original_source_path']:errors.append('Parent source path mismatch')
        if errors:invalid.append({'art_id':ident,'errors':errors})
        else:valid.append(entry)
    counts={k:sum(e['art_type']==k for e in valid) for k in ('watercolor','double_exposure')}
    return dict(config=config,settings=settings,native=native,destination=destination,original_path=original_path,
                manifest_path=manifest_path,original=original,manifest=manifest,valid=valid,invalid=invalid,counts=counts)

def validate_merged(merged,original,entries):
    count=len(original['media'])
    if merged['media'][:count]!=original['media']:raise ValueError('Original object changed/lost')
    if merged['timeline_items']!=original['timeline_items']:raise ValueError('Timeline changed')
    media=merged['media'];ids=[m['media_id'] for m in media]
    if len(ids)!=len(set(ids)) or len(media)!=count+len(entries):raise ValueError('Merged count/ID mismatch')
    parents={m['media_id']:m for m in original['media']};arts=media[count:]
    if {a['art_id'] for a in arts}!={e['art_id'] for e in entries}:raise ValueError('ART coverage mismatch')
    for a in arts:
        p=parents[a['parent_source_media_id']]
        if a['parent_original_path']!=p['source_path'] or a['source_family_id']!=p['media_id']:raise ValueError('Broken ART parent link')
        family=merged['source_families'][p['media_id']]
        if a['media_id'] not in family or p['media_id'] not in family:raise ValueError('Broken source family')
        if a['status']!='CLASSIFIED' or a['media_origin']!='generated_art':raise ValueError('Invalid classified ART')

def merge(original,records,version):
    result=copy.deepcopy(original)
    result.update(timestamp=common.stamp(),bank_version=version,classification_scope='ORIGINAL + ART',classification_update_version=VERSION)
    result['media']=result['media']+records
    families={m['media_id']:[m['media_id']] for m in original['media']}
    for r in records:families[r['parent_source_media_id']].append(r['media_id'])
    result['source_families']=families
    result['candidate_policy']='Same-source derivatives are separate candidates, not independent events; no forced inclusion, adjacency, or DROP.'
    return result

def save_status(task,data,remote=None):
    work=task/'classify_art';work.mkdir(exist_ok=True)
    write_json(work/'status.json',data)
    (work/'CLASSIFY_ART_STATUS.md').write_text('# ART → CLASSIFY UPDATE\n\n```json\n'+json.dumps(data,ensure_ascii=False,indent=2)+'\n```\n\nSTOP\n',encoding='utf-8')
    if remote:
        common.copy_verified(work/'status.json',remote/'status.json')
        common.copy_verified(work/'CLASSIFY_ART_STATUS.md',remote/'CLASSIFY_ART_STATUS.md')
    state=read(task/'state.json');state.setdefault('history',[]).append(data)
    state.setdefault('stages',{})['PREPARE_CLASSIFY_ART' if data['mode']=='PREPARE' else 'CLASSIFY_ART']=data['status']
    state['classify_art']=data;state.update(stage='ART → CLASSIFY UPDATE',status=data['status'],execution_state='STOP',next_stage='NOT STARTED')
    write_json(task/'state.json',state)


def run(task_id,prepare=False,dry_run=False):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',task_id) or task_id.lower()=='legacy':raise ValueError('Invalid TASK ID')
    task=ROOT/'tasks'/task_id
    if task.resolve()!=task.absolute():raise ValueError('Redirected task folder')
    i=inputs(task);original=i['original'];entries=i['valid'];settings=i['settings']
    say(f'{task_id} — ART CLASSIFY UPDATE\nOriginal classified objects: {len(original["media"])}')
    for kind,n in i['counts'].items():say(f'{kind} found VALID: {n}; expected: {i["manifest"]["expected"].get(kind)}')
    for bad in i['invalid']:say('INVALID ART: '+json.dumps(bad))
    version=token([VERSION,digest(i['original_path']),digest(i['manifest_path']),settings['model']])
    target=i['destination']/'versions'/version
    status=dict(task_id=task_id,timestamp=common.stamp(),mode='PREPARE' if prepare or dry_run else 'RUN',status='ART CLASSIFY UPDATE READY FOR USER RUN',original_classified=len(original['media']),art_found=i['counts'],invalid_art=i['invalid'],expected_art=i['manifest']['expected'],permanent_directory=str(i['native']),version_path=str(target),full_art_classify_update='NOT STARTED',execution_state='STOP')
    if dry_run:
        say('DRY RUN OK. No AI, permanent writes, merge or state changes. STOP');return 0
    if prepare:
        save_status(task,status);say(status['status']+'\nFULL ART CLASSIFY UPDATE: NOT STARTED\nSTOP');return 0
    status.update(full_art_classify_update='STARTED',status='INCOMPLETE')
    work=task/'classify_art';work.mkdir(exist_ok=True);target.mkdir(parents=True,exist_ok=True)
    snapshot(i['original_path'],target/'classification_before_art.json')
    snapshot(i['manifest_path'],target/'art_manifest.json')
    snapshot(task/'task.json',target/'task_metadata.json')
    original_hash=digest(i['original_path']);manifest_hash=digest(i['manifest_path'])
    common.load_dotenv(ROOT/'.env')
    client=None;records=[];failed=[]
    parents={m['media_id']:m for m in original['media']}
    say('Classifying NEW ART only...')
    for n,e in enumerate(entries,1):
        say(f'[{n}/{len(entries)}] {e["art_id"]} — {Path(e["permanent_art_result_path"]).name}')
        parent=parents[e['source_media_id']];source=Path(e['permanent_art_result_path'])
        media_id='art:'+token([task_id,e['art_id'],e['result_sha256']])
        signature=token([VERSION,media_id,e['result_sha256'],parent,settings['model']])
        checkpoint=target/'checkpoints'/f'{e["art_id"]}.json';record=None
        if checkpoint.exists():
            try:
                cached=read(checkpoint)
                if (cached.get('signature')==signature and cached.get('status')=='CLASSIFIED'
                    and cached.get('media_id')==media_id and cached.get('parent_source_media_id')==parent['media_id']
                    and cached.get('source_sha256')==e['result_sha256']
                    and cached.get('scene',{}).get('summary') and isinstance(cached.get('quality'),dict)
                    and isinstance(cached.get('usefulness'),dict)):record=cached
            except (ValueError,OSError):pass
        if record:say('SKIP ALREADY CLASSIFIED VALID ART')
        else:
            try:
                if client is None:client=common.scene._get_client().with_options(timeout=120,max_retries=1)
                directory=work/'previews'/media_id.replace(':','_')
                with heartbeat(e['art_id']+' analyzing ART'):
                    image,frames,sampling,technical=common.preview(source,[],directory,1,1)
                    extra=('This is an independently generated '+e['art_type']+' image. Assess this image itself, not its source photo. '
                           'Describe composition and artistic features. White paper and deliberate transparency are not defects. '
                           'Add artistic_features (string), composition (string), visual_strength (string), '
                           'possible_dramatic_function (string, descriptive potential only, no editing instructions). '
                           'Do not identify people or assert family relations; parent identities may be UNKNOWN. '
                           'No narrative assembly, no KEEP/DROP, no required source/derivative adjacency.')
                    record=common.analyze_visual(client,image,settings['model'],directory,frames,sampling,technical,'image',extra)
                    if digest(source)!=e['result_sha256']:raise ValueError('ART file changed during analysis')
                record.update(media_id=media_id,signature=signature,source_path=str(source),art_path=str(source),timestamp=common.stamp(),
                    media_origin='generated_art',media_kind='image',art_type=e['art_type'],art_id=e['art_id'],
                    parent_source_media_id=parent['media_id'],parent_original_path=parent['source_path'],source_family_id=parent['media_id'],
                    source_sha256=e['result_sha256'],model=settings['model'],version=VERSION,parent_identity_metadata=parent.get('identity'),
                    artistic_features=record['semantic_raw'].get('artistic_features','UNKNOWN'),composition=record['semantic_raw'].get('composition','UNKNOWN'),
                    visual_strength=record['semantic_raw'].get('visual_strength','UNKNOWN'),possible_dramatic_function=record['semantic_raw'].get('possible_dramatic_function','UNKNOWN'))
                write_json(checkpoint,record)
                if read(checkpoint)!=record:raise ValueError('Checkpoint readback mismatch')
                say('SAVED permanent checkpoint')
            except Exception as exc:
                failure=dict(art_id=e['art_id'],error=f'{type(exc).__name__}: {exc}',status='FAILED',signature=signature)
                write_json(checkpoint,failure);failed.append(failure);say('FAILED: '+failure['error'])
                if __import__('os').environ.get('FAMILY_PIPELINE_AUTO')=='1':raise
                continue
        records.append(record)
    counts={k:sum(r['art_type']==k for r in records) for k in i['counts']}
    status.update(classified_art=counts,failed=failed,total_classified_media=len(original['media'])+len(records))
    if failed or i['invalid'] or any(i['counts'][k]!=i['manifest']['expected'].get(k) for k in i['counts']):
        save_status(task,status,target);say('CLASSIFY + ART INCOMPLETE\nSTOP');return 1
    if digest(i['original_path'])!=original_hash or digest(i['manifest_path'])!=manifest_hash:raise ValueError('Inputs changed during classification')
    say('Merging classification...');merged=merge(original,records,version);validate_merged(merged,original,entries)
    output=target/'classification.json'
    if output.exists():
        previous=read(output)
        comparable=copy.deepcopy(merged);comparable['timestamp']=previous['timestamp']
        if previous!=comparable:raise ValueError('Immutable merged version conflict')
        merged=previous
    else:write_json(output,merged)
    validate_merged(read(output),original,entries)
    if read(output)!=merged:raise ValueError('Merged readback mismatch')
    pointer=dict(schema_version=1,task_id=task_id,classification_path=str(output),sha256=digest(output),bank_version=version,
                 original_classified=len(original['media']),new_watercolor=counts['watercolor'],new_double_exposure=counts['double_exposure'],total_classified_media=len(merged['media']))
    write_json(target/'bank_index.json',pointer)
    status.update(status='CLASSIFY + ART COMPLETE',permanent_classification_path=str(output),permanent_sha256=digest(output),permanent_copy_verified=True)
    save_status(task,status,target)
    # Publish the current bank pointer only after all records and recovery data pass checks.
    write_json(i['destination']/'current.json',pointer)
    write_json(task/'classified_media_bank.json',pointer)
    say(f'Original: {len(original["media"])}; new watercolor: {counts["watercolor"]}; new double exposure: {counts["double_exposure"]}; total: {len(merged["media"])}')
    say('CLASSIFY + ART COMPLETE\nSTOP');return 0

if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('task_id');m=cli.add_mutually_exclusive_group();m.add_argument('--prepare',action='store_true');m.add_argument('--dry-run',action='store_true');args=cli.parse_args()
    try:raise SystemExit(run(args.task_id,args.prepare,args.dry_run))
    except Exception as exc:
        say(f'ART CLASSIFY UPDATE FAILED: {type(exc).__name__}: {exc}\nSTOP')
        if not args.dry_run and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',args.task_id) and args.task_id.lower()!='legacy':
            task=ROOT/'tasks'/args.task_id
            if task.resolve()==task.absolute() and (task/'state.json').exists():
                try:save_status(task,dict(task_id=args.task_id,mode='PREPARE' if args.prepare else 'RUN',status='FAILED',timestamp=common.stamp(),error=f'{type(exc).__name__}: {exc}',execution_state='STOP'))
                except Exception as status_error:say(f'Could not save failure status: {status_error}')
        raise SystemExit(1)
