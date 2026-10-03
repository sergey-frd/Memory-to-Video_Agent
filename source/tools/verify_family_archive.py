"""Verify closeout copies and register current Adobe/media dependencies offline."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def verify(manifest, adobe_root=None, save=False):
    data=json.loads(manifest.read_text(encoding='utf-8'))
    for e in data['entries']:
        if sha(Path(e['target']))!=e['sha256']:raise ValueError('Archive mismatch: '+e['target'])
    results={}
    for hero,info in data['heroes'].items():
        archive=Path(info['archive']);cfg=json.loads((archive/'workspace'/Path(info['config']).name).read_text(encoding='utf-8-sig'))
        project_dir=Path(cfg['premiere_project_dir']);target=adobe_root/hero if adobe_root else project_dir
        projects=[]
        for p in sorted(project_dir.rglob('*.prproj')):
            raw=p.read_bytes();root=ET.fromstring(gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw)
            media=sorted(set(e.text for e in root.iter() if e.tag in ['FilePath','ActualMediaFilePath'] and e.text and ':' in e.text))
            projects.append(dict(path=str(p),sha256=sha(p),media=media,missing=[s for s in media if not Path(s).exists()]))
            if save and adobe_root and not p.resolve().is_relative_to(adobe_root.resolve()):
                dest=target/'projects'/p.relative_to(project_dir);dest.parent.mkdir(parents=True,exist_ok=True)
                if not dest.exists():shutil.copy2(p,dest)
                if sha(dest)!=sha(p):raise ValueError('Adobe copy conflict')
                projects[-1]['adobe_copy']=str(dest)
        adobe=[]
        for e in data['entries']:
            p=Path(e['target'])
            if not p.is_relative_to(archive) or p.suffix.lower() not in ['.jsx','.epr','.prproj']:continue
            item=dict(archive=str(p),sha256=e['sha256'])
            if save and adobe_root:
                dest=target/'recovery'/p.relative_to(archive);dest.parent.mkdir(parents=True,exist_ok=True)
                if not dest.exists():shutil.copy2(p,dest)
                if sha(dest)!=e['sha256']:raise ValueError('Adobe recovery conflict')
                item['adobe_copy']=str(dest)
            adobe.append(item)
        report=dict(status='HASH_VERIFIED',native_open_check='NOT_RUN',projects=projects,adobe_assets=adobe,
                    data_archive=str(archive),adobe_root=str(target),source_materials=info['source_materials_dir'])
        if save:
            (archive/'STORAGE_VERIFICATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            if adobe_root:
                target.mkdir(parents=True,exist_ok=True)
                (target/'STORAGE_VERIFICATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        results[hero]=dict(projects=len(projects),adobe_assets=len(adobe),missing=sum(len(p['missing']) for p in projects),archive=str(archive))
    return results

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--adobe-root',type=Path);parser.add_argument('--save',action='store_true')
    args=parser.parse_args();print(json.dumps(verify(args.manifest,args.adobe_root,args.save),ensure_ascii=False,indent=2))
