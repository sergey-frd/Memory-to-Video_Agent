"""Exercise accumulation and resume using a fake worker; no network or real AI."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4
from PIL import Image
from tools import integrate_art as m


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root=Path('output/test_art_integration')/uuid4().hex;self.root.mkdir(parents=True)
        self.root=self.root.resolve();self.package=self.root/'package';self.package.mkdir();(self.package/'previews').mkdir();(self.package/'context').mkdir()
        self.original=self.root/'original.png';Image.new('RGB',(256,256),'blue').save(self.original)
        Image.new('RGB',(256,256),'blue').save(self.package/'previews/original.png')
        self.human=self.root/'human.txt';self.human.write_text('Творчество и поддержка.',encoding='utf-8')
        self.hero=self.root/'hero.json';m.write_json(self.hero,dict(human_detail_txt=str(self.human)))
        self.row=dict(id='M1',path=str(self.original),sha256=m.digest(self.original),kind='image',placements=[],previews=[dict(path='previews/original.png')])
        m.write_json(self.package/'inventory.json',[self.row]);m.write_json(self.package/'classification_input.json',dict(status='READY',schema_version=1,inventory='inventory.json',context={}))
        base=self.root/'base';base.mkdir();r=dict(self.row,description='unchanged original',themes=[],style='photo',quality_notes=[],uncertainties=[],decision='REVIEW')
        (base/'catalog.jsonl').write_text(json.dumps(r)+'\n',encoding='utf-8');self.original_catalog=(base/'catalog.jsonl').read_bytes()
        self.base=base/'classification_result.json';m.write_json(self.base,dict(status='CLASSIFIED_REVIEW_REQUIRED',completed=1,total=1,input_manifest=str(self.package/'classification_input.json')))
        self.calls=0

    def config(self,name,base,count):
        art=self.root/name;art.mkdir();f=art/'art.png';Image.new('RGB',(256,256), 'red' if name=='WC' else 'green').save(f)
        h=m.digest(f);m.write_json(art/'actual_pairs.json',[dict(pair_id=name,source_id='M1',original_path=str(self.original),original_sha256=m.digest(self.original),output_path=str(f),output_sha256=h,width=256,height=256)])
        m.write_json(art/'generation_result.json',dict(status='GENERATED_REVIEW_REQUIRED',expected_count=1))
        m.write_json(art/'generation_state.json',dict(items={name:dict(status='GENERATED_REVIEW_REQUIRED',output_sha256=h,dimensions=[256,256])}))
        cfg=dict(schema_version=1,base_classification_result=str(base),expected_base_count=count,expected_new_count=1,art_directory=str(art),art_style=name,hero_config=str(self.hero),output_root=str(self.root/(name+'_out')),model='fake',network_timeout_seconds=1,hard_deadline_seconds=2)
        p=self.root/(name+'.json');m.write_json(p,cfg);return p

    def fake_worker(self,cmd,*args):
        self.calls+=1;r=m.read(cmd[-1]);analysis=dict(description='Visible finished artwork',themes=['family'],style='ART',quality_notes=[],uncertainties=[],hero_qualities=['support'],second_layer='light',source_fidelity='pose preserved',montage_reason='meaningful alternative',suitability='suitable')
        m.write_json(Path(r['response_file']),dict(status='completed',response_id='fake',output_text=json.dumps(analysis)));return 0

    def invoke(self,p,dry=False):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-no-network'}),patch.object(m,'wait_worker',self.fake_worker):m.run(p,dry)

    def test_accumulate_resume_preserve_original_and_contract(self):
        wc=self.config('WC',self.base,1);self.invoke(wc);self.invoke(wc);self.assertEqual(self.calls,1)
        first=self.root/'WC_out/classification/classification_result.json'
        de=self.config('DE',first,2);self.invoke(de);self.assertEqual(self.calls,2)
        cat=self.root/'DE_out/classification/catalog.jsonl';self.assertTrue(cat.read_bytes().startswith(self.original_catalog))
        rows=[json.loads(s) for s in cat.read_text(encoding='utf-8').splitlines()];self.assertEqual(len(rows),3);self.assertEqual(len({r['id'] for r in rows}),3)
        self.assertEqual(rows[-1]['art_source_id'],'M1');self.assertIn('оригинал M1',rows[-1]['description'])
        self.assertEqual(m.read(cat.parent/'classification_result.json')['total'],3)

    def test_dry_run_does_not_create_combined_result(self):
        p=self.config('WC',self.base,1);self.invoke(p,True);self.assertEqual(self.calls,0)
        self.assertFalse((self.root/'WC_out/classification').exists())

    def test_unknown_request_not_repeated(self):
        p=self.config('WC',self.base,1)
        def fail(*args):raise TimeoutError('simulated')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake'}),patch.object(m,'wait_worker',fail),self.assertRaises(TimeoutError):m.run(p)
        with self.assertRaisesRegex(ValueError,'Unresolved request'):self.invoke(p)
        self.assertEqual(self.calls,0)

    def test_invalid_art_hash_stops_before_api(self):
        p=self.config('WC',self.base,1);(self.root/'WC/art.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):self.invoke(p)
        self.assertEqual(self.calls,0)

    def test_future_second_stage_dry_run_is_explicitly_waiting(self):
        wc=self.config('WC',self.base,1);de=self.config('DE',self.root/'WC_out/classification/classification_result.json',2)
        cfg=m.read(de);cfg['predecessor_config']=str(wc);m.write_json(de,cfg);self.invoke(de,True)
        self.assertEqual(m.read(self.root/'DE_out/dry_run_result.json')['status'],'VALIDATED_INPUTS_WAITING_PREVIOUS')

    def test_hard_deadline_terminates_local_worker(self):
        log=m.Log(self.root/'timeout.log')
        with self.assertRaises(TimeoutError):
            m.wait_worker([sys.executable,'-c','import time;time.sleep(5)'],self.root/'worker.log',log,'fake local wait',0.1)


if __name__=='__main__':unittest.main()
