import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.director import read_script, compile_manifest, timeline_data, write_plan
from studio.comfy_client import ComfyClient, ComfyError


class StudioUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p = Path(self.temp.name)
        self.scene = {
            'title': '小片', 'gpu': 'L4', 'aspect': '16:9', 'resolution': '480P',
            'shots': [
                {'id': 'shot01', 'seconds': 5, 'mode': 'text', 'prompt': 'A camera moves toward a quiet lake.'},
                {'id': 'shot02', 'seconds': 6, 'mode': 'text', 'prompt': 'A camera pans across a quiet lake.', 'transition': 'fade_black'},
            ],
        }
        self.path = self.p / 'director.json'
        self.path.write_text(json.dumps(self.scene, ensure_ascii=False), encoding='utf-8')

    def test_director_manifest(self):
        s, _ = read_script(self.path)
        m = compile_manifest(s)
        self.assertEqual(len(m['jobs']), 2)
        self.assertEqual(m['jobs'][1]['aspect'], '16:9')
        self.assertEqual(timeline_data(s)['total_seconds'], 11)

    def test_director_export(self):
        target, out = write_plan(self.path, self.p / 'preview')
        self.assertTrue(target.is_file())
        html = (out / 'timeline.html').read_text(encoding='utf-8')
        self.assertIn('导出分镜 JSON', html)
        self.assertIn('shot02', html)
        self.assertTrue((out / 'timeline.edl.json').is_file())

    def test_invalid_seconds(self):
        self.scene['shots'][0]['seconds'] = 35
        self.path.write_text(json.dumps(self.scene), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '2–15'):
            read_script(self.path)

    def test_missing_reference_mentions(self):
        self.scene['shots'][0]['mode'] = 'ref'
        self.scene['shots'][0]['inputs'] = ['test.png']
        (self.p / 'test.png').write_bytes(b'test')
        self.path.write_text(json.dumps(self.scene), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '参考素材'):
            read_script(self.path)

    def test_ui_api_format_required(self):
        client = ComfyClient('http://127.0.0.1:8188')
        client.info = lambda: {'LoadImage': {}}
        with self.assertRaises(ComfyError):
            client.validate_graph({'nodes': [], 'links': []})


if __name__ == '__main__':
    unittest.main()
