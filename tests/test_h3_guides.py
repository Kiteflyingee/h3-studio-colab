import unittest


class GuideTests(unittest.TestCase):
    def test_time_guides_are_wired_and_out_of_range_is_rejected(self):
        from studio.adapters.h3_guides import build_job
        job = {'id': 'a', 'type': 'video', 'mode': 'guides', 'prompt': 'A cat.', 'seconds': 5, 'guides': [{'at_seconds': 0, 'image': 'a.png'}, {'at_seconds': 4, 'image': 'b.png'}]}
        staged = {'guides': [dict(x, media={'name': x['image']}) for x in job['guides']]}
        g = build_job(job, staged, 'h3-guides')
        self.assertEqual(g['guide1']['inputs']['frame_idx'], 96)
        self.assertEqual(g['guider']['inputs']['conditioning'], ['guide1', 0])
        job['guides'][1]['at_seconds'] = 6
        with self.assertRaises(ValueError): build_job(job, staged, 'h3-guides')

