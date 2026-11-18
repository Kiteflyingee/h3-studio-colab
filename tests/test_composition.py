import json
import tempfile
import unittest
from pathlib import Path
from studio.media import ffmpeg, describe_media
from studio.director import compose_video


class CompositionTests(unittest.TestCase):
    def test_three_longer_sources_are_trimmed_to_requested_duration(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'out').mkdir()
            shots = []
            for i in range(3):
                name = f'shot{i}'
                ffmpeg(['-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=24:d=2.5', '-c:v', 'libx264', root / 'out' / (name + '.mp4')])
                shots.append({'id': name, 'mode': 'text', 'prompt': 'A lake.', 'seconds': 2})
            path = root / 'director.json'
            path.write_text(json.dumps({'shots': shots, 'aspect': '1:1', 'resolution': '480P'}))
            final = compose_video(path, root / 'final.mp4')
            self.assertAlmostEqual(describe_media(final)['seconds'], 6, delta=3/24)

