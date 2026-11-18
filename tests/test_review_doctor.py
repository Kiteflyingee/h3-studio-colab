import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from _pil_compat import Image


class Client:
    def stats(self):return {}
    def info(self):return {'SaveImage':{'input':{'required':{}}}}


class DoctorReviewTests(unittest.TestCase):
    def test_nested_guide_media_is_described_without_upload(self):
        from studio.cli import doctor_command
        class Adapter:
            LOCAL=False
            @staticmethod
            def build_job(job,staged,profile):
                assert staged['guides'][0]['media']['name']
                return {'s':{'class_type':'SaveImage','inputs':{}}}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);image=root/'r.png';Image.new('RGB',(64,64)).save(image)
            p=root/'m.json';p.write_text(json.dumps({'jobs':[{'id':'a','type':'video','mode':'guides','prompt':'lake','seconds':3,'guides':[{'at_seconds':0,'image':'r.png'}]}]}))
            args=argparse.Namespace(manifest=str(p),offline=False,url='http://localhost:8188',backend='current')
            buffer=io.StringIO()
            with contextlib.redirect_stdout(buffer),patch('studio.cli.ComfyClient',return_value=Client()),patch('studio.executor.adapter_for',return_value=Adapter):doctor_command(args)
            data=json.loads(buffer.getvalue())
            self.assertTrue(data['checks'][0]['environment_ready'],data)

    def test_ripple_readiness_checks_actual_node(self):
        from studio.cli import doctor_command
        from studio.media import ffmpeg
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);Image.new('RGB',(64,64)).save(root/'r.png');p=root/'m.json'
            ffmpeg(['-f','lavfi','-i','color=c=blue:s=64x64:r=24:d=2','-c:v','libx264',root/'s.mp4'])
            p.write_text(json.dumps({'jobs':[{'id':'a','type':'video_edit','source_video':'s.mp4','anchors':[{'frame':0,'image':'r.png'}]}]}))
            args=argparse.Namespace(manifest=str(p),offline=False,url='http://localhost:8188',backend='local')
            buffer=io.StringIO()
            with contextlib.redirect_stdout(buffer),patch('studio.cli.ComfyClient',return_value=Client()):doctor_command(args)
            row=json.loads(buffer.getvalue())['checks'][0]
            self.assertIn('errors',row)
            self.assertFalse(row['environment_ready'])
