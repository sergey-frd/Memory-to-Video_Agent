"""Prepare SHORT only from verified FULL. User executes the native stage."""
import argparse,copy,json,sys,xml.etree.ElementTree as ET
from pathlib import Path
from prepare_full_master import ROOT,read,make_xml,HELPERS
from ingest import digest,write_json
from classify import copy_verified,stamp
from utils import premiere_project as pp
from utils.premiere_sequence_motion import _track_item_contexts

# Absolute source seconds; still ranges specify screen duration. Deliberate new order.
SHOTS=[
('FM052',3,10,1,'HOOK','Игра с краской: действие сразу, оба участника в кадре.'),
('FM053',0,1.2,1,'HOOK','Короткий художественный отклик на цветную игру.'),
('FM018',0,1.6,1,'DEVELOPMENT','Общий интерес и совместное внимание к игрушке.'),
('FM006',6,10,1,'DEVELOPMENT','Самостоятельный характер: костюм, жест, уверенный выход.'),
('FM009',0,4,2,'DEVELOPMENT','Быстрый подход перед другим выступлением.'),
('FM010',7,13,1,'DEVELOPMENT','Акробатическое действие в нормальной скорости.'),
('FM025',12,18,1,'DEVELOPMENT','Сосредоточенность за пианино; сохранить музыкальный звук.'),
('FM026',30,36,1,'DEVELOPMENT','Другой музыкальный характер: барабаны, исходный звук.'),
('FM063',0,1.6,1,'DEVELOPMENT','Два участника в лодке — от индивидуальных занятий к общему делу.'),
('FM040',3,9,1,'PEAK','Совместный спуск и взаимопомощь: отношения через действие.'),
('FM046',10,14,1,'PEAK','Короткий прыжок на склоне; не ускорять само движение.'),
('FM065',1,5,1,'PEAK','Прыжок в воду и всплеск поддерживают энергию.'),
('FM049',0,1.2,1,'PEAK','Вместе на аттракционе — мгновенно читаемый акцент.'),
('FM052',20,27,1,'PEAK','Возврат к краске: обмен ролями завершает начатую в hook сцену.'),
('FM082',39,45,1,'PEAK','Совместная радость при рассматривании подарка.'),
('FM014',12,17,1,'ENDING','Два лица рядом, улыбки: замедление ритма.'),
('FM001',0,2.4,1,'ENDING','Объятие как ясный образ связи.'),
('FM099',84,89,1,'ENDING','Близость во время чтения; спокойная человеческая скорость.'),
('FM104',0,2.4,1,'ENDING','Семейное тепло — короткая пауза перед последним образом.'),
('FM106',0,3.2,1,'ENDING','Акварельное объятие завершает фильм, не начинает новую сцену.')]

def prepare(dry=False):
 task=ROOT/'tasks/BM26';work=task/'short';s=read(task/'full_master/status.json');v=read(task/'full_master/verification.json')
 assert s['native_sequence_verified'] and v['status']=='TECHNICAL_PASS'
 source=Path(s['project_checkpoint']);assert digest(source)==v['checkpoint_sha256']
 full=read(s['plan']);byid={c['id']:c for c in full['clips']}
 root=pp.load_premiere_project_root(source);seq=pp.find_project_sequence_node(root,'BM26_FULL_MASTER_01');assert seq is not None
 rows=_track_item_contexts(seq,group_index=0,id_lookup=pp.build_project_object_id_lookup(root),uid_lookup=pp.build_project_object_uid_lookup(root),project_path=source)
 assert len(rows)==len(full['clips'])
 T=pp.PREMIERE_TICKS_PER_SECOND
 for a,c in zip(rows,full['clips']):
  assert str(Path(a.source_path)).lower()==str(Path(c['path'])).lower()
  assert abs(a.source_in-c['source_in_seconds']*T)<=T/50
  assert abs(a.source_out-c['source_out_seconds']*T)<=T/50
 clips=[];cursor=0
 for order,(key,lo,hi,speed,chapter,reason) in enumerate(SHOTS,1):
  parent=byid[key];assert parent['source_in_seconds']<=lo<hi<=parent['source_out_seconds']+1e-6
  assert Path(parent['path']).is_file()
  c=copy.deepcopy(parent);frames=round((hi-lo)*25/speed);assert abs(frames/25*speed-(hi-lo))<1e-6
  c.update(id=f'SM{order:03d}',short_order=order,source_full_item=key,source_in_seconds=lo,source_out_seconds=hi,speed=speed,frames=frames,
   timeline_start_frame=cursor,duration_seconds=frames/25,audio='source' if parent['kind']=='video' and speed==1 else 'muted',
   chapter=chapter,dramaturgical_function=chapter,reason=reason,reason_for_selection=reason,
   full_range_seconds=[parent['timeline_start_frame']/25,(parent['timeline_start_frame']+parent['frames'])/25],
   selected_full_range_seconds=[parent['timeline_start_frame']/25+(lo-parent['source_in_seconds'])/parent['speed'],parent['timeline_start_frame']/25+(hi-parent['source_in_seconds'])/parent['speed']],
   short_range_seconds=[cursor/25,(cursor+frames)/25],media_origin='ART' if parent['art_type'] else 'original')
  clips.append(c);cursor+=frames
 plan=dict(schema_version=1,task_id='BM26',sequence_name='BM26_SHORT_MASTER_01',source_sequence='BM26_FULL_MASTER_01',fps=25,width=3840,height=2160,
  frames=cursor,duration_seconds=cursor/25,full_duration_seconds=full['duration_seconds'],duration_target=None,clips=clips,structure=['HOOK','DEVELOPMENT','PEAK','ENDING'],returned_from_wide=[],
  source_project=str(source),source_project_sha256=digest(source),source_plan_sha256=digest(Path(s['plan'])),
  limitations=['No per-media name identification: solo moments show distinct children without assigning names.', 'Selected ranges based on prior visual evidence; speech/music cut boundaries require listening review.'],
  music_added=False,finishing=False)
 stats=dict(duration_seconds=cursor/25,items=len(clips),video=sum(c['kind']=='video' for c in clips),photos=sum(c['kind']=='image' and not c['art_type'] for c in clips),watercolor=sum(c['art_type']=='watercolor' for c in clips),double_exposure=sum(c['art_type']=='double_exposure' for c in clips),variable_speed_video=1,returned_from_wide=0)
 plan['stats']=stats
 xml=make_xml(plan,full).replace('bm26-full-master-01','bm26-short-master-01');ET.fromstring(xml)
 project=source.with_name('BAM_26_BM_1_SHORT_MASTER_01.prproj')
 info=read(task/'task.json');package=Path(info['classify']['permanent_project_dir'])/'SHORT_MASTER'/'BM26_SHORT_MASTER_01'
 assert not project.exists() and not package.exists(),'Refusing overwrite'
 if dry:print(json.dumps({'status':'DRY RUN PASS',**stats}));return
 # Script and editorial plan exist before Adobe sequence creation.
 write_json(work/'short_master.json',plan)
 story=['# SHORT SCRIPT — Братья','', 'Краска и игра → два разных характера → общее действие и обмен ролями → близость.','', 'Разные события соединены тематически, без заявления об общей хронологии. Имена отдельным лицам не присваиваются.','']
 for c in clips:story.append(f"{c['id']} | {c['chapter']} | {c['short_range_seconds']} | {c['source_full_item']} | {c['reason']}")
 (work/'SHORT_SCRIPT_RU.md').write_text('\n'.join(story)+'\n',encoding='utf-8')
 package.mkdir(parents=True);copy_verified(source,project)
 for name in ['short_master.json','SHORT_SCRIPT_RU.md','request.md']:copy_verified(work/name,package/name)
 (package/'SHORT_TIMELINE.xml').write_text(xml,encoding='utf-8')
 preset=ET.parse(Path(s['plan']).parent/'review_720p25.epr')
 for n in preset.getroot().iter('ExporterParam'):
  key=n.findtext('ParamIdentifier')
  if key=='ADBEVideoTargetBitrate':n.find('ParamValue').text='1.2'
  if key=='ADBEVideoMaxBitrate':n.find('ParamValue').text='2.'
 preset.write(package/'review_720p25_light.epr',encoding='utf-8',xml_declaration=True)
 job=dict(project=project.as_posix(),sequence=plan['sequence_name'],source_sequence=plan['source_sequence'],plan=plan,xml=(package/'SHORT_TIMELINE.xml').as_posix(),preset=(package/'review_720p25_light.epr').as_posix(),video=(package/'BM26_SHORT_MASTER_01_REVIEW.mp4').as_posix())
 script=(ROOT/'scripts/full_master_native.jsx').read_text(encoding='utf-8').replace('FULL','SHORT').replace('WIDE absent','FULL absent')
 script=script.replace('__HELPERS__',HELPERS[:HELPERS.index('function cloneNamed')]).replace('__JOB__',json.dumps(job,ensure_ascii=True))
 (package/'assemble_short_master.jsx').write_text(script,encoding='utf-8');(work/'native_syntax_check.js').write_text(script,encoding='utf-8');write_json(package/'job.json',job)
 status=dict(task_id='BM26',stage='SHORT',status='SHORT PREPARED — NATIVE RUN PENDING',timestamp=stamp(),stats=stats,plan=str(package/'short_master.json'),project_checkpoint=str(project),source_project=str(source),source_project_sha256=digest(source),sequence=plan['sequence_name'],jsx=str(package/'assemble_short_master.jsx'),review=job['video'],native_sequence_created=False,review_created=False,execution_state='STOP',next='USER EXECUTES JSX; FINISHING NOT STARTED')
 write_json(work/'status.json',status);write_json(package/'preparation_status.json',status)
 state=read(task/'state.json');state.update(stage='SHORT',status=status['status'],short=status,next_stage=status['next']);state['stages']['SHORT']='PREPARED_NATIVE_NOT_RUN';write_json(task/'state.json',state)
 assert digest(source)==v['checkpoint_sha256']==digest(project)
 print(json.dumps(status,ensure_ascii=True,indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--dry-run',action='store_true');a=p.parse_args();prepare(a.dry_run)
