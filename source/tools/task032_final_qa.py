"""Fixed TASK_032 helper. Requires an explicit local config and --execute."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(args):
    from pathlib import Path
    import json,subprocess,re,sys
    import cv2,numpy as np
    from PIL import Image,ImageDraw,ImageFont
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from tools.task032_pipeline import SOURCE,DEST,PREVIEW,OUT,SHA,sha,FT,NAME,TARGET,items,load_premiere_project_root,_validate_all_refs,comps,build_project_object_id_lookup
    F=__import__("utils.video_frame_extract", fromlist=["resolve_ffmpeg_executable"]).resolve_ffmpeg_executable()
    m=json.loads((OUT/'TASK_032_MASTER.json').read_text(encoding='utf8'))
    if args.preview is not None:PREVIEW=args.preview.resolve()
    c=cv2.VideoCapture(str(PREVIEW));info={'width':int(c.get(3)),'height':int(c.get(4)),'fps':c.get(5),'frames':int(c.get(7))};info['duration']=info['frames']/info['fps']
    print(info,flush=True)
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',18)
    for page in range(int(info['duration']//25)+1):
        s=Image.new('RGB',(1600,1010),'#111111');d=ImageDraw.Draw(s)
        for k in range(25):
            sec=page*25+k
            if sec>=info['duration']:continue
            c.set(cv2.CAP_PROP_POS_MSEC,sec*1000);ok,a=c.read()
            if ok:s.paste(Image.fromarray(cv2.cvtColor(a,cv2.COLOR_BGR2RGB)).resize((320,180)),(k%5*320,k//5*202));d.text((k%5*320+4,k//5*202+182),str(sec)+'s',font=font,fill='white')
        s.save(OUT/f'TASK_032_FINAL_REVIEW_{page+1}.jpg',quality=90)
    # Every motion endpoint is inspected at the actual rendered sequence timing.
    motions=[]
    for page in range((len(m['strong_animation_operations'])+7)//8):
        s=Image.new('RGB',(960,8*206),'#111111');d=ImageDraw.Draw(s)
        for j,o in enumerate(m['strong_animation_operations'][page*8:(page+1)*8]):
            ims=[]
            for q,fr in enumerate([o['output_in'],(o['output_in']+o['output_out']-1)//2,o['output_out']-1]):
                c.set(cv2.CAP_PROP_POS_FRAMES,fr);ok,a=c.read()
                if ok:
                    ims.append(a);s.paste(Image.fromarray(cv2.cvtColor(a,cv2.COLOR_BGR2RGB)).resize((320,180)),(q*320,j*206));d.text((q*320+3,j*206+182),f"ID {o['source_clip_id']} / f{fr}",font=font,fill='white')
            if len(ims)==3:motions.append({'source_clip_id':o['source_clip_id'],'rendered_start_end_mean_abs_difference':round(float(np.abs(ims[0].astype(float)-ims[2].astype(float)).mean()),3)})
        s.save(OUT/f'TASK_032_MOTION_ENDPOINTS_{page+1}.jpg',quality=90)
    c.release()
    r=subprocess.run([F,'-hide_banner','-i',str(PREVIEW),'-af','loudnorm=I=-17:TP=-1:LRA=22:print_format=json','-vf','blackdetect=d=0.04:pix_th=0.10:pic_th=0.98','-f','null','NUL'],capture_output=True,text=True,encoding='utf8',errors='replace')
    (OUT/'TASK_032_FINAL_DECODE_AUDIO_LOG.txt').write_text(r.stderr,encoding='utf8')
    print(r.stderr[-1800:],flush=True)
    loudness=json.loads(re.findall(r'\{\s*"input_i".*?\}',r.stderr,re.S)[-1]);black=re.findall(r'black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)',r.stderr)
    root=load_premiere_project_root(DEST);ids=build_project_object_id_lookup(root);refs=_validate_all_refs(root);video=items(root,TARGET);aud=items(root,TARGET,1)
    fx=[]
    for i in video:
        names=[co.findtext('MatchName') for _,co in comps(i,ids)]
        fx.append({'name':i.name,'track':i.track_index,'in':i.start//FT,'out':i.end//FT,'effects':names})
    qa={'native_export':info,'decode_return_code':r.returncode,'loudness':loudness,'black_ranges':black,'source_unchanged':sha(SOURCE)==SHA,'output_sha256':sha(DEST),'preview_sha256':sha(PREVIEW),'master_sha256':sha(OUT/'TASK_032_MASTER.json'),'references':refs,'effects':fx,'motion_endpoint_differences':motions,
     'checks':{'size':info['width']==640 and info['height']==360,'duration':info['frames']==3480 and info['fps']==25,'no_black':not black,'decode':r.returncode==0,'lufs_target':-18.2<=float(loudness['input_i'])<=-16,'peak':float(loudness['input_tp'])<=-1,'source_unchanged':sha(SOURCE)==SHA}}
    (OUT/'TASK_032_FINAL_QA_MEASUREMENTS.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(qa['checks']),flush=True)


if __name__ == '__main__':
    from utils.premiere_art_runtime import tool_entry
    tool_entry('032', main)
