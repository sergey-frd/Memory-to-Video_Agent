(function(){
var base=new File($.fileName).parent.fsName.replace(/\\/g,'/')+'/';
function write(n,s){var f=new File(base+n);f.encoding='UTF-8';f.open('w');f.write(s);f.close();}
function esc(s){return String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/[\x00-\x1f]/g,function(c){var h=c.charCodeAt(0).toString(16);return '\\u'+('0000'+h).slice(-4);});}
function json(x){if(x===null||typeof x==='undefined')return 'null';if(typeof x==='string')return '"'+esc(x)+'"';if(typeof x==='number'||typeof x==='boolean')return String(x);var a=[],k;if(x instanceof Array){for(k=0;k<x.length;k++)a.push(json(x[k]));return '['+a.join(',')+']';}for(k in x)if(x.hasOwnProperty(k))a.push(json(k)+':'+json(x[k]));return '{'+a.join(',')+'}';}
function time(fr){var t=new Time();t.ticks=String(fr*10160640000);return t;}
try{
 var mf=new File(base+'TASK_032_MASTER.json');mf.encoding='UTF-8';mf.open('r');var m=eval('('+mf.read()+')');mf.close();
 var output=new File(m.output_project);
 if(!output.exists)throw Error('Output project missing');
 if(new File(m.preview).exists)throw Error('Preview exists: no overwrite');
 if(app.project.path!==output.fsName){app.openDocument(output.fsName);}
 if(app.project.path!==output.fsName)throw Error('Wrong open project: '+app.project.path);
 var seq=null;
 for(var i=0;i<app.project.sequences.numSequences;i++)if(app.project.sequences[i].name===m.output_sequence)seq=app.project.sequences[i];
 if(!seq)throw Error('Output sequence missing');
 app.project.openSequence(seq.sequenceID);
 if(app.project.activeSequence.name!==m.output_sequence)throw Error('Wrong active sequence');
 if(Number(seq.end)!==m.expected_duration.frames*10160640000)throw Error('Duration mismatch');
 var clip=seq.videoTracks[0].clips[0];
 if(clip.name!==m.background_sequence.output)throw Error('Wrong background target '+clip.name);
 var tint=null;
 for(i=0;i<clip.components.numItems;i++)if(clip.components[i].displayName==='Tint')tint=clip.components[i];
 if(tint)throw Error('Tint already present: duplicate application prohibited');
 app.enableQE();var q=qe.project.getActiveSequence();
 q.getVideoTrackAt(0).getItemAt(0).addVideoEffect(qe.project.getVideoEffectByName(m.background_sequence.tint.effect));
 for(i=0;i<clip.components.numItems;i++)if(clip.components[i].displayName==='Tint')tint=clip.components[i];
 if(!tint)throw Error('Tint was not added');
 var propNames=[],black=null,white=null,amount=null;
 for(i=0;i<tint.properties.numItems;i++){
  var p=tint.properties[i];propNames.push(p.displayName);
  if(p.displayName==='Map Black To')black=p;
  if(p.displayName==='Map White To')white=p;
  if(p.displayName==='Amount to Tint')amount=p;
 }
 if(!black||!white||!amount)throw Error('Unexpected Tint properties: '+propNames.join(','));
 var spec=m.background_sequence.tint, b=spec.map_black, w=spec.map_white;
 black.setColorValue(255,b[0],b[1],b[2],true);white.setColorValue(255,w[0],w[1],w[2],true);
 amount.setTimeVarying(true);
 var actualKeys=[];
 for(i=0;i<spec.amount_keyframes.length;i++){var key=spec.amount_keyframes[i],kt=time(key[0]);amount.addKey(kt);amount.setValueAtKey(kt,key[1],true);actualKeys.push([key[0],amount.getValueAtTime(kt)]);if(Math.abs(actualKeys[i][1]-key[1])>.01)throw Error('Tint key mismatch');}
 var nativeLog={status:'PASS',project:app.project.path,sequence:seq.name,version:app.version,background:clip.name,map_black:black.getColorValue(),map_white:white.getColorValue(),tint_keys:actualKeys,properties:propNames};
 if(nativeLog.map_black[1]!==b[0]||nativeLog.map_white[1]!==w[0])throw Error('Color readback mismatch');
 app.project.save();write('TASK_032_NATIVE_BACKGROUND_APPLY_LOG.json',json(nativeLog));
 write('TASK_032_NATIVE_EXPORT_STATUS.txt','STARTED');
 var res=seq.exportAsMediaDirect(new File(m.preview).fsName,new File(base+'TASK_032_H264_640_360_25.epr').fsName,0);
 write('TASK_032_NATIVE_EXPORT_STATUS.txt','RETURN '+String(res)+'; exists '+new File(m.preview).exists);
 seq.setPlayerPosition('0');
}catch(e){write('TASK_032_NATIVE_FINISH_ERROR.txt',String(e)+'; line '+e.line);}
})();
