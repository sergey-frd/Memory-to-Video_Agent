"""Run two task-specific original-photo queues with a permanent console log."""
import argparse
from datetime import datetime
from pathlib import Path
import sys
from run_grok_queue import load_profile,prepare,queue_lock,read,save_state,Settings

class Tee:
    def __init__(self,console,file):self.console=console;self.file=file
    def write(self,text):self.console.write(text);self.file.write(text);self.file.flush()
    def flush(self):self.console.flush();self.file.flush()

def main():
    p=argparse.ArgumentParser();p.add_argument('profile',type=Path);p.add_argument('--dry-run',action='store_true');p.add_argument('--prepare-only',action='store_true');a=p.parse_args()
    profile=a.profile.resolve();data=read(profile)
    # Use the same host-wide queue mutex as the existing launcher.
    with queue_lock(Settings().output_dir/'.grok-queue.lock'):
        for n,name in enumerate(data['projects'],1):
            _,spec=load_profile(profile,name)
            print(f'STAGE {n}/{len(data["projects"])} {name}',flush=True)
            plan=prepare(name,spec,Settings(),dry_run=a.dry_run)
            if plan and not a.dry_run and not a.prepare_only:
                from run_grok_prepared_queue import run
                run(plan)
                state=read(plan);state['queue_complete']=True;save_state(plan,state)

if __name__=='__main__':
    # The profile path is the first positional argument supplied by the launcher.
    if len(sys.argv)<2:raise SystemExit('Expected profile JSON path')
    folder=Path(sys.argv[1]).resolve().parent
    log=folder/(Path(sys.argv[1]).stem+'_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.log')
    with log.open('a',encoding='utf-8') as f:
        original_out,original_err=sys.stdout,sys.stderr
        sys.stdout=Tee(original_out,f);sys.stderr=Tee(original_err,f)
        try:main()
        finally:sys.stdout=original_out;sys.stderr=original_err
