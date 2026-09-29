"""Family pipeline coordinator. No stage algorithms and no Premiere GUI control."""
import argparse,json,os,queue,re,subprocess,sys,threading,time,traceback
from datetime import datetime,timezone
from pathlib import Path
from family_contract import ROOT,STAGES,config,read,sha,task_path,artifacts,verify
from ingest import write_json

def now():return datetime.now(timezone.utc).isoformat()
def command(task,flag='--resume'):return f'"{ROOT / "scripts/run_all.bat"}" {task.name} {flag}'

def watch_native(task):
 state=read(task/'pipeline/state.json');handoff=state.get('handoff')
 if not handoff:raise ValueError('No pending Premiere handoff to monitor')
 folder=Path(handoff['jsx']).parent;log=folder/'native_progress.log';status=folder/'native_status.txt';offset=0;start=time.monotonic()
 print('Monitoring files only; run the prepared JSX in Premiere. Ctrl+C stops this monitor.',flush=True)
 while True:
  if log.exists():
   text=log.read_text(encoding='utf-8',errors='replace')
   if len(text)<offset:offset=0
   if len(text)>offset:print(text[offset:],end='',flush=True);offset=len(text)
  actual=status.read_text(encoding='utf-8',errors='replace').strip() if status.exists() else 'WAITING FOR JSX'
  if actual.startswith('FAILED'):return {'status':'FAILED','native_status':actual}
  if actual=='EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA':
   print('Native export reported; verification still required: '+command(task),flush=True);return {'status':'USER_ACTION_REQUIRED','native_status':actual}
  print(f'HEARTBEAT {int(time.monotonic()-start)}s status={actual}; does not confirm native completion.',flush=True);time.sleep(5)

def child(args,log,stage,progress,timeout=1800):
 """Pump child output while showing heartbeats even when the child is silent."""
 env=dict(os.environ,PYTHONIOENCODING='utf-8',FAMILY_PIPELINE_AUTO='1')
 proc=subprocess.Popen([sys.executable,'-u',*args],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',env=env)
 q=queue.Queue()
 def pump():
  for line in proc.stdout:q.put(line)
  q.put(None)
 threading.Thread(target=pump,daemon=True).start();start=time.monotonic();last_line=start;last='starting';errors=0
 try:
  with log.open('a',encoding='utf-8') as f:
   while True:
    try:line=q.get(timeout=5)
    except queue.Empty:line=''
    if line is None:break
    if line:
     last=line.strip();last_line=time.monotonic();errors+=int('FAILED' in line);print(line,end='',flush=True);f.write(line);f.flush()
    else:print(f'[{stage}] HEARTBEAT elapsed={int(time.monotonic()-start)}s current={last} errors={errors}',flush=True)
    progress(last,errors,int(time.monotonic()-start))
    if time.monotonic()-last_line>timeout:raise TimeoutError('Stage output stalled')
   rc=proc.wait()
   if rc:raise RuntimeError(f'{stage} exit={rc}; see {log}')
 except BaseException:
  proc.terminate();proc.wait(timeout=30);raise

def run(task,mode,dry=False,start_stage=None,executor=None):
 path=task/'pipeline';sp=path/'state.json'
 if mode=='watch':return watch_native(task)
 if mode=='status':
  frozen=(task/'task.json').exists() and read(task/'task.json').get('family_pipeline',{}).get('run_enabled') is False
  state=read(sp) if sp.exists() else {'task':task.name,'status':'FROZEN_REFERENCE' if frozen else 'NOT_INITIALIZED','next':'Historical experiment; no rerun' if frozen else command(task,'--dry-run')}
  print(json.dumps(state,ensure_ascii=False,indent=2));return state
 cfg=config(task);fingerprint=sha(task/'task.json')
 if dry:
  from family_stages import preflight
  preflight(task,cfg)
  if sp.exists():
   state=read(sp)
   if state['config_sha256']!=fingerprint:raise ValueError('Config changed since checkpoint')
   for entry in state['checkpoints'].values():verify(entry['artifacts'])
  result={'status':'DRY_RUN_PASS','stages':STAGES,'paid_operations':0,'premiere_started':False,'init_accepted':(path/'init_accepted.json').exists()}
  print(json.dumps(result,indent=2));return result
 if not cfg['family_pipeline']['run_enabled']:raise ValueError('Task is frozen/reference-only: run_enabled=false')
 from family_stages import preflight,stage_outputs,resume_handoff
 preflight(task,cfg)
 if mode=='accept-init':
  path.mkdir(parents=True,exist_ok=True);write_json(path/'init_accepted.json',dict(config_sha256=fingerprint,source_sha256=sha(cfg['paths']['premiere_project']),accepted_at=now()))
  print('INIT_ACCEPTED; AUTO NOT STARTED\n'+command(task,'--auto'));return
 approval=read(path/'init_accepted.json')
 if approval['config_sha256']!=fingerprint or approval['source_sha256']!=sha(cfg['paths']['premiere_project']):raise ValueError('INIT approval is stale')
 path.mkdir(parents=True,exist_ok=True);lock=path/'run.lock'
 state=read(sp) if sp.exists() else dict(task=task.name,config_sha256=fingerprint,checkpoints={},status='READY',last_success=None)
 with lock.open('x',encoding='utf-8') as f:f.write(json.dumps({'pid':os.getpid(),'started':now()}))
 stage='VALIDATE';remote=Path(cfg['classify']['permanent_project_dir'])/'pipeline'/task.name
 def save():
  state['updated']=now();write_json(sp,state);write_json(remote/'state.json',state)
  # Stage modules keep their historical reports; this is the authoritative task view.
  task_state=task/'state.json'
  if task_state.exists():
   summary=read(task_state);summary.update(stage=state.get('stage','INIT'),status=state['status'],execution_state=state['status'],pipeline_state=str(sp),next_stage=state.get('resume_command',command(task)))
   if 'CLASSIFY' in state['checkpoints']:summary.setdefault('stages',{})['CLASSIFY']='COMPLETE'
   write_json(task_state,summary)
 def preserve_logs():
  from classify import copy_verified
  for log in path.glob('*.log'):copy_verified(log,remote/'logs'/log.name)
 def event(text):
  print(text,flush=True)
  with (path/'master.log').open('a',encoding='utf-8') as f:f.write(now()+' '+text+'\n')
 def progress(last,errors,elapsed):
  match=re.search(r'(\d+)/(\d+)',last)
  state['progress']=dict(stage=stage,current_item=last,n=int(match[1]) if match else None,total=int(match[2]) if match else None,elapsed_seconds=elapsed,error_count=errors,last_success=state['last_success'],heartbeat=now());save()
 try:
  if state['config_sha256']!=fingerprint:raise ValueError('Config changed; do not invalidate successful paid stages')
  if start_stage and any(s not in state['checkpoints'] for s in STAGES[:STAGES.index(start_stage)]):raise ValueError('--from cannot bypass unvalidated dependencies')
  for stage in STAGES:
   entry=state['checkpoints'].get(stage)
   if entry:
    verify(entry['artifacts']);stage_outputs(task,cfg,stage);event('[SKIP] '+stage+' checkpoint verified');continue
   if stage=='STRUCTURE_REVIEW':
    state.update(status='USER_ACTION_REQUIRED',stage=stage,user_action='Review MAIN and SHORT structures before VISUAL FINISH; no automatic COLOR or music.',resume_command=command(task));save();event('USER_ACTION_REQUIRED: STRUCTURE_REVIEW');return state
   if stage.endswith('PREMIERE_HANDOFF'):
    pending=state.get('handoff')
    if pending and pending['stage']==stage:
     if mode!='resume':state['status']='USER_ACTION_REQUIRED';save();event('USER_ACTION_REQUIRED: '+str(pending));return state
     outputs=resume_handoff(task,cfg,pending)
     state['checkpoints'][stage]=dict(artifacts=artifacts(outputs),completed=now());state.update(last_success=stage,handoff=None);save();continue
   state.update(status='RUNNING',stage=stage);save();event('[START] '+stage+' last_success='+str(state['last_success']))
   args=[str(ROOT/'scripts/family_stages.py'),task.name,stage]
   if executor:executor(stage,args)
   else:child(args,path/(stage+'.log'),stage,progress)
   outputs=stage_outputs(task,cfg,stage);preserve_logs()
   if stage.endswith('PREMIERE_HANDOFF'):
    handoff=read(outputs[0]);state.update(status='USER_ACTION_REQUIRED',handoff=handoff,stage=stage);save();event(json.dumps(handoff,ensure_ascii=False,indent=2));return state
   state['checkpoints'][stage]=dict(artifacts=artifacts(outputs),completed=now());state['last_success']=stage;save()
   event('[PASS] '+stage+' → CONTINUE')
  return state
 except (Exception,KeyboardInterrupt) as exc:
  failure=dict(timestamp=now(),stage=stage,error=f'{type(exc).__name__}: {exc}',expected='Validated current-stage outputs / unchanged checkpoints',actual=str(exc),paths={'task':str(task),'log':str(path/(stage+'.log'))},last_success=state['last_success'],resume_command=command(task),traceback=traceback.format_exc())
  diagnostic=path/('failure_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json');write_json(diagnostic,failure);write_json(remote/diagnostic.name,failure)
  state.update(status='FAILED',failed_stage=stage,diagnostic=str(diagnostic),resume_command=command(task));save();event('FAILED '+json.dumps(failure,ensure_ascii=False));preserve_logs();return state
 finally:
  lock.unlink(missing_ok=True)

def main():
 for s in [sys.stdout,sys.stderr]:
  if hasattr(s,'reconfigure'):s.reconfigure(encoding='utf-8',errors='replace')
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('task');g=p.add_mutually_exclusive_group();g.add_argument('--auto',action='store_true');g.add_argument('--resume',action='store_true');g.add_argument('--status',action='store_true');g.add_argument('--watch',action='store_true');g.add_argument('--accept-init',action='store_true');p.add_argument('--dry-run',action='store_true');p.add_argument('--from',dest='start_stage',choices=STAGES);a=p.parse_args()
 try:
  mode='watch' if a.watch else 'accept-init' if a.accept_init else 'resume' if a.resume else 'auto' if a.auto or a.dry_run else 'status'
  result=run(task_path(a.task),mode,a.dry_run,a.start_stage);return 1 if result and result.get('status')=='FAILED' else 0
 except Exception as exc:print(f'FAILED PREFLIGHT: {exc}\nNo stages started. Fix INIT/config or inspect existing run.lock.');return 1
if __name__=='__main__':raise SystemExit(main())
