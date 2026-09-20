"""Prepare guarded native Cross Dissolves on an assembled project copy."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import shutil
import sys
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.prepare_native_export import JSX
from tools.prepare_classification_input import digest, write_json
from tools.revise_edit_plan import validate_plan
from utils.premiere_project import load_premiere_project_root, find_project_sequence_node, build_project_object_id_lookup, build_project_object_uid_lookup, get_project_track_nodes
from utils.premiere_sequence_motion import _track_item_contexts

TICKS=254016000000


def select_transitions(plan, cfg):
    validate_plan(plan)
    fps=plan['fps']; seconds=cfg['duration_seconds']; minimum=cfg['minimum_clip_seconds']
    frames=float(seconds)*fps
    if not math.isfinite(frames) or frames<=0 or not frames.is_integer() or int(frames)%2 or not math.isfinite(minimum) or minimum<=seconds:
        raise ValueError('Duration must be positive, even frame count; minimum clip length must exceed duration')
    selected=[];skipped=[]
    for index,(left,right) in enumerate(zip(plan['clips'],plan['clips'][1:])):
        reason=None
        if left['kind']!='video' or right['kind']!='video': reason='not video-to-video'
        elif min(left['duration_seconds'],right['duration_seconds'])<minimum: reason='short adjacent clip'
        elif left['path']==right['path']: reason='same source'
        else:
            tail=left['source_placement']['source_out_ticks']/TICKS-left['source_out_seconds']
            head=right['source_in_seconds']-right['source_placement']['source_in_ticks']/TICKS
            if min(tail,head)+1e-8<seconds/2: reason='insufficient verified source handles'
        entry=dict(left_index=index,right_index=index+1,cut_frame=right['timeline_start_frame'],duration_frames=int(frames))
        if reason: skipped.append(dict(entry,reason=reason))
        else:selected.append(entry)
    return selected,skipped


APPLY=r'''
 step('transition preflight');
 app.project.openSequence(seq.sequenceID);
 if(seq.videoTracks[0].transitions.numItems!==0)throw Error('Existing transitions: refusing duplicate application');
 app.enableQE();
 var transition=qe.project.getVideoTransitionByName('Cross Dissolve');
 if(!transition)throw Error('Cross Dissolve unavailable');
 for(var ti=0;ti<job.transitions.length;ti++){
  var spec=job.transitions[ti];
  step('TRANSITION '+(ti+1)+'/'+job.transitions.length+' at '+spec.cut_frame/job.plan.fps+'s');
  var qt=qe.project.getActiveSequence().getVideoTrackAt(0),incoming=null;
  for(var qi=0;qi<qt.numItems;qi++){
   var qitem=qt.getItemAt(qi);
   if(qitem.type==='Clip' && closeEnough(qitem.start.ticks,spec.cut_frame*ticks/job.plan.fps)){
    if(incoming)throw Error('Ambiguous QE clip');incoming=qitem;
   }
  }
  if(!incoming)throw Error('Incoming QE clip not found');
  var duration=atTicks(spec.duration_frames*ticks/job.plan.fps).getFormatted(seq.getSettings().videoFrameRate,101);
  var count=seq.videoTracks[0].transitions.numItems;
  incoming.addTransition(transition,true,duration,'0:00:00:00',0.5,false,true);
  if(seq.videoTracks[0].transitions.numItems!==count+1)throw Error('Transition creation not confirmed');
  var found=0;
  for(var ri=0;ri<seq.videoTracks[0].transitions.numItems;ri++){
   var tr=seq.videoTracks[0].transitions[ri];
   if(closeEnough(tr.start.ticks,(spec.cut_frame-spec.duration_frames/2)*ticks/job.plan.fps) &&
      closeEnough(tr.end.ticks,(spec.cut_frame+spec.duration_frames/2)*ticks/job.plan.fps))found++;
  }
  if(found!==1)throw Error('Transition bounds mismatch; export stopped');
  log('TRANSITION VERIFIED at '+spec.cut_frame/job.plan.fps+'s');
 }
'''


def prepare(path,dry_run=False):
    cfg=json.loads(path.read_text(encoding='utf-8-sig'));base=path.resolve().parent
    if cfg.get('schema_version')!=1:raise ValueError('Schema version 1 required')
    job=json.loads((base/cfg['source_job']).read_text(encoding='utf-8'))
    selected,skipped=select_transitions(job['plan'],cfg)
    if not selected:raise ValueError('No eligible transitions')
    source=Path(job['project']);before=digest(source)
    root=load_premiere_project_root(source);ids=build_project_object_id_lookup(root);uids=build_project_object_uid_lookup(root)
    seq=find_project_sequence_node(root,job['sequence'])
    if seq is None:raise ValueError('Missing source sequence')
    for _,track in get_project_track_nodes(seq,track_group_index=0,object_id_lookup=ids,object_uid_lookup=uids):
        transitions=track.find('./ClipTrack/TransitionItems')
        if transitions is not None and any(n.get("ObjectRef") for n in transitions.iter()):raise ValueError('Existing transitions')
    actual=_track_item_contexts(seq,group_index=0,id_lookup=ids,uid_lookup=uids,project_path=source)
    plan=job['plan'];fps=plan['fps']
    if len(actual)!=len(plan['clips']):raise ValueError('Clip count mismatch')
    for a,c in zip(actual,plan['clips']):
        if (a.track_index!=0 or Path(a.source_path)!=Path(c['path']) or a.start!=c['timeline_start_frame']*TICKS//fps
            or a.end!=(c['timeline_start_frame']+c['frames'])*TICKS//fps or abs(a.source_in-c['source_in_seconds']*TICKS)>1):raise ValueError('Timeline mismatch')
        if digest(c['path'])!=c['sha256']:raise ValueError('Media changed')
    if dry_run:
        print('PRECHECK PASS: '+str(len(selected))+' transitions; '+str(len(skipped))+' skipped');return
    out=(base/cfg['output_root'])/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]);out.mkdir(parents=True)
    copied=out/'Ben26_V3_ANIMATED_TRANSITIONS.prproj';shutil.copyfile(source,copied)
    if digest(source)!=before or digest(copied)!=before:raise ValueError('Source changed during copy')
    job.update(project=copied.as_posix(),video=(out/'animated_transitions_1080p.mp4').as_posix(),transitions=selected)
    head=JSX[:JSX.index(' for(i=0;i<seq.videoTracks.numTracks;i++)if')]
    tail=JSX[JSX.index(" step('verify all final video placements');"):]
    validation=tail[:tail.index(" step('activate sequence');")]
    script=(head+validation+APPLY+tail).replace('__JOB__',json.dumps(job,ensure_ascii=True))
    (out/'transitions_export.jsx').write_text(script,encoding='utf-8')
    write_json(out/'transition_job.json',job)
    write_json(out/'transition_plan.json',dict(config=cfg,selected=selected,skipped=skipped,status='PREPARED_NATIVE_NOT_RUN',source_sha256=before))
    print('PREPARED_NATIVE_NOT_RUN\n'+str(copied)+'\n'+str(out/'transitions_export.jsx'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--dry-run',action='store_true')
    a=p.parse_args();prepare(a.config,a.dry_run)
