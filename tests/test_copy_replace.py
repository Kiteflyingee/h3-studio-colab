import tempfile
import unittest
from pathlib import Path
from studio.media import ffmpeg, describe_media


class CopyTests(unittest.TestCase):
    def test_subtitle_text_and_timing_survive_render(self):
        from studio.copy_replace import replace_subtitles
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); video = root / 'source.mp4'; target = root / 'new.mp4'
            ffmpeg(['-f', 'lavfi', '-i', 'color=c=blue:s=160x160:r=24:d=2', '-c:v', 'libx264', video])
            result = replace_subtitles(video, {'subtitles': [{'start': .2, 'end': 1.8, 'text': 'New product'}]}, target)
            self.assertEqual(result.kind, 'video')
            self.assertAlmostEqual(result.metadata['seconds'], 2, delta=1/24)
            self.assertIn('New product', target.with_suffix('.srt').read_text())

