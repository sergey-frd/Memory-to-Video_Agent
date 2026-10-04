import copy
import json
from pathlib import Path
import pytest
from scripts.creative_finish_plan import validate,packet,check

def example():
    structure=dict(task_id='Hero',branch='MAIN',fps=25,clips=[
        dict(id='A',path='photo_a.jpg',kind='image',frames=100),
        dict(id='B',path='photo_b.jpg',kind='image',frames=100)])
    scenes=[]
    for i,c in enumerate(structure['clips']):
        scenes.append(dict(id=c['id'],source=c['path'],kind='image',frames=100,timeline_start_frame=i*100,
            observation='Both people remain in view',
            motion=dict(mode='pulse',keys=[[0,1,.5,.5],[.5,1.04,.5,.5],[1,1,.5,.5]],reason='Quiet visual emphasis'),
            color=dict(mode='preserve',values={},reason='Pair already matches')))
    plan=dict(schema_version=1,status='PLAN_READY_NATIVE_PENDING',task_id='Hero',branch='MAIN',
        structure_sha256='hash',scenes=scenes,audio_policy='ignore',music_bpm=None,pulse_peaks=[],
        boundaries=[dict(left='A',right='B',decision='cut',frames=0,left_exit='Settles',right_entry='Still',reason='Direct action connection')],
        second_pass=dict(status='ACCEPTED',reviewed_scene_ids=['A','B'],reviewed_boundaries=[['A','B']],
            rhythm='Varied',composition='Faces safe',color_continuity='Matched',issues=[]))
    return structure,plan

def test_complete_unified_plan_is_not_native_completion():
    structure,plan=example();result=validate(plan,structure,'hash')
    assert result['status']=='PLAN_CONTRACT_PASS' and result['native_executed'] is False

@pytest.mark.parametrize('mutate',[
    lambda p:p.update(structure_sha256='stale'),
    lambda p:p.update(boundaries=[]),
    lambda p:p['scenes'].reverse(),
    lambda p:p['scenes'][0].update(frames=99),
    lambda p:p['scenes'][0]['motion']['keys'][-1].__setitem__(1,1.1),
    lambda p:p['scenes'][0]['color']['values'].update(Exposure=1),
    lambda p:p['second_pass'].update(issues=['Unsafe crop']),
    lambda p:p.update(second_pass=None),
    lambda p:p.update(music_bpm=120),
    lambda p:p['boundaries'][0].update(decision='cross_dissolve',frames=10),
])
def test_incomplete_or_stale_joint_decisions_are_rejected(mutate):
    structure,plan=example();mutate(plan)
    with pytest.raises(ValueError):validate(plan,structure,'hash')

def test_packet_keeps_scene_order_and_is_pending_without_ai_or_adobe(tmp_path):
    from PIL import Image
    photo=tmp_path/'photo.jpg';Image.new('RGB',(32,48),'white').save(photo)
    structure=tmp_path/'structure.json'
    structure.write_text(json.dumps(dict(task_id='Hero',branch='SHORT',fps=25,
        clips=[dict(id='S',path=str(photo),kind='image',frames=100)])),encoding='utf-8')
    result=packet(structure,tmp_path/'packet')
    plan=json.loads(Path(result['plan']).read_text(encoding='utf-8'))
    assert plan['audio_policy']=='ignore' and plan['music_bpm'] is None
    assert plan['scenes'][0]['motion'] is None and plan['second_pass'] is None
    with pytest.raises(ValueError):check(Path(result['plan']))
    with pytest.raises(ValueError):packet(structure,tmp_path/'packet')
