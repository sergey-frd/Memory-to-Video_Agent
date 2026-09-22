import pytest
from tools.build_video_structure import validate


def test_coverage_has_no_runtime_target_but_keeps_source_and_duplicate_guards():
    from tools.build_video_structure import repair_small_duration_error, repair_messages
    rows = [dict(id='V', kind='video', placements=[dict(source_in_ticks=0, source_out_ticks=8*254016000000)])]
    cfg = dict(duration_mode='coverage', required_ids=[], excluded_ids=[])
    item = dict(material_id='V', duration_seconds=7, reason='Distinct facet')
    plan = dict(title='Master', synopsis='Portrait', warnings=[], blocks=[dict(title='A', purpose='B', items=[item])])
    repair_small_duration_error(plan, rows, cfg)
    assert validate(plan, rows, cfg) == 7
    messages=[]
    repair_messages(messages, '{}', 'bad source', rows, cfg)
    assert 'No total duration target' in messages[-1]['content']
    item['duration_seconds']=9
    with pytest.raises(ValueError, match='exceeds available'): validate(plan, rows, cfg)
    item['duration_seconds']=7
    plan['blocks'][0]['items'].append(item.copy())
    with pytest.raises(ValueError, match='Repeated ID'): validate(plan, rows, cfg)


def test_structure_constraints():
    rows = [dict(id="M1", kind="image"), dict(id="M2", kind="video", placements=[dict(source_in_ticks=0, source_out_ticks=2540160000000)])]
    cfg = dict(required_ids=["M1"], excluded_ids=[], target_duration_seconds=10)
    plan = dict(title="Story", synopsis="Draft", warnings=[], blocks=[dict(title="A", purpose="B", items=[dict(material_id="M1", duration_seconds=10, reason="Opening")])])
    assert validate(plan, rows, cfg) == 10
    cfg["excluded_ids"] = ["M1"]
    with pytest.raises(ValueError): validate(plan, rows, cfg)
    cfg["excluded_ids"] = []
    plan["blocks"][0]["items"].append(dict(material_id="M1", duration_seconds=1, reason="Repeat"))
    with pytest.raises(ValueError): validate(plan, rows, cfg)
    plan["blocks"][0]["items"] = [dict(material_id="M2", duration_seconds=11, reason="Too long")]
    with pytest.raises(ValueError): validate(plan, rows, cfg)


def test_enum_and_repair_context():
    from tools.build_video_structure import schema_for, repair_messages, SCHEMA
    schema = schema_for([dict(id='M1'), dict(id='M2')], dict(excluded_ids=['M2']))
    field = schema['properties']['blocks']['items']['properties']['items']['items']['properties']['material_id']
    assert field['enum'] == ['M1']
    assert 'enum' not in SCHEMA['properties']['blocks']['items']['properties']['items']['items']['properties']['material_id']
    messages = []
    repair_messages(messages, '{"previous": true}', 'Repeated ID: M1')
    assert messages[0] == dict(role='assistant', content='{"previous": true}')
    assert 'Repeated ID: M1' in messages[1]['content']


def test_collects_duplicate_and_duration_errors():
    rows = [dict(id='M1', kind='image')]
    plan = dict(title='A', synopsis='B', warnings=[], blocks=[dict(title='A', purpose='B',
        items=[dict(material_id='M1', duration_seconds=100, reason='a')] * 2)])
    with pytest.raises(ValueError) as error:
        validate(plan, rows, dict(required_ids=[], excluded_ids=[], target_duration_seconds=10))
    assert 'Repeated ID: M1' in str(error.value)
    assert 'Total duration 200s' in str(error.value)


def test_retry_requests_editorial_revision_not_padding():
    import json
    from tools.build_video_structure import repair_messages
    rows = [dict(id='photo', kind='image'), dict(id='unused', kind='image'),
            dict(id='excluded', kind='image'), dict(id='video', kind='video',
                 placements=[dict(source_in_ticks=0, source_out_ticks=int(19.76*254016000000))])]
    plan = dict(blocks=[dict(items=[dict(material_id='photo', duration_seconds=228)])])
    messages = []
    repair_messages(messages, json.dumps(plan), 'duration', rows,
                    dict(target_duration_seconds=300, excluded_ids=['excluded']))
    content = messages[-1]['content']
    assert 'seconds_to_add' not in content
    assert 'not a minimum to fill' in content
    assert 'source limits' in content


def test_deficit_does_not_pad_photos_or_hide_invalid_sources():
    from tools.build_video_structure import repair_small_duration_error
    rows = [dict(id=str(i), kind='image') for i in range(6)]
    plan = dict(title='a', synopsis='b', warnings=[], blocks=[dict(title='a', purpose='b',
        items=[dict(material_id=str(i), duration_seconds=44, reason='x') for i in range(6)])])
    cfg = dict(target_duration_seconds=300, required_ids=[], excluded_ids=[])
    repair_small_duration_error(plan, rows, cfg)
    assert validate(plan, rows, cfg)==264
    assert not plan['warnings']
    for item in plan['blocks'][0]['items']: item['duration_seconds']=30
    repair_small_duration_error(plan, rows, cfg)
    assert sum(i['duration_seconds'] for i in plan['blocks'][0]['items'])==180
    rows[0].update(kind='video', placements=[dict(source_in_ticks=0,source_out_ticks=254016000000)])
    with pytest.raises(ValueError, match='exceeds available'):
        repair_small_duration_error(plan, rows, cfg)


def test_explicit_target_rejects_deficit_without_mutating_shots():
    import copy
    from tools.build_video_structure import repair_small_duration_error
    rows = [dict(id='p', kind='image')]
    plan = dict(title='A', synopsis='B', warnings=[], blocks=[dict(title='A', purpose='B',
        items=[dict(material_id='p', duration_seconds=5, reason='The expression is clear')])])
    original = copy.deepcopy(plan)
    cfg = dict(duration_mode='target', target_duration_seconds=10, required_ids=[], excluded_ids=[])
    with pytest.raises(ValueError, match='Total duration'):
        repair_small_duration_error(plan, rows, cfg)
    assert plan == original
    assert validate(plan, rows, dict(cfg, duration_mode='compact')) == 5
