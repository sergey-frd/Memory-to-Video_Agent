"""Persist family task recovery material under final_output_dir; never opens Adobe."""
import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from family_contract import ROOT, read, sha
from ingest import write_json


def retain(task):
    cfg = read(task/'task.json')
    if not cfg.get('paths',{}).get('config'):
        return None
    user_path = Path(cfg['paths']['config'])
    if not user_path.is_file():
        return None
    user = read(user_path)
    if not user.get('retain_family_results', False):
        return None
    output = Path(user['final_output_dir'])
    if output.resolve() != Path(cfg['classify']['permanent_project_dir']).resolve():
        raise ValueError('final_output_dir and permanent_project_dir disagree')
    if output.resolve().is_relative_to(task.resolve()):
        raise ValueError('Recovery output cannot be inside task')
    recovery = output/'RECOVERY'
    recovery.mkdir(parents=True, exist_ok=True)
    entries = {}
    def add(path, name):
        if path.is_file(): entries[name] = path
    for p in task.rglob('*'):
        if '__pycache__' not in p.parts and p.name != 'run.lock':
            add(p, 'task/'+p.relative_to(task).as_posix())
    add(user_path, 'config/'+user_path.name)
    # Include the implementation used by these checkpoints, not credentials or browser data.
    for folder in ('scripts', 'utils', 'tools', 'api'):
        for p in (ROOT/folder).rglob('*'):
            if '__pycache__' not in p.parts and p.suffix.lower() in {'.py','.jsx','.js','.json','.bat','.ps1','.epr'}:
                add(p,'runtime/'+p.relative_to(ROOT).as_posix())
    for p in ROOT.glob('*.py'):
        add(p,'runtime/'+p.name)
    for name in ('AGENTS.md','requirements.txt','.cursor/skills/premiere-offline-first/SKILL.md','docs/VIDEO_WORKFLOW_FINAL_RU.md'):
        add(ROOT/name,'runtime/'+name)
    projects = {Path(cfg['paths']['premiere_project'])}
    projects.update(p for p in Path(cfg['paths']['premiere_project']).parent.glob(task.name+'*.prproj'))
    def discover(obj):
        if isinstance(obj,dict):
            for key,val in obj.items(): discover(key); discover(val)
        elif isinstance(obj,list):
            for val in obj: discover(val)
        elif isinstance(obj,str) and obj.lower().endswith('.prproj'):
            p=Path(obj)
            if p.is_absolute() and p.is_file(): projects.add(p)
    for p in task.rglob('*.json'):
        try: discover(read(p))
        except (ValueError,UnicodeError): pass
    project_map = {}
    for p in sorted(projects):
        # Avoid collisions between projects of equal name at different locations.
        name='projects/'+hashlib.sha256(str(p).lower().encode()).hexdigest()[:10]+'/'+p.name
        add(p,name);project_map[str(p)]=name
    manifest = {name:dict(source=str(p),sha256=sha(p),bytes=p.stat().st_size) for name,p in entries.items()}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid4().hex[:6]
    dest=recovery/(task.name+'_recovery_'+stamp+'.zip')
    temp=dest.with_suffix('.partial')
    with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
        for name,p in entries.items():
            data=p.read_bytes()
            if hashlib.sha256(data).hexdigest()!=manifest[name]['sha256']:
                raise ValueError('File changed during snapshot: '+str(p))
            archive.writestr(name,data)
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        archive.writestr('project_paths.json',json.dumps(project_map,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(temp) as archive:
        for name,spec in manifest.items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=spec['sha256']:
                raise ValueError('Recovery hash mismatch: '+name)
    os.replace(temp,dest)
    # Inventory permanent results without recursively archiving prior recovery bundles.
    output_files={p.relative_to(output).as_posix():dict(sha256=sha(p),bytes=p.stat().st_size)
                  for p in output.rglob('*') if p.is_file() and not p.is_relative_to(recovery)}
    report=dict(task_id=task.name,status='VERIFIED_SAVED_SNAPSHOT',created_utc=stamp,
        archive=str(dest),archive_sha256=sha(dest),archived_files=len(manifest),
        saved_projects=len(project_map),permanent_files=len(output_files),
        native_QA='NOT_PERFORMED_BY_RETENTION',unsaved_premiere_changes='NOT_INCLUDED',
        source_media='Original photos/videos remain in their source directories. They are inputs, not duplicated results.')
    write_json(recovery/(stamp+'_output_inventory.json'),output_files)
    write_json(recovery/'LATEST.json',report)
    (recovery/'RESTORE_RU.txt').write_text(
        'Комплект результатов: родительский каталог, указанный в final_output_dir. Не удалять RECOVERY.\n'
        'LATEST.json указывает последний проверенный архив. В архиве: task — полное сохранённое состояние задачи, '
        'config — конфигурация, projects — сохранённые проекты Premiere, runtime — код этого pipeline. '
        'manifest.json содержит SHA-256, project_paths.json — исходные адреса проектов.\n'
        'Основное рабочее состояние хранится в final_output_dir/tasks/<TASK>. Для восстановления репозитория создать junction tasks/<TASK> на этот внешний каталог. Если он утрачен, извлечь task из архива туда. Каноническая конфигурация находится в final_output_dir/config. Для совместимости конфигурацию можно скопировать в корень репозитория; '
        'проекты по адресам project_paths.json; при другом расположении требуется согласованная миграция путей. '
        'Исторические checkpoints/JSX используют старый путь FamilyMemoryHub/output/Katya26: сохранить или восстановить junction '
        'на текущий final_output_dir. Не удалять содержимое через junction.\n'
        'Исходные фото и видео сохранять отдельно на исходных адресах. Архив не содержит несохранённых изменений Premiere. '
        'Перед очисткой после ручного сохранения проекта выполнить python scripts/family_retention.py Katya26.\n',encoding='utf-8')
    print(f'RETENTION VERIFIED: {len(manifest)} files, {len(project_map)} saved projects -> {dest}',flush=True)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('task');args=parser.parse_args()
    retain(ROOT/'tasks'/args.task)
