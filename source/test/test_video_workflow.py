import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import pytest
from tools.run_video_workflow import run, read, native_output
from tools.prepare_classification_input import write_json, digest


def fixture():
    root=(Path('test_runtime')/('workflow_'+uuid4().hex)).resolve();root.mkdir(parents=True)
    clip=dict(id='p',kind='image',path='image.jpg',sha256='x',source_in_seconds=0,source_out_seconds=None,
              frames=250,duration_seconds=10,timeline_start_frame=0)
    plan=dict(schema_version=1,status='DRAFT',fps=25,frames=250,duration_seconds=10,clips=[clip])
    write_json(root/'edit_plan.json',plan);(root/'draft.mp4').write_bytes(b'mock-video')
    write_json(root/'render_result.json',dict(status='DRAFT_READY',video=str(root/'draft.mp4'),sha256=digest(root/'draft.mp4')))
    cfg=dict(schema_version=1,output_root='state',initial_review_result='render_result.json',
             native=dict(project='source.prproj',target_sequence='target',preset='preset.epr'),
             animation=dict(enabled=False),transitions=dict(enabled=False))
    write_json(root/'config.json',cfg)
    return root,root/'config.json'


def test_start_resume_approval_and_tampering():
    root,cfg=fixture()
    run(cfg,'start');run(cfg,'start')
    assert len(read(root/'state/workflow_state.json')['reviews'])==1
    with pytest.raises(ValueError,match='approve'):run(cfg,'finish')
    run(cfg,'approve')
    (root/'draft.mp4').write_bytes(b'changed')
    with pytest.raises(ValueError,match='artifact changed'):run(cfg,'finish')
    assert not (root/'state/workflow.lock').exists()


def test_revision_invalidates_approval_and_resumes_render_failure():
    root,cfg=fixture();run(cfg,'start');run(cfg,'approve')
    rc=root/'revision.json'
    write_json(rc,dict(schema_version=1,revision='v2',source_edit_plan='edit_plan.json',output_dir='revision',
                       timeline_ranges=[dict(in_seconds=1,out_seconds=8)]))
    with patch('tools.run_video_workflow.render',side_effect=RuntimeError('render failed')):
        with pytest.raises(RuntimeError):run(cfg,'revise',rc)
    assert (root/'revision/edit_plan.json').exists()
    with pytest.raises(ValueError,match='pending'):run(cfg,'approve')
    def fake_render(path):
        c=read(path);out=Path(c['output_root'])/'run';out.mkdir(parents=True)
        write_json(out/'edit_plan.json',read(c['edit_plan']));(out/'draft.mp4').write_bytes(b'revision')
        write_json(out/'render_result.json',dict(status='DRAFT_READY',video=str(out/'draft.mp4'),sha256=digest(out/'draft.mp4')))
        return dict(output=str(out))
    with patch('tools.run_video_workflow.render',side_effect=fake_render):run(cfg,'revise',rc)
    state=read(root/'state/workflow_state.json')
    assert len(state['reviews'])==2 and 'approval' not in state and 'pending_revision' not in state
    assert read(state['reviews'][-1]['plan'])['frames']==175


def test_native_wait_resume_and_final_acceptance():
    root,cfg=fixture();run(cfg,'start');run(cfg,'approve')
    def prepare(path,dry_run=False):
        if dry_run:return
        c=read(path);out=Path(c['output_root'])/'run';out.mkdir(parents=True)
        write_json(out/'job.json',dict(plan=read(c['edit_plan']),project=str(out/'project.prproj'),video=str(out/'final.mp4')))
        (out/'assemble_export.jsx').write_text('mock')
    with patch('tools.run_video_workflow.native',side_effect=prepare) as prep,patch('tools.run_video_workflow.native_output',return_value=None):
        state=run(cfg,'finish');assert state['status']=='WAITING_PREMIERE_NATIVE'
        run(cfg,'finish');assert prep.call_count==2  # dry-run + preparation, not duplicated on resume
    job=read(state['finishing']['native']['job']);Path(job['video']).write_bytes(b'final')
    with patch('tools.run_video_workflow.native_output',return_value={job['video']:digest(job['video'])}):
        state=run(cfg,'finish')
    assert state['status']=='FINAL_REVIEW_REQUIRED'
    assert state['finishing']['animation']['skipped']=='disabled in configuration'
    assert run(cfg,'accept')['status']=='FINAL_ACCEPTED'


def test_native_rejects_export_older_than_project():
    root,_=fixture();video=root/'out.mp4';project=root/'p.prproj';video.write_bytes(b'v');project.write_bytes(b'p')
    os.utime(video,(100,100));os.utime(project,(200,200))
    write_json(root/'job.json',dict(video=str(video),project=str(project)))
    (root/'native_status.txt').write_text('EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA')
    with pytest.raises(ValueError,match='saved after'):native_output(root/'job.json',None)


def test_adopt_renamed_review_requires_original_checksum():
    root,cfg=fixture()
    (root/'draft.mp4').rename(root/'renamed.mp4')
    c=read(cfg);c['initial_review_video']='renamed.mp4';write_json(cfg,c)
    assert run(cfg,'start')['reviews'][0]['video']==str(root/'renamed.mp4')
    assert run(cfg,'start')['status']=='REVIEW_REQUIRED'
