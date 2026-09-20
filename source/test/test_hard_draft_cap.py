import json
from unittest.mock import patch
import pytest
from tools.review_draft_alternative import balance_duration
from tools.render_structure_draft import run


def test_removes_whole_scenes_until_under_cap():
    items=[dict(material_id=str(i),duration_seconds=70,reason='x') for i in range(4)]
    data=dict(alternative=dict(title='a',synopsis='b',warnings=[],blocks=[dict(title='a',purpose='b',items=items)]))
    rows=[dict(id=str(i),kind='image') for i in range(4)]
    cfg=dict(target_duration_seconds=180,max_duration_seconds=180,required_ids=['3'],excluded_ids=[])
    balance_duration(data,rows,cfg)
    kept=data['alternative']['blocks'][0]['items']
    assert sum(i['duration_seconds'] for i in kept)==140
    assert [i['material_id'] for i in kept]==['0','3']


def test_renderer_blocks_one_frame_over_cap_before_ffmpeg(tmp_path):
    cfg=dict(schema_version=1,width=1280,height=720,fps=25,edit_plan='plan.json',output_root='out',max_duration_seconds=180)
    (tmp_path/'cfg.json').write_text(json.dumps(cfg))
    (tmp_path/'plan.json').write_text(json.dumps(dict(fps=25,clips=[dict(frames=4501)])))
    with patch('tools.revise_edit_plan.validate_plan'), patch('tools.render_structure_draft.resolve_ffmpeg_executable') as ffmpeg:
        with pytest.raises(ValueError,match='Render forbidden'):
            run(tmp_path/'cfg.json')
        ffmpeg.assert_not_called()
    assert not (tmp_path/'out').exists()
