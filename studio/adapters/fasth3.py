"""FastH3 Omni 的原生八步路线，独立于 H3 Turbo LoRA。"""
from .h3 import build_job as build_h3
from .common import collect_job


def build_job(job, staged, profile_id):
    if job.get('mode', 'ref') not in ('text', 'ref'):
        raise ValueError('该 FastH3 Omni 配置只验配文本/多模态参考入口，首尾帧请使用 FL2VA')
    normal = dict(job, mode='ref', steps=8)
    return build_h3(normal, staged, profile_id)
