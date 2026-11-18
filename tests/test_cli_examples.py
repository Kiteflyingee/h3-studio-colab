import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def test_subtitle_only_remake_runs_without_comfyui(self):
        from studio.media import ffmpeg, describe_media
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'source.mp4'
            ffmpeg(['-f', 'lavfi', '-i', 'color=c=blue:s=160x160:r=24:d=2', '-c:v', 'libx264', source])
            request = root / 'remake.json'
            request.write_text(json.dumps({'source_video': 'source.mp4', 'copy': {'subtitles': [{'start': 0, 'end': 2, 'text': 'NEW PRODUCT'}]}}))
            final = root / 'final.mp4'
            r = subprocess.run([sys.executable, str(ROOT / 'scripts/h3studio.py'), 'remake-render', str(request),
                                '--backend', 'local', '--output', str(final)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(final.is_file())
            self.assertAlmostEqual(describe_media(final)['seconds'], 2, delta=2/24)

    def test_offline_plan_and_doctor_do_not_contact_server(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'manifest.json'
            p.write_text(json.dumps({'jobs': [{'id': 'a', 'type': 'video', 'mode': 'text', 'prompt': 'A lake.', 'seconds': 5}]}))
            for args in (['plan', str(p), '--backend', 'current'], ['doctor', '--manifest', str(p), '--offline', '--json']):
                r = subprocess.run([sys.executable, str(ROOT / 'scripts/h3studio.py'), *args], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                data = json.loads(r.stdout)
                self.assertIn('jobs', data)
                self.assertNotIn('prompt_id', r.stdout)

