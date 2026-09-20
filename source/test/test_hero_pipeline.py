import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import pytest
from tools.run_hero_pipeline import run, validate_config
from tools.prepare_classification_input import write_json


def test_placeholders_rejected():
    with pytest.raises(ValueError, match='Fill configuration'):
        validate_config(dict(schema_version=1,task_id='A',hero_config='<pending>'))


def test_checkpoint_resume_and_changed_artifact_rejected():
    root=(Path('test_runtime')/('pipeline_'+uuid4().hex)).resolve();root.mkdir(parents=True)
    (root/'hero.json').write_text('{}');(root/'project.prproj').write_bytes(b'fixture')
    cfg=dict(schema_version=1,task_id='Test',hero_config='hero.json',project='project.prproj',source_sequence='Source',target_sequence='Target',output_root='results',target_duration_seconds=300)
    write_json(root/'config.json',cfg)
    class FakeProcess:
        def __init__(self, command, **kwargs):
            stage=json.loads(Path(command[-1]).read_text());out=Path(stage['output_root'])/'run';out.mkdir(parents=True)
            write_json(out/'classification_input.json',dict(status='READY'))
            write_json(out/'inventory.json',[])
            self.stdout=iter(['fixture run\n'])
        def wait(self):return 0
    with patch('tools.run_hero_pipeline.load_generation_config'), patch('tools.run_hero_pipeline.load_premiere_project_root'), patch('tools.run_hero_pipeline.find_project_sequence_node'), patch('tools.run_hero_pipeline.subprocess.Popen',side_effect=FakeProcess) as proc:
        run(root/'config.json',until='inventory')
        run(root/'config.json',until='inventory')
        assert proc.call_count==1
        artifact=root/'results/inventory/run/inventory.json';artifact.write_text('[1]')
        with pytest.raises(ValueError,match='artifact changed'):
            run(root/'config.json',until='inventory')
        assert not (root/'results/pipeline.lock').exists()
