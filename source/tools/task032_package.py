"""Fixed TASK_032 helper. Requires an explicit local config and --execute."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(args):
    from pathlib import Path
    import sys,json,shutil,zipfile
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from tools.task032_pipeline import OUT,REPORT_DIR,SOURCE,DEST,PREVIEW,SHA,sha,FT
    p=json.loads((OUT/'TASK_032_NATIVE_PLAYBACK_POSITION.json').read_text(encoding='utf8'));assert int(p['ticks'])==3480*FT
    assert sha(SOURCE)==SHA
    report=OUT/'TASK_032_PREMIERE_OPEN_CHECK.txt'
    extra='\nНепрерывное воспроизведение R03 завершено: позиция3480 кадров/139.2с. Начало: TASK_032_NATIVE_PLAYBACK_START.json; окончание: TASK_032_NATIVE_PLAYBACK_POSITION.json. После фиксации конца sequence возвращена на кадр0 без изменения сохранённого монтажа.\n'
    if extra not in report.read_text(encoding='utf8'):report.write_text(report.read_text(encoding='utf8')+extra,encoding='utf8')
    (OUT/'TASK_032_WAITING_MUZA_QA.txt').write_text('Ожидается художественная и субъективная звуковая приёмка Музы. Технический результат подготовлен, исходник не изменён.\nОблачная загрузка этим инструментом не выполняется и не подтверждается.\nЛокальный preview: '+str(PREVIEW)+'\nЛокальный проект: '+str(DEST)+'\nTASK_032_DONE.txt не создавался.\n',encoding='utf8')
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    selected=[]
    for p in OUT.iterdir():
        if p.is_file() and p.name.startswith('TASK_032_') and p.suffix.lower() in ['.json','.txt','.jpg','.png','.epr'] and 'CALIBRATION' not in p.name and 'GUI_CURRENT' not in p.name and 'QA_PANEL' not in p.name:
            selected.append(p)
    for p in (OUT/'scopes').glob('TASK_032_*.jpg'):selected.append(p)
    for p in OUT.glob('task032_*.jsx'):selected.append(p)
    for hist in ['revision_01','revision_02']:
        for p in (OUT/hist).iterdir():
            if p.suffix in ['.json','.txt']:selected.append(p)
    for p in selected:
        dest=REPORT_DIR/p.relative_to(OUT);dest.parent.mkdir(exist_ok=True,parents=True)
        if dest.exists():assert sha(dest)==sha(p),'Existing report differs: '+str(dest)
        else:shutil.copy2(p,dest)
    scripts=REPORT_DIR/'scripts';scripts.mkdir(exist_ok=True)
    for p in (Path(__file__).resolve().parent).glob('task032_*.py'):
        dest=scripts/p.name
        if dest.exists():assert sha(dest)==sha(p)
        else:shutil.copy2(p,dest)
    comparison=OUT/'TASK_032_COMPARISON_1280_400_R03.mp4';cd=REPORT_DIR/comparison.name
    if cd.exists():assert sha(cd)==sha(comparison)
    else:shutil.copy2(comparison,cd)
    # Archive history is a local rollback route, omitted from the compact report ZIP.
    for hist in ['revision_01','revision_02']:
        for p in (OUT/hist).iterdir():
            if p.suffix in ['.prproj','.mp4']:
                dest=REPORT_DIR/hist/p.name
                if dest.exists():assert sha(dest)==sha(p)
                else:shutil.copy2(p,dest)
    zip_path=OUT/'TASK_032_REPORTS_JSON_QA.zip'
    assert not zip_path.exists()
    with zipfile.ZipFile(zip_path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in selected:z.write(p,p.relative_to(OUT))
        for p in scripts.iterdir():z.write(p,Path('scripts')/p.name)
    with zipfile.ZipFile(zip_path) as z:assert z.testzip() is None
    shutil.copy2(zip_path,REPORT_DIR/zip_path.name)
    assert all((REPORT_DIR/n).exists() for n in ['TASK_032_MASTER.json','TASK_032_MASTER_VALIDATION.json','TASK_032_FINAL_REPORT.txt','TASK_032_STRUCTURAL_QA.json','TASK_032_VISUAL_AUDIO_QA.txt','TASK_032_PREMIERE_OPEN_CHECK.txt','TASK_032_AUDIO_QA.txt','TASK_032_BACKGROUND_QA.txt','TASK_032_EXTREME_COLOR_QA.txt','TASK_032_STRONG_ANIMATION_QA.txt'])
    print('PACKAGED',len(selected),'files; zip',zip_path.stat().st_size,'bytes. Local report directory verified.')


if __name__ == '__main__':
    from utils.premiere_art_runtime import tool_entry
    tool_entry('032', main)
