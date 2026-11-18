import unittest


class PictureTests(unittest.TestCase):
    def test_lora_uses_fl2va_and_target_is_not_reordered(self):
        from studio.adapters.h3 import build_job
        refs = [{'kind': 'image', 'name': n, 'width': 100, 'height': 100} for n in ('target.png', 'reference.png')]
        graph = build_job({'id': 'a', 'type': 'image', 'prompt': 'Use @图片2 to edit @图片1.', 'seed': 9}, {'inputs': refs}, 'ph3-lora-int8')
        self.assertIn('fl2va_pruned', graph['iu']['inputs']['unet_name'])
        self.assertEqual(graph['ir1']['inputs']['image'], 'target.png')
        self.assertEqual(graph['ienc']['inputs']['prompt'], 'Use <Picture 2> to edit <Picture 1>.')
        self.assertIn('p-h3-image_text_encoder', graph['il']['inputs']['clip_name'])

    def test_standalone_has_no_lora(self):
        from studio.adapters.h3 import build_job
        g = build_job({'id': 'a', 'type': 'image', 'prompt': 'A cat.', 'seed': 4}, {}, 'ph3-standalone-w4a8')
        self.assertIn('p-h3-image_w4a8', g['iu']['inputs']['unet_name'])
        self.assertNotIn('ilo', g)

