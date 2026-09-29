(function(){
var job=__JOB__,ticks=254016000000,frame=ticks/25,operation='startup';
var base=new File($.fileName).parent;
function log(s){var f=new File(base.fsName+'/native_progress.log');f.encoding='UTF-8';if(!f.open('a'))throw Error('Cannot write log');f.writeln(new Date().toString()+' '+s);f.close();}
function state(s){var f=new File(base.fsName+'/native_status.txt');f.open('w');f.write(s);f.close();log(s);}
function same(a,b){return String(a).replace(/\\/g,'/').toLowerCase()===String(b).replace(/\\/g,'/').toLowerCase();}
function time(n){var t=new Time();t.ticks=String(Math.round(n));return t;}
function exact(a,b,label){if(Math.abs(Number(a)-Number(b))>frame/2)throw Error(label+' mismatch: '+a+' != '+b);}
function sequence(name){var found=null;for(var i=0;i<app.project.sequences.numSequences;i++)if(app.project.sequences[i].name===name){if(found)throw Error('Duplicate sequence '+name);found=app.project.sequences[i];}return found;}
__HELPERS__
function conform(c,r){
 if(c.projectItem.isOffline()||!same(c.projectItem.getMediaPath(),r.path))throw Error('Offline/wrong source '+r.id+' expected='+r.path+' actual='+c.projectItem.getMediaPath()+' offline='+c.projectItem.isOffline());
 if(Math.abs(c.getSpeed()-r.speed)>.0001)throw Error('Speed import failed '+r.id+': '+c.getSpeed());
 c.inPoint=time(r.source_in_seconds*ticks/r.speed);c.outPoint=time(r.source_out_seconds*ticks/r.speed);
 c.start=time(r.timeline_start_frame*frame);c.end=time((r.timeline_start_frame+r.frames)*frame);
}
function check(c,r){
 if(!c||!same(c.projectItem.getMediaPath(),r.path)||c.projectItem.isOffline())throw Error('Source mismatch '+r.id);
 exact(c.start.ticks,r.timeline_start_frame*frame,'start '+r.id);exact(c.end.ticks,(r.timeline_start_frame+r.frames)*frame,'end '+r.id);
 exact(c.inPoint.ticks,r.source_in_seconds*ticks/r.speed,'IN '+r.id);exact(c.outPoint.ticks,r.source_out_seconds*ticks/r.speed,'OUT '+r.id);
 if(Math.abs(c.getSpeed()-r.speed)>.0001)throw Error('Speed mismatch '+r.id);
}
try{
 if(!app.project||!same(app.project.path,job.project))throw Error('Open FULL work copy: '+job.project);
 if(sequence(job.sequence))throw Error('FULL already exists; no overwrite');
 if(!sequence(job.source_sequence))throw Error('WIDE absent');
 if(new File(job.video).exists)throw Error('Review already exists; no overwrite');
 if(!new File(job.xml).exists||!new File(job.preset).exists)throw Error('Missing XML/preset');
 var i,j;
 for(i=0;i<app.project.sequences.numSequences;i++){var old=app.project.sequences[i];checkpoints.push({sequence:old,signature:signature(old)});}
 operation='import FULL XML';state(operation);
 if(!app.project.importFiles([job.xml],true,app.project.rootItem,false))throw Error('XML import failed');
 var seq=sequence(job.sequence);if(!seq)throw Error('FULL not imported');app.project.openSequence(seq.sequenceID);
 exact(seq.timebase,frame,'timebase');if(seq.frameSizeHorizontal!==3840||seq.frameSizeVertical!==2160)throw Error('Sequence is not 4K');
 if(seq.videoTracks[0].clips.numItems!==job.plan.clips.length)throw Error('Video clip count');
 for(i=1;i<seq.videoTracks.numTracks;i++)if(seq.videoTracks[i].clips.numItems)throw Error('Unexpected video track');
 for(i=0;i<job.plan.clips.length;i++){
  var r=job.plan.clips[i],c=seq.videoTracks[0].clips[i];operation='conform '+r.id;state('ASSEMBLING '+(i+1)+'/'+job.plan.clips.length+' '+r.wide_item+' '+r.speed+'x');
  conform(c,r);c.name=r.id+' | '+r.wide_item+' | '+Math.round(r.speed*100)+'%';
  var motion=null;for(j=0;j<c.components.numItems;j++)if(c.components[j].matchName==='AE.ADBE Motion')motion=c.components[j];
  if(!motion)throw Error('Motion absent');
  var scale=motion.properties[1];if(scale.isTimeVarying())throw Error('Unexpected animated scale');
  scale.setValue(r.fit_scale,true);
  if(Math.abs(Number(scale.getValue())-r.fit_scale)>.01)throw Error('Fit scale readback failed');
  check(c,r);
 }
 var audioRows=[];for(i=0;i<job.plan.clips.length;i++)if(job.plan.clips[i].audio==='source')audioRows.push(job.plan.clips[i]);
 if(seq.audioTracks.numTracks<2)throw Error('Stereo tracks absent');
 for(var at=0;at<seq.audioTracks.numTracks;at++){
  var track=seq.audioTracks[at];if(at>1){if(track.clips.numItems)throw Error('Unexpected audio track');continue;}
  if(track.clips.numItems!==audioRows.length)throw Error('Audio count mismatch');
  for(i=0;i<audioRows.length;i++){conform(track.clips[i],audioRows[i]);check(track.clips[i],audioRows[i]);}
 }
 for(i=0;i<job.plan.clips.length;i++)check(seq.videoTracks[0].clips[i],job.plan.clips[i]);
 exact(seq.end,job.plan.frames*frame,'total duration');verifyCheckpoints();
 operation='save FULL checkpoint';var started=new Date().getTime();app.project.save();var disk=new File(job.project);
 if(!disk.exists||disk.length<=0||disk.modified.getTime()<started-2000)throw Error('Save not confirmed');
 state('NATIVE_TIMELINE_CHECKED_EXPORTING');operation='720p native export';
 var result=seq.exportAsMediaDirect(new File(job.video).fsName,new File(job.preset).fsName,0);log('EXPORT returned='+result);
 var output=new File(job.video);if(!output.exists||output.length<=0)throw Error('Review output absent');
 verifyCheckpoints();state('EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA');alert('FULL review created. Send result for verification: '+job.video);
}catch(e){state('FAILED operation='+operation+'; line='+e.line+'; '+e);alert('FULL failed: '+operation+'; '+e);throw e;}
})();
