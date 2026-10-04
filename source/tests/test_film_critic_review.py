import copy
import pytest
from scripts.film_critic_review import policy,validate

def example():
    structure=dict(task_id='Hero',clips=[dict(id='A',kind='image',frames=50,path='a.jpg'),
                                       dict(id='B',kind='image',frames=50,path='b.jpg')])
    report=dict(task_id='Hero',sequence_name='FILM',structure_sha256='s',engine='Premiere',
        project=dict(path='before.prproj',sha256='p'),render=dict(path='before.mp4',sha256='r'),
        native_record=dict(path='native.json',sha256='n'),audio_evaluated=False,
        reviewed_scene_ids=['A','B'],reviewed_boundaries=[['A','B']],continuous_watch_complete=True,
        frame_by_frame_ranges=[[0,100]],overall_assessment='Visually coherent',issues=[],correction_round=0)
    return structure,report

def issue():
    return dict(id='I1',severity='major',frame_range=[49,51],scene_ids=['A','B'],
        observation='Brightness jump',evidence='Adjacent rendered frames differ',
        proposed_fix='Adjust B highlights',reason='Smoother visual continuity',status='OPEN')

def test_default_enabled_and_no_music_completion_claim():
    assert policy()['enabled'] and policy()['max_correction_rounds']==1
    s,r=example();out=validate(r,s,r)
    assert out['status']=='VISUAL_READY_WAITING_MUSIC' and out['stop']

def test_issue_requests_one_correction():
    s,r=example();r['issues']=[issue()]
    assert validate(r,s,r)['status']=='CRITIC_CORRECTION_REQUIRED'

@pytest.mark.parametrize('mutate',[
    lambda r:r.update(frame_by_frame_ranges=[[0,49],[50,100]]),
    lambda r:r.update(continuous_watch_complete=False),
    lambda r:r.update(reviewed_boundaries=[]),
    lambda r:r.update(engine='FFmpeg'),
    lambda r:r.update(audio_evaluated=True),
    lambda r:r.update(correction_round=1),
    lambda r:r.update(issues=[dict(issue(),status='RESOLVED')]),
])
def test_incomplete_review_cannot_pass(mutate):
    s,r=example();mutate(r)
    with pytest.raises(ValueError):validate(r,s,r)

def test_recheck_needs_new_outputs_and_all_previous_issues():
    s,old=example();old['issues']=[issue()]
    r=copy.deepcopy(old);r.update(correction_round=1,previous_review_sha256='old',
        project=dict(path='after.prproj',sha256='p2'),render=dict(path='after.mp4',sha256='r2'))
    previous=dict(report=old,sha256='old')
    assert validate(r,s,r,previous)['status']=='CRITIC_ISSUES_REMAIN'
    r['issues'][0].update(status='RESOLVED',recheck_evidence='New render frames 49/50 now match')
    assert validate(r,s,r,previous)['status']=='VISUAL_READY_WAITING_MUSIC'
    r['issues']=[]
    with pytest.raises(ValueError):validate(r,s,r,previous)

def test_disabled_is_explicit_skip_not_qa_pass():
    s,r=example();r['critic_review']={'enabled':False}
    assert validate(r,s,r)['status']=='CRITIC_SKIPPED'
