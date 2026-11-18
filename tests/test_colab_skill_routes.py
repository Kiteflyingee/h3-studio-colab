import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class SkillRouteTests(unittest.TestCase):
    def test_run_defaults_to_colab_with_automatic_runtime_preparation(self):
        cli = module('h3studio_route', ROOT / 'scripts/h3studio.py')
        with patch('studio.cli.run_command') as run, patch('sys.argv', ['h3studio', 'run', 'jobs.json']):
            cli.main()
        args = run.call_args.args[0]
        self.assertEqual(args.backend, 'colab')
        self.assertTrue(args.setup_runtime)
        self.assertTrue(args.install_models)
        self.assertEqual(args.idle_minutes, 15)

    def test_remake_anchors_and_render_accept_colab_lifecycle_options(self):
        cli = module('h3studio_remake_route', ROOT / 'scripts/h3studio.py')
        for subcmd in ('remake-anchors', 'remake-render'):
            argv = ['h3studio', subcmd, 'request.json', '--backend', 'colab', '--session', 'existing', '--resume', '--confirmed']
            with patch('studio.cli.remake_command') as remake, patch('sys.argv', argv):
                cli.main()
            self.assertEqual(remake.call_args.args[0].backend, 'colab')
            self.assertEqual(remake.call_args.args[0].command, subcmd)

    def test_unified_cli_exposes_session_management_without_legacy_h3run(self):
        self.assertFalse((ROOT / 'scripts/h3run.py').exists())
        cli = module('h3studio_session_route', ROOT / 'scripts/h3studio.py')
        with patch('studio.colab_cli.colab_exe', return_value='/usr/local/bin/colab'), \
             patch('studio.colab_cli.colab', return_value='[colab] No active sessions.'), \
             patch('studio.colab_cli.usage', return_value=(100.0, '100 units')), \
             patch('sys.argv', ['h3studio', 'check']):
            cli.main()

    def test_request_payload_covers_variant_assets_and_does_not_rewrite_prompt(self):
        from studio.remake_remote import build_remake_payload
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ('source.mp4', 'person.png', 'product.png', 'font.ttf', 'analysis.json'):
                (root / name).write_bytes(name.encode())
            request = {
                'source_video': 'source.mp4', 'prompt': 'person.png',
                'characters': [{'target_id': 'woman', 'reference': 'person.png'}],
                'copy': {'font': 'font.ttf'}, 'analysis': {'analysis_file': 'analysis.json'},
                'variants': [{'name': 'b', 'products': [{'target_id': 'bottle', 'reference': 'product.png'}]}],
            }
            payload, files = build_remake_payload(request, root)
            self.assertEqual(len(files), 5)
            self.assertEqual(payload['prompt'], 'person.png')
            self.assertTrue(payload['variants'][0]['products'][0]['reference'].startswith('/content/h3studio_job/inputs/'))

    def test_download_paths_preserve_variants_and_reject_foreign_paths(self):
        from studio.backends import artifact_target
        root = Path('/tmp/local')
        self.assertNotEqual(
            artifact_target(root, '/remote/remake/a/out/shot.mp4', '/remote'),
            artifact_target(root, '/remote/remake/b/out/shot.mp4', '/remote'),
        )
        with self.assertRaises(ValueError):
            artifact_target(root, '/another/private', '/remote')
