"""Task ART adapter for the existing OpenAI portrait edit backend. No timeline operations."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import threading
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from ingest import write_json, digest, say
from utils.project_delivery import copy_file_to_path
from utils.image_analysis import analyze_image
from api.openai_image import edit_image_with_openai, SUPPORTED_EDIT_MODELS, _fit_prompt_length, GEOMETRY_POLICY
from PIL import Image

TYPES={'watercolor':'W','double_exposure':'D'}
def now():return datetime.now(timezone.utc).isoformat()
def signature(entry):
    fields=['art_id','source_media_id','source_sha256','prompt','generation_backend','model','geometry_policy']
    return hashlib.sha256(json.dumps({k:entry[k] for k in fields},sort_keys=True).encode()).hexdigest()
def valid_image(path):
    try:
        with Image.open(path) as im:
            if im.format!='PNG' or min(im.size)<256:return False
            im.verify()
        return True
    except (OSError,ValueError):return False

def verified_copy(source,target):
    if source.resolve()==target.resolve():raise ValueError('Source and copy target must differ')
    copy_file_to_path(source,target)
    if digest(source)!=digest(target):raise ValueError(f'Copy checksum mismatch: {target}')

@contextmanager
def waiting(label,seconds):
    done=threading.Event()
    def progress():
        elapsed=0
        while not done.wait(seconds):
            elapsed+=seconds;say(f'{label} Waiting for AI... {elapsed//60:02d}:{elapsed%60:02d}')
    thread=threading.Thread(target=progress,daemon=True);thread.start()
    try:yield
    finally:done.set();thread.join()

def build_plan(task,config,classification,permanent):
    media={m['media_id']:m for m in classification['media']}
    entries=[];used={};counts={k:0 for k in TYPES}
    for selection in config['selections']:
        kind=selection['art_type']
        if kind not in TYPES:raise ValueError('Unknown art type')
        counts[kind]+=1;index=counts[kind];ident=f'{TYPES[kind]}{index:02d}'
        m=media[selection['source_media_id']];source=Path(m['source_path'])
        if m['status']!='CLASSIFIED' or not source.is_file():raise ValueError('Classified source unavailable')
        if source.suffix.lower() not in {'.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff'}:raise ValueError('ART requires still photos')
        prior=used.setdefault(m['media_id'],[])
        if kind in prior:raise ValueError('Duplicate source within style')
        prior.append(kind)
        stem=f'{index:02d}';relative=Path(kind)/ident
        source_name=f'source_{stem}{source.suffix}'
        output_name=f'{kind}_{stem}.png'
        instruction=('Create a genuine hand-painted watercolor, not a photo filter. Preserve every central child exactly as in the reference: facial features, age, proportions, expression and pose; keep them the focus. Use luminous transparent washes, pigment granulation, watercolor bleed, soft lost edges and visible white textured watercolor paper. Leave at least half the background nearly unpainted white. Simplify distracting background figures without inventing people. No text, border or collage.' if kind=='watercolor' else
                     'Create a purposeful fine-art double exposure from this photograph. Preserve the central children and their recognizable facial features, age, proportions, expression and pose. Keep faces clear and natural; integrate the second layer mainly through clothing, silhouettes and surrounding space. Avoid duplicate faces or extra people. Use coherent light and restrained transitions, not a pasted background or mechanical filter. No text or border. Second layer: '+selection['background_concept'])
        if config.get('subject_description'):
            instruction=instruction.replace('every central child','every central person').replace('central children','central people')
            instruction+=' Task subject context (do not invent identity or extra people): '+config['subject_description']
        prompt=_fit_prompt_length(instruction)
        if prompt.endswith('...'):raise ValueError('Prompt would be truncated')
        entry=dict(art_id=ident,index=index,art_type=kind,source_media_id=m['media_id'],original_source_path=str(source),
            source_filename=source.name,source_copy_path=str(task/'art'/relative/source_name),art_result_path=str(task/'art'/relative/output_name),
            permanent_source_copy_path=str(permanent/relative/source_name),permanent_art_result_path=str(permanent/relative/output_name),
            relative_source=str(relative/source_name),relative_result=str(relative/output_name),prompt=prompt,transformation_instruction=instruction,
            artistic_rationale=selection['artistic_rationale'],background_concept=selection.get('background_concept'),
            classification_summary=m['scene']['summary'],classification_quality=m['quality'],identity_status=m['identity']['category'],
            generation_backend=config['generation_backend'],model=config['model'],timestamp=now(),status='PREPARED',source_sha256=digest(source),
            result_sha256=None,permanent_copy_verified=False,geometry_policy=GEOMETRY_POLICY,preserve_aspect_ratio=True)
        entry['request_signature']=signature(entry);entries.append(entry)
    for kind in TYPES:
        if counts[kind]!=config[kind+'_count'] or counts[kind]<0:raise ValueError(f'Count mismatch: {kind}')
    for e in entries:e['cross_style_overlap']=len(used[e['source_media_id']])>1
    return entries

def review(path,entries):
    lines=['<!doctype html><html lang="ru"><meta charset="utf-8"><title>ART Review</title><style>body{font:16px system-ui;margin:24px;background:#f5f5f5}table{width:100%;border-collapse:collapse}td,th{padding:12px;border:1px solid #ccc;vertical-align:top}img{width:100%;max-width:460px;max-height:420px;object-fit:contain}small{display:block}h2{margin-top:40px}</style><h1>ART candidate bank</h1><p>Source и ART рядом только для review. Это не порядок монтажа. Идентичность по лицам не устанавливалась; UNKNOWN сохранён. Pending — генерация ещё не выполнена.</p>']
    for kind in TYPES:
        lines.append(f'<h2>{kind}</h2><table><tr><th>ID</th><th>SOURCE</th><th>ART</th><th>Concept / rationale</th></tr>')
        for e in entries:
            if e['art_type']!=kind:continue
            source=Path(e['relative_source']).as_posix();result=Path(e['relative_result']).as_posix()
            art=f'<a href="{result}"><img loading="lazy" src="{result}" alt="{e["art_id"]}"></a>' if e['status']=='COMPLETE' else '<strong>Pending</strong>'
            lines.append(f'<tr><td>{e["art_id"]}<small>{html.escape(e["status"])}</small></td><td><a href="{source}"><img loading="lazy" src="{source}" alt="Source"></a></td><td>{art}</td><td>{html.escape(e["artistic_rationale"])}<p>{html.escape(e.get("background_concept") or "")}</p><small>Identity: {html.escape(e["identity_status"])}</small></td></tr>')
        lines.append('</table>')
    path.write_text('\n'.join(lines)+'</html>',encoding='utf-8')

def publish(work,permanent,manifest):
    entries=manifest['entries']
    write_json(work/'art_manifest.json',manifest)
    for kind in TYPES:
        rows=[dict(index=e['index'],art_id=e['art_id'],source_media_id=e['source_media_id'],original_source_path=e['original_source_path'],source_copy=e['permanent_source_copy_path'],art_result=e['permanent_art_result_path']) for e in entries if e['art_type']==kind]
        write_json(work/f'{kind}_sources.json',rows)
        (work/f'{kind}_sources.txt').write_text('\n'.join(r['source_copy'] for r in rows)+'\n',encoding='utf-8')
    review(work/'ART_REVIEW.html',entries)
    for name in ['art_manifest.json','ART_REVIEW.html']+[f'{k}_sources.{ext}' for k in TYPES for ext in ('json','txt')]:verified_copy(work/name,permanent/name)
    if json.loads((permanent/'art_manifest.json').read_text(encoding='utf-8'))!=manifest:raise ValueError('Manifest readback mismatch')

def record_status(task,work,permanent,manifest,prepare):
    counts={k:sum(e['status']=='COMPLETE' for e in manifest['entries'] if e['art_type']==k) for k in TYPES}
    complete=all(counts[k]==manifest['expected'][k] for k in TYPES)
    status='ART READY FOR USER RUN' if prepare else 'ART COMPLETE' if complete else 'ART INCOMPLETE'
    data=dict(timestamp=now(),status=status,mode='PREPARE' if prepare else 'RUN',valid=counts,expected=manifest['expected'],permanent_storage=str(permanent),full_art_run='NOT STARTED' if prepare else 'STARTED',execution_state='STOP',next_stage='NOT STARTED')
    (work/'ART_STATUS.md').write_text('# ART STATUS\n\n```json\n'+json.dumps(data,ensure_ascii=False,indent=2)+'\n```\n\nTechnical validation only; visual likeness and artistic quality require review. Source/ART adjacency is not an editing instruction.\n\nSTOP\n',encoding='utf-8')
    verified_copy(work/'ART_STATUS.md',permanent/'ART_STATUS.md')
    state_path=task/'state.json';state=json.loads(state_path.read_text(encoding='utf-8'));state.setdefault('history',[]).append(data)
    state.setdefault('stages',{})['PREPARE_ART' if prepare else 'ART']=status;state.update(stage='PREPARE ART' if prepare else 'ART',status=status,execution_state='STOP',next_stage='USER MANUALLY RUNS ART' if prepare else 'NOT STARTED');state['art']=data;write_json(state_path,state)
    return status

def run(task_id,prepare=False,dry_run=False):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',task_id) or task_id.lower()=='legacy':raise ValueError('Invalid TASK ID')
    task=ROOT/'tasks'/task_id
    if task.resolve()!=task.absolute():raise ValueError('Redirected task folder')
    config=json.loads((task/'art_config.json').read_text(encoding='utf-8'))
    if config['task_id']!=task_id or config['generation_backend']!='openai_api':raise ValueError('Task/backend mismatch')
    if config['model'] not in SUPPORTED_EDIT_MODELS:raise ValueError('Unsupported existing edit model')
    state=json.loads((task/'state.json').read_text(encoding='utf-8'))
    if state['stages'].get('CLASSIFY')!='COMPLETE':raise ValueError('CLASSIFY is not complete')
    classified=task/'classify/classification.json'
    if digest(classified)!=config['classification_sha256']:raise ValueError('CLASSIFY changed: review selection first')
    classification=json.loads(classified.read_text(encoding='utf-8'))
    native=Path(config['permanent_project_dir']).resolve();permanent=(native/config['permanent_relative_dir']).resolve()
    if not native.is_dir() or native.is_relative_to(ROOT.resolve()) or not permanent.is_relative_to(native):raise ValueError('Permanent folder unresolved/unsafe')
    work=task/'art';entries=build_plan(task,config,classification,permanent)
    say(f'{task_id} ART\nPERMANENT STORAGE: {permanent}')
    if dry_run:
        for e in entries:say(f'{e["art_id"]} {e["source_filename"]} -> {e["permanent_art_result_path"]}')
        say('DRY RUN OK: no generation, no copy, no state writes. STOP');return 0
    work.mkdir(exist_ok=True);permanent.mkdir(parents=True,exist_ok=True)
    manifest_path=work/'art_manifest.json'
    if not manifest_path.exists() and (permanent/'art_manifest.json').exists():verified_copy(permanent/'art_manifest.json',manifest_path)
    if manifest_path.exists():
        old=json.loads(manifest_path.read_text(encoding='utf-8'))
        if [e['request_signature'] for e in old['entries']]!=[e['request_signature'] for e in entries]:raise ValueError('Frozen ART selection/config changed; refusing overwrite')
        entries=old['entries']
    manifest={'schema_version':1,'task_id':task_id,'classification_sha256':config['classification_sha256'],'expected':{k:config[k+'_count'] for k in TYPES},'entries':entries}
    for e in entries:
        source=Path(e['original_source_path']);local=Path(e['source_copy_path']);remote=Path(e['permanent_source_copy_path'])
        if digest(source)!=e['source_sha256']:raise ValueError('Original source changed')
        for destination in (local,remote):
            if destination.exists() and digest(destination)!=e['source_sha256']:raise ValueError('Source copy conflict')
            if not destination.exists():verified_copy(source,destination)
    for source,name in [(task/'art_config.json','art_config.json'),(task/'task.json','task_metadata.json'),(classified,'classification_snapshot.json')]:verified_copy(source,permanent/name)
    publish(work,permanent,manifest)
    if prepare:
        say(record_status(task,work,permanent,manifest,True));say('FULL ART RUN: NOT STARTED\nSTOP');return 0
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    for kind in TYPES:
        say(kind.upper())
        for e in entries:
            if e['art_type']!=kind:continue
            label=f'[{e["art_id"]}/{config[kind+"_count"]}]';say(label+' '+e['source_filename'])
            result=Path(e['art_result_path']);remote=Path(e['permanent_art_result_path'])
            checkpoint=work/'checkpoints'/f'{e["art_id"]}.json'
            try:
                if not result.exists() and e.get('result_sha256') and remote.exists() and digest(remote)==e['result_sha256'] and valid_image(remote):verified_copy(remote,result)
                if result.exists():
                    if not valid_image(result):raise ValueError('Existing output invalid; refusing silent overwrite')
                    if e.get('result_sha256') and digest(result)!=e['result_sha256']:raise ValueError('Output hash conflict')
                    if not e.get('result_sha256') and e['status'] not in ('GENERATING','FAILED'):raise ValueError('Untracked output; cannot adopt')
                    say(label+' SKIP EXISTING VALID RESULT')
                else:
                    e.update(status='GENERATING',timestamp=now());write_json(checkpoint,e);publish(work,permanent,manifest)
                    say(label+' Generating '+kind+'...')
                    if e.get('background_concept'):say('Background concept: '+e['background_concept'])
                    with waiting(label,config['heartbeat_seconds']):
                        edit_image_with_openai(Path(e['source_copy_path']),kind,result,analyze_image(Path(e['source_copy_path'])),e['art_id'],prompt_override=e['prompt'],model_name=e['model'])
                    if not valid_image(result):raise ValueError('Invalid generated PNG')
                e['result_sha256']=digest(result);write_json(checkpoint,e)
                verified_copy(result,remote)
                provenance={}
                for suffix in ['.reference.png','.response.png','.geometry.json']:
                    local_sidecar=result.with_suffix(suffix)
                    if local_sidecar.exists():
                        permanent_sidecar=remote.with_suffix(suffix);verified_copy(local_sidecar,permanent_sidecar)
                        provenance[suffix]=dict(local_path=str(local_sidecar),permanent_path=str(permanent_sidecar),sha256=digest(local_sidecar))
                if provenance:e['geometry_provenance']=provenance
                e.update(status='COMPLETE',permanent_copy_verified=True,timestamp=now());e.pop('error',None)
                write_json(checkpoint,e);verified_copy(checkpoint,permanent/'checkpoints'/checkpoint.name)
                publish(work,permanent,manifest);say(label+' Result saved. Permanent copy OK. Checkpoint saved.')
            except Exception as exc:
                e.update(status='FAILED',error=f'{type(exc).__name__}: {exc}',timestamp=now());write_json(checkpoint,e);publish(work,permanent,manifest);say(label+' FAILED: '+e['error'])
                if __import__('os').environ.get('FAMILY_PIPELINE_AUTO')=='1':raise
        say(f'{kind.upper()} VALID: {sum(e["status"]=="COMPLETE" for e in entries if e["art_type"]==kind)}/{config[kind+"_count"]}')
    publish(work,permanent,manifest);status=record_status(task,work,permanent,manifest,False);say(status+'\nSTOP');return 0 if status=='ART COMPLETE' else 1

if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('task_id');modes=cli.add_mutually_exclusive_group();modes.add_argument('--prepare',action='store_true');modes.add_argument('--dry-run',action='store_true');args=cli.parse_args()
    try:raise SystemExit(run(args.task_id,args.prepare,args.dry_run))
    except Exception as exc:say(f'ART FAILED: {type(exc).__name__}: {exc}\nSTOP');raise SystemExit(1)
