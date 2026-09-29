"""Synthetic-only ingest tests; never opens the real task project."""
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
from contextlib import contextmanager
import uuid

@contextmanager
def fixture_directory():
 parent=Path(__file__).resolve().parent
 path=parent/("ingest_fixture_"+uuid.uuid4().hex)
 path.mkdir()
 try:yield str(path)
 finally:
  assert path.resolve().parent==parent and path.name.startswith("ingest_fixture_")
  for entry in sorted(path.rglob("*"),key=lambda p:len(p.parts),reverse=True):
   if entry.is_file():entry.unlink()
   else:entry.rmdir()
  path.rmdir()
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('task_ingest',Path(__file__).parents[1]/'scripts/ingest.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
XML='''<Project><Sequence><Name>Fixture</Name><TrackGroups><TrackGroup Index="0"><Second ObjectRef="1"/></TrackGroup></TrackGroups></Sequence>
<Group ObjectID="1"><TrackGroup><Tracks><Track Index="0" ObjectURef="track"/></Tracks></TrackGroup></Group>
<VideoTrack ObjectUID="track"><ClipTrack><ClipItems><TrackItems><TrackItem ObjectRef="2"/><TrackItem ObjectRef="missing"/></TrackItems></ClipItems></ClipTrack></VideoTrack>
<VideoClipTrackItem ObjectID="2"><ClipTrackItem><SubClip ObjectRef="3"/><TrackItem><Start>0</Start><End>254016000000</End></TrackItem></ClipTrackItem></VideoClipTrackItem>
<SubClip ObjectID="3"><Name>Missing image</Name><Clip ObjectRef="4"/></SubClip>
<VideoClip ObjectID="4"><Clip><Source ObjectRef="5"/><InPoint>0</InPoint><OutPoint>254016000000</OutPoint></Clip></VideoClip>
<Source ObjectID="5"><MediaSource><Media ObjectURef="media"/></MediaSource></Source>
<Media ObjectUID="media"><FilePath><LOCAL_PATH></FilePath></Media></Project>'''

class IngestTest(unittest.TestCase):
 def run_fixture(self,check=False,missing=False):
  with fixture_directory() as directory:
   root=Path(directory);task=root/'tasks/T';task.mkdir(parents=True)
   project=root/'fixture.prproj';project.write_bytes(gzip.compress(XML.encode()))
   original=project.read_bytes()
   (task/'task.json').write_text(json.dumps({'task_id':'T','paths':{'premiere_project':str(project)},'sequences':{'source':{'name':'Absent' if missing else 'Fixture'}}}))
   (task/'state.json').write_text(json.dumps({'stage':'INIT','status':'BM INIT COMPLETE','execution_state':'STOP'}))
   with patch.object(m,'ROOT',root),patch('sys.argv',['ingest','T']+(['--check'] if check else [])):
    code=m.main()
   self.assertEqual(project.read_bytes(),original)
   state=json.loads((task/'state.json').read_text());self.assertEqual(state['execution_state'],'STOP')
   self.assertEqual(state['init_snapshot']['stage'],'INIT')
   manifest=task/'ingest/manifest.json'
   if check or missing:self.assertFalse(manifest.exists())
   else:
    data=json.loads(manifest.read_text());self.assertEqual(data['total_items'],2)
    self.assertEqual(data['items'][0]['raw_source_paths']['FilePath'],'<LOCAL_PATH>')
    self.assertEqual(data['items'][0]['media_status'],'MISSING')
    self.assertTrue(data['items'][1]['unresolved_reference'])
   self.assertEqual(code,1 if missing else 0)
   if missing:self.assertEqual(state['ingest']['status'],'FAILED')
 def test_full_manifest(self):self.run_fixture()
 def test_check_no_manifest(self):self.run_fixture(check=True)
 def test_failure(self):self.run_fixture(missing=True)

if __name__=='__main__':unittest.main()
