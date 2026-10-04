"""Optional final critic review contract. Validates records; does not watch video."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.creative_finish_plan import read,sha,save,clips

DEFAULT_POLICY={'enabled':True,'max_correction_rounds':1,'pause_before_native':True,'pause_before_music':True}

def policy(options=None):
    result=dict(DEFAULT_POLICY);options=options or {}
    if set(options)-set(result):raise ValueError('Unknown critic option')
    result.update(options)
    if not isinstance(result['enabled'],bool):raise ValueError('enabled must be boolean')
    n=result['max_correction_rounds']
    if isinstance(n,bool) or not isinstance(n,int) or not 0<=n<=1:raise ValueError('This route supports at most one correction round')
    if result['pause_before_native'] is not True or result['pause_before_music'] is not True:raise ValueError('Native handoff and music pauses required')
    return result

def artifact(record):
    p=Path(record['path'])
    if sha(p)!=record['sha256']:raise ValueError('Artifact changed: '+str(p))
    return p.resolve()

def coverage(ranges,frames):
    cursor=0
    for a,b in ranges:
        if any(isinstance(v,bool) or not isinstance(v,int) for v in [a,b]) or a!=cursor or not a<b<=frames:raise ValueError('Incomplete/overlapping frame review')
        cursor=b
    if cursor!=frames:raise ValueError('All rendered frames must be reviewed')

def validate(report,structure,current,previous=None):
    rows=clips(structure);ids=[c['id'] for c in rows];frames=sum(c['frames'] for c in rows)
    opts=policy(report.get('critic_review'))
    if not opts['enabled']:return dict(status='CRITIC_SKIPPED',native_qa_still_required=True,stop=True)
    if report['task_id']!=structure['task_id'] or report['structure_sha256']!=current['structure_sha256']:raise ValueError('Stale structure')
    if report['sequence_name']!=current['sequence_name']:raise ValueError('Wrong sequence')
    for name in ['project','render','native_record']:
        if report[name]!=current[name]:raise ValueError('Review is not of current native result')
    if current['engine'] not in ['Premiere','Adobe Media Encoder']:raise ValueError('Native Adobe render required')
    if report['audio_evaluated'] is not False:raise ValueError('Music/audio is outside visual critic review')
    if report['reviewed_scene_ids']!=ids or report['reviewed_boundaries']!=[list(p) for p in zip(ids,ids[1:])]:raise ValueError('Scenes/boundaries not fully reviewed')
    if report['continuous_watch_complete'] is not True:raise ValueError('Whole film review required')
    coverage(report['frame_by_frame_ranges'],frames)
    if not report['overall_assessment'].strip():raise ValueError('Critic assessment required')
    cursor=0;bounds={}
    for c in rows:bounds[c['id']]=(cursor,cursor+c['frames']);cursor+=c['frames']
    seen=set();unresolved=[]
    for issue in report['issues']:
        ident=issue['id']
        if not ident or ident in seen:raise ValueError('Duplicate/empty issue ID')
        seen.add(ident)
        if issue['severity'] not in ['blocking','major','minor']:raise ValueError('Unknown severity')
        a,b=issue['frame_range']
        if any(isinstance(v,bool) or not isinstance(v,int) for v in [a,b]) or not 0<=a<b<=frames:raise ValueError('Issue range outside render')
        if not issue['scene_ids'] or any(s not in bounds for s in issue['scene_ids']):raise ValueError('Unknown affected scene')
        if any(not (a<bounds[s][1] and b>bounds[s][0]) for s in issue['scene_ids']):raise ValueError('Issue range does not match scenes')
        for key in ['observation','evidence','proposed_fix','reason']:
            if not isinstance(issue[key],str) or not issue[key].strip():raise ValueError('Concrete critic evidence and fix required')
        if issue['status'] not in ['OPEN','RESOLVED','DEFERRED']:raise ValueError('Unknown issue status')
        if issue['status']=='RESOLVED' and previous is None:raise ValueError('Cannot claim correction without a recheck')
        if issue['status']!='RESOLVED':unresolved.append(issue)
    round_index=report['correction_round']
    if isinstance(round_index,bool) or not isinstance(round_index,int) or not 0<=round_index<=opts['max_correction_rounds']:raise ValueError('Correction limit exceeded')
    if previous is not None:
        if report.get('previous_review_sha256')!=previous['sha256'] or round_index!=previous['report']['correction_round']+1:raise ValueError('Wrong correction lineage')
        old=previous['report']
        if old['task_id']!=report['task_id'] or old['sequence_name']!=report.get('previous_sequence_name',report['sequence_name']):raise ValueError('Previous review mismatch')
        if old['project']['path']==report['project']['path'] or old['render']['path']==report['render']['path']:raise ValueError('Preserve baseline project/render; use new outputs')
        if old['project']['sha256']==report['project']['sha256'] or old['render']['sha256']==report['render']['sha256']:raise ValueError('No changed native result to recheck')
        old_issues={i['id']:i for i in old['issues'] if i['status']!='RESOLVED'}
        if set(old_issues)-seen:raise ValueError('Previous issues silently dropped')
        for issue in report['issues']:
            if issue['status']=='RESOLVED':
                if issue['id'] not in old_issues or not issue.get('recheck_evidence','').strip():raise ValueError('Fix needs traceable recheck evidence')
    elif round_index!=0:raise ValueError('Previous review required for correction round')
    blockers=[i['id'] for i in unresolved if i['severity']!='minor']
    open_issues=[i['id'] for i in unresolved if i['status']=='OPEN']
    if blockers or open_issues:
        state='CRITIC_CORRECTION_REQUIRED' if round_index<opts['max_correction_rounds'] else 'CRITIC_ISSUES_REMAIN'
    else:state='VISUAL_READY_WAITING_MUSIC'
    return dict(status=state,issues_to_fix=open_issues,blocking_issues=blockers,
                deferred_minor=[i['id'] for i in unresolved if i['status']=='DEFERRED'],
                stop=state!='CRITIC_CORRECTION_REQUIRED',audio_evaluated=False,
                verification_scope='Record integrity and review coverage; visual judgement is the agent assessment')

def packet(structure_path,project,render,native_record,output):
    structure_path=structure_path.resolve();output=output.resolve()
    if output.is_relative_to(ROOT):raise ValueError('Critic data must be outside repository')
    if output.exists():raise ValueError('Use fresh review directory')
    structure=read(structure_path);rows=clips(structure);native=read(native_record)
    if native['engine'] not in ['Premiere','Adobe Media Encoder']:raise ValueError('FFmpeg preview cannot stand in for native render')
    if sha(project)!=native['project_sha256'] or sha(render)!=native['render_sha256']:raise ValueError('Native result provenance is stale')
    if native['frames']!=sum(c['frames'] for c in rows) or native['fps']!=structure.get('fps',25):raise ValueError('Native timing does not match structure')
    report=dict(task_id=structure['task_id'],structure_path=str(structure_path),structure_sha256=sha(structure_path),
        sequence_name=native['sequence_name'],engine=native['engine'],critic_review=policy(structure.get('critic_review')),
        project=dict(path=str(project.resolve()),sha256=sha(project)),render=dict(path=str(render.resolve()),sha256=sha(render)),
        native_record=dict(path=str(native_record.resolve()),sha256=sha(native_record)),correction_round=0,
        audio_evaluated=False,continuous_watch_complete=False,frame_by_frame_ranges=[],
        reviewed_scene_ids=[],reviewed_boundaries=[],overall_assessment='',issues=[])
    output.mkdir(parents=True);save(output/'critic_review.json',report)
    (output/'REQUEST_RU.md').write_text('''# Финальный проход кинокритика

Оцени фактический native render и текущий сохранённый проект. Сначала просмотри
фильм целиком, затем все кадры последовательно; подробно проверь склейки,
пики Motion, лица/текст у края и цветовые скачки. Контактный лист с редкими пробами
не подтверждает покадровый просмотр. Оцени как режиссёр и художник, не защищай
предыдущий план. Не придумывай недостатки ради количества замечаний.
Запиши каждое конкретное замечание с frame range, сценами, доказательством,
важностью и минимальным исправлением. Музыку/аудио не оценивай. Если нужно,
подготовь одну новую исправленную версию; сохраняй исходный проект/render.
Перед нативным применением остановка USER_ACTION_REQUIRED. После применения
посмотри новый native render целиком, перепроверь все кадры и каждое замечание.
Затем остановись: VISUAL_READY_WAITING_MUSIC или CRITIC_ISSUES_REMAIN.
Контракт и границы реализации — docs/FILM_CRITIC_RU.md.
''',encoding='utf-8')
    return dict(status='CRITIC_REVIEW_REQUIRED',report=str(output/'critic_review.json'),options=report['critic_review'])

def check(path,previous_path=None):
    report=read(path);structure=read(report['structure_path'])
    if sha(report['structure_path'])!=report['structure_sha256']:raise ValueError('Structure changed')
    for name in ['project','render','native_record']:artifact(report[name])
    native=read(report['native_record']['path'])
    if native['project_sha256']!=report['project']['sha256'] or native['render_sha256']!=report['render']['sha256']:raise ValueError('Native record does not cover reviewed files')
    if native['engine']!=report['engine'] or native['sequence_name']!=report['sequence_name'] or native['frames']!=sum(c['frames'] for c in clips(structure)) or native['fps']!=structure.get('fps',25):raise ValueError('Native record mismatch')
    previous=None
    if previous_path:
        old=read(previous_path)
        for name in ['project','render','native_record']:artifact(old[name])
        previous=dict(report=old,sha256=sha(previous_path))
    return validate(report,structure,report,previous)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='mode',required=True)
    p=sub.add_parser('packet')
    for name in ['structure','project','render','native-record','output']:p.add_argument('--'+name,type=Path,required=True)
    p=sub.add_parser('check');p.add_argument('--report',type=Path,required=True);p.add_argument('--previous',type=Path)
    a=parser.parse_args()
    result=packet(a.structure,a.project,a.render,a.native_record,a.output) if a.mode=='packet' else check(a.report,a.previous)
    print(json.dumps(result,ensure_ascii=False,indent=2))
