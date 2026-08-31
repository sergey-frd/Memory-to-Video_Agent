"""Fixed TASK_032 helper. Requires an explicit local config and --execute."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(args):
    from pathlib import Path
    import sys,json,shutil
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from tools.task032_pipeline import OUT,SOURCE,DEST,PREVIEW,SHA,sha
    m=json.loads((OUT/'TASK_032_MASTER.json').read_text(encoding='utf8'));q=m['qa_revision_operations']
    if m.get('revision')==3:
        r=m['color_safety_revision'];q={'preview_staging':r['preview_staging'],'expected_current_preview_sha256':r['preview_before_sha256'],'archive':r['archive']}
    qa=json.loads((OUT/'TASK_032_FINAL_QA_MEASUREMENTS.json').read_text(encoding='utf8'))
    assert all(qa['checks'].values()) and sha(SOURCE)==SHA
    assert sha(DEST)==qa['output_sha256'] and sha(Path(q['preview_staging']))==qa['preview_sha256']
    assert sha(PREVIEW)==q['expected_current_preview_sha256'] and sha(Path(q['archive'])/PREVIEW.name)==q['expected_current_preview_sha256']
    shutil.copy2(q['preview_staging'],PREVIEW)
    assert sha(PREVIEW)==qa['preview_sha256']
    (OUT/'TASK_032_LOCAL_DELIVERY_LOG.json').write_text(json.dumps({'status':'PASS','project':str(DEST),'project_sha256':sha(DEST),'preview':str(PREVIEW),'preview_sha256':sha(PREVIEW),'archived_previous_preview':str(Path(q['archive'])/PREVIEW.name),'source_unchanged':sha(SOURCE)==SHA},ensure_ascii=False,indent=2),encoding='utf8')
    print('PASS: native R02 preview copied to required local path; R01 retained.')


if __name__ == '__main__':
    from utils.premiere_art_runtime import tool_entry
    tool_entry('032', main)
