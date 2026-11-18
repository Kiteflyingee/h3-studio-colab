import unittest


class H3ProfileTests(unittest.TestCase):
    def test_text_and_frames_use_fl2va_without_style_loras(self):
        from studio.adapters.h3 import build_job
        job = {'id': 'a', 'type': 'video', 'mode': 'text', 'prompt': 'A quiet lake.', 'seconds': 5, 'seed': 1}
        graph = build_job(job, {}, 'h3-fl2va-int8')
        self.assertIn('fl2va_pruned', graph['model']['inputs']['unet_name'])
        self.assertFalse(any('Mystic' in str(n) or 'Combat' in str(n) for n in graph.values()))
        self.assertEqual(graph['noise']['inputs']['noise_seed'], 1)
        self.assertEqual(graph['cond']['class_type'], 'MiniMaxH3ImageToVideo')
        self.assertEqual((graph['cond']['inputs']['length'] - 5) % 17, 0)

    def test_reference_inputs_reach_conditioning(self):
        from studio.adapters.h3 import build_job
        job = {'id': 'a', 'type': 'video', 'mode': 'ref', 'prompt': '@图片1 follows @视频1.', 'seed': 1}
        staged = {'inputs': [{'kind': 'image', 'name': 'cat.png'},
                             {'kind': 'video', 'name': 'source.mp4', 'has_audio': True}]}
        g = build_job(job, staged, 'h3-ref2va-int8')
        self.assertIn('ref2va_pruned', g['model']['inputs']['unet_name'])
        self.assertIn('ref_images.ref_image_0', g['cond']['inputs'])
        self.assertIn('ref_videos.ref_video_0', g['cond']['inputs'])
        self.assertIn('ref_video_audios.ref_video_audio_0', g['cond']['inputs'])

