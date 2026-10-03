"""Prepare a wide editorial checkpoint using the existing native assembly/export JSX."""
from pathlib import Path
import argparse
import copy
import json
import sys
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from ingest import digest,write_json,say
from classify import copy_verified,stamp
from utils import premiere_project as pp
from tools.prepare_native_export import JSX
from tools.prepare_native_finish import HELPERS,replace_once

def build_plan(bank,cfg):
    media={m['media_id']:m for m in bank['media']};clips=[];selected=set();cursor=0;fps=cfg['fps']
    for chapter in cfg['chapters']:
        for mid in chapter['media_ids']:
            if mid in selected:raise ValueError('Repeated media in editorial selection')
            selected.add(mid);m=media[mid];path=Path(m['source_path'])
            if not path.is_file():raise ValueError('Offline selected media: '+str(path))
            art=m.get('media_origin')=='generated_art';video=path.suffix.lower() in {'.mp4','.mov','.m4v','.avi','.mkv'}
            occurrences=[x['ingest_record'] for x in bank['timeline_items'] if x['media_id']==mid and 'Video' in x['ingest_record']['track_type']]
            ranges=[]
            if video:
                known=sorted(set((x['source_in_ticks'],x['source_out_ticks']) for x in occurrences if x['source_in_ticks'] is not None and x['source_out_ticks'] is not None))
                for start,end in known:
                    if end<=start:raise ValueError('Invalid source bounds')
                    if ranges and start<=ranges[-1][1]:ranges[-1]=(ranges[-1][0],max(end,ranges[-1][1]))
                    else:ranges.append((start,end))
                if not ranges:raise ValueError('No verified video bounds')
            else:ranges=[(0,int((cfg['art_seconds'] if art else cfg['photo_seconds'])*pp.PREMIERE_TICKS_PER_SECOND))]
            for segment,(start,end) in enumerate(ranges,1):
                frames=round((end-start)*fps/pp.PREMIERE_TICKS_PER_SECOND)
                item=dict(order=len(clips)+1,id=f'WM{len(clips)+1:03d}',media_id=mid,path=str(path),source_path=str(path),kind='video' if video else 'image',
                    media_origin='generated_art' if art else 'premiere_inventory',art_id=m.get('art_id'),art_type=m.get('art_type'),
                    source_family_id=m.get('source_family_id',mid),parent_source_media_id=m.get('parent_source_media_id'),
                    source_in_seconds=start/pp.PREMIERE_TICKS_PER_SECOND,source_out_seconds=start/pp.PREMIERE_TICKS_PER_SECOND+frames/fps,
                    original_source_in_ticks=start,original_source_out_ticks=end,duration_seconds=frames/fps,frames=frames,timeline_start_frame=cursor,
                    chapter_id=chapter['id'],chapter_title=chapter['title'],semantic_function=chapter['purpose'],reason=m['scene']['summary'],
                    selection_reason='Содержательный эпизод сохранён в исходном выбранном диапазоне; без временной компрессии.' if video else 'Спокойно читаемый кадр раскрывает действие/отношение и меняет масштаб внутри главы.',
                    identity=m.get('identity',{'category':'UNKNOWN'}),source_stat={'size':path.stat().st_size,'mtime_ns':path.stat().st_mtime_ns},speed=1.0)
                if art:item['selection_reason']='Художественная пауза/обобщение главы; самостоятельный кандидат, не новое событие.'
                clips.append(item);cursor+=frames
    families={}
    for c in clips:families.setdefault(c['source_family_id'],set()).add(c['media_id'])
    related=[{'source_family_id':f,'media_ids':sorted(ids),'reason':'Разнесённые наблюдение и художественное обобщение/финальная рифма; это одно source family, не два события.'} for f,ids in families.items() if len(ids)>1]
    excluded=[]
    for m in bank['media']:
        if m['media_id'] in selected:continue
        reason=('Сохранён в ART bank; другая интерпретация/та же source family, нет отдельной функции в этом плане.' if m.get('media_origin')=='generated_art' else
                'Ранее импортированный художественный derivative: сохранён в банке, предпочтены актуальные ART с проверенными parent links.' if Path(m['source_path']).suffix.lower()=='.png' else
                'Другой кадр близкого события/контекста; выбран более ясный или самостоятельный эпизод. Не технический DROP; доступен для восстановления.')
        excluded.append({'media_id':m['media_id'],'path':m['source_path'],'decision':'NOT_SELECTED_THIS_VERSION','reason':reason,'evidence':m['scene']['summary']})
    counts={'original_video':sum(Path(media[mid]['source_path']).suffix.lower() in {'.mp4','.mov','.m4v','.avi','.mkv'} for mid in selected),
            'original_photo':sum(media[mid].get('media_origin')!='generated_art' and Path(media[mid]['source_path']).suffix.lower() not in {'.mp4','.mov','.m4v','.avi','.mkv'} for mid in selected),
            'watercolor':sum(media[mid].get('art_type')=='watercolor' for mid in selected),'double_exposure':sum(media[mid].get('art_type')=='double_exposure' for mid in selected)}
    return dict(schema_version=1,task_id=cfg['task_id'],sequence_name=cfg['sequence_name'],bank_sha256=cfg['bank_sha256'],fps=fps,frames=cursor,duration_seconds=cursor/fps,duration_target=None,duration_was_not_a_target=True,
                width=cfg['width'],height=cfg['height'],available_media=len(media),selected_media=len(selected),selected_counts=counts,clips=clips,chapters=cfg['chapters'],same_source_families=related,not_selected=excluded,
                limitations=['CLASSIFY video analysis used sampled frames; no full listening or precise empty-tail decisions were invented.','Individual identities remain unverified; no invented biography, relationships or chronology.','Native run, full audiovisual review and 720p export QA are pending.'],
                audio_policy=cfg['audio_policy'],music_added=False,animation=False,color_correction=False,transitions=False)

def validate(plan):
    cursor=0
    for c in plan['clips']:
        if c['timeline_start_frame']!=cursor or c['frames']<=0 or c['speed']!=1:raise ValueError('Invalid wide timeline')
        if abs(c['duration_seconds']*plan['fps']-c['frames'])>1e-6:raise ValueError('Frame duration mismatch')
        cursor+=c['frames']
    if cursor!=plan['frames'] or plan['duration_target'] is not None:raise ValueError('Invalid plan total/target')

def script(job):
    # Reuse native import, bounds conformance, audio checks and export; add protected clone.
    helpers=HELPERS[:HELPERS.index('function checkpoint(){')]
    s=replace_once(JSX,'try{',helpers+'\ntry{')
    guard="""
 for(var si=0;si<app.project.sequences.numSequences;si++){
  var oldSeq=app.project.sequences[si];
  if(oldSeq.name===job.output_sequence)throw Error('WIDE sequence already exists: refusing overwrite');
  checkpoints.push({sequence:oldSeq,signature:signature(oldSeq)});
 }
 seq=cloneNamed(seq,job.output_sequence);
"""
    s=replace_once(s," step('get sequence settings');",guard+"\n step('get sequence settings');")
    s=replace_once(s," step('activate sequence');", " verifyCheckpoints();\n step('activate sequence');")
    s=replace_once(s,"step('save project copy');app.project.save();state('NATIVE_TIMELINE_CHECKED_EXPORTING');", "step('save project copy');app.project.save();var disk=new File(job.project);if(!disk.exists || disk.length<=0)throw Error('Saved checkpoint missing');state('NATIVE_TIMELINE_CHECKED_EXPORTING');")
    return s.replace('__JOB__',json.dumps(job,ensure_ascii=True))

def prepare(task_id,dry_run=False):
    task=ROOT/'tasks'/task_id;cfg=json.loads((task/'wide_master_config.json').read_text(encoding='utf-8'));state=json.loads((task/'state.json').read_text(encoding='utf-8'))
    if state['stages'].get('CLASSIFY_ART')!='CLASSIFY + ART COMPLETE':raise ValueError('WIDE MASTER NOT STARTED: CLASSIFY + ART incomplete')
    pointer=json.loads((task/'classified_media_bank.json').read_text(encoding='utf-8'));bank_path=Path(pointer['classification_path'])
    if digest(bank_path)!=pointer['sha256'] or pointer['sha256']!=cfg['bank_sha256']:raise ValueError('Unified bank changed')
    bank=json.loads(bank_path.read_text(encoding='utf-8'));plan=build_plan(bank,cfg);validate(plan)
    info=json.loads((task/'task.json').read_text(encoding='utf-8'));source=Path(info['paths']['premiere_project']);project_hash=digest(source)
    root=pp.load_premiere_project_root(source);template=pp.find_project_sequence_node(root,cfg['template_sequence'])
    if template is None:raise ValueError('Template missing')
    if pp.find_project_sequence_node(root,cfg['sequence_name']) is not None:raise ValueError('Output sequence already exists in source project')
    ids,uids=pp.build_project_object_id_lookup(root),pp.build_project_object_uid_lookup(root)
    for g in (0,1):
        for _,tr in pp.get_project_track_nodes(template,track_group_index=g,object_id_lookup=ids,object_uid_lookup=uids):
            if pp.iter_project_track_item_refs(tr):raise ValueError('Template not empty')
    group=ids[template.find('./TrackGroups/TrackGroup[@Index="0"]/Second').get('ObjectRef')]
    if int(group.findtext('./TrackGroup/FrameRate'))!=pp.PREMIERE_TICKS_PER_SECOND//cfg['fps']:raise ValueError('Template FPS mismatch')
    native=Path(info['classify']['permanent_project_dir']).resolve();package=native/'WIDE_MASTER'/cfg['sequence_name']
    project=source.parent/(source.stem+'_WIDE_MASTER_01.prproj')
    preset_source=Path(cfg['review_preset']);preset=ET.parse(preset_source)
    for n in preset.getroot().iter('ExporterParam'):
        name=n.findtext('ParamIdentifier');values={'ADBEVideoWidth':'1280','ADBEVideoHeight':'720','ADBEVideoFPS':str(pp.PREMIERE_TICKS_PER_SECOND//cfg['fps']),'ADBEVideoMatchSource':'false','ADBEVideoTargetBitrate':'4.','ADBEVideoMaxBitrate':'6.','ADBEVideoBitrateEncoding':'1'}
        if name in values:n.find('ParamValue').text=values[name]
    if dry_run:say(json.dumps({'status':'DRY RUN PASS','selected':plan['selected_counts'],'duration_seconds':plan['duration_seconds'],'timeline_clips':len(plan['clips'])}));return
    if project.exists() or package.exists():raise FileExistsError('Checkpoint/package already exists; no overwrite')
    package.mkdir(parents=True);copy_verified(source,project)
    if digest(source)!=project_hash:raise ValueError('Source project changed during copy')
    work=task/'wide_master';work.mkdir(exist_ok=True);write_json(work/'wide_master.json',plan);copy_verified(work/'wide_master.json',package/'wide_master.json')
    copy_verified(bank_path,package/'classified_media_bank_snapshot.json')
    copy_verified(task/'wide_master_config.json',package/'wide_master_config.json')
    preset_path=package/'review_720p25.epr';preset.write(preset_path,encoding='utf-8',xml_declaration=True)
    job=dict(project=project.as_posix(),sequence=cfg['template_sequence'],output_sequence=cfg['sequence_name'],plan=plan,video=(package/(cfg['sequence_name']+'_REVIEW.mp4')).as_posix(),preset=preset_path.as_posix(),width=cfg['width'],height=cfg['height'])
    jsx=package/'assemble_wide_master.jsx';jsx.write_text(script(job),encoding='utf-8');write_json(package/'job.json',job)
    status=dict(task_id=task_id,status='WIDE MASTER PREPARED — NATIVE RUN PENDING',timestamp=stamp(),classify_art='VERIFIED',available_media=len(bank['media']),selected_media=plan['selected_media'],selected_counts=plan['selected_counts'],planned_duration_seconds=plan['duration_seconds'],duration_was_not_a_target=True,plan=str(package/'wide_master.json'),project_checkpoint=str(project),source_project=str(source),source_project_sha256=project_hash,prepared_project_sha256=digest(project),sequence=cfg['sequence_name'],jsx=str(jsx),review=job['video'],native_sequence_created=False,review_created=False,source_media_modified=False,source_sequence_modified=False,execution_state='STOP',next='USER RUNS JSX; DENSE MASTER NOT STARTED')
    write_json(work/'status.json',status);write_json(package/'preparation_status.json',status)
    instructions=f'''# WIDE MASTER — ручной запуск

1. Открыть в Premiere отдельную копию: `{project}`.
2. Выбирать sequence не требуется. Скрипт клонирует пустую {cfg['template_sequence']} в новую {cfg['sequence_name']}; существующие sequence защищены проверками.
3. Запустить монитор: `scripts\\watch_wide_master.bat {task_id}` из корня repository.
4. Через Run Transition Script выполнить `{jsx}`.
5. Дождаться сохранения проекта и экспорта 720p: `{job['video']}`. Скрипт содержит прямые склейки, исходный звук видео, без добавленной музыки и отделки.
6. Сообщить результат. При ошибке не запускать поверх частичной сборки; прислать native_status.txt и native_progress.log из этой папки. Файлы не удалять.

PREPARED не означает NATIVE COMPLETE. Просмотреть экспорт со звуком; после этого агент проверит проект/sequence и MP4. STOP.
'''
    (work/'START_RU.md').write_text(instructions,encoding='utf-8');copy_verified(work/'START_RU.md',package/'START_RU.md')
    state.setdefault('history',[]).append(status);state.setdefault('stages',{})['WIDE_MASTER']='PREPARED_NATIVE_NOT_RUN';state['wide_master']=status;state.update(stage='WIDE MASTER',status=status['status'],execution_state='STOP',next_stage='USER EXECUTES PREMIERE JSX');write_json(task/'state.json',state)
    say(json.dumps(status,ensure_ascii=False,indent=2))

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser();parser.add_argument('task_id');parser.add_argument('--dry-run',action='store_true');a=parser.parse_args();prepare(a.task_id,a.dry_run)
