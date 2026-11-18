"""原生 MiniMax H3 视频与 P-H3-Image 图片适配器。"""
import json
import re
from pathlib import Path
from .. import geometry as G
from ..model_registry import profiles, catalog, resolve_video_vae_key
from .common import collect_job

ROOT = Path(__file__).resolve().parents[2]


def node(cls, **inputs):
    return {'class_type': cls, 'inputs': inputs}


def component_name(profile_id, folder, contains=None, job=None):
    items = catalog()
    cfg = profiles()[profile_id]
    if folder == 'vae' and contains == 'video_vae' and job and job.get('video_vae'):
        key = resolve_video_vae_key(profile_id, job)
        return items[key]['relative_target'][len('vae/'):]
    for key in cfg['components']:
        c = items[key]
        if c['relative_target'].startswith(folder + '/') and (contains is None or contains in c['file']):
            return c['relative_target'][len(folder) + 1:]
    raise ValueError(f'{profile_id} 缺少 {folder} 组件：{contains or ""}')


def base_video(profile_id, job):
    cfg = profiles()[profile_id]
    sampling = cfg.get('sampling', {})
    fast = job.get('fast', True)
    graph = {
        'model': node('UNETLoader', unet_name=component_name(profile_id, 'diffusion_models'), weight_dtype='default'),
        'clip': node('CLIPLoader', clip_name=component_name(profile_id, 'text_encoders'), type='minimax', device='default'),
        'vae': node('VAELoader', vae_name=component_name(profile_id, 'vae', 'video_vae', job=job)),
        'audio_vae': node('VAELoader', vae_name=component_name(profile_id, 'vae', 'audio_vae')),
        'noise': node('RandomNoise', noise_seed=int(job.get('seed', 0))),
        'sampler': node('KSamplerSelect', sampler_name=sampling.get('sampler', 'res_multistep')),
        'schedule': node(
            'BasicScheduler',
            model=['model', 0],
            scheduler=sampling.get('scheduler', 'simple'),
            steps=int(job.get('steps') or sampling.get('fast_steps' if fast else 'normal_steps', 20)),
            denoise=1.0,
        ),
        'guider': node('BasicGuider', model=['model', 0], conditioning=['cond', 0]),
        'sample': node(
            'SamplerCustomAdvanced',
            noise=['noise', 0],
            guider=['guider', 0],
            sampler=['sampler', 0],
            sigmas=['schedule', 0],
            latent_image=['cond', 1],
        ),
        'decode': node('VAEDecode', samples=['sample', 0], vae=['vae', 0]),
        'decode_audio': node('VAEDecodeAudio', samples=['sample', 0], vae=['audio_vae', 0]),
        'create': node('CreateVideo', images=['decode', 0], audio=['decode_audio', 0], fps=24.0),
        'save': node('SaveVideo', video=['create', 0], filename_prefix='h3studio/' + job['id'], format='auto', codec='auto'),
    }
    model = ['model', 0]
    if fast and any('turbo' in catalog()[key]['file'] for key in cfg['components']):
        graph['turbo'] = node(
            'LoraLoaderModelOnly',
            model=model,
            lora_name=component_name(profile_id, 'loras', 'turbo'),
            strength_model=1.0,
        )
        model = ['turbo', 0]
    if 'shift_video' in sampling:
        graph['shift'] = node(
            'MiniMaxH3SigmaShift',
            model=model,
            shift_video=sampling['shift_video'],
            shift_audio=sampling['shift_audio'],
        )
        model = ['shift', 0]
    graph['guider']['inputs']['model'] = model
    graph['schedule']['inputs']['model'] = model
    return graph


def build_job(job, staged, profile_id):
    if job['type'] == 'image':
        return build_picture(job, staged, profile_id)
    kwargs = {k: staged[k] for k in ('inputs', 'first', 'last') if k in staged}
    planning_job = dict(job)
    if job.get('mode') in ('guides', 'control'):
        planning_job['mode'] = 'ref'
    plan = G.plan_video(planning_job, **kwargs)
    if job.get('aspect', 'auto') == 'auto':
        candidates = [x for x in staged.get('inputs', []) if x.get('kind') in ('video', 'image')]
        if staged.get('extension'):
            candidates.insert(0, staged['extension'].get('tail', {}))
        if candidates and candidates[0].get('width') and candidates[0].get('height'):
            plan['width'], plan['height'] = G.dims(
                'auto', job.get('resolution', '480P'), (candidates[0]['width'], candidates[0]['height'])
            )
    graph = base_video(profile_id, job)
    seconds = float(job.get('seconds', 10))
    if staged.get('extension'):
        seconds += staged['extension']['context_seconds']
    frames = max(5, round(seconds * 24))
    frames += (5 - frames) % 17
    cond = dict(
        clip=['clip', 0],
        vae=['vae', 0],
        prompt=plan['final_prompt'],
        width=plan['width'],
        height=plan['height'],
        length=frames,
    )
    if plan['mode'] == 'ref':
        cond.update(audio_vae=['audio_vae', 0], ref_image_size='match')
        graph['cond'] = node('MiniMaxH3ReferenceToVideo', **cond)
        for i, item in enumerate(plan['chosen']['image']):
            graph[f'ri{i}'] = node('LoadImage', image=item['name'])
            cond[f'ref_images.ref_image_{i}'] = [f'ri{i}', 0]
        for i, item in enumerate(plan['chosen']['video']):
            graph[f'rv{i}'] = node('LoadVideo', file=item['name'])
            graph[f'rc{i}'] = node('GetVideoComponents', video=[f'rv{i}', 0])
            cond[f'ref_videos.ref_video_{i}'] = [f'rc{i}', 0]
            if item.get('has_audio'):
                cond[f'ref_video_audios.ref_video_audio_{i}'] = [f'rc{i}', 1]
        for i, item in enumerate(plan['chosen']['audio']):
            graph[f'ra{i}'] = node('LoadAudio', audio=item['name'])
            cond[f'ref_audios.ref_audio_{i}'] = [f'ra{i}', 0]
        graph['cond']['inputs'] = cond
    else:
        graph['cond'] = node('MiniMaxH3ImageToVideo', **cond)
        for slot in ('first', 'last'):
            if staged.get(slot):
                graph[slot] = node('LoadImage', image=staged[slot]['name'])
                graph[slot + '_scale'] = node(
                    'ImageScale',
                    image=[slot, 0],
                    upscale_method='lanczos',
                    width=plan['width'],
                    height=plan['height'],
                    crop='disabled',
                )
                graph['cond']['inputs'][slot + '_scale'[:-6] + '_frame'] = [slot + '_scale', 0]
    if staged.get('extension'):
        from .extension import apply_context
        graph = apply_context(graph, staged['extension'])
    return graph


def build_picture(job, staged, profile_id):
    graph = json.loads((ROOT / 'workflows/h3_picture_api.json').read_text(encoding='utf-8'))
    refs = staged.get('inputs', [])
    if len(refs) > 8 or any(r['kind'] != 'image' for r in refs):
        raise ValueError('图片编辑只接受最多 8 张图片')
    prompt = job['prompt']

    def mention(match):
        index = int(match.group(1))
        if not 1 <= index <= len(refs):
            raise ValueError(f'找不到参考图片 {index}')
        return f'<Picture {index}>'

    prompt = re.sub(r'@(?:图片|圖片|Image)\s*(\d+)', mention, prompt, flags=re.I)
    w, h = G.image_dims(job.get('aspect', 'auto'), job.get('size', '2K'), refs)
    graph['iu']['inputs']['unet_name'] = component_name(profile_id, 'diffusion_models')
    graph['il']['inputs']['clip_name'] = component_name(profile_id, 'text_encoders', 'text_encoder')
    graph['iv']['inputs']['vae_name'] = component_name(profile_id, 'vae')
    graph['ienc']['inputs'].update(prompt=prompt, width=int(job.get('width') or w), height=int(job.get('height') or h))
    graph['in']['inputs']['noise_seed'] = int(job.get('seed', 0))
    graph['isave']['inputs']['filename_prefix'] = 'h3studio/' + job['id']
    if profile_id == 'ph3-standalone-w4a8':
        G.bypass(graph, 'ilo')
    for i, item in enumerate(refs, 1):
        graph[f'ir{i}'] = node('LoadImage', image=item['name'])
        graph['ienc']['inputs'][f'images.image_{i}'] = [f'ir{i}', 0]
    if job.get('upsample'):
        key = profiles()[profile_id]['upsampler_components'][0]
        model = catalog()[key]['relative_target'].split('/', 1)[1]
        graph['upclip'] = node('CLIPLoader', clip_name=model, type='minimax', device='default')
        graph['upsample'] = node(
            'TextGenerate',
            clip=['upclip', 0],
            prompt=f'Aspect ratio: {job.get("aspect", "1:1")}\nRequest: {prompt}',
            max_length=2048,
            sampling_mode='off',
            thinking=True,
            use_default_template=True,
            mtp='auto',
        )
        if refs:
            for i, _ in enumerate(refs, 1):
                graph[f'upscale{i}'] = node(
                    'ImageScale', image=[f'ir{i}', 0], upscale_method='lanczos', width=1024, height=1024, crop='disabled'
                )
            previous = ['upscale1', 0]
            for i in range(2, len(refs) + 1):
                graph[f'upbatch{i}'] = node('ImageBatch', image1=previous, image2=[f'upscale{i}', 0])
                previous = [f'upbatch{i}', 0]
            graph['upsample']['inputs']['image'] = previous
        graph['ienc']['inputs']['prompt'] = ['upsample', 0]
    if job.get('align_to_first'):
        if not refs:
            raise ValueError('align_to_first 需要至少 1 张源参考图片作为对齐基准')
        align_inputs = dict(
            source=['ir1', 0],
            edited=['id', 0],
            mode='affine',
            auto_mask_threshold=0.08,
            grow_px=2,
            feather_px=2,
        )
        if staged.get('align_mask'):
            graph['align_mask_img'] = node('LoadImage', image=staged['align_mask']['name'])
            graph['align_mask'] = node('ImageToMask', image=['align_mask_img', 0], channel='red')
            align_inputs['edit_mask'] = ['align_mask', 0]
        graph['align'] = node('QwenEditAlignComposite', **align_inputs)
        graph['isave']['inputs']['images'] = ['align', 0]
    return graph
