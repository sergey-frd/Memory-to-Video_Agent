"""Archive an accepted workflow, then optionally remove verified working files."""
from pathlib import Path
from tools import hero_video_closeout as archive


def finish_cleanup(report, pending):
    for entry in pending['finals']:
        if archive.sha(entry['path'])!=entry['sha256']:raise ValueError('Final copy changed')
    result=archive.cleanup(report/'plan.json',apply=True)
    saved=dict(pending,status='CLOSED',protected_sources=result['protected_sources'],deleted=len(result['deleted']))
    archive.save(report/'closed.json',saved)
    plan=archive.load(report/'plan.json')
    archive.save(Path(plan['storage']['metadata'])/'workflow_closed.json',saved)
    print('CLOSED: '+str(report/'closed.json'));return saved


def run(config_path, workflow_path, apply=False):
    cfg=archive.load(config_path);base=Path(config_path).resolve().parent
    wf=archive.load(workflow_path)
    work=archive.safe(Path(workflow_path).resolve().parent/wf['output_root'])
    if (work/'workflow.lock').exists():raise ValueError('Workflow is running; closeout cannot run concurrently')
    source=archive.safe(base/cfg['working_dir'])
    task=cfg['task_id']
    if archive.norm(source)!=archive.norm(archive.ROOT/'output'/task):
        raise ValueError('Closeout scope must be repository/output/<task_id>')
    report=archive.safe(base/cfg['report_dir'])
    if archive.below(report,source):raise ValueError('Report must be outside the cleanup directory')
    receipt=report/'closed.json'
    if receipt.exists():
        saved=archive.load(receipt)
        if archive.sha(config_path)!=saved['config_sha256']:raise ValueError('Closeout configuration changed')
        archive.verify(report/'plan.json')
        for entry in saved['finals']:
            if archive.sha(entry['path'])!=entry['sha256']:raise ValueError('Final artifact changed')
        print('CLOSED: archive and final files verified');return saved
    pending_path=report/'cleanup_pending.json'
    if pending_path.exists():
        pending=archive.load(pending_path)
        if pending['config_sha256']!=archive.sha(config_path):raise ValueError('Closeout configuration changed')
        if not apply:
            archive.cleanup(report/'plan.json',apply=False);return pending
        lock=report/'.workflow_closeout.lock'
        with lock.open('x') as f:f.write('Resuming cleanup')
        try:return finish_cleanup(report,pending)
        finally:lock.unlink()
    state=archive.load(work/'workflow_state.json')
    if state.get('status')!='FINAL_ACCEPTED':raise ValueError('Closeout requires FINAL_ACCEPTED; review and accept final video first')
    from tools.run_video_workflow import verify
    for entry in state['finishing'].values():verify(entry['completed'])
    last=state['finishing'].get('color',state['finishing']['transitions']);job=archive.load(last['job'])
    final_sources=[Path(job['video']),Path(job['project'])]
    if not all(archive.below(p,source) for p in final_sources):raise ValueError('Final files must belong to this task working directory')
    hero_path=archive.safe(base/cfg['hero_config']);hero=archive.load(hero_path)
    finals=[]
    for src,key in zip(final_sources,['video_config_key','project_config_key']):
        destination=archive.safe(Path(hero[cfg[key]])/task/'final'/src.name)
        if archive.below(destination,source) or archive.below(source,destination):raise ValueError('Final destination overlaps cleanup scope')
        finals.append(dict(source=str(src),path=str(destination)))
    report.mkdir(parents=True,exist_ok=True)
    lock=report/'.workflow_closeout.lock'
    with lock.open('x') as f:f.write('Closeout running')
    try:
        # The plan and receipt live outside the directory that will be cleaned.
        job_path=report/'job.json';plan_path=report/'plan.json'
        generated=dict(task_id=task,hero_config=str(hero_path),working_dir=str(source),
            project_scan_roots=cfg['project_scan_roots'],retained_results=[],
            storage={
                'classification':dict(config_key='source_materials_dir',relative=f'classification/{task}/final_archive'),
                'artwork':dict(config_key='regeneration_assets_dir',relative=f'{task}/final_archive/artwork'),
                'history':dict(config_key='regeneration_assets_dir',relative=f'{task}/final_archive/history'),
                'provenance':dict(config_key='regeneration_assets_dir',relative=f'{task}/final_archive/provenance'),
                'metadata':dict(config_key='regeneration_assets_dir',relative=f'{task}/final_archive/metadata')})
        if job_path.exists():
            if archive.load(job_path)!=generated:raise ValueError('Closeout job changed')
        else:archive.save(job_path,generated)
        if not plan_path.exists():archive.make_plan(job_path,plan_path)
        else:archive.validate_plan(plan_path)
        if not apply:
            print('CLOSEOUT_PLANNED: '+str(plan_path)+'; no archive writes or deletion. Use --apply after final acceptance.')
            return dict(status='CLOSEOUT_PLANNED',finals=finals)
        archive.archive(plan_path)
        archive.verify(plan_path)
        plan=archive.load(plan_path)
        manifest=archive.load(report/'archive_manifest.json')
        mapping={archive.norm(row['source']):row for row in manifest['files']}
        for entry in finals:
            archived=mapping[archive.norm(entry['source'])]
            entry['sha256']=archive.put(Path(entry['path']),Path(archived['destination']).read_bytes())
        # Configurations outside output also belong in the durable provenance.
        provenance=Path(plan['storage']['provenance'])/'workflow_configs'
        configs=[Path(workflow_path),Path(config_path)]
        if wf.get('pipeline_config'):configs.append(Path(workflow_path).resolve().parent/wf['pipeline_config'])
        for config in configs:archive.put(provenance/config.name,config.read_bytes())
        code_root=Path(__file__).resolve().parents[1]
        code_files=[code_root/'config.py',*code_root.glob('requirements*.txt'),
                    *code_root.glob('run_*pipeline.bat'),*code_root.glob('run_*workflow.bat')]
        for folder in ['tools','utils','models']:
            code_files.extend((code_root/folder).glob('*.py'))
        code_manifest=[]
        for code in code_files:
            if code.is_file():
                target=Path(plan['storage']['provenance'])/'technology_snapshot'/code.relative_to(code_root)
                code_manifest.append(dict(path=str(target),sha256=archive.put(target,code.read_bytes())))
        archive.save(Path(plan['storage']['provenance'])/'technology_manifest.json',code_manifest)
        for entry in finals:
            if archive.sha(entry['path'])!=entry['sha256']:raise ValueError('Final copy verification failed')
        archive.cleanup(plan_path,apply=False)
        pending=dict(status='CLEANUP_PENDING',config_sha256=archive.sha(config_path),finals=finals,
                     native_relocated_project_check='PENDING; original accepted project archived; no native reopen claimed')
        archive.save(pending_path,pending)
        return finish_cleanup(report,pending)
    finally:lock.unlink()
