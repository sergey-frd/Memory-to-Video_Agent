(function(){
    var report=new File('<LOCAL_PATH>');
    report.encoding='UTF-8';
    if(!report.open('w')){alert('Cannot write diagnostic report');return;}
    function line(s){report.writeln(s);}
    try {
        line('PROJECT '+app.project.path);
        var found=false;
        for(var s=0;s<app.project.sequences.numSequences;s++){
            var seq=app.project.sequences[s];
            if(seq.name!=='BM26_FULL_MASTER_01')continue;
            found=true;line('SEQUENCE '+seq.name);
            for(var g=0;g<2;g++){
                var tracks=g===0?seq.videoTracks:seq.audioTracks;
                for(var t=0;t<tracks.numTracks;t++)for(var i=0;i<tracks[t].clips.numItems;i++){
                    var c=tracks[t].clips[i];
                    line([g===0?'VIDEO':'AUDIO',t,i+1,c.name,c.projectItem.getMediaPath(),'offline='+c.projectItem.isOffline(),'start='+c.start.ticks,'end='+c.end.ticks,'in='+c.inPoint.ticks,'out='+c.outPoint.ticks,'speed='+c.getSpeed()].join('\t'));
                }
            }
        }
        if(!found)line('FULL SEQUENCE NOT FOUND');
        line('READ ONLY: no project or media changes; no project save.');
        report.close();alert('Diagnostic saved. Send Codex: diagnostic ready.');
    }catch(e){line('ERROR '+e);report.close();alert('Diagnostic error: '+e);}
})();
