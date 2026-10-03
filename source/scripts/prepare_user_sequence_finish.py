"""Prepare an editable finish of an existing user montage; never launches Adobe."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils import premiere_project as pp
from utils.premiere_sequence_motion import _track_item_contexts, protected_property_snapshot

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def validate(job):
    if (job['width'],job['height'],job['fps'])!=(2160,3840,25):
        raise ValueError('Current native route supports vertical UHD 25 fps only; horizontal requires separate validation')
    if not isinstance(job['video_track'],int) or job['video_track']<0:raise ValueError('Invalid track')
    if job['transition_alignment'] not in [0,.5]:raise ValueError('Alignment must be 0 or .5')
    if job['source_name']==job['name']:raise ValueError('Use a new sequence name')
    if not job['name'] or not job['source_name']:raise ValueError('Sequence names required')
    video=[c for c in job['expected'] if c['group']==0 and c['track_index']==job['video_track']]
    if not video:raise ValueError('Empty selected video track')
    indices=set()
    def number(v):
        if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v):raise ValueError('Nonfinite value')
    def position(v):
        if not isinstance(v,list) or len(v)!=2:raise ValueError('Position needs two coordinates')
        for n in v:number(n)
    for edit in job['edits']:
        i=edit['index']
        if not isinstance(i,int) or i<1 or i>len(video) or i in indices:raise ValueError('Invalid/duplicate shot index')
        indices.add(i);number(edit['scale']);position(edit['position'])
        if edit['scale']<=0:raise ValueError('Positive scale required')
        frames=(video[i-1]['timeline_end_ticks']-video[i-1]['timeline_start_ticks'])/10160640000
        previous=-1
        for row in edit['keys']:
            if len(row)!=3:raise ValueError('Key needs frame, scale, position')
            number(row[0]);number(row[1]);position(row[2])
            if not isinstance(row[0],int) or not previous<row[0]<frames or row[1]<=0:raise ValueError('Invalid key timing/scale')
            previous=row[0]
    ranges=[]
    for tr in job['transitions']:
        i=tr['incoming'];n=tr['frames'];cut=tr['cut']
        if not isinstance(i,int) or not 2<=i<=len(video):raise ValueError('Invalid incoming shot')
        if not isinstance(n,int) or n<=0 or (job['transition_alignment']==.5 and n%2):raise ValueError('Invalid transition frames')
        if cut*10160640000!=video[i-1]['timeline_start_ticks']:raise ValueError('Transition is not at incoming cut')
        if video[i-2]['timeline_end_ticks']!=video[i-1]['timeline_start_ticks']:raise ValueError('Transition requires adjoining clips')
        lo=cut-n*job['transition_alignment'];hi=cut+n*(1-job['transition_alignment'])
        if lo<0 or hi>job['frames'] or any(lo<b and hi>a for a,b in ranges):raise ValueError('Overlapping/out of range transition')
        ranges.append((lo,hi))
    if job.get('colors'):raise ValueError('This pass preserves Lumetri; use the separate reviewed color workflow')

def prepare(config_path, apply=False):
    config_path=config_path.resolve();cfg=json.loads(config_path.read_text(encoding='utf-8-sig'))
    source=Path(cfg['source_project']).resolve()
    data=Path(cfg['task_root']).resolve();projects=Path(cfg['premiere_project_dir']).resolve()
    for p in [data,projects]:
        if p.is_relative_to(ROOT):raise ValueError('Data/projects must be outside repository')
    if data==projects or projects.is_relative_to(data):raise ValueError('Keep Adobe projects in separate Video Project storage')
    run=cfg['run_id']
    if not run or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in run):raise ValueError('Unsafe run_id')
    records=data/'finish'/run;package=projects/'finish'/run;project=projects/(run+'_WORK.prproj')
    root=pp.load_premiere_project_root(source);ids=pp.build_project_object_id_lookup(root);uids=pp.build_project_object_uid_lookup(root)
    seq=pp.find_project_sequence_node(root,cfg['source_sequence'])
    if seq is None:raise ValueError('Source sequence missing')
    if pp.find_project_sequence_node(root,cfg['target_sequence']) is not None:raise ValueError('Target already exists')
    snapshot=[]
    for group in [0,1]:
        for ctx in _track_item_contexts(seq,group_index=group,id_lookup=ids,uid_lookup=uids,project_path=source):
            item=protected_property_snapshot(ctx,ids);item['group']=group;snapshot.append(item)
    snapshot.sort(key=lambda x:(x['group'],x['track_index'],x['timeline_start_ticks']))
    for item in snapshot:
        if not Path(item['source_path']).is_file():raise FileNotFoundError(item['source_path'])
    last=max((c['timeline_end_ticks'] for c in snapshot),default=0)
    if not last or last%10160640000:raise ValueError('Sequence is not frame aligned at 25 fps')
    job=dict(id=run,project=str(project),source_name=cfg['source_sequence'],name=cfg['target_sequence'],
             width=cfg['width'],height=cfg['height'],fps=cfg['fps'],video_track=cfg['video_track'],
             frames=last//10160640000,expected=snapshot,edits=cfg['edits'],transitions=cfg['transitions'],
             transition_alignment=cfg.get('transition_alignment',.5),export_preview=cfg.get('export_preview',False),
             preview=str(package/'native_preview.mp4'),preset=cfg.get('preset',''))
    validate(job)
    if job['export_preview'] and not Path(job['preset']).is_file():raise ValueError('Export preset missing')
    summary=dict(status='DRY_RUN_PASS',source_sha256=sha(source),clips=len(snapshot),frames=job['frames'],project=str(project),package=str(package),records=str(records))
    if not apply:return summary
    if package.exists() or records.exists() or project.exists():raise ValueError('Package/project exists: use a new run_id')
    package.mkdir(parents=True);(package/'snapshot').mkdir();projects.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,project)
    if sha(source)!=summary['source_sha256'] or sha(project)!=summary['source_sha256']:raise ValueError('Project copy mismatch')
    records.mkdir(parents=True)
    shutil.copy2(config_path,records/'config.json')
    (records/'plan.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
    template=(ROOT/'scripts/user_sequence_finish_native.jsx').read_text(encoding='utf-8')
    jsx=package/'APPLY_FINISH.jsx';jsx.write_text(template.replace('__JOB__',json.dumps(job,ensure_ascii=True,allow_nan=False)),encoding='utf-8')
    summary.update(status='USER_ACTION_REQUIRED',jsx=str(jsx),native_qa='NOT_RUN')
    (package/'preflight.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (records/'preflight.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true',help='Write copy and JSX after preflight; default is read-only dry-run')
    args=parser.parse_args();print(json.dumps(prepare(args.config,args.prepare),ensure_ascii=False,indent=2))
