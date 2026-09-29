(function(){
var f=new File('<LOCAL_PATH>');f.encoding='UTF-8';
if(!f.open('w')){alert('Cannot write diagnostic');return;}
try{
 f.writeln('PROJECT '+app.project.path);
 for(var i=0;i<app.project.sequences.numSequences;i++){
  var s=app.project.sequences[i];if(s.name!=='BM26_FULL_FINISH_01'&&s.name!=='BM26_SHORT_FINISH_01')continue;
  f.writeln('SEQUENCE '+s.name+' timebase='+s.timebase+' end='+s.end);
  for(var t=0;t<s.videoTracks.numTracks;t++){
   var tr=s.videoTracks[t];f.writeln('TRACK '+t+' clips='+tr.clips.numItems+' transitions='+tr.transitions.numItems);
   for(var j=0;j<tr.transitions.numItems;j++){var x=tr.transitions[j];f.writeln('TRANSITION '+j+' startTicks='+x.start.ticks+' endTicks='+x.end.ticks+' startSeconds='+x.start.seconds+' endSeconds='+x.end.seconds);}
   for(var k=0;k<tr.clips.numItems;k++){var c=tr.clips[k];if(k>=59&&k<=66)f.writeln('CLIP '+k+' '+c.name+' start='+c.start.ticks+' end='+c.end.ticks+' in='+c.inPoint.ticks+' out='+c.outPoint.ticks);}
  }
 }
 f.writeln('READ ONLY. No project changes or save.');f.close();alert('Transition diagnostic saved. Send Codex: ready.');
}catch(e){f.writeln('ERROR '+e);f.close();alert('Diagnostic error: '+e);}
})();
