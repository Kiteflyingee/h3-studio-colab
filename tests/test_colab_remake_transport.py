import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.media import ffmpeg,describe_media
from studio.model_registry import file_sha256

ROOT=Path(__file__).resolve().parents[1]


class FilesystemTransport:
    """Runs the actual uploaded worker in a temporary filesystem, without a VM/GPU."""
    def __init__(self):
        self.actions=[]
        self.children=[]
        self.fail_download=False
        self.repeat_launch=False
    def colab(self,action,*args,**kwargs):
        self.actions.append(action)
        if action in ('upload','download'):
            source,target=args[-2:];Path(target).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
            if action=='download' and self.fail_download:
                self.fail_download=False
                raise RuntimeError('simulated transfer interruption')
        return ''
    def remote_exec(self,session,code):
        stream=io.StringIO()
        popen=subprocess.Popen
        def record(*args,**kwargs):
            child=popen(*args,**kwargs)
            self.children.append(child)
            return child
        with contextlib.redirect_stdout(stream),patch('subprocess.Popen',side_effect=record):
            exec(compile(code,'<transport>','exec'),{})
            if self.repeat_launch and 'H3STARTED' in code:
                exec(compile(code,'<transport>','exec'),{})
        return stream.getvalue()
    def set_keep(self,*args,**kwargs):pass
    def start_watchdog(self,*args):pass
    def drop_keep(self,*args):pass


class ColabRemakeTransportTests(unittest.TestCase):
    def test_actual_remote_worker_renders_two_copy_variants_and_downloads_them(self):
        from studio.cli import remake_command
        import argparse
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'source.mp4'
            ffmpeg(['-f','lavfi','-i','color=c=blue:s=128x128:r=30:d=2','-c:v','libx264',source])
            request=root/'request.json';request.write_text(json.dumps({'source_video':'source.mp4','variants':[
                {'name':'version_a','copy':{'subtitles':[{'start':0,'end':2,'text':'FIRST'}]}},
                {'name':'version_b','copy':{'subtitles':[{'start':0,'end':2,'text':'SECOND'}]}}]}))
            args=argparse.Namespace(command='remake-render',request=str(request),backend='colab',output=str(root/'final.mp4'),
                work_dir=str(root/'out'),session='existing',gpu='L4',resume=False,confirmed=True,allow_unverified=False,
                setup_runtime=True,install_models=True,stop_after=True,idle_minutes=0,max_minutes=1)
            transport=FilesystemTransport()
            transport.fail_download=True
            transport.repeat_launch=True
            sleep=time.sleep
            try:
                with patch('studio.backends.REMOTE',str(root/'remote')),patch('studio.backends.legacy_transport',return_value=transport),patch('studio.backends.time.sleep',side_effect=lambda _:sleep(.05)),contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError,'simulated transfer interruption'):
                        remake_command(args)
                    self.assertNotIn('stop',transport.actions)
                    args.resume=True
                    report=remake_command(args)
                    uploads=transport.actions.count('upload')
                    downloads=transport.actions.count('download')
                    self.assertEqual(remake_command(args),report)
                    self.assertEqual(transport.actions.count('upload'),uploads)
                    self.assertEqual(transport.actions.count('download'),downloads)
            finally:
                for child in transport.children:child.wait(timeout=15)
            self.assertEqual(len(transport.children),1)
            for version in ('version_a','version_b'):
                path=root/('final_'+version+'.mp4')
                self.assertTrue(path.is_file())
                media=describe_media(path)
                self.assertAlmostEqual(media['seconds'],2,delta=.07)
                self.assertEqual(media['fps_float'],30)
                self.assertFalse(media['has_audio'])
            self.assertNotEqual(file_sha256(root/'final_version_a.mp4'),file_sha256(root/'final_version_b.mp4'))
            self.assertTrue((root/'out/remake/analysis.json').is_file())
            self.assertTrue((root/'out/remake/version_a/manifest.json').is_file())
            self.assertTrue((root/'out/remake/version_b/manifest.json').is_file())
            self.assertIn('upload',transport.actions);self.assertIn('download',transport.actions);self.assertIn('stop',transport.actions)
            self.assertEqual(report['jobs']['delivery']['status'],'done')
            self.assertFalse(any(p.suffix=='.ipynb' for p in (root/'remote').rglob('*')))

    def test_actual_worker_composes_director_shots_before_downloading(self):
        import argparse
        from studio.cli import run_command
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            jobs=[];shots=[]
            for name,color in [('a','blue'),('b','red')]:
                source=root/f'{name}.mp4'
                ffmpeg(['-f','lavfi','-i',f'color=c={color}:s=96x96:r=24:d=2','-c:v','libx264',source])
                jobs.append({'id':name,'type':'media_copy','source_video':source.name})
                shots.append({'id':name,'seconds':2})
            manifest=root/'jobs.json'
            manifest.write_text(json.dumps({'jobs':jobs,'director':{'aspect':'16:9','shots':shots}}))
            args=argparse.Namespace(manifest=str(manifest),backend='colab',output_dir=str(root/'out'),
                session=None,gpu=None,resume=False,confirmed=True,allow_unverified=False,only=None,
                with_dependents=False,setup_runtime=True,install_models=True,stop_after=False,idle_minutes=0,max_minutes=1)
            transport=FilesystemTransport()
            sleep=time.sleep
            try:
                with patch('studio.backends.REMOTE',str(root/'remote')),patch('studio.backends.legacy_transport',return_value=transport),patch('studio.backends.time.sleep',side_effect=lambda _:sleep(.05)),contextlib.redirect_stdout(io.StringIO()):
                    report=run_command(args)
            finally:
                for child in transport.children:child.wait(timeout=15)
            delivery=Path(report['jobs']['director-delivery']['file'])
            self.assertTrue(delivery.is_file())
            self.assertAlmostEqual(describe_media(delivery)['seconds'],4,delta=.1)
            self.assertEqual(args.gpu,'CPU')
            self.assertIn('new',transport.actions)
            self.assertIn('stop',transport.actions)
