"""Prepare task-owned FINISH from an approved branch checkpoint; never starts Adobe."""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from family_contract import read, sha, verify
from ingest import write_json
from tools.prepare_native_finish import HELPERS
from utils import premiere_project as pp
from utils.premiere_sequence_motion import (_track_item_contexts, _motion_params,
    _meaningful_existing_motion, _baseline_scale, _baseline_position)

TICKS = 254016000000

def prepare(task, dry=False):
    cfg = read(task / 'task.json')
    approval = read(task / 'pipeline/structure_accepted.json')
    if approval['status'] != 'ACCEPTED':
        raise ValueError('Structure approval required')
    if approval['config_sha256'] != sha(task/'task.json'):
        raise ValueError('Structure approval config is stale')
    verify(approval['artifacts'])
    handoff = read(task / 'pipeline/BRANCH_PREMIERE_HANDOFF/handoff.json')
    creative = read(task / 'visual_finish/creative_plan.json')
    if creative['task_id'] != task.name or cfg['task_id'] != task.name:
        raise ValueError('Task mismatch')
    source = Path(handoff['project'])
    if str(source) not in approval['artifacts']:
        raise ValueError('Source project not covered by structure approval')
    project = Path(cfg['classify']['permanent_project_dir'])/'projects'/(task.name + '_VISUAL_FINISH_01.prproj')
    package = Path(cfg['classify']['permanent_project_dir']) / 'VISUAL_FINISH' / (task.name + '_VISUAL_FINISH_01')
    work = task / 'visual_finish'
    root = pp.load_premiere_project_root(source)
    ids = pp.build_project_object_id_lookup(root)
    uids = pp.build_project_object_uid_lookup(root)
    versions = []
    for j in handoff['jobs']:
        plan = j['plan']; label = plan['branch']
        seq = pp.find_project_sequence_node(root, j['sequence'])
        if seq is None: raise ValueError('Missing source sequence')
        contexts = _track_item_contexts(seq, group_index=0, id_lookup=ids, uid_lookup=uids, project_path=source)
        if len(contexts) != len(plan['clips']): raise ValueError('Clip count changed')
        motion = []
        for i, (ctx, clip) in enumerate(zip(contexts, plan['clips'])):
            if Path(ctx.source_path) != Path(clip['path']) or not Path(clip['path']).is_file():
                raise ValueError('Source mismatch ' + clip['id'])
            for actual, expected in [(ctx.start, clip['timeline_start_frame']/25),
                    (ctx.end, (clip['timeline_start_frame']+clip['frames'])/25),
                    (ctx.source_in, clip['source_in_seconds']), (ctx.source_out, clip['source_out_seconds'])]:
                if abs(actual/TICKS-expected) > .021: raise ValueError('Timing mismatch ' + clip['id'])
            spec = creative['motion'][label].get(clip['id'])
            if not spec: continue
            params = _motion_params(ctx.track_item_node, ids)
            if clip['kind'] != 'image' or _meaningful_existing_motion(params)[0]:
                raise ValueError('Refusing existing animation/non-image ' + clip['id'])
            z0, z1, reason = spec
            if not 1 <= min(z0,z1) <= max(z0,z1) <= 1.06: raise ValueError('Unsafe motion range')
            motion.append(dict(index=i, id=clip['id'], kind='art' if clip['art_type'] else 'photo',
                keys=[[0,z0,.5,.5],[1,z1,.5,.5]], reason=reason,
                baseline_scale=_baseline_scale(params.scale), baseline_position=_baseline_position(params.position)))
        if {m['id'] for m in motion} != set(creative['motion'][label]): raise ValueError('Unknown motion IDs')
        output = task.name + '_' + label + '_FINISH_01'
        versions.append(dict(label=label, source_sequence=j['sequence'], output_sequence=output,
            plan=plan, motion=motion, transitions=[], preset=str(package/(task.name+'_'+label+'_review.epr')),
            original_preset=j['preset'], review=str(package/(output+'_REVIEW.mp4'))))
    job = dict(task_id=task.name, project=str(project), source_project=str(source), source_sha256=sha(source),
        versions=versions, known_issues=creative['known_issues'], color_next=creative['color_next'])
    report = dict(status='DRY_RUN_PASS', source_sha256=sha(source),
        versions=[dict(branch=v['label'], clips=len(v['plan']['clips']), motions=len(v['motion']),
            seconds=sum(c['frames'] for c in v['plan']['clips'])/25) for v in versions],
        native_executed=False, color_applied=False)
    template = (ROOT/'scripts/visual_finish_native.jsx').read_text(encoding='utf-8')
    template = template.replace('/3840', '/seq.frameSizeHorizontal').replace('/2160', '/seq.frameSizeVertical')
    template = template.replace(".replace(/\\\\/g,'/').toLowerCase()", ".replace(/\\\\/g,'/').replace(/\\/+/g,'/').toLowerCase()")
    template = template.replace('new File(masterJob.preset)', 'new File(v.preset)')
    template = template.replace('FULL and SHORT finish reviews created.', 'MAIN and SHORT finish reviews created.')
    template = template.replace("if(!isFinite(baseline)", "if(Math.abs(baseline-m.baseline_scale)>.02||Math.abs(position[0]-m.baseline_position[0])>.0001||Math.abs(position[1]-m.baseline_position[1])>.0001)throw Error('Motion baseline changed '+m.id);\n  if(!isFinite(baseline)")
    # Validate every clip before cloning, not just those receiving Motion.
    guard = """
 for(var vi=0;vi<masterJob.versions.length;vi++){
  var vv=masterJob.versions[vi],ss=findSeq(vv.source_sequence);
  if(ss.videoTracks[0].clips.numItems!==vv.plan.clips.length)throw Error('Clip count changed');
  for(var ci=0;ci<vv.plan.clips.length;ci++){
   var cc=ss.videoTracks[0].clips[ci],rr=vv.plan.clips[ci];
   if(cc.projectItem.isOffline()||!same(cc.projectItem.getMediaPath(),rr.path)||
      !closeEnough(cc.start.ticks,rr.timeline_start_frame*ticks/25)||
      !closeEnough(cc.end.ticks,(rr.timeline_start_frame+rr.frames)*ticks/25)||
      !closeEnough(cc.inPoint.ticks,rr.source_in_seconds*ticks)||
      !closeEnough(cc.outPoint.ticks,rr.source_out_seconds*ticks))throw Error('Conform '+rr.id);
  }
 }
"""
    template = template.replace('for(i=0;i<app.project.sequences.numSequences;i++)', guard+'\n for(i=0;i<app.project.sequences.numSequences;i++)', 1)
    script = template.replace('__HELPERS__', HELPERS[:HELPERS.index('function checkpoint')]).replace('__TRANSITIONS__','').replace('__JOB__',json.dumps(job,ensure_ascii=True))
    if '__JOB__' in script or '__HELPERS__' in script: raise ValueError('Unresolved template')
    work.mkdir(parents=True,exist_ok=True)
    write_json(work/'dry_run.json',report)
    (work/'native_syntax_check.js').write_text(script,encoding='utf-8')
    if dry:
        print(json.dumps(report)); return
    if project.exists() or package.exists(): raise ValueError('Existing FINISH package; refusing overwrite')
    package.mkdir(parents=True)
    project.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,project)
    if sha(project)!=job['source_sha256']: raise ValueError('Project copy hash mismatch')
    for v in versions: shutil.copy2(v['original_preset'],v['preset'])
    write_json(work/'visual_finish.json',job); write_json(package/'visual_finish.json',job)
    write_json(package/'creative_plan.json',creative)
    jsx=package/(task.name+'_apply_visual_finish.jsx'); jsx.write_text(script,encoding='utf-8')
    monitor=package/(task.name+'_monitor.cmd')
    monitor.write_text('@echo off\r\ncd /d "'+str(ROOT)+'"\r\npython scripts\\family_finish_monitor.py '+task.name+'\r\npause\r\n',encoding='utf-8')
    status=dict(task_id=task.name,status='USER_ACTION_REQUIRED',stage='VISUAL_FINISH',project=str(project),jsx=str(jsx),
        monitor=str(monitor),plan=str(package/'visual_finish.json'),native_executed=False,color_applied=False,
        source_sha256=job['source_sha256'],next='USER RUNS JSX; CODEX CHECKS FINISH REVIEW INCLUDING BLACK SHOT; THEN COLOR',
        resume_command='python scripts/family_finish_monitor.py '+task.name+' --snapshot')
    write_json(work/'status.json',status);write_json(package/'preparation_status.json',status)
    state=read(task/'state.json');state.update(stage='VISUAL_FINISH',status='USER_ACTION_REQUIRED',visual_finish=status,next_stage=status['next'])
    state.setdefault('stages',{})['VISUAL_FINISH']='PREPARED_NATIVE_NOT_RUN';write_json(task/'state.json',state)
    if sha(source)!=job['source_sha256']: raise ValueError('Original source changed')
    print(json.dumps(status,ensure_ascii=True,indent=2))

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('task');parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();prepare(ROOT/'tasks'/args.task,args.dry_run)
