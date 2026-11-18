import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from _pil_compat import Image
from test_executor import FakeAdapter, FakeClient
from studio.contracts import ExecutionContext
from studio.media import ffmpeg, describe_media


class ReviewRecoveryTests(unittest.TestCase):
    def test_history_disconnect_never_resubmits(self):
        from studio.executor import execute_manifest
        class Client(FakeClient):
            def wait(self, *args): raise ConnectionError('history unavailable')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); p=root/'m.json'; p.write_text(json.dumps({'jobs':[{'id':'a','type':'image','prompt':'red'}]}))
            c=Client(); ctx=ExecutionContext(root,root/'out',None,'current',c,True)
            with patch('studio.executor.adapter_for', return_value=FakeAdapter):
                for _ in range(3): execute_manifest(p,ctx,resume=True)
            self.assertEqual(c.submissions,1)

    def test_download_failure_resumes_collection_by_prompt(self):
        from studio.executor import execute_manifest
        class Client(FakeClient):
            attempts=0
            def save_result(self,*args):
                self.attempts+=1
                if self.attempts==1:raise ConnectionError('download interrupted')
                return super().save_result(*args)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'m.json';p.write_text(json.dumps({'jobs':[{'id':'a','type':'image','prompt':'red'}]}))
            c=Client();ctx=ExecutionContext(root,root/'out',None,'current',c,True)
            with patch('studio.executor.adapter_for',return_value=FakeAdapter):
                execute_manifest(p,ctx); result=execute_manifest(p,ctx,resume=True)
            self.assertEqual(c.submissions,1)
            self.assertEqual(result['jobs']['a']['status'],'done')

    def test_dependency_only_child_invalidates(self):
        from studio.executor import execute_manifest
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'m.json';c=FakeClient();ctx=ExecutionContext(root,root/'out',None,'current',c,True)
            jobs=[{'id':'a','type':'image','prompt':'red'},{'id':'b','type':'image','prompt':'same','depends_on':['a']}]
            with patch('studio.executor.adapter_for',return_value=FakeAdapter):
                p.write_text(json.dumps({'jobs':jobs}));execute_manifest(p,ctx)
                jobs[0]['prompt']='blue';p.write_text(json.dumps({'jobs':jobs}));execute_manifest(p,ctx,resume=True)
            self.assertEqual(c.submissions,4)

    def test_concurrent_report_writer_is_rejected(self):
        from studio.executor import execute_manifest
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'m.json';p.write_text(json.dumps({'jobs':[{'id':'a','type':'image','prompt':'red'}]}))
            c=FakeClient();ctx=ExecutionContext(root,root/'out',None,'current',c,True)
            original=c.wait
            def nested(*args):
                with self.assertRaisesRegex(RuntimeError,'运行|锁'):
                    execute_manifest(p,ctx,resume=True)
                return original(*args)
            c.wait=nested
            with patch('studio.executor.adapter_for',return_value=FakeAdapter): result=execute_manifest(p,ctx)
            self.assertEqual(result['jobs']['a']['status'],'done')


class ReviewGraphTests(unittest.TestCase):
    def test_incompatible_profiles_are_rejected(self):
        from studio.model_registry import select_profile
        for job in ({'type':'video','mode':'frames','profile':'h3-ref2va-int8'},
                    {'type':'video_edit','profile':'fasth3-omni-int8'}):
            with self.assertRaises(ValueError):select_profile(job)

    def test_portrait_source_resolves_auto_geometry(self):
        from studio.adapters.h3 import build_job
        g=build_job({'id':'a','type':'video','mode':'ref','prompt':'Follow @视频1.','aspect':'auto','seconds':3},
                    {'inputs':[{'name':'v.mp4','kind':'video','width':540,'height':960,'seconds':3}]},'h3-ref2va-int8')
        self.assertLess(g['cond']['inputs']['width'],g['cond']['inputs']['height'])

    def test_fast_profiles_use_locked_upstream_euler(self):
        from studio.model_registry import profiles
        for name in ('fasth3','fasth3-omni-int8'):
            self.assertEqual(profiles()[name]['sampling']['sampler'],'euler')

    def test_picture_align_to_first_wires_qwen_edit_align_composite(self):
        from studio.adapters.h3 import build_job
        g=build_job({'id':'a','type':'image','prompt':'Edit @图片1 using @图片2.','align_to_first':True},
                    {'inputs':[{'name':'src.png','kind':'image','width':640,'height':360},{'name':'ref.png','kind':'image','width':512,'height':512}],
                     'align_mask':{'name':'m.png','kind':'image','width':640,'height':360}},'ph3-lora-int8')
        self.assertEqual(g['align']['class_type'],'QwenEditAlignComposite')
        self.assertEqual(g['isave']['inputs']['images'],['align',0])


class ReviewRemakeTests(unittest.TestCase):
    def test_direct_video_edit_requires_confirmed_anchors(self):
        from studio.manifest import validate_manifest
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'source.mp4').write_bytes(b'video')
            errors=validate_manifest({'jobs':[{'id':'a','type':'video_edit','source_video':'source.mp4'}]},root)
            self.assertTrue(any('anchors' in error for error in errors))

    def test_timed_edit_never_changes_outside_interval(self):
        from studio.remake import compile_remake
        p=compile_remake({'source_video':'s.mp4','products':[{'target_id':'bottle','reference':'p.png','time_range':[7,9]}]},
                         {'seconds':10,'shots':[{'start':0,'end':5},{'start':5,'end':10}]})
        edits=[j for j in p['jobs'] if j['type']=='video_edit']
        self.assertTrue(edits)
        self.assertTrue(all(j['metadata']['source_start']>=7 and j['metadata']['source_end']<=9 for j in edits))

    def test_remake_render_blocks_when_step1_anchors_not_confirmed(self):
        from studio.remake import compile_remake
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'remake-anchors'):
                compile_remake({'source_video':'s.mp4','products':[{'target_id':'b','reference':'r.png'}]},
                               {'seconds':4,'shots':[{'start':0,'end':4}]},anchors_dir=Path(d)/'anchors',require_confirmed_anchors=True)

    def test_segments_are_cut_at_matching_source_times(self):
        from studio.remake import compile_remake, materialize_plan
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);video=root/'source.mp4'
            ffmpeg(['-f','lavfi','-i','color=c=red:s=64x64:r=24:d=4','-c:v','libx264',video])
            plan=compile_remake({'source_video':str(video),'products':[{'target_id':'b','reference':'r.png'}]},
                                {'seconds':4,'shots':[{'start':0,'end':2},{'start':2,'end':4}]})
            manifest=json.loads(materialize_plan(plan,root/'work').read_text())
            paths=[j['source_video'] for j in manifest['jobs']]
            self.assertNotEqual(paths[0],paths[1])
            self.assertTrue(all(abs(describe_media(p)['seconds']-2)<.07 for p in paths))


class ReviewMediaTests(unittest.TestCase):
    def test_primary_image_comes_from_final_save_node(self):
        from studio.adapters.common import collect_job
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);preview=root/'preview.png';final=root/'final.png'
            Image.new('RGB',(32,32),'red').save(preview);Image.new('RGB',(32,32),'blue').save(final)
            class Client:
                last_downloads=[{'path':str(preview),'node':'preview'},{'path':str(final),'node':'isave'}]
                def save_result(self,*args):return [preview,final]
            result=collect_job({'id':'a','type':'image'}, {}, ExecutionContext(root,root,None,'current',Client()))
            with Image.open(result.artifacts[result.primary_index].path) as image:
                self.assertEqual(image.getpixel((0,0)),(0,0,255))

    def test_mixed_outputs_all_enter_result_record(self):
        from studio.adapters.common import collect_job
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);image=root/'image.png';audio=root/'voice.wav';Image.new('RGB',(32,32)).save(image)
            ffmpeg(['-f','lavfi','-i','sine=duration=1',audio])
            class Client:
                def save_result(self,*args):return [image,audio]
            result=collect_job({'id':'a','type':'image'}, {}, ExecutionContext(root,root,None,'current',Client()))
            self.assertEqual({x.kind for x in result.artifacts},{'image','audio'})

    def test_webm_output_is_delivered_in_valid_container(self):
        from studio.adapters.common import collect_job
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);video=root/'raw.webm';ffmpeg(['-f','lavfi','-i','color=c=red:s=64x64:r=24:d=2','-c:v','libvpx-vp9',video])
            class Client:
                def save_result(self,*args):return [video]
            result=collect_job({'id':'a','type':'video','seconds':2},{},ExecutionContext(root,root,None,'current',Client()))
            self.assertEqual(describe_media(result.artifacts[0].path)['kind'],'video')

    def test_cumulative_extension_keeps_source_rate_and_canvas(self):
        from studio.adapters.extension import finalize_extension
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'work').mkdir();source=root/'source.mp4';gen=root/'generated.mp4'
            ffmpeg(['-f','lavfi','-i','color=c=red:s=64x96:r=60:d=1','-c:v','libx264',source])
            ffmpeg(['-f','lavfi','-i','color=c=blue:s=96x64:r=24:d=2','-c:v','libx264',gen])
            ctx=ExecutionContext(root,root,None,'current',None)
            out=finalize_extension(source,gen,{'id':'e','seconds':1,'_context_seconds':1},ctx)
            info=describe_media(out.path)
            self.assertEqual((info['width'],info['height'],info['fps_float']),(64,96,60))
