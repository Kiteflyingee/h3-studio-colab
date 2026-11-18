import unittest


class AccelerationTests(unittest.TestCase):
    def test_fast_omni_uses_own_checkpoint_without_turbo_lora(self):
        from studio.adapters.fasth3 import build_job
        g = build_job({'id': 'a', 'type': 'video', 'mode': 'ref', 'prompt': 'A cat.', 'seconds': 5, 'seed': 3}, {}, 'fasth3-omni-int8')
        self.assertIn('fasth3_omniref', g['model']['inputs']['unet_name'])
        self.assertEqual(g['schedule']['inputs']['steps'], 8)
        self.assertNotIn('turbo', g)
        self.assertEqual(g['shift']['inputs']['shift_video'], 12.)

