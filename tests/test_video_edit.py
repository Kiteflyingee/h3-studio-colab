import json
import unittest


class VideoEditTests(unittest.TestCase):
    def test_confirmed_anchors_and_source_video_are_connected_to_ripple(self):
        from studio.adapters.video_edit import build_job
        job = {
            'id': 'a',
            'type': 'video_edit',
            'seconds': 4.0,
            'anchors': [{'frame': 0, 'image': 'edited_0.png'}, {'frame': 95, 'image': 'edited_95.png'}],
            'comparison': True,
        }
        staged = {
            'source_video': {'kind': 'video', 'name': 'original.mp4', 'width': 1280, 'height': 720, 'seconds': 4.0, 'frames': 96, 'has_audio': True},
            'anchors': [
                {'frame': 0, 'image_media': {'kind': 'image', 'name': 'ed0.png'}},
                {'frame': 95, 'image_media': {'kind': 'image', 'name': 'ed95.png'}},
            ],
        }
        graph = build_job(job, staged, 'h3-ripple')
        self.assertEqual(graph['source']['inputs']['file'], 'original.mp4')
        self.assertIn('src_padded', graph)
        markers = json.loads(graph['ripple_clip']['inputs']['markers'])
        self.assertEqual([m['frame'] for m in markers], [0, 95])
        self.assertEqual(graph['cond']['inputs']['edited_anchor_1'], ['anchor_load_1', 0])
        self.assertEqual(graph['cond']['inputs']['edited_anchor_2'], ['anchor_load_2', 0])
        self.assertEqual(graph['sampler']['inputs']['sampler_name'], 'lcm')
        self.assertEqual(graph['schedule']['inputs']['steps'], 6)
        self.assertEqual(graph['save']['inputs']['save'], 'shot + comparison')
