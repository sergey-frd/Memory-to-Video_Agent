/* User sequence finish. Run manually in the prepared COPY. */
(function () {
    var JOB = __JOB__, TICKS = 254016000000, FRAME = 10160640000;
    var operation = 'PRE-FLIGHT', label = '', element = 0, base = new File($.fileName).parent;
    function encode(v) {
        if (v === null || typeof v === 'undefined') return 'null';
        if (typeof v === 'number' || typeof v === 'boolean') return String(v);
        if (typeof v === 'string') return '"' + v.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r/g, '\\r').replace(/\n/g, '\\n').replace(/\t/g, '\\t').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,function(c){return '\\u'+('0000'+c.charCodeAt(0).toString(16)).slice(-4);}) + '"';
        var a = [], k;
        if (v instanceof Array) { for (k = 0; k < v.length; k++) a.push(encode(v[k])); return '[' + a.join(',') + ']'; }
        for (k in v) if (v.hasOwnProperty(k)) a.push(encode(k) + ':' + encode(v[k]));
        return '{' + a.join(',') + '}';
    }
    function log(result, warning) {
        var f = new File(base.fsName + '/native_operations.jsonl'); f.encoding = 'UTF-8';
        if (!f.open('a')) throw Error('Cannot append native log');
        f.writeln(encode({timestamp:new Date().toString(),stage:'USER SEQUENCE FINISH',sequence:label,element:element,operation:operation,result:result,error_warning:warning || null})); f.close();
    }
    function writeNew(name, value) {
        var f = new File(base.fsName + '/' + name); if (f.exists) throw Error('Refuse overwrite: ' + name);
        f.encoding = 'UTF-8'; if (!f.open('w')) throw Error('Cannot write ' + name); f.write(encode(value)); f.close();
    }
    function status(s) {
        log(s); var f = new File(base.fsName + '/native_status.txt'); f.encoding='UTF-8';
        if (!f.open('w')) throw Error('Cannot write status'); f.write(new Date().toString() + '\n' + s); f.close();
    }
    function same(a,b) { return String(a).replace(/\\/g,'/').toLowerCase() === String(b).replace(/\\/g,'/').toLowerCase(); }
    function time(n) { var t = new Time(); t.ticks = String(Math.round(n)); return t; }
    function exact(a,b,msg) { if (Math.abs(Number(a)-Number(b))>1) throw Error(msg + ': ' + a + ' != ' + b); }
    function find(node,path) {
        if (node.getMediaPath && same(node.getMediaPath(),path)) return node;
        if (node.children) for (var i=0;i<node.children.numItems;i++) { var x=find(node.children[i],path); if(x) return x; }
        return null;
    }
    function sequence(name) {
        var found=null; for (var i=0;i<app.project.sequences.numSequences;i++) if(app.project.sequences[i].name===name) {
            if(found) throw Error('Duplicate sequence ' + name); found=app.project.sequences[i];
        } return found;
    }
    function empty(s) {
        for(var g=0;g<2;g++) { var tracks=g===0?s.videoTracks:s.audioTracks;
            for(var i=0;i<tracks.numTracks;i++) if(tracks[i].clips.numItems || (g===0 && tracks[i].transitions.numItems)) throw Error('Template is not empty'); }
    }
    function clone(s,name) {
        var before={},found=null,i; for(i=0;i<app.project.sequences.numSequences;i++) before[String(app.project.sequences[i].sequenceID)]=true;
        if(!s.clone()) throw Error('Clone failed');
        for(i=0;i<app.project.sequences.numSequences;i++) if(!before[String(app.project.sequences[i].sequenceID)]) {
            if(found) throw Error('Ambiguous clone'); found=app.project.sequences[i]; }
        if(!found) throw Error('No clone'); found.name=name; if(found.name!==name) throw Error('Rename failed'); return found;
    }
    function component(clip,name) { for(var i=0;i<clip.components.numItems;i++) if(clip.components[i].matchName===name) return clip.components[i]; throw Error('Missing component '+name); }
    function prop(c,name) { for(var i=0;i<c.properties.numItems;i++) if(c.properties[i].displayName===name) return c.properties[i]; throw Error('Missing property '+name); }
    function staticValue(p,v) { if(p.areKeyframesSupported() && p.isTimeVarying()) throw Error('Unexpected animated property '+p.displayName); p.setValue(v,true); }
    function fit(clip,asset) {
        var m=component(clip,'AE.ADBE Motion');
        staticValue(prop(m,'Scale'),asset.fit_scale); staticValue(prop(m,'Position'),[0.5,0.5]); staticValue(prop(m,'Rotation'),0);
        var s=Number(prop(m,'Scale').getValue()),p=prop(m,'Position').getValue(),r=Number(prop(m,'Rotation').getValue());
        if(Math.abs(s-asset.fit_scale)>0.001 || Math.abs(p[0]-0.5)>0.000001 || Math.abs(p[1]-0.5)>0.000001 || Math.abs(r)>0.000001) throw Error('FIT readback mismatch');
    }
    function clipAt(track,start,path) {
        // All inserts are chronological; the new item is last. Do not scan thousands of preceding clips.
        var c=track.clips[track.clips.numItems-1];
        if(!c || !same(c.projectItem.getMediaPath(),path)) throw Error('Inserted source mismatch');
        exact(c.start.ticks,start,'Inserted start'); return c;
    }
    function insert(track,item,start,end,ins,outs) {
        item.setInPoint(ins/TICKS,1); item.setOutPoint(outs/TICKS,1);
        track.overwriteClip(item,String(start)); var c=clipAt(track,start,item.getMediaPath());
        c.inPoint=time(ins); c.outPoint=time(outs); c.start=time(start); c.end=time(end);
        exact(c.start.ticks,start,'START'); exact(c.end.ticks,end,'END'); exact(c.inPoint.ticks,ins,'IN'); exact(c.outPoint.ticks,outs,'OUT');
        if(c.projectItem.isOffline()) throw Error('Offline inserted media'); return c;
    }
    function readClip(c,trackIndex) {
        var o={name:c.name,track:trackIndex,source:c.projectItem.getMediaPath(),start:String(c.start.ticks),end:String(c.end.ticks),inPoint:String(c.inPoint.ticks),outPoint:String(c.outPoint.ticks),offline:c.projectItem.isOffline(),components:[]};
        for(var i=0;i<c.components.numItems;i++) {
            var co=c.components[i],obj={matchName:co.matchName,name:co.displayName,properties:[]};
            for(var j=0;j<co.properties.numItems;j++) {
                var p=co.properties[j],v=null; try { v=p.getValue(); } catch(ignore) { v='UNREADABLE'; }
                var q={name:p.displayName,value:v,timeVarying:false,keys:[]};
                if(p.areKeyframesSupported()) { q.timeVarying=p.isTimeVarying(); if(q.timeVarying) {
                    var keys=p.getKeys(); if(keys) for(var k=0;k<keys.length;k++) q.keys.push({ticks:String(keys[k].ticks),value:p.getValueAtTime(keys[k])});
                }} obj.properties.push(q);
            } o.components.push(obj);
        } return o;
    }
    function signature(s) {
        var out=[s.name,s.sequenceID,s.timebase,s.end,s.frameSizeHorizontal,s.frameSizeVertical];
        for(var g=0;g<2;g++) { var tr=g===0?s.videoTracks:s.audioTracks;
            for(var t=0;t<tr.numTracks;t++) { out.push(g,t,tr[t].clips.numItems);
                if(g===0) out.push(tr[t].transitions.numItems);
                for(var i=0;i<tr[t].clips.numItems;i++) out.push(encode(readClip(tr[t].clips[i],t))); }
        } return out.join('|');
    }
    function settingsSummary(s) {
        var v=s.getSettings(); return {width:v.videoFrameWidth,height:v.videoFrameHeight,pixelAspectRatio:String(v.videoPixelAspectRatio),fieldType:String(v.videoFieldType),frameRateTicks:v.videoFrameRate?String(v.videoFrameRate.ticks):null};
    }
    function shots(s){var a=[];for(var g=0;g<2;g++){var ts=g===0?s.videoTracks:s.audioTracks;for(var t=0;t<ts.numTracks;t++)for(var k=0;k<ts[t].clips.numItems;k++){var c=readClip(ts[t].clips[k],t);c.group=g;a.push(c);}}return a;}
    function transitions(s){var a=[];for(var t=0;t<s.videoTracks.numTracks;t++)for(var k=0;k<s.videoTracks[t].transitions.numItems;k++){var c=s.videoTracks[t].transitions[k];a.push({track:t,name:String(c.name),start:Math.round(Number(c.start.ticks)/FRAME),end:Math.round(Number(c.end.ticks)/FRAME)});}return a;}
    function muted(s){var a=[];for(var g=0;g<2;g++){var ts=g===0?s.videoTracks:s.audioTracks;for(var t=0;t<ts.numTracks;t++)a.push([g,t,ts[t].isMuted()]);}return encode(a);}
    function clear(p){if(p.areKeyframesSupported()){var keys=p.getKeys();if(keys)for(var k=keys.length-1;k>=0;k--)p.removeKey(keys[k]);p.setTimeVarying(false);}}
    function verify(p,t,v){var a=p.getValueAtTime(t);if(v instanceof Array){for(var j=0;j<v.length;j++)if(Math.abs(a[j]-v[j])>.00001)throw Error('Position readback');}else if(Math.abs(Number(a)-v)>.002)throw Error('Scale readback');}
    function mask(a){var vi=0;for(var i=0;i<a.length;i++){if(a[i].group!==0 || a[i].track!==JOB.video_track)continue;vi++;var edit=false;for(var j=0;j<JOB.edits.length;j++)if(JOB.edits[j].index===vi)edit=true;if(!edit)continue;for(j=0;j<a[i].components.length;j++){var co=a[i].components[j];if(co.matchName!=='AE.ADBE Motion')continue;var keep=[];for(var k=0;k<co.properties.length;k++){var n=co.properties[k].name;if(n!=='Scale' && n!=='Scale Width' && n!=='Position')keep.push(co.properties[k]);}co.properties=keep;}}return encode(a);}
    function qeClip(s,frame){var t=qe.project.getActiveSequence().getVideoTrackAt(JOB.video_track),found=null;for(var k=0;k<t.numItems;k++){var item=t.getItemAt(k);if(item.type==='Clip' && Math.abs(Number(item.start.ticks)-frame*FRAME)<=1){if(found)throw Error('Ambiguous QE clip');found=item;}}if(!found)throw Error('QE clip missing '+frame);return found;}
    function boundaries(s){var a=shots(s);if(a.length!==JOB.expected.length)throw Error('Count changed');for(var i=0;i<a.length;i++){var c=a[i],e=JOB.expected[i];if(c.group!==e.group || c.track!==e.track_index || !same(c.source,e.source_path) || c.offline)throw Error('Source/track '+i);exact(c.start,e.timeline_start_ticks,'START');exact(c.end,e.timeline_end_ticks,'END');exact(c.inPoint,e.source_in_ticks,'IN');exact(c.outPoint,e.source_out_ticks,'OUT');}exact(s.end,JOB.frames*FRAME,'Duration');}
    try{
        if(!app.project || !same(app.project.path,JOB.project))throw Error('Open prepared finish WORK copy: '+JOB.project);
        if(new File(base.fsName+'/RUN.started.json').exists || sequence(JOB.name))throw Error('Already attempted. Do not rerun.');
        if(JOB.export_preview && new File(JOB.preview).exists)throw Error('Preview exists');
        var src=sequence(JOB.source_name);if(!src)throw Error('Saved source missing');boundaries(src);exact(src.timebase,FRAME,'FPS');
        if(src.frameSizeHorizontal!==JOB.width || src.frameSizeVertical!==JOB.height)throw Error('Sequence settings');
        var prior=[],i,k;for(i=0;i<app.project.sequences.numSequences;i++){var s=app.project.sequences[i];prior.push({s:s,sig:signature(s),trans:encode(transitions(s)),mute:muted(s)});}
        if(!new File(JOB.project).copy(base.fsName+'/snapshot/BEFORE_FINISH.prproj'))throw Error('Backup failed');
        writeNew('RUN.started.json',{job:JOB.id,date:new Date().toString()});operation='CLONE source';label=JOB.name;status('Creating finish from saved source');var seq=clone(src,JOB.name);app.project.openSequence(seq.sequenceID);
        var before=shots(seq),oldTransitions=transitions(seq),oldMute=muted(seq);
        for(i=0;i<JOB.edits.length;i++){
            var spec=JOB.edits[i],c=seq.videoTracks[JOB.video_track].clips[spec.index-1];element=spec.index;operation='MOTION '+element;var mo=component(c,'AE.ADBE Motion'),sp=prop(mo,'Scale'),pp=prop(mo,'Position');
            if(!prop(mo,'Uniform Scale').getValue())throw Error('Nonuniform scale');clear(sp);clear(pp);sp.setValue(spec.scale,true);pp.setValue(spec.position,true);
            if(spec.keys.length){sp.setTimeVarying(true);pp.setTimeVarying(true);for(k=0;k<spec.keys.length;k++){var row=spec.keys[k],tm=time(Number(c.inPoint.ticks)+row[0]*FRAME);sp.addKey(tm);sp.setValueAtKey(tm,row[1],true);sp.setInterpolationTypeAtKey(tm,0,true);pp.addKey(tm);pp.setValueAtKey(tm,row[2],true);pp.setInterpolationTypeAtKey(tm,0,true);verify(sp,tm,row[1]);verify(pp,tm,row[2]);}}
            else{verify(sp,c.inPoint,spec.scale);verify(pp,c.inPoint,spec.position);}
            status('MOTION '+(i+1)+'/'+JOB.edits.length+' | SHOT '+element);
        }
        operation='TRANSITIONS';app.enableQE();var effect=qe.project.getVideoTransitionByName('Cross Dissolve');if(!effect)throw Error('Cross Dissolve unavailable');
        for(i=0;i<JOB.transitions.length;i++){
            var tr=JOB.transitions[i],list=transitions(seq),lo=tr.cut-tr.frames*JOB.transition_alignment,hi=tr.cut+tr.frames*(1-JOB.transition_alignment);
            for(k=0;k<list.length;k++)if(list[k].track===JOB.video_track && list[k].start<hi && list[k].end>lo)throw Error('Transition overlap at '+tr.cut);
            var incoming=qeClip(seq,tr.cut),duration=time(tr.frames*FRAME).getFormatted(seq.getSettings().videoFrameRate,101);incoming.addTransition(effect,true,duration,'0:00:00:00',JOB.transition_alignment,false,true);
            var after=transitions(seq),hit=0;if(after.length!==list.length+1)throw Error('Transition count not confirmed');for(k=0;k<after.length;k++)if(after[k].track===JOB.video_track && after[k].start===lo && after[k].end===hi)hit++;
            if(hit!==1)throw Error('Transition bounds mismatch '+tr.cut);boundaries(seq);status('TRANSITION '+(i+1)+'/'+JOB.transitions.length+' before SHOT '+tr.incoming);
        }
        operation='VERIFY';boundaries(seq);if(mask(before)!==mask(shots(seq)))throw Error('Unexpected change outside planned Motion');if(muted(seq)!==oldMute)throw Error('Track mute changed');
        var finalTransitions=transitions(seq);for(i=0;i<oldTransitions.length;i++){var found=false;for(k=0;k<finalTransitions.length;k++)if(encode(oldTransitions[i])===encode(finalTransitions[k]))found=true;if(!found)throw Error('Existing transition changed');}
        for(i=0;i<prior.length;i++)if(signature(prior[i].s)!==prior[i].sig || encode(transitions(prior[i].s))!==prior[i].trans || muted(prior[i].s)!==prior[i].mute)throw Error('Protected version changed');
        operation='SAVE';app.project.save();writeNew('native_readback.json',{status:'SAVED_QA_PENDING',name:seq.name,settings:settingsSummary(seq),frames:JOB.frames,shots:shots(seq),transitions:transitions(seq),previous_versions_unchanged:true});
        if(JOB.export_preview){operation='EXPORT';status('Saved. Native preview export in progress.');var result=seq.exportAsMediaDirect(new File(JOB.preview).fsName,new File(JOB.preset).fsName,0),f=new File(JOB.preview);if(!f.exists || !f.length)throw Error('Export failed '+result);}
        writeNew('RUN.completed.json',{status:JOB.export_preview?'EXPORTED_QA_PENDING':'SAVED_QA_PENDING',date:new Date().toString()});status('Saved. Native visual/audio QA pending.');alert(JOB.name+' saved. Tell Codex to verify.');
    }catch(e){var msg='ERROR | '+operation+' | '+String(e)+' | line '+e.line;try{writeNew('RUN.failure.json',{error:msg});status(msg);}catch(ignore){}alert(msg+'\nSTOP. Do not rerun.');throw e;}
})();
