from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image
from tools import prepare_classification_input as mod


def test_gif_sample_preserves_last_frame_hold(tmp_path):
    source = tmp_path / 'animated.gif'
    frames = [Image.new('RGB', (8, 8), color) for color in ('red', 'blue')]
    frames[0].save(source, save_all=True, append_images=frames[1:], duration=[400, 400], loop=0)
    with mod.gif_frame_at(source, .1) as first, mod.gif_frame_at(source, .72) as last:
        assert first.getpixel((0, 0)) == (255, 0, 0)
        assert last.getpixel((0, 0)) == (0, 0, 255)
    with pytest.raises(ValueError, match='outside source duration'):
        mod.gif_frame_at(source, 1)


@pytest.mark.parametrize(('suffix', 'kind'), [('.jpg', 'image'), ('.GIF', 'video')])
def test_repeated_media_keeps_placements_and_stable_id(suffix, kind):
    from uuid import uuid4
    tmp_path = Path("test_runtime") / ("inventory_" + uuid4().hex)
    tmp_path.mkdir(parents=True)
    source = tmp_path / ('media' + suffix)
    source.write_bytes(b'fixture')
    def item(start):
        return SimpleNamespace(source_path=str(source), name='photo', track_index=0,
                               start=start, end=start + 10, source_in=0, source_out=10)
    with patch.object(mod, 'load_premiere_project_root'), patch.object(mod, 'find_project_sequence_node'), \
         patch.object(mod, 'build_project_object_id_lookup'), patch.object(mod, 'build_project_object_uid_lookup'), \
         patch.object(mod, 'get_project_track_nodes', return_value=[(0, None)]), \
         patch.object(mod, 'iter_project_track_item_refs', return_value=[1, 2]), \
         patch.object(mod, '_track_item_contexts', return_value=[item(0), item(20)]):
        rows = mod.collect(Path('test.prproj'), 'source')
        assert len(rows) == 1
        assert rows[0]['kind'] == kind
        assert [p['start_ticks'] for p in rows[0]['placements']] == [0, 20]
        assert rows[0]['placements'][0]['source_out_ticks'] == 10
        with patch.object(mod, 'digest', side_effect=AssertionError('Unexpected hashing')):
            assert mod.collect(Path('test.prproj'), 'source', hash_media=False)[0]['sha256'] is None
        assert mod.collect(Path('test.prproj'), 'source')[0]['id'] == rows[0]['id']
        source.unlink()
        with pytest.raises(FileNotFoundError):
            mod.collect(Path('test.prproj'), 'source')

    tmp_path.rmdir()
