"""ART contract tests with a fake backend; no external requests or real task writes."""
import json
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import art

class ArtContract(unittest.TestCase):
 def test_restart_delivery_and_conflicts(self):
  base=Path(__file__).resolve().parent/('art_fixture_'+uuid.uuid4().hex);base.mkdir()
  try:
   root=base/'repo';task=root/'tasks/T';task.mkdir(parents=True);(task/'classify').mkdir()
   permanent=base/'native';permanent.mkdir()
   source=base/'original.JPEG';Image.new('RGB',(320,320),'blue').save(source)
   classified={'media':[{'media_id':'x','source_path':str(source),'status':'CLASSIFIED','scene':{'summary':'two children'},'quality':{},'identity':{'category':'UNKNOWN'}}]}
   art.write_json(task/'classify/classification.json',classified)
   config=dict(task_id='T',watercolor_count=1,double_exposure_count=1,generation_backend='openai_api',model='gpt-image-1.5',heartbeat_seconds=12,permanent_project_dir=str(permanent),permanent_relative_dir='ART',classification_sha256=art.digest(task/'classify/classification.json'),selections=[dict(art_type=k,source_media_id='x',artistic_rationale='fixture',background_concept='trees' if k=='double_exposure' else None) for k in art.TYPES])
   art.write_json(task/'art_config.json',config);art.write_json(task/'task.json',{'task_id':'T'});art.write_json(task/'state.json',{'stages':{'CLASSIFY':'COMPLETE'}})
   def fake_edit(source,style,dest,*args,**kwargs):
    dest.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(320,320),'red').save(dest);return dest
   with patch.object(art,'ROOT',root),patch.object(art,'edit_image_with_openai',side_effect=fake_edit) as backend:
    self.assertEqual(art.run('T',dry_run=True),0);backend.assert_not_called();self.assertFalse((task/'art').exists())
    self.assertEqual(art.run('T',prepare=True),0);backend.assert_not_called()
    plan=json.loads((task/'art/art_manifest.json').read_text());self.assertEqual([e['art_id'] for e in plan['entries']],['W01','D01'])
    self.assertTrue(all(e['cross_style_overlap'] for e in plan['entries']))
    self.assertTrue(Path(plan['entries'][0]['source_copy_path']).name=='source_01.JPEG')
    self.assertEqual(art.run('T'),0);self.assertEqual(backend.call_count,2)
    self.assertEqual(art.run('T'),0);self.assertEqual(backend.call_count,2)
    self.assertEqual(art.digest(task/'art/art_manifest.json'),art.digest(permanent/'ART/art_manifest.json'))
    ready=json.loads((task/'art/art_manifest.json').read_text())
    result=Path(ready['entries'][0]['art_result_path']);result.write_bytes(b'broken')
    self.assertEqual(art.run('T'),1);self.assertEqual(backend.call_count,2)
    self.assertEqual(art.digest(source),ready['entries'][0]['source_sha256'])
  finally:
   assert base.resolve().parent==Path(__file__).resolve().parent and base.name.startswith('art_fixture_')
   for p in sorted(base.rglob('*'),key=lambda p:len(p.parts),reverse=True):
    if p.is_file():p.unlink()
    else:p.rmdir()
   base.rmdir()

if __name__=='__main__':unittest.main()
