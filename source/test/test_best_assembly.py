import pytest
from tools.build_best_assembly import assemble

def plan(mid,source_in=10):
    return dict(fps=25,frames=250,duration_seconds=10,clips=[dict(id=mid,kind='video',path='source.mov',
        sha256='test',frames=250,duration_seconds=10,timeline_start_frame=0,source_in_seconds=source_in,
        source_out_seconds=source_in+10,source_placement=dict(source_in_ticks=0,source_out_ticks=100*254016000000))])

def test_segments_map_back_to_original_media():
    p=assemble({3:plan('a'),6:plan('b',30)},[
        dict(review_index=3,in_seconds=2,out_seconds=5,reason='opening'),
        dict(review_index=6,in_seconds=1,out_seconds=7,reason='ending')])
    assert p['duration_seconds']==9
    assert [(c['source_in_seconds'],c['source_out_seconds'],c['timeline_start_frame']) for c in p['clips']]==[(12,15,0),(31,37,75)]
    assert [c['assembly_source_review'] for c in p['clips']]==[3,6]

def test_duplicate_media_and_unaligned_cut_rejected():
    selection=dict(review_index=3,in_seconds=0,out_seconds=5,reason='scene')
    with pytest.raises(ValueError,match='Repeated'):assemble({3:plan('a')},[selection,selection])
    with pytest.raises(ValueError,match='align'):assemble({3:plan('a')},[dict(selection,in_seconds=.01)])
