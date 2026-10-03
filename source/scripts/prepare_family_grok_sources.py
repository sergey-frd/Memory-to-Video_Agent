"""Stage original ART source photographs for the existing Grok queue. No API/browser calls."""
import argparse
import shutil
from pathlib import Path
from family_contract import ROOT, read, sha, task_path
from ingest import write_json

def prepare(task):
    cfg=read(task/'task.json'); user=read(cfg['paths']['config'])
    output=Path(user['final_output_dir']).resolve()
    if output!=Path(cfg['classify']['permanent_project_dir']).resolve():
        raise ValueError('Permanent output mismatch')
    package=output/'GROK_FROM_ART_SOURCES';package.mkdir(parents=True,exist_ok=True)
    profiles={};provenance=[]
    checkpoints=[read(p) for p in sorted((task/'art/checkpoints').glob('*.json'))]
    art_config=read(task/'art_config.json')
    for kind in ('watercolor','double_exposure'):
        rows=[c for c in checkpoints if c.get('art_type')==kind and c.get('status')=='COMPLETE']
        expected_count=art_config[kind+'_count']
        if len(rows)!=expected_count:raise ValueError(f'Expected {expected_count} completed {kind} checkpoints; found {len(rows)}')
        folder=package/kind; inputs=folder/'input';inputs.mkdir(parents=True,exist_ok=True)
        delivery=folder/'delivery.json'
        write_json(delivery,dict(final_videos_dir=str(Path(user['final_videos_dir']).resolve()),regeneration_assets_dir=str(folder/'archive'),final_output_dir=str(output)))
        for c in rows:
            source=Path(c['original_source_path']);expected=sha(source)
            target=inputs/(task.name+'_'+c['art_id']+'_original'+source.suffix.lower())
            if target.exists() and sha(target)!=expected:raise ValueError('Staged input changed: '+str(target))
            if not target.exists():shutil.copy2(source,target)
            if sha(target)!=expected:raise ValueError('Source copy verification failed')
            provenance.append(dict(art_id=c['art_id'],art_type=kind,source_media_id=c['source_media_id'],original=str(source),staged=str(target),sha256=expected,input_kind='ORIGINAL_PHOTOGRAPH'))
        name=task.name+'_'+kind
        profiles[name]=dict(heroes=cfg['heroes'],goal='Task context: '+cfg['intent']+' Animate the ORIGINAL photographs selected for the '+kind+' ART set. Keep a photographic image, not a watercolor or double-exposure transformation. Do not infer the identity of any visible person.',
            motion_guidance='Preserve all existing people, face likeness, age, pose, hands, clothing, objects and composition. Minimal natural motion and a locked camera; choose image-specific motion. No new people, morphing, style change, scene change, text, speech or music.',
            input_dir=str(inputs),output_dir=str(folder/'work'),delivery_config=str(delivery),
            classification_file=str((task/'classify/classification.json').resolve()),art_checkpoints_dir=str((task/'art/checkpoints').resolve()),
            prompt_model='gpt-4.1-mini',cdp_url='http://127.0.0.1:9222',upload_timeout=300,generation_timeout=600,remove_completed_inputs=False)
    profile_path=package/(task.name+'_grok_sources.json')
    write_json(profile_path,dict(default_project=next(iter(profiles)),projects=profiles))
    write_json(package/'source_manifest.json',dict(task_id=task.name,input_kind='ORIGINAL_PHOTOGRAPHS',items=provenance))
    launcher=package/(task.name+'_RUN_GROK_SOURCES.bat')
    launcher.write_text('@echo off\r\nsetlocal\r\nset "PYTHONUTF8=1"\r\ncd /d "'+str(ROOT)+'"\r\npython -X utf8 scripts\\run_family_grok_sources.py "'+str(profile_path)+'" %*\r\nset "RESULT=%errorlevel%"\r\necho Exit code: %RESULT%\r\npause\r\nexit /b %RESULT%\r\n',encoding='utf-8')
    (package/'README_RU.txt').write_text('Katya26: 10 оригинальных фото для watercolor и 10 для double_exposure. Это не готовые ART-изображения.\nЗапуск: '+str(launcher)+'\nСначала watercolor, затем double_exposure; при ошибке остановка. Повтор той же команды продолжает очередь без повторения проверенных готовых видео.\nПроверка без API/браузера: добавить --dry-run. Только подготовка промптов: --prepare-only.\nИспользуется уже открытый Chrome с CDP 9222, одна вкладка https://grok.com/imagine, вход в аккаунт. Выбрать 6s и 720p. Звук скрипт отключает. Положение курсора не имеет значения. Не менять вкладку во время работы.\nПромпты создаются через OpenAI API (gpt-4.1-mini): передаются фото и соответствующее описание. Генерация видео выполняется в Grok. Нужны существующий OPENAI_API_KEY и зависимости проекта.\nГотовые видео обеих очередей: '+str(Path(user['final_videos_dir']).resolve())+'. Все планы, кэш, реестр повторов, входные копии и логи сохраняются в этом пакете. Исходники не удаляются. Видео автоматически не вставляются в MAIN/SHORT.\n',encoding='utf-8')
    print(f'PREPARED {len(provenance)} original photographs\n{launcher}')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('task');a=p.parse_args();prepare(task_path(a.task))
