import unittest
from studio.director import compile_manifest


class DirectorContinuityTests(unittest.TestCase):
    def test_continue_previous_compiles_incremental_segment(self):
        script = {'shots': [{'id': 'a', 'mode': 'text', 'prompt': 'A lake.', 'seconds': 5},
                            {'id': 'b', 'mode': 'ref', 'prompt': 'Continue.', 'seconds': 3, 'continue_previous': True}]}
        manifest = compile_manifest(script)
        self.assertEqual(manifest['jobs'][1]['extend'], 'a')
        self.assertEqual(manifest['jobs'][1]['extension_output'], 'segment')

