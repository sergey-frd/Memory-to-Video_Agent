"""Build a resumable offline portrait from an already classified catalog."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.run_watercolor_package import exclusive_lock, write_json, digest
from tools.classify_source_package import ProgressLog
from tools.render_structure_draft import run as render, make_plan
from utils.video_frame_extract import resolve_ffmpeg_executable
from tools.portrait_coverage import duration_options

REPO = Path(__file__).resolve().parents[1]
def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))

def run(path, check=False):
    cfg = read(path)
    timing = duration_options(cfg)
    resolve = lambda s: (path.parent/s).resolve()
    root = resolve(cfg['output_root']); root.mkdir(parents=True, exist_ok=True)
    log = ProgressLog(root/'progress.log')
    with exclusive_lock(root/'draft.lock'):
        log.emit('STAGE', '1/4 VERIFY CLASSIFICATION AND MEDIA')
        result = resolve(cfg['classification_result'])
        catalog = result.parent/'catalog.jsonl'
        rows = [json.loads(s) for s in catalog.read_text(encoding='utf-8').splitlines() if s.strip()]
        current = result
        while True:
            data = read(current); raw = (current.parent/'catalog.jsonl').read_bytes()
            assert data['status']=='CLASSIFIED_REVIEW_REQUIRED' and data['completed']==data['total']
            assert len(raw.splitlines())==data['total']
            if data.get('catalog_sha256'): assert digest(current.parent/'catalog.jsonl')==data['catalog_sha256']
            if not data.get('base_classification_result'): break
            prior = Path(data['base_classification_result'])
            assert raw.startswith((prior.parent/'catalog.jsonl').read_bytes())
            current = prior
        assert len(rows)==cfg['expected_count'] and len({r['id'] for r in rows})==len(rows)
        byid = {r['id']:r for r in rows}
        for n,row in enumerate(rows,1):
            log.emit('VERIFY', f'{n}/{len(rows)} {row["id"]}')
            assert digest(row['path'])==row['sha256'], row['path']
            if row.get('art_source_id'): assert row['art_source_id'] in byid
        hero = read(resolve(cfg['hero_config']))
        human = Path(hero['human_detail_txt'])
        narrative = cfg['narrative']+'\nАКТУАЛЬНАЯ ХАРАКТЕРИСТИКА ГЕРОЯ:\n'+human.read_text(encoding='utf-8-sig')
        fingerprint = [digest(path),digest(catalog),digest(human)]
        facets = []
        if cfg.get('coverage_map'):
            map_path=resolve(cfg['coverage_map']); facets=read(map_path)['facets']
            fingerprint.append(digest(map_path))
            narrative += '\nКАРТА РАСКРЫТИЯ:\n'+json.dumps(facets,ensure_ascii=False)
        statepath = root/'state.json'
        state = read(statepath) if statepath.exists() else dict(fingerprint=fingerprint)
        if state['fingerprint']!=fingerprint: raise ValueError('Inputs changed; use a new output_root')
        sc = root/'structure_config.json'
        write_json(sc, dict(schema_version=1,classification_result=str(result),output_root=str(root/'structures'),
            model=cfg['model'],ai_enabled=True,**timing,api_max_retries=0,structure_attempts=1,
            media_type_policy=cfg.get('media_type_policy','legacy'),coverage_facets=facets,
            narrative=narrative,required_ids=[],excluded_ids=[]))
        ffmpeg = resolve_ffmpeg_executable()
        subprocess.run([ffmpeg,'-version'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=20)
        if check:
            subprocess.run([sys.executable,'-B',str(REPO/'tools/build_video_structure.py'),'--config',str(sc),'--dry-run'],check=True)
            write_json(root/'check_result.json',dict(status='CHECK_PASS_NO_API_NO_RENDER',materials=len(rows),fingerprint=fingerprint))
            log.emit('FINISH','CHECK PASS; no AI or render; duration policy='+json.dumps(timing)); return
        # Recover a structure already saved by the child before a coordinator interruption.
        if not state.get('structure'):
            ready = sorted((root/'structures').glob('*/structure.json'))
            ready = [p for p in ready if read(p).get('status')=='DRAFT_REVIEW_REQUIRED']
            if ready: state['structure']=str(ready[-1])
        if not state.get('structure'):
            if state.get('api_started'): raise RuntimeError('Previous structure request unresolved. Inspect logs before permitting a paid retry; no automatic retry.')
            state['api_started']=True; write_json(statepath,state)
            log.emit('STAGE','2/4 STRUCTURE API; one attempt; deadline=240s')
            child = subprocess.Popen([sys.executable,'-u','-B',str(REPO/'tools/build_video_structure.py'),'--config',str(sc)],cwd=REPO)
            started=time.monotonic()
            try:
                while child.poll() is None:
                    try: child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        log.emit('WAIT',f'STRUCTURE; elapsed={time.monotonic()-started:.0f}s; deadline=240s')
                    if time.monotonic()-started>240: raise TimeoutError('Structure deadline; inspect logs; no automatic paid retry')
                if child.returncode: raise RuntimeError('Structure failed; see structures/*/progress.log; no automatic retry')
            finally:
                if child.poll() is None: child.kill(); child.wait()
            ready=sorted((root/'structures').glob('*/structure.json'))
            state['structure']=str(ready[-1]); write_json(statepath,state)
        structure = read(state['structure'])
        plan = make_plan(structure,rows,25)
        if timing.get('max_duration_seconds') is not None and plan['duration_seconds']>timing['max_duration_seconds']: raise ValueError('Plan exceeds hard duration cap')
        write_json(root/'full_draft_plan.json',plan)
        selected={c['id'] for c in plan['clips']}
        if facets:
            write_json(root/'coverage_audit.json',dict(facets=facets,audit=structure['coverage_audit'],duration_seconds=plan['duration_seconds'],human_review='PENDING'))
        write_json(root/'art_selection.json',[dict(id=r['id'],source_id=r['art_source_id'],style=r['art_style'],
            selected=r['id'] in selected,original_selected=r['art_source_id'] in selected,
            reason=next((c['editorial_reason'] for c in plan['clips'] if c['id']==r['id']),'Not selected by this portrait plan; available for review'))
            for r in rows if r.get('art_source_id')])
        if state.get('video') and Path(state['video']).exists() and digest(state['video'])==state.get('video_sha256'):
            log.emit('FINISH','CACHED DRAFT '+state['video']); return
        log.emit('STAGE',f'3/4 RENDER 720p; clips={len(plan["clips"])}; duration={plan["duration_seconds"]}s')
        rc=root/'render_config.json'
        write_json(rc,dict(schema_version=1,structure=state['structure'],output_root=str(root/'renders'),
            width=1280,height=720,fps=25,video_bitrate='1800k',audio_bitrate='128k',**({ 'max_duration_seconds':timing['max_duration_seconds']} if 'max_duration_seconds' in timing else {} )))
        rendered=render(rc)
        state.update(video=rendered['video'],video_sha256=rendered['sha256'],status='DRAFT_READY_REVIEW_REQUIRED',duration_seconds=plan['duration_seconds'])
        write_json(statepath,state)
        log.emit('FINISH',f'{state["video"]}; {plan["duration_seconds"]}s; audio: source video only, silence for photos; artistic review pending')

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--check','--dry-run',action='store_true')
    a=p.parse_args();run(a.config.resolve(),a.check)
