import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_executor import FakeAdapter, FakeClient
from studio.contracts import ExecutionContext


class PendingReviewTests(unittest.TestCase):
    def test_failed_prerequisite_does_not_erase_pending_child(self):
        from studio.executor import execute_manifest
        class Client(FakeClient):
            def wait(self, pid, *args):
                if pid == '2':
                    raise TimeoutError('pending child')
                return super().wait(pid, *args)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / 'm.json'
            client = Client()
            ctx = ExecutionContext(root, root / 'out', None, 'current', client, True)
            jobs = [{'id': 'a', 'type': 'image', 'prompt': 'red'}, {'id': 'b', 'type': 'image', 'prompt': 'blue', 'depends_on': ['a']}]
            with patch('studio.executor.adapter_for', return_value=FakeAdapter):
                p.write_text(json.dumps({'jobs': jobs}))
                execute_manifest(p, ctx)
                jobs[0]['metadata'] = {'fail': True}
                p.write_text(json.dumps({'jobs': jobs}))
                r = execute_manifest(p, ctx, resume=True)
            self.assertEqual(r['jobs']['b']['metadata']['prompt_id'], '2')
            self.assertEqual(r['jobs']['b']['status'], 'running')
