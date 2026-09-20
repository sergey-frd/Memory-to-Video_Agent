"""Review-driven coordinator: draft, revisions, approval, native finishing."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.prepare_classification_input import digest, write_json
from tools.classify_source_package import ProgressLog
from tools.revise_edit_plan import run as revise, validate_plan
from tools.render_structure_draft import run as render
from tools.run_hero_pipeline import run as pipeline
from tools.prepare_native_export import prepare as native
from tools.prepare_plan_animation import prepare as animation
from tools.prepare_plan_color import prepare as color
from tools.prepare_plan_transitions import prepare as transitions, select_transitions
from tools.review_draft_alternative import run as alternative
from utils.video_frame_extract import resolve_ffmpeg_executable


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def snapshot(paths):
    return {str(Path(p).resolve()):digest(p) for p in paths}


def verify(hashes):
    for path, sha in hashes.items():
        if digest(path)!=sha:raise ValueError('Checkpoint artifact changed: '+path)


def review_record(result, video_override=None):
    data=read(result)
    if data['status']!='DRAFT_READY':raise ValueError('Draft is not ready')
    plan=Path(result).parent/'edit_plan.json';validate_plan(read(plan))
    video=Path(video_override or data['video'])
    if digest(video)!=data['sha256']:raise ValueError('Review video checksum mismatch')
    return dict(plan=str(plan.resolve()),video=str(video),artifacts=snapshot([result,plan,video]))


def selected_review(state):
    return state['reviews'][state.get('selected_review', len(state['reviews'])-1)]


def check_approval(state):
    review=selected_review(state);verify(review['artifacts'])
    if state.get('approval')!=review['artifacts']:
        raise ValueError('Review the latest draft and run --action approve first')


def native_output(job_path, log):
    job=read(job_path);folder=Path(job_path).parent
    status=folder/'native_status.txt';video=Path(job['video']);project=Path(job['project'])
    if not status.exists() or status.read_text().strip()!='EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA' or not video.exists():
        return None
    if project.stat().st_mtime>video.stat().st_mtime+2:
        raise ValueError('Project was saved after MP4 export; export the current project again: '+str(project))
    with log.activity('VERIFY NATIVE MP4 '+str(video)), (folder/'workflow_decode.log').open('w',encoding='utf-8') as output:
        subprocess.run([resolve_ffmpeg_executable(),'-v','error','-xerror','-nostdin','-i',str(video),'-f','null','-'],
                       stdout=subprocess.DEVNULL,stderr=output,check=True)
    if video.stat().st_size==0:raise ValueError('Empty native export')
    return snapshot([job_path,project,video,status])


def run(path,action='status',revision_config=None,until='color',closeout_config=None,apply=False,alternative_config=None,review_index=None,assembly_config=None):
    path=path.resolve();cfg=read(path)
    if cfg.get('schema_version')!=1:raise ValueError('Expected schema_version 1')
    if action=='closeout':
        if closeout_config is None:raise ValueError('--closeout-config is required')
        from tools.workflow_closeout import run as closeout
        return closeout(closeout_config.resolve(),path,apply)
    resolve=lambda s:(path.parent/s).resolve()
    root=resolve(cfg['output_root']);root.mkdir(parents=True,exist_ok=True)
    state_path=root/'workflow_state.json';lock=root/'workflow.lock'
    with lock.open('x') as stream:stream.write('Do not remove while workflow is running')
    try:
        state=read(state_path) if state_path.exists() else dict(config_sha256=digest(path),reviews=[],finishing={})
        if state['config_sha256']!=digest(path):raise ValueError('Workflow configuration changed; use a new output_root')
        def save():
            temp=state_path.with_suffix('.tmp');write_json(temp,state);temp.replace(state_path)
        log=ProgressLog(root/'workflow.log')
        def stage_config(name,payload):
            target=root/(name+'.local.json');write_json(target,payload);return target
        if action=='status':
            print(json.dumps(state,ensure_ascii=False,indent=2));return state
        if action=='start':
            if state['reviews']:
                verify(selected_review(state)['artifacts']);print('REVIEW_REQUIRED: '+selected_review(state)['video']);return state
            if cfg.get('initial_review_result'):
                result=resolve(cfg['initial_review_result'])
            else:
                pc=resolve(cfg['pipeline_config']);pipeline(pc,until='draft')
                pipeline_root=(pc.parent/read(pc)['output_root']).resolve()
                upstream=read(pipeline_root/'pipeline_state.json')
                if 'draft' not in upstream['stages']:
                    state['status']='UPSTREAM_REVIEW_REQUIRED';save();return state
                result=Path(upstream['stages']['draft']['result'])
            override=resolve(cfg['initial_review_video']) if cfg.get('initial_review_video') and cfg.get('initial_review_result') else None
            state['reviews'].append(review_record(result,override));state['status']='REVIEW_REQUIRED';save()
            print('REVIEW_REQUIRED: '+state['reviews'][-1]['video']);return state
        if not state['reviews']:raise ValueError('Run --action start first')
        if state.get('pending_assembly') and action!='best-assembly':
            raise ValueError('Complete pending best assembly first')
        if state.get('pending_alternative') and action!='alternative':
            raise ValueError('Complete pending alternative first')
        if action=='select':
            if state.get('pending_revision'):raise ValueError('Complete pending revision first')
            if type(review_index) is not int or not 1<=review_index<=len(state['reviews']):
                raise ValueError('--review-index must identify an existing review (1-based)')
            chosen=state['reviews'][review_index-1];verify(chosen['artifacts'])
            state['selected_review']=review_index-1;state.pop('approval',None)
            if state['finishing']:state.setdefault('previous_finishing',[]).append(state['finishing'])
            state['finishing']={};state['status']='REVIEW_REQUIRED';save()
            print('SELECTED REVIEW '+str(review_index)+': '+chosen['video']);return state
        verify(selected_review(state)['artifacts'])
        if action=='best-assembly':
            from tools.build_best_assembly import run as best_assembly
            if state.get('pending_revision'):raise ValueError('Complete pending revision first')
            if assembly_config is None:raise ValueError('--assembly-config is required')
            ac=assembly_config.resolve();fingerprint=digest(ac)
            pending=state.get('pending_assembly')
            if pending and pending['sha256']!=fingerprint:raise ValueError('Resume with the same assembly config')
            if not pending:
                best_assembly(ac,state['reviews'],selected_review(state),dry_run=True)
                state['pending_assembly']=dict(config=str(ac),sha256=fingerprint);save()
            result=best_assembly(ac,state['reviews'],selected_review(state))
            record=review_record(Path(result['result']))
            record.update(variant='best_assembly',critique=result['report'],iteration_report=result['report'],post_critique=result['post_critique'])
            record['artifacts'].update(result['inputs']);record['artifacts'].update(result['artifacts'])
            state['reviews'].append(record);state['selected_review']=len(state['reviews'])-1
            state.pop('approval',None);state.pop('pending_assembly')
            if state['finishing']:state.setdefault('previous_finishing',[]).append(state['finishing'])
            state['finishing']={};state['status']='BEST_ASSEMBLY_REVIEW_REQUIRED';save()
            print('BEST ASSEMBLY: '+record['video']+'\nREPORT: '+result['report']);return state
        if action=='alternative':
            if state.get('pending_revision'):raise ValueError('Complete pending revision first')
            current=selected_review(state)
            if current.get('post_critique') and not read(current['post_critique'])['continue_iteration']:
                state['status']='ITERATION_REVIEW_REQUIRED';save()
                print('STOP: no justified further automatic improvement; compare saved versions. Report: '+current['iteration_report']);return state
            if alternative_config is None:raise ValueError('--alternative-config is required')
            ac=alternative_config.resolve();fingerprint=digest(ac)
            pending=state.get('pending_alternative')
            if pending and pending['sha256']!=fingerprint:raise ValueError('Resume pending alternative with the same config')
            source_index=state.get('selected_review',len(state['reviews'])-1)
            out=root/('alternative_'+str(len(state['reviews'])+1))
            if not pending:
                alternative(ac,selected_review(state),out,dry_run=True)
                if out.exists():raise FileExistsError(out)
                state['pending_alternative']=dict(config=str(ac),sha256=fingerprint,source_index=source_index);save()
            result=alternative(ac,selected_review(state),out)
            record=review_record(Path(result['result']))
            record.update(variant='alternative',based_on_review=source_index+1,critique=result['critique'])
            for key in ('post_critique','iteration_report'):
                if key in result:
                    record[key]=result[key];record['artifacts'].update(snapshot([Path(result[key])]))
            record['artifacts'].update(snapshot([Path(result[k]) for k in ('critique','evidence','inputs')]))
            record['artifacts'].update(read(result['inputs']))
            state['reviews'].append(record);state['selected_review']=len(state['reviews'])-1
            state.pop('approval',None);state.pop('pending_alternative')
            if state['finishing']:state.setdefault('previous_finishing',[]).append(state['finishing'])
            state['finishing']={};state['status']='ALTERNATIVE_REVIEW_REQUIRED';save()
            print('COMPARE ORIGINAL AND ALTERNATIVE: '+record['video']+'\nCritique: '+record['critique']);return state
        if action=='revise':
            if revision_config is None:raise ValueError('--revision-config is required')
            rc=revision_config.resolve();payload=read(rc)
            previous=Path(selected_review(state)['plan'])
            if (rc.parent/payload['source_edit_plan']).resolve()!=previous:
                raise ValueError('Revision must refer to the selected review edit_plan.json')
            fingerprint=digest(rc)
            pending=state.get('pending_revision')
            if pending and pending['sha256']!=fingerprint:raise ValueError('Resume pending revision with the same config')
            revision_dir=(rc.parent/payload['output_dir']).resolve()
            if not pending:
                if revision_dir.exists():raise FileExistsError(revision_dir)
                state['pending_revision']=dict(sha256=fingerprint,config=str(rc));save()
            plan=revision_dir/'edit_plan.json'
            if not plan.exists():revise(rc)
            if read(plan).get('revision_config_sha256')!=fingerprint:raise ValueError('Revision provenance mismatch')
            draft_cfg=dict(schema_version=1,edit_plan=str(plan),output_root=str(revision_dir/'renders'),
                           width=1280,height=720,fps=read(plan)['fps'],video_bitrate='1400k',audio_bitrate='128k')
            if payload.get('max_duration_seconds') is not None:
                cap=payload['max_duration_seconds']
                if type(cap) is not int or cap<=0:raise ValueError('Invalid max_duration_seconds')
                revised=read(plan)
                if sum(c['frames'] for c in revised['clips'])>cap*revised['fps']:
                    raise ValueError('Render forbidden: revision exceeds hard duration cap')
                draft_cfg['max_duration_seconds']=cap
            # Recover a completed render if the coordinator stopped before checkpointing it.
            ready=[p for p in (revision_dir/'renders').glob('*/render_result.json') if read(p).get('status')=='DRAFT_READY']
            if ready:result=max(ready,key=lambda p:p.stat().st_mtime)
            else:
                rendered=render(stage_config('revision_render',draft_cfg));result=Path(rendered['output'])/'render_result.json'
            state['reviews'].append(review_record(result));state.pop('approval',None)
            state['selected_review']=len(state['reviews'])-1
            if state['finishing']:state.setdefault('previous_finishing',[]).append(state['finishing'])
            state['finishing']={};state.pop('pending_revision');state['status']='REVIEW_REQUIRED';save()
            print('REVIEW_REQUIRED: '+state['reviews'][-1]['video']);return state
        if action=='approve':
            if state.get('pending_revision'):raise ValueError('Complete pending revision first')
            if cfg.get('review',{}).get('require_alternative',False):
                variants=[r for r in state['reviews'] if r.get('variant')=='alternative']
                if not variants:raise ValueError('Create and compare an alternative before approval')
                for variant in variants:verify(variant['artifacts'])
            state['approval']=selected_review(state)['artifacts'];state['status']='EDIT_APPROVED';save()
            print('EDIT_APPROVED');return state
        check_approval(state)
        if state.get('pending_revision'):raise ValueError('Complete pending revision first')
        if action=='accept':
            if state.get('status')!='FINAL_REVIEW_REQUIRED':raise ValueError('Finish all stages and review the final MP4 first')
            for entry in state['finishing'].values():verify(entry['completed'])
            state['status']='FINAL_ACCEPTED';save();print('FINAL_ACCEPTED');return state
        if action!='finish':raise ValueError('Unknown action')
        if state.get('status')=='FINAL_ACCEPTED':
            for entry in state['finishing'].values():verify(entry['completed'])
            print('FINAL_ACCEPTED: checkpoints verified');return state
        for stage in ['native','animation','transitions','color'][:['native','animation','transitions','color'].index(until)+1]:
            entry=state['finishing'].get(stage)
            if entry and entry.get('completed'):
                verify(entry['completed']);continue
            if entry is None:
                if stage!='native':
                    previous_stage={'animation':'native','transitions':'animation','color':'transitions'}[stage]
                    previous=state['finishing'][previous_stage]
                    current_plan=read(previous['job'])['plan']
                    settings=cfg.get(stage,{'enabled':False})
                    reason=None
                    if not settings.get('enabled',True):reason='disabled in configuration'
                    elif stage=='animation' and not any(c['kind']=='image' for c in current_plan['clips']):reason='no photographs'
                    elif stage=='transitions' and not select_transitions(current_plan,settings)[0]:reason='no eligible video boundaries'
                    if reason:
                        state['finishing'][stage]=dict(job=previous['job'],completed=previous['completed'],skipped=reason)
                        save();print('[SKIP] '+stage+': '+reason);continue
                out=root/('review_'+str(state.get('selected_review',len(state['reviews'])-1)+1))/stage
                adopted=cfg.get('existing_jobs',{}).get(stage) if selected_review(state) is state['reviews'][0] else None
                job_name={'native':'job.json','animation':'animation_job.json','transitions':'transition_job.json','color':'color_job.json'}[stage]
                jsx_name={'native':'assemble_export.jsx','animation':'animate_export.jsx','transitions':'transitions_export.jsx','color':'color_export.jsx'}[stage]
                if adopted:
                    job_path=resolve(adopted)
                    if read(job_path)['plan']!=read(selected_review(state)['plan']):raise ValueError('Adopted job has a different edit plan')
                else:
                    payload=dict(schema_version=1,output_root=str(out))
                    if stage=='native':
                        settings=cfg['native'];payload.update(project=str(resolve(settings['project'])),target_sequence=settings['target_sequence'],
                            preset=str(resolve(settings['preset'])),edit_plan=selected_review(state)['plan'],width=1920,height=1080)
                        prepare=native
                    else:
                        previous_stage={'animation':'native','transitions':'animation','color':'transitions'}[stage]
                        payload.update(cfg[stage]);payload.pop('enabled',None);payload['source_job']=state['finishing'][previous_stage]['job']
                        prepare={'animation':animation,'transitions':transitions,'color':color}[stage]
                    cp=stage_config(stage,payload)
                    # Only one preparation per stage/review; discover after interruption.
                    existing=list(out.glob('*/'+job_name))
                    if not existing:
                        prepare(cp,dry_run=True);prepare(cp)
                        existing=list(out.glob('*/'+job_name))
                    if len(existing)!=1:raise ValueError('Ambiguous native preparation outputs')
                    job_path=existing[0]
                jsx=job_path.parent/jsx_name
                entry=dict(job=str(job_path),jsx=str(jsx),prepared=snapshot([job_path,jsx]))
                state['finishing'][stage]=entry;save()
            verify(entry['prepared'])
            completed=native_output(entry['job'],log)
            if completed is None:
                state['status']='WAITING_PREMIERE_'+stage.upper();save()
                print(state['status']+'\nOpen: '+read(entry['job'])['project']+'\nRun JSX: '+entry['jsx']+'\nThen repeat --action finish');return state
            entry['completed']=completed;save()
        state['status']='FINAL_REVIEW_REQUIRED' if until=='color' else 'STOPPED_AFTER_'+until.upper();save()
        print(state['status']+'\nVideo: '+read(state['finishing'][until]['job'])['video'])
        return state
    finally:lock.unlink()


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--action',choices=['start','revise','alternative','best-assembly','select','approve','finish','status','accept','closeout'],default='status')
    p.add_argument('--assembly-config',type=Path)
    p.add_argument('--alternative-config',type=Path);p.add_argument('--review-index',type=int)
    p.add_argument('--revision-config',type=Path);p.add_argument('--until',choices=['native','animation','transitions','color'],default='color')
    p.add_argument('--closeout-config',type=Path);p.add_argument('--apply',action='store_true')
    a=p.parse_args();run(a.config,a.action,a.revision_config,a.until,a.closeout_config,a.apply,a.alternative_config,a.review_index,a.assembly_config)
