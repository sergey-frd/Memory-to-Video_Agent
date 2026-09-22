import json
from unittest.mock import patch
import pytest
from tools.review_draft_alternative import balance_duration
from tools.render_structure_draft import run


def test_over_budget_never_removes_ending_or_other_scenes():
    items=[dict(material_id=str(i),duration_seconds=70,reason='x') for i in range(4)]
    data=dict(alternative=dict(title='a',synopsis='b',warnings=[],blocks=[dict(title='a',purpose='b',items=items)]))
    rows=[dict(id=str(i),kind='image') for i in range(4)]
    cfg=dict(target_duration_seconds=180,max_duration_seconds=180,required_ids=['3'],excluded_ids=[])
    import copy
    original = copy.deepcopy(data)
    with pytest.raises(ValueError, match='duration|budget'):
        balance_duration(data, rows, cfg)
    assert data == original


def test_renderer_blocks_one_frame_over_cap_before_ffmpeg():
    from pathlib import Path
    from uuid import uuid4
    tmp_path = Path('test_runtime') / ('compact_cap_' + uuid4().hex)
    tmp_path.mkdir(parents=True)
    cfg=dict(schema_version=1,width=1280,height=720,fps=25,edit_plan='plan.json',output_root='out',max_duration_seconds=180)
    (tmp_path/'cfg.json').write_text(json.dumps(cfg))
    (tmp_path/'plan.json').write_text(json.dumps(dict(fps=25,clips=[dict(frames=4501)])))
    with patch('tools.revise_edit_plan.validate_plan'), patch('tools.render_structure_draft.resolve_ffmpeg_executable') as ffmpeg:
        with pytest.raises(ValueError,match='Render forbidden'):
            run(tmp_path/'cfg.json')
        ffmpeg.assert_not_called()
    assert not (tmp_path/'out').exists()
