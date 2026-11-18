import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_executor import FakeAdapter, FakeClient
from studio.contracts import ExecutionContext


class RecoveryEdgeTests(unittest.TestCase):
    def test_pending_task_cannot_be_replaced_after_prompt_change(self):
        from studio.executor import execute_manifest
        class Pending(FakeClient):
            def wait(self, *args): raise TimeoutError('still running')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); path = root / 'manifest.json'; client = Pending()
            context = ExecutionContext(root, root / 'out', None, 'current', client, True)
            with patch('studio.executor.adapter_for', return_value=FakeAdapter):
                for prompt in ('red', 'blue', 'green'):
                    path.write_text(json.dumps({'jobs': [{'id': 'a', 'type': 'image', 'prompt': prompt}]}))
                    result = execute_manifest(path, context, resume=True)
            self.assertEqual(client.submissions, 1)
            self.assertEqual(result['jobs']['a']['status'], 'running')

    def test_uncertain_submission_stays_uncertain_across_retries(self):
        from studio.executor import execute_manifest
        class Disconnected(FakeClient):
            def request(self, path, body=None):
                if path == '/prompt':
                    self.submissions += 1
                    raise ConnectionError('connection lost after submit')
                return {}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); path = root / 'manifest.json'; client = Disconnected()
            path.write_text(json.dumps({'jobs': [{'id': 'a', 'type': 'image', 'prompt': 'red'}]}))
            context = ExecutionContext(root, root / 'out', None, 'current', client, True)
            with patch('studio.executor.adapter_for', return_value=FakeAdapter):
                for _ in range(3): result = execute_manifest(path, context, resume=True)
            self.assertEqual(client.submissions, 1)
            self.assertEqual(result['jobs']['a']['status'], 'uncertain')

    def test_only_with_dependents_does_not_invalidate_sibling(self):
        from studio.executor import execute_manifest
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); path = root / 'manifest.json'; client = FakeClient()
            jobs = [{'id': 'a', 'type': 'image', 'prompt': 'red'},
                    {'id': 'b', 'type': 'image', 'prompt': 'blue', 'depends_on': ['a']},
                    {'id': 'c', 'type': 'image', 'prompt': 'green', 'depends_on': ['a']}]
            path.write_text(json.dumps({'jobs': jobs}))
            context = ExecutionContext(root, root / 'out', None, 'current', client, True)
            with patch('studio.executor.adapter_for', return_value=FakeAdapter):
                execute_manifest(path, context)
                before = client.submissions
                execute_manifest(path, context, resume=False, only={'b'}, with_dependents=True)
            self.assertEqual(client.submissions - before, 1)

