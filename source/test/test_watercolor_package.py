"""Local fake-CLI tests. No OpenAI API calls or real generated artwork."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('watercolor_runner', ROOT/'tools/run_watercolor_package.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class WatercolorTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT/'output/test_watercolor'/uuid4().hex
        self.root.mkdir(parents=True)
        cli = self.root/'fake_cli.py'
        cli.write_text("import sys\nfrom pathlib import Path\nfrom PIL import Image\nif '--dry-run' not in sys.argv:\n p=Path(sys.argv[sys.argv.index('--out')+1]);Image.new('RGB',(256,256),'white').save(p)\n",encoding='utf-8')
        self.cfg=dict(schema_version=1,expected_count=2,output_root=str(self.root/'results'),imagegen_cli=str(cli),imagegen_cli_sha256=runner.digest(cli),env_file=str(self.root/'no.env'),model='fake',size='256x256',quality='high',items=[])
        for i in range(2):
            ref=self.root/f'ref{i}.png';Image.new('RGB',(256,256),(i*30,20,10)).save(ref)
            prompt=self.root/f'prompt{i}.txt';prompt.write_text('test',encoding='utf-8')
            self.cfg['items'].append(dict(pair_id=f'W{i}',source_id=f'M{i}',original_path=str(ref),original_sha256=runner.digest(ref),reference_path=str(ref),reference_sha256=runner.digest(ref),prompt_path=str(prompt),prompt_sha256=runner.digest(prompt),planned_output=str(self.root/'results'/f'W{i}.png')))
        self.path=self.root/'config.json';runner.write_json(self.path,self.cfg)

    def invoke(self, dry=False):
        with patch.dict(os.environ, {'OPENAI_API_KEY':'fake-no-network'}):
            runner.run(self.path,dry)

    def test_dry_run_creates_no_outputs_or_actual_pairs(self):
        self.invoke(True)
        self.assertFalse((self.root/'results/actual_pairs.json').exists())
        self.assertFalse((self.root/'results/W0.png').exists())

    def test_resume_skips_completed_and_preserves_actual_pairs(self):
        self.invoke()
        with patch.object(runner.subprocess,'Popen',side_effect=AssertionError('duplicate request')):
            self.invoke()
        pairs=json.loads((self.root/'results/actual_pairs.json').read_text())
        self.assertEqual(len(pairs),2)
        self.assertTrue(all(Path(p['output_path']).exists() for p in pairs))

    def test_uncertain_request_is_not_repeated(self):
        out=self.root/'results';out.mkdir()
        runner.write_json(out/'generation_state.json',dict(config_sha256=runner.digest(self.path),items={'W0':{'status':'REQUEST_STARTED'}}))
        with self.assertRaisesRegex(ValueError,'outcome unknown'):
            self.invoke()

    def test_saved_output_recovered_without_request(self):
        self.invoke()
        p=self.root/'results/generation_state.json';state=json.loads(p.read_text());state['items']['W0']={'status':'REQUEST_STARTED'};runner.write_json(p,state)
        with patch.object(runner.subprocess,'Popen',side_effect=AssertionError('duplicate request')):
            self.invoke()

    def test_changed_original_stops_before_api(self):
        Path(self.cfg['items'][0]['original_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Changed original_path'):
            self.invoke()


if __name__ == '__main__':
    unittest.main()
