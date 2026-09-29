"""Prepare one protected project and two editable visual finish sequences."""
import argparse,json,sys,xml.etree.ElementTree as ET
from pathlib import Path
from prepare_full_master import ROOT,read,HELPERS
from classify import copy_verified,stamp
from ingest import digest,write_json
from utils import premiere_project as pp
from utils.premiere_sequence_motion import _track_item_contexts
from tools.prepare_plan_transitions import APPLY

# Relative FIT scale and normalized source centre. Explicit, image-specific choices.
FULL={
'FM001':(1,1.06,.5,.48,'Приближение к общему объятию, оба лица остаются вместе.'),
'FM018':(1,1.08,.5,.48,'Внимание от игрушки к общему интересу.'),
'FM020':(1.06,1,.5,.5,'Отъезд раскрывает совместное занятие и взрослых.'),
'FM021':(1,1.04,.5,.5,'Мягкое приближение к семейному столу.'),
'FM027':(1,1.06,.5,.5,'Связь взгляда и шахматной доски.'),
'FM029':(1.04,1,.5,.5,'От книг к двум читающим детям.'),
'FM033':(1.04,1,.5,.5,'Общий музейный контекст раскрывается отъездом.'),
'FM035':(1,1.025,.5,.5,'Акварель: почти неподвижная пауза.'),
'FM041':(1,1.04,.5,.5,'Два масштаба действия на площадке, сохранить обоих.'),
'FM042':(1.04,1,.5,.5,'Отъезд на общую пару на качелях.'),
'FM048':(1.05,1,.5,.5,'Раскрыть масштаб машины относительно детей.'),
'FM055':(1.04,1,.5,.5,'Открыть архитектурный контекст семейной прогулки.'),
'FM059':(1,1.05,.5,.49,'Лица на фоне воды.'),
'FM061':(1.04,1,.5,.5,'Раскрыть стадион вокруг семейной группы.'),
'FM063':(1,1.05,.5,.5,'Совместное действие с веслом.'),
'FM064':(1.04,1,.5,.5,'Отъезд к общему водному пространству.'),
'FM069':(1,1.025,.5,.5,'Акварель воды: короткое спокойное послесловие.'),
'FM078':(1,1.06,.5,.52,'Внимание к рукам и рисунку мелом.'),
'FM083':(1.04,1,.5,.5,'Общая семья важнее изолированного лица.'),
'FM085':(1,1.04,.5,.5,'Тихая близость во время чтения.'),
'FM091':(1.04,1,.5,.5,'Раскрыть совместное музыкальное занятие.'),
'FM092':(1,1.05,.5,.51,'Руки и совместное рассматривание предмета.'),
'FM095':(1.04,1,.5,.5,'Семейный общий план как пауза.'),
'FM096':(1,1.03,.5,.5,'Очень мягкое приближение к объятию.'),
'FM100':(1,1.04,.5,.49,'Лица и книга вместе.'),
'FM106':(1.03,1,.5,.5,'Финальный акварельный образ раскрывается и успокаивается.')}
SHORT={
'SM003':(1,1.07,.5,.48,'Быстрый акцент на общем интересе к игрушке.'),
'SM009':(1.05,1,.5,.5,'Короткий отъезд связывает двух участников лодки.'),
'SM017':(1,1.035,.5,.48,'Объятие: движение почти незаметно.'),
'SM020':(1.025,1,.5,.5,'Финальное раскрытие акварели, меньше движения чем в FULL.')}

def prepare(dry=False):
 task=ROOT/'tasks/BM26';work=task/'visual_finish';ss=read(task/'short/status.json');sv=read(task/'short/verification.json');fs=read(task/'full_master/status.json');fv=read(task/'full_master/verification.json')
 assert sv['status']==fv['status']=='TECHNICAL_PASS'
 source=Path(ss['project_checkpoint']);assert digest(source)==sv['checkpoint_sha256']
 root=pp.load_premiere_project_root(source);ids=pp.build_project_object_id_lookup(root);uids=pp.build_project_object_uid_lookup(root)
 versions=[]
 for label,status,profiles,bridges,dissolves in [('FULL',fs,FULL,['FM053','FM079'],[('FM063','FM064',10),('FM104','FM105',10),('FM105','FM106',10)]),('SHORT',ss,SHORT,['SM002'],[('SM019','SM020',6)])]:
  plan=read(status['plan']);seq=pp.find_project_sequence_node(root,plan['sequence_name']);assert seq is not None
  contexts=_track_item_contexts(seq,group_index=0,id_lookup=ids,uid_lookup=uids,project_path=source);assert len(contexts)==len(plan['clips'])
  for a,c in zip(contexts,plan['clips']):
   assert str(Path(a.source_path)).lower()==str(Path(c['path'])).lower()
   for x,y in [(a.start,c['timeline_start_frame']/25),(a.end,(c['timeline_start_frame']+c['frames'])/25),(a.source_in,c['source_in_seconds']),(a.source_out,c['source_out_seconds'])]:assert abs(x/254016000000-y)<.021
  motion=[];trans=[];lookup={c['id']:(i,c) for i,c in enumerate(plan['clips'])}
  for key,(z0,z1,cx,cy,reason) in profiles.items():
   i,c=lookup[key];assert c['kind']=='image'
   motion.append(dict(index=i,id=key,kind='art' if c['art_type'] else 'photo',keys=[[0,z0,.5,.5],[1,z1,cx,cy]],reason=reason))
  for key in bridges:
   i,c=lookup[key];assert c['art_type']
   motion.append(dict(index=i,id=key,kind='art',keys=[[0,1.04,.5,.5],[.4,1.075,.5,.49],[1,1,.5,.5]],reason='Короткий ART transmission: деталь → раскрытие художественного образа → следующий реальный кадр; существующая длительность сохранена.'))
  # High-resolution, stable musical close-ups only. No crop on active wide video shots.
  video_keys=['FM025','FM026'] if label=='FULL' else ['SM007','SM008']
  for key in video_keys:
   i,c=lookup[key];assert c['width']>=3840 and c['height']>=2160
   motion.append(dict(index=i,id=key,kind='video',keys=[[0,1.025,.5,.5],[1,1.025,.5,.5]],reason='Статичное приближение 2.5% к музыкальному действию; руки и инструмент сохраняются.'))
  for left,right,d in dissolves:
   li,l=lookup[left];ri,r=lookup[right];assert ri==li+1 and l['kind']==r['kind']=='image'
   trans.append(dict(left_index=li,right_index=ri,cut_frame=r['timeline_start_frame'],duration_frames=d,reason='Мягкая связь соседних неподвижных образов: общий контекст/семейная близость; CUT на живом действии сохранён.'))
  counts=dict(photo_animations=sum(m['kind']=='photo' for m in motion),art_animations=sum(m['kind']=='art' for m in motion),video_reframes=len(video_keys),transitions=len(trans),transmissions=len(bridges),unchanged_cuts=len(plan['clips'])-1-len(trans))
  versions.append(dict(label=label,source_sequence=plan['sequence_name'],output_sequence=f'BM26_{label}_FINISH_01',plan=plan,motion=motion,transitions=trans,transmissions=bridges,counts=counts))
 package=Path(read(task/'task.json')['classify']['permanent_project_dir'])/'VISUAL_FINISH'/'BM26_VISUAL_FINISH_01';project=source.with_name('BAM_26_BM_1_VISUAL_FINISH_01.prproj')
 assert not package.exists() and not project.exists()
 if dry:print(json.dumps({'status':'DRY RUN PASS','versions':[{v['label']:v['counts']} for v in versions]}));return
 work.mkdir(exist_ok=True);package.mkdir(parents=True);copy_verified(source,project)
 colors=dict(applied=False,notes=[{'scope':'FULL/SHORT indoor videos','note':'Сверить тёплые интерьерные источники со светом из окна; не выравнивать творческий ART под кожу.'},{'scope':'FM085/FM096/FM100/FM104 and SHORT SM018/SM019','note':'Проверить тени и разницу экспозиции тихого финала; сохранить вечернее настроение.'},{'scope':'all ART','note':'Отдельное обращение: белая бумага watercolor и цвет double exposure являются частью изображения.'}])
 write_json(work/'color_notes.json',colors);write_json(package/'color_notes.json',colors)
 preset=ET.parse(Path(ss['plan']).parent/'review_720p25_light.epr');preset.write(package/'review_light.epr',encoding='utf-8',xml_declaration=True)
 job=dict(project=project.as_posix(),preset=(package/'review_light.epr').as_posix(),versions=versions)
 for v in versions:v['review']=(package/(v['output_sequence']+'_REVIEW.mp4')).as_posix()
 write_json(work/'visual_finish.json',job);write_json(package/'visual_finish.json',job)
 script=(ROOT/'scripts/visual_finish_native.jsx').read_text(encoding='utf-8').replace('__HELPERS__',HELPERS[:HELPERS.index('function checkpoint')]).replace('__TRANSITIONS__',APPLY).replace('__JOB__',json.dumps(job,ensure_ascii=True))
 (package/'apply_visual_finish.jsx').write_text(script,encoding='utf-8');(work/'native_syntax_check.js').write_text(script,encoding='utf-8')
 status=dict(task_id='BM26',stage='VISUAL FINISH',status='VISUAL FINISH PREPARED — NATIVE RUN PENDING',timestamp=stamp(),source_project=str(source),source_project_sha256=digest(source),project_checkpoint=str(project),jsx=str(package/'apply_visual_finish.jsx'),plan=str(package/'visual_finish.json'),color_notes=str(package/'color_notes.json'),counts={v['label']:v['counts'] for v in versions},reviews={v['label']:v['review'] for v in versions},color_applied=False,execution_state='STOP',next='USER EXECUTES JSX; COLOR NOT STARTED')
 write_json(work/'status.json',status);write_json(package/'preparation_status.json',status)
 state=read(task/'state.json');state.update(stage='VISUAL FINISH',status=status['status'],visual_finish=status,next_stage=status['next']);state['stages']['VISUAL_FINISH']='PREPARED_NATIVE_NOT_RUN';write_json(task/'state.json',state)
 assert digest(source)==sv['checkpoint_sha256']==digest(project)
 print(json.dumps(status,ensure_ascii=True,indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--dry-run',action='store_true');a=p.parse_args();prepare(a.dry_run)
