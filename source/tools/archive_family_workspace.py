"""Hash-verified external closeout; never follows junctions or opens Adobe.

Plan first. Apply copies all records before deleting any source. Runtime folders
are replaced by compatibility junctions, preserving historical Premiere paths.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def files(root):
    if root.is_file():
        yield root
        return
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base)/d).is_junction()
                   and not (Path(base)/d).is_symlink()]
        for name in names:
            p = Path(base)/name
            if not p.is_symlink():
                yield p

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def plan(destination, config):
    settings=json.loads(config.read_text(encoding='utf-8-sig'))
    archive_id=settings['archive_id']
    if not archive_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in archive_id):
        raise ValueError('Unsafe archive ID')
    entries, redirects, configs = [], [], {}
    def add(source, target, remove=False):
        entries.append(dict(source=str(source.absolute()), target=str(target.absolute()),
                            sha256=sha(source), bytes=source.stat().st_size, remove=remove))
    for hero, item in settings['heroes'].items():
        config_name=item['config']
        cfg=json.loads(Path(config_name).read_text(encoding='utf-8-sig'))
        archive=Path(cfg['final_output_dir'])/'RECOVERY'/settings['archive_id']
        if archive.resolve().is_relative_to(ROOT):
            raise ValueError('Archive must be external')
        configs[hero]=dict(archive=str(archive), config=config_name,
                          premiere_project_dir=cfg['premiere_project_dir'],
                          source_materials_dir=cfg['source_materials_dir'])
        tokens=[hero.lower()]+[t.lower() for t in item.get('aliases',[])]
        if any(not t for t in tokens):raise ValueError('Empty hero/alias')
        for folder in ['output','premiere_scripts','tasks']:
            for p in (ROOT/folder).iterdir():
                if not p.is_dir() or p.is_junction() or p.is_symlink(): continue
                if any(t in p.name.lower() for t in tokens):
                    target=archive/'workspace'/p.relative_to(ROOT)
                    redirects.append(dict(source=str(p.absolute()), target=str(target.absolute())))
                    for f in files(p): add(f, target/f.relative_to(p), True)
        for folder in [ROOT,ROOT/'tools',ROOT/'docs',ROOT/'scripts']:
            for p in folder.iterdir():
                if not p.is_file(): continue
                specific=any(t in p.name.lower() for t in tokens) or p.resolve()==Path(config_name).resolve()
                specific=specific or p.relative_to(ROOT).as_posix() in item.get('extra_files',[])
                if specific: add(p,archive/'workspace'/p.relative_to(ROOT),True)
        for key in ['human_detail_txt','family_detail_txt','family_screenshot']:
            if cfg.get(key):
                p=Path(cfg[key])
                if not p.is_file(): raise FileNotFoundError(p)
                add(p,archive/'inputs'/key/p.name)
        # Shared queue state can contain multiple heroes: preserve for each.
        for name in ['config_grok_queue.json']:
            p=ROOT/name
            if p.is_file(): add(p,archive/'workspace'/name)
    # Deduplicate shared historical scripts by source; preserve in every archive
    # but delete only after all copies have been checked.
    result=dict(status='PLANNED', heroes=configs, entries=entries, redirects=redirects)
    write(destination,result)
    print(json.dumps(dict(plan=str(destination),files=len(entries),bytes=sum(e['bytes'] for e in entries)),indent=2))

def apply(path, archive_only=False):
    data=json.loads(path.read_text(encoding='utf-8'))
    if data['status']!='PLANNED': raise ValueError('Already applied')
    archives=[Path(v['archive']).resolve() for v in data['heroes'].values()]
    for e in data['entries']:
        src,dst=Path(e['source']),Path(e['target'])
        if not any(dst.resolve().is_relative_to(a) for a in archives): raise ValueError('Destination escape')
        if e['remove'] and (not src.resolve().is_relative_to(ROOT) or src.is_symlink()): raise ValueError('Unsafe removal')
        if sha(src)!=e['sha256']: raise ValueError('Source changed: '+str(src))
        dst.parent.mkdir(parents=True,exist_ok=True)
        if not dst.exists(): shutil.copy2(src,dst)
        if sha(dst)!=e['sha256']: raise ValueError('Copy mismatch: '+str(dst))
    # Persist provenance externally before removing even one source.
    for a in archives: write(a/'manifests'/path.name,data)
    if archive_only:
        print(json.dumps(dict(status='ARCHIVE_VERIFIED_CLEANUP_PENDING',files=len(data['entries'])),indent=2))
        return
    for e in data['entries']:
        if sha(Path(e['target']))!=e['sha256']: raise ValueError('Archive changed')
    removed=set()
    for e in data['entries']:
        if e['remove'] and e['source'] not in removed:
            p=Path(e['source'])
            if sha(p)!=e['sha256']: raise ValueError('Source changed before deletion')
            p.unlink();removed.add(e['source'])
    for item in data['redirects']:
        src,dst=Path(item['source']),Path(item['target'])
        if src.resolve()==ROOT or not src.resolve().is_relative_to(ROOT): raise ValueError('Unsafe directory')
        for base,dirs,names in os.walk(src,topdown=False,followlinks=False):
            Path(base).rmdir() # fails safely if any unplanned file appeared
        dst.mkdir(parents=True,exist_ok=True)
        # PowerShell only: no cross-shell path enumeration/deletion.
        quote=lambda p: "'"+str(p).replace("'","''")+"'"
        subprocess.run(['powershell','-NoProfile','-Command',
                        'New-Item -ItemType Junction -Path '+quote(src)+' -Target '+quote(dst)+' | Out-Null'],check=True)
    data.update(status='ARCHIVED_AND_CLEANED',removed_files=len(removed))
    write(path,data)
    for a in archives: write(a/'manifests'/path.name,data)
    print(json.dumps(dict(status=data['status'],removed_files=len(removed),junctions=len(data['redirects'])),indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['plan','archive','apply'])
    parser.add_argument('--manifest',type=Path,default=ROOT/'cleanup_archive/workspace_closeout.json')
    parser.add_argument('--config',type=Path,help='External private hero mapping; required for plan')
    args=parser.parse_args()
    if args.mode=='plan':
        if args.config is None:parser.error('plan requires --config')
        plan(args.manifest,args.config)
    else:apply(args.manifest,archive_only=args.mode=='archive')
