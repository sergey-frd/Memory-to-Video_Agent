from copy import deepcopy
import pytest
from tools.revise_edit_plan import revise, validate_plan

def fixture():
    c=dict(id='v',kind='video',path='x',sha256='x',source_in_seconds=5,source_out_seconds=15,source_placement=dict(source_in_ticks=5*254016000000,source_out_ticks=20*254016000000),frames=250,duration_seconds=10,timeline_start_frame=0)
    return dict(fps=25,frames=250,duration_seconds=10,clips=[c])

def test_explicit_ranges_preserved():
    old=fixture()
    cfg=dict(clips=[dict(id='v',source_in_seconds=8,source_out_seconds=12.04)])
    new=revise(old,cfg)
    assert new['frames']==101
    assert new['clips'][0]['source_in_seconds']==8
    assert new['clips'][0]['source_out_seconds']==12.04
    assert old['clips'][0]['source_in_seconds']==5

@pytest.mark.parametrize('start,end',[(4,8),(12,21),(12,11),(8,8.01)])
def test_bad_ranges(start,end):
    with pytest.raises(ValueError):
        revise(fixture(),dict(clips=[dict(id='v',source_in_seconds=start,source_out_seconds=end)]))

def test_missing_id_and_timeline():
    with pytest.raises(ValueError): revise(fixture(),dict(clips=[]))
    plan=fixture();plan['clips'][0]['timeline_start_frame']=1
    with pytest.raises(ValueError):validate_plan(plan)


def test_timeline_split_maps_source_offsets():
    plan=revise(fixture(),dict(timeline_ranges=[dict(in_seconds=1,out_seconds=3),dict(in_seconds=7,out_seconds=9)]))
    assert [(c['source_in_seconds'],c['source_out_seconds']) for c in plan['clips']]==[(6,8),(12,14)]
    assert [c['timeline_start_frame'] for c in plan['clips']]==[0,50]
    assert plan['frames']==100

@pytest.mark.parametrize('ranges', [[(2,5),(4,6)],[(0,11)],[(-1,2)],[(0,0.01)],[]])
def test_timeline_rejects_invalid_intervals(ranges):
    with pytest.raises(ValueError):
        revise(fixture(),dict(timeline_ranges=[dict(in_seconds=a,out_seconds=b) for a,b in ranges]))
