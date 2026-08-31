"""Fixed TASK_032 helper. Requires an explicit local config and --execute."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(args):
    import sys,json,shutil
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import tools.task032_pipeline as pipeline
    globals().update({k:v for k,v in vars(pipeline).items() if not k.startswith('_')})
    from tools.task032_pipeline import _validate_all_refs
    m=json.loads((OUT/'TASK_032_MASTER.json').read_text(encoding='utf8'));qa=json.loads((OUT/'TASK_032_FINAL_QA_MEASUREMENTS.json').read_text(encoding='utf8'));assert sha(SOURCE)==SHA and sha(DEST)==qa['output_sha256'] and sha(PREVIEW)==qa['preview_sha256']
    archive=OUT/'revision_02';archive.mkdir(exist_ok=False)
    for p in list(OUT.glob('TASK_032_*.json'))+list(OUT.glob('TASK_032_*QA.txt')):
        shutil.copy2(p,archive/p.name)
    shutil.copy2(DEST,archive/DEST.name);shutil.copy2(PREVIEW,archive/PREVIEW.name)
    assert sha(archive/DEST.name)==sha(DEST) and sha(archive/PREVIEW.name)==sha(PREVIEW)
    metrics=json.loads((OUT/'TASK_032_CLIPPING_COMPARISON.json').read_text(encoding='utf8'))
    targets=[x['id'] for x in metrics if max(x['after_clipped_channels'])>1 or x['after_dark_percent']>2]
    changes=[];root=load_premiere_project_root(DEST);ids=build_project_object_id_lookup(root);vi=items(root,TARGET)
    for cid in targets:
        o=next(c for c in m['extreme_color_operations'] if c['source_clip_id']==cid);before=dict(o['after']);a=o['after']
        a.update({'whites':-25,'highlights':-55,'blacks':max(10,a['blacks']),'shadows':max(38,a['shadows'])})
        if cid in ['927','930']:a.update({'exposure':min(.12,a['exposure']),'saturation':110,'vibrance':0})
        if cid in ['945','975','946','949']:a.update({'saturation':112,'temperature':4,'vibrance':0})
        if cid in ['962','963','964','965','966']:a.update({'exposure':.15,'contrast':25,'temperature':0,'saturation':106,'vibrance':0,'shadows':42,'blacks':15})
        if cid in ['967','968']:a.update({'exposure':.45,'saturation':108,'temperature':0,'vibrance':0,'shadows':45,'contrast':25})
        o['exception_reason']='Защита светов/теней по фактическим Waveform/RGB Parade: подробности TASK_032_CLIPPING_COMPARISON.json ревизии 2. Сильная коррекция сохранена в контрасте и тенях, ограничена насыщенность светлых областей.'
        candidates=[i for i in vi if i.track_index==o['track'] and i.start//FT==o['output_in']];assert len(candidates)==1
        comps_l=[co for _,co in comps(candidates[0],ids) if co.findtext('MatchName')=='AE.ADBE Lumetri'];assert len(comps_l)==1
        ps=params(comps_l[0],ids)
        for key,val in before.items():assert abs(float(ps[PID[key]].findtext('StartKeyframe').split(',')[1])-val)<.001
        changes.append({'source_clip_id':cid,'track':o['track'],'output_in':o['output_in'],'before':before,'after':a})
    m['revision_history'].append({'revision':2,'master_sha256':sha(OUT/'TASK_032_MASTER.json'),'archive':str(archive),'audio':qa['loudness']})
    m['revision']=3
    m['color_safety_revision']={'operations':changes,'reason':'Убрать добавочный RGB clipping и crushed blacks, обнаруженные в R02 scopes. Не менять монтаж, фон, движение и проверенный звук.','project_before_sha256':sha(DEST),'preview_before_sha256':sha(PREVIEW),'archive':str(archive),'preview_staging':str(OUT/'TASK_032_REVISION_03_NATIVE.mp4'),'rollback':'Не сохранять при ошибке postcondition; R02 целиком в архиве.','postconditions':['Прочитанные значения совпадают с JSON','Новый полный export декодируется','Повторные scopes без добавочного массового clipping','Проверенный аудиосигнал не изменяется']}
    assert not Path(m['color_safety_revision']['preview_staging']).exists()
    dump('TASK_032_MASTER.json',m)
    dump('TASK_032_MASTER_VALIDATION.json',{'status':'PASS','revision':3,'master_sha256':sha(OUT/'TASK_032_MASTER.json'),'targets':targets,'references':_validate_all_refs(root),'checks':['Каждый target однозначен','Все исходные Lumetri параметры совпали','Нативный Lumetri уже существует','Монтаж, Motion, аудио и фон не меняются','Существующая собственная R02 сохранена и сверена по SHA256','Новый staging-файл не существует','Исходник неизменен']})
    write('TASK_032_DRY_RUN_REPORT.txt','PASS. Ревизия 3: защита светов и теней после scopes. Все целевые клипы и значения до коррекции проверены. Полные проверки предыдущих ревизий сохранены в revision_01 и revision_02.\n')
    print('R03 PASS',targets)


if __name__ == '__main__':
    from utils.premiere_art_runtime import tool_entry
    tool_entry('032', main)
