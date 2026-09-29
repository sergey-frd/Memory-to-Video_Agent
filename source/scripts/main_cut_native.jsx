(function(){
var job=__JOB__,ticks=254016000000,base=new File($.fileName).parent,operation='startup';
function log(s){var f=new File(base.fsName+'/native_progress.log');f.encoding='UTF-8';f.open('a');f.writeln(new Date().toString()+' '+s);f.close();}
function state(s){var f=new File(base.fsName+'/native_status.txt');f.open('w');f.write(s);f.close();log(s);}
function step(s){operation=s;log(s);}
function same(a,b){return String(a).replace(/\\/g,'/').toLowerCase()===String(b).replace(/\\/g,'/').toLowerCase();}
function findSeq(n){var r=null;for(var i=0;i<app.project.sequences.numSequences;i++)if(app.project.sequences[i].name===n){if(r)throw Error('Duplicate '+n);r=app.project.sequences[i];}return r;}
function near(a,b){return Math.abs(Number(a)-Number(b))<=ticks/1000;}
function effects(c){var out=[];for(var i=0;i<c.components.numItems;i++){var co=c.components[i];out.push(co.matchName);if(co.matchName!=='AE.ADBE Motion'&&co.matchName!=='AE.ADBE Lumetri')continue;for(var p=0;p<co.properties.numItems;p++){var prop=co.properties[p];out.push(prop.displayName,String(prop.getValue()));if(prop.areKeyframesSupported()&&prop.isTimeVarying()){var keys=prop.getKeys();out.push(keys?keys.length:0);if(keys)for(var k=0;k<keys.length;k++)out.push(keys[k].ticks,String(prop.getValueAtTime(keys[k])));}}}return out.join('|');}
function validate(c,r){if(!same(c.projectItem.getMediaPath(),r.path)||c.projectItem.isOffline())throw Error('Offline/wrong media '+r.id);if(!near(c.start.ticks,r.start_ticks)||!near(c.end.ticks,r.end_ticks)||!near(c.inPoint.ticks,r.source_in_ticks)||!near(c.outPoint.ticks,r.source_out_ticks))throw Error('Bounds mismatch '+r.id);if(Math.abs(c.getSpeed()-1)>.00001)throw Error('Unexpected speed '+r.id);}
__HELPERS__
try{
 if(!same(app.project.path,job.project))throw Error('Open prepared MAIN project');
 if(new File(job.video).exists)throw Error('Review exists; no overwrite');
 var seq=findSeq(job.sequence),source=findSeq(job.source_sequence);if(!seq||!source)throw Error('MAIN/FULL COLOR missing');
 for(var i=0;i<app.project.sequences.numSequences;i++){var s=app.project.sequences[i];checkpoints.push({sequence:s,signature:signature(s)});}
 step('MAIN native open-check');
 if(seq.frameSizeHorizontal!==3840||seq.frameSizeVertical!==2160||!near(seq.timebase,ticks/25)||!near(seq.end,job.duration_seconds*ticks))throw Error('Sequence format/duration mismatch');
 if(seq.videoTracks[0].clips.numItems!==job.clips.length)throw Error('Video count mismatch');
 for(i=0;i<job.clips.length;i++){var r=job.clips[i],c=seq.videoTracks[0].clips[i];validate(c,r);if(effects(c)!==effects(source.videoTracks[0].clips[r.source_index]))throw Error('Inherited Motion/Lumetri differs '+r.id);log('VERIFIED '+r.id+' <- '+r.source_full_id);}
 var actualAudio=0;for(var t=0;t<seq.audioTracks.numTracks;t++){var track=seq.audioTracks[t],expected=[];for(i=0;i<job.audio.length;i++)if(job.audio[i].track===t)expected.push(job.audio[i]);if(track.clips.numItems!==expected.length)throw Error('Audio count mismatch');for(i=0;i<expected.length;i++){validate(track.clips[i],expected[i]);actualAudio++;}}
 if(actualAudio!==job.audio.length)throw Error('Missing audio tracks');
 var tr=seq.videoTracks[0].transitions;if(tr.numItems!==job.transitions.length)throw Error('Transition count mismatch');
 for(i=0;i<job.transitions.length;i++){r=job.transitions[i];if(!near(tr[i].start.ticks,r.start_ticks)||!near(tr[i].end.ticks,r.end_ticks))throw Error('Transition bounds mismatch');}
 verifyCheckpoints();state('NATIVE_STRUCTURE_AND_EFFECTS_PASS');step('save native MAIN checkpoint');var started=new Date().getTime();app.project.save();var disk=new File(job.project);if(!disk.exists||disk.modified.getTime()<started-2000)throw Error('Save unconfirmed');
 app.project.openSequence(seq.sequenceID);state('EXPORTING BM26_MAIN_01');var result=seq.exportAsMediaDirect(new File(job.video).fsName,new File(job.preset).fsName,0);log('EXPORT '+result);
 var file=new File(job.video);if(!file.exists||file.length<=0)throw Error('Review missing');verifyCheckpoints();state('EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA');alert('MAIN review created. Send result for verification.');
}catch(e){state('FAILED '+operation+' line='+e.line+' '+e);alert('MAIN failed: '+operation+' '+e);throw e;}
})();
