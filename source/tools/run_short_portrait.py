"""Derive the first SHORT only from a saved FULL plan; no FULL approval required."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.run_portrait_draft import read, REPO
from tools.run_watercolor_package import digest, write_json, exclusive_lock
from tools.classify_source_package import ProgressLog
from tools.revise_edit_plan import validate_plan
from tools.render_structure_draft import run as render

def derive(full, structure, minimum, maximum):
    byid={c['id']:c for c in full['clips']}; plan=deepcopy(full); plan['clips']=[]; seen=set();cursor=0
    for block in structure['blocks']:
        for item in block['items']:
            key=item['material_id']; duration=item['duration_seconds']
            if key not in byid or key in seen:raise ValueError('SHORT must use unique FULL IDs only')
            if type(duration) is not int or not 0<duration<=byid[key]['duration_seconds']:raise ValueError('SHORT clip cannot exceed its FULL duration')
            seen.add(key);c=deepcopy(byid[key]);c.update(duration_seconds=duration,frames=duration*full['fps'],timeline_start_frame=cursor,block=block['title'],editorial_reason=item['reason'])
            if c['kind']=='video':c['source_out_seconds']=c['source_in_seconds']+duration
            plan['clips'].append(c);cursor+=c['frames']
    plan.update(frames=cursor,duration_seconds=cursor/full['fps'],status='DRAFT',human_review='PENDING',variant='SHORT',title=structure['title'],synopsis=structure['synopsis'])
    validate_plan(plan)
    if not minimum<=plan['duration_seconds']<=maximum:raise ValueError('SHORT must be 60â€“180 seconds; revise editorially, never pad automatically')
    return plan

def run(path,check=False):
    cfg=read(path);root=Path(cfg['output_root']);root.mkdir(parents=True,exist_ok=True)
    with exclusive_lock(root/'short.lock'):
        log=ProgressLog(root/'progress.log');source=Path(cfg['source_edit_plan'])
        if not source.exists():
            if check:
                log.emit('WAITING_DEPENDENCY','New FULL edit plan required: '+str(source)+'; no fallback to previous FULL; no AI/render')
                write_json(root/'check_result.json',dict(status='WAITING_DEPENDENCY',required_full_plan=str(source),api_called=False));return
            raise FileNotFoundError('First generate FULL edit plan: '+str(source))
        full=read(source);validate_plan(full)
        ratio=cfg.get('duration_mode')=='semantic_ratio'
        minimum=1/full['fps'] if ratio else 60
        maximum=(full['frames']-1)/full['fps'] if ratio else 180
        facets=read(Path(cfg['coverage_map']))['facets'] if cfg.get('coverage_map') else []
        narrative=cfg['narrative']
        if ratio:
            fraction=cfg.get('approximate_fraction',1/3)
            if not 0<fraction<1:raise ValueError('Invalid approximate_fraction')
            narrative+=f'\nActual FULL duration={full["duration_seconds"]}s. Soft orientation only: about {full["duration_seconds"]*fraction:.1f}s. No minimum or fixed seconds quota. Must be strictly shorter than FULL; depart from ratio for meaning.'
        result=Path(cfg['classification_result']);rows=[json.loads(s) for s in (result.parent/'catalog.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
        byid={r['id']:r for r in rows};subset=[]
        for n,c in enumerate(full['clips'],1):
            r=deepcopy(byid[c['id']]);assert r['sha256']==c['sha256']==digest(c['path'])
            r['quality_notes']+=['FULL shot limit: '+str(c['duration_seconds'])+' seconds. SHORT duration must not exceed this. FULL editorial purpose: '+c['editorial_reason']]
            subset.append(r);log.emit('VERIFY',f'{n}/{len(full["clips"])} {c["id"]}')
        fingerprint=[digest(path),digest(source),digest(result.parent/'catalog.jsonl')]
        if cfg.get('coverage_map'): fingerprint.append(digest(cfg['coverage_map']))
        statepath=root/'state.json';state=read(statepath) if statepath.exists() else dict(fingerprint=fingerprint)
        if state['fingerprint']!=fingerprint:raise ValueError('FULL/config changed; use new SHORT output_root')
        inherited=root/'inherited_full_selection';inherited.mkdir(exist_ok=True)
        (inherited/'catalog.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in subset),encoding='utf-8')
        metadata=read(result);metadata.update(total=len(subset),completed=len(subset),inherited_from_full=str(source),no_new_classification=True)
        write_json(inherited/'classification_result.json',metadata)
        config=root/'structure_config.json'
        write_json(config,dict(schema_version=1,classification_result=str(inherited/'classification_result.json'),output_root=str(root/'structures'),model=cfg['model'],ai_enabled=True,**(dict(duration_mode='coverage',target_duration_seconds=None) if ratio else dict(duration_mode='compact',target_duration_seconds=180,max_duration_seconds=180)),media_type_policy=cfg.get('media_type_policy','legacy'),coverage_facets=facets,api_max_retries=0,structure_attempts=1,required_ids=[],excluded_ids=[],narrative=narrative+'\nFULL plan (only allowed shots, retain source ranges):\n'+json.dumps(full,ensure_ascii=False)))
        command=[sys.executable,'-u','-B',str(REPO/'tools/build_video_structure.py'),'--config',str(config)]
        if check:
            subprocess.run(command+['--dry-run'],check=True,timeout=60)
            log.emit('FINISH','CHECK PASS; FULL plan available; no AI/render; FULL MP4/approval not required');return
        if not state.get('plan'):
            ready=sorted((root/'structures').glob('*/structure.json'))
            if not ready:
                if state.get('api_started'):raise RuntimeError('Unresolved prior API request; inspect logs, no automatic retry')
                state['api_started']=True;write_json(statepath,state)
                with log.activity('SHORT STRUCTURE; one API attempt; 240s deadline'):
                    subprocess.run(command,check=True,timeout=240,cwd=REPO)
                ready=sorted((root/'structures').glob('*/structure.json'))
            short_structure=read(ready[-1])
            plan=derive(full,short_structure,minimum,maximum)
            if facets:write_json(root/'coverage_audit.json',short_structure['coverage_audit'])
            plan['source_full_plan']=str(source);plan['source_full_sha256']=digest(source)
            target=root/'short_draft_plan.json';write_json(target,plan);state['plan']=str(target);write_json(statepath,state)
        if state.get('video') and Path(state['video']).exists() and digest(state['video'])==state.get('sha256'):
            log.emit('FINISH','CACHED '+state['video']);return
        rc=root/'render_config.json';write_json(rc,dict(schema_version=1,edit_plan=state['plan'],output_root=str(root/'renders'),width=1280,height=720,fps=full['fps'],video_bitrate='1800k',audio_bitrate='128k',**({} if ratio else dict(max_duration_seconds=180))))
        saved=read(state['plan']);validate_plan(saved)
        if not minimum<=saved['duration_seconds']<=maximum:raise ValueError('SHORT duration must satisfy current policy')
        output=render(rc);state.update(video=output['video'],sha256=output['sha256'],status='DRAFT_READY_REVIEW_REQUIRED');write_json(statepath,state)
        log.emit('FINISH',state['video'])

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--check','--dry-run',action='store_true');a=p.parse_args();run(a.config.resolve(),a.check)
