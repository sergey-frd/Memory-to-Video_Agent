"""Fresh WIDE-based retry with checksum-verified, uniquely named ART copies."""
import json
from pathlib import Path
from prepare_full_master import ROOT, read, make_xml, HELPERS
from classify import copy_verified
from ingest import digest, write_json

def main():
    work=ROOT/'tasks/BM26/full_master'
    old=read(work/'status.json');original_package=Path(old['plan']).parent
    job=read(original_package/'job.json')
    wide_status=read(ROOT/'tasks/BM26/wide_master/status.json')
    source=Path(wide_status['project_checkpoint'])
    assert digest(source)==old['source_project_sha256']
    package=original_package/'retry_03'
    project=source.with_name('BAM_26_BM_1_FULL_MASTER_01_RETRY_03.prproj')
    if package.exists() or project.exists():raise FileExistsError('Retry already exists')
    for c in job['plan']['clips']:assert Path(c['path']).is_file()
    package.mkdir();(package/'media').mkdir()
    mapping={}
    for c in job['plan']['clips']:
        if not c['art_type']:continue
        original=Path(c['path']);sha=digest(original)
        target=package/'media'/('BM26_FULL_'+sha[:16]+'_'+original.name)
        if not target.exists():copy_verified(original,target)
        assert digest(target)==sha
        mapping[str(original)]={'copy':str(target),'sha256':sha}
        c['original_source_path']=str(original);c['path']=str(target)
    for item in job['plan']['items']:
        for c in item['speed_segments']:
            if c['path'] in mapping:
                c['original_source_path']=c['path'];c['path']=mapping[c['path']]['copy']
    job['plan']['media_aliases']=mapping
    job.update(project=project.as_posix(),xml=(package/'FULL_TIMELINE.xml').as_posix(),preset=(package/'review_720p25.epr').as_posix(),video=(package/'BM26_FULL_MASTER_01_REVIEW.mp4').as_posix())
    copy_verified(source,project);copy_verified(original_package/'review_720p25.epr',package/'review_720p25.epr')
    (package/'FULL_TIMELINE.xml').write_text(make_xml(job['plan'],read(original_package/'wide_master_snapshot.json')),encoding='utf-8')
    template=(ROOT/'scripts/full_master_native.jsx').read_text(encoding='utf-8')
    script=template.replace('__HELPERS__',HELPERS[:HELPERS.index('function cloneNamed')]).replace('__JOB__',json.dumps(job,ensure_ascii=True))
    (package/'assemble_full_master.jsx').write_text(script,encoding='utf-8')
    (work/'retry_native_syntax_check.js').write_text(script,encoding='utf-8')
    write_json(package/'job.json',job);write_json(package/'full_master.json',job['plan']);write_json(package/'media_aliases.json',mapping)
    copy_verified(work/'native_source_diagnostic.txt',package/'previous_native_source_diagnostic.txt')
    old.update(status='FULL MASTER RETRY PREPARED — NATIVE RUN PENDING',plan=str(package/'full_master.json'),project_checkpoint=str(project),jsx=str(package/'assemble_full_master.jsx'),review=job['video'],native_sequence_created=False,native_sequence_verified=False,next='USER RUNS RETRY JSX; SHORT NOT STARTED',retry_reason='XML import matched 3 legacy ART assets by duplicate basename',media_aliases=str(package/'media_aliases.json'))
    write_json(work/'status.json',old);write_json(package/'preparation_status.json',old)
    state=read(ROOT/'tasks/BM26/state.json');state.update(status=old['status'],full_master=old,next_stage=old['next']);state['stages']['FULL_MASTER']='RETRY_PREPARED_NATIVE_NOT_RUN';write_json(ROOT/'tasks/BM26/state.json',state)
    assert digest(source)==old['source_project_sha256']==digest(project)
    print(json.dumps(old,ensure_ascii=True,indent=2))

if __name__=='__main__':main()
