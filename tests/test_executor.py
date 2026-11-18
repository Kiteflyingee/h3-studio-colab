import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from _pil_compat import Image
from studio.contracts import ExecutionContext


class FakeClient:
    def __init__(self): self.submissions = 0; self.waited = []
    def info(self): return {'SaveImage': {'input': {'required': {}}}}
    def request(self, path, body=None):
        if path == '/prompt':
            self.submissions += 1
            return {'prompt_id': str(self.submissions)}
        return {}
    def wait(self, pid, timeout=3600): self.waited.append(pid); return pid, {'outputs': {}}
    def save_result(self, entry, output_dir, prefix):
        p = Path(output_dir) / (prefix + '.png'); Image.new('RGB', (32, 32), 'red').save(p)
        return [p]


class FakeAdapter:
    @staticmethod
    def build_job(job, staged, profile_id):
        if job.get('metadata', {}).get('fail'): raise RuntimeError('intentional failure')
        return {'save': {'class_type': 'SaveImage', 'inputs': {}}}
    @staticmethod
    def collect_job(job, entry, context):
        from studio.adapters.common import collect_job
        return collect_job(job, entry, context)


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.client = FakeClient()
        self.context = ExecutionContext(self.base, self.base / 'out', None, 'current', self.client, True)
        self.path = self.base / 'manifest.json'

    def run_jobs(self, jobs, **kwargs):
        from studio.executor import execute_manifest
        self.path.write_text(json.dumps({'jobs': jobs}))
        with patch('studio.executor.adapter_for', return_value=FakeAdapter):
            return execute_manifest(self.path, self.context, **kwargs)

    def test_resume_uses_fingerprint_not_filename(self):
        jobs = [{'id': 'a', 'type': 'image', 'prompt': 'red'}]
        self.run_jobs(jobs); self.run_jobs(jobs, resume=True)
        self.assertEqual(self.client.submissions, 1)
        jobs[0]['prompt'] = 'blue'; self.run_jobs(jobs, resume=True)
        self.assertEqual(self.client.submissions, 2)

    def test_failure_blocks_only_dependents(self):
        jobs = [{'id': 'a', 'type': 'image', 'prompt': 'red', 'metadata': {'fail': True}},
                {'id': 'b', 'type': 'image', 'prompt': 'blue', 'inputs': ['@a']},
                {'id': 'c', 'type': 'image', 'prompt': 'green'}]
        result = self.run_jobs(jobs)
        self.assertEqual(result['jobs']['a']['status'], 'error')
        self.assertEqual(result['jobs']['b']['status'], 'dependency_failed')
        self.assertEqual(result['jobs']['c']['status'], 'done')

    def test_resume_waits_known_prompt_without_resubmit(self):
        jobs = [{'id': 'a', 'type': 'image', 'prompt': 'red'}]
        result = self.run_jobs(jobs)
        result['jobs']['a']['status'] = 'running'; result['jobs']['a']['artifacts'] = []
        (self.base / 'out/results.json').write_text(json.dumps(result))
        self.run_jobs(jobs, resume=True)
        self.assertEqual(self.client.submissions, 1)
        self.assertEqual(self.client.waited, ['1', '1'])

