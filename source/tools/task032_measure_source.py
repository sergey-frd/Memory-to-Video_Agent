"""Fixed TASK_032 helper. Requires an explicit local config and --execute."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(args):
    from pathlib import Path
    import subprocess,json,re
    import cv2
    from PIL import Image,ImageDraw,ImageFont
    from tools.task032_preflight import OUT as ROOT, NAME
    src=ROOT/'TASK_032_SOURCE_NATIVE_640_360.mp4'
    ffmpeg=__import__("utils.video_frame_extract", fromlist=["resolve_ffmpeg_executable"]).resolve_ffmpeg_executable()
    cap=cv2.VideoCapture(str(src))
    info={'width':cap.get(cv2.CAP_PROP_FRAME_WIDTH),'height':cap.get(cv2.CAP_PROP_FRAME_HEIGHT),'fps':cap.get(cv2.CAP_PROP_FPS),'frames':cap.get(cv2.CAP_PROP_FRAME_COUNT),'source':'Экспорт Premiere Pro 26.3.2, штатный пресет YouTube 720p'}
    info['duration']=info['frames']/info['fps']
    (ROOT/'TASK_032_SOURCE_NATIVE_PROBE.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf8')
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',18)
    for page in range(7):
        sheet=Image.new('RGB',(1600,5*202),'#111111'); d=ImageDraw.Draw(sheet)
        for k in range(25):
            second=page*25+k
            if second>=info['duration']:continue
            cap.set(cv2.CAP_PROP_POS_MSEC,second*1000)
            ok,frame=cap.read()
            if ok:
                im=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)).resize((320,180))
                x=k%5*320;y=k//5*202;sheet.paste(im,(x,y));d.text((x+6,y+182),f'{second:03d}s',font=font,fill='white')
        sheet.save(ROOT/f'TASK_032_NATIVE_REVIEW_{page+1}.jpg',quality=88)
    cap.release()
    print(json.dumps(info))
    r=subprocess.run([str(ffmpeg),'-hide_banner','-i',str(src),'-af','loudnorm=I=-17:TP=-1:LRA=11:print_format=json','-vf','blackdetect=d=0.04:pix_th=0.10:pic_th=0.98','-f','null','NUL'],capture_output=True,text=True,encoding='utf8',errors='replace')
    (ROOT/'TASK_032_SOURCE_DECODE_AUDIO_LOG.txt').write_text(r.stderr,encoding='utf8')
    print(r.stderr[-2000:])


if __name__ == '__main__':
    from utils.premiere_art_runtime import tool_entry
    tool_entry('032', main)
