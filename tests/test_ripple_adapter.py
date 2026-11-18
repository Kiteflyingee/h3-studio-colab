import json
import tempfile
import unittest
from pathlib import Path
from _pil_compat import Image
from studio.media import ffmpeg, describe_media


def _mock_ripple_object_info():
    return {
        'UNETLoader': {'input': {'required': {'unet_name': [['minimax_h3_fl2va_pruned_int8_convrot.safetensors']], 'weight_dtype': [['default']]}}, 'output': ['MODEL']},
        'LoraLoaderModelOnly': {'input': {'required': {'model': ['MODEL'], 'lora_name': [['pika_h3_flex_fl2va_v1_bf16.safetensors', 'minimax_H3_Ripple-v1.safetensors']], 'strength_model': ['FLOAT', {'min': -10, 'max': 10}]}}, 'output': ['MODEL']},
        'CLIPLoader': {'input': {'required': {'clip_name': [['qwen3vl_32b_minimax_h3_int8_convrot.safetensors']], 'type': [['minimax']], 'device': [['default']]}}, 'output': ['CLIP']},
        'VAELoader': {'input': {'required': {'vae_name': [['minimax_h3_video_vae_fp16.safetensors']]}}, 'output': ['VAE']},
        'LoadVideo': {'input': {'required': {'file': [['a.mp4']]}}, 'output': ['VIDEO']},
        'GetVideoComponents': {'input': {'required': {'video': ['VIDEO']}}, 'output': ['IMAGE', 'AUDIO', 'FLOAT']},
        'LoadImage': {'input': {'required': {'image': [['a.png']]}}, 'output': ['IMAGE', 'MASK']},
        'ImageFromBatch': {'input': {'required': {'image': ['IMAGE'], 'batch_index': ['INT', {'min': 0}], 'length': ['INT', {'min': 1}]}}, 'output': ['IMAGE']},
        'RepeatImageBatch': {'input': {'required': {'image': ['IMAGE'], 'amount': ['INT', {'min': 1}]}}, 'output': ['IMAGE']},
        'ImageBatch': {'input': {'required': {'image1': ['IMAGE'], 'image2': ['IMAGE']}}, 'output': ['IMAGE']},
        'WN_RippleFromFrames': {
            'input': {
                'required': {
                    'images': ['IMAGE'], 'frame_rate': ['FLOAT', {'min': 0.001, 'max': 1000.0}],
                    'duration': ['FLOAT', {'min': 0.2, 'max': 150.0}], 'megapixels': ['FLOAT', {'min': 0.001, 'max': 268.0}],
                    'window_start': ['INT', {'min': 0}], 'markers': ['STRING', {}],
                },
                'optional': {'audio': ['AUDIO'], 'crop': ['STRING', {}], 'allow_upscale': ['BOOLEAN', {}], 'resize_method': [['lanczos', 'bicubic']]},
            },
            'output': ['IMAGE', 'WN_RIPPLE', 'IMAGE', 'AUDIO'] + ['IMAGE'] * 16,
        },
        'WN_RippleApply': {
            'input': {
                'required': {'clip': ['CLIP'], 'vae': ['VAE'], 'ripple': ['WN_RIPPLE'], 'prompt': ['STRING', {}]},
                'optional': {f'edited_anchor_{i}': ['IMAGE'] for i in range(1, 17)},
            },
            'output': ['CONDITIONING', 'LATENT'],
        },
        'WN_RippleOutput': {
            'input': {
                'required': {'images': ['IMAGE'], 'ripple': ['WN_RIPPLE']},
                'optional': {'save': [['shot + comparison', 'shot', 'comparison', 'nothing']], 'filename_prefix': ['STRING', {}], 'show_anchor_markers': ['BOOLEAN', {}]},
            },
            'output': ['IMAGE', 'AUDIO', 'FLOAT', 'IMAGE'],
        },
        'QwenEditAlignComposite': {
            'input': {
                'required': {
                    'source': ['IMAGE'], 'edited': ['IMAGE'], 'mode': [['translation', 'affine']],
                    'auto_mask_threshold': ['FLOAT', {'min': 0.0, 'max': 1.0}], 'grow_px': ['INT', {'min': 0, 'max': 256}], 'feather_px': ['INT', {'min': 0, 'max': 256}],
                },
                'optional': {'edit_mask': ['MASK'], 'warp_params': ['STRING', {}]},
            },
            'output': ['IMAGE', 'IMAGE', 'MASK', 'STRING'],
        },
        'RandomNoise': {'input': {'required': {'noise_seed': ['INT', {}]}}, 'output': ['NOISE']},
        'KSamplerSelect': {'input': {'required': {'sampler_name': [['lcm', 'euler']]}}, 'output': ['SAMPLER']},
        'BasicScheduler': {'input': {'required': {'model': ['MODEL'], 'scheduler': [['simple']], 'steps': ['INT', {'min': 1}], 'denoise': ['FLOAT', {'min': 0.0, 'max': 1.0}]}}, 'output': ['SIGMAS']},
        'BasicGuider': {'input': {'required': {'model': ['MODEL'], 'conditioning': ['CONDITIONING']}}, 'output': ['GUIDER']},
        'SamplerCustomAdvanced': {'input': {'required': {'noise': ['NOISE'], 'guider': ['GUIDER'], 'sampler': ['SAMPLER'], 'sigmas': ['SIGMAS'], 'latent_image': ['LATENT']}}, 'output': ['LATENT', 'LATENT']},
        'VAEDecode': {'input': {'required': {'samples': ['LATENT'], 'vae': ['VAE']}}, 'output': ['IMAGE']},
    }


class _MockRippleClient:
    def info(self): return _mock_ripple_object_info()
    def models(self, group):
        return {
            'diffusion_models': ['minimax_h3_fl2va_pruned_int8_convrot.safetensors'],
            'loras': ['pika_h3_flex_fl2va_v1_bf16.safetensors', 'minimax_H3_Ripple-v1.safetensors'],
            'text_encoders': ['qwen3vl_32b_minimax_h3_int8_convrot.safetensors'],
            'vae': ['minimax_h3_video_vae_fp16.safetensors'],
        }.get(group, [])


class TwoStepRippleRemakeTests(unittest.TestCase):
    def test_two_step_anchor_generation_and_confirmed_ripple_compilation(self):
        from studio.remake import (
            compile_anchor_plan, materialize_anchor_plan, finalize_anchors, compile_remake,
        )
        from studio.remake_remote import attach_local_confirmed_anchors
        from studio.model_registry import required_components

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'source.mp4'
            ref_img = root / 'cat.png'
            ffmpeg(['-f', 'lavfi', '-i', 'color=c=blue:s=128x72:r=24:d=4.5', '-c:v', 'libx264', source])
            Image.new('RGB', (64, 64), 'red').save(ref_img)

            req = {
                'source_video': str(source),
                'characters': [{'target_id': 'the host', 'reference': str(ref_img)}],
            }
            analysis = {'seconds': 4.5, 'shots': [{'start': 0, 'end': 4.5}]}

            # Step 1: compile & materialize anchor jobs (2 anchors for >=4s shot: start & end)
            folder = root / 'out/remake/variant001'
            aplan = compile_anchor_plan(req, analysis)
            self.assertEqual(len(aplan['jobs']), 2)
            self.assertTrue(all(j['type'] == 'image' and j['align_to_first'] for j in aplan['jobs']))
            mpath = materialize_anchor_plan(aplan, folder)
            self.assertTrue(mpath.is_file())

            # Before Step 1 finishes, attach_local_confirmed_anchors blocks Step 2
            with self.assertRaisesRegex(ValueError, 'remake-anchors'):
                attach_local_confirmed_anchors(req, root / 'out')

            # Simulate Step 1 completion & finalize side-by-side comparison PNGs
            fake_results = {'jobs': {}}
            for job in aplan['jobs']:
                gen = root / (job['id'] + '_gen.png')
                Image.new('RGB', (128, 72), 'green').save(gen)
                fake_results['jobs'][job['id']] = {'status': 'done', 'artifacts': [{'path': str(gen)}], 'primary_index': 0}
            saved = finalize_anchors(aplan, folder, fake_results)
            self.assertEqual(len(saved), 2)
            self.assertTrue(all(ed.is_file() and cmp_p.is_file() for ed, cmp_p in saved))
            self.assertEqual(describe_media(saved[0][1])['width'], 256)

            # Step 2: attach confirmed anchors & compile lightweight 5-component H3 Ripple plan
            attached = attach_local_confirmed_anchors(req, root / 'out')
            plan = compile_remake(attached, analysis, anchors_dir=folder / 'anchors', require_confirmed_anchors=True)
            visual = plan['jobs'][0]
            self.assertEqual(visual['profile'], 'h3-ripple')
            self.assertEqual(len(visual['anchors']), 2)

            # Shared FL2VA base model across Step 1 (ph3-lora-int8) and Step 2 (h3-ripple)
            combined = required_components(aplan['jobs'] + [visual])
            files = [c['file'] for c in combined]
            self.assertEqual(len(files), len(set(files)))
            self.assertEqual(sum('fl2va_pruned' in f for f in files), 1)
            self.assertIn('minimax_H3_Ripple-v1.safetensors', files)

    def test_ripple_graph_passes_schema_preflight_and_tracks_dependencies(self):
        from studio.adapters.video_edit import build_job
        from studio.manifest import dependencies, file_values
        from studio.model_registry import select_profile, required_components
        from studio.preflight import check_workflow

        job = {
            'id': 'shot2', 'type': 'video_edit', 'source_video': 'clip.mp4', 'seconds': 3.75,
            'anchors': [{'frame': 0, 'image': '@edit_img'}, {'frame': 45, 'image': 'edited_45.png'}],
        }
        self.assertEqual(select_profile(job), 'h3-ripple')
        self.assertIn('edit_img', dependencies(job, {'edit_img'}))
        self.assertIn('edited_45.png', file_values(job))
        self.assertEqual(len(required_components([job])), 5)

        staged = {
            'source_video': {'kind': 'video', 'name': 'clip.mp4', 'width': 640, 'height': 360, 'seconds': 3.75, 'frames': 90, 'has_audio': False},
            'anchors': [
                {'frame': 0, 'image_media': {'kind': 'image', 'name': 'ed0.png'}},
                {'frame': 45, 'image_media': {'kind': 'image', 'name': 'ed45.png'}},
            ],
        }
        graph = build_job(job, staged, 'h3-ripple')
        report = check_workflow(graph, 'h3-ripple', _MockRippleClient())
        self.assertTrue(report.ready, report.errors)

    def test_recreate_mode_packs_short_cuts_and_builds_640p_silent_proxy(self):
        from studio.remake import compile_remake, materialize_plan

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'viral.mp4'
            cat = root / 'cat.png'
            ffmpeg([
                '-f', 'lavfi', '-i', 'color=c=red:s=1280x720:r=24:d=7',
                '-f', 'lavfi', '-i', 'sine=duration=7',
                '-c:v', 'libx264', '-c:a', 'aac', source,
            ])
            Image.new('RGB', (64, 64), 'blue').save(cat)
            analysis = {'seconds': 7.0, 'shots': [{'start': 0, 'end': 2.2}, {'start': 2.2, 'end': 4.6}, {'start': 4.6, 'end': 7.0}]}
            req = {'source_video': str(source), 'mode': 'recreate', 'characters': [{'target_id': 'lead', 'reference': str(cat)}]}
            plan = compile_remake(req, analysis)
            self.assertEqual(len(plan['jobs']), 1)
            self.assertEqual(plan['jobs'][0]['profile'], 'h3-ref2va-int8')
            mpath = materialize_plan(plan, root / 'work')
            manifest = json.loads(mpath.read_text())
            proxy = Path(manifest['jobs'][0]['inputs'][-1])
            info = describe_media(proxy)
            self.assertEqual(info['width'], 640)
            self.assertFalse(info['has_audio'])
