"""Fixed TASK_032 helper. Requires an explicit local config and --execute."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(args):
    """Explicit, versioned QA revision; never changes the source or unidentified outputs."""
    from pathlib import Path
    import json, shutil, sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from tools.task032_pipeline import OUT,SOURCE,DEST,PREVIEW,SHA,sha

    def dump(name,data):
        (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')

    assert sha(SOURCE)==SHA
    qa=json.loads((OUT/'TASK_032_FINAL_QA_MEASUREMENTS.json').read_text(encoding='utf8'))
    assert sha(DEST)==qa['output_sha256'] and sha(PREVIEW)==qa['preview_sha256']
    archive=OUT/'revision_01';archive.mkdir(exist_ok=False)
    for p in list(OUT.glob('TASK_032_*.json'))+list(OUT.glob('TASK_032_*QA.txt'))+list(OUT.glob('TASK_032_*REPORT.txt')):
        shutil.copy2(p,archive/p.name)
    shutil.copy2(DEST,archive/DEST.name)
    shutil.copy2(PREVIEW,archive/PREVIEW.name)
    assert sha(archive/DEST.name)==qa['output_sha256'] and sha(archive/PREVIEW.name)==qa['preview_sha256']
    scratch=OUT/'TASK_032_EFFECT_CALIBRATION.prproj'
    assert not scratch.exists();shutil.copy2(DEST,scratch)
    master=json.loads((OUT/'TASK_032_MASTER.json').read_text(encoding='utf8'))
    master['revision']=2
    master['revision_history']=[{'revision':1,'master_sha256':qa['master_sha256'],'archive':str(archive),'qa':qa['checks']}]
    colors=[]
    for cid,patch in [('960',{'temperature':-10,'saturation':105,'exposure':.15,'contrast':30,'highlights':-45,'shadows':30,'whites':3,'blacks':0,'vibrance':0}),('961',{'temperature':-8,'saturation':106,'contrast':30,'exposure':.2,'highlights':-45,'shadows':30,'whites':3,'blacks':0,'vibrance':0})]:
        c=next(c for c in master['extreme_color_operations'] if c['source_clip_id']==cid)
        before=dict(c['after']);c['after'].update(patch)
        c['exception_reason']='После просмотра native export: уже тёплый источник требует меньшей насыщенности и отрицательной температуры для защиты кожи.'
        colors.append({'track':c['track'],'output_in':c['output_in'],'source_clip_id':cid,'before':before,'after':c['after']})
    master['background_sequence']['outer_blur']=600
    master['background_sequence']['tint']['map_black']=[54,38,24]
    master['background_sequence']['tint']['map_white']=[153,112,57]
    master['background_sequence']['solid_composite']={'effect':'Solid Composite','color':[75,52,28],'source_opacity_percent':100,'opacity_percent':100,'blending':'Normal','purpose':'Непрозрачная бронзовая подложка для альфа-канала скульптуры; без чёрных полей.'}
    for b in master['unified_background_operations']:
        if b.get('use'): b['blur']=600
    master['audio_operations'][0]['limiter']={'effect':'Hard Limiter','input_boost_db':2.7,'maximum_amplitude_db':-1.5,'lookahead_ms':7,'release_ms':100,'link_channels':True,'placement':'Каждый из двух музыкальных клипов; сохранён исходный кроссфейд и fade 80 кадров.'}
    master['qa_revision_operations']={'expected_current_project_sha256':qa['output_sha256'],'expected_current_preview_sha256':qa['preview_sha256'],'archive':str(archive),'calibration_project':str(scratch),'background':master['background_sequence'],'colors':colors,'audio':master['audio_operations'][0]['limiter'],'reason':'Фактический R01: -19.94 LUFS; узнаваемые силуэты фона; прозрачные поля скульптуры; перегрев ренессансного портрета.','rollback':'Не сохранять при ошибке параметров. Оригинальный проект неизменен. Проверенная копия R01 сохранена с хешами.','preview_staging':str(OUT/'TASK_032_REVISION_02_NATIVE.mp4')}
    dump('TASK_032_MASTER.json',master)
    dump('TASK_032_CALIBRATION_VALIDATION.json',{'status':'PASS','scope':'Только отдельная диагностическая копия; финальный проект не изменяется до проверки параметров.','master_sha256':sha(OUT/'TASK_032_MASTER.json'),'source_unchanged':True,'scratch_matches_current_output':sha(scratch)==qa['output_sha256'],'effects_available':(OUT/'TASK_032_REVISION_EFFECT_AVAILABILITY.txt').read_text(encoding='utf8')})
    print('R02 JSON записан. Архив R01 проверен. Калибровка отдельной копии разрешена.')


if __name__ == '__main__':
    from utils.premiere_art_runtime import tool_entry
    tool_entry('032', main)
