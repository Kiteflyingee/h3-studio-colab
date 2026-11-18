import argparse
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


def options(root, **kw):
    p=root/'m.json';p.write_text(json.dumps({'jobs':[{'id':'a','type':'image','prompt':'red'}]}))
    data=dict(manifest=str(p),session='existing',output_dir=str(root/'out'),setup_runtime=False,install_models=False,
              gpu='L4',resume=True,only=None,with_dependents=False,allow_unverified=True,stop_after=True,max_minutes=.01)
    data.update(kw);return argparse.Namespace(**data)


class ReviewRemoteTests(unittest.TestCase):
    def test_runner_done_with_pending_collection_never_stops_vm(self):
        from studio.backends import run_colab
        from studio.model_registry import file_sha256
        class Transport:
            stopped=False
            restarted=0
            def token_ok(self):return True
            def colab(self,*args,**kw):
                if args[0]=='stop':self.stopped=True
            def remote_exec(self,session,code):
                if 'H3REMOTE' in code:return 'H3REMOTE '+json.dumps({'run_id':'owned','done':True,'report':{'jobs':{'a':{'status':'collecting','artifacts':[]}}}})
                if 'runner_v3.py' in code:self.restarted+=1
                return 'H3STOP True'
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);args=options(root);out=Path(args.output_dir);out.mkdir()
            (out/'remote-session.json').write_text(json.dumps({'session':'existing','run_id':'owned','manifest_sha256':file_sha256(args.manifest),'only':None,'with_dependents':False,'launched':True}))
            transport=Transport()
            with patch('studio.backends.legacy_transport',return_value=transport),patch('studio.backends.time.sleep'):
                with self.assertRaises(RuntimeError):run_colab(args)
            self.assertFalse(transport.stopped)
            self.assertEqual(transport.restarted,1)

    def test_busy_guard_never_stops_unrelated_session(self):
        from studio.backends import run_colab
        class Transport:
            stopped=False
            def token_ok(self):return True
            def remote_exec(self,*args):raise RuntimeError('busy: another batch')
            def colab(self,*args,**kw):
                if args[0]=='stop':self.stopped=True
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);transport=Transport()
            with patch('studio.backends.legacy_transport',return_value=transport):
                with self.assertRaises(RuntimeError):run_colab(options(root))
            self.assertFalse(transport.stopped)

    def test_resume_attaches_to_matching_live_run_without_upload(self):
        from studio.backends import run_colab
        from studio.model_registry import file_sha256
        class Transport:
            calls=[]
            def token_ok(self):return True
            def colab(self,*args,**kw):self.calls.append(args[0])
            def remote_exec(self,session,code):
                if 'H3REMOTE' in code:return 'H3REMOTE '+json.dumps({'run_id':'owned','done':True,'running':False,'report':{'jobs':{'a':{'status':'done','artifacts':[]}}}})
                raise RuntimeError('busy: existing live run')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);args=options(root,stop_after=False);out=Path(args.output_dir);out.mkdir()
            (out/'remote-session.json').write_text(json.dumps({'session':'existing','run_id':'owned','manifest_sha256':file_sha256(args.manifest),'only':None,'with_dependents':False,'launched':True}))
            transport=Transport()
            with patch('studio.backends.legacy_transport',return_value=transport),patch('studio.backends.time.sleep'):
                result=run_colab(args)
            self.assertEqual(result['jobs']['a']['status'],'done')
            self.assertNotIn('upload',transport.calls)

    def test_source_archive_excludes_user_media_and_secrets(self):
        from studio.backends import archive_application
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'studio').mkdir();(root/'studio/a.py').write_text('# app')
            (root/'secrets.json').write_text('secret');(root/'studio/private.mp4').write_bytes(b'private')
            (root/'out').mkdir();(root/'out/token.txt').write_text('secret')
            target=root/'source.zip';archive_application(root,target)
            with zipfile.ZipFile(target) as z:
                self.assertEqual(z.namelist(),['studio/a.py'])
