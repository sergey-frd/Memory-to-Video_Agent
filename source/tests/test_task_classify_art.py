"""Offline contract tests: no real task writes, no network calls."""
import copy
import json
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import Mock,patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import classify_art as ca

class ArtUpdateTest(unittest.TestCase):
 def test_merge_restart_and_validation(self):
  base=Path(__file__).resolve().parent/('classify_art_fixture_'+uuid.uuid4().hex);base.mkdir()
  try:
   root=base/'repo';task=root/'tasks/T';task.mkdir(parents=True)
   native=base/'native';(native/'reports').mkdir(parents=True);(native/'ART').mkdir()
   parent={'media_id':'photo','status':'CLASSIFIED','source_path':'original.jpg','identity':{'category':'UNKNOWN'},'scene':{'summary':'unchanged original'}}
   original={'task_id':'T','media':[parent],'timeline_items':[{'media_id':'photo','ingest_record':{'order':1}}]}
   ca.write_json(native/'reports/classification.json',original)
   config={'task_id':'T','classify':{'permanent_project_dir':str(native),'permanent_relative_dir':'reports'},'classify_art':{'permanent_relative_dir':'with_art','model':'fake'}}
   ca.write_json(task/'task.json',config);ca.write_json(task/'art_config.json',{'permanent_relative_dir':'ART'});ca.write_json(task/'state.json',{'stages':{'CLASSIFY':'COMPLETE','ART':'ART COMPLETE'}})
   entries=[]
   for ident,kind in [('W01','watercolor'),('D01','double_exposure')]:
    output=native/'ART'/f'{ident}.png';Image.new('RGB',(300,300),'blue').save(output)
    entries.append(dict(art_id=ident,art_type=kind,status='COMPLETE',source_media_id='photo',original_source_path='original.jpg',permanent_art_result_path=str(output),result_sha256=ca.digest(output)))
   ca.write_json(native/'ART/art_manifest.json',{'task_id':'T','classification_sha256':ca.digest(native/'reports/classification.json'),'expected':{'watercolor':1,'double_exposure':1},'entries':entries})
   response=dict(status='CLASSIFIED',scene={'summary':'new artwork'},quality={},usefulness={},identity={'category':'UNKNOWN'},semantic_raw={})
   with patch.object(ca,'ROOT',root),patch.object(ca.common.scene,'_get_client',return_value=Mock()),patch.object(ca.common,'analyze_visual',side_effect=lambda *a,**k:copy.deepcopy(response)) as analyze:
    self.assertEqual(ca.run('T',dry_run=True),0);analyze.assert_not_called()
    self.assertFalse((native/'reports/with_art').exists())
    self.assertEqual(ca.run('T'),0);self.assertEqual(analyze.call_count,2)
    pointer=ca.read(task/'classified_media_bank.json');merged=ca.read(Path(pointer['classification_path']))
    self.assertEqual(merged['media'][0],parent);self.assertEqual(merged['timeline_items'],original['timeline_items'])
    self.assertEqual(len(merged['source_families']['photo']),3)
    self.assertEqual(ca.run('T'),0);self.assertEqual(analyze.call_count,2)
    broken=copy.deepcopy(merged);broken['media'][0]['scene']['summary']='changed'
    with self.assertRaises(ValueError):ca.validate_merged(broken,original,entries)
    self.assertEqual(ca.read(native/'reports/classification.json'),original)
    Path(entries[0]['permanent_art_result_path']).write_bytes(b'invalid')
    self.assertEqual(ca.run('T'),1);self.assertEqual(analyze.call_count,2)
  finally:
   assert base.resolve().parent==Path(__file__).resolve().parent and base.name.startswith('classify_art_fixture_')
   for p in sorted(base.rglob('*'),key=lambda p:len(p.parts),reverse=True):
    if p.is_file():p.unlink()
    else:p.rmdir()
   base.rmdir()

 def test_shared_analysis(self):
  client=Mock();directory=Path(__file__).parent
  response=json.dumps({'summary':'sample','people_count':0,'people':[],'quality':{'assessment':'ok'},'usefulness':{'observed_material_value':'texture'}})
  with patch.object(ca.common.scene,'_request_scene_analysis',return_value=response):
   result=ca.common.analyze_visual(client,Path('unused.png'),'fake',directory,[],'single image',{},'image','Describe artwork.')
  self.assertEqual(result['scene']['summary'],'sample');self.assertEqual(result['status'],'CLASSIFIED')

if __name__=='__main__':unittest.main()
