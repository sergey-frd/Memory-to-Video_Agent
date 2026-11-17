"""Project-only handoff and original-only bank regression checks; no Adobe/API."""
import json,sys
import shutil,subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import family_stages as stages
from family_contract import read
from ingest import write_json


def test_zero_art_stages_publish_original_bank(tmp_path,monkeypatch):
 task=tmp_path/'Hero';task.mkdir()
 cfg={'family_pipeline':{'art':{'watercolor_count':0,'double_exposure_count':0}},'classify':{'permanent_project_dir':str(tmp_path/'permanent')}}
 write_json(task/'classify/classification.json',{'task_id':'Hero','media':[]})
 monkeypatch.setattr(stages,'config',lambda _:cfg)
 monkeypatch.setattr(stages,'missing_inputs',lambda _:[])
 monkeypatch.setattr(stages,'art_plan',lambda *a:(_ for _ in ()).throw(AssertionError('ART called')))
 for stage in ['ART_PLAN','ART','ART_CLASSIFY','BANK']:
  stages.run(task,stage)
  stages.stage_outputs(task,cfg,stage)
 assert read(task/'classified_media_bank.json')['new_watercolor']==0
 assert not (task/'art_config.json').exists()


def test_project_only_handoff_has_no_review_preset(tmp_path,monkeypatch):
 import xml.etree.ElementTree as ET
 task=tmp_path/'Hero';task.mkdir();source=tmp_path/'source.prproj';source.write_text('source')
 cfg={'paths':{'premiere_project':str(source)},'sequences':{'source':{'name':'SOURCE'}},'classify':{'permanent_project_dir':str(tmp_path/'permanent')},'family_pipeline':{},'execution_scope':{'review_delivery':'premiere_project_only'}}
 for branch in ['wide','full']:
  write_json(task/'pipeline'/f'{branch}_plan.json',{'sequence_name':branch.upper(),'clips':[]})
 cfg['family_pipeline']['review']={'video_mbps':1.2,'audio_kbps':128}
 monkeypatch.setattr(stages.pp,'load_premiere_project_root',lambda _:ET.Element('Project'))
 monkeypatch.setattr(stages,'make_xml',lambda *a:'<xml/>')
 stages.prepare_handoff(task,cfg,'FULL_PREMIERE_HANDOFF')
 h=read(task/'pipeline/FULL_PREMIERE_HANDOFF/handoff.json')
 assert h['project_only'] and all(j['project_only'] for j in h['jobs'])
 assert not list(tmp_path.rglob('*.epr')) and not list(tmp_path.rglob('*.mp4'))
 assert all(not p.endswith('.epr') for p in h['prepared_artifacts'])
 js=Path(h['jsx']).read_text(encoding='utf-8')
 assert "state('CHECKPOINT_SAVED_MORE_PENDING')" in js
 assert "state('PROJECT_SAVED_REQUIRES_USER_REVIEW')" in js
 if shutil.which('node'):
  result=subprocess.run(['node','--check'],input=js,text=True,capture_output=True)
  assert result.returncode==0,result.stderr


def test_project_only_branch_scripts_parse_without_review_outputs(tmp_path,monkeypatch):
 import xml.etree.ElementTree as ET
 import family_manual_branches as branches
 task=tmp_path/'Hero';task.mkdir();source=tmp_path/'source.prproj';source.write_text('source')
 cfg={'family_pipeline':{'review':{'video_mbps':1.2,'audio_kbps':128}},'classify':{'permanent_project_dir':str(tmp_path/'permanent')},'execution_scope':{'review_delivery':'premiere_project_only'}}
 monkeypatch.setattr(stages.pp,'load_premiere_project_root',lambda _:ET.Element('Project'))
 monkeypatch.setattr(stages.pp,'find_project_sequence_node',lambda root,name:ET.Element('Sequence') if name=='MAIN' else None)
 import utils.premiere_sequence_motion as motion
 monkeypatch.setattr(motion,'_track_item_contexts',lambda *a,**k:[])
 monkeypatch.setattr(branches,'make_xml',lambda *a:'<xml/>')
 plans=[dict(branch=b,sequence_name=b,source_sequence='FULL',width=w,height=h,clips=[]) for b,w,h in [('MAIN',3840,2160),('SHORT',2160,3840)]]
 h=branches.prepare(task,cfg,source,plans)
 assert h['project_only'] and Path(h['jsx']).name=='verify_and_save.jsx'
 assert not list(tmp_path.rglob('*.epr')) and not list(tmp_path.rglob('*.mp4'))
 for path in [h['jsx'],h['assembly_jsx']]:
  js=Path(path).read_text(encoding='utf-8')
  if shutil.which('node'):
   result=subprocess.run(['node','--check'],input=js,text=True,capture_output=True)
   assert result.returncode==0,result.stderr


def test_project_only_resume_validates_saved_project_without_ffmpeg(tmp_path,monkeypatch):
 import xml.etree.ElementTree as ET
 source=tmp_path/'source';source.write_text('source');project=tmp_path/'work';project.write_text('saved')
 folder=tmp_path/'native';folder.mkdir();(folder/'native_status.txt').write_text('PROJECT_SAVED_REQUIRES_USER_REVIEW')
 pending={'prepared_artifacts':{},'source_project':str(source),'source_sha256':stages.sha(source),'project':str(project),'jsx':str(folder/'run.jsx'),'project_only':True,'stage':'FULL_PREMIERE_HANDOFF','expected_result':['FULL'],'jobs':[{'sequence':'FULL','plan':{'clips':[]}}]}
 monkeypatch.setattr(stages.pp,'load_premiere_project_root',lambda _:ET.Element('Project'))
 monkeypatch.setattr(stages.pp,'find_project_sequence_node',lambda *a:ET.Element('Sequence'))
 monkeypatch.setattr(stages,'_track_item_contexts',lambda *a,**k:[])
 monkeypatch.setattr(stages,'resolve_ffmpeg_executable',lambda:(_ for _ in ()).throw(AssertionError('FFmpeg called')))
 outputs=stages.resume_handoff(tmp_path,{},pending)
 assert read(outputs[0])['status']=='SAVED_PROJECT_STRUCTURE_PASS'
 assert read(outputs[0])['human_audiovisual_review']=='PENDING'
 assert not read(outputs[0])['native_render_verified']
