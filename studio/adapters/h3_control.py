from .h3 import build_job as build_h3, node, component_name
from .common import collect_job


def build_job(job, staged, profile_id):
    control = staged.get('control', {})
    kind = control.get('kind')
    if kind not in {'pose', 'canny', 'depth', 'hed', 'mlsd', 'inpaint'}: raise ValueError('不支持的控制类型')
    if not control.get('video'): raise ValueError('缺少控制视频')
    graph = build_h3(job, staged, profile_id)
    graph['control_video'] = node('LoadVideo', file=control['video']['name'])
    graph['control_parts'] = node('GetVideoComponents', video=['control_video', 0])
    controlled = ['control_parts', 0]
    if control.get('preprocess'):
        if kind == 'canny': graph['preprocess'] = node('Canny', image=controlled, low_threshold=.4, high_threshold=.8)
        else: raise ValueError(f'{kind} 在线预处理器未内置，请提供已预处理的视频并设置 preprocess=false（或使用 canny）')
        controlled = ['preprocess', 0]
    graph['control_patch'] = node('ModelPatchLoader', name=component_name(profile_id, 'model_patches'))
    args = dict(model=graph['guider']['inputs']['model'], model_patch=['control_patch', 0], vae=['vae', 0],
                strength=1., start_percent=0., end_percent=1.)
    if kind == 'inpaint':
        if not control.get('mask'): raise ValueError('视频修复需要 mask 视频或图片')
        mask = control['mask']
        if mask.get('kind') == 'image':
            graph['mask_load'] = node('LoadImage', image=mask['name']); masks = ['mask_load', 0]
        else:
            graph['mask_load'] = node('LoadVideo', file=mask['name'])
            graph['mask_parts'] = node('GetVideoComponents', video=['mask_load', 0]); masks = ['mask_parts', 0]
        graph['mask'] = node('ImageToMask', image=masks, channel='red')
        args.update(source_video=['control_parts', 0], mask=['mask', 0])
    else: args['control_video'] = controlled
    graph['control_apply'] = node('MiniMaxH3FunControlNetApply', **args)
    graph['guider']['inputs']['model'] = ['control_apply', 0]
    graph['schedule']['inputs']['model'] = ['control_apply', 0]
    return graph
