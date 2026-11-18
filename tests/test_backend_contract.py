import json
import tempfile
import unittest
from pathlib import Path


class BackendContractTests(unittest.TestCase):
    def test_payload_rewrites_file_paths_without_exposing_credentials(self):
        from studio.backends import build_payload
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'a.png').write_bytes(b'one')
            (root / 'source.mp4').write_bytes(b'two')
            manifest = {
                'jobs': [{
                    'id': 'a',
                    'type': 'video_edit',
                    'source_video': 'source.mp4',
                    'anchors': [{'frame': 0, 'image': 'a.png'}],
                    'metadata': {'note': 'a.png'},
                }]
            }
            remapped, uploads = build_payload(manifest, root)
            self.assertEqual(len(uploads), 2)
            self.assertTrue(remapped['jobs'][0]['source_video'].startswith('/content/h3studio_job/inputs/'))
            self.assertTrue(remapped['jobs'][0]['anchors'][0]['image'].startswith('/content/h3studio_job/inputs/'))
            self.assertEqual(remapped['jobs'][0]['metadata']['note'], 'a.png')
            self.assertNotIn('token', json.dumps(remapped).lower())
