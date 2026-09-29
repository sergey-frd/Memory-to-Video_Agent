"""Read-only editorial sampling of the complete FULL COLOR timeline."""
import sys,json,subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from utils.video_frame_extract import resolve_ffmpeg_executable
root=Path(__file__).resolve().parents[1];out=root/'tasks/BM26/main/assessment';out.mkdir(parents=True,exist_ok=True)
read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
s=read(root/'tasks/BM26/color/status.json');f=read(root/'tasks/BM26/full_master/status.json');plan=read(f['plan']);ff=resolve_ffmpeg_executable();rows=[]
for c in plan['clips']:
 for k,fraction in enumerate([.15,.5,.85] if c['kind']=='video' else [.5]):
  t=(c['timeline_start_frame']+c['frames']*fraction)/25;rows.append(dict(id=c['id'],time=t,source=c['source_in_seconds']+c['duration_seconds']*fraction*c['speed'],file=str(out/(c['id']+'_'+str(k)+'.jpg'))))
def sample(r):
 p=Path(r['file'])
 if not p.exists():subprocess.run([ff,'-v','error','-nostdin','-ss',str(r['time']),'-i',s['reviews']['FULL'],'-frames:v','1','-vf','scale=400:225','-pix_fmt','yuvj420p','-q:v','2',str(p)],capture_output=True,check=True)
 return r
with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(sample,rows))
for start in range(0,len(rows),24):
 group=rows[start:start+24];im=Image.new('RGB',(1600,6*250),'#202020');d=ImageDraw.Draw(im)
 for k,r in enumerate(group):
  x=k%4*400;y=k//4*250;im.paste(Image.open(r['file']),(x,y));d.text((x+5,y+227),r['id']+' src '+str(round(r['source'],2))+' FULL '+str(round(r['time'],2)),fill='white')
 im.save(out/('sheet_'+str(start//24)+'.jpg'),quality=90)
(out/'samples.json').write_text(json.dumps(rows,indent=2),encoding='utf-8');print(len(rows),'samples',len(plan['clips']),'source segments')
