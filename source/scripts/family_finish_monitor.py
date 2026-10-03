"""Read-only FINISH heartbeat. Native output existence is not a QA pass."""
import argparse
import time
from pathlib import Path
from family_contract import ROOT, read

def watch(task, snapshot=False):
    status=read(task/'visual_finish/status.json');base=Path(status['jsx']).parent
    job=read(status['plan']);started=time.monotonic()
    while True:
        sf=base/'native_status.txt';lf=base/'native_progress.log'
        native=sf.read_text(encoding='utf-8-sig') if sf.exists() else 'WAITING_USER_RUN'
        lines=lf.read_text(encoding='utf-8-sig',errors='replace').splitlines() if lf.exists() else []
        last=lines[-1] if lines else 'No native execution observed'
        success=[s for s in lines if 'EXPORT ' in s]
        n=sum(Path(v['review']).exists() for v in job['versions'])
        print(f'STAGE=VISUAL_FINISH current={native} reviews={n}/{len(job["versions"])} monitor_elapsed={int(time.monotonic()-started)}s last_success={success[-1] if success else "STRUCTURE_REVIEW"} errors={sum("FAILED" in s for s in lines)} heartbeat={time.strftime("%H:%M:%S")}\n{last}',flush=True)
        if snapshot or native.startswith('FAILED') or native=='EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA': return
        time.sleep(5)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('task');p.add_argument('--snapshot',action='store_true');a=p.parse_args();watch(ROOT/'tasks'/a.task,a.snapshot)
