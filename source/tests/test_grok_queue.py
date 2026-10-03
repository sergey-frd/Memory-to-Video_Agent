import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

from config import Settings
from scripts import run_grok_queue as queue
from scripts import run_grok_prepared_queue as runner
from utils.grok_workspace import digest, record_completed, save_state


def fixture_project(tmp_path):
    settings = Settings(project_root=tmp_path)
    settings.input_dir.mkdir()
    image = settings.input_dir / 'source.png'
    Image.new('RGB', (20, 20), 'blue').save(image)
    config = tmp_path / 'delivery.json'
    save_state(config, {'final_videos_dir': str(tmp_path / 'videos'),
                        'regeneration_assets_dir': str(tmp_path / 'archive')})
    profile = dict(input_dir=str(settings.input_dir), delivery_config=str(config),
                   heroes=['A', 'B'], goal='Family memories', prompt_model='fixture-model')
    return settings, image, profile


def fake_client():
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(status='completed', output_text=json.dumps({
        'prompt': 'Preserve faces, poses and clothing. ' * 8,
        'scene_summary': 'Two children', 'motion_reason': 'Minimal breathing'}))
    return client


def test_prompt_uses_image_and_caches_by_goal(tmp_path):
    settings, image, profile = fixture_project(tmp_path)
    client = fake_client()
    cache = tmp_path / 'cache'
    queue.make_prompt(image, profile, {}, cache, client)
    queue.make_prompt(image, profile, {}, cache, client)
    assert client.responses.create.call_count == 1
    args = client.responses.create.call_args.kwargs
    assert args['input'][0]['content'][1]['image_url'].startswith('data:image/jpeg;base64,')
    profile['goal'] = 'Different goal'
    queue.make_prompt(image, profile, {}, cache, client)
    assert client.responses.create.call_count == 2


def test_alternative_camera_has_separate_prompt_contract_and_cache(tmp_path):
    _,image,profile=fixture_project(tmp_path)
    client=fake_client();cache=tmp_path/'cache'
    queue.make_prompt(image,profile,{},cache,client)
    profile['camera_variant']='alternative_perspective'
    profile['previous_prompts']={digest(image):['Locked camera, minimal breathing.']}
    queue.make_prompt(image,profile,{},cache,client)
    assert client.responses.create.call_count==2
    args=client.responses.create.call_args.kwargs
    assert args['instructions']==queue.ALTERNATIVE_CAMERA_INSTRUCTION
    context=json.loads(args['input'][0]['content'][0]['text'])
    assert context['previous_prompts']==['Locked camera, minimal breathing.']


def test_invalid_model_response_not_rebilled_on_retry(tmp_path):
    _, image, profile = fixture_project(tmp_path)
    client = fake_client()
    client.responses.create.return_value.output_text = '{"prompt":"too short"}'
    for _ in range(2):
        with pytest.raises(ValueError, match='Invalid prompt'):
            queue.make_prompt(image, profile, {}, tmp_path / 'cache', client)
    assert client.responses.create.call_count == 1


def test_content_matching_not_filename(tmp_path):
    _, image, profile = fixture_project(tmp_path)
    bank = tmp_path / 'bank.json'
    save_state(bank, {'media': [{'media_id': 'media-1', 'source_path': str(image), 'scene': {}}]})
    profile['classification_file'] = str(bank)
    index = queue.classification_index(profile)
    renamed = tmp_path / 'renamed.jpg'
    renamed.write_bytes(image.read_bytes())
    assert index[digest(renamed)]['media_id'] == 'media-1'
    Image.new('RGB', (20, 20), 'red').save(image)
    assert digest(image) not in index


def test_dry_run_does_not_request_or_delete(tmp_path):
    settings, image, profile = fixture_project(tmp_path)
    client = fake_client()
    assert queue.prepare('T', profile, settings, dry_run=True, client=client) is None
    assert image.exists()
    assert not settings.output_dir.exists()
    client.responses.create.assert_not_called()


def test_saved_queue_resumes_without_new_model_requests(tmp_path):
    settings, image, profile = fixture_project(tmp_path)
    client = fake_client()
    first = queue.prepare('T', profile, settings, client=client)
    (settings.input_dir / 'new.png').write_bytes(image.read_bytes())
    second = queue.prepare('T', profile, settings, client=client)
    assert first == second
    assert len(queue.read(second)['items']) == 1
    assert client.responses.create.call_count == 1


@pytest.mark.parametrize('remove', [True, False])
def test_completed_queue_archives_and_skips_browser(tmp_path, monkeypatch, remove):
    settings, image, profile = fixture_project(tmp_path)
    profile['remove_completed_inputs'] = remove
    delivery = tmp_path / 'videos'
    delivery.mkdir()
    video = delivery / 'existing.mp4'
    video.write_bytes(b'already verified fixture')
    record_completed(settings, image, delivery, [video])
    unrelated = settings.output_dir / 'unrelated.txt'
    unrelated.write_text('keep')
    client = fake_client()
    plan = queue.prepare('T', profile, settings, client=client)
    client.responses.create.assert_not_called()
    monkeypatch.setattr(runner, 'Settings', lambda: settings)
    monkeypatch.setattr(runner, 'resolve_ffmpeg_executable', lambda: pytest.fail('No browser or FFmpeg needed'))
    runner.run(plan)
    assert image.exists() == (not remove)
    assert unrelated.read_text() == 'keep'
    assert video.read_bytes() == b'already verified fixture'
    copies = list((tmp_path / 'archive').rglob('*_source.png'))
    assert len(copies) == 1


def test_overlapping_input_delivery_rejected(tmp_path):
    settings, image, profile = fixture_project(tmp_path)
    save_state(Path(profile['delivery_config']), {'final_videos_dir': str(image.parent / 'videos'),
                                                'regeneration_assets_dir': str(tmp_path / 'archive')})
    with pytest.raises(ValueError, match='Input must be separate'):
        queue.prepare('T', profile, settings, dry_run=True)


def test_empty_queue_no_model(tmp_path):
    settings, image, profile = fixture_project(tmp_path)
    image.unlink()
    client = fake_client()
    assert queue.prepare('T', profile, settings, client=client) is None
    client.responses.create.assert_not_called()


def test_external_workspace_persisted_and_registry_used_on_resume(tmp_path, monkeypatch):
    settings,image,profile=fixture_project(tmp_path)
    profile['output_dir']=str(tmp_path/'hero'/'work')
    plan=queue.prepare('T',profile,settings,client=fake_client())
    assert plan.is_relative_to(Path(profile['output_dir']))
    assert queue.read(plan)['output_dir']==str(Path(profile['output_dir']).resolve())
    delivery=tmp_path/'videos';delivery.mkdir()
    video=delivery/'done.mp4';video.write_bytes(b'verified fixture')
    record_completed(settings,image,delivery,[video])
    profile_settings=Settings(project_root=tmp_path)
    monkeypatch.setattr(runner,'Settings',lambda:profile_settings)
    monkeypatch.setattr(runner,'resolve_ffmpeg_executable',lambda:pytest.fail('Completed item must not reach browser'))
    runner.run(plan)
    assert profile_settings.output_dir==Path(profile['output_dir']).resolve()


def test_changed_delivered_video_no_longer_skips(tmp_path):
    settings, image, profile = fixture_project(tmp_path)
    delivery = tmp_path / 'videos'
    delivery.mkdir()
    video = delivery / 'existing.mp4'
    video.write_bytes(b'verified')
    record_completed(settings, image, delivery, [video])
    video.write_bytes(b'corrupt')
    client = fake_client()
    plan = queue.prepare('T', profile, settings, client=client)
    assert queue.read(plan)['items'][0]['prompt']
    assert client.responses.create.call_count == 1


def test_failed_source_archive_never_deletes_input(tmp_path, monkeypatch):
    settings, image, profile = fixture_project(tmp_path)
    delivery = tmp_path / 'videos'
    delivery.mkdir()
    video = delivery / 'existing.mp4'
    video.write_bytes(b'verified')
    record_completed(settings, image, delivery, [video])
    plan = queue.prepare('T', profile, settings, client=fake_client())
    monkeypatch.setattr(runner, 'Settings', lambda: settings)
    def fail(*args):
        raise OSError('archive disk failure')
    monkeypatch.setattr(runner, 'archive_source', fail)
    with pytest.raises(OSError, match='archive disk failure'):
        runner.run(plan)
    assert image.exists()
