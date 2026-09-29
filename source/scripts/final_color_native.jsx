(function(){
var job=__JOB__,ticks=254016000000,operation='startup',base=new File($.fileName).parent;
function log(s){var f=new File(base.fsName+'/native_progress.log');f.encoding='UTF-8';if(!f.open('a'))throw Error('Cannot write log');f.writeln(new Date().toString()+' '+s);f.close();}
function state(s){var f=new File(base.fsName+'/native_status.txt');f.open('w');f.write(s);f.close();log(s);}
function step(s){operation=s;log(s);}
function same(a,b){return String(a).replace(/\\/g,'/').toLowerCase()===String(b).replace(/\\/g,'/').toLowerCase();}
function findSeq(n){var r=null;for(var i=0;i<app.project.sequences.numSequences;i++)if(app.project.sequences[i].name===n){if(r)throw Error('Duplicate sequence '+n);r=app.project.sequences[i];}return r;}
function lumetri(c){var a=[];for(var i=0;i<c.components.numItems;i++)if(c.components[i].matchName==='AE.ADBE Lumetri')a.push(c.components[i]);return a;}
function apply(component,settings){
 for(var name in settings)if(settings.hasOwnProperty(name)){
  var p=null;for(var i=0;i<component.properties.numItems;i++)if(component.properties[i].displayName===name){p=component.properties[i];break;}
  if(!p)throw Error('Missing Basic Correction property '+name);
  var neutral=name==='Saturation'?100:0,val=Number(p.getValue());
  if(!isFinite(val)||Math.abs(val-neutral)>.01)throw Error('Unexpected baseline '+name+' '+val);
  p.setValue(settings[name],true);if(Math.abs(Number(p.getValue())-settings[name])>.02)throw Error('Color readback failed '+name);
 }
}
__HELPERS__
try{
 if(!same(app.project.path,job.project))throw Error('Open FINAL COLOR checkpoint copy');
 if(!new File(job.preset).exists)throw Error('Missing review preset');
 app.enableQE();var effect=qe.project.getVideoEffectByName('Lumetri Color');if(!effect)throw Error('Lumetri Color unavailable');
 for(var vi=0;vi<job.versions.length;vi++){
  var v=job.versions[vi],src=findSeq(v.source_sequence);
  if(!src||findSeq(v.output_sequence)||new File(v.review).exists)throw Error('Missing FINISH or output already exists; no overwrite');
  if(src.videoTracks[0].clips.numItems!==v.clips.length)throw Error('Clip count changed');
  for(var ci=0;ci<v.clips.length;ci++){
   var c=src.videoTracks[0].clips[ci],r=v.clips[ci];
   if(!same(c.projectItem.getMediaPath(),r.path)||c.projectItem.isOffline()||Math.abs(Number(c.start.ticks)-r.start_ticks)>1||Math.abs(Number(c.end.ticks)-r.end_ticks)>1)throw Error('Source/timing mismatch '+r.id);
   if(lumetri(c).length)throw Error('Existing Lumetri; refusing cumulative correction '+r.id);
  }
 }
 for(var i=0;i<app.project.sequences.numSequences;i++){var s=app.project.sequences[i];checkpoints.push({sequence:s,signature:signature(s)});}
 for(vi=0;vi<job.versions.length;vi++){
  v=job.versions[vi];state('COLOR '+v.output_sequence);src=findSeq(v.source_sequence);var before=invariant(src),seq=cloneNamed(src,v.output_sequence);
  if(invariant(seq)!==before)throw Error('Clone differs from FINISH');
  for(ci=0;ci<v.clips.length;ci++){
   r=v.clips[ci];var needed=false;for(var name in r.correction)if(r.correction.hasOwnProperty(name))needed=true;
   if(!needed)continue;
   step(v.output_sequence+' CORRECTION '+r.id);c=seq.videoTracks[0].clips[ci];
   var qt=qe.project.getActiveSequence().getVideoTrackAt(0),target=null;
   for(var qi=0;qi<qt.numItems;qi++){var item=qt.getItemAt(qi);if(item.type==='Clip'&&Math.abs(Number(item.start.ticks)-Number(c.start.ticks))<=1){if(target)throw Error('Ambiguous QE clip');target=item;}}
   if(!target)throw Error('QE clip missing '+r.id);target.addVideoEffect(effect);
   var effects=lumetri(c);if(effects.length!==1)throw Error('Lumetri insertion failed');apply(effects[0],r.correction);log('COLOR VERIFIED '+r.id);
  }
  if(invariant(seq)!==before)throw Error('COLOR changed motion/transitions/content/timing');verifyCheckpoints();
  checkpoints.push({sequence:seq,signature:signature(seq)});
 }
 step('save COLOR checkpoint');var started=new Date().getTime();app.project.save();var disk=new File(job.project);
 if(!disk.exists||disk.modified.getTime()<started-2000)throw Error('Save not confirmed');
 for(vi=0;vi<job.versions.length;vi++){
  v=job.versions[vi];seq=findSeq(v.output_sequence);app.project.openSequence(seq.sequenceID);state('EXPORTING '+v.output_sequence);
  var result=seq.exportAsMediaDirect(new File(v.review).fsName,new File(job.preset).fsName,0);log('EXPORT '+v.output_sequence+' '+result);
  var file=new File(v.review);if(!file.exists||file.length<=0)throw Error('Review missing');
 }
 verifyCheckpoints();state('EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA');alert('FULL and SHORT COLOR reviews created. Send result for verification.');
}catch(e){state('FAILED '+operation+' line='+e.line+' '+e);alert('FINAL COLOR failed: '+operation+' '+e);throw e;}
})();
