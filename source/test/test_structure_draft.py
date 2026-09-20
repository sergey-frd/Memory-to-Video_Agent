import pytest
from tools.render_structure_draft import make_plan, TICKS


def test_uses_original_range_and_rejects_short_clip():
    row=dict(id='V', path='clip.mp4', sha256='x', kind='video', placements=[dict(number=1,start_ticks=0,source_in_ticks=7*TICKS,source_out_ticks=17*TICKS)])
    s=dict(status='DRAFT_REVIEW_REQUIRED',blocks=[dict(title='A',items=[dict(material_id='V',duration_seconds=5)])])
    plan=make_plan(s,[row],25)
    assert plan['clips'][0]['source_in_seconds']==7
    assert plan['clips'][0]['source_out_seconds']==12
    assert plan['frames']==125
    s['blocks'][0]['items'][0]['duration_seconds']=11
    with pytest.raises(ValueError):make_plan(s,[row],25)
