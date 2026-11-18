import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def validate(self, jobs):
        from studio.manifest import validate_manifest
        return validate_manifest({'jobs': jobs}, self.base)

    def video(self, **values):
        return dict(id='a', type='video', mode='text', prompt='A quiet lake.', seconds=5, **values)

    def test_contract_is_available(self):
        self.assertIsNotNone(importlib.util.find_spec('studio.manifest'))

    def test_legacy_examples_and_paths(self):
        from studio.manifest import load_manifest
        p = self.base / 'manifest.json'
        p.write_text(json.dumps({'jobs': [self.video()]}))
        self.assertEqual(load_manifest(p)['jobs'][0]['id'], 'a')

    def test_invalid_seconds_duplicate_and_self_reference(self):
        for seconds in (1, 16, float('nan'), True):
            job = self.video(); job['seconds'] = seconds
            self.assertTrue(self.validate([job]), seconds)
        self.assertTrue(self.validate([self.video(), self.video()]))
        job = self.video(); job['inputs'] = ['@a']; job['mode'] = 'ref'
        self.assertTrue(self.validate([job]))

    def test_future_reference_and_missing_file(self):
        job = self.video(); job['first'] = '@b'; job['mode'] = 'frames'
        self.assertTrue(self.validate([job]))
        job['first'] = 'missing.png'
        self.assertTrue(self.validate([job]))

    def test_reference_limits_and_nonprompt_tasks(self):
        image = {'id': 'x', 'type': 'image', 'prompt': 'x', 'inputs': ['@a'] * 9}
        self.assertTrue(self.validate([self.video(), image]))
        tts = {'id': 'x', 'type': 'tts', 'prompt': 'x', 'voices': ['@a'] * 4}
        self.assertTrue(self.validate([self.video(), tts]))
        (self.base / 'source.png').write_bytes(b'x')
        (self.base / 'source.mp4').write_bytes(b'v')
        job = {'id': 'x', 'type': 'video_edit', 'source_video': 'source.mp4', 'anchors': [{'frame': 0, 'image': 'source.png'}]}
        self.assertEqual(self.validate([job]), [])

    def test_dependency_order_and_unknown_fields(self):
        from studio.manifest import dependency_order
        jobs = [self.video(), {'id': 'b', 'type': 'video', 'mode': 'ref', 'prompt': '@video1', 'extend': 'a'}]
        self.assertEqual(dependency_order({'jobs': jobs}), ['a', 'b'])
        self.assertEqual(self.validate(jobs), [])
        jobs[0]['resoluton'] = '720P'
        self.assertTrue(any('resoluton' in e for e in self.validate(jobs)))

