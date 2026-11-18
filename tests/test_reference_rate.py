import tempfile
import unittest
from pathlib import Path
from studio.contracts import ExecutionContext
from studio.media import ffmpeg, describe_media


class ReferenceRateTests(unittest.TestCase):
    def test_h3_reference_is_resampled_without_shortening(self):
        from studio.media import normalize_reference
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'source.mp4'
            ffmpeg(['-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=60:d=1', '-c:v', 'libx264', source])
            result = normalize_reference(source, 24, root / 'work')
            info = describe_media(result)
            self.assertAlmostEqual(info['fps_float'], 24)
            self.assertAlmostEqual(info['seconds'], 1, delta=1/24)

