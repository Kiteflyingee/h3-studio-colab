import math
from .h3 import build_job as build_h3, node
from .common import collect_job


def build_job(job, staged, profile_id):
    guides = job.get('guides', [])
    times = [float(g['at_seconds']) for g in guides]
    seconds = float(job.get('seconds', 10))
    if not times or any(not math.isfinite(t) or not 0 <= t <= seconds for t in times) or times != sorted(set(times)):
        raise ValueError('参考时间点必须严格递增且位于片段内')
    graph = build_h3(job, staged, profile_id)
    previous = ['cond', 0]
    for i, guide in enumerate(staged['guides']):
        graph[f'guide_image{i}'] = node('LoadImage', image=guide['media']['name'])
        graph[f'guide_scale{i}'] = node('ImageScale', image=[f'guide_image{i}', 0], upscale_method='lanczos',
                                       width=graph['cond']['inputs']['width'], height=graph['cond']['inputs']['height'], crop='disabled')
        graph[f'guide{i}'] = node('MiniMaxH3AddGuide', positive=previous, latent=['cond', 1], vae=['vae', 0],
                                 image=[f'guide_scale{i}', 0], frame_idx=min(round(times[i] * 24), round(seconds*24)-1))
        previous = [f'guide{i}', 0]
    graph['guider']['inputs']['conditioning'] = previous
    return graph
