import tempfile
import unittest
from pathlib import Path
from studio.media import ffmpeg


class AudioMixTests(unittest.TestCase):
    def test_new_voice_does_not_shorten_video(self):
        from studio.audio_mix import mix_voice
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); video = root / 'v.mp4'; voice = root / 'a.wav'
            ffmpeg(['-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=24:d=3', '-c:v', 'libx264', video])
            ffmpeg(['-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', voice])
            result = mix_voice(video, voice, {'start': 1, 'replace': True}, root / 'out.mp4')
            self.assertAlmostEqual(result.metadata['seconds'], 3, delta=1/24)
            self.assertTrue(result.metadata['has_audio'])

