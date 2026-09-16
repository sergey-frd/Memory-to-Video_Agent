import gzip
import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

from tools import hero_workspace_finalize as f
from tools.hero_video_closeout import load, save, sha, project_xml


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(__file__).resolve().parents[1] / 'tmp/finalize_tests' / uuid.uuid4().hex
        self.root = self.base / 'workspace'
        self.root.mkdir(parents=True)
        self.patch = patch.object(f, 'ROOT', self.root)
        self.patch.start()
        self.old = self.root / 'output/TASK999/ADDENDUM/artwork/photo.png'
        self.old.parent.mkdir(parents=True)
        self.new = self.base / 'hero/artwork/photo.png'
        self.new.parent.mkdir(parents=True)
        self.new.write_bytes(b'original photo')
        self.project = self.base / 'projects/old.prproj'
        self.project.parent.mkdir()
        tree = ET.Element('Project')
        ET.SubElement(tree, 'FilePath').text = str(self.old)
        ET.SubElement(tree, 'Scale').text = '117.5'
        self.project.write_bytes(gzip.compress(ET.tostring(tree)))
        self.obsolete = self.root / 'obsolete.py'
        self.obsolete.write_text('old utility')
        self.final = self.base / 'projects/final.mp4'
        self.final.write_bytes(b'accepted video')
        self.previous = self.base / 'old_plan.json'
        save(self.previous, {'files': [{'source': str(self.old), 'sha256': sha(self.new)}]})
        self.config = self.base / 'config.local.json'
        save(self.config, {'private_archive': str(self.base / 'archive'),
                           'relocation': {'old_root': str(self.old.parent), 'new_root': str(self.new.parent), 'closeout_plan': str(self.previous)},
                           'project_scan_roots': [str(self.project.parent)],
                           'obsolete_workspace_files': ['obsolete.py'],
                           'empty_directories': ['output/TASK999/ADDENDUM/artwork'],
                           'retained_results': [str(self.final)]})
        self.plan = self.root / 'reports/plan.json'

    def tearDown(self):
        self.patch.stop()
        expected = Path(__file__).resolve().parents[1] / 'tmp/finalize_tests'
        assert self.base.resolve().is_relative_to(expected.resolve())
        shutil.rmtree(self.base)

    def make_plan(self):
        with patch.object(f, 'scan_projects', return_value=([{'project': str(self.project)}], 1)):
            f.plan(self.config, self.plan)

    def test_apply_preserves_backup_and_effects(self):
        old = sha(self.project)
        self.make_plan()
        with patch.object(f.subprocess, 'run') as tasklist:
            tasklist.return_value.returncode = 0
            tasklist.return_value.stdout = ''
            f.apply(self.plan)
        root = ET.fromstring(project_xml(self.project))
        self.assertEqual(root.findtext('FilePath'), str(self.new))
        self.assertEqual(root.findtext('Scale'), '117.5')
        report = load(self.plan.with_name('result.json'))
        self.assertEqual(sha(report['repaired_projects'][0]['backup']), old)
        self.assertFalse(self.obsolete.exists())
        self.assertEqual((self.base / 'archive/workspace_files/obsolete.py').read_text(), 'old utility')
        self.assertEqual(self.final.read_bytes(), b'accepted video')

    def test_changed_project_prevents_deletion(self):
        self.make_plan()
        self.project.write_bytes(b'new edits')
        with patch.object(f.subprocess, 'run') as tasklist:
            tasklist.return_value.returncode = 0
            tasklist.return_value.stdout = ''
            with self.assertRaises(ValueError):
                f.apply(self.plan)
        self.assertTrue(self.obsolete.exists())

    def test_wrong_moved_image_rejected(self):
        self.new.write_bytes(b'different image')
        with self.assertRaises(ValueError):
            self.make_plan()

    def test_separate_copy_stage_preserves_original_projects(self):
        oldhash = sha(self.project)
        self.make_plan()
        f.stage(self.plan)
        staged = load(self.plan.with_name('staged_copies.json'))
        archive = staged['archive_zip']
        shutil.copy2(archive['source'], archive['destination'])
        for row in staged['project_copies']:
            shutil.copy2(row['prepared'], row['destination'])
        f.prune_staged(self.plan)
        self.assertEqual(sha(self.project), oldhash)
        self.assertFalse(self.obsolete.exists())
        self.assertTrue(Path(staged['project_copies'][0]['destination']).exists())
