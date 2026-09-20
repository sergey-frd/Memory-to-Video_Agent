from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import pytest
from tools import hero_video_closeout as a
from tools.workflow_closeout import run


def fixture():
    root=(Path('test_runtime')/('closeout_flow_'+uuid4().hex)).resolve();root.mkdir(parents=True)
    src=root/'output/Ben26';src.mkdir(parents=True)
    (root/'projects').mkdir()
    a.save(root/'hero.json',dict(source_materials_dir=str(root/'media'),regeneration_assets_dir=str(root/'assets'),
        final_videos_dir=str(root/'videos'),premiere_project_dir=str(root/'projects')))
    a.save(root/'workflow.json',dict(schema_version=1,output_root='output/Ben26/workflow'))
    video=src/'final.mp4';video.write_bytes(b'accepted video')
    project=src/'final.prproj';project.write_text('<Project/>')
    a.save(src/'job.json',dict(video=str(video),project=str(project)))
    a.save(src/'workflow/workflow_state.json',dict(status='FINAL_ACCEPTED',finishing={'transitions':dict(job=str(src/'job.json'),completed={str(video):a.sha(video),str(project):a.sha(project)})}))
    (src/'segments').mkdir();(src/'segments/0001.mp4').write_bytes(b'rebuildable')
    (src/'cache.prin').write_bytes(b'cache')
    a.save(root/'closeout.json',dict(task_id='Ben26',hero_config='hero.json',working_dir='output/Ben26',report_dir='reports',
        project_scan_roots=[str(root/'projects'),str(root/'output')],video_config_key='final_videos_dir',project_config_key='premiere_project_dir'))
    return root,src


def test_plan_archive_cleanup_and_repeat():
    root,src=fixture()
    with patch.object(a,'ROOT',root):
        run(root/'closeout.json',root/'workflow.json')
        assert (src/'cache.prin').exists() and not (root/'videos').exists()
        result=run(root/'closeout.json',root/'workflow.json',True)
        assert result['status']=='CLOSED' and not src.exists()
        assert (root/'videos/Ben26/final/final.mp4').read_bytes()==b'accepted video'
        assert not list((root/'assets').rglob('0001.mp4'))
        run(root/'closeout.json',root/'workflow.json',True)


def test_unaccepted_and_new_files_block_deletion():
    root,src=fixture()
    with patch.object(a,'ROOT',root):
        state=src/'workflow/workflow_state.json';data=a.load(state);data['status']='REVIEW_REQUIRED';a.save(state,data)
        with pytest.raises(ValueError,match='FINAL_ACCEPTED'):run(root/'closeout.json',root/'workflow.json',True)
        data['status']='FINAL_ACCEPTED';a.save(state,data)
        run(root/'closeout.json',root/'workflow.json')
        (src/'new-user-file.txt').write_text('retain')
        with pytest.raises(ValueError,match='New files'):run(root/'closeout.json',root/'workflow.json',True)
        assert (src/'final.mp4').exists() and (src/'new-user-file.txt').exists()


def test_resume_after_interrupted_cleanup():
    root,src=fixture()
    with patch.object(a,'ROOT',root):
        original=a.cleanup
        def interrupted(path,apply=False):
            if apply:raise RuntimeError('interrupted')
            return original(path,apply)
        with patch.object(a,'cleanup',side_effect=interrupted):
            with pytest.raises(RuntimeError):run(root/'closeout.json',root/'workflow.json',True)
        assert (root/'reports/cleanup_pending.json').exists()
        # Simulate a file already deleted before interruption.
        (src/'workflow/workflow_state.json').unlink()
        assert run(root/'closeout.json',root/'workflow.json',True)['status']=='CLOSED'
