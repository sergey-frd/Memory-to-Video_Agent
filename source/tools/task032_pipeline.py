"""TASK_032: JSON-driven, transactional Premiere project editing.

plan -> dry-run (memory only) -> apply. The source is never written.
Native Tint and the native export are a separate logged phase of the same JSON.
"""
from __future__ import annotations
import argparse, copy, gzip, hashlib, json, math, re, sys, uuid
from pathlib import Path
import xml.etree.ElementTree as ET
import cv2
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.task032_preflight import SOURCE, NAME, OUT, write
from utils.premiere_project import *
from utils.premiere_sequence_motion import (_track_item_contexts,_motion_params,_baseline_scale,_baseline_position,
    build_scale_keyframes,build_position_keyframes,_set_param_keyframes)
from utils.premiere_project_export import (clone_named_sequence,_ProjectObjectIdAllocator,
    _update_sequence_duration_metadata,_find_sequence_masterclip)
from utils.premiere_media_import_export import _clone_filter_component
from utils.premiere_sequence_timeline_assembly import _validate_all_refs
from utils.premiere_trim_review_export import _reindex_track_items

FT=10160640000
W,H=3840,2160
TARGET='SF_26_Bd_Art_6_TASK032_EXTREME_FINAL'
BG='TASK032_BRONZE_OCHRE_BACKGROUND'
DEST=SOURCE.parent/'SF_26_Bd_Art_4_TASK032_EXTREME_FINAL.prproj'
CHECKPOINT=SOURCE.parent/'SF_26_Bd_Art_4_TASK032_EDIT_CHECKPOINT.prproj'
BACKUP=SOURCE.parent/'SF_26_Bd_Art_3_TASK031_before_TASK_032.prproj'
PREVIEW=SOURCE.parent/(TARGET+'_640_360.mp4')
REPORT_DIR=SOURCE.parent/OUT.name
SHA='8fb4d5bb42d14262064e1063af530372721b709ef097f3848c9ec39fa9e65dd5'
PID={'temperature':'7','tint':'8','saturation':'20','exposure':'11','contrast':'12','highlights':'13','shadows':'14','whites':'15','blacks':'16','sharpen':'29','vibrance':'30','vignette':'51'}
SEM={
'926':('Молодой Сергей за столом','Импульс начала','young'),
'927':('Юношеский портрет Сергея','Личность автора','young'),
'928':('Сергей с маленьким ребёнком в декоративной рамке','Раннее отцовство, подготовка темы продолжения','young'),
'929':('Сергей держит ребёнка','Отцовство','young'),
'930':('Молодой Сергей, крупный портрет','Переход к автору чеканок','young'),
'931':('Сергей работает рядом с рисунками и чеканкой','Творческий процесс','workshop'),
'932':('Мастерская, работы на стене и чеканка','Пространство творчества','workshop'),
'970':('Вырезанный силуэт Сергея в мастерской','Автор в пространстве работ','workshop'),
'933':('Мастерская с чеканкой под двойной экспозицией','Личное и художественное','workshop'),
'971':('Парный портрет в двойной экспозиции','Близость и искусство','young'),
'934':('Линейный рисунок и рельефное лицо','Рисунок превращается в объём','bronze'),
'935':('Скульптура в зале','Пластическое соответствие','sculpture'),
'972':('Второй слой той же скульптуры','Авторская многослойность','sculpture'),
'936':('Вертикальная бронзовая чеканка','Собственное искусство','bronze'),
'973':('Второй слой чеканки','Крупность и объём','bronze'),
'937':('Рельефная фигура в металле','Пластическая рифма со скульптурой','bronze'),
'938':('Скрипач и чеканка, первый вариант','Повтор следующего сопоставления','bronze'),
'939':('Скрипач и чеканка, выразительный второй вариант','Связь музыки и металла','bronze'),
'940':('Портрет композитора и чеканка','Течение музыки и времени','bronze'),
'941':('Портрет молодого бородатого Сергея','Возвращение к автору','young'),
'942':('Живописный портрет','Художественный контрапункт','painting'),
'974':('Второй слой живописного портрета','Многослойность','painting'),
'943':('Современный Сергей в шапке, автопортрет','Время проходит','modern'),
'944':('Живописный портрет и металлический лик','Внешние художественные соответствия','bronze'),
'945':('Современная графическая интерпретация женского профиля','Форма и её исходник','bronze'),
'975':('Исходная чеканка женского профиля','Сопоставление оригинала и интерпретации','bronze'),
'946':('Женщина и чеканка лица','Живой источник художественной формы','bronze'),
'947':('Скульптурная голова, короткая вставка','Короткий повтор пластической мысли','sculpture'),
'948':('Женский профиль и бронзовая форма','Связь жизни и чеканки','bronze'),
'949':('Молодая пара и два металлических лица','Личное в художественном','bronze'),
'950':('Собственная вытянутая скульптурная фигура','Пластика движения','bronze'),
'951':('Роден, выразительный силуэт','Пластическое соответствие','sculpture'),
'952':('Сергей за рабочим столом в зрелости','Подготовка нового инструмента','modern'),
'953':('Матисс, Танец','Движение, круг жизни','painting'),
'954':('Круглая ретикульная чеканка','Ритм танца в металле','bronze'),
'955':('Матисс и крупная чеканка в едином изображении','Ясное доказательство соответствия','painting'),
'956':('Повтор Матисса с меньшей чеканкой','Дублирует уже показанное соответствие','painting'),
'957':('Круг танцующих фигур в чеканке','Замыкание художественного блока','bronze'),
'958':('Сергей в живописной стилизации за столом','Новые цифровые формы','modern'),
'959':('Акварельная версия того же портрета','Материальность рисунка','painting'),
'960':('Ренессансная стилизация Сергея','Цифровая живопись','bronze'),
'961':('Анимированная ренессансная стилизация','От статики к видео','video'),
'962':('Зрелый Сергей за рабочим столом','Жизнь и время','modern'),
'963':('Сергей с камерой Nikon','Камера как продолжение творчества','modern'),
'964':('Сергей монтирует видео за двумя мониторами','Компьютер — творческий инструмент','computer'),
'965':('Сергей и семейные кадры на монтажных мониторах','Семейная память создаётся сейчас','computer'),
'966':('Монитор с семейным видео и Сергей рядом','Прямой мост к Нури','computer'),
'967':('Сергей с Нури на фоне долины','Продолжение жизни','nuri'),
'968':('Живое движение Сергея и Нури в той же сцене','Развитие финального образа','nuri_video'),
'969':('Золотистый свет вокруг Сергея и Нури','Свет, продолжение творчества','final')}

from utils.premiere_art_runtime import configure_module
configure_module(globals(), "032")

def dump(name, data):write(name,data)
def txt(name,s):(OUT/name).write_text(s.rstrip()+'\n',encoding='utf8')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def items(root,name,group=0):
    return _track_item_contexts(find_project_sequence_node(root,name),group_index=group,
        id_lookup=build_project_object_id_lookup(root),uid_lookup=build_project_object_uid_lookup(root),project_path=SOURCE)
def comps(item,ids):
    ref=item.track_item_node.find('ClipTrackItem/ComponentOwner/Components')
    chain=ids[ref.get('ObjectRef')]
    return [(ref,ids[ref.get('ObjectRef')]) for ref in chain.findall('ComponentChain/Components/Component')]
def params(comp,ids):
    return {ids[p.get('ObjectRef')].findtext('ParameterID'):ids[p.get('ObjectRef')] for p in comp.findall('Component/Params/Param')}
def settext(n,key,value):
    child=n.find(key)
    if child is None:child=ET.SubElement(n,key)
    child.text=str(value)
def static(param,value):
    s=param.findtext('StartKeyframe')
    assert s is not None,(param.tag,param.findtext('Name'))
    parts=s.split(',');parts[1]=str(value);param.find('StartKeyframe').text=','.join(parts)
    for tag in ['Keyframes','IsTimeVarying','CurrentValue']:
        for n in param.findall(tag):param.remove(n)
def dimensions(path):
    p=Path(path)
    if p.suffix.lower() not in ['.mp4','.mov','.avi']:
        with Image.open(p) as im:return list(im.size)
    c=cv2.VideoCapture(str(p));d=[int(c.get(cv2.CAP_PROP_FRAME_WIDTH)),int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))];c.release();return d
def neutral_lumetri(root,ids):
    candidates=[c for c in root.iter('VideoFilterComponent') if c.findtext('MatchName')=='AE.ADBE Lumetri']
    for c in candidates:
        ps=params(c,ids)
        if all(float((ps[pid].findtext('StartKeyframe') or '').split(',')[1])==(100 if k=='saturation' else 0) for k,pid in PID.items()):return c
    raise RuntimeError('Нет нейтрального шаблона Lumetri')
def color_values(bucket,ordinal,path):
    base={'temperature':0,'tint':0,'saturation':117,'exposure':0.25,'contrast':38,'highlights':-35,'shadows':25,'whites':8,'blacks':-10,'sharpen':4,'vibrance':8,'vignette':-0.7}
    if bucket=='young':base.update(temperature=-4,saturation=110,contrast=30,exposure=.12,highlights=-28,shadows=22,blacks=-5,vignette=-.35)
    elif bucket=='workshop':base.update(temperature=6,saturation=115,contrast=35,exposure=.24,shadows=30,highlights=-40,vignette=-.45)
    elif bucket=='bronze':base.update(temperature=8+(ordinal%3)*2,saturation=120,contrast=42+(ordinal%3)*4,exposure=.25,shadows=38,blacks=-8,highlights=-42,whites=12,vignette=-.85)
    elif bucket=='sculpture':base.update(temperature=7,saturation=110,contrast=42,exposure=.15,shadows=35,highlights=-40,blacks=-10,vignette=-.65)
    elif bucket=='painting':base.update(temperature=0,saturation=108,contrast=28,exposure=.15,shadows=20,highlights=-35,blacks=-4,vignette=-.3,sharpen=2)
    elif bucket in ['modern','computer']:base.update(temperature=-3,saturation=112,contrast=30,exposure=.32,shadows=32,highlights=-45,blacks=-2,vignette=-.3)
    elif bucket in ['nuri','nuri_video']:base.update(temperature=3,saturation=113,contrast=26,exposure=.85,shadows=42,highlights=-48,whites=10,blacks=8,vignette=-.2,sharpen=2)
    elif bucket=='final':base.update(temperature=-6,saturation=110,contrast=25,exposure=.2,shadows=32,highlights=-52,whites=-8,blacks=8,vignette=-.15,sharpen=1)
    elif bucket=='video':base.update(temperature=2,saturation=116,contrast=34,exposure=.3,shadows=35,highlights=-42,vignette=-.4)
    # Per-clip measured input luminance; do not apply the same grade blindly.
    metrics={}
    if Path(path).suffix.lower() not in ['.mp4','.mov']:
        with Image.open(path) as im:
            a=np.asarray(im.convert('RGB').resize((256,144)),dtype=float)/255
        y=a@np.array([.2126,.7152,.0722]);metrics={'mean':round(float(y.mean()),4),'p05':round(float(np.quantile(y,.05)),4),'p95':round(float(np.quantile(y,.95)),4)}
        if metrics['mean']<.24 and bucket=='bronze':base['exposure']=.52;base['shadows']=44;base['blacks']=1
        if metrics['p95']>.94:base['highlights']=-50;base['whites']=-8
    return base,metrics

def plan():
    assert sha(SOURCE)==SHA,'Исходник изменён'
    root=load_premiere_project_root(SOURCE);ids=build_project_object_id_lookup(root)
    rows=json.loads((OUT/'TASK_032_TIMELINE_MANIFEST.json').read_text(encoding='utf8'))[NAME]
    main=[r for r in rows if r['group']=='video' and r['track']==1]
    trims={'926':85,'927':85,'928':65,'929':65,'930':90,'939':100,'967':100}
    removes={'938','947','956'}
    beats=[];cursor=0
    for r in main:
        ident=r['identity']['ObjectID'];length=0 if ident in removes else trims.get(ident,int(r['out']-r['in']))
        beats.append({'source_in':int(r['in']),'source_out':int(r['out']),'output_in':cursor,'output_out':cursor+length,'main_id':ident})
        cursor+=length
    ops=[];animation=[];colors=[];audit=[];bgops=[]
    source_items={i.track_item_node.get('ObjectID'):i for i in items(root,NAME)}
    for r in rows:
        if r['group']!='video' or r['track']==0:continue
        ident=r['identity']['ObjectID']; b=next(b for b in beats if b['source_in']==r['in'])
        length=b['output_out']-b['output_in'];oldlen=int(r['out']-r['in']);kind='REMOVE' if length==0 else 'TRIM' if length!=oldlen else 'KEEP'
        content,function,bucket=SEM[ident]
        reason='Убрать повтор уже показанной мысли' if kind=='REMOVE' else 'Уплотнить ритм без потери жеста и смысла' if kind=='TRIM' else 'Сохранить уникальный смысловой вклад кадра'
        identity={'object_id':ident,'sequence':NAME,'track':r['track'],'name':r['name'],'timeline_in':int(r['in']),'timeline_out':int(r['out']),'source_in':int(r['source_in']),'source_out':int(r['source_out']),'path':r['path']}
        op={'operation_id':'EDIT_'+ident,'type':kind,'clip_identity':identity,'output_in':b['output_in'],'output_out':b['output_out'],'new_source_in':int(r['source_in']),'new_source_out':int(r['source_in'])+length,
            'preconditions':['Совпадают identity, source и timeline IN/OUT','Исходник имеет зафиксированный SHA256'],
            'postconditions':['Новая длительность и координаты строго соответствуют JSON','Другие слои той же сцены сдвинуты синхронно'],
            'reason':reason,'confidence':.97,'risk':'низкий' if kind!='REMOVE' else 'средний: убирается вариант, основной образ остаётся',
            'fallback':'Остановить всю транзакцию','rollback':'Перечитать неизменённый исходник; готовый output не перезаписывать'}
        ops.append(op)
        info={'clip_identity':identity,'content':content,'dramaturgical_function':function,'recommendation':kind,'reason':reason,'quality':'Источник просмотрен; исходный экспорт Premiere проверен покадровым декодированием и секундными контактами','confidence':.95,'risk':op['risk'],'animation':'Сильная индивидуальная Motion-траектория для фото; собственное движение для видео','color_bucket':bucket,'background':'Общая бронза/охра; светлее к Нури'}
        audit.append(info)
        if length==0:continue
        iw,ih=dimensions(r['path']); isstill=Path(r['path']).suffix.lower() not in ['.mp4','.mov']
        if isstill:
            # Shared trajectory for coincident overlay layers; each whole image is kept within the canvas.
            index=next(j for j,q in enumerate(beats) if q==b)
            zoom=6 if length<60 else 8 if length<80 else 10 if bucket in ['young','nuri'] else 12
            pan=.04 if length<60 else .055 if bucket=='nuri' else .06
            sign=1 if index in [0,3,6,10,14,19,24,27,31,35,39,41] else -1
            pos0=[.5-sign*pan/2,.5];pos1=[.5+sign*pan/2,.5]
            maxscale=min(W*(1-pan-.02)/iw,H*.96/ih)*100
            low=maxscale/(1+zoom/100)
            scale0,scale1=(low,maxscale) if index%3!=1 else (maxscale,low)
            start=int(r['source_in']);end=start+length-1
            bounds=[]
            for f in [0,.5,1]:
                q=f*f*(3-2*f);s=scale0+(scale1-scale0)*q;x=pos0[0]+(pos1[0]-pos0[0])*q;y=.5
                box=[x*W-iw*s/200,y*H-ih*s/200,x*W+iw*s/200,y*H+ih*s/200]
                bounds.append({'fraction':f,'bbox_pixels':[round(v,4) for v in box],'entire_source_inside':min(box[:2])>=0 and box[2]<=W and box[3]<=H})
            mp=_motion_params(source_items[ident].track_item_node,ids)
            animation.append({'operation_id':'MOTION_'+ident,'source_clip_id':ident,'track':r['track'],'output_in':b['output_in'],'output_out':b['output_out'],'source_dimensions':[iw,ih],
                'before':{'scale':ET.tostring(mp.scale,encoding='unicode') if mp else 'Встроенный default','position':ET.tostring(mp.position,encoding='unicode') if mp else 'Встроенный default'},
                'scale_start':round(scale0,8),'scale_end':round(scale1,8),'position_start':pos0,'position_end':pos1,'rotation_start':0,'rotation_end':0,
                'keyframe_frames':[start,end],'keyframe_ticks':[str(start*FT),str(end*FT)],'easing':'BEZIER_EASE_IN_OUT','zoom_percent':zoom,'pan_percent':pan*100,
                'safe_crop_calculation':{'formula':'bbox = Position*frame ± original_dimensions*Scale/200; все точки внутри кадра','samples':bounds,'black_edges':'Непокрытая область заполняется отдельным фоном, который гарантированно перекрывает весь кадр'},
                'reset_crop':True,'preserve_opacity':True,'meaning':('Плавно приблизиться к Нури, не обрезая руки и ребёнка' if bucket=='nuri' else 'Раскрыть образ по направлению движения; заменить прежние ключи, а не суммировать')})
        vals,metrics=color_values(bucket,len(colors),r['path'])
        existing=[]
        for _,c in comps(source_items[ident],ids):
            if c.findtext('MatchName')=='AE.ADBE Lumetri':
                ps=params(c,ids);existing.append({k:(ps[v].findtext('StartKeyframe') or '').split(',')[1] for k,v in PID.items()})
        colors.append({'operation_id':'COLOR_'+ident,'source_clip_id':ident,'track':r['track'],'output_in':b['output_in'],'output_out':b['output_out'],'bucket':bucket,'before':existing,'after':vals,'source_metrics':metrics,
            'consolidate_neutral_duplicates':len(existing)>1,'duplicate_policy':'Свести дубли Lumetri к одному только после проверки нейтральных Basic/Creative и равенства кривых и колёс нейтральному шаблону. Иначе BLOCKED.',
            'scope':'Только верхний самостоятельный клип; без добавочного Lumetri на родительской sequence',
            'internal_correction':'Основной источник — файл; внутри готового видео возможен запечённый цвет, выбран меньший нагрев. Фоновая nested обрабатывается независимо',
            'skin_protection':'Низкий Temperature на портретах; Shadows восстанавливают лицо; Highlights ограничивают свет; без общего оранжевого фильтра',
            'scopes_check':'Проверить фактический native render: RGB extrema, waveform, parade, vectorscope; параметры сами по себе не считаются QA',
            'exception_reason':'Матисс и светлые картины уже насыщенны: Saturation 108 вместо 120 во избежание кислотности' if bucket=='painting' else None,
            'artistic_effect':function+'; явно различимый Lumetri ON/OFF'})
        bgops.append({'source_clip_id':ident,'use':r['track']==1,'target_range':[b['output_in'],b['output_out']],
            'source_dimensions':[iw,ih],'scale_to_fill':round(max(W/iw,H/ih)*110,8),'position':[.5,.5],
            'method':'Дубликат исходника в отдельной cloned nested; заполнение кадра, штатный Gaussian Blur, штатный Tint',
            'blur':200,'opacity':100,'blending':'Normal','palette':{'black':[42,28,18],'white':[192,145,77]},
            'no_black_edges_proof':{'rendered_dimensions':[iw*max(W/iw,H/ih)*1.1,ih*max(W/iw,H/ih)*1.1],'frame':[W,H],'overscan_percent':10},
            'foreground_fullscreen_video':not isstill and abs(iw/ih-W/H)<.02})
    nuri_start=next(o['output_in'] for o in ops if o['clip_identity']['object_id']=='967')
    comp_start=next(o['output_in'] for o in ops if o['clip_identity']['object_id']=='964')
    master={'schema_version':'1.0','task_id':'TASK_032','source_project':str(SOURCE),'source_sequence':NAME,'source_sha256':SHA,'output_project':str(DEST),'output_sequence':TARGET,
        'checkpoint_project':str(CHECKPOINT),'backup_project':str(BACKUP),'preview':str(PREVIEW),'local_reports':str(REPORT_DIR),
        'editorial_intent':'Молодой автор и семья → мастерская → рисунок и бронза → художественные рифмы → современный Сергей → компьютер и семейное видео → Нури → свет. Удалить 11.24 с чёрного разрыва и три повтора, уплотнить вступление.',
        'invariants':['Исходный файл и все исходные sequences неизменны','Отдельные проект и sequence','Все эффекты остаются штатными и редактируемыми','Готовые видео не получают новую Motion-анимацию','Не использовать baked preview как исходник монтажа','Музыку не заменять и не разрезать вслед за каждым видеомонтажным удалением'],
        'edit_operations':ops,'gap_operations':[{'type':'REMOVE_GAP','timeline_in':3173,'timeline_out':3454,'reason':'11.24 с чёрного изображения подтверждены native export и blackdetect','postcondition':'Компьютерный блок непосредственно перед Нури'}],
        'strong_animation_operations':animation,'unified_background_operations':bgops,
        'background_sequence':{'source':'Nested Sequence 03','output':BG,'outer_scale':100,'outer_blur_match_name':'AE.ADBE Gaussian Blur 2','outer_blur':200,'repeat_edge_pixels':True,
            'tint':{'effect':'Tint','map_black':[42,28,18],'map_white':[192,145,77],'amount_keyframes':[[0,88],[comp_start,80],[nuri_start,60],[cursor-1,35]]},
            'lumetri':{'temperature':0,'tint':0,'saturation':65,'exposure':-.55,'contrast':15,'highlights':-30,'shadows':15,'whites':-5,'blacks':3,'sharpen':0,'vibrance':0,'vignette':-.5},
            'exposure_keyframes':[[0,-.55],[comp_start,-.45],[nuri_start,.2],[cursor-1,.75]]},
        'extreme_color_operations':colors,
        'audio_operations':[{'operation_id':'AUDIO_MASTER','measured_source':{'integrated_lufs':-18.05,'true_peak_dbtp':-1.38,'lra_lu':20.1},'target_lufs_range':[-18,-16],'gain_db':.15,'peak_ceiling_dbtp':-1,
            'limiter':'Не добавлять, если после линейного gain и финального fade peak проходит. При превышении остановить QA и пересчитать JSON без скрытого clipping.',
            'keep_intro_crossfade':True,'main_audio_source_id':'976','intro_audio_source_id':'979','main_timeline_in':137,'main_timeline_out':cursor,'main_source_in':2765,'main_source_out':2765+cursor-137,'final_fade_frames':80,
            'verification':'Измерить фактический нативный export после сохранения; LUFS, dBTP, LRA, анализ огибающих и полного декодирования','reason':'Исходная громкость близка к цели. Сохранить динамику фортепиано, завершить музыку плавным fade 3.2 с.'}],
        'validation_rules':['Все clips однозначны и online','Без дыры после ripple','Совпадают точные длительности','Все кадры фото внутри canvas на старте/середине/конце','Штатные эффекты найдены через Premiere','ObjectRef/ObjectURef разрешены','Исходная sequence и её граф не изменены','Ни один output не существует до первого применения'],
        'expected_duration':{'frames':cursor,'fps':25,'seconds':cursor/25},'rollback':'При ошибке до записи выбросить дерево в памяти; источник не изменяется. После native ошибки закрыть без сохранения и восстановить собственный checkpoint, не заменять входной файл.',
        'unresolved_items':[],'pending_final_acceptance':['Native export и повторное открытие выходного проекта','Проверка scopes по конечному render','Художественная приёмка Музы']}
    dump('TASK_032_MASTER.json',master);dump('TASK_032_AUDIT.json',audit)
    dump('TASK_032_STRONG_ANIMATION_PLAN.json',animation);dump('TASK_032_EXTREME_COLOR_PLAN.json',colors)
    dump('TASK_032_BACKGROUND_DESIGN.json',{'operations':bgops,'sequence':master['background_sequence']})
    dump('TASK_032_AUDIO_AUDIT.json',master['audio_operations'][0]['measured_source'])
    dump('TASK_032_REMOVE_TRIM_CANDIDATES.json',[o for o in ops if o['type']!='KEEP']+master['gap_operations'])
    dump('TASK_032_ADD_CANDIDATES.json',{'decision':'Не добавлять внешние источники','reason':'Компьютерный мост присутствует в 20230815_111101.mp4 и 20230815_111633.mp4. Удаление подтверждённой чёрной дыры даёт прямой переход к Нури. Случайные кадры не нужны.'})
    txt('TASK_032_AUDIT_REPORT.txt','TASK_032 — НОВЫЙ АУДИТ\nФактическая sequence: 4006 кадров / 160.24 с / 3840×2160 / 25 fps.\nЭкспорт Premiere: 1280×720, 25 fps, 4006 кадров; файл с суффиксом 640_360 является исходным диагностическим экспортом 720p.\nНайден чёрный разрыв [3173,3454): 126.92–138.16 с.\nСтарый отчёт неверно называл компьютерные кадры музеем; новый вывод опирается на просмотр исходников и native render.\nИсходный LUFS -18.05; True Peak -1.38; LRA 20.1.\nПочти все самостоятельные фото статичны; отдельные старые Motion имеют сильное небезопасное увеличение до 434%. Новые траектории заменяют старые.\nФон существует, но имеет случайные оттенки. Пересобирается отдельной копией nested в единую бронзово-охристую систему.\nНовое вступление 15.6 с; удалены варианты 938,947,956 и чёрный разрыв. Плановая длительность '+str(cursor/25)+' с.\nПолный аудиосигнал технически проанализирован. Субъективное прослушивание агентом в текстовой среде не подтверждено; художественная звуковая приёмка остаётся открытой.')
    print('План записан:',cursor,'кадров;',len(animation),'Motion;',len(colors),'Lumetri')

def source_closure(root):
    ids=build_project_object_id_lookup(root);uids=build_project_object_uid_lookup(root);seen={};stack=[find_project_sequence_node(root,NAME)]
    while stack:
        n=stack.pop();key=n.get('ObjectID') or n.get('ObjectUID')
        if key in seen:continue
        seen[key]=ET.tostring(n)
        for c in n.iter():
            for attr,lookup in [('ObjectRef',ids),('ObjectURef',uids)]:
                if c.get(attr) in lookup:stack.append(lookup[c.get(attr)])
    return seen
def clone_filter(root,item,template,ids,alloc):
    c=_clone_filter_component(root,template,object_id_lookup=ids,template_object_id_lookup=ids,id_allocator=alloc)
    ref=item.track_item_node.find('ClipTrackItem/ComponentOwner/Components');chain=ids[ref.get('ObjectRef')];container=chain.find('ComponentChain/Components')
    if container is None:container=ET.SubElement(chain.find('ComponentChain'),'Components',{'Version':'1'})
    ET.SubElement(container,'Component',{'Index':str(len(container)),'ObjectRef':c.get('ObjectID')})
    return c
def find_motion(root,item,ids,alloc):
    found=_motion_params(item.track_item_node,ids)
    if found:return found
    template=next(n for n in root.iter('VideoFilterComponent') if n.findtext('MatchName')=='AE.ADBE Motion')
    clone_filter(root,item,template,ids,alloc)
    return _motion_params(item.track_item_node,ids)
def trim_item(item,ids,start,end,si,so):
    node=item.track_item_node.find('ClipTrackItem/TrackItem');settext(node,'Start',start*FT);settext(node,'End',end*FT)
    sub=ids[item.track_item_node.find('ClipTrackItem/SubClip').get('ObjectRef')];cl=ids[sub.find('Clip').get('ObjectRef')].find('Clip')
    settext(cl,'InPoint',si*FT);settext(cl,'OutPoint',so*FT)
def drop(item):
    parent=next(n for n in item.track_node.iter() if item.track_item_ref in list(n));parent.remove(item.track_item_ref);_reindex_track_items(parent)
def apply_grade(root,item,values,ids,alloc,template,consolidate_neutral=False):
    cs=[c for _,c in comps(item,ids) if c.findtext('MatchName')=='AE.ADBE Lumetri']
    if len(cs)>1:
        assert consolidate_neutral,'Двойной Lumetri без основания'
        neutral=params(template,ids)
        binary_by_hash={n.get('BinaryHash'):(n.text or '').strip() for n in root.iter() if n.get('BinaryHash') and (n.text or '').strip()}
        def binary_value(p):
            node=p.find('StartKeyframeValue')
            return (node.text or '').strip() or binary_by_hash.get(node.get('BinaryHash'),'')
        for extra in cs[1:]:
            xp=params(extra,ids)
            for k,pid in PID.items():assert float(xp[pid].findtext('StartKeyframe').split(',')[1])==(100 if k=='saturation' else 0),'Дубль содержит ненулевую коррекцию'
            for pid,p in xp.items():
                if p.find('StartKeyframeValue') is not None:
                    assert binary_value(p)==binary_value(neutral[pid]),'Дубль содержит ненейтральные кривые/колёса: '+str(pid)
            chain=ids[item.track_item_node.find('ClipTrackItem/ComponentOwner/Components').get('ObjectRef')].find('ComponentChain/Components')
            for ref in list(chain):
                if ref.get('ObjectRef')==extra.get('ObjectID'):chain.remove(ref)
            for index,ref in enumerate(chain):ref.set('Index',str(index))
    c=cs[0] if cs else clone_filter(root,item,template,ids,alloc)
    ps=params(c,ids)
    for k,v in values.items():static(ps[PID[k]],v)
    return {k:float(ps[PID[k]].findtext('StartKeyframe').split(',')[1]) for k in values}
def reset_crop(item,ids):
    for _,c in comps(item,ids):
        name=c.findtext('MatchName')
        ps=params(c,ids)
        for p in ps.values():
            nm=(p.findtext('Name') or '').lower()
            if name=='AE.ADBE AECrop' and nm in ['left','top','right','bottom','edge feather']:static(p,0)
            if name=='AE.ADBE Motion' and nm in ['crop left','crop top','crop right','crop bottom','rotation']:static(p,0)
def edit_tree(root,m):
    original=source_closure(root);ids=build_project_object_id_lookup(root);uids=build_project_object_uid_lookup(root)
    clone_named_sequence(root,source_sequence_name=NAME,new_sequence_name=TARGET,object_id_lookup=ids,object_uid_lookup=uids)
    clone_named_sequence(root,source_sequence_name='Nested Sequence 03',new_sequence_name=BG,object_id_lookup=build_project_object_id_lookup(root),object_uid_lookup=build_project_object_uid_lookup(root))
    ids=build_project_object_id_lookup(root);alloc=_ProjectObjectIdAllocator(root);byid={};logs=[]
    dst_items=items(root,TARGET);bgitems=items(root,BG)
    for op in m['edit_operations']:
        ident=op['clip_identity'];found=[i for i in dst_items if i.track_index==ident['track'] and i.start==ident['timeline_in']*FT and i.name==ident['name']]
        assert len(found)==1,(op['operation_id'],len(found));item=found[0];byid[ident['object_id']]=item
        foundbg=[i for i in bgitems if i.track_index==ident['track']-1 and i.start==ident['timeline_in']*FT and i.name==ident['name']]
        assert len(foundbg)==1
        for it in [item,*foundbg]:
            if op['type']=='REMOVE':drop(it)
            else:trim_item(it,ids,op['output_in'],op['output_out'],op['new_source_in'],op['new_source_out'])
        logs.append({'operation_id':op['operation_id'],'before':ident,'after':None if op['type']=='REMOVE' else {'in':op['output_in'],'out':op['output_out'],'source_in':op['new_source_in'],'source_out':op['new_source_out']},'postcondition':'PASS'})
    duration=m['expected_duration']['frames'];bgouter=next(i for i in dst_items if i.track_index==0)
    trim_item(bgouter,ids,0,duration,0,duration)
    sub=ids[bgouter.track_item_node.find('ClipTrackItem/SubClip').get('ObjectRef')];clip=ids[sub.find('Clip').get('ObjectRef')];src_ref=clip.find('Clip/Source');src=copy.deepcopy(ids[src_ref.get('ObjectRef')]);src.set('ObjectID',alloc.allocate());src.find('SequenceSource/Sequence').set('ObjectURef',find_project_sequence_node(root,BG).get('ObjectUID'));settext(src,'OriginalDuration',duration*FT);root.append(src);ids[src.get('ObjectID')]=src;src_ref.set('ObjectRef',src.get('ObjectID'))
    sub.find('MasterClip').set('ObjectURef',_find_sequence_masterclip(root,BG).get('ObjectUID'));settext(sub,'Name',BG)
    # Preserve opening musical edit; shorten only the long tail, no video-ripple cuts in the piano.
    aud=items(root,TARGET,1);ao=m['audio_operations'][0]
    main=next(i for i in aud if i.track_index==0)
    trim_item(main,ids,ao['main_timeline_in'],ao['main_timeline_out'],ao['main_source_in'],ao['main_source_out'])
    for name in [TARGET,BG]:_update_sequence_duration_metadata(root,find_project_sequence_node(root,name),new_total_duration=duration*FT)
    assert original==source_closure(root),'Исходная sequence или её граф изменились'
    _validate_all_refs(root)
    return logs
def effects_tree(root,m):
    before=source_closure(root);ids=build_project_object_id_lookup(root);alloc=_ProjectObjectIdAllocator(root);template=neutral_lumetri(root,ids)
    current=items(root,TARGET);lookup={}
    for o in m['edit_operations']:
        if o['type']=='REMOVE':continue
        ident=o['clip_identity'];lookup[ident['object_id']]=next(i for i in current if i.track_index==ident['track'] and i.start==o['output_in']*FT and i.name==ident['name'])
    motion_log=[];color_log=[];bglog=[]
    for o in m['strong_animation_operations']:
        item=lookup[o['source_clip_id']];mp=find_motion(root,item,ids,alloc);reset_crop(item,ids)
        s,e=map(int,o['keyframe_ticks']);a,b=o['position_start'],o['position_end']
        static(mp.scale,o['scale_start']);static(mp.position,':'.join(map(str,a)))
        _set_param_keyframes(mp.scale,keyframes=build_scale_keyframes(s,e,o['scale_start'],o['scale_end']))
        _set_param_keyframes(mp.position,keyframes=build_position_keyframes(s,e,a[0],a[1],b[0],b[1]))
        motion_log.append({'operation_id':o['operation_id'],'scale_keyframes':mp.scale.findtext('Keyframes'),'position_keyframes':mp.position.findtext('Keyframes'),'safe_crop':o['safe_crop_calculation'],'postcondition':'PASS'})
    for o in m['extreme_color_operations']:
        actual=apply_grade(root,lookup[o['source_clip_id']],o['after'],ids,alloc,template,o['consolidate_neutral_duplicates']);assert actual==o['after'];color_log.append({'operation_id':o['operation_id'],'before':o['before'],'after':actual,'neutral_duplicates_removed':max(0,len(o['before'])-1),'postcondition':'PASS'})
    # Background internal clips are filled independently, without inherited source zooms or double exposures.
    for i in items(root,BG):
        if i.track_index>0:drop(i);continue
        op=next(o for o in m['unified_background_operations'] if o['use'] and o['target_range']==[i.start//FT,i.end//FT])
        # At the back of an opaque 16:9 video a generated backdrop is unnecessary.
        if op['foreground_fullscreen_video']:drop(i);bglog.append({'range':op['target_range'],'result':'Фон отсутствует под полноэкранным видео'});continue
        mp=find_motion(root,i,ids,alloc)
        for ref,c in list(comps(i,ids)):
            if c.findtext('MatchName')!='AE.ADBE Motion':
                chain=ids[i.track_item_node.find('ClipTrackItem/ComponentOwner/Components').get('ObjectRef')];chain.find('ComponentChain/Components').remove(ref)
        reset_crop(i,ids);static(mp.scale,op['scale_to_fill']);static(mp.position,'0.5:0.5')
        bglog.append({'range':op['target_range'],'scale':op['scale_to_fill'],'postcondition':'Fill > 110% canvas; opaque native background'})
    outer=next(i for i in current if i.track_index==0);mp=find_motion(root,outer,ids,alloc);static(mp.scale,100);static(mp.position,'0.5:0.5');reset_crop(outer,ids)
    chain=ids[outer.track_item_node.find('ClipTrackItem/ComponentOwner/Components').get('ObjectRef')].find('ComponentChain/Components')
    for ref,c in list(comps(outer,ids)):
        if c.findtext('MatchName')!='AE.ADBE Motion':chain.remove(ref)
    blurtemplate=next(c for c in root.iter('VideoFilterComponent') if c.findtext('MatchName')=='AE.ADBE Gaussian Blur 2');blur=clone_filter(root,outer,blurtemplate,ids,alloc);ps=params(blur,ids);static(ps['1'],200);static(ps['2'],0);static(ps['3'],'true')
    apply_grade(root,outer,m['background_sequence']['lumetri'],ids,alloc,template)
    lum=next(c for _,c in comps(outer,ids) if c.findtext('MatchName')=='AE.ADBE Lumetri');ex=params(lum,ids)['11'];keys=m['background_sequence']['exposure_keyframes'];_set_param_keyframes(ex,keyframes=''.join(build_scale_keyframes(a[0]*FT,b[0]*FT,a[1],b[1]).split(';')[0]+';' for a,b in zip(keys,keys[1:]))+build_scale_keyframes(keys[-1][0]*FT,keys[-1][0]*FT,keys[-1][1],keys[-1][1]).split(';')[0]+';')
    audio_log=[]
    for i in items(root,TARGET,1):
        ac=next(c for _,c in comps(i,ids) if c.findtext('FilterMatchName')=='Internal Volume Stereo')
        pr=next(ids[p.get('ObjectRef')] for p in ac.findall('AudioComponent/Component/Params/Param') if ids[p.get('ObjectRef')].findtext('Name')=='Level')
        old=pr.findtext('Keyframes');gain=10**(m['audio_operations'][0]['gain_db']/20)
        entries=[e.split(',') for e in old.split(';') if e]
        for entry in entries:entry[1]=str(float(entry[1])*gain)
        if i.track_index==0:
            so=m['audio_operations'][0]['main_source_out']*FT;fade=m['audio_operations'][0]['final_fade_frames']*FT
            entries=[e for e in entries if int(e[0])<so-fade]
            val=float(entries[-1][1]);entries.extend([[str(so-fade),str(val),'0','0','0','0','0','0'],[str(so-FT),'0.0000001','0','0','0','0','0','0']])
        settext(pr,'Keyframes',';'.join(','.join(e) for e in entries)+';')
        audio_log.append({'track':i.track_index,'before':old,'after':pr.findtext('Keyframes'),'gain_db':m['audio_operations'][0]['gain_db'],'postcondition':'PASS: музыкальная огибающая сохранена, финальный fade добавлен'})
    assert before==source_closure(root),'Граф исходника изменён эффектами'
    _validate_all_refs(root)
    return motion_log,color_log,bglog,audio_log

def dryrun(m):
    errors=[];checks=[]
    required=['schema_version','source_project','source_sequence','output_project','output_sequence','editorial_intent','invariants','edit_operations','strong_animation_operations','unified_background_operations','extreme_color_operations','audio_operations','validation_rules','expected_duration','rollback','unresolved_items']
    try:
        assert all(k in m for k in required),'schema missing fields'
        assert sha(SOURCE)==m['source_sha256']==SHA,'source checksum mismatch'
        assert not m['unresolved_items'],'Неразрешённые вопросы'
        for p in [DEST,CHECKPOINT,BACKUP,PREVIEW]:assert not p.exists(),'Output уже существует: '+str(p)
        root=load_premiere_project_root(SOURCE);original=source_closure(root)
        seqitems=items(root,NAME);srcids={i.track_item_node.get('ObjectID'):i for i in seqitems}
        assert len(srcids)==len(seqitems),'Duplicate identities'
        for o in m['edit_operations']:
            q=o['clip_identity'];i=srcids[q['object_id']]
            assert (i.track_index,i.name,i.start//FT,i.end//FT,i.source_in//FT,i.source_out//FT)==(q['track'],q['name'],q['timeline_in'],q['timeline_out'],q['source_in'],q['source_out'])
            assert Path(q['path']).is_file(),'offline'
            assert 0<=o['output_in']<=o['output_out']<=m['expected_duration']['frames']
            assert o['new_source_out']<=q['source_out'],'Insufficient handles'
            assert o['new_source_out']-o['new_source_in']==o['output_out']-o['output_in']
        checks.append('Schema, exact identity, media, source handles, IN/OUT: PASS')
        for o in m['strong_animation_operations']:
            assert all(s['entire_source_inside'] for s in o['safe_crop_calculation']['samples'])
            assert 4<=o['zoom_percent']<=16 and 4<=o['pan_percent']<=10
        checks.append('Индивидуальные Motion-траектории и safe-crop: PASS')
        available=(OUT/'TASK_032_EFFECT_AVAILABILITY.txt').read_text(encoding='utf-8-sig');assert all(k+': AVAILABLE' in available for k in ['Tint','Gaussian Blur','Lumetri Color'])
        checks.append('Эффекты подтверждены непосредственно Premiere: PASS')
        log=edit_tree(root,m)
        v=sorted([i for i in items(root,TARGET) if i.track_index==1],key=lambda i:i.start)
        assert v[0].start==0 and v[-1].end==m['expected_duration']['frames']*FT
        assert all(a.end==b.start for a,b in zip(v,v[1:])),'Дыра в основном видео'
        checks.append('Монтаж применён в памяти; сплошное покрытие, ripple, аудио и длительность: PASS')
        effects_tree(root,m)
        assert original==source_closure(root)
        checks.append('Все XML-операции проиграны в памяти; ссылки, независимость source graph, эффекты: PASS')
    except Exception as e:
        errors.append(str(e))
    validation={'status':'PASS' if not errors else 'BLOCKED','master_sha256':sha(OUT/'TASK_032_MASTER.json'),'checks':checks,'errors':errors,'project_files_written':False,'native_tint_application':'Запланирована после PASS с проверкой свойств и сохранением только при успехе'}
    dump('TASK_032_MASTER_VALIDATION.json',validation);txt('TASK_032_DRY_RUN_REPORT.txt',json.dumps(validation,ensure_ascii=False,indent=2));print(json.dumps(validation,ensure_ascii=False,indent=2))
    return not errors
def save_new(root,path):
    payload = gzip.compress(ET.tostring(root,encoding='utf-8',xml_declaration=True))
    with Path(path).open('xb') as f:f.write(payload)
def apply(m):
    validation=json.loads((OUT/'TASK_032_MASTER_VALIDATION.json').read_text(encoding='utf8'))
    assert validation['status']=='PASS' and validation['master_sha256']==sha(OUT/'TASK_032_MASTER.json'),'Нет PASS для точной версии JSON'
    assert dryrun(m),'Повторный dry-run не прошёл'
    # Re-read the contract after validation. No artistic values are chosen during execution.
    m=json.loads((OUT/'TASK_032_MASTER.json').read_text(encoding='utf8'))
    assert sha(SOURCE)==SHA
    with BACKUP.open('xb') as f:f.write(SOURCE.read_bytes())
    root=load_premiere_project_root(SOURCE);log=edit_tree(root,m);save_new(root,CHECKPOINT)
    dump('TASK_032_EDIT_APPLY_LOG.json',log);txt('TASK_032_EDIT_APPLY_REPORT.txt',f'Исполнен JSON {sha(OUT/"TASK_032_MASTER.json")}.\n{len(log)} операций. Все postconditions PASS.\nМонтажный checkpoint: {CHECKPOINT}')
    post={'status':'PASS','duration_frames':m['expected_duration']['frames'],'clips':len([i for i in items(root,TARGET) if i.track_index==1]),'gap_removed':True,'source_preserved':True,'purpose':'Повторный структурный аудит монтажа до новых анимации и цвета','visual_acceptance':'Ожидает нативного просмотра'}
    dump('TASK_032_POST_EDIT_AUDIT.json',post);txt('TASK_032_POST_EDIT_AUDIT_REPORT.txt',json.dumps(post,ensure_ascii=False,indent=2))
    motion,color,bg,audio=effects_tree(root,m);save_new(root,DEST)
    dump('TASK_032_STRONG_ANIMATION_APPLY_LOG.json',motion);dump('TASK_032_EXTREME_COLOR_APPLY_LOG.json',color);dump('TASK_032_BACKGROUND_APPLY_LOG.json',bg);dump('TASK_032_AUDIO_APPLY_LOG.json',audio)
    verified=load_premiere_project_root(DEST);_validate_all_refs(verified)
    assert source_closure(verified)==source_closure(load_premiere_project_root(SOURCE));assert sha(SOURCE)==SHA
    dump('TASK_032_STRUCTURAL_QA.json',{'status':'PASS_XML_NATIVE_PENDING','source_sha256':SHA,'master_sha256':sha(OUT/'TASK_032_MASTER.json'),'duration_frames':m['expected_duration']['frames'],'source_preserved':True,'references_resolved':True,'native_tint_pending':True})
    txt('TASK_032_STRONG_ANIMATION_QA.txt',f'{len(motion)} индивидуальных анимаций. Zoom 6–12%, pan 4–6%; Bezier. Весь исходный кадр внутри canvas на старте/середине/конце. Видеоклипы не получают новые Motion. Визуальная проверка native render ожидается.')
    print('PROJECT_WRITTEN_NATIVE_TINT_PENDING',DEST)

if __name__ == '__main__':
    from main_premiere_art_task import main as launch
    launch(['--task', '032'] + sys.argv[1:])
