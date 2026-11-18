import tempfile
import unittest
import wave
from pathlib import Path


class MediaTests(unittest.TestCase):
    def test_staging_uses_bytes_not_path_or_size(self):
        from studio.comfy_client import ComfyClient
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'voice.wav'; dest = root / 'input'; dest.mkdir()
            source.write_bytes(b'aaaa')
            first = ComfyClient().stage_local_file(source, dest)
            source.write_bytes(b'bbbb')
            second = ComfyClient().stage_local_file(source, dest)
            self.assertNotEqual(first, second)
            self.assertEqual((dest / second).read_bytes(), b'bbbb')

    def test_audio_metadata_is_actual(self):
        from studio.media import describe_media
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'sample.wav'
            with wave.open(str(p), 'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b'\0\0' * 16000)
            info = describe_media(p)
            self.assertEqual(info['kind'], 'audio')
            self.assertAlmostEqual(info['seconds'], 1, places=3)

