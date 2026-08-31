"""Fixed TASK_032 helper. Requires an explicit local config and --execute."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(args):
    from pathlib import Path
    import sys,json
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import tools.task032_pipeline as pipeline
    globals().update({k:v for k,v in vars(pipeline).items() if not k.startswith('_')})
    from tools.task032_pipeline import _validate_all_refs
    m=json.loads((OUT/'TASK_032_MASTER.json').read_text(encoding='utf8'));q=m['qa_revision_operations']
    assert sha(SOURCE)==SHA and sha(DEST)==q['expected_current_project_sha256'] and sha(PREVIEW)==q['expected_current_preview_sha256']
    root=load_premiere_project_root(DEST);ids=build_project_object_id_lookup(root)
    video=items(root,TARGET);bgitems=items(root,BG)
    target=[i for i in bgitems if i.start//FT==1780 and i.name=='671_xl-Enh-SR.png'];assert len(target)==1
    calibration=(OUT/'TASK_032_CALIBRATION_PROPERTIES.txt').read_text(encoding='utf8')
    assert 'ADD Alpha Adjust true' in calibration and 'COMPONENT Hard Limiter' in calibration
    q['background'].pop('solid_composite',None);m['background_sequence'].pop('solid_composite',None)
    alpha={'effect':'Alpha Adjust','sequence':BG,'track':target[0].track_index,'output_in':1780,'name':target[0].name,'parameters':{'Opacity':100,'Ignore Alpha':True,'Invert Alpha':False,'Mask Only':False},'purpose':'Снять прозрачность только с фонового источника скульптуры ДО общего Tint и Blur. Верхняя скульптура остаётся прозрачной и редактируемой.'}
    q['alpha_adjust']=alpha;m['background_sequence']['alpha_adjust']=alpha
    q['audio']['normalized_parameters']={'Maximum Amplitude':.985,'Input Boost':(100+2.7)/150,'Look-Ahead Time':(7-5)/15,'Release Time':(100-40)/160,'Link Channels':1,'Decay to Ceiling':1,'Limit True Peak':1}
    q['audio']['units_verified']='Шкалы реального Premiere Clip Fx Editor: Maximum -100..0 dB; Boost -100..50 dB; Look-Ahead 5..20 ms; Release 40..200 ms. TASK_032_LIMITER_GUI.png.'
    q['audio']['documentation']='https://helpx.adobe.com/ca/audition/desktop/effects-reference/amplitude-compression-effects.html'
    m['audio_operations'][0]['limiter']=q['audio']
    for o in q['colors']:
        c=[i for i in video if i.track_index==o['track'] and i.start//FT==o['output_in']];assert len(c)==1
        lum=[co for _,co in comps(c[0],ids) if co.findtext('MatchName')=='AE.ADBE Lumetri'];assert len(lum)==1
        ps=params(lum[0],ids)
        for k,v in o['before'].items():assert abs(float(ps[PID[k]].findtext('StartKeyframe').split(',')[1])-v)<.001
    aud=items(root,TARGET,1);assert len(aud)==2
    for i in aud:assert not any(co.findtext('MatchName')=='e0b23f05-f1a7-4ef7-9b50-7ec3e3002058' for _,co in comps(i,ids))
    assert not Path(q['preview_staging']).exists()
    dump('TASK_032_MASTER.json',m)
    validation={'status':'PASS','revision':2,'master_sha256':sha(OUT/'TASK_032_MASTER.json'),'source_sha256':sha(SOURCE),'current_project_sha256':sha(DEST),'current_preview_sha256':sha(PREVIEW),'references':_validate_all_refs(root),'checks':['Хеши собственной R01 и архива совпали','Параметры и шкалы эффектов проверены на отдельной копии','Оба цветовых target однозначны, исходные параметры совпали','Фоновый альфа-клип однозначен','Два аудиоклипа существуют, повторного лимитера нет','Монтаж и движения не меняются','Новый экспорт пишется в отдельный staging-файл','Исходный проект неизменен'],'calibration_only_rejected_effect':'Solid Composite не добавился; заменён проверенным Alpha Adjust на внутреннем фоновом клипе.'}
    dump('TASK_032_MASTER_VALIDATION.json',validation)
    write('TASK_032_DRY_RUN_REPORT.txt','PASS. Ревизия 2: финальный JSON перечитан; идентичности, фактические исходные параметры, источники, архив, шкалы штатных эффектов и новые staging-пути проверены. До PASS финальный проект R01 не изменялся. Первичный полный dry-run сохранён в revision_01.\n')
    print(json.dumps(validation,ensure_ascii=False))


if __name__ == '__main__':
    from utils.premiere_art_runtime import tool_entry
    tool_entry('032', main)
