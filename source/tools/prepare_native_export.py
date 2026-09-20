"""Prepare a protected project copy and native Premiere assembly/export script."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.prepare_classification_input import digest, write_json
from utils.premiere_project import (load_premiere_project_root, find_project_sequence_node,
    build_project_object_id_lookup, build_project_object_uid_lookup, get_project_track_nodes,
    iter_project_track_item_refs)

JSX = r'''
(function(){
var job=__JOB__, ticks=254016000000, operation='startup';
var base=new File($.fileName).parent;
function log(s){var f=new File(base.fsName+'/native_progress.log');f.encoding='UTF-8';f.open('a');f.writeln(new Date().toString()+' '+s);f.close();}
function state(s){var f=new File(base.fsName+'/native_status.txt');f.open('w');f.write(s);f.close();log(s);}
function same(a,b){return String(a).replace(/\\/g,'/').toLowerCase()===String(b).replace(/\\/g,'/').toLowerCase();}
function step(s){operation=s;log('STEP '+s);}
function find(node,path){if(node.getMediaPath && same(node.getMediaPath(),path))return node; if(node.children)for(var i=0;i<node.children.numItems;i++){var x=find(node.children[i],path);if(x)return x;}return null;}
function atTicks(n){var t=new Time();t.ticks=String(Math.round(n));return t;}
function closeEnough(a,b){return Math.abs(Number(a)-Number(b))<=ticks/job.plan.fps/2;}
try{
 step('check project');
 if(!app.project || !same(app.project.path,job.project))throw Error('Open the prepared project COPY first: '+job.project);
 if(new File(job.video).exists)throw Error('Output exists; refusing overwrite');
 if(!new File(job.preset).exists)throw Error('Missing export preset');
 step('find target sequence');
 var seq=null,i,j;
 for(i=0;i<app.project.sequences.numSequences;i++)if(app.project.sequences[i].name===job.sequence){if(seq)throw Error('Duplicate sequence name');seq=app.project.sequences[i];}
 if(!seq)throw Error('Target sequence missing');
 for(i=0;i<seq.videoTracks.numTracks;i++)if(seq.videoTracks[i].clips.numItems)throw Error('Target video tracks must be empty');
 for(i=0;i<seq.audioTracks.numTracks;i++)if(seq.audioTracks[i].clips.numItems)throw Error('Target audio tracks must be empty');
 if(!seq.videoTracks.numTracks || !seq.audioTracks.numTracks)throw Error('V1 and A1 required');
 step('check existing timebase');
 if(Math.abs(Number(seq.timebase)-ticks/job.plan.fps)>1)throw Error('Source target sequence must already use '+job.plan.fps+' fps');
 step('get sequence settings');
 var settings=seq.getSettings();
 log('SETTINGS width='+settings.videoFrameWidth+' height='+settings.videoFrameHeight+' PAR='+settings.videoPixelAspectRatio+' PARtype='+typeof settings.videoPixelAspectRatio+' field='+settings.videoFieldType+' fieldType='+typeof settings.videoFieldType);
 // Preserve Adobe-owned Time, aspect-ratio and field objects/types verbatim.
 step('set width');settings.videoFrameWidth=Number(job.width);
 step('set height');settings.videoFrameHeight=Number(job.height);
 step('apply sequence dimensions');seq.setSettings(settings);
 if(seq.frameSizeHorizontal!==job.width || seq.frameSizeVertical!==job.height || !closeEnough(seq.timebase,ticks/job.plan.fps))throw Error('Sequence settings readback failed');
 step('create import bin');
 var bin=app.project.rootItem.createBin('Native_review_sources_'+new Date().getTime());
 for(i=0;i<job.plan.clips.length;i++){
  var c=job.plan.clips[i];state('ASSEMBLING '+(i+1)+'/'+job.plan.clips.length+' '+c.id);
  if(!new File(c.path).exists)throw Error('Offline media: '+c.path);
  step('import '+c.id);
  if(!app.project.importFiles([c.path],true,bin,false))throw Error('Import failed: '+c.path);
  var item=find(bin,c.path);if(!item)throw Error('Imported item not found in dedicated bin');
  step('scale to frame '+c.id);item.setScaleToFrameSize();
  step('set source IN '+c.id);item.setInPoint(c.source_in_seconds,4);
  step('set source OUT '+c.id);item.setOutPoint(c.source_in_seconds+c.duration_seconds,4);
  step('overwrite clip '+c.id);
  seq.overwriteClip(item,String(Math.round(c.timeline_start_frame*ticks/job.plan.fps)),0,0);
  step('read inserted clip '+c.id);
  var clip=null;
  for(var vi=0;vi<seq.videoTracks[0].clips.numItems;vi++){
   var candidate=seq.videoTracks[0].clips[vi];
   if(same(candidate.projectItem.getMediaPath(),c.path) && closeEnough(candidate.start.ticks,c.timeline_start_frame*ticks/job.plan.fps)){
    if(clip)throw Error('Ambiguous inserted clip: '+c.id);
    clip=candidate;
   }
  }
  if(!clip)throw Error('No inserted clip for '+c.id);
  log('BOUNDS BEFORE '+c.id+' path='+clip.projectItem.getMediaPath()+' start='+clip.start.ticks+' end='+clip.end.ticks+' IN='+clip.inPoint.ticks+' OUT='+clip.outPoint.ticks);
  if(!same(clip.projectItem.getMediaPath(),c.path))throw Error('Inserted source path mismatch');
  // Explicit native TrackItem bounds avoid relying on import/default still duration.
  step('conform source bounds '+c.id);
  clip.inPoint=atTicks(c.source_in_seconds*ticks);
  clip.outPoint=atTicks((c.source_in_seconds+c.duration_seconds)*ticks);
  step('conform timeline bounds '+c.id);
  clip.start=atTicks(c.timeline_start_frame*ticks/job.plan.fps);
  clip.end=atTicks((c.timeline_start_frame+c.frames)*ticks/job.plan.fps);
  // Conform only linked source audio inserted at this same planned start.
  for(var ai=0;ai<seq.audioTracks.numTracks;ai++)for(var aj=0;aj<seq.audioTracks[ai].clips.numItems;aj++){
   var ac=seq.audioTracks[ai].clips[aj];
   if(c.kind==='video' && same(ac.projectItem.getMediaPath(),c.path) && closeEnough(ac.start.ticks,c.timeline_start_frame*ticks/job.plan.fps)){
    ac.inPoint=atTicks(c.source_in_seconds*ticks);ac.outPoint=atTicks((c.source_in_seconds+c.duration_seconds)*ticks);
    ac.start=atTicks(c.timeline_start_frame*ticks/job.plan.fps);ac.end=atTicks((c.timeline_start_frame+c.frames)*ticks/job.plan.fps);
   }
  }
  log('BOUNDS AFTER '+c.id+' start='+clip.start.ticks+' end='+clip.end.ticks+' IN='+clip.inPoint.ticks+' OUT='+clip.outPoint.ticks);
  step('verify inserted clip '+c.id);
  if(!clip || !same(clip.projectItem.getMediaPath(),c.path) || !closeEnough(clip.start.ticks,c.timeline_start_frame*ticks/job.plan.fps) || !closeEnough(clip.end.ticks,(c.timeline_start_frame+c.frames)*ticks/job.plan.fps))throw Error('Native timeline bounds mismatch: '+c.id);
  if(c.kind==='video' && !closeEnough(clip.inPoint.ticks,c.source_in_seconds*ticks))throw Error('Native source IN mismatch');
 }
 step('verify all final video placements');
 for(i=0;i<job.plan.clips.length;i++){
  var expected=job.plan.clips[i],actual=seq.videoTracks[0].clips[i];
  if(!actual || !same(actual.projectItem.getMediaPath(),expected.path) ||
     !closeEnough(actual.start.ticks,expected.timeline_start_frame*ticks/job.plan.fps) ||
     !closeEnough(actual.end.ticks,(expected.timeline_start_frame+expected.frames)*ticks/job.plan.fps) ||
     (expected.kind==='video' && !closeEnough(actual.inPoint.ticks,expected.source_in_seconds*ticks)))throw Error('Final video placement mismatch: '+expected.id);
 }
 if(seq.videoTracks[0].clips.numItems!==job.plan.clips.length || !closeEnough(seq.end,job.plan.frames*ticks/job.plan.fps))throw Error('Final duration/count mismatch');
 for(i=0;i<seq.audioTracks.numTracks;i++)for(j=0;j<seq.audioTracks[i].clips.numItems;j++){
  var a=seq.audioTracks[i].clips[j],matched=false;
  if(a.projectItem.isOffline())throw Error('Offline audio');
  for(var k=0;k<job.plan.clips.length;k++){var c=job.plan.clips[k];if(c.kind==='video' && same(a.projectItem.getMediaPath(),c.path) && closeEnough(a.start.ticks,c.timeline_start_frame*ticks/job.plan.fps) && closeEnough(a.end.ticks,(c.timeline_start_frame+c.frames)*ticks/job.plan.fps))matched=true;}
  if(!matched)throw Error('Unexpected audio placement');
 }
 step('activate sequence');app.project.openSequence(seq.sequenceID);step('save project copy');app.project.save();state('NATIVE_TIMELINE_CHECKED_EXPORTING');
 step('native export');
 if(seq.frameSizeHorizontal!==job.width || seq.frameSizeVertical!==job.height)throw Error('Export dimensions mismatch');
 var outputFile=new File(job.video),presetFile=new File(job.preset);
 log('EXPORT path='+outputFile.fsName+' preset='+presetFile.fsName);
 var result=seq.exportAsMediaDirect(outputFile.fsName,presetFile.fsName,0);
 log('EXPORT returned='+String(result));
 var vf=new File(job.video);if(!vf.exists || vf.length===0)throw Error('Export output missing; return='+result);
 state('EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA');
 alert('Native export file created. Review video and audio: '+job.video);
}catch(e){var detail='operation='+operation+'; line='+e.line+'; '+String(e);state('FAILED '+detail);alert('Native export failed: '+detail);throw e;}
})();
'''


def prepare(path, dry_run=False):
    path=path.resolve(); cfg=json.loads(path.read_text(encoding='utf-8-sig'))
    if cfg.get('schema_version') != 1:
        raise ValueError('Schema version 1 required')
    local=lambda s: (path.parent/s).resolve()
    project, plan_path, preset=map(local,(cfg['project'],cfg['edit_plan'],cfg['preset']))
    if not preset.is_file():
        raise FileNotFoundError(preset)
    plan=json.loads(plan_path.read_text(encoding='utf-8'))
    from tools.revise_edit_plan import validate_plan
    validate_plan(plan)
    if plan.get('schema_version')!=1 or not plan.get('clips') or plan.get('fps')!=25:
        raise ValueError('Expected nonempty 25 fps edit plan')
    cursor=0
    for c in plan['clips']:
        if c['timeline_start_frame']!=cursor or c['frames']!=c['duration_seconds']*plan['fps'] or c['frames']<=0:
            raise ValueError('Invalid timeline')
        if digest(c['path'])!=c['sha256']:
            raise ValueError('Media changed: '+c['id'])
        cursor+=c['frames']
    if cursor!=plan['frames']:
        raise ValueError('Total frames mismatch')
    before=digest(project);root=load_premiere_project_root(project)
    target=find_project_sequence_node(root,cfg['target_sequence'])
    if target is None:
        raise ValueError('Target sequence missing')
    ids,uids=build_project_object_id_lookup(root),build_project_object_uid_lookup(root)
    for group in (0,1):
        for _,track in get_project_track_nodes(target,track_group_index=group,object_id_lookup=ids,object_uid_lookup=uids):
            if list(iter_project_track_item_refs(track)):
                raise ValueError('Target sequence is not empty')
    if dry_run:
        print('PRECHECK PASS; no project copied or Adobe started');return
    out=local(cfg['output_root'])/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]);out.mkdir(parents=True)
    copied=out/(project.stem+'_NATIVE_REVIEW.prproj');shutil.copyfile(project,copied)
    if digest(copied)!=before or digest(project)!=before:
        raise ValueError('Project changed during copy')
    job=dict(project=copied.as_posix(),sequence=cfg['target_sequence'],plan=plan,
             video=(out/'native_1080p.mp4').as_posix(),preset=preset.as_posix(),width=cfg['width'],height=cfg['height'])
    jsx=out/'assemble_export.jsx';jsx.write_text(JSX.replace('__JOB__',json.dumps(job,ensure_ascii=True)),encoding='utf-8')
    write_json(out/'job.json',job)
    write_json(out/'preparation_result.json',dict(status='PREPARED_NATIVE_NOT_RUN',source_project=str(project),
        source_sha256=before,edit_plan_sha256=digest(plan_path),project=str(copied),jsx=str(jsx)))
    print('PREPARED_NATIVE_NOT_RUN\nOpen project copy in Premiere: '+str(copied)+'\nWindow > Extensions > Run Transition Script\nRun JSX: '+str(jsx))



def prepare_export_only(job_path):
    job_path = job_path.resolve()
    job = json.loads(job_path.read_text(encoding='utf-8'))
    preset = Path(job['preset']).parent / '00 - Match Source - High bitrate.epr'
    if not preset.is_file():
        raise FileNotFoundError(preset)
    job['preset'] = str(preset)
    job['video'] = str(job_path.parent / 'native_1080p_retry.mp4')
    if Path(job['video']).exists():
        raise FileExistsError(job['video'])
    # Retain guards and final timeline validation, omit all assembly/mutation.
    head = JSX[:JSX.index(' for(i=0;i<seq.videoTracks.numTracks;i++)if')]
    tail = JSX[JSX.index(" step('verify all final video placements');"):]
    tail = tail.replace("step('save project copy');app.project.save();", "")
    tail = tail.replace("var result=seq.exportAsMediaDirect(job.video,job.preset,0);",
        "var settings=seq.getSettings();log('EXPORT width='+seq.frameSizeHorizontal+' height='+seq.frameSizeVertical+' timebase='+seq.timebase);\n"
        " if(seq.frameSizeHorizontal!==job.width || seq.frameSizeVertical!==job.height)throw Error('Export dimensions mismatch');\n"
        " var outputFile=new File(job.video),presetFile=new File(job.preset);\n"
        " log('EXPORT path='+outputFile.fsName+' preset='+presetFile.fsName);\n"
        " var extension=seq.getExportFileExtension(presetFile.fsName);log('EXPORT extension='+extension);\n"
        " var result=seq.exportAsMediaDirect(outputFile.fsName,presetFile.fsName,0);log('EXPORT returned='+String(result));")
    script = (head + tail).replace('__JOB__',json.dumps(job,ensure_ascii=True))
    script = script.replace('native_progress.log','export_only_progress.log').replace('native_status.txt','export_only_status.txt')
    target=job_path.parent/'export_only.jsx';target.write_text(script,encoding='utf-8')
    print('EXPORT ONLY, native execution pending: '+str(target))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path);p.add_argument('--export-only-job',type=Path);p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    if a.export_only_job:
        prepare_export_only(a.export_only_job)
    elif a.config:
        prepare(a.config,a.dry_run)
    else:
        p.error('--config or --export-only-job required')
