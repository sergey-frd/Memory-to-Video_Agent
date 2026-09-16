"""Archive obsolete task utilities and reconcile manually relocated project media.

Dry-run produces an exact hashed plan. Apply requires that plan and never touches
the accepted final videos. Keep private paths in a *.local.json configuration.
"""
from __future__ import annotations

import argparse
import gzip
import os
import subprocess
import sys
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.hero_video_closeout import (ROOT, PATH_TAGS, below, load, norm, now,
                                     project_xml, put, resolve_ref, safe, save, scan_projects, sha)


def check_workspace_file(path):
    p = safe(path)
    if not below(p, ROOT) or any(part in {'.git', '.agents', '.codex'} for part in p.relative_to(ROOT).parts):
        raise ValueError(f'Not an allowed workspace artifact: {p}')
    return p


def repair_bytes(project, mapping):
    root = ET.fromstring(project_xml(project))
    before = ET.tostring(root)
    changes = []
    for index, node in enumerate(root.iter()):
        if node.tag not in PATH_TAGS:
            continue
        oldpath = resolve_ref(node.text, project)
        if not oldpath or norm(oldpath) not in mapping:
            continue
        target = mapping[norm(oldpath)]
        value = os.path.relpath(target, project.parent) if node.tag == 'RelativePath' else target
        changes.append({'index': index, 'tag': node.tag, 'old': node.text, 'new': value})
        node.text = value
    result = gzip.compress(ET.tostring(root, encoding='utf-8', xml_declaration=True), mtime=0)
    nodes = list(root.iter())
    for change in changes:
        nodes[change['index']].text = change['old']
    if ET.tostring(root) != before:
        raise ValueError('Non-path XML changed')
    return result, changes


def plan(config_path, output):
    config = load(config_path)
    archive = safe(config['private_archive'])
    if below(archive, ROOT):
        raise ValueError('Private archive must be outside the repository')
    oldroot = check_workspace_file(config['relocation']['old_root'])
    newroot = safe(config['relocation']['new_root'])
    previous = load(config['relocation']['closeout_plan'])
    moved = []
    for row in previous['files']:
        old = Path(row['source'])
        if not below(old, oldroot):
            continue
        new = safe(newroot / old.relative_to(oldroot))
        if sha(new) != row['sha256']:
            raise ValueError(f'Moved file differs from the previous inventory: {new}')
        moved.append({'old': str(old), 'new': str(new), 'sha256': row['sha256']})
    if not moved:
        raise ValueError('No verified relocated files')
    mapping = {norm(x['old']): x['new'] for x in moved}
    references, count = scan_projects(config['project_scan_roots'], oldroot.parent.parent)
    repairs = []
    for ref in references:
        project = safe(ref['project'])
        raw, changes = repair_bytes(project, mapping)
        if changes:
            repairs.append({'project': str(project), 'sha256': sha(project),
                            'backup': str(archive / 'project_originals' / (sha(project)[:16] + '_' + project.name)),
                            'changes': changes})
    artifacts = []
    for rel in config['obsolete_workspace_files']:
        p = check_workspace_file(ROOT / rel)
        if not p.is_file():
            raise ValueError(f'Expected obsolete artifact missing: {p}')
        artifacts.append({'source': str(p), 'destination': str(archive / 'workspace_files' / p.relative_to(ROOT)),
                          'sha256': sha(p), 'bytes': p.stat().st_size})
    retained = []
    for path in config['retained_results']:
        p = safe(path)
        retained.append({'path': str(p), 'sha256': sha(p)})
    report = {'created_at': now(), 'config': str(Path(config_path).resolve()), 'config_sha256': sha(config_path),
              'archive': str(archive), 'moved_files': moved, 'scanned_projects': count,
              'project_repairs': repairs, 'artifacts': artifacts, 'retained_results': retained,
              'empty_directories': [str(check_workspace_file(ROOT / p)) for p in config['empty_directories']]}
    if Path(output).exists():
        raise ValueError('Plan already exists; choose a new plan file')
    save(output, report)
    print(f'PLAN: {len(moved)} verified moved files; {len(repairs)} path-only project repairs; {len(artifacts)} obsolete workspace files to archive and remove.')


def apply(plan_path):
    plan = load(plan_path)
    if sha(plan['config']) != plan['config_sha256']:
        raise ValueError('Configuration changed')
    config = load(plan['config'])
    archive = safe(config['private_archive'])
    if str(archive) != plan['archive'] or below(archive, ROOT):
        raise ValueError('Archive mismatch')
    allowed = {norm(check_workspace_file(ROOT / p)) for p in config['obsolete_workspace_files']}
    if {norm(x['source']) for x in plan['artifacts']} != allowed:
        raise ValueError('Artifact list differs from configuration')
    if os.name == 'nt':
        proc = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Adobe Premiere Pro.exe', '/FO', 'CSV', '/NH'], capture_output=True, text=True)
        if proc.returncode or 'Adobe Premiere Pro.exe' in proc.stdout:
            raise ValueError('Close Premiere before applying in-place historical project path repairs')
    for row in plan['moved_files']:
        if sha(safe(row['new'])) != row['sha256']:
            raise ValueError('Relocated media changed')
    for row in plan['artifacts']:
        p = check_workspace_file(row['source'])
        expected = archive / 'workspace_files' / p.relative_to(ROOT)
        if norm(row['destination']) != norm(expected) or sha(p) != row['sha256']:
            raise ValueError('Obsolete artifact changed or invalid destination')
    for row in plan['retained_results']:
        if sha(safe(row['path'])) != row['sha256']:
            raise ValueError('Accepted result changed')
    mapping = {norm(x['old']): x['new'] for x in plan['moved_files']}
    prepared = []
    for row in plan['project_repairs']:
        p = safe(row['project'])
        if not any(below(p, safe(base)) for base in config['project_scan_roots']) or sha(p) != row['sha256']:
            raise ValueError('Project changed or outside scan scope')
        raw, changes = repair_bytes(p, mapping)
        if changes != row['changes']:
            raise ValueError('Project repair differs from reviewed plan')
        expected_backup = archive / 'project_originals' / (row['sha256'][:16] + '_' + p.name)
        if norm(row['backup']) != norm(expected_backup):
            raise ValueError('Unexpected backup path')
        put(expected_backup, p.read_bytes())
        prepared.append((p, raw, row))
    for row in plan['artifacts']:
        put(Path(row['destination']), Path(row['source']).read_bytes())
    # All copies exist and are verified before the first overwrite or deletion.
    result = {'at': now(), 'plan_sha256': sha(plan_path), 'repaired_projects': [], 'removed_files': [],
              'removed_empty_directories': [], 'native_qa': 'NOT_RUN_PATH_REPAIR_ONLY', 'current_results': 'UNCHANGED'}
    local_report = Path(plan_path).with_name('result.json')
    for p, raw, row in prepared:
        if sha(p) != row['sha256']:
            raise ValueError('Project changed during operation')
        temp = safe(p.with_name(p.name + '.closeout.tmp'))
        with temp.open('xb') as f:
            f.write(raw)
        ET.fromstring(project_xml(temp))
        os.replace(temp, p)
        actual = ET.fromstring(project_xml(p))
        nodes = list(actual.iter())
        for change in row['changes']:
            if nodes[change['index']].text != change['new']:
                raise ValueError('Readback failed')
            q = resolve_ref(change['new'], p)
            if not q or not q.is_file():
                raise ValueError('Repaired media reference missing')
            nodes[change['index']].text = change['old']
        if ET.tostring(actual) != ET.tostring(ET.fromstring(project_xml(Path(row['backup'])))):
            raise ValueError('Project content readback mismatch')
        result['repaired_projects'].append({'path': str(p), 'sha256': sha(p), 'backup': row['backup'], 'path_changes': len(row['changes'])})
        save(local_report, result)
    for row in plan['artifacts']:
        p = check_workspace_file(row['source'])
        if sha(p) != row['sha256'] or sha(safe(row['destination'])) != row['sha256']:
            raise ValueError('Copy check failed before deletion')
        p.unlink()
        result['removed_files'].append(row)
        save(local_report, result)
    for value in plan['empty_directories']:
        p = check_workspace_file(value)
        if not below(p, ROOT / 'output') or norm(p) == norm(ROOT / 'output'):
            raise ValueError('Not a task output directory')
        if p.is_dir() and not any(p.iterdir()):
            p.rmdir()
            result['removed_empty_directories'].append(str(p))
    for row in plan['retained_results']:
        if sha(row['path']) != row['sha256']:
            raise ValueError('Accepted result changed')
    result['completed_at'] = now()
    save(local_report, result)
    save(archive / 'plan.json', plan)
    save(archive / 'result.json', result)
    print(f'APPLIED: {len(prepared)} historical project repairs; {len(result["removed_files"])} archived workspace files removed; final results unchanged.')


def stage(plan_path):
    """Prepare reviewable copies only, without any writes outside the workspace."""
    plan = load(plan_path)
    if sha(plan['config']) != plan['config_sha256']:
        raise ValueError('Configuration changed')
    mapping = {norm(x['old']): x['new'] for x in plan['moved_files']}
    for row in plan['moved_files']:
        if sha(row['new']) != row['sha256']:
            raise ValueError('Moved media changed')
    base = check_workspace_file(Path(plan_path).parent)
    result = {'at': now(), 'project_copies': [], 'native_qa': 'NOT_RUN_STRUCTURAL_ONLY'}
    entries = []
    for row in plan['artifacts']:
        p = check_workspace_file(row['source'])
        if sha(p) != row['sha256']:
            raise ValueError('Source changed')
        entries.append(('workspace_files/' + p.relative_to(ROOT).as_posix(), p.read_bytes(), row['sha256']))
    for row in plan['project_repairs']:
        p = Path(row['project'])
        if sha(p) != row['sha256']:
            raise ValueError('Project changed')
        raw, changes = repair_bytes(p, mapping)
        if changes != row['changes']:
            raise ValueError('Plan no longer matches')
        target = base / 'relinked_projects' / (p.stem + '_RELINKED.prproj')
        digest = put(target, raw)
        # Copies are intended for the original parent folder, preserving relative references.
        result['project_copies'].append({'source': str(p), 'prepared': str(target),
                                        'destination': str(p.with_name(target.name)), 'sha256': digest})
        entries.append(('project_originals/' + Path(row['backup']).name, p.read_bytes(), row['sha256']))
    import hashlib
    planbytes = json_bytes(plan)
    entries.append(('plan.json', planbytes, hashlib.sha256(planbytes).hexdigest()))
    zipped = base / 'workspace_archive.zip'
    if zipped.exists():
        raise ValueError('Staged ZIP already exists')
    with zipfile.ZipFile(zipped, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data, digest in entries:
            archive.writestr(name, data)
    with zipfile.ZipFile(zipped) as archive:
        for name, data, digest in entries:
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError('ZIP verification failed')
    result['archive_zip'] = {'source': str(zipped), 'sha256': sha(zipped),
                             'destination': plan['archive'] + '.zip'}
    save(base / 'staged_copies.json', result)
    print(f'STAGED: verified ZIP with {len(entries)} entries and {len(result["project_copies"])} relinked project copies; originals unchanged.')


def json_bytes(value):
    import json
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def prune_staged(plan_path):
    """Delete workspace artifacts only after separately deployed copies verify."""
    import hashlib
    plan = load(plan_path)
    staged = load(Path(plan_path).with_name('staged_copies.json'))
    if sha(plan['config']) != plan['config_sha256']:
        raise ValueError('Configuration changed')
    config = load(plan['config'])
    expected = {norm(check_workspace_file(ROOT / p)) for p in config['obsolete_workspace_files']}
    if {norm(row['source']) for row in plan['artifacts']} != expected:
        raise ValueError('Artifact list changed')
    archived = safe(plan['archive'] + '.zip')
    if norm(staged['archive_zip']['destination']) != norm(archived) or sha(archived) != staged['archive_zip']['sha256']:
        raise ValueError('Permanent ZIP archive is not verified')
    expected_projects = {norm(Path(x['project']).with_name(Path(x['project']).stem + '_RELINKED.prproj')) for x in plan['project_repairs']}
    if {norm(x['destination']) for x in staged['project_copies']} != expected_projects:
        raise ValueError('Unexpected repaired project destinations')
    for row in staged['project_copies']:
        if sha(safe(row['destination'])) != row['sha256']:
            raise ValueError('Permanent repaired project copy is not verified')
    for row in plan['moved_files']:
        if sha(safe(row['new'])) != row['sha256']:
            raise ValueError('Moved media changed')
    for row in plan['retained_results']:
        if sha(safe(row['path'])) != row['sha256']:
            raise ValueError('Final result changed')
    with zipfile.ZipFile(archived) as z:
        for row in plan['artifacts']:
            p = check_workspace_file(row['source'])
            entry = 'workspace_files/' + p.relative_to(ROOT).as_posix()
            if hashlib.sha256(z.read(entry)).hexdigest() != row['sha256'] or sha(p) != row['sha256']:
                raise ValueError('Source or ZIP entry changed')
    result = {'at': now(), 'permanent_archive': str(archived), 'archive_sha256': sha(archived),
              'relinked_project_copies': staged['project_copies'], 'native_qa': staged['native_qa'],
              'removed_files': [], 'removed_empty_directories': [], 'current_results': 'UNCHANGED'}
    for row in plan['artifacts']:
        p = check_workspace_file(row['source'])
        if sha(p) != row['sha256']:
            raise ValueError('Source changed before deletion')
        p.unlink()
        result['removed_files'].append({'path': str(p), 'sha256': row['sha256'], 'bytes': row['bytes']})
        save(Path(plan_path).with_name('result.json'), result)
    for value in plan['empty_directories']:
        p = check_workspace_file(value)
        if not below(p, ROOT / 'output') or norm(p) == norm(ROOT / 'output'):
            raise ValueError('Not a task output directory')
        if p.is_dir() and not any(p.iterdir()):
            p.rmdir()
            result['removed_empty_directories'].append(str(p))
    result['completed_at'] = now()
    save(Path(plan_path).with_name('result.json'), result)
    print(f'PRUNED: {len(result["removed_files"])} verified archived workspace files; {len(result["removed_empty_directories"])} empty task directories.')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', type=Path)
    ap.add_argument('--plan', type=Path, required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--stage', action='store_true')
    ap.add_argument('--prune-staged', action='store_true')
    args = ap.parse_args()
    if sum((args.stage, args.apply, args.prune_staged)) > 1:
        ap.error('--stage, --apply and --prune-staged are mutually exclusive')
    if args.prune_staged:
        prune_staged(args.plan)
    elif args.stage:
        stage(args.plan)
    elif args.apply:
        apply(args.plan)
    elif args.config:
        plan(args.config, args.plan)
    else:
        ap.error('--config is required for planning')
