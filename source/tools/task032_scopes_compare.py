"""Fixed TASK_032 helper. Requires an explicit local config and --execute."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(args):
    from pathlib import Path
    import json,sys,subprocess,math
    import cv2,numpy as np
    from PIL import Image,ImageDraw,ImageFont
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from tools.task032_pipeline import OUT,PREVIEW
    F=__import__("utils.video_frame_extract", fromlist=["resolve_ffmpeg_executable"]).resolve_ffmpeg_executable()
    m=json.loads((OUT/'TASK_032_MASTER.json').read_text(encoding='utf8'));c=cv2.VideoCapture(str(PREVIEW));font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',21)
    sc=OUT/'scopes';sc.mkdir(exist_ok=True)
    metrics=[]
    for o in m['extreme_color_operations']:
        fr=(o['output_in']+o['output_out']-1)//2;c.set(cv2.CAP_PROP_POS_FRAMES,fr);ok,bgr=c.read();assert ok
        rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB);lum=rgb@np.array([.2126,.7152,.0722])
        metrics.append({'source_clip_id':o['source_clip_id'],'frame':fr,'mean_luma_rgb_0_255':round(float(lum.mean()),2),'dark_luma_le2_percent':round(float((lum<=2).mean()*100),4),'white_luma_ge253_percent':round(float((lum>=253).mean()*100),4),'channel_ge254_percent':np.round((rgb>=254).mean(axis=(0,1))*100,4).tolist(),'channel_le1_percent':np.round((rgb<=1).mean(axis=(0,1))*100,4).tolist()})
    c.release()
    for sec in [1,39,55,72,79,92,120,135]:
        paths=[]
        filters=[('frame','null'),('luma','waveform=components=1:graticule=green:scale=ire'),('parade','format=gbrp,waveform=components=7:display=parade:graticule=green'),('vector','vectorscope=mode=color4:graticule=color:colorspace=601')]
        for name,vf in filters:
            p=sc/f'{sec:03d}_{name}.png'
            subprocess.run([F,'-v','error','-y','-ss',str(sec),'-i',str(PREVIEW),'-vf',vf,'-frames:v','1',str(p)],check=True);paths.append(p)
        sheet=Image.new('RGB',(1280,800),'#141414');d=ImageDraw.Draw(sheet)
        for j,p in enumerate(paths):
            im=Image.open(p).convert('RGB');im=im.resize((340,340) if j==3 else (620,340));x=(j%2)*640;y=(j//2)*400;sheet.paste(im,(x,y+40));d.text((x+8,y+8),['Кадр '+str(sec)+' с','Waveform Y / IRE','RGB Parade','Vectorscope Cb/Cr; линия кожи — ориентир'][j],fill='white',font=font)
            if j==3:
                # A reference skin vector from RGB(195,144,115), converted to Rec.601 Cb/Cr.
                cx=x+im.width/2;cy=y+40+im.height/2;dx=-.168736*195-.331264*144+.5*115;dy=.5*195-.418688*144-.081312*115
                scale=im.width/256*3.8;d.line((cx,cy,cx+dx*scale,cy-dy*scale),fill='#ffff80',width=1)
        sheet.save(sc/f'TASK_032_SCOPES_{sec:03d}.jpg',quality=92)
    (OUT/'TASK_032_SCOPE_MEASUREMENTS.json').write_text(json.dumps({'method':'Полный native MP4: стандартные FFmpeg Waveform Y, RGB Parade и Vectorscope; RGB экстремумы в середине каждого клипа. Skin line — ориентир, не автоматический вердикт о коже. Анализ limited-range smpte170m SD export; sequence остаётся 4K.','clips':metrics},ensure_ascii=False,indent=2),encoding='utf8')
    if args.scopes_only:return
    # Synchronized source/final comparison. Source positions come only from the contract.
    old=cv2.VideoCapture(str(OUT/'TASK_032_SOURCE_NATIVE_640_360.mp4'));new=cv2.VideoCapture(str(PREVIEW));segments=[]
    for cid in ['926','939','943','949','953','960','967']:
        op=next(o for o in m['edit_operations'] if o['clip_identity']['object_id']==cid)
        segments.append(op)
    print(json.dumps(segments[0],ensure_ascii=False),flush=True)
    # Video encoding uses raw frames read from native exports; no recreated Premiere effects.
    dest=OUT/f"TASK_032_COMPARISON_1280_400_R{m.get('revision',1):02d}.mp4"
    assert not dest.exists()
    p=subprocess.Popen([F,'-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','1280x400','-r','25','-i','-','-an','-c:v','libx264','-crf','19','-pix_fmt','yuv420p','-movflags','+faststart',str(dest)],stdin=subprocess.PIPE)
    for op in segments:
        start=op['clip_identity']['timeline_in'];outstart=op['output_in']
        for k in range(40):
            old.set(cv2.CAP_PROP_POS_FRAMES,start+k);new.set(cv2.CAP_PROP_POS_FRAMES,outstart+k);aok,a=old.read();bok,b=new.read();assert aok and bok
            sheet=Image.new('RGB',(1280,400),'#111111');sheet.paste(Image.fromarray(cv2.cvtColor(cv2.resize(a,(640,360)),cv2.COLOR_BGR2RGB)),(0,40));sheet.paste(Image.fromarray(cv2.cvtColor(b,cv2.COLOR_BGR2RGB)),(640,40));d=ImageDraw.Draw(sheet);d.text((12,7),'SF_26_Bd_Art_5 — ДО',fill='white',font=font);d.text((652,7),'TASK_032 — ПОСЛЕ',fill='white',font=font);p.stdin.write(sheet.tobytes())
    p.stdin.close();assert p.wait()==0;old.release();new.release();print('SCOPES / COMPARISON COMPLETE')


if __name__ == '__main__':
    from utils.premiere_art_runtime import tool_entry
    tool_entry('032', main)
