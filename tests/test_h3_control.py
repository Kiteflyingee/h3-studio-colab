import unittest


class ControlTests(unittest.TestCase):
    def test_control_video_connects_actual_union_patch(self):
        from studio.adapters.h3_control import build_job
        job = {'id': 'a', 'type': 'video', 'mode': 'control', 'prompt': 'A dancer.', 'seconds': 5,
               'control': {'kind': 'pose', 'video': 'pose.mp4', 'preprocess': False}}
        graph = build_job(job, {'control': {'video': {'name': 'pose.mp4'}, 'kind': 'pose', 'preprocess': False}}, 'h3-control-union')
        self.assertEqual(graph['control_apply']['class_type'], 'MiniMaxH3FunControlNetApply')
        self.assertIn('control_video', graph['control_apply']['inputs'])
        self.assertEqual(graph['guider']['inputs']['model'], ['control_apply', 0])

