import pytest
from tools.prepare_classification_input import digest, write_json
from tools.review_draft_alternative import resolve_classification


def test_checkpoint_resolution_and_tamper_detection(tmp_path):
    result = tmp_path / 'classification_result.json'
    catalog = tmp_path / 'catalog.jsonl'
    write_json(result, {'status': 'CLASSIFIED_REVIEW_REQUIRED'})
    catalog.write_text('{}\n')
    state = tmp_path / 'pipeline_state.json'
    write_json(state, {'stages': {'classification': {
        'result': str(result),
        'artifacts': {str(p): digest(p) for p in (result, catalog)}}}})
    config = tmp_path / 'alternative.json'
    cfg = {'pipeline_state': state.name}
    assert resolve_classification(config, cfg) == result
    catalog.write_text('changed')
    with pytest.raises(ValueError, match='artifact changed'):
        resolve_classification(config, cfg)


def test_missing_checkpoint_and_ambiguous_source(tmp_path):
    config = tmp_path / 'alternative.json'
    write_json(tmp_path / 'state.json', {'stages': {}})
    with pytest.raises(ValueError, match='not completed'):
        resolve_classification(config, {'pipeline_state': 'state.json'})
    with pytest.raises(ValueError, match='exactly one'):
        resolve_classification(config, {'pipeline_state': 'state.json', 'classification_result': 'r.json'})
    assert resolve_classification(config, {'classification_result': 'r.json'}) == tmp_path / 'r.json'
