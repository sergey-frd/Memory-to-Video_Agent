"""Cheap orchestration tests: no network, no Adobe, no real media generation."""
import copy,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import family_contract as contract
import family_stages as stages
import run_all as runner

def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v),encoding='utf-8')

@pytest.fixture
def rig(tmp_path,monkeypatch):
 task=tmp_path/'HERO';task.mkdir();project=tmp_path/'source.prproj';project.write_text('source')
 cfg={'family_pipeline':{'run_enabled':True},'paths':{'premiere_project':str(project)},'classify':{'permanent_project_dir':str(tmp_path/'permanent')}}
 write(task/'task.json',cfg)
 monkeypatch.setattr(runner,'config',lambda _:cfg);monkeypatch.setattr(stages,'preflight',lambda *args:None)
 def outputs(task,cfg,stage):
  return [task/'pipeline'/stage/('handoff.json' if stage.endswith('HANDOFF') else 'result.json')]
 monkeypatch.setattr(stages,'stage_outputs',outputs)
 calls=[]
 def executor(stage,args):
  calls.append(stage);write(outputs(task,cfg,stage)[0],{'stage':stage,'status':'USER_ACTION_REQUIRED' if stage.endswith('HANDOFF') else 'PASS'})
 monkeypatch.setattr(stages,'resume_handoff',lambda t,c,h:outputs(t,c,h['stage']))
 runner.run(task,'accept-init')
 return task,cfg,calls,executor

def test_auto_to_handoff_then_independent_branches(rig):
 task,cfg,calls,execute=rig
 s=runner.run(task,'auto',executor=execute)
 assert s['status']=='USER_ACTION_REQUIRED' and s['stage']=='FULL_PREMIERE_HANDOFF'
 assert calls==contract.STAGES[:9]
 before=list(calls);runner.run(task,'auto',executor=execute);assert calls==before
 s=runner.run(task,'resume',executor=execute)
 assert s['stage']=='BRANCH_PREMIERE_HANDOFF' and calls[-2:]==['BRANCH_PLANS','BRANCH_PREMIERE_HANDOFF']
 s=runner.run(task,'resume',executor=execute)
 assert s['stage']=='STRUCTURE_REVIEW' and s['status']=='USER_ACTION_REQUIRED'
 assert calls.count('ART')==1 and calls.count('CLASSIFY')==1

def test_failure_and_resume_never_repeat_paid_success(rig):
 task,cfg,calls,execute=rig
 def failing(stage,args):
  if stage=='FULL_PLAN':raise RuntimeError('synthetic failure')
  execute(stage,args)
 s=runner.run(task,'auto',executor=failing)
 assert s['status']=='FAILED' and s['last_success']=='WIDE_PLAN'
 assert Path(s['diagnostic']).exists() and not (task/'pipeline/run.lock').exists()
 s=runner.run(task,'resume',executor=execute)
 assert s['status']=='USER_ACTION_REQUIRED' and calls.count('ART')==1

def test_corrupt_checkpoint_stops_instead_of_regenerating(rig):
 task,cfg,calls,execute=rig;runner.run(task,'auto',executor=execute)
 (task/'pipeline/ART/result.json').write_text('corrupt')
 before=list(calls);s=runner.run(task,'resume',executor=execute)
 assert s['status']=='FAILED' and calls==before

def test_from_cannot_skip_dependencies(rig):
 task,cfg,calls,execute=rig;s=runner.run(task,'auto',start_stage='ART',executor=execute)
 assert s['status']=='FAILED' and calls==[]

def test_dry_run_status_no_mutation_or_ai(rig):
 task,cfg,calls,execute=rig
 before=sorted(str(p) for p in task.rglob('*'));runner.run(task,'auto',dry=True);runner.run(task,'status')
 assert sorted(str(p) for p in task.rglob('*'))==before and calls==[]

def test_exclusive_lock(rig):
 task,cfg,calls,execute=rig;(task/'pipeline/run.lock').write_text('owned by another process')
 with pytest.raises(FileExistsError):runner.run(task,'auto',executor=execute)
 assert (task/'pipeline/run.lock').read_text()=='owned by another process' and not calls

def test_single_multi_hero_schema_and_targets():
 cfg=contract.read(contract.ROOT/'config_family_task.example.json');cfg['paths']={'premiere_project':'actual','source_materials':'actual'};cfg['sequences']['source']['name']='source';cfg['classify']['permanent_project_dir']='actual'
 schema=contract.read(contract.ROOT/'scripts/schemas/family_task.schema.json')
 contract.validate_schema(cfg,schema)
 cfg['heroes']=['One','Two'];contract.validate_schema(cfg,schema)
 cfg['family_pipeline']['main_target_seconds']=180;contract.validate_schema(cfg,schema)
 cfg['heroes']=[]
 with pytest.raises(ValueError):contract.validate_schema(cfg,schema)

def test_branch_lineage_and_speed_mapping(tmp_path,monkeypatch):
 cfg={'heroes':['Person'],'intent':'A life','family_pipeline':{'main_target_seconds':2,'main_target_range':[1,3],'short_target_seconds':1,'short_target_range':[1,2]}}
 source={'sequence_name':'HERO_FULL_MASTER_01','clips':[{'id':'FM001','kind':'video','path':'unchanged.mp4','source_in_seconds':10,'source_out_seconds':14,'duration_seconds':2,'speed':2}]}
 monkeypatch.setattr(stages,'ask',lambda *args:{'decisions':[{'id':'FM001','reason':'Human moment','segments':[{'in_seconds':.48,'out_seconds':1.48,'speed':1}]}],'warnings':[]})
 for branch in ['MAIN','SHORT']:
  result=stages.edit_plan(tmp_path,cfg,source,branch)
  assert result['source_sequence']=='HERO_FULL_MASTER_01'
  assert result['clips'][0]['source_in_seconds']==pytest.approx(10.96) and result['clips'][0]['source_out_seconds']==pytest.approx(12.96)
  assert result['clips'][0]['speed']==2

def test_resume_rejects_missing_native_result(tmp_path):
 with pytest.raises(ValueError,match='native status'):
  stages.resume_handoff(tmp_path,{},dict(prepared_artifacts={},source_project=str(__file__),source_sha256=contract.sha(__file__),project=str(tmp_path/'no.prproj'),jsx=str(tmp_path/'run.jsx')))

def test_art_single_hero_and_zero_counts(tmp_path):
 import art
 entries=art.build_plan(tmp_path,{'selections':[],'watercolor_count':0,'double_exposure_count':0},{'media':[]},tmp_path/'permanent')
 assert entries==[]

def test_schema_invalid_range_length():
 schema=contract.read(contract.ROOT/'scripts/schemas/family_task.schema.json')['properties']['family_pipeline']['properties']['main_target_range']
 with pytest.raises(ValueError):contract.validate_schema([1,2,3],schema)

def test_plan_readback_rejects_source_overrun(tmp_path,monkeypatch):
 cfg={'heroes':['Person'],'intent':'Portrait','family_pipeline':{}}
 parent={'sequence_name':'HERO_WIDE_MASTER_01','clips':[dict(id='WM001',kind='video',path='source.mp4',source_in_seconds=0,source_out_seconds=2,duration_seconds=2,speed=1)]}
 monkeypatch.setattr(stages,'ask',lambda *a:dict(decisions=[dict(id='WM001',reason='Retain action',segments=[dict(in_seconds=0,out_seconds=2,speed=1)])],warnings=[]))
 plan=stages.edit_plan(tmp_path,cfg,parent,'FULL');stages.validate_edit_plan(plan,parent,tmp_path,cfg,'FULL')
 plan['clips'][0]['source_out_seconds']=3
 with pytest.raises(ValueError,match='bounds'):stages.validate_edit_plan(plan,parent,tmp_path,cfg,'FULL')

def test_handoff_reuses_xml_native_engine_without_adobe(tmp_path,monkeypatch):
 import xml.etree.ElementTree as ET
 task=tmp_path/'HERO';project=tmp_path/'source.prproj';project.write_bytes(b'protected source')
 media=tmp_path/'photo.jpg';media.write_bytes(b'placeholder; metadata mocked')
 cfg={'paths':{'premiere_project':str(project)},'sequences':{'source':{'name':'ORIGINAL'}},'classify':{'permanent_project_dir':str(tmp_path/'permanent')},'family_pipeline':{'review':{'video_mbps':1.2,'audio_kbps':128}}}
 clip=dict(id='WM001',wide_item='WM001',media_id='image1',path=str(media),kind='image',speed=1,source_in_seconds=0,source_out_seconds=2,frames=50,timeline_start_frame=0,duration_seconds=2)
 for branch in ['wide','full']:
  write(task/f'pipeline/{branch}_plan.json',dict(task_id='HERO',sequence_name=f'HERO_{branch.upper()}_MASTER_01',frames=50,duration_seconds=2,clips=[clip]))
 monkeypatch.setattr(stages.pp,'load_premiere_project_root',lambda _:ET.Element('Project'))
 monkeypatch.setattr(stages,'metadata',lambda _:dict(width=2000,height=1200,channels=0))
 stages.prepare_handoff(task,cfg,'FULL_PREMIERE_HANDOFF')
 hand=contract.read(task/'pipeline/FULL_PREMIERE_HANDOFF/handoff.json')
 assert hand['status']=='USER_ACTION_REQUIRED' and project.read_bytes()==b'protected source'
 assert hand['jobs'][0]['source_sequence']=='ORIGINAL' and hand['jobs'][1]['source_sequence']=='HERO_WIDE_MASTER_01'
 for job in hand['jobs']:assert ET.parse(job['xml']).findtext('sequence/name')==job['sequence']
 script=Path(hand['jsx']).read_text()
 assert '__JOB__' not in script and '__HELPERS__' not in script and 'BM26' not in script
 contract.verify(hand['prepared_artifacts'])

def test_frozen_task_never_executes(rig):
 task,cfg,calls,execute=rig;cfg['family_pipeline']['run_enabled']=False
 with pytest.raises(ValueError,match='frozen'):runner.run(task,'auto',executor=execute)
 assert calls==[]

def test_native_monitor_reports_export_without_claiming_qa(tmp_path):
 folder=tmp_path/'native';folder.mkdir();(folder/'native_status.txt').write_text('EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA')
 write(tmp_path/'pipeline/state.json',{'handoff':{'jsx':str(folder/'assemble.jsx')}})
 result=runner.run(tmp_path,'watch')
 assert result['status']=='USER_ACTION_REQUIRED' and result['native_status']=='EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA'
