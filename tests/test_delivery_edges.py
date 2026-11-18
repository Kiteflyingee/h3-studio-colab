import json
import tempfile
import unittest
from pathlib import Path
from studio.media import ffmpeg, describe_media


class DeliveryEdges(unittest.TestCase):
    def test_font_participates_in_payload_and_cache(self):
        from studio.manifest import file_values
        job = {'type': 'subtitle_replace', 'source_video': 'source.mp4', 'plan': {'font': 'font.ttf'}}
        self.assertIn('font.ttf', file_values(job))

    def test_short_dialogue_fails_before_gpu_allocation(self):
        from studio.remake import compile_remake
        request = {'source_video': 's.mp4', 'copy': {'dialogue': [{'start': 0, 'end': 1, 'text': 'Hello'}]}}
        with self.assertRaisesRegex(ValueError, '2'):
            compile_remake(request, {'seconds': 3})

    def test_scene_text_compiles_into_exact_overlay(self):
        from studio.remake import compile_remake
        req = {'source_video': 's.mp4', 'copy': {'in_scene_text': [{'start': 0, 'end': 2, 'text': 'NEW', 'x': 10, 'y': 10}]}}
        plan = compile_remake(req, {'seconds': 3})
        self.assertEqual(plan['jobs'][-1]['type'], 'subtitle_replace')
        self.assertEqual(plan['jobs'][-1]['plan']['overlays'][0]['text'], 'NEW')

    def test_restore_source_audio_and_exact_overlay_are_real_media(self):
        from studio.audio_mix import restore_source_audio
        from studio.copy_replace import replace_subtitles
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); silent = root / 'silent.mp4'; source = root / 'source.mp4'
            ffmpeg(['-f', 'lavfi', '-i', 'color=c=black:s=128x128:r=24:d=2', '-c:v', 'libx264', silent])
            ffmpeg(['-f', 'lavfi', '-i', 'color=c=red:s=128x128:r=24:d=2', '-f', 'lavfi', '-i', 'sine=duration=2', '-c:v', 'libx264', '-c:a', 'aac', source])
            audio = restore_source_audio(silent, source, root / 'audio.mp4')
            result = replace_subtitles(Path(audio.path), {'overlays': [{'start': 0, 'end': 1, 'text': "NEW:100%'", 'x': 10, 'y': 10}]}, root / 'overlay.mp4')
            info = describe_media(result.path)
            self.assertTrue(info['has_audio'])
            self.assertAlmostEqual(info['seconds'], 2, delta=.06)
