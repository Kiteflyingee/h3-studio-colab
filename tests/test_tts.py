import unittest


class TTSTests(unittest.TestCase):
    def test_voice_references_and_seed_are_wired(self):
        from studio.adapters.tts import build_job
        job = {'id': 'a', 'type': 'tts', 'prompt': 'An adult woman says hello.', 'seconds': 5, 'seed': 12}
        for count in (0, 1, 3):
            g = build_job(job, {'voices': [{'name': f'{i}.wav'} for i in range(count)]}, 'h3-tts32-legacy')
            self.assertEqual(g['54']['inputs']['seed'], 12)
            for i in range(count):self.assertIn(f'ref_audios.ref_audio_{i}', g['7']['inputs'])
        with self.assertRaises(ValueError):
            build_job(job, {'voices': [{'name': 'x.wav'}] * 4}, 'h3-tts32-legacy')

