import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from tools.classify_source_package import run, validate, cache_key
from tools.prepare_classification_input import digest, write_json


def test_api_cache_and_manual_status():
    root = (Path('test_runtime') / ('classify_' + uuid4().hex)).resolve()
    root.mkdir(parents=True)
    source = root / 'source.jpg'
    source.write_bytes(b'test evidence')
    row = dict(id='M1', path=str(source), sha256=digest(source), kind='image',
               placements=[], previews=[dict(path='source.jpg')])
    write_json(root / 'inventory.json', [row])
    write_json(root / 'classification_input.json', dict(schema_version=1, status='READY',
               inventory='inventory.json', context={}))
    cfg = dict(schema_version=1, input_manifest='classification_input.json', output_root='results')
    write_json(root / 'config.json', cfg)
    answer = dict(description='Описание', themes=['семья'], style='photo', quality_notes=[], uncertainties=[])
    with patch('openai.OpenAI') as client:
        client.return_value.responses.create.return_value = SimpleNamespace(output_text=json.dumps(answer))
        assert run(root / 'config.json', dry_run=True)['status'] == 'VALIDATED_NO_API'
        client.assert_not_called()
        assert run(root / 'config.json')['status'] == 'CLASSIFIED_REVIEW_REQUIRED'
        assert run(root / 'config.json')['completed'] == 1
        assert client.return_value.responses.create.call_count == 1
        cfg['ai_enabled'] = False
        write_json(root / 'config.json', cfg)
        assert run(root / 'config.json')['status'] == 'MANUAL_REQUIRED'
        source.write_bytes(b'changed')
        with pytest.raises(ValueError, match='checksum mismatch'):
            run(root / 'config.json', dry_run=True)


def test_response_validation_and_cache_invalidation():
    with pytest.raises(ValueError):
        validate({'description': 'incomplete'})
    row = dict(sha256='a', kind='image', placements=[])
    assert cache_key(row, 'model', {}, ['a']) != cache_key(row, 'model', {}, ['b'])
    assert cache_key(row, 'model', {}, ['a']) != cache_key(row, 'other', {}, ['a'])


def test_progress_heartbeat_stops_after_activity():
    import threading
    from tools.classify_source_package import ProgressLog
    root = Path('test_runtime') / ('log_' + uuid4().hex)
    root.mkdir(parents=True)
    log = ProgressLog(root / 'progress.log', interval=0.01)
    observed = threading.Event()
    original = log.emit
    def emit(event, message):
        original(event, message)
        if event == 'WAIT':
            observed.set()
    log.emit = emit
    with log.activity('API M001 sample.jpg'):
        assert observed.wait(2)
    text = log.path.read_text(encoding='utf-8')
    assert '[START]' in text and '[WAIT]' in text and 'M001' in text
    log.emit('DONE', 'M001')
    assert log.path.read_text(encoding='utf-8').endswith('[DONE] M001\n')


def test_json_retry_and_fenced_response():
    from unittest.mock import Mock
    from tools.classify_source_package import request_analysis, parse_analysis
    answer = dict(description='ok', themes=[], style='photo', quality_notes=[], uncertainties=[])
    assert parse_analysis('```json\n' + json.dumps(answer) + '\n```') == answer
    root = Path('test_runtime') / ('retry_' + uuid4().hex)
    client, log = Mock(), Mock()
    from contextlib import nullcontext
    log.activity.side_effect = lambda label: nullcontext()
    client.responses.create.side_effect = [SimpleNamespace(output_text=''), SimpleNamespace(output_text=json.dumps(answer))]
    assert request_analysis(client, 'model', [], log, 'M28', root) == answer
    assert client.responses.create.call_count == 2
    assert client.responses.create.call_args.kwargs['text']['format']['strict'] is True
    assert (root / 'attempt_1.json').exists()
    client.responses.create.side_effect = [SimpleNamespace(output_text='broken') for _ in range(3)]
    with pytest.raises(ValueError, match='No valid classification'):
        request_analysis(client, 'model', [], log, 'M28', root)
