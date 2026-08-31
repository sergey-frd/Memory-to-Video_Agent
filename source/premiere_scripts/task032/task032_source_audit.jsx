(function () {
    var base = new File($.fileName).parent.fsName.replace(/\\/g,'/')+'/';
    function write(name, value) { var f = new File(base + name); f.encoding='UTF-8'; f.open('w'); f.write(value); f.close(); }
    function esc(x) {return String(x).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/\r/g,'\\r').replace(/\n/g,'\\n').replace(/\t/g,'\\t');}
    function json(x) {if (x===null || typeof x==='undefined') return 'null'; if(typeof x==='string') return '"'+esc(x)+'"'; if(typeof x==='number'||typeof x==='boolean') return String(x); var a=[], k; if(x instanceof Array){for(k=0;k<x.length;k++)a.push(json(x[k])); return '['+a.join(',')+']';} for(k in x)if(x.hasOwnProperty(k))a.push(json(k)+':'+json(x[k]));return '{'+a.join(',')+'}';}
    try {
        var seq=app.project.activeSequence;
        var runtimeFile=new File(base+'TASK_032_RUNTIME.json');runtimeFile.open('r');var runtime=eval('('+runtimeFile.read()+')');runtimeFile.close();
        if (app.project.path!==new File(runtime.source_project).fsName) throw Error('Wrong source project');
        if (!seq || seq.name!==runtime.source_sequence) throw Error('Wrong active sequence');
        var report={project:app.project.path,sequence:seq.name,end:seq.end,timebase:seq.timebase,tracks:[]};
        var groups=[seq.videoTracks,seq.audioTracks];
        for(var g=0;g<2;g++) for(var t=0;t<groups[g].numTracks;t++) {
            var tr=groups[g][t], row={kind:g===0?'video':'audio',index:t,name:tr.name,muted:tr.isMuted(),clips:[]};
            for(var c=0;c<tr.clips.numItems;c++) {
                var clip=tr.clips[c];
                var cr={name:clip.name,nodeId:clip.nodeId,in_ticks:clip.start.ticks,out_ticks:clip.end.ticks,source_in_ticks:clip.inPoint.ticks,source_out_ticks:clip.outPoint.ticks,components:[]};
                try {cr.offline=clip.projectItem.isOffline(); cr.media=clip.projectItem.getMediaPath();}catch(e){}
                for(var ci=0;ci<clip.components.numItems;ci++) {
                    var comp=clip.components[ci], co={name:comp.displayName,matchName:comp.matchName,properties:[]};
                    for(var p=0;p<comp.properties.numItems;p++) {
                        var prop=comp.properties[p], po={name:prop.displayName};
                        try { po.value=prop.getValue(); po.animated=prop.isTimeVarying(); } catch(e) {po.error=String(e);}
                        co.properties.push(po);
                    }
                    cr.components.push(co);
                }
                row.clips.push(cr);
            }
            report.tracks.push(row);
        }
        write('TASK_032_PREMIERE_SOURCE_READBACK.json',json(report));
        var out=base+'TASK_032_SOURCE_NATIVE_640_360.mp4';
        if (new File(out).exists) throw Error('Source audit export exists; not overwritten');
        write('TASK_032_SOURCE_EXPORT_STATUS.txt','STARTED');
        var preset=new File(base+'TASK_032_H264_640_360_25.epr');
        write('TASK_032_SOURCE_EXPORT_DIAG.txt', 'preset '+preset.exists+'; extension '+seq.getExportFileExtension(preset.fsName)+'; out '+new File(out).fsName+'; version '+app.version);
        var result=seq.exportAsMediaDirect(new File(out).fsName,preset.fsName,0);
        write('TASK_032_SOURCE_EXPORT_STATUS.txt','RETURN '+String(result)+'; exists '+new File(out).exists);
    } catch(e) {write('TASK_032_SOURCE_EXPORT_ERROR.txt',String(e)+'; line '+e.line);}
})();
