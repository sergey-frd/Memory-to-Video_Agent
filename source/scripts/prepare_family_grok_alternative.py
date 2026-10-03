"""Prepare one alternative camera queue per unique verified completed source photo."""
import argparse
import shutil
from pathlib import Path
from family_contract import ROOT,task_path,read,sha
from ingest import write_json

def prepare(task):
    cfg=read(task/'task.json');user=read(cfg['paths']['config'])
    output=Path(user['final_output_dir']).resolve();previous=output/'GROK_FROM_ART_SOURCES'
    source_manifest=read(previous/'source_manifest.json')
    completed={}
    for plan_path in previous.glob('*/work/.grok-queues/*/plan.json'):
        for item in read(plan_path)['items']:
            state_path=plan_path.parent/(Path(item['image']).stem+'_'+item['sha256'][:10]+'.json')
            if not state_path.exists():continue
            state=read(state_path)
            if state.get('status')!='COMPLETE':continue
            video=Path(state['delivered_video'])
            if not video.is_file() or sha(video)!=state['video_sha256']:raise ValueError('Previous video missing/changed: '+str(video))
            completed.setdefault(item['sha256'],[]).append(dict(video=str(video),video_sha256=state['video_sha256'],prompt=item['prompt']))
    if not completed:raise ValueError('No verified completed source videos')
    package=output/'GROK_ALTERNATIVE_CAMERA';inputs=package/'input';inputs.mkdir(parents=True,exist_ok=True)
    records=[];prompts={}
    for digest,versions in sorted(completed.items()):
        matches=[r for r in source_manifest['items'] if r['sha256']==digest]
        if not matches:raise ValueError('Completed source absent from manifest')
        original=Path(matches[0]['original']);target=inputs/(task.name+'_ALT_'+'_'.join(r['art_id'] for r in matches)+original.suffix.lower())
        if sha(original)!=digest:raise ValueError('Original source changed')
        if target.exists() and sha(target)!=digest:raise ValueError('Alternative input changed')
        if not target.exists():shutil.copy2(original,target)
        if sha(target)!=digest:raise ValueError('Copy mismatch')
        prompts[digest]=list(dict.fromkeys(v['prompt'] for v in versions))
        records.append(dict(image=str(target),original=str(original),sha256=digest,art_ids=[r['art_id'] for r in matches],previous_versions=versions))
    if len(list(inputs.iterdir()))!=len(records):raise ValueError('Unexpected alternative input files')
    delivery=package/'delivery.json'
    write_json(delivery,dict(final_videos_dir=user['final_videos_dir'],regeneration_assets_dir=str(package/'archive'),final_output_dir=str(output)))
    name=task.name+'_ALT_CAMERA';profile=package/(name+'.json')
    write_json(profile,dict(default_project=name,projects={name:dict(heroes=cfg['heroes'],goal=cfg['intent']+' Alternative camera perspective of the same original photographs.',
        motion_guidance='Choose a modest sideways, raised, lowered or diagonal camera move based on the actual image. Preserve faces and poses; reveal existing context rather than inventing hidden surfaces. Distinguish this take from the previous prompts.',
        camera_variant='alternative_perspective',previous_prompts=prompts,input_dir=str(inputs),output_dir=str(package/'work'),delivery_config=str(delivery),
        classification_file=str((task/'classify/classification.json').resolve()),art_checkpoints_dir=str((task/'art/checkpoints').resolve()),prompt_model='gpt-4.1-mini',
        cdp_url='http://127.0.0.1:9222',upload_timeout=300,generation_timeout=600,remove_completed_inputs=False)}))
    write_json(package/'source_manifest.json',dict(task_id=task.name,unique_photos=len(records),items=records))
    bat=package/(task.name+'_RUN_ALT_CAMERA.bat')
    bat.write_text('@echo off\r\nsetlocal\r\nset "PYTHONUTF8=1"\r\ncd /d "'+str(ROOT)+'"\r\npython -X utf8 scripts\\run_family_grok_sources.py "'+str(profile)+'" %*\r\nset "RESULT=%errorlevel%"\r\necho Exit code: %RESULT%\r\npause\r\nexit /b %RESULT%\r\n',encoding='utf-8')
    (package/'README_RU.txt').write_text(f'{len(records)} уникальных оригинальных фотографий с подтверждёнными готовыми видео. По одному дополнительному ролику на фото, с альтернативным движением камеры.\nЗапуск: {bat}\nGrok Imagine: отдельный Chrome CDP 9222, одна вкладка, 6s, 720p. Промпты формируются через OpenAI API при запуске.\nРезультаты: '+user['final_videos_dir']+'; имена '+name+'_*.mp4. Первые версии не заменяются. Кэш и реестр альтернативной очереди отдельные; повтор BAT продолжает её.\nДвижение выбирается индивидуально по изображению и предыдущим промптам. Готовые лица необходимо проверить визуально: обещать отсутствие деформаций нельзя.\n',encoding='utf-8')
    print(f'PREPARED {len(records)} unique photos; {sum(len(v) for v in completed.values())} previous verified videos\n{bat}')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('task');a=p.parse_args();prepare(task_path(a.task))
