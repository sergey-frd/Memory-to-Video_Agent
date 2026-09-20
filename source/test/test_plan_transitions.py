from tools.prepare_plan_transitions import select_transitions
import pytest

def plan():
    clips=[]
    for i in range(2):
        clips.append(dict(id=str(i),path=str(i),kind='video',source_in_seconds=2,source_out_seconds=12,source_placement=dict(source_in_ticks=0,source_out_ticks=20*254016000000),frames=250,duration_seconds=10,timeline_start_frame=i*250))
    return dict(fps=25,frames=500,duration_seconds=20,clips=clips)

def test_selection_and_source_handles():
    p=plan();cfg=dict(duration_seconds=.4,minimum_clip_seconds=8)
    selected,skipped=select_transitions(p,cfg)
    assert selected[0]['cut_frame']==250 and selected[0]['duration_frames']==10 and not skipped
    p['clips'][1].update(source_in_seconds=0,source_out_seconds=10)
    assert select_transitions(p,cfg)[1][0]['reason']=='insufficient verified source handles'

def test_short_same_source_and_odd_frames():
    p=plan()
    assert not select_transitions(p,dict(duration_seconds=.4,minimum_clip_seconds=11))[0]
    p['clips'][1]['path']='0'
    assert not select_transitions(p,dict(duration_seconds=.4,minimum_clip_seconds=8))[0]
    with pytest.raises(ValueError):select_transitions(p,dict(duration_seconds=.44,minimum_clip_seconds=8))
