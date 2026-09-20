import copy
from pathlib import Path
from unittest.mock import patch

import pytest
from tools.prepare_classification_input import write_json, digest
from tools.review_draft_alternative import validate_response, sample_times, run as alternative
from tools.run_video_workflow import run, read
from test_video_workflow import fixture


def test_timestamp_schema_excludes_rounded_or_invented_times():
    from tools.review_draft_alternative import timestamp_schema
    schema=timestamp_schema([24.98, .596, 24.98])
    assert schema==dict(type='number',enum=[.596,24.98])
    assert 25.1 not in schema['enum'] and .6 not in schema['enum']


def test_repeat_removal_requires_valid_remaining_plan():
    from tools.review_draft_alternative import remove_validated_repeats
    data=response(); item=data['alternative']['blocks'][0]['items'][0]
    data['alternative']['blocks'][0]['items'].append(dict(item))
    original=copy.deepcopy(data)
    cfg=dict(target_duration_seconds=16,required_ids=[],excluded_ids=[])
    with pytest.raises(ValueError,match='Total duration'):
        remove_validated_repeats(data,[dict(id='p',kind='image')],cfg)
    assert data==original
    cfg['target_duration_seconds']=8
    remove_validated_repeats(data,[dict(id='p',kind='image')],cfg)
    assert len(data['alternative']['blocks'][0]['items'])==1
    assert len(data['duplicate_repair'])==1


def test_critique_retry_preserves_envelope_and_supplies_budget():
    import json
    from tools.review_draft_alternative import critique_repair_messages
    data=response()
    data['alternative']['blocks'][0]['items'][0]['duration_seconds']=195
    raw=json.dumps(data)
    messages=critique_repair_messages(raw, 'Duration repair exceeds 20%',
        [dict(id='p',kind='image'),dict(id='unused',kind='image')],
        dict(target_duration_seconds=300,excluded_ids=[]),[.996])
    assert messages[0]['content']==raw
    assert '"seconds_to_add": 105' in messages[1]['content']
    assert 'FULL critique AND alternative' in messages[1]['content']
    assert '[0.996]' in messages[1]['content']


def test_duration_repair_respects_capacity_and_rejects_unrelated_errors():
    from tools.review_draft_alternative import balance_duration
    cfg=dict(required_ids=[],excluded_ids=[],target_duration_seconds=124)
    data=response();data['alternative']['blocks'][0]['items']=[dict(material_id='p',duration_seconds=105,reason='rhythm')]
    row=dict(id='p',kind='video',placements=[dict(source_in_ticks=0,source_out_ticks=124*254016000000)])
    balance_duration(data,[row],cfg)
    assert data['duration_repair']['after_seconds']==124
    assert data['alternative']['blocks'][0]['items'][0]['duration_seconds']==124
    bad=response();bad['alternative']['blocks'][0]['items'][0]['duration_seconds']=105
    short=dict(row,placements=[dict(source_in_ticks=0,source_out_ticks=110*254016000000)])
    with pytest.raises(ValueError,match='capacity'):balance_duration(bad,[short],cfg)
    bad['alternative']['blocks'][0]['items'][0]['material_id']='unknown'
    with pytest.raises(ValueError,match='Unknown ID'):balance_duration(bad,[row],cfg)


def response():
    return dict(critique=dict(summary='Shorten opening', strengths=['Clear subject'],
        findings=[dict(time_seconds=.996, observation='Repetitive opening', change='Shorten')], limitations=['No audio']),
        alternative=dict(title='B', synopsis='Shorter', warnings=[], blocks=[dict(title='A',purpose='Opening',
            items=[dict(material_id='p', duration_seconds=8, reason='Less repetition')])]))


def setup(root):
    row=dict(id='p',kind='image',path='image.jpg',sha256='x')
    (root/'catalog.jsonl').write_text(__import__('json').dumps(row)+'\n',encoding='utf-8')
    write_json(root/'classification_result.json',dict(status='CLASSIFIED_REVIEW_REQUIRED',completed=1,total=1))
    cfg=dict(schema_version=1,classification_result='classification_result.json',model='unused',
             target_duration_seconds=8,narrative='Shorter',required_ids=[],excluded_ids=[])
    write_json(root/'alternative.json',cfg)
    return row,cfg


def test_response_rejects_invented_evidence_and_unchanged_edit():
    root,_=fixture();row,cfg=setup(root);plan=read(root/'edit_plan.json');times=sample_times(plan)
    result=response();assert validate_response(result,[row],cfg,plan,times)['frames']==200
    bad=copy.deepcopy(result);bad['critique']['findings'][0]['time_seconds']=500
    with pytest.raises(ValueError,match='timestamp'):validate_response(bad,[row],cfg,plan,times)
    bad=copy.deepcopy(result);bad['alternative']['blocks'][0]['items'][0]['duration_seconds']=10
    with pytest.raises(ValueError,match='actual selection'):validate_response(bad,[row],dict(cfg,target_duration_seconds=10),plan,times)


def test_review_branch_resume_select_and_approval():
    root,cfg=fixture();setup(root)
    c=read(cfg);c['review']=dict(require_alternative=True);write_json(cfg,c)
    run(cfg,'start')
    with pytest.raises(ValueError,match='alternative'):run(cfg,'approve')
    calls=[]
    def fake(ac,review,out,dry_run=False):
        if dry_run:return {}
        calls.append(1)
        if len(calls)==1:raise RuntimeError('interrupted')
        out.mkdir();plan=read(review['plan']);plan['clips'][0].update(frames=200,duration_seconds=8)
        plan.update(frames=200,duration_seconds=8);write_json(out/'edit_plan.json',plan)
        (out/'video.mp4').write_bytes(b'alternative')
        write_json(out/'result.json',dict(status='DRAFT_READY',video=str(out/'video.mp4'),sha256=digest(out/'video.mp4')))
        (out/'critique.md').write_text('critique');write_json(out/'evidence.json',response());write_json(out/'inputs.json',{})
        return dict(result=str(out/'result.json'),critique=str(out/'critique.md'),evidence=str(out/'evidence.json'),inputs=str(out/'inputs.json'))
    with patch('tools.run_video_workflow.alternative',side_effect=fake):
        with pytest.raises(RuntimeError):run(cfg,'alternative',alternative_config=root/'alternative.json')
        with pytest.raises(ValueError,match='pending alternative'):run(cfg,'approve')
        state=run(cfg,'alternative',alternative_config=root/'alternative.json')
    assert len(state['reviews'])==2 and state['selected_review']==1
    state=run(cfg,'select',review_index=1);state=run(cfg,'approve')
    assert state['approval']==state['reviews'][0]['artifacts']
    state=run(cfg,'select',review_index=2);assert 'approval' not in state
    assert (root/'draft.mp4').read_bytes()==b'mock-video'


def test_cached_critique_survives_render_failure_and_inputs_are_pinned():
    root,cfg=fixture();setup(root);state=run(cfg,'start');review=state['reviews'][0]
    out=root/'alternative';out.mkdir();write_json(out/'critique_and_alternative.json',response())
    with patch('tools.review_draft_alternative.render',side_effect=RuntimeError('render failed')):
        with pytest.raises(RuntimeError):alternative(root/'alternative.json',review,out)
    assert read(out/'edit_plan.json')['frames']==200
    def fake_render(config):
        c=read(config);folder=Path(c['output_root'])/'one';folder.mkdir(parents=True)
        write_json(folder/'render_result.json',dict(status='DRAFT_READY',structure_sha256=digest(c['edit_plan'])))
        return dict(output=str(folder))
    def fake_assess(cfg, review, result, proposal, folder):
        path=folder/'POST_CRITIQUE.json'
        write_json(path,dict(summary='Improved',verdict='improved',continue_iteration=False,
            weaknesses_found=['long'],changes_made=['shortened'],why_better=['less repetition'],unresolved=['audio']))
        return path
    with patch('tools.review_draft_alternative.render',side_effect=fake_render) as rendered, patch('tools.review_draft_alternative.assess_rendered',side_effect=fake_assess):
        a=alternative(root/'alternative.json',review,out)
        b=alternative(root/'alternative.json',review,out)
        assert a==b and rendered.call_count==1
    (root/'catalog.jsonl').write_text((root/'catalog.jsonl').read_text()+'\n')
    with pytest.raises(ValueError,match='inputs changed'):alternative(root/'alternative.json',review,out)


def test_plateau_stops_without_rendering_another_version():
    root,cfg=fixture();state=run(cfg,'start')
    post=root/'post.json';write_json(post,dict(continue_iteration=False))
    state['reviews'][0].update(post_critique=str(post),iteration_report='report.md')
    state['reviews'][0]['artifacts'][str(post)]=digest(post)
    write_json(root/'state/workflow_state.json',state)
    with patch('tools.run_video_workflow.alternative') as creator:
        result=run(cfg,'alternative',alternative_config=root/'unused.json')
        creator.assert_not_called()
    assert result['status']=='ITERATION_REVIEW_REQUIRED' and len(result['reviews'])==1


def test_simple_permutation_is_rejected():
    root,_=fixture();row,cfg=setup(root);plan=read(root/'edit_plan.json')
    plan['clips'][0].update(frames=100,duration_seconds=4)
    second=copy.deepcopy(plan['clips'][0]);second.update(id='q',timeline_start_frame=100)
    plan['clips'].append(second);plan.update(frames=200,duration_seconds=8)
    data=response();data['critique']['findings'][0]['time_seconds']=sample_times(plan)[0]
    data['alternative']['blocks'][0]['items']=[dict(material_id=i,duration_seconds=4,reason='reorder') for i in ['q','p']]
    with pytest.raises(ValueError,match='mere permutation'):
        validate_response(data,[row,dict(row,id='q')],cfg,plan,sample_times(plan))
