"""Prepare two editable Lumetri layers: shot correction and narrative look."""
import argparse
from datetime import datetime
import io
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.prepare_classification_input import digest,write_json
from tools.prepare_native_export import JSX
from tools.revise_edit_plan import validate_plan
from tools.classify_source_package import ProgressLog
from utils.video_frame_extract import resolve_ffmpeg_executable
from utils.premiere_project import load_premiere_project_root,find_project_sequence_node,build_project_object_id_lookup,build_project_object_uid_lookup
from utils.premiere_sequence_motion import _track_item_contexts

LIMITS={'Temperature':(-15,15),'Tint':(-15,15),'Exposure':(-1,1),'Contrast':(-30,30),
        'Highlights':(-50,50),'Shadows':(-50,50),'Whites':(-30,30),'Blacks':(-30,30),'Saturation':(70,120)}
LOOKS={
 'neutral':{},
 'warm_family':{'Temperature':6,'Contrast':4,'Highlights':-8,'Shadows':5,'Saturation':104},
 'bright_optimistic':{'Temperature':3,'Exposure':.12,'Contrast':6,'Highlights':-6,'Shadows':6,'Saturation':107},
 'quiet_reflective':{'Temperature':-3,'Contrast':-5,'Highlights':-10,'Shadows':3,'Saturation':92}}


def values(raw):
    for name,value in raw.items():
        if name not in LIMITS or type(value) not in (float,int) or not math.isfinite(value) or not LIMITS[name][0]<=value<=LIMITS[name][1]:
            raise ValueError('Unsupported/unsafe color parameter: '+name)
    return dict(raw)


def global_values(cfg):
    strength=cfg['strength']
    if type(strength) not in (int,float) or not math.isfinite(strength) or not 0<=strength<=1:raise ValueError('Look strength must be 0..1')
    if cfg['profile'] not in LOOKS:raise ValueError('Unknown look profile')
    raw=dict(LOOKS[cfg['profile']]);raw.update(cfg.get('overrides',{}));values(raw)
    return {k:round((100 if k=='Saturation' else 0)+(v-(100 if k=='Saturation' else 0))*strength,4) for k,v in raw.items()}


COLOR=r'''
 step('color preflight');app.project.openSequence(seq.sequenceID);app.enableQE();
 function lumetri(clip){var list=[];for(var i=0;i<clip.components.numItems;i++){var co=clip.components[i];if(co.matchName==='AE.ADBE Lumetri')list.push(co);}return list;}
 for(var cc=0;cc<seq.videoTracks[0].clips.numItems;cc++)if(lumetri(seq.videoTracks[0].clips[cc]).length)throw Error('Existing Lumetri: refusing cumulative correction');
 var effect=qe.project.getVideoEffectByName('Lumetri Color');if(!effect)throw Error('Lumetri Color unavailable');
 function applyValues(component,settings){
  for(var name in settings)if(settings.hasOwnProperty(name)){
   var property=null;
   // First named occurrence is the Basic Correction control; Creative Saturation may also exist.
   for(var pi=0;pi<component.properties.numItems;pi++)if(component.properties[pi].displayName===name){property=component.properties[pi];break;}
   if(!property)throw Error('Lumetri property missing: '+name);
   var baseline=Number(property.getValue()),neutral=name==='Saturation'?100:0;
   if(!isFinite(baseline)||Math.abs(baseline-neutral)>.01)throw Error('Unexpected Lumetri default for '+name+': '+baseline);
   property.setValue(settings[name],true);
   if(Math.abs(Number(property.getValue())-settings[name])>.02)throw Error('Lumetri readback failed: '+name);
  }
 }
 for(var colorIndex=0;colorIndex<job.color.length;colorIndex++){
  var spec=job.color[colorIndex],clip=seq.videoTracks[0].clips[spec.index];
  step('COLOR '+(colorIndex+1)+'/'+job.color.length+' '+spec.instance);
  for(var layer=0;layer<2;layer++){
   var qt=qe.project.getActiveSequence().getVideoTrackAt(0),target=null;
   for(var qi=0;qi<qt.numItems;qi++){var item=qt.getItemAt(qi);if(item.type==='Clip' && closeEnough(item.start.ticks,clip.start.ticks))target=item;}
   if(!target)throw Error('QE clip missing');target.addVideoEffect(effect);
   var effects=lumetri(clip);if(effects.length!==layer+1)throw Error('Lumetri insertion failed');
   applyValues(effects[layer],layer===0?spec.correction:job.global_look);
  }
  log('COLOR VERIFIED '+spec.instance+'; Lumetri 1=shot correction, 2=global look');
 }
'''


def prepare(path,dry_run=False):
    from PIL import Image
    path=path.resolve();cfg=json.loads(path.read_text(encoding='utf-8-sig'))
    if cfg.get('schema_version')!=1:raise ValueError('Schema version 1 required')
    if not cfg['creative_brief']['purpose'].strip():raise ValueError('Describe the purpose of the film')
    look=global_values(cfg['global_look'])
    overrides=cfg['individual'].get('overrides',{})
    for item in overrides.values():values(item)
    if cfg['individual']['exposure_mode'] not in ('suggest','apply_conservative','manual'):raise ValueError('Unknown exposure mode')
    cap=cfg['individual']['maximum_auto_exposure']
    if type(cap) not in (int,float) or not 0<=cap<=.3:raise ValueError('Auto exposure cap must be 0..0.3 stops')
    job=json.loads((path.parent/cfg['source_job']).read_text(encoding='utf-8'))
    plan=job['plan'];validate_plan(plan);source=Path(job['project']);before=digest(source)
    root=load_premiere_project_root(source);ids=build_project_object_id_lookup(root);uids=build_project_object_uid_lookup(root)
    seq=find_project_sequence_node(root,job['sequence'])
    if seq is None:raise ValueError('Sequence missing')
    contexts=_track_item_contexts(seq,group_index=0,id_lookup=ids,uid_lookup=uids,project_path=source)
    if len(contexts)!=len(plan['clips']):raise ValueError('Timeline changed; update edit plan before color')
    instances=[]
    for index,(actual,c) in enumerate(zip(contexts,plan['clips'])):
        if (actual.track_index!=0 or Path(actual.source_path)!=Path(c['path']) or abs(actual.start-c['timeline_start_frame']*254016000000/plan['fps'])>1
            or abs(actual.end-(c['timeline_start_frame']+c['frames'])*254016000000/plan['fps'])>1
            or abs(actual.source_in-c['source_in_seconds']*254016000000)>1):raise ValueError('Timeline bounds changed')
        if digest(c['path'])!=c['sha256']:raise ValueError('Source media changed')
        instances.append(c.get('clip_instance_id',c['id']+'_'+str(index)))
    if set(overrides)-set(instances):raise ValueError('Unknown clip instance override')
    if dry_run:print('PRECHECK PASS: '+str(len(contexts))+' clips; no project modified');return
    out=(path.parent/cfg['output_root'])/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]);out.mkdir(parents=True)
    log=ProgressLog(out/'analysis.log');ffmpeg=resolve_ffmpeg_executable();rows=[]
    for index,c in enumerate(plan['clips']):
        luminance=[]
        with log.activity('SAMPLE '+str(index+1)+'/'+str(len(contexts))+' '+instances[index]):
            for sample,fraction in enumerate([.2,.5,.8] if c['kind']=='video' else [0]):
                args=['-ss',str(c['source_in_seconds']+fraction*c['duration_seconds'])] if c['kind']=='video' else []
                result=subprocess.run([ffmpeg,'-v','error','-nostdin',*args,'-i',c['path'],'-frames:v','1','-vf','scale=320:-2','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True,timeout=60)
                image=Image.open(io.BytesIO(result.stdout)).convert('RGB');image.save(out/f'clip_{index+1:02}_{sample}.png')
                # Brightness proxy only; not a calibrated exposure or white-balance measurement.
                histogram=image.convert('L').histogram();luminance.append(sum(i*n for i,n in enumerate(histogram))/sum(histogram)/255)
        rows.append(dict(index=index,instance=instances[index],luminance=luminance,median=statistics.median(luminance)))
    reference=statistics.median(row['median'] for row in rows)
    for row in rows:
        proposal=round(max(-cap,min(cap,math.log2(max(reference,.02)/max(row['median'],.02)))),3)
        correction={'Exposure':proposal} if cfg['individual']['exposure_mode']=='apply_conservative' else {}
        correction.update(overrides.get(row['instance'],{}))
        row.update(suggested_exposure=proposal,correction=values(correction),review_required=True)
    if digest(source)!=before:raise ValueError('Source project changed during analysis')
    copied=out/'COLOR_REVIEW.prproj';shutil.copyfile(source,copied)
    if digest(copied)!=before:raise ValueError('Project copy mismatch')
    job.update(project=copied.as_posix(),video=(out/'color_1080p_review.mp4').as_posix(),color=rows,global_look=look,creative_brief=cfg['creative_brief'])
    head=JSX[:JSX.index(' for(i=0;i<seq.videoTracks.numTracks;i++)if')]
    tail=JSX[JSX.index(" step('verify all final video placements');"):]
    validation=tail[:tail.index(" step('activate sequence');")]
    script=(head+validation+COLOR+tail).replace('__JOB__',json.dumps(job,ensure_ascii=True))
    (out/'color_export.jsx').write_text(script,encoding='utf-8')
    write_json(out/'color_job.json',job);write_json(out/'color_plan.json',dict(config=cfg,clips=rows,global_look=look,
        status='COLOR_REVIEW_REQUIRED_NATIVE_NOT_RUN',source_sha256=before,
        limitations=['Luminance proxy is not semantic analysis or calibrated color measurement','No automatic white balance or skin detection','Reference and music descriptions are human direction, not automatically analyzed']))
    print('PREPARED_NATIVE_NOT_RUN\n'+str(copied)+'\n'+str(out/'color_export.jsx'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--dry-run',action='store_true')
    a=p.parse_args();prepare(a.config,a.dry_run)
