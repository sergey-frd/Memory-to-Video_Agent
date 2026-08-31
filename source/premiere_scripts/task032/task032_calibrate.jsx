(function(){
var base=new File($.fileName).parent.fsName.replace(/\\/g,'/')+'/';
var f=new File(base+'TASK_032_MASTER.json');f.encoding='UTF-8';f.open('r');var m=eval('('+f.read()+')');f.close();
var log=new File(base+'TASK_032_CALIBRATION_PROPERTIES.txt');log.encoding='UTF-8';log.open('w');
function inspect(c){log.writeln('COMPONENT '+c.displayName+' '+c.matchName);for(var j=0;j<c.properties.numItems;j++){var p=c.properties[j];try{log.writeln(j+' | '+p.displayName+' | '+p.getValue());}catch(e){log.writeln(j+' | '+p.displayName+' | ERROR '+e);}}}
try{
 var scratch=new File(m.qa_revision_operations.calibration_project);app.openDocument(scratch.fsName);
 if(app.project.path!==scratch.fsName)throw Error('Wrong project');
 var seq;for(var i=0;i<app.project.sequences.numSequences;i++)if(app.project.sequences[i].name===m.output_sequence)seq=app.project.sequences[i];app.project.openSequence(seq.sequenceID);app.enableQE();
 var q=qe.project.getActiveSequence();log.writeln('ADD Alpha Adjust '+q.getVideoTrackAt(0).getItemAt(0).addVideoEffect(qe.project.getVideoEffectByName('Alpha Adjust')));
 var bg=seq.videoTracks[0].clips[0];for(i=0;i<bg.components.numItems;i++)inspect(bg.components[i]);
 var ac=seq.audioTracks[0].clips[0];for(i=0;i<ac.components.numItems;i++)inspect(ac.components[i]);
 var vc=seq.videoTracks[1].clips;for(var k=0;k<vc.numItems;k++)if(Number(vc[k].start.ticks)===2268*10160640000){for(i=0;i<vc[k].components.numItems;i++)if(vc[k].components[i].displayName==='Lumetri Color')inspect(vc[k].components[i]);}
 for(var tr=0;tr<seq.videoTracks.numTracks;tr++)for(var ci=0;ci<seq.videoTracks[tr].clips.numItems;ci++)seq.videoTracks[tr].clips[ci].setSelected(false,true);
 ac.setSelected(true,true);app.project.save();log.writeln('PASS: scratch only');
}catch(e){log.writeln('ERROR '+e+' line '+e.line);}log.close();
})();
