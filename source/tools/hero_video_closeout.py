"""Offline, manifest-driven closeout. No AI, Adobe launch, or recursive deletion."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
PATH_TAGS = {'FilePath', 'ActualMediaFilePath', 'RelativePath', 'MediaFilePath'}
HISTORY_TAGS = {'project.settings.lastknowngoodprojectpath',
                'project.settings.lastknownparentdirectorypathaboveprojectpath'}
CACHE_SUFFIXES = {'.pyc', '.pyo', '.prin', '.pek', '.cfa', '.tmp', '.temp'}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def norm(path):
    value = os.fspath(path)
    if value.startswith('\\\\?\\UNC\\'):
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value))


def below(path, root):
    return Path(norm(path)).is_relative_to(Path(norm(root)))


def safe(path):
    """Reject symlinks/junctions on any existing component, including parents."""
    p = Path(os.path.abspath(path))
    for q in (p, *p.parents):
        if q.is_symlink() or (hasattr(q, 'is_junction') and q.is_junction()):
            raise ValueError(f'Reparse point is not allowed: {q}')
    return p


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, data):
    path = safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def files(root):
    root = safe(root)
    if not root.is_dir():
        raise ValueError(f'Missing scan directory: {root}')
    def error(e):
        raise e
    for folder, dirs, names in os.walk(root, followlinks=False, onerror=error):
        for d in dirs:
            safe(Path(folder) / d)
        for name in names:
            yield safe(Path(folder) / name)


def context(job_path):
    job_path = safe(job_path)
    job = load(job_path)
    config = safe(ROOT / job['hero_config'])
    hero = load(config)
    task = job['task_id']
    if not re.fullmatch(r'TASK\d{3,}', task):
        raise ValueError('Invalid task_id')
    source = safe(ROOT / job['working_dir'])
    # Destructive scope is deliberately restricted to one task under this repository/output.
    if norm(source) != norm(ROOT / 'output' / task):
        raise ValueError('working_dir must be exactly repository/output/<task_id>')
    destinations = {}
    for name, route in job['storage'].items():
        base = safe(hero[route['config_key']])
        dest = safe(base / route['relative'])
        if not below(dest, base) or below(dest, source) or below(source, dest):
            raise ValueError(f'Unsafe destination: {dest}')
        destinations[name] = dest
    return job, hero, config, source, destinations


def resolve_ref(value, project):
    if not value or value.isdigit():
        return None
    if value.startswith('file://'):
        url = urlparse(value)
        value = unquote(url.path)
        if re.match(r'^/[A-Za-z]:', value):
            value = value[1:]
        elif url.netloc and url.netloc != 'localhost':
            value = '//' + url.netloc + value
    elif '://' in value:
        return None
    p = Path(value)
    return Path(os.path.abspath(p if p.is_absolute() else project.parent / p))


def project_xml(path):
    raw = path.read_bytes()
    return gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw


def scan_projects(scan_roots, source):
    """Scan all configured projects, including autosaves, without starting Adobe."""
    candidates = sorted({p for base in scan_roots for p in files(base) if p.suffix.lower() == '.prproj'})
    def inspect(p):
        # Files inside the task will have operational relocated copies; external files stay unchanged.
        if below(p, source):
            return None
        raw = project_xml(p)
        # An outside project cannot reference this task without the task directory name.
        lowered = raw.lower()
        if not any(source.name.lower().encode(encoding) in lowered for encoding in ('utf-8', 'utf-16-le', 'utf-16-be')):
            return None
        root = ET.fromstring(raw)
        refs = []
        for node in root.iter():
            # Premiere's remembered save location is provenance, not a media dependency.
            if node.tag in HISTORY_TAGS:
                continue
            values = ([node.text] if node.text else []) + list(node.attrib.values())
            for value in values:
                if node.tag not in PATH_TAGS and source.name.lower() not in value.lower():
                    continue
                q = resolve_ref(value, p)
                if q and below(q, source):
                    refs.append(str(q))
        if refs:
            return {'project': str(p), 'sha256': sha(p), 'references': sorted(set(refs))}
        return None
    protected = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, item in enumerate(pool.map(inspect, candidates), 1):
            if item:
                protected.append(item)
            if index % 500 == 0:
                print(f'Project reference scan: {index}/{len(candidates)}', flush=True)
    return protected, len(candidates)


def route_file(p, source, storage):
    rel = p.relative_to(source)
    parts = [s.lower() for s in rel.parts]
    if p.suffix.lower() in CACHE_SUFFIXES or '__pycache__' in parts:
        return None, 'rebuildable_cache'
    if any('adobe premiere pro audio previews' in s for s in parts):
        return None, 'rebuildable_audio_preview'
    if parts[0] == '01_classification':
        return storage['classification'] / Path(*rel.parts[1:]), 'classification'
    if 'classification' in parts:
        return storage['classification'] / 'historical' / rel, 'classification_history'
    if 'artwork' in parts:
        return storage['artwork'] / rel, 'artwork'
    if p.suffix.lower() in {'.py', '.jsx'}:
        return storage['provenance'] / 'legacy_scripts' / rel, 'legacy_code_snapshot'
    return storage['history'] / rel, 'history'


def make_plan(job_path, plan_path):
    job, hero, config, source, storage = context(job_path)
    if Path(plan_path).exists():
        raise ValueError('Plan already exists. Reuse it, or choose a new --plan path.')
    if not source.is_dir():
        raise ValueError(f'Working directory does not exist: {source}')
    protected, count = scan_projects([safe(ROOT / s) for s in job['project_scan_roots']], source)
    refs = {norm(r) for p in protected for r in p['references']}
    rows = []
    for p in sorted(files(source)):
        dest, kind = route_file(p, source, storage)
        # A cache referenced by a retained project is not disposable.
        if norm(p) in refs and dest is None:
            dest, kind = storage['history'] / p.relative_to(source), 'referenced_dependency'
        rows.append({'source': str(p), 'destination': str(dest) if dest else None,
                     'kind': kind, 'bytes': p.stat().st_size, 'sha256': sha(p),
                     'keep_source': norm(p) in refs})
    paths = [norm(x['destination']) for x in rows if x['destination']]
    if len(paths) != len(set(paths)):
        raise ValueError('Destination collision')
    final = []
    for entry in job['retained_results']:
        p = safe(Path(hero[entry['config_key']]) / entry['relative'])
        if not p.is_file():
            raise ValueError(f'Retained result is missing: {p}')
        final.append({'path': str(p), 'sha256': sha(p), 'bytes': p.stat().st_size,
                      'role': entry['role']})
    plan = {'schema_version': 1, 'created_at': now(), 'task_id': job['task_id'],
            'job_path': str(Path(job_path).resolve()), 'job_sha256': sha(job_path),
            'hero_config': str(config), 'hero_config_sha256': sha(config),
            'working_dir': str(source), 'storage': {k: str(v) for k, v in storage.items()},
            'project_scan_count': count, 'protected_by_projects': protected,
            'retained_results': final, 'files': rows,
            'acceptance': 'User requested closeout assuming completed, satisfactory videos; no new creative QA claimed.',
            'native_qa': 'Existing final projects are not modified. Relocated historical projects require separate native review if reused.'}
    save(plan_path, plan)
    print_summary(plan)


def print_summary(plan):
    rows = plan['files']
    print(json.dumps({'task': plan['task_id'], 'files': len(rows),
                      'archive_files': sum(bool(x['destination']) for x in rows),
                      'protected_sources': sum(x['keep_source'] for x in rows),
                      'cleanup_files': sum(not x['keep_source'] for x in rows),
                      'cleanup_bytes': sum(x['bytes'] for x in rows if not x['keep_source']),
                      'cache_bytes': sum(x['bytes'] for x in rows if not x['destination'])}, ensure_ascii=False))


def validate_plan(plan_path):
    plan = load(plan_path)
    job, hero, config, source, storage = context(plan['job_path'])
    if sha(plan['job_path']) != plan['job_sha256'] or sha(config) != plan['hero_config_sha256']:
        raise ValueError('Configuration changed after planning')
    if plan['working_dir'] != str(source) or plan['storage'] != {k: str(v) for k, v in storage.items()}:
        raise ValueError('Plan storage mismatch')
    for row in plan['files']:
        p = safe(row['source'])
        if not below(p, source) or norm(p) == norm(source):
            raise ValueError(f'Outside cleanup scope: {p}')
        dest, kind = route_file(p, source, storage)
        if row['kind'] == 'referenced_dependency':
            dest = storage['history'] / p.relative_to(source)
        if row['destination'] != (str(dest) if dest else None):
            raise ValueError(f'Invalid route: {p}')
        if dest:
            safe(dest)
    return plan, job, source, storage


def mapped_string(value, mapping):
    if Path(value).is_absolute() and norm(value) in mapping:
        return mapping[norm(value)]
    return value


def transform_json(data, mapping):
    if isinstance(data, str):
        return mapped_string(data, mapping)
    if isinstance(data, list):
        return [transform_json(x, mapping) for x in data]
    if isinstance(data, dict):
        return {k: transform_json(v, mapping) for k, v in data.items()}
    return data


def transformed(p, dest, mapping):
    raw = p.read_bytes()
    changes = []
    if p.suffix.lower() == '.prproj':
        root = ET.fromstring(project_xml(p))
        for n in root.iter():
            if n.tag not in PATH_TAGS:
                continue
            q = resolve_ref(n.text, p)
            if not q:
                continue
            new = mapping.get(norm(q), str(q))
            # Relative media references are made absolute so relocation preserves their meaning.
            if n.text != new:
                changes.append({'tag': n.tag, 'old': n.text, 'new': new})
                n.text = new
        if changes:
            raw = gzip.compress(ET.tostring(root, encoding='utf-8', xml_declaration=True), mtime=0)
    elif p.suffix.lower() in {'.json', '.jsonl'}:
        try:
            text = raw.decode('utf-8-sig')
            old = [json.loads(s) for s in text.splitlines() if s.strip()] if p.suffix.lower() == '.jsonl' else json.loads(text)
            new = transform_json(old, mapping)
            if old != new:
                changes = [{'kind': 'mapped_absolute_paths'}]
                raw = (('\n'.join(json.dumps(x, ensure_ascii=False) for x in new) if p.suffix.lower() == '.jsonl'
                        else json.dumps(new, ensure_ascii=False, indent=2)) + '\n').encode('utf-8')
        except (ValueError, UnicodeError):
            # Historical malformed files are preserved byte-for-byte, never silently repaired.
            pass
    return raw, changes


def put(path, raw):
    path = safe(path)
    expected = hashlib.sha256(raw).hexdigest()
    if path.exists():
        if sha(path) != expected:
            raise ValueError(f'Destination conflict; refusing overwrite: {path}')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + '.tmp')
        with temp.open('xb') as f:
            f.write(raw)
        if sha(temp) != expected:
            raise ValueError(f'Copy checksum failed: {temp}')
        os.replace(temp, path)
    return expected


def archive(plan_path):
    plan, job, source, storage = validate_plan(plan_path)
    if (storage['metadata'] / 'archive_manifest.json').exists():
        verify(plan_path, fresh_scan=False)
        print('Archive already complete; verified and reused.')
        return
    mapping = {norm(x['source']): x['destination'] for x in plan['files'] if x['destination']}
    manifest = {'plan_sha256': sha(plan_path), 'created_at': now(), 'files': [],
                'retained_results': plan['retained_results'], 'native_qa': plan['native_qa']}
    # Validate all source checksums before writing anything outside the workspace.
    for row in plan['files']:
        if sha(row['source']) != row['sha256']:
            raise ValueError(f'Source changed: {row["source"]}')
    for row in plan['files']:
        if not row['destination']:
            continue
        p, dest = Path(row['source']), Path(row['destination'])
        raw, changes = transformed(p, dest, mapping)
        entry = dict(row, archived_sha256=put(dest, raw), changes=changes)
        if changes:
            original = storage['provenance'] / 'original_bytes' / p.relative_to(source)
            # .original is a provenance snapshot, not a relocated operational project.
            original = original.with_name(original.name + '.original')
            entry['original_copy'] = str(original)
            entry['original_sha256'] = put(original, p.read_bytes())
        manifest['files'].append(entry)
    for config in [Path(plan['hero_config']), Path(plan['job_path'])]:
        target = storage['metadata'] / config.name
        manifest.setdefault('config_snapshots', []).append({'path': str(target), 'sha256': put(target, config.read_bytes())})
    save(storage['metadata'] / 'closeout_plan.json', plan)
    save(storage['metadata'] / 'relocations.json', [{'old': x['source'], 'new': x['destination']} for x in manifest['files']])
    save(storage['metadata'] / 'archive_manifest.json', manifest)
    save(Path(plan_path).parent / 'archive_manifest.json', manifest)
    print(f'Archived {len(manifest["files"])} files. Manifest: {storage["metadata"] / "archive_manifest.json"}', flush=True)


def verify(plan_path, *, fresh_scan=True):
    plan, job, source, storage = validate_plan(plan_path)
    manifest = load(storage['metadata'] / 'archive_manifest.json')
    if manifest['plan_sha256'] != sha(plan_path):
        raise ValueError('Archive was made with a different plan')
    expected_sources = {x['source'] for x in plan['files'] if x['destination']}
    if {x['source'] for x in manifest['files']} != expected_sources:
        raise ValueError('Incomplete archive manifest')
    planned = {x['source']: x for x in plan['files']}
    for entry in manifest['files']:
        for key in ('destination', 'sha256', 'bytes', 'kind'):
            if entry[key] != planned[entry['source']][key]:
                raise ValueError('Archive manifest does not match the plan')
    report = {'checked_at': now(), 'status': 'PASS_ARCHIVE_INTEGRITY', 'files_verified': 0,
              'preexisting_missing_media': [], 'relocated_projects': [], 'native_qa': plan['native_qa']}
    dests = {norm(x['destination']) for x in manifest['files']}
    originals = {norm(x['source']): x['destination'] for x in manifest['files']}
    for entry in manifest['files']:
        p = safe(entry['destination'])
        if sha(p) != entry['archived_sha256']:
            raise ValueError(f'Archived file changed: {p}')
        if entry.get('original_copy') and sha(safe(entry['original_copy'])) != entry['sha256']:
            raise ValueError(f'Original snapshot failed: {entry["original_copy"]}')
        report['files_verified'] += 1
        if p.suffix.lower() == '.prproj':
            root = ET.fromstring(project_xml(p))
            missing = set()
            for n in root.iter():
                if n.tag not in PATH_TAGS:
                    continue
                q = resolve_ref(n.text, p)
                if not q:
                    continue
                if below(q, source):
                    raise ValueError(f'Archived project still depends on output: {p}: {q}')
                if norm(q) in dests and not q.is_file():
                    raise ValueError(f'Missing relocated dependency: {q}')
                if not q.exists():
                    missing.add(str(q))
            # Prove every reference still means exactly the old path or its relocation.
            oldpath = Path(entry.get('original_copy', entry['destination']))
            oldroot = ET.fromstring(project_xml(oldpath))
            oldrefs = [n for n in oldroot.iter() if n.tag in PATH_TAGS]
            newrefs = [n for n in root.iter() if n.tag in PATH_TAGS]
            if len(oldrefs) != len(newrefs):
                raise ValueError('Project reference count changed')
            for old, new in zip(oldrefs, newrefs):
                q = resolve_ref(old.text, Path(entry['source']))
                if q:
                    expected = originals.get(norm(q), str(q))
                    actual = resolve_ref(new.text, p)
                    if not actual or norm(actual) != norm(expected):
                        raise ValueError(f'Project reference meaning changed: {p}')
                # Revert path edits then compare all non-path XML exactly.
                new.text = old.text
            if ET.tostring(root) != ET.tostring(oldroot):
                raise ValueError(f'Non-path project data changed: {p}')
            report['relocated_projects'].append(str(p))
            if missing:
                report['preexisting_missing_media'].append({'project': str(p), 'paths': sorted(missing)})
    for row in manifest.get('config_snapshots', []):
        if sha(safe(row['path'])) != row['sha256']:
            raise ValueError('Config snapshot changed')
    for result in plan['retained_results']:
        if sha(safe(result['path'])) != result['sha256']:
            raise ValueError(f'Retained final changed: {result["path"]}')
    if fresh_scan:
        protected, count = scan_projects([safe(ROOT / s) for s in job['project_scan_roots']], source)
        report['protected_by_projects'] = protected
        report['project_scan_count'] = count
    save(Path(plan_path).parent / 'verify_report.json', report)
    print(f'Verified {report["files_verified"]} archive files; {len(report["relocated_projects"])} historical projects; existing final results unchanged.', flush=True)
    return report


def cleanup(plan_path, apply=False):
    plan, job, source, storage = validate_plan(plan_path)
    report = verify(plan_path)
    protected = {norm(r) for p in report['protected_by_projects'] for r in p['references']}
    protected.update(norm(x['source']) for x in plan['files'] if x['keep_source'])
    if source.exists():
        known = {norm(x['source']) for x in plan['files']}
        unexpected = [str(p) for p in files(source) if norm(p) not in known]
        if unexpected:
            raise ValueError(f'New files appeared after planning: {unexpected[:8]}')
    candidates, retained, absent = [], [], []
    for row in plan['files']:
        p = safe(row['source'])
        if not p.exists():
            absent.append(str(p))
        elif sha(p) != row['sha256']:
            raise ValueError(f'Source changed since plan: {p}')
        elif norm(p) in protected:
            retained.append(str(p))
        else:
            candidates.append(row)
    result = {'at': now(), 'mode': 'APPLY' if apply else 'DRY_RUN',
              'candidate_files': len(candidates), 'candidate_bytes': sum(x['bytes'] for x in candidates),
              'protected_sources': retained, 'already_absent': len(absent), 'deleted': []}
    local = Path(plan_path).parent / ('cleanup_result.json' if apply else 'cleanup_dry_run.json')
    if apply and local.exists():
        save(local.parent / 'cleanup_runs' / (sha(local) + '.json'), load(local))
    save(local, result)
    if apply:
        # Only the explicit verified files are removed; no rmtree/glob deletion is used.
        for row in candidates:
            p = safe(row['source'])
            if not below(p, source) or norm(p) == norm(source) or sha(p) != row['sha256']:
                raise ValueError(f'Deletion precondition failed: {p}')
            p.unlink()
            result['deleted'].append({'path': str(p), 'bytes': row['bytes'], 'reason': row['kind'] if not row['destination'] else 'verified_archived_copy'})
            save(local, result)
        if source.exists():
            for folder, dirs, names in os.walk(source, topdown=False):
                p = safe(folder)
                if below(p, source) and not any(p.iterdir()):
                    p.rmdir()
        result['completed_at'] = now()
        save(local, result)
        save(local.parent / 'cleanup_runs' / (sha(local) + '.json'), result)
    print(json.dumps({k: v for k, v in result.items() if k not in ('deleted', 'protected_sources')}, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['plan', 'archive', 'verify', 'cleanup', 'publish-report'])
    parser.add_argument('--job', type=Path)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='Required only for cleanup deletion')
    args = parser.parse_args()
    plan_path = safe(args.plan)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    lock = plan_path.parent / '.closeout.lock'
    with lock.open('x') as f:
        f.write(str(os.getpid()))
    try:
        if args.stage == 'plan':
            if not args.job:
                parser.error('--job is required for plan')
            make_plan(args.job, plan_path)
        elif args.stage == 'archive':
            archive(plan_path)
        elif args.stage == 'verify':
            verify(plan_path)
        elif args.stage == 'cleanup':
            cleanup(plan_path, args.apply)
        else:
            plan, job, source, storage = validate_plan(plan_path)
            for name in ('verify_report.json', 'cleanup_dry_run.json', 'cleanup_result.json'):
                p = plan_path.parent / name
                if p.exists():
                    save(storage['metadata'] / name, load(p))
                    if name == 'cleanup_result.json':
                        save(storage['metadata'] / 'cleanup_runs' / (sha(p) + '.json'), load(p))
            history = plan_path.parent / 'cleanup_runs'
            if history.exists():
                for p in history.glob('*.json'):
                    save(storage['metadata'] / 'cleanup_runs' / p.name, load(p))
            print(f'Reports saved to {storage["metadata"]}')
    finally:
        lock.unlink()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, ET.ParseError) as exc:
        print(f'CLOSEOUT STOPPED: {exc}', file=sys.stderr)
        sys.exit(1)
