import argparse
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class MacOSAndA100FixTests(unittest.TestCase):
    def test_video_vae_override_switches_graph_and_required_components(self):
        from studio.adapters.h3 import build_job
        from studio.model_registry import required_components

        job = {
            'id': 'v1', 'type': 'video', 'mode': 'text', 'prompt': 'A calm ocean.',
            'seconds': 5, 'video_vae': 'minimax_h3_video_vae_fp16.safetensors'
        }
        graph = build_job(job, {}, 'h3-fl2va-int8')
        self.assertEqual(graph['vae']['inputs']['vae_name'], 'minimax_h3_video_vae_fp16.safetensors')
        files = {c['file'] for c in required_components([job])}
        self.assertIn('vae/minimax_h3_video_vae_fp16.safetensors', files)
        self.assertNotIn('vae/minimax_h3_video_vae_int8_convrot.safetensors', files)

        with self.assertRaises(ValueError):
            required_components([dict(job, video_vae='unlocked_custom_vae.safetensors')])

    def test_a100_and_h100_defaults_in_run_colab(self):
        from studio.backends import run_colab

        captured = {}
        def fake_inner(args, transport, manifest_path, manifest, out):
            captured['jobs'] = manifest['jobs']
            return {'jobs': {}}

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            m = root / 'm.json'
            m.write_text(json.dumps({'jobs': [{'id': 'v', 'type': 'video', 'mode': 'text', 'prompt': 'Sunset.', 'seconds': 5}]}))
            args = argparse.Namespace(manifest=str(m), session='s1', output_dir=str(root / 'out'),
                                      setup_runtime=False, install_models=False, gpu='A100', resume=False,
                                      only=None, with_dependents=False, allow_unverified=True,
                                      stop_after=True, max_minutes=1)
            with patch('studio.backends.legacy_transport'), patch('studio.backends._run_colab', side_effect=fake_inner):
                run_colab(args)
            self.assertEqual(captured['jobs'][0]['resolution'], '720P')
            self.assertEqual(captured['jobs'][0]['video_vae'], 'minimax_h3_video_vae_fp16.safetensors')

            args.gpu = 'H100'
            with patch('studio.backends.legacy_transport'), patch('studio.backends._run_colab', side_effect=fake_inner):
                run_colab(args)
            self.assertEqual(captured['jobs'][0]['resolution'], '720P')
            self.assertNotIn('video_vae', captured['jobs'][0])

    def test_artifact_target_handles_symlinked_remote_dir_and_blocks_traversal(self):
        from studio.backends import artifact_target

        with tempfile.TemporaryDirectory() as d:
            real_base = (Path(d) / 'real_remote').resolve()
            real_base.mkdir()
            link_base = Path(d) / 'symlink_remote'
            os.symlink(real_base, link_base)
            target = artifact_target(Path(d) / 'out', str(real_base / 'out/clip.mp4'), str(link_base))
            self.assertEqual(target, Path(d) / 'out/clip.mp4')
            with self.assertRaises(ValueError):
                artifact_target(Path(d) / 'out', str(link_base) + '/../escape.mp4', str(link_base))

    def test_ffmpeg_error_preserves_called_process_error_and_exposes_stderr(self):
        from studio.media import ffmpeg

        with self.assertRaises(subprocess.CalledProcessError) as ctx:
            ffmpeg(['-i', '/nonexistent_input_file_12345.mp4', '/tmp/unused.mp4'])
        self.assertIn('nonexistent_input_file_12345.mp4', str(ctx.exception))
