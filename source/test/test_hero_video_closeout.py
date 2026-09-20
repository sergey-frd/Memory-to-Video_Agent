import contextlib
import gzip
import io
import json
import shutil
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import hero_video_closeout as c


class CloseoutTests(unittest.TestCase):
    def setUp(self):
        self.test_base = Path(__file__).resolve().parents[1] / 'tmp/closeout_tests'
        self.root = self.test_base / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.patch = patch.object(c, 'ROOT', self.root)
        self.patch.start()
        self.source = self.root / 'output/TASK999'
        self.source.mkdir(parents=True)
        self.projects = self.root / 'projects'
        self.projects.mkdir()
        self.photo = self.source / 'artwork/photo.png'
        self.photo.parent.mkdir()
        self.photo.write_bytes(b'precious image')
        (self.source / 'cache.prin').write_bytes(b'rebuildable')
        self.catalog = self.source / '01_CLASSIFICATION/catalog.json'
        c.save(self.catalog, {'path': str(self.photo), 'manual': 'keep this description'})
        self.project = self.source / 'draft.prproj'
        self.project.write_bytes(gzip.compress(b'<Project><Media><FilePath>artwork/photo.png</FilePath></Media><Scale>120</Scale></Project>'))
        self.final = self.projects / 'final.mp4'
        self.final.write_bytes(b'accepted video')
        c.save(self.root / 'hero.json', {'source_materials_dir': str(self.root / 'archive'),
                                      'premiere_project_dir': str(self.projects)})
        self.job = self.root / 'job.json'
        c.save(self.job, {'task_id': 'TASK999', 'hero_config': 'hero.json', 'working_dir': 'output/TASK999',
                         'project_scan_roots': [str(self.projects), 'output'],
                         'storage': {name: {'config_key': 'source_materials_dir', 'relative': name} for name in
                                     ('classification', 'artwork', 'history', 'provenance', 'metadata')},
                         'retained_results': [{'config_key': 'premiere_project_dir', 'relative': 'final.mp4', 'role': 'video'}]})
        self.plan = self.root / 'reports/plan.json'

    def tearDown(self):
        self.patch.stop()
        assert self.root.resolve().is_relative_to(self.test_base.resolve())
        shutil.rmtree(self.root)

    def prepare(self):
        c.make_plan(self.job, self.plan)
        c.archive(self.plan)

    def test_relocation_integrity_dry_run_cleanup_and_repeat(self):
        self.prepare()
        manifest = c.load(self.root / 'archive/metadata/archive_manifest.json')
        cat = next(x for x in manifest['files'] if x['source'] == str(self.catalog))
        self.assertEqual(c.load(cat['destination'])['manual'], 'keep this description')
        self.assertEqual(c.load(cat['original_copy'])['path'], str(self.photo))
        self.assertNotEqual(c.load(cat['destination'])['path'], str(self.photo))
        c.cleanup(self.plan)
        self.assertTrue(self.photo.exists())
        result = c.cleanup(self.plan, apply=True)
        self.assertEqual(len(result['deleted']), 4)
        self.assertFalse(self.source.exists())
        c.verify(self.plan)
        c.archive(self.plan)
        self.assertEqual(c.cleanup(self.plan, apply=True)['candidate_files'], 0)
        runs = [c.load(p) for p in (self.plan.parent / 'cleanup_runs').glob('*.json')]
        self.assertTrue(any(len(x['deleted']) == 4 for x in runs))
        self.assertEqual(self.final.read_bytes(), b'accepted video')

    def test_corrupt_archive_blocks_all_deletion(self):
        self.prepare()
        manifest = c.load(self.root / 'archive/metadata/archive_manifest.json')
        Path(manifest['files'][0]['destination']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            c.cleanup(self.plan, apply=True)
        self.assertTrue(self.photo.exists())

    def test_exact_disposable_routes_preserve_working_dependency(self):
        job=c.load(self.job)
        job['file_routes']={
            'artwork/photo.png': {'storage':'artwork','relative':'photo.png'},
            '01_CLASSIFICATION/catalog.json': {'storage':'classification','relative':'catalog.json'},
            'cache.prin': None,
            'draft.prproj': None,
        }
        job['keep_working_files']=['artwork/photo.png']
        c.save(self.job,job)
        self.prepare()
        result=c.cleanup(self.plan,apply=True)
        self.assertTrue(self.photo.exists())
        self.assertFalse(self.project.exists())
        self.assertFalse((self.root/'archive/history/draft.prproj').exists())
        self.assertEqual(len(result['deleted']),3)
        self.assertEqual(c.load(self.root/'archive/classification/catalog.json')['manual'],'keep this description')

    def test_exact_routes_reject_escape_and_unlisted_file(self):
        storage={'artwork':self.root/'archive/artwork'}
        with self.assertRaises(ValueError):
            c.job_route(self.photo,self.source,storage,{'file_routes':{}})
        with self.assertRaises(ValueError):
            c.job_route(self.photo,self.source,storage,{'file_routes':{'artwork/photo.png':{'storage':'artwork','relative':'../../outside'}}})

    def test_changed_source_blocks_all_deletion(self):
        self.prepare()
        self.photo.write_bytes(b'new user edit')
        with self.assertRaises(ValueError):
            c.cleanup(self.plan, apply=True)
        self.assertTrue(self.project.exists())

    def test_new_file_blocks_all_deletion(self):
        self.prepare()
        (self.source / 'new.txt').write_text('new')
        with self.assertRaises(ValueError):
            c.cleanup(self.plan, apply=True)
        self.assertTrue(self.photo.exists())

    def test_new_external_reference_protects_media(self):
        self.prepare()
        (self.projects / 'new.prproj').write_text('<Project><FilePath>' + str(self.photo) + '</FilePath></Project>')
        result = c.cleanup(self.plan, apply=True)
        self.assertIn(str(self.photo), result['protected_sources'])
        self.assertTrue(self.photo.exists())

    def test_changed_final_blocks_deletion(self):
        self.prepare()
        self.final.write_bytes(b'changed final')
        with self.assertRaises(ValueError):
            c.cleanup(self.plan, apply=True)
        self.assertTrue(self.photo.exists())

    def test_unknown_project_path_field_is_protected(self):
        self.prepare()
        (self.projects / 'plugin.prproj').write_text('<Project><CustomLUT path="' + str(self.photo) + '" /></Project>')
        result = c.cleanup(self.plan, apply=True)
        self.assertIn(str(self.photo), result['protected_sources'])
        self.assertTrue(self.photo.exists())

    def test_remembered_save_location_is_not_a_dependency(self):
        self.prepare()
        (self.projects / 'saved.prproj').write_text('<Project><project.settings.lastknowngoodprojectpath>' + str(self.project) + '</project.settings.lastknowngoodprojectpath></Project>')
        result = c.cleanup(self.plan, apply=True)
        self.assertEqual(result['protected_sources'], [])
        self.assertFalse(self.source.exists())

    def test_outside_source_plan_rejected(self):
        self.prepare()
        p = c.load(self.plan)
        p['files'][0]['source'] = str(self.final)
        c.save(self.plan, p)
        with self.assertRaises(ValueError):
            c.cleanup(self.plan, apply=True)
        self.assertTrue(self.final.exists())

    def test_destination_collision_is_not_overwritten(self):
        c.make_plan(self.job, self.plan)
        plan = c.load(self.plan)
        row = next(x for x in plan['files'] if x['destination'])
        target = Path(row['destination'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'other user data')
        with self.assertRaises(ValueError):
            c.archive(self.plan)
        self.assertEqual(target.read_bytes(), b'other user data')


if __name__ == '__main__':
    with contextlib.redirect_stdout(io.StringIO()):
        unittest.main()
