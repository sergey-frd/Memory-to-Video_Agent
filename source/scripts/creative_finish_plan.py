"""Agent review packet and unified two-pass plan contract; no AI/Adobe execution."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils.video_frame_extract import extract_video_frames

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def clips(structure):
    rows=structure['clips'];seen=set();cursor=0
    fps=structure.get('fps',25)
    if isinstance(fps,bool) or not isinstance(fps,(int,float)) or not math.isfinite(fps) or fps<=0:raise ValueError('Invalid fps')
    if not rows:raise ValueError('Empty structure')
    for c in rows:
        if not isinstance(c['id'],str) or not c['id'] or c['id'] in seen:raise ValueError('Duplicate/invalid scene ID')
        seen.add(c['id'])
        if not isinstance(c['frames'],int) or isinstance(c['frames'],bool) or c['frames']<1:raise ValueError('Invalid duration')
        if c.get('timeline_start_frame',cursor)!=cursor:raise ValueError('Structure must be contiguous')
        if c['kind'] not in ['image','video']:raise ValueError('Unsupported media kind')
        cursor+=c['frames']
    return rows

def packet(structure_path,output):
    structure_path=structure_path.resolve();output=output.resolve()
    if output.is_relative_to(ROOT):raise ValueError('Task data must be outside repository')
    structure=read(structure_path);rows=clips(structure)
    if output.exists():raise ValueError('Use a fresh packet directory')
    for c in rows:
        if not Path(c['path']).is_file():raise FileNotFoundError(c['path'])
    output.mkdir(parents=True);scenes=[];cursor=0;fps=structure.get('fps',25)
    for index,c in enumerate(rows):
        path=Path(c['path']).resolve()
        if c['kind']=='video':
            start=c.get('source_in_seconds',0);rate=c.get('speed',1)
            duration=(c['frames']-1)/fps*rate
            samples=extract_video_frames(path,output_dir=output/'evidence',
                timestamps_sec=[start,start+duration/2,start+duration],prefix=f'scene_{index:04}')
            evidence=[dict(path=str(p),sha256=sha(p),source_seconds=t) for t,p in samples]
        else:evidence=[dict(path=str(path),sha256=sha(path))]
        scenes.append(dict(id=c['id'],timeline_start_frame=cursor,frames=c['frames'],kind=c['kind'],
            source=str(path),source_sha256=sha(path),evidence=evidence,
            observation=None,motion=None,color=None))
        cursor+=c['frames']
    from scripts.film_critic_review import policy
    plan=dict(schema_version=1,status='AGENT_REVIEW_REQUIRED',task_id=structure['task_id'],critic_review=policy(structure.get('critic_review')),
        branch=structure.get('branch','USER'),structure_path=str(structure_path),structure_sha256=sha(structure_path),
        audio_policy='ignore',music_bpm=None,scenes=scenes,
        boundaries=[dict(left=a['id'],right=b['id'],decision=None,left_exit=None,right_entry=None,reason=None)
                    for a,b in zip(scenes,scenes[1:])],pulse_peaks=[],second_pass=None)
    save(output/'creative_finish_plan.json',plan)
    (output/'AGENT_REQUEST_RU.md').write_text('''# Единое визуальное доведение

Открой реальные изображения evidence каждой сцены. Для видео просмотри также
сам фрагмент, если три пробы не объясняют движение. Не суди только по именам файлов.
Проход 1: последовательные пары сцен. Вместе реши кадрирование, Motion/пульс,
цвет и переход с учётом выхода предыдущей и входа следующей сцены. Заполни каждую
сцену и границу, включая осознанные hold/preserve/cut. Сохрани порядок и длительности.
Проход 2: весь фильм. Проверь ритм, повторения, безопасное положение лиц/текста,
цветовую связность, начало/кульминацию/финал и исправь тот же единый план.
Аудио не анализировать. Музыку не генерировать; BPM неизвестен. pulse_peaks —
кандидаты для будущей ручной подгонки музыки. Без native review не заявляй готовность
для клиента. Формат контракта — docs/CREATIVE_FINISH_RU.md.
''',encoding='utf-8')
    return dict(status=plan['status'],plan=str(output/'creative_finish_plan.json'),scenes=len(scenes),boundaries=len(plan['boundaries']))

def validate(plan,structure,structure_hash):
    rows=clips(structure);ids=[c['id'] for c in rows]
    if plan['schema_version']!=1 or plan['task_id']!=structure['task_id'] or plan['structure_sha256']!=structure_hash or plan['branch']!=structure.get('branch','USER'):raise ValueError('Stale/mismatched structure')
    if plan['status']!='PLAN_READY_NATIVE_PENDING':raise ValueError('Agent review not complete')
    if plan['audio_policy']!='ignore' or plan['music_bpm'] is not None:raise ValueError('Audio/music is outside this pass')
    if [c['id'] for c in plan['scenes']]!=ids:raise ValueError('All scenes must be reviewed in structure order')
    def reason(s):
        if not isinstance(s,str) or not s.strip():raise ValueError('Visual observation/reason required')
    def number(v):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ValueError('Invalid number')
    cursor=0
    for item,source in zip(plan['scenes'],rows):
        if item['frames']!=source['frames'] or item['timeline_start_frame']!=cursor or item['kind']!=source['kind']:raise ValueError('Montage changed')
        if Path(item['source']).resolve()!=Path(source['path']).resolve():raise ValueError('Source changed')
        cursor+=source['frames'];reason(item['observation'])
        m=item['motion'];reason(m['reason'])
        if m['mode'] not in ['hold','pulse','pan','zoom']:raise ValueError('Unsupported motion')
        if m['mode']!='hold':
            keys=m['keys']
            if len(keys)<2 or keys[0][0]!=0 or keys[-1][0]!=1:raise ValueError('Motion must span scene')
            prev=-1
            for u,z,x,y in keys:
                for v in [u,z,x,y]:number(v)
                if not prev<u<=1 or z<=0 or not 0<=x<=1 or not 0<=y<=1:raise ValueError('Invalid motion key')
                prev=u
            if m['mode']=='pulse' and (len(keys)<3 or keys[0][1:]!=keys[-1][1:] or all(k[1:]==keys[0][1:] for k in keys)):raise ValueError('Pulse must move and return to its starting composition')
            if source['frames']<3 and m['mode']=='pulse':raise ValueError('Pulse too short')
        color=item['color'];reason(color['reason'])
        if color['mode'] not in ['preserve','lumetri']:raise ValueError('Unsupported color')
        values=color['values']
        if not isinstance(values,dict) or (color['mode']=='preserve' and values):raise ValueError('Invalid color values')
        allowed={'Exposure','Temperature','Tint','Contrast','Highlights','Shadows','Whites','Blacks','Saturation'}
        if set(values)-allowed:raise ValueError('Unsupported Lumetri parameter')
        for v in values.values():number(v)
    pairs=list(zip(ids,ids[1:]))
    if [(b['left'],b['right']) for b in plan['boundaries']]!=pairs:raise ValueError('Every adjacent pair needs one decision')
    for b,a,c in zip(plan['boundaries'],rows,rows[1:]):
        for key in ['reason','left_exit','right_entry']:reason(b[key])
        if b['decision'] not in ['cut','cross_dissolve']:raise ValueError('Unsupported transition')
        n=b['frames']
        if not isinstance(n,int) or isinstance(n,bool) or n<0 or n>=min(a['frames'],c['frames']):raise ValueError('Transition too long')
        if (b['decision']=='cut')!=(n==0):raise ValueError('Cut/dissolve duration mismatch')
        if b['decision']=='cross_dissolve' and b.get('handle_check')!='REQUIRED':raise ValueError('Native/source handles must be checked')
    starts={s['id']:s['timeline_start_frame'] for s in plan['scenes']}
    lengths={c['id']:c['frames'] for c in rows}
    for peak in plan['pulse_peaks']:
        ident=peak['scene_id'];frame=peak['frame'];reason(peak['purpose'])
        if ident not in starts or not isinstance(frame,int) or not starts[ident]<=frame<starts[ident]+lengths[ident]:raise ValueError('Marker outside scene')
        if next(s for s in plan['scenes'] if s['id']==ident)['motion']['mode']!='pulse':raise ValueError('Pulse marker requires pulse')
    review=plan['second_pass']
    if not isinstance(review,dict) or review['status']!='ACCEPTED' or review['reviewed_scene_ids']!=ids or review['reviewed_boundaries']!=[list(p) for p in pairs] or review['issues']:raise ValueError('Global review incomplete')
    for key in ['rhythm','composition','color_continuity']:reason(review[key])
    if plan['status']!='PLAN_READY_NATIVE_PENDING':raise ValueError('Plan status required; native QA not completed')
    return dict(status='PLAN_CONTRACT_PASS',scenes=len(rows),boundaries=len(pairs),native_executed=False)

def check(path):
    plan=read(path);structure_path=Path(plan['structure_path']);structure=read(structure_path)
    result=validate(plan,structure,sha(structure_path))
    for item in plan['scenes']:
        if sha(item['source'])!=item['source_sha256']:raise ValueError('Source changed after visual review')
        if not item['evidence']:raise ValueError('Visual evidence required')
        for evidence in item['evidence']:
            if sha(evidence['path'])!=evidence['sha256']:raise ValueError('Evidence changed')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='mode',required=True)
    p=sub.add_parser('packet');p.add_argument('--structure',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('check');p.add_argument('--plan',type=Path,required=True)
    a=parser.parse_args();result=packet(a.structure,a.output) if a.mode=='packet' else check(a.plan)
    print(json.dumps(result,ensure_ascii=False,indent=2))
