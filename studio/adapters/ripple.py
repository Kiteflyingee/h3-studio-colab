"""WepeNerd H3 Ripple 关键帧编辑传播适配器（Step 2：将已确认的精修锚点帧 6 步扩散至整段视频）。"""
import json
from .h3 import node, component_name
from .common import collect_job

DEFAULT_RIPPLE_PROMPT = (
    "Edit propagation: each edited frame shows exactly how that moment of the video should look after the edit. "
    "Apply the same changes to every frame of the clip, before, between and after the edited frames, so the edit "
    "stays consistent through the whole video. Keep the guide video's motion, timing, camera movement, composition "
    "and all unedited content unchanged."
)
RESOLUTION_MP = {"480P": 0.25, "720P": 0.92, "1080P": 2.07}


def _megapixels_for(job) -> float:
    if job.get('megapixels'):
        return float(job['megapixels'])
    res = str(job.get('resolution', '480P')).upper()
    return RESOLUTION_MP.get(res if res.endswith('P') else res + 'P', 0.25)


def build_job(job, staged, profile_id):
    source = staged.get('source_video')
    if not source:
        raise ValueError('H3 Ripple 需要 source_video 源视频')
    anchors_staged = staged.get('anchors') or []
    if not 1 <= len(anchors_staged) <= 16:
        raise ValueError('H3 Ripple 需要在 anchors 中提供 1–16 张已确认的精修锚点图片（爆款复刻请先运行 remake-anchors）')

    seconds = float(job.get('seconds') or source.get('seconds') or (107 / 24))
    if not (5 / 24 <= seconds <= 15):
        raise ValueError('H3 Ripple 单段时长需在 0.21–15 秒之间；长视频请通过 remake-anchors → remake-render 自动切段')
    length = max(5, round(seconds * 24))
    length += (5 - length) % 17
    avail = source.get('frames') or round(float(source.get('seconds') or seconds) * 24)
    seed = int(job.get('seed', 0))

    graph = {
        'unet': node('UNETLoader', unet_name=component_name(profile_id, 'diffusion_models'), weight_dtype='default'),
        'lora_pika': node('LoraLoaderModelOnly', model=['unet', 0],
                          lora_name=component_name(profile_id, 'loras', 'pika_h3_flex_fl2va'), strength_model=1.0),
        'lora_ripple': node('LoraLoaderModelOnly', model=['lora_pika', 0],
                            lora_name=component_name(profile_id, 'loras', 'Ripple'), strength_model=1.0),
        'clip': node('CLIPLoader', clip_name=component_name(profile_id, 'text_encoders', 'qwen3vl_32b'),
                     type='minimax', device='default'),
        'vae': node('VAELoader', vae_name=component_name(profile_id, 'vae', 'video_vae', job=job)),
        'source': node('LoadVideo', file=source['name']),
        'source_parts': node('GetVideoComponents', video=['source', 0]),
    }

    images_in = ['source_parts', 0]
    if avail < length:
        graph['src_last'] = node('ImageFromBatch', image=images_in, batch_index=max(0, avail - 1), length=1)
        graph['src_repeat'] = node('RepeatImageBatch', image=['src_last', 0], amount=length - avail)
        graph['src_padded'] = node('ImageBatch', image1=images_in, image2=['src_repeat', 0])
        images_in = ['src_padded', 0]

    markers = [{'id': i, 'frame': min(max(0, int(a.get('frame', 0))), length - 1)}
               for i, a in enumerate(anchors_staged, 1)]
    clip_inputs = dict(
        images=images_in,
        frame_rate=24.0,
        duration=round(length / 24.0, 6),
        megapixels=_megapixels_for(job),
        window_start=0,
        markers=json.dumps(markers, separators=(',', ':')),
        crop=str(job.get('crop', '')),
        allow_upscale=bool(job.get('allow_upscale', False)),
        resize_method='lanczos',
    )
    if source.get('has_audio'):
        clip_inputs['audio'] = ['source_parts', 1]
    graph['ripple_clip'] = node('WN_RippleFromFrames', **clip_inputs)

    apply_inputs = dict(
        clip=['clip', 0],
        vae=['vae', 0],
        ripple=['ripple_clip', 1],
        prompt=DEFAULT_RIPPLE_PROMPT,
    )
    use_align = bool(job.get('align_composite', False))
    for i, item in enumerate(anchors_staged, 1):
        media = item.get('image_media')
        if not media or media.get('kind') != 'image':
            raise ValueError(f'anchors[{i - 1}] 必须是有效图片素材')
        graph[f'anchor_load_{i}'] = node('LoadImage', image=media['name'])
        ed_ref = [f'anchor_load_{i}', 0]
        if use_align:
            graph[f'align_{i}'] = node(
                'QwenEditAlignComposite',
                source=['ripple_clip', 3 + i],
                edited=ed_ref,
                mode='affine',
                auto_mask_threshold=0.08,
                grow_px=2,
                feather_px=2,
            )
            ed_ref = [f'align_{i}', 0]
        apply_inputs[f'edited_anchor_{i}'] = ed_ref

    graph['cond'] = node('WN_RippleApply', **apply_inputs)
    graph['noise'] = node('RandomNoise', noise_seed=seed)
    graph['sampler'] = node('KSamplerSelect', sampler_name='lcm')
    graph['schedule'] = node('BasicScheduler', model=['lora_ripple', 0], scheduler='simple',
                             steps=int(job.get('steps') or 6), denoise=1.0)
    graph['guider'] = node('BasicGuider', model=['lora_ripple', 0], conditioning=['cond', 0])
    graph['sample'] = node('SamplerCustomAdvanced', noise=['noise', 0], guider=['guider', 0],
                           sampler=['sampler', 0], sigmas=['schedule', 0], latent_image=['cond', 1])
    graph['decode'] = node('VAEDecode', samples=['sample', 0], vae=['vae', 0])
    graph['save'] = node(
        'WN_RippleOutput',
        images=['decode', 0],
        ripple=['ripple_clip', 1],
        save='shot + comparison' if job.get('comparison') else 'shot',
        filename_prefix='h3studio/' + job['id'],
        show_anchor_markers=False,
    )
    return graph
