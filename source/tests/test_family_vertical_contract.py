"""INIT/vertical contract regressions, no API or Adobe."""
import copy,json,socket,subprocess,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import family_contract as c
import family_stages as s
import run_all as r

@pytest.fixture
def cfg():return c.read(c.ROOT/'tasks/Katya26/task.json')

def test_single_hero_formats_and_review(cfg):
 c.config(c.ROOT/'tasks/Katya26')
 assert cfg['heroes']==['Катя / Ekaterina']
 assert c.branch_format(cfg,'main')==dict(width=3840,height=2160,fps=25,aspect_ratio='16:9')
 assert c.branch_format(cfg,'short')==dict(width=2160,height=3840,fps=25,aspect_ratio='9:16')
 assert c.branch_review(cfg,'short')['height']==1280
 assert c.branch_review(cfg,'short')['width']==720

@pytest.mark.parametrize('branch',['MAIN','SHORT'])
def test_full_parent_and_cross_parent_rejection(branch):
 c.require_parent({'sequence_name':'Katya26_FULL_MASTER_01'},branch)
 for wrong in ['MAIN','SHORT','WIDE']:
  with pytest.raises(ValueError):c.require_parent({'branch':wrong,'sequence_name':'arbitrary'},branch)

@pytest.mark.parametrize('target,maximum',[(160,175),(80,100)])
def test_configurable_short_no_lower_quota(cfg,tmp_path,target,maximum):
 cfg['family_pipeline'].update(short_target_seconds=target,short_max_seconds=maximum)
 task=tmp_path/'Katya26';task.mkdir();(task/'task.json').write_text(json.dumps(cfg),encoding='utf-8')
 c.config(task,check_paths=False)
 assert c.duration_bounds(cfg,'SHORT')==(.04,maximum)
 cfg['family_pipeline']['short_target_seconds']=maximum+1
 (task/'task.json').write_text(json.dumps(cfg),encoding='utf-8')
 with pytest.raises(ValueError,match='exceeds'):c.config(task,check_paths=False)

def test_art_geometry_unchanged():
 from api.openai_image import PRESERVE_ASPECT_RATIO,GEOMETRY_POLICY
 assert PRESERVE_ASPECT_RATIO is True
 assert GEOMETRY_POLICY=='exif-normalized-reference_native-output-v2'

def test_dry_run_read_only_no_network_no_child(monkeypatch,cfg):
 task=c.ROOT/'tasks/Katya26';before={str(p):c.sha(p) for p in task.rglob('*') if p.is_file()}
 def forbidden(*a,**k):raise AssertionError('External execution forbidden')
 monkeypatch.setattr(socket,'create_connection',forbidden)
 monkeypatch.setattr(subprocess,'Popen',forbidden)
 monkeypatch.setattr(s,'ask',forbidden)
 result=r.run(task,'auto',dry=True)
 assert result['paid_operations']==0 and result['premiere_started'] is False and result['init_accepted']==(task/'pipeline/init_accepted.json').exists()
 assert result['user_input_required']==c.missing_inputs(cfg)
 assert before=={str(p):c.sha(p) for p in task.rglob('*') if p.is_file()}

@pytest.mark.parametrize('mode',['accept-init','auto'])
def test_missing_inputs_block_without_mutation(mode,tmp_path,cfg):
 task=tmp_path/'Katya26';task.mkdir()
 cfg['family_pipeline']['main_target_seconds']=None
 (task/'task.json').write_text(json.dumps(cfg),encoding='utf-8')
 with pytest.raises(ValueError,match='USER_INPUT_REQUIRED'):r.run(task,mode)
 assert not (task/'pipeline/init_accepted.json').exists()

def test_no_previous_hero_in_active_config(cfg):
 import re
 text=json.dumps(cfg,ensure_ascii=False)
 assert not re.search(r'\b(Ben|Max|brothers?|BM26)\b|B&M',text,re.I)
 user_cfg=c.read(cfg['paths']['config'])
 assert Path(cfg['classify']['permanent_project_dir']).resolve()==Path(user_cfg['final_output_dir']).resolve()
 assert Path(user_cfg['final_output_dir']).resolve().is_relative_to(Path(cfg['paths']['source_materials']).resolve())

def test_task_state_in_declared_hero_storage(cfg):
 task=c.task_path(cfg['task_id'])
 assert task.resolve()==(Path(cfg['classify']['permanent_project_dir'])/'tasks'/cfg['task_id']).resolve()

def test_vertical_xml_dimensions(tmp_path):
 import xml.etree.ElementTree as E
 plan=dict(sequence_name='Katya26_SHORT_MASTER_01',width=2160,height=3840,frames=1,clips=[])
 xml=E.fromstring(s.make_xml(plan,plan))
 assert xml.findtext('sequence/media/video/format/samplecharacteristics/width')=='2160'
 assert xml.findtext('sequence/media/video/format/samplecharacteristics/height')=='3840'

def test_vertical_handoff_routes_to_manual_preparation(tmp_path,cfg,monkeypatch):
 task=tmp_path/'Katya26';(task/'pipeline/FULL_PREMIERE_HANDOFF').mkdir(parents=True)
 def write(p,v):p.write_text(json.dumps(v),encoding='utf-8')
 write(task/'pipeline/FULL_PREMIERE_HANDOFF/handoff.json',{'project':'nonexistent.prproj','jobs':[{'sequence':'Katya26_FULL_MASTER_01'}]})
 for b in ['main','short']:write(task/f'pipeline/{b}_plan.json',{'width':2160,'height':3840})
 import family_manual_branches
 calls=[]
 monkeypatch.setattr(family_manual_branches,'prepare',lambda *args:calls.append(args))
 s.prepare_handoff(task,cfg,'BRANCH_PREMIERE_HANDOFF')
 assert len(calls)==1 and not list(tmp_path.rglob('*.prproj'))

def test_neutral_wide_limitations():
 text=(c.ROOT/'scripts/prepare_wide_master.py').read_text(encoding='utf-8')
 assert 'elder/younger' not in text
