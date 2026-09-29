"""Create a read-only comparison from completed ART_2 files; no generation."""
import html
from pathlib import Path
from PIL import Image,ImageOps,ImageDraw
from family_contract import read,task_path,verify

def render(task):
 work=task/'art_2';state=read(work/'generation_state.json');plan=read(work/'ART_2_REGENERATE.json')
 entries=[e for e in plan['entries'] if e['decision']=='REGENERATE_PORTRAIT']
 review=work/'review';review.mkdir(exist_ok=True)
 rows=['<!doctype html><html lang="ru"><meta charset="utf-8"><title>ART_2 results</title><style>body{font:17px system-ui;margin:24px;background:#eee}table{width:100%;table-layout:fixed;background:white}td{vertical-align:top;padding:12px}img{width:100%;height:480px;object-fit:contain}h2{margin-top:36px}</style><h1>BM26 — ART_2</h1><p>Original / ART v1 / ART v2. Canvas is preserved; no stretching. Four approved generations. Montage and v1 unchanged.</p>']
 for e in entries:
  r=state['results'][e['art_id']]
  if r['status']!='COMPLETE':raise ValueError('Generation incomplete')
  verify(r['artifacts'])
  paths=[e['source_path'],e['old_art_path'],r['new_art_path']]
  labels=['ORIGINAL (EXIF normalized)','ART v1','ART v2 — native canvas']
  sheet=Image.new('RGB',(1500,800),'#eeeeee');draw=ImageDraw.Draw(sheet)
  rows.append('<h2>'+e['art_id']+' — '+e['art_type']+'</h2><table><tr>')
  for i,(name,path) in enumerate(zip(labels,paths)):
   with Image.open(path) as im:
    im=ImageOps.exif_transpose(im).convert('RGB');size=im.size;im.thumbnail((480,730));sheet.paste(im,(i*500+(500-im.width)//2,50+(730-im.height)//2))
   draw.text((i*500+12,16),e['art_id']+' '+name+' '+str(size),fill='black')
   uri=html.escape(Path(path).as_uri(),quote=True)
   rows.append('<td>'+name+' '+str(size)+'<br><a href="'+uri+'"><img src="'+uri+'"></a></td>')
  rows.append('</tr></table>');sheet.save(review/(e['art_id']+'_comparison.jpg'),quality=92)
 (work/'ART_2_RESULTS.html').write_text('\n'.join(rows)+'</html>',encoding='utf-8')
 print('Created review for '+', '.join(e['art_id'] for e in entries))
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('task');a=p.parse_args();render(task_path(a.task))
