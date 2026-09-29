(function(){
var masterJob=__JOB__,job=masterJob,ticks=254016000000,operation='startup',base=new File($.fileName).parent;
function log(s){var f=new File(base.fsName+'/native_progress.log');f.encoding='UTF-8';f.open('a');f.writeln(new Date().toString()+' '+s);f.close();}
function state(s){var f=new File(base.fsName+'/native_status.txt');f.open('w');f.write(s);f.close();log(s);}
function step(s){operation=s;log(s);}
function same(a,b){return String(a).replace(/\\/g,'/').toLowerCase()===String(b).replace(/\\/g,'/').toLowerCase();}
function atTicks(n){var t=new Time();t.ticks=String(Math.round(n));return t;}
function closeEnough(a,b){return Math.abs(Number(a)-Number(b))<=ticks/50;}
function findSeq(n){var r=null;for(var i=0;i<app.project.sequences.numSequences;i++)if(app.project.sequences[i].name===n){if(r)throw Error('Duplicate sequence');r=app.project.sequences[i];}return r;}
function timeline(s){var p=[s.end,s.timebase,s.frameSizeHorizontal,s.frameSizeVertical];for(var g=0;g<2;g++){var ts=g?s.audioTracks:s.videoTracks;for(var t=0;t<ts.numTracks;t++)for(var i=0;i<ts[t].clips.numItems;i++){var c=ts[t].clips[i];p.push(g,t,c.projectItem.getMediaPath(),c.start.ticks,c.end.ticks,c.inPoint.ticks,c.outPoint.ticks,c.getSpeed());}}return p.join('|');}
__HELPERS__
function transitions(seq){
__TRANSITIONS__
}
function motion(seq,v){
 for(var i=0;i<v.motion.length;i++){
  var m=v.motion[i],c=seq.videoTracks[0].clips[m.index],r=v.plan.clips[m.index],component=null;
  step(v.output_sequence+' motion '+m.id);
  if(!same(c.projectItem.getMediaPath(),r.path))throw Error('Motion source mismatch');
  for(var j=0;j<c.components.numItems;j++)if(c.components[j].matchName==='AE.ADBE Motion')component=c.components[j];
  if(!component)throw Error('Motion absent');var pos=component.properties[0],scale=component.properties[1];
  if(pos.isTimeVarying()||scale.isTimeVarying())throw Error('Existing motion animation');
  var baseline=Number(scale.getValue()),position=pos.getValue();
  if(!isFinite(baseline)||baseline<=0||position.length!==2)throw Error('Invalid Motion values');
  if(m.kind==='video'){scale.setValue(baseline*m.keys[0][1],true);if(Math.abs(Number(scale.getValue())-baseline*m.keys[0][1])>.02)throw Error('Static scale verification failed');continue;}
  scale.setTimeVarying(true);pos.setTimeVarying(true);
  for(var f=0;f<r.frames;f++){
   var u=f/(r.frames-1),a=m.keys[0],b=m.keys[m.keys.length-1];
   for(var k=1;k<m.keys.length;k++)if(u<=m.keys[k][0]){a=m.keys[k-1];b=m.keys[k];break;}
   var q=(u-a[0])/(b[0]-a[0]);q=q*q*(3-2*q);
   var z=a[1]+(b[1]-a[1])*q,cx=a[2]+(b[2]-a[2])*q,cy=a[3]+(b[3]-a[3])*q,sv=baseline*z;
   var pv=[Number(position[0])-(cx-.5)*r.width*sv/100/3840,Number(position[1])-(cy-.5)*r.height*sv/100/2160];
   var tm=atTicks(Number(c.inPoint.ticks)+f*ticks/25);
   scale.addKey(tm);scale.setValueAtKey(tm,sv,true);scale.setInterpolationTypeAtKey(tm,0,true);
   pos.addKey(tm);pos.setValueAtKey(tm,pv,true);pos.setInterpolationTypeAtKey(tm,0,true);
   var got=pos.getValueAtTime(tm);if(Math.abs(Number(scale.getValueAtTime(tm))-sv)>.02||Math.abs(got[0]-pv[0])>.0001||Math.abs(got[1]-pv[1])>.0001)throw Error('Motion readback mismatch');
  }
  if(scale.getKeys().length!==r.frames||pos.getKeys().length!==r.frames)throw Error('Key count mismatch');
 }
}
try{
 if(!same(app.project.path,masterJob.project))throw Error('Open VISUAL FINISH copy');
 for(var i=0;i<masterJob.versions.length;i++){var v=masterJob.versions[i];if(findSeq(v.output_sequence)||new File(v.review).exists)throw Error('Finish already exists; no overwrite');if(!findSeq(v.source_sequence))throw Error('MASTER absent');}
 for(i=0;i<app.project.sequences.numSequences;i++){var s=app.project.sequences[i];checkpoints.push({sequence:s,signature:signature(s)});}
 for(i=0;i<masterJob.versions.length;i++){
  var v=masterJob.versions[i];job=v;state('BUILDING '+v.output_sequence);var source=findSeq(v.source_sequence),before=timeline(source),seq=cloneNamed(source,v.output_sequence);
  if(timeline(seq)!==before)throw Error('Clone structure differs');
  // Native centered dissolves need source handles even for imported stills.
  var handled={};
  for(var ti=0;ti<v.transitions.length;ti++){
   var spec=v.transitions[ti],indices=[spec.left_index,spec.right_index];
   for(var hi=0;hi<indices.length;hi++){
    var index=indices[hi];if(handled[index])continue;handled[index]=true;
    var still=seq.videoTracks[0].clips[index],expected=v.plan.clips[index];
    if(expected.kind!=='image')throw Error('Handle offset restricted to still images');
    var st=Number(still.start.ticks),en=Number(still.end.ticks),oldIn=Number(still.inPoint.ticks),oldOut=Number(still.outPoint.ticks);
    still.outPoint=atTicks(oldOut+ticks);still.inPoint=atTicks(oldIn+ticks);
    still.start=atTicks(st);still.end=atTicks(en);
    if(!closeEnough(still.inPoint.ticks,oldIn+ticks)||!closeEnough(still.outPoint.ticks,oldOut+ticks)||!closeEnough(still.start.ticks,st)||!closeEnough(still.end.ticks,en))throw Error('Still handle readback failed');
    log('STILL HANDLE OFFSET '+expected.id+' +1s; screen duration unchanged');
   }
  }
  before=timeline(seq);
  motion(seq,v);transitions(seq);
  if(timeline(seq)!==before)throw Error('Finish changed content/timing');verifyCheckpoints();
  checkpoints.push({sequence:seq,signature:signature(seq)});
 }
 operation='save finish checkpoint';var started=new Date().getTime();app.project.save();var disk=new File(masterJob.project);
 if(!disk.exists||disk.modified.getTime()<started-2000)throw Error('Save not confirmed');
 for(i=0;i<masterJob.versions.length;i++){
  var v=masterJob.versions[i],seq=findSeq(v.output_sequence);app.project.openSequence(seq.sequenceID);state('EXPORTING '+v.output_sequence);
  var result=seq.exportAsMediaDirect(new File(v.review).fsName,new File(masterJob.preset).fsName,0);log('EXPORT '+v.output_sequence+' '+result);
  var file=new File(v.review);if(!file.exists||file.length<=0)throw Error('Review missing');
 }
 verifyCheckpoints();state('EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA');alert('FULL and SHORT finish reviews created. Send result for verification.');
}catch(e){state('FAILED '+operation+' line='+e.line+' '+e);alert('VISUAL FINISH failed: '+operation+' '+e);throw e;}
})();
