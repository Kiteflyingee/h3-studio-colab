import tempfile
import unittest
from pathlib import Path
from studio.media import ffmpeg


class RemakeTests(unittest.TestCase):
    def test_product_edit_does_not_request_person_or_dialogue_changes(self):
        from studio.remake import compile_remake
        req = {'source_video': 'source.mp4', 'products': [{'target_id': 'bottle', 'reference': 'product.png'}]}
        analysis = {'seconds': 5, 'shots': [{'start': 0, 'end': 5}], 'transcript': []}
        plan = compile_remake(req, analysis)
        visual = plan['jobs'][0]
        self.assertEqual(visual['type'], 'video_edit')
        self.assertEqual(visual['profile'], 'h3-ripple')
        self.assertEqual([e['kind'] for e in visual['metadata']['edits']], ['product'])
        self.assertIn('characters', visual['preserve'])
        self.assertFalse(any(j['type'] == 'tts' for j in plan['jobs']))

    def test_long_source_is_split_without_truncation(self):
        from studio.remake import compile_remake
        analysis = {'seconds': 31, 'shots': [{'start': 0, 'end': 31}]}
        plan = compile_remake({'source_video': 'source.mp4', 'products': [{'target_id': 'bottle', 'reference': 'p.png'}]}, analysis)
        self.assertAlmostEqual(sum(s['seconds'] for s in plan['timeline']), 31)
        self.assertTrue(all(2 <= j['seconds'] <= 15 for j in plan['jobs']))

    def test_source_analysis_reports_missing_semantic_tools(self):
        from studio.source_analysis import analyze_source
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'source.mp4'
            ffmpeg(['-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=24:d=2', '-c:v', 'libx264', source])
            data = analyze_source(source, {})
            self.assertAlmostEqual(data['seconds'], 2, places=2)
            self.assertFalse(data['capabilities']['asr'])
            self.assertFalse(data['capabilities']['ocr'])

