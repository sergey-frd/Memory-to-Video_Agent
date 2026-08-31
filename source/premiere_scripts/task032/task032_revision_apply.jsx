(function(){
var base=new File($.fileName).parent.fsName.replace(/\\/g,'/')+'/';
function write(n,s){var f=new File(base+n);f.encoding='UTF-8';f.open('w');f.write(s);f.close();}
function read(n){var f=new File(base+n);f.encoding='UTF-8';f.open('r');var r=eval('('+f.read()+')');f.close();return r;}
function esc(s){return String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/[\x00-\x1f]/g,function(c){return '\\u'+('0000'+c.charCodeAt(0).toString(16)).slice(-4);});}
function json(x){if(x===null||typeof x==='undefined')return 'null';if(typeof x==='string')return '"'+esc(x)+'"';if(typeof x==='number'||typeof x==='boolean')return String(x);var a=[],k;if(x instanceof Array){for(k=0;k<x.length;k++)a.push(json(x[k]));return '['+a.join(',')+']';}for(k in x)if(x.hasOwnProperty(k))a.push(json(k)+':'+json(x[k]));return '{'+a.join(',')+'}';}
var entries=[];
function comp(c,name){var out=[];for(var i=0;i<c.components.numItems;i++)if(c.components[i].displayName===name)out.push(c.components[i]);if(out.length!==1)throw Error('Ambiguous component '+name+' count '+out.length);return out[0];}
function prop(c,name){for(var i=0;i<c.properties.numItems;i++)if(c.properties[i].displayName===name)return c.properties[i];throw Error('Missing parameter '+name);}
function set(c,n,v){var p=prop(c,n),before=p.getValue();p.setValue(v,true);var actual=p.getValue();if(Math.abs(Number(actual)-Number(v))>.0001)throw Error('Readback '+n+' '+actual+' != '+v);entries.push({effect:c.displayName,parameter:n,before:before,after:actual});}
function sequence(name){var out=[];for(var i=0;i<app.project.sequences.numSequences;i++)if(app.project.sequences[i].name===name)out.push(app.project.sequences[i]);if(out.length!==1)throw Error('Ambiguous sequence '+name);return out[0];}
function clip(seq,tr,fr,audio){var t=audio?seq.audioTracks[tr]:seq.videoTracks[tr],out=[];for(var i=0;i<t.clips.numItems;i++)if(Number(t.clips[i].start.ticks)===fr*10160640000)out.push(t.clips[i]);if(out.length!==1)throw Error('Ambiguous clip '+tr+' '+fr);return out[0];}
try{
 var m=read('TASK_032_MASTER.json'),v=read('TASK_032_MASTER_VALIDATION.json'),r=m.qa_revision_operations;
 if(v.status!=='PASS'||v.revision!==2)throw Error('Validation not PASS R2');
 var path=new File(m.output_project);app.openDocument(path.fsName);if(app.project.path!==path.fsName)throw Error('Wrong project');
 var seq=sequence(m.output_sequence),bg=sequence(m.background_sequence.output);app.project.openSequence(seq.sequenceID);app.enableQE();
 var outer=clip(seq,0,0,false);set(comp(outer,'Gaussian Blur (Legacy)'),'Blurriness',r.background.outer_blur);
 var tint=comp(outer,'Tint');var b=r.background.tint.map_black,w=r.background.tint.map_white;
 prop(tint,'Map Black To').setColorValue(255,b[0],b[1],b[2],true);prop(tint,'Map White To').setColorValue(255,w[0],w[1],w[2],true);
 entries.push({effect:'Tint',black:prop(tint,'Map Black To').getColorValue(),white:prop(tint,'Map White To').getColorValue()});
 var mapping={temperature:'Temperature',tint:'Tint',saturation:'Saturation',exposure:'Exposure',contrast:'Contrast',highlights:'Highlights',shadows:'Shadows',whites:'Whites',blacks:'Blacks',sharpen:'Sharpen',vibrance:'Vibrance',vignette:'Amount'};
 for(var i=0;i<r.colors.length;i++){var o=r.colors[i],lc=comp(clip(seq,o.track,o.output_in,false),'Lumetri Color');for(var k in o.after)if(o.after.hasOwnProperty(k))set(lc,mapping[k],o.after[k]);}
 for(var at=0;at<2;at++){var ac=seq.audioTracks[at].clips[0],qt=qe.project.getActiveSequence().getAudioTrackAt(at),matches=[];for(i=0;i<qt.numItems;i++)if(qt.getItemAt(i).type==='Clip')matches.push(qt.getItemAt(i));if(matches.length!==1)throw Error('Ambiguous QE audio');matches[0].addAudioEffect(qe.project.getAudioEffectByName(r.audio.effect));var limit=comp(ac,r.audio.effect);for(k in r.audio.normalized_parameters)if(r.audio.normalized_parameters.hasOwnProperty(k))set(limit,k,r.audio.normalized_parameters[k]);}
 app.project.openSequence(bg.sequenceID);var a=r.alpha_adjust,bc=clip(bg,a.track,a.output_in,false),qbt=qe.project.getActiveSequence().getVideoTrackAt(a.track),qmatches=[];
 if(bc.name!==a.name)throw Error('Wrong alpha clip');for(i=0;i<qbt.numItems;i++)if(qbt.getItemAt(i).type==='Clip'&&qbt.getItemAt(i).name===a.name)qmatches.push(qbt.getItemAt(i));if(qmatches.length!==1)throw Error('Ambiguous QE background');qmatches[0].addVideoEffect(qe.project.getVideoEffectByName(a.effect));var alpha=comp(bc,a.effect);for(k in a.parameters)if(a.parameters.hasOwnProperty(k))set(alpha,k,a.parameters[k]);
 app.project.openSequence(seq.sequenceID);app.project.save();write('TASK_032_REVISION_02_APPLY_LOG.json',json({status:'PASS',project:app.project.path,sequence:seq.name,entries:entries}));
 var preview=new File(r.preview_staging);if(preview.exists)throw Error('Staging preview exists');write('TASK_032_REVISION_02_EXPORT_STATUS.txt','STARTED');var result=seq.exportAsMediaDirect(preview.fsName,new File(base+'TASK_032_H264_640_360_25.epr').fsName,0);write('TASK_032_REVISION_02_EXPORT_STATUS.txt','RETURN '+result+'; exists '+preview.exists);seq.setPlayerPosition('0');
}catch(e){write('TASK_032_REVISION_02_ERROR.txt',String(e)+' line '+e.line+'; changes are NOT saved if apply log is absent');}
})();
