import json
import shutil
from pathlib import Path

import pytest

from scripts.cleanup_grok_state import cleanup
from scripts.run_grok_queue import queue_lock
from tests.test_grok_queue import fixture_project
from utils.grok_workspace import digest, record_completed, save_state


def setup_archive(tmp_path):
    settings, image, profile = fixture_project(tmp_path)
    delivery = tmp_path / 'videos'
    delivery.mkdir()
    video = delivery / 'finished.mp4'
    video.write_bytes(b'previously verified video')
    record = record_completed(settings, image, delivery, [video])
    cache = settings.output_dir / '.grok-queues/prompt-cache' / ('a' * 64 + '.json')
    cache.parent.mkdir(parents=True)
    save_state(cache, {'status': 'completed', 'output': json.dumps({'prompt': 'owned prompt'})})
    archive = tmp_path / 'archive/closed'
    archive.mkdir(parents=True)
    plan = archive / 'plan.json'
    save_state(plan, {'config_file': profile['delivery_config'], 'items': [{'prompt': 'owned prompt'}]})
    entries = [{'source': str(settings.output_dir / 'old-plan.json'), 'target': str(plan), 'sha256': digest(plan)}]
    for index, src in enumerate([record, cache]):
        dst = archive / f'copy{index}.json'
        shutil.copy2(src, dst)
        entries.append({'source': str(src), 'target': str(dst), 'sha256': digest(src)})
    manifest = archive / 'manifest.json'
    save_state(manifest, {'status': 'ARCHIVED_AND_CLEANED', 'entries': entries})
    (settings.output_dir / '.grok-queue.lock').write_bytes(b'0')
    return settings, Path(profile['delivery_config']), manifest, record, cache, video


def test_cleanup_preserves_unrelated_output_and_archive(tmp_path):
    settings, config, manifest, record, cache, video = setup_archive(tmp_path)
    valuable = settings.output_dir / 'valuable-project.prproj'
    valuable.write_bytes(b'precious unrelated project')
    result = cleanup(settings, config, manifest, True)
    assert result['metadata_files'] == 2
    assert not record.exists() and not cache.exists()
    assert not (settings.output_dir / '.grok-queue.lock').exists()
    assert valuable.read_bytes() == b'precious unrelated project'
    assert video.exists() and (manifest.parent / 'copy0.json').exists()


def test_dry_run_preserves_metadata(tmp_path):
    settings, config, manifest, record, cache, _ = setup_archive(tmp_path)
    assert cleanup(settings, config, manifest)['metadata_files'] == 2
    assert record.exists() and cache.exists()


@pytest.mark.parametrize('damage', ['archive', 'video'])
def test_damage_blocks_all_deletion(tmp_path, damage):
    settings, config, manifest, record, cache, video = setup_archive(tmp_path)
    (video if damage == 'video' else manifest.parent / 'copy0.json').write_bytes(b'changed')
    with pytest.raises(ValueError):
        cleanup(settings, config, manifest, True)
    assert record.exists() and cache.exists()


def test_live_queue_keeps_shared_cache_and_lock(tmp_path):
    settings, config, manifest, record, cache, _ = setup_archive(tmp_path)
    live = settings.output_dir / '.grok-queues/other-project/plan.json'
    live.parent.mkdir()
    save_state(live, {'queue_complete': False})
    cleanup(settings, config, manifest, True)
    assert not record.exists()
    assert cache.exists() and live.exists()
    assert (settings.output_dir / '.grok-queue.lock').exists()


def test_unknown_metadata_is_never_deleted(tmp_path):
    settings, config, manifest, record, cache, _ = setup_archive(tmp_path)
    unknown = record.parent / ('b' * 64 + '.json')
    unknown.write_text('unlisted valuable data', encoding='utf-8')
    cleanup(settings, config, manifest, True)
    assert unknown.read_text(encoding='utf-8') == 'unlisted valuable data'


def test_mutex_creates_no_output_file(tmp_path):
    path = tmp_path / 'output/.grok-queue.lock'
    with queue_lock(path):
        assert not path.exists()
    assert not path.parent.exists()


def test_active_project_blocks_cleanup(tmp_path):
    settings, config, manifest, record, cache, _ = setup_archive(tmp_path)
    live = settings.output_dir / '.grok-queues/active/plan.json'
    live.parent.mkdir()
    save_state(live, {'config_file': str(config), 'queue_complete': False})
    with pytest.raises(ValueError, match='unfinished queue'):
        cleanup(settings, config, manifest, True)
    assert record.exists() and cache.exists()


def test_mutex_excludes_another_process(tmp_path):
    import subprocess
    import sys
    path = tmp_path / 'output/.grok-queue.lock'
    code = "from pathlib import Path; from scripts.run_grok_queue import queue_lock; import sys\nwith queue_lock(Path(sys.argv[1])): pass"
    with queue_lock(path):
        other = subprocess.run([sys.executable, '-B', '-c', code, str(path)], capture_output=True, text=True)
    assert other.returncode != 0
    assert 'Another Grok queue is already running' in other.stderr
