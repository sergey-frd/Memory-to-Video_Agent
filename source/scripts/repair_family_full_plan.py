"""Repair missing FULL decisions without replacing previous paid decisions."""
import argparse,copy
from pathlib import Path
from family_contract import config,read,task_path,sha,verify
from family_stages import ask,edit_plan,validate_edit_plan
from ingest import write_json
from classify import copy_verified
from tools.build_video_structure import obj

def run(task,cached):
 cfg=config(task);source=read(task/'pipeline/wide_plan.json');old=read(cached)
 for entry in read(task/'pipeline/state.json')['checkpoints'].values():verify(entry['artifacts'])
 ids=[c['id'] for c in source['clips']];rows=copy.deepcopy(old['decisions']);known=[d['id'] for d in rows]
 if len(set(known))!=len(known) or set(known)-set(ids):raise ValueError('Only missing decisions can be repaired')
 missing=[i for i in ids if i not in known]
 if not missing:raise ValueError('No missing decisions')
 output=task/'pipeline/full_plan.json'
 if output.exists():raise ValueError('FULL plan already exists')
 segment=obj({'in_seconds':{'type':'number','minimum':0},'out_seconds':{'type':'number','minimum':0},'speed':{'type':'integer','enum':[1,2,3]}})
 item=obj({'insert_before':{'type':'string','enum':known+['']},'reason':{'type':'string','minLength':1},'segments':{'type':'array','items':segment}})
 schema=obj({'decisions':obj({i:item for i in missing})})
 payload=dict(heroes=cfg['heroes'],concept=cfg['intent'],source_clips=source['clips'],existing_decisions=rows,missing=missing)
 answer=ask(task,cfg,'full_missing_repair_v1',payload,schema,'Repair only missing FULL decisions. Existing decisions stay unchanged. FULL is a rich edit of WIDE with no duration target. Supply source-relative timeline seconds aligned to 25fps, speed 1 for images; video acceleration only for evidence-based waiting/repetition. Empty segments means DROP with reason. Choose insert_before from existing IDs; empty means append. No invented biography.')
 for ident,d in answer['decisions'].items():
  row=dict(id=ident,reason=d['reason'],segments=d['segments']);before=d['insert_before']
  index=next((i for i,r in enumerate(rows) if r['id']==before),len(rows));rows.insert(index,row)
 normalized=[];lookup={c['id']:c for c in source['clips']}
 for row in rows:
  c=lookup[row['id']]
  for seg in row['segments']:
   if seg['out_seconds']<=c['duration_seconds']+1e-7:continue
   if c['kind']!='video' or c.get('speed',1)!=1 or abs(seg['in_seconds']-c['source_in_seconds'])>1e-7 or abs(seg['out_seconds']-c['source_out_seconds'])>1e-7:raise ValueError('Ambiguous source bounds; manual diagnosis required: '+row['id'])
   normalized.append(dict(id=row['id'],original=dict(seg),policy='exact full source bounds to parent-relative range'))
   seg.update(in_seconds=0,out_seconds=c['duration_seconds'])
 wire=dict(decisions={d['id']:dict(order=i,reason=d['reason'],segments=d['segments']) for i,d in enumerate(rows)},warnings=old.get('warnings',[]))
 plan=edit_plan(task,cfg,source,'FULL',decision_override=wire)
 validate_edit_plan(plan,source,task,cfg,'FULL')
 report=dict(original_response=str(cached),original_sha256=sha(cached),source_sha256=sha(task/'pipeline/wide_plan.json'),repaired_ids=missing,preserved_editorial_decisions=len(known),coordinate_normalizations=normalized,decisions=wire)
 write_json(task/'pipeline/full_decision_repair.json',report);write_json(output,plan)
 remote=Path(cfg['classify']['permanent_project_dir'])/'pipeline'/task.name/'checkpoints/FULL_PLAN'
 copy_verified(output,remote/output.name);copy_verified(task/'pipeline/full_decision_repair.json',remote/'full_decision_repair.json')
 print('FULL PLAN VERIFIED:',len(plan['clips']),'clips;',plan['duration_seconds'],'seconds; repaired',missing)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('task');p.add_argument('cached_response');a=p.parse_args();run(task_path(a.task),Path(a.cached_response))
