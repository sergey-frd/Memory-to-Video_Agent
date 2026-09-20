"""Recover selected timeline ranges from reviewed versions into one source-based plan."""
from pathlib import Path
from tools.prepare_classification_input import digest, write_json
from tools.revise_edit_plan import revise_timeline, validate_plan
from tools.render_structure_draft import run as render
from tools.review_draft_alternative import read, assess_rendered


def assemble(plans, selections):
    if not selections:raise ValueError('Empty best assembly')
    clips=[];provenance=[];cursor=0;fps=None;seen=set()
    for selection in selections:
        index=selection['review_index']
        if index not in plans:raise ValueError('Unknown review index')
        plan=plans[index];validate_plan(plan)
        if fps is None:fps=plan['fps']
        if fps!=plan['fps']:raise ValueError('Mixed frame rates are not supported')
        if not selection.get('reason','').strip():raise ValueError('Selection reason required')
        part=revise_timeline(plan,dict(timeline_ranges=[dict(in_seconds=selection['in_seconds'],out_seconds=selection['out_seconds'],note=selection['reason'])]))
        start=cursor
        for clip in part['clips']:
            if clip['id'] in seen:raise ValueError('Repeated material in assembly: '+clip['id'])
            seen.add(clip['id']);clip['timeline_start_frame']=cursor
            clip['assembly_source_review']=index
            clip['assembly_source_range']=[clip['parent_timeline_in_seconds'],clip['parent_timeline_out_seconds']]
            cursor+=clip['frames'];clips.append(clip)
        provenance.append(dict(selection,output_in_seconds=start/fps,output_out_seconds=cursor/fps))
    result=dict(schema_version=1,status='DRAFT',revision='BEST ASSEMBLY',fps=fps,frames=cursor,
        duration_seconds=cursor/fps,clips=clips,assembly_selections=provenance,human_review='PENDING',
        limitations=['Rendered from originals without Premiere effects; audio cuts require listening'])
    validate_plan(result);return result


def run(config, reviews, comparison_review, dry_run=False):
    from tools.run_video_workflow import verify, snapshot
    config=Path(config).resolve();cfg=read(config)
    if cfg.get('schema_version')!=1:raise ValueError('Expected schema_version 1')
    plans={};inputs=snapshot([config]);indices=sorted(set(s['review_index'] for s in cfg['selections']))
    for index in indices:
        if type(index) is not int or not 1<=index<=len(reviews):raise ValueError('Invalid review_index')
        review=reviews[index-1];verify(review['artifacts'])
        plans[index]=read(review['plan']);inputs.update(review['artifacts'])
    verify(comparison_review['artifacts']);inputs.update(comparison_review['artifacts'])
    plan=assemble(plans,cfg['selections'])
    out=(config.parent/cfg['output_dir']).resolve()
    if dry_run:return dict(status='VALIDATED_NO_RENDER',duration_seconds=plan['duration_seconds'])
    if out.exists():
        if not (out/'inputs.json').exists() or read(out/'inputs.json')!=inputs:raise ValueError('Assembly output exists with different inputs')
    out.mkdir(parents=True,exist_ok=True);write_json(out/'inputs.json',inputs)
    write_json(out/'edit_plan.json',plan)
    proposal=dict(brief=cfg['narrative'],comparison=cfg['comparison'],selections=plan['assembly_selections'])
    write_json(out/'critique_and_alternative.json',proposal)
    lines=['# COMPARE → BEST ASSEMBLY',cfg['narrative'],'## Сравнение решений',*cfg['comparison'],'## Выбранные фрагменты']
    for s in plan['assembly_selections']:
        lines.append(f"- Alt{s['review_index']} {s['in_seconds']}–{s['out_seconds']} с → {s['output_in_seconds']}–{s['output_out_seconds']} с: {s['reason']}")
    report=out/'BEST_ASSEMBLY_RU.md';report.write_text('\n\n'.join(lines),encoding='utf-8')
    render_cfg=out/'render.local.json';write_json(render_cfg,dict(schema_version=1,edit_plan=str(out/'edit_plan.json'),
        output_root=str(out/'renders'),width=1280,height=720,fps=plan['fps'],video_bitrate='1400k',audio_bitrate='128k'))
    ready=[p for p in (out/'renders').glob('*/render_result.json') if read(p).get('status')=='DRAFT_READY' and read(p).get('structure_sha256')==digest(out/'edit_plan.json')]
    if len(ready)>1:raise ValueError('Ambiguous renders')
    result=ready[0] if ready else Path(render(render_cfg)['output'])/'render_result.json'
    post=assess_rendered(dict(model=cfg['model'],narrative=cfg['narrative']),comparison_review,result,proposal,out)
    lines+=['## Повторная оценка',read(post)['summary'],'## Осталось проверить',*read(post)['unresolved']]
    report.write_text('\n\n'.join(lines),encoding='utf-8');verify(inputs)
    return dict(result=str(result),report=str(report),post_critique=str(post),inputs=inputs,
        artifacts=snapshot([config,out/'inputs.json',out/'edit_plan.json',out/'critique_and_alternative.json',post,report]))
