"""Prepare editable native photo motion on a copy of an assembled project."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.prepare_native_export import JSX
from tools.prepare_classification_input import digest, write_json
from tools.revise_edit_plan import validate_plan
from utils.premiere_project import load_premiere_project_root, find_project_sequence_node, build_project_object_id_lookup, build_project_object_uid_lookup
from utils.premiere_sequence_motion import _track_item_contexts

TICKS = 254016000000


def motion_keys(frames, peak, zoom, retreat):
    if frames < 3 or not 0 < peak < 1 or not 0 < zoom <= .25 or not 0 <= retreat <= 1:
        raise ValueError('Invalid motion profile')
    mid = max(1, min(frames-2, round((frames-1)*peak)))
    keys = []
    for frame in range(frames):
        t = frame/mid if frame <= mid else (frame-mid)/(frames-1-mid)
        eased = t*t*(3-2*t)
        travel = eased if frame <= mid else 1-retreat*eased
        keys.append([frame, 1+zoom*travel])
    return keys


ANIMATE = r'''
 step('preflight photo motion');
 var targets=[];
 for(var mi=0;mi<job.motion.length;mi++){
  var spec=job.motion[mi],photo=seq.videoTracks[0].clips[spec.index],scale=null;
  for(var ci=0;ci<photo.components.numItems;ci++){
   var component=photo.components[ci];
   if(component.matchName==='AE.ADBE Motion' || component.displayName==='Motion'){
    for(var pi=0;pi<component.properties.numItems;pi++)if(component.properties[pi].displayName==='Scale')scale=component.properties[pi];
   }
  }
  if(!scale || !scale.areKeyframesSupported())throw Error('Scale property unavailable: '+photo.name);
  if(scale.isTimeVarying())throw Error('Existing animated Scale: '+photo.name);
  var baseline=Number(scale.getValue());
  if(!isFinite(baseline) || baseline<=0)throw Error('Invalid Scale baseline');
  targets.push({scale:scale,baseline:baseline,spec:spec,photo:photo});
 }
 for(var mi=0;mi<targets.length;mi++){
  var target=targets[mi],scale=target.scale,spec=target.spec;
  step('animate photo '+(mi+1)+'/'+targets.length+' '+target.photo.name);
  scale.setTimeVarying(true);
  for(var ki=0;ki<spec.keys.length;ki++){
   var key=spec.keys[ki],kt=atTicks(Number(target.photo.inPoint.ticks)+key[0]*ticks/job.plan.fps);
   var value=target.baseline*key[1];
   scale.addKey(kt);scale.setValueAtKey(kt,value,true);
   if(Math.abs(Number(scale.getValueAtTime(kt))-value)>.02)throw Error('Scale key readback mismatch');
  }
  var actualKeys=scale.getKeys();
  if(!actualKeys || actualKeys.length!==spec.keys.length)throw Error('Scale key count mismatch');
  log('MOTION VERIFIED '+target.photo.name+' baseline='+target.baseline+' keys='+actualKeys.length);
 }
'''


def prepare(path, dry_run=False):
    cfg=json.loads(path.read_text(encoding='utf-8-sig'))
    if cfg.get('schema_version') != 1:
        raise ValueError('Expected schema version 1')
    base=path.resolve().parent
    job=json.loads((base/cfg['source_job']).read_text(encoding='utf-8'))
    source=Path(job['project']); before=digest(source)
    validate_plan(job['plan'])
    root=load_premiere_project_root(source)
    ids,uids=build_project_object_id_lookup(root),build_project_object_uid_lookup(root)
    seq=find_project_sequence_node(root,job['sequence'])
    if seq is None: raise ValueError('Missing sequence')
    contexts=_track_item_contexts(seq,group_index=0,id_lookup=ids,uid_lookup=uids,project_path=source)
    clips=job['plan']['clips'];fps=job['plan']['fps']
    if len(contexts)!=len(clips): raise ValueError('Assembled clip count mismatch')
    motion=[]
    for index,(actual,expected) in enumerate(zip(contexts,clips)):
        if (actual.track_index!=0 or Path(actual.source_path)!=Path(expected['path'])
            or actual.start!=expected['timeline_start_frame']*TICKS//fps
            or actual.end!=(expected['timeline_start_frame']+expected['frames'])*TICKS//fps
            or abs(actual.source_in-expected['source_in_seconds']*TICKS)>1):
            raise ValueError('Assembled placement mismatch: '+expected['id'])
        if digest(expected['path'])!=expected['sha256']: raise ValueError('Media changed')
        if expected['kind']=='image':
            keys=motion_keys(expected['frames'],cfg['peak_fraction'],cfg['zoom_fraction'],cfg['return_fraction'])
            motion.append(dict(index=index,id=expected['id'],keys=keys))
    if not motion: raise ValueError('No photos to animate')
    if dry_run:
        print(f'PRECHECK PASS: {len(motion)} photos, timeline unchanged');return
    out=(base/cfg['output_root'])/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]);out.mkdir(parents=True)
    copied=out/'Ben26_V3_ANIMATED.prproj';shutil.copyfile(source,copied)
    if digest(source)!=before or digest(copied)!=before: raise ValueError('Source changed during copy')
    job.update(project=copied.as_posix(),video=(out/'animated_1080p.mp4').as_posix(),motion=motion)
    head=JSX[:JSX.index(' for(i=0;i<seq.videoTracks.numTracks;i++)if')]
    tail=JSX[JSX.index(" step('verify all final video placements');"):]
    marker=" step('activate sequence');"
    tail=tail.replace(marker,ANIMATE+'\n'+marker)
    script=(head+tail).replace('__JOB__',json.dumps(job,ensure_ascii=True))
    (out/'animate_export.jsx').write_text(script,encoding='utf-8')
    write_json(out/'animation_job.json',job)
    write_json(out/'preparation_result.json',dict(status='PREPARED_NATIVE_NOT_RUN',source_project=str(source),source_sha256=before,photos=len(motion),project=str(copied),jsx=str(out/'animate_export.jsx'),profile=cfg))
    print('PREPARED_NATIVE_NOT_RUN\n'+str(copied)+'\n'+str(out/'animate_export.jsx'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--dry-run',action='store_true')
    a=p.parse_args();prepare(a.config,a.dry_run)
