"""Guard montage timing and unsupported native routes before any Adobe write."""
import copy
import pytest
from scripts.prepare_user_sequence_finish import validate

F=10160640000

def job():
    return dict(width=2160,height=3840,fps=25,video_track=1,
                transition_alignment=.5,source_name='USER',name='FINISH',frames=100,
                expected=[dict(group=0,track_index=1,timeline_start_ticks=0,timeline_end_ticks=50*F),
                          dict(group=0,track_index=1,timeline_start_ticks=50*F,timeline_end_ticks=100*F)],
                edits=[dict(index=1,scale=100,position=[.5,.5],keys=[[0,100,[.5,.5]],[49,103,[.5,.5]]])],
                transitions=[dict(incoming=2,cut=50,frames=10)])

def test_adjoining_cut_and_valid_key_timing():
    validate(job())

@pytest.mark.parametrize('change',[
    lambda j:j.update(width=3840,height=2160),
    lambda j:j['edits'][0]['keys'].append([50,105,[.5,.5]]),
    lambda j:j['edits'][0].update(scale=float('nan')),
    lambda j:j['transitions'][0].update(cut=49),
    lambda j:j['transitions'].append(copy.deepcopy(j['transitions'][0])),
    lambda j:j['expected'][0].update(timeline_end_ticks=49*F),
    lambda j:j.update(colors=[{'Exposure':.1}]),
])
def test_refuses_unsafe_plan(change):
    value=job();change(value)
    with pytest.raises(ValueError):validate(value)
