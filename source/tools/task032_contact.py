"""Fixed TASK_032 helper. Requires an explicit local config and --execute."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(args):
    """Create unaltered source thumbnails for visual audit, not a sequence render."""
    import json
    from pathlib import Path
    from PIL import Image, ImageOps, ImageDraw, ImageFont
    import cv2
    from tools.task032_preflight import OUT as ROOT, NAME
    rows = json.loads((ROOT/'TASK_032_TIMELINE_MANIFEST.json').read_text(encoding='utf8'))[NAME]
    rows = [r for r in rows if r['path'] and r['group']=='video']
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 16)
    for page in range((len(rows)+14)//15):
        sheet = Image.new('RGB', (5*384,3*280), '#181818')
        d=ImageDraw.Draw(sheet)
        for j,r in enumerate(rows[page*15:(page+1)*15]):
            path=Path(r['path'])
            if path.suffix.lower() in ['.mp4','.mov']:
                cap=cv2.VideoCapture(str(path)); cap.set(cv2.CAP_PROP_POS_MSEC,(r['source_in']+(r['out']-r['in'])/2)/25*1000)
                ok,frame=cap.read(); cap.release()
                im=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)) if ok else Image.new('RGB',(320,180),'#660000')
            else:
                im=Image.open(path).convert('RGB')
            im.thumbnail((376,216))
            x=(j%5)*384; y=(j//5)*280
            sheet.paste(im,(x+(384-im.width)//2,y))
            d.text((x+4,y+218),f"ID {r['identity']['ObjectID']} | {r['in']/25:.2f}–{r['out']/25:.2f} | V{r['track']+1}",font=font,fill='white')
            d.text((x+4,y+240),r['name'][:37],font=font,fill='#dddddd')
        sheet.save(ROOT/f'TASK_032_SOURCE_CONTACT_{page+1}.jpg',quality=92)
    print('pages',page+1)


if __name__ == '__main__':
    from utils.premiere_art_runtime import tool_entry
    tool_entry('032', main)
