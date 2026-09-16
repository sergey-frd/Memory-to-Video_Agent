(function(){
var base=new File($.fileName).parent.fsName.replace(/\\/g,'/')+'/';
function write(n,s){var f=new File(base+n);f.encoding='UTF-8';f.open('w');f.write(s);f.close();}
function esc(s){return String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/[\x00-\x1f]/g,function(c){var h=c.charCodeAt(0).toString(16);return '\\u'+('0000'+h).slice(-4);});}
function json(x){if(x===null||typeof x==='undefined')return 'null';if(typeof x==='string')return '"'+esc(x)+'"';if(typeof x==='number'||typeof x==='boolean')return String(x);var a=[],k;if(x instanceof Array){for(k=0;k<x.length;k++)a.push(json(x[k]));return '['+a.join(',')+']';}for(k in x)if(x.hasOwnProperty(k))a.push(json(k)+':'+json(x[k]));return '{'+a.join(',')+'}';}
function samePath(a,b){return String(a).replace(/\\/g,'/').toLowerCase()===String(b).replace(/\\/g,'/').toLowerCase();}
try{
 var outputProject='<LOCAL_PATH>';
 var outputSequence='TASK035_Felix_Hvr26_FINAL_v01';
 var outputVideo='<LOCAL_PATH>';
 var preset='<LOCAL_PATH>';
 var expectedFrames=4116, frameTicks=10160640000;
 var pf=new File(outputProject), vf=new File(outputVideo), ef=new File(preset);
 if(!pf.exists)throw Error('Output project missing: '+outputProject);
 if(!ef.exists)throw Error('Export preset missing: '+preset);
 if(vf.exists)throw Error('Native output exists; refusing overwrite: '+outputVideo);
 if(!app.project || !samePath(app.project.path,pf.fsName))app.openDocument(pf.fsName);
 if(!app.project || !samePath(app.project.path,pf.fsName))throw Error('Wrong project open: '+(app.project?app.project.path:'none'));
 var seq=null,i,j,k;
 for(i=0;i<app.project.sequences.numSequences;i++)if(app.project.sequences[i].name===outputSequence)seq=app.project.sequences[i];
 if(!seq)throw Error('Final sequence missing: '+outputSequence);
 app.project.openSequence(seq.sequenceID);
 if(!app.project.activeSequence || app.project.activeSequence.name!==outputSequence)throw Error('Final sequence is not active');
 if(Number(seq.end)!==expectedFrames*frameTicks)throw Error('Duration mismatch: '+String(seq.end));
 var videoCount=0,audioCount=0,offline=[],lumetriCount=0,motionCount=0,keyframedMotion=[],clips=[];
 for(i=0;i<seq.videoTracks.numTracks;i++){
  var vt=seq.videoTracks[i];
  for(j=0;j<vt.clips.numItems;j++){
   var clip=vt.clips[j];videoCount++;
   var item={name:clip.name,track:i,startTicks:String(clip.start.ticks),endTicks:String(clip.end.ticks),components:[]};
   try{if(clip.projectItem&&clip.projectItem.isOffline())offline.push(clip.name);}catch(ignoreOffline){}
   for(k=0;k<clip.components.numItems;k++){
    var comp=clip.components[k], cn=String(comp.displayName), pnames=[], keyCount=0, values=[];
    if(cn==='Lumetri Color')lumetriCount++;
    if(cn==='Motion')motionCount++;
    for(var q=0;q<comp.properties.numItems;q++){
     var prop=comp.properties[q], pn=String(prop.displayName);pnames.push(pn);
     if(cn==='Motion'&&pn==='Scale'){
      try{var keys=prop.getKeys();keyCount=keys?keys.length:0;for(var z=0;keys&&z<keys.length;z++)values.push(prop.getValueAtTime(keys[z]));}catch(ignoreKeys){}
     }
    }
    if(cn==='Motion'&&keyCount>=2)keyframedMotion.push({name:clip.name,keyCount:keyCount,scaleValues:values});
    item.components.push({name:cn,properties:pnames,scaleKeyCount:keyCount});
   }
   clips.push(item);
  }
 }
 for(i=0;i<seq.audioTracks.numTracks;i++)audioCount+=seq.audioTracks[i].clips.numItems;
 if(videoCount!==59)throw Error('Video item count mismatch: '+videoCount);
 if(audioCount!==1)throw Error('Audio item count mismatch: '+audioCount);
 if(offline.length)throw Error('Offline media: '+offline.join(','));
 if(lumetriCount!==59)throw Error('Lumetri count mismatch: '+lumetriCount);
 if(keyframedMotion.length!==7)throw Error('Animated still count mismatch: '+keyframedMotion.length);
 app.project.save();
 var readback={status:'PASS_OPEN_CHECK',project:app.project.path,sequence:seq.name,premiereVersion:app.version,durationFrames:expectedFrames,videoItems:videoCount,audioItems:audioCount,offlineMedia:offline,lumetriComponents:lumetriCount,motionComponents:motionCount,keyframedMotion:keyframedMotion,clips:clips,preset:ef.fsName,output:vf.fsName};
 write('TASK035_NATIVE_PREMIERE_READBACK.json',json(readback));
 write('TASK035_NATIVE_EXPORT_STATUS.txt','STARTED\n'+vf.fsName);
 var result=seq.exportAsMediaDirect(vf.fsName,ef.fsName,0);
 write('TASK035_NATIVE_EXPORT_STATUS.txt','RETURN '+String(result)+'\nexists='+String(vf.exists)+'\nbytes='+(vf.exists?String(vf.length):'0'));
 if(!vf.exists||vf.length<1024)throw Error('Native export did not create a usable file');
 seq.setPlayerPosition('0');
 write('TASK035_NATIVE_PREMIERE_COMPLETE.json',json({status:'PASS_NATIVE_EXPORT',project:app.project.path,sequence:seq.name,output:vf.fsName,bytes:vf.length,returnValue:String(result),premiereVersion:app.version}));
}catch(e){write('TASK035_NATIVE_PREMIERE_ERROR.txt',String(e)+'; line '+e.line);}
})();
