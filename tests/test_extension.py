import tempfile
import unittest
from pathlib import Path
from studio.media import ffmpeg, describe_media
from studio.contracts import ExecutionContext


class ExtensionTests(unittest.TestCase):
    def test_segment_and_cumulative_do_not_repeat_source(self):
        from studio.adapters.extension import finalize_extension
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'source.mp4'; generated = root / 'generated.mp4'
            for p, seconds, color in [(source, 5, 'red'), (generated, 4, 'blue')]:
                ffmpeg(['-f', 'lavfi', '-i', f'color=c={color}:s=64x64:r=24:d={seconds}', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', p])
            ctx = ExecutionContext(root, root, root, 'current', None)
            for mode, expected in [('segment', 3), ('cumulative', 8)]:
                job = {'id': mode, 'type': 'video', 'seconds': 3, 'extension_output': mode, '_context_seconds': 1}
                result = finalize_extension(source, generated, job, ctx)
                self.assertAlmostEqual(result.metadata['seconds'], expected, delta=2/24)

