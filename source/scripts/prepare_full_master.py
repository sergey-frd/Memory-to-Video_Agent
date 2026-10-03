"""Prepare an editable FULL from the verified WIDE; never launches Adobe."""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from ingest import digest, write_json
from classify import copy_verified, stamp
from utils import premiere_project as pp
from utils.premiere_sequence_motion import _track_item_contexts
from tools.prepare_native_finish import HELPERS


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))


def build(wide, decisions):
    clips, items, cursor = [], [], 0
    assert set(decisions) == {c['id'] for c in wide['clips']}
    for c in wide['clips']:
        d = decisions[c['id']]
        segments = d['segments']
        previous = 0
        first = cursor
        for n, (lo, hi, speed) in enumerate(segments):
            assert 0 <= lo < hi <= c['duration_seconds'] + 1e-6
            assert lo >= previous and speed in (1, 2, 3)
            assert c['kind'] == 'video' or speed == 1
            frames = round((hi-lo)*25/speed)
            assert abs(frames*speed/25-(hi-lo)) < 1e-6
            previous = hi
            source_in = c['source_in_seconds'] + lo if c['kind'] == 'video' else 0
            source_out = source_in + frames*speed/25
            clips.append(dict(id=f'FM{len(clips)+1:03d}', wide_item=c['id'], media_id=c['media_id'],
                source_family=c['source_family_id'], path=c['path'], kind=c['kind'], art_type=c['art_type'],
                source_in_seconds=source_in, source_out_seconds=source_out, speed=speed,
                frames=frames, timeline_start_frame=cursor, duration_seconds=frames/25,
                audio='source' if c['kind']=='video' and speed==1 else 'muted',
                reason=d['reason'], chapter=c['chapter_title']))
            cursor += frames
        decision = 'REMOVE' if not segments else 'KEEP' if segments == [[0, c['duration_seconds'], 1]] else 'COMPRESS'
        items.append(dict(source_wide_master_item=c['id'], media_id=c['media_id'], source_family=c['source_family_id'],
            decision=decision, original_wide_range_seconds=[c['timeline_start_frame']/25,(c['timeline_start_frame']+c['frames'])/25],
            original_source_range_seconds=[c['source_in_seconds'],c['source_out_seconds']],
            selected_full_range_seconds=[first/25,cursor/25] if segments else None,
            speed_segments=[x for x in clips if x['wide_item']==c['id']], full_duration_seconds=(cursor-first)/25,
            original_audio_decision='Preserve synchronous audio at 100%; omit audio only on accelerated ranges' if c['kind']=='video' else 'No audio',
            reason=d['reason'], dramaturgical_function=c['semantic_function'],
            evidence='12 timestamped samples from native WIDE review' if c['kind']=='video' else 'Original image contact sheet',
            audio_boundary_review='PENDING' if c['kind']=='video' and decision!='KEEP' else 'Unchanged'))
    stats={key:sum(i['decision']==key for i in items) for key in ('KEEP','COMPRESS','REMOVE')}
    stats.update(wide_seconds=wide['duration_seconds'],full_planned_seconds=cursor/25,
        reduction_seconds=wide['duration_seconds']-cursor/25,
        reduction_percent=round(100*(1-cursor/wide['frames']),2),
        video_with_variable_speed=sum(len({s[2] for s in decisions[c['id']]['segments']})>1 for c in wide['clips'] if c['kind']=='video'),
        original_audio_segments=sum(c['audio']=='source' for c in clips),
        original_audio_seconds=sum(c['duration_seconds'] for c in clips if c['audio']=='source'),
        photos=sum(c['kind']=='image' and not c['art_type'] for c in clips),
        watercolor=sum(c['art_type']=='watercolor' for c in clips),double_exposure=sum(c['art_type']=='double_exposure' for c in clips))
    return dict(schema_version=1,task_id=wide['task_id'],sequence_name=wide['task_id']+'_FULL_MASTER_01',source_sequence=wide['sequence_name'],
        fps=25,width=3840,height=2160,frames=cursor,duration_seconds=cursor/25,duration_target=None,
        items=items,clips=clips,stats=stats,limitations=['Visual sampling is not continuous audiovisual review.',
        'Speech boundaries and muted accelerated intervals require user listening review.',
        'Native XML speed import must pass getSpeed and source bounds readback before export.'],
        music_added=False,finish_applied=False,status='PREPARED_NATIVE_NOT_RUN')


def make_xml(plan, wide):
    def el(parent, tag, value=None, **attrs):
        node=ET.SubElement(parent,tag,attrs)
        if value is not None: node.text=str(value)
        return node
    def rate(parent):
        r=el(parent,'rate');el(r,'timebase',25);el(r,'ntsc','FALSE')
    def fmt(parent,w=3840,h=2160):
        el(parent,'width',w);el(parent,'height',h);rate(parent);el(parent,'pixelaspectratio','square');el(parent,'fielddominance','none');el(parent,'anamorphic','FALSE')
    root=ET.Element('xmeml',version='5');seq=el(root,'sequence',id=plan['sequence_name'].lower().replace('_','-'))
    el(seq,'name',plan['sequence_name']);el(seq,'duration',plan['frames']);rate(seq)
    media=el(seq,'media');video=el(media,'video');fmt(el(el(video,'format'),'samplecharacteristics'),plan.get('width',3840),plan.get('height',2160));vt=el(video,'track')
    audio=el(media,'audio');el(audio,'numOutputChannels',2);aformat=el(el(audio,'format'),'samplecharacteristics');el(aformat,'depth',16);el(aformat,'samplerate',48000)
    outputs=el(audio,'outputs')
    for ch in (1,2):
        group=el(outputs,'group');el(group,'index',ch);el(group,'numchannels',1);el(group,'downmix',0);el(el(group,'channel'),'index',ch)
    ats=[el(audio,'track'),el(audio,'track')];seen=set();audio_index=0
    for index,c in enumerate(plan['clips']):
        if c['audio']=='source':audio_index+=1
        for ch in ([0,1,2] if c['audio']=='source' else [0]):
            parent=vt if ch==0 else ats[ch-1];node=el(parent,'clipitem',id=f'{c["id"]}-{ch}')
            el(node,'name',c['id']+' | '+c['wide_item']);el(node,'enabled','TRUE');rate(node)
            end=max(x['source_out_seconds'] for x in wide['clips'] if x['media_id']==c['media_id'])
            el(node,'duration',round(end*25/c['speed']));el(node,'start',c['timeline_start_frame']);el(node,'end',c['timeline_start_frame']+c['frames'])
            ins=round(c['source_in_seconds']*25/c['speed']);el(node,'in',ins);el(node,'out',ins+c['frames'])
            file=el(node,'file',id='file-'+c['media_id'])
            if c['media_id'] not in seen:
                seen.add(c['media_id']);el(file,'name',Path(c['path']).name);el(file,'pathurl',Path(c['path']).as_uri());rate(file);el(file,'duration',round(end*25))
                fm=el(file,'media');fv=el(fm,'video');fmt(el(fv,'samplecharacteristics'),c['width'],c['height'])
                if c['kind']=='image':el(fv,'stillframe','TRUE')
                else:
                    fa=el(fm,'audio');el(fa,'channelcount',c['channels']);sc=el(fa,'samplecharacteristics');el(sc,'depth',16);el(sc,'samplerate',48000)
            if ch:
                st=el(node,'sourcetrack');el(st,'mediatype','audio');el(st,'trackindex',min(ch,c['channels']))
            if c['audio']=='source':
                for linked in (0,1,2):
                    link=el(node,'link');el(link,'linkclipref',f'{c["id"]}-{linked}');el(link,'mediatype','video' if linked==0 else 'audio');el(link,'trackindex',1 if linked==0 else linked);el(link,'clipindex',index+1 if linked==0 else audio_index)
                    if linked:el(link,'groupindex',1)
            if c['speed']!=1:
                effect=el(el(node,'filter'),'effect')
                for k,v in [('name','Time Remap'),('effectid','timeremap'),('effectcategory','motion'),('effecttype','motion'),('mediatype','video')]:el(effect,k,v)
                for k,v in [('variablespeed',0),('speed',c['speed']*100),('reverse','FALSE'),('frameblending','FALSE')]:
                    p=el(effect,'parameter');el(p,'parameterid',k);el(p,'name',k);el(p,'value',v)
    for ch,track in enumerate(ats,1):el(track,'outputchannelindex',ch)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'+ET.tostring(root,encoding='unicode')


def prepare(task_id, dry_run=False):
    task=ROOT/'tasks'/task_id;work=task/'full_master';s=read(task/'wide_master/status.json');v=read(task/'wide_master/verification.json')
    if not s['native_sequence_created'] or not s['review_created'] or v['full_decode']!='PASS':raise ValueError('FULL MASTER NOT STARTED: WIDE incomplete')
    source=Path(s['project_checkpoint']);wide_path=Path(s['plan']);wide=read(wide_path)
    if digest(source)!=v['checkpoint_sha256']:raise ValueError('WIDE checkpoint changed; re-audit required')
    root=pp.load_premiere_project_root(source);seq=pp.find_project_sequence_node(root,s['sequence'])
    if seq is None:raise ValueError('WIDE sequence absent')
    contexts=_track_item_contexts(seq,group_index=0,id_lookup=pp.build_project_object_id_lookup(root),uid_lookup=pp.build_project_object_uid_lookup(root),project_path=source)
    contexts=sorted(contexts,key=lambda c:c.start);assert len(contexts)==len(wide['clips'])
    tick=pp.PREMIERE_TICKS_PER_SECOND
    for actual,c in zip(contexts,wide['clips']):
        assert str(Path(actual.source_path)).lower()==str(Path(c['path'])).lower()
        assert abs(actual.start-c['timeline_start_frame']*tick/25)<tick/50
        assert abs(actual.end-(c['timeline_start_frame']+c['frames'])*tick/25)<tick/50
        assert abs(actual.source_in-c['source_in_seconds']*tick)<tick/50
        stat=Path(c['path']).stat();assert stat.st_size==c['source_stat']['size'] and stat.st_mtime_ns==c['source_stat']['mtime_ns']
    plan=build(wide,read(task/'full_master_decisions.json'));plan.update(source_project=str(source),source_project_sha256=digest(source),wide_plan_sha256=digest(wide_path))
    metadata=read(work/'source_metadata.json')
    for c in plan['clips']:c.update(metadata[c['media_id']])
    # Item segments share objects with clips so metadata is retained in both views.
    xml=make_xml(plan,wide);ET.fromstring(xml)
    project=source.with_name('BAM_26_BM_1_FULL_MASTER_01.prproj');package=wide_path.parent.parent.parent/'FULL_MASTER'/plan['sequence_name']
    if project.exists() or package.exists():raise FileExistsError('FULL checkpoint/package already exists; refusing overwrite')
    if dry_run:
        print(json.dumps(dict(status='DRY RUN PASS',**plan['stats']),ensure_ascii=False));return
    package.mkdir(parents=True);copy_verified(source,project)
    write_json(work/'full_master.json',plan);copy_verified(work/'full_master.json',package/'full_master.json');copy_verified(wide_path,package/'wide_master_snapshot.json')
    copy_verified(task/'full_master_decisions.json',package/'full_master_decisions.json')
    copy_verified(work/'source_metadata.json',package/'source_metadata.json')
    (package/'FULL_TIMELINE.xml').write_text(xml,encoding='utf-8')
    copy_verified(wide_path.parent/'review_720p25.epr',package/'review_720p25.epr')
    job=dict(project=project.as_posix(),sequence=plan['sequence_name'],source_sequence=wide['sequence_name'],plan=plan,
        xml=(package/'FULL_TIMELINE.xml').as_posix(),preset=(package/'review_720p25.epr').as_posix(),video=(package/(plan['sequence_name']+'_REVIEW.mp4')).as_posix())
    template=(ROOT/'scripts/full_master_native.jsx').read_text(encoding='utf-8')
    jsx=template.replace('__HELPERS__',HELPERS[:HELPERS.index('function cloneNamed')]).replace('__JOB__',json.dumps(job,ensure_ascii=True))
    (package/'assemble_full_master.jsx').write_text(jsx,encoding='utf-8');(work/'native_syntax_check.js').write_text(jsx,encoding='utf-8')
    write_json(package/'job.json',job)
    status=dict(task_id=task_id,stage='FULL MASTER',status='FULL MASTER PREPARED — NATIVE RUN PENDING',timestamp=stamp(),
        stats=plan['stats'],plan=str(package/'full_master.json'),project_checkpoint=str(project),source_project=str(source),source_project_sha256=digest(source),
        jsx=str(package/'assemble_full_master.jsx'),sequence=plan['sequence_name'],review=job['video'],native_sequence_created=False,review_created=False,
        execution_state='STOP',next='USER EXECUTES JSX; SHORT NOT STARTED')
    write_json(work/'status.json',status);write_json(package/'preparation_status.json',status)
    state=read(task/'state.json');state.update(stage='FULL MASTER',status=status['status'],full_master=status,next_stage='USER EXECUTES PREMIERE JSX',execution_state='STOP');state['stages']['FULL_MASTER']='PREPARED_NATIVE_NOT_RUN';state['history'].append(status);write_json(task/'state.json',state)
    instructions=f'''# FULL MASTER — ручной запуск

Открыть отдельный проект `{project}`. WIDE остаётся в нём и в независимом checkpoint.
Выбирать sequence не требуется: JSX импортирует новую {plan['sequence_name']} из XML.
Запустить монитор `scripts\\watch_full_master.bat {task.name}` из корня репозитория.
Через Run Transition Script запустить `{package / 'assemble_full_master.jsx'}`.
Скрипт проверяет speed, IN/OUT, длительности, источники, звук и неизменность прежних sequences; затем сохраняет и экспортирует 720p.
При ошибке не повторять поверх частичной сборки. Прислать native_status.txt и native_progress.log.
Review: `{job['video']}`. После запуска требуется проверка реального результата и прослушивание границ речи.
PREPARED не означает COMPLETE. SHORT — NOT STARTED. STOP.
'''
    (work/'START_RU.md').write_text(instructions,encoding='utf-8');copy_verified(work/'START_RU.md',package/'START_RU.md')
    assert digest(source)==v['checkpoint_sha256']
    print(json.dumps(status,ensure_ascii=False,indent=2))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser();parser.add_argument('task_id');parser.add_argument('--dry-run',action='store_true');a=parser.parse_args();prepare(a.task_id,a.dry_run)
