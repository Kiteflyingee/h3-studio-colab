"""MiniMax H3 + WepeNerd Ripple 统一任务清单校验与依赖拓扑解析。"""
import json
import math
import re
from pathlib import Path

TYPES = {'image', 'video', 'video_edit', 'tts', 'subtitle_replace', 'voice_replace', 'media_copy'}
COMMON = {'id', 'type', 'profile', 'seed', 'timeout', 'depends_on', 'output', 'metadata'}
FIELDS = {
    'media_copy': {'source_video', 'seconds'},
    'image': {'prompt', 'inputs', 'aspect', 'size', 'width', 'height', 'upsample', 'align_to_first', 'align_mask'},
    'video': {
        'prompt', 'inputs', 'mode', 'first', 'last', 'extend', 'extension_output', 'seconds',
        'aspect', 'resolution', 'intensity', 'fast', 'steps', 'video_vae', 'guides', 'control',
    },
    'video_edit': {
        'source_video', 'anchors', 'align_composite', 'comparison', 'crop', 'megapixels',
        'allow_upscale', 'steps', 'preserve', 'seconds', 'aspect', 'resolution', 'video_vae',
    },
    'tts': {'prompt', 'seconds', 'voices', 'video_vae'},
    'subtitle_replace': {'source_video', 'plan', 'subtitles', 'font', 'clear_regions', 'seconds'},
    'voice_replace': {'source_video', 'voice', 'options', 'seconds'},
}
FILE_KEYS = ('first', 'last', 'source_video', 'voice', 'font', 'align_mask')


def file_slots(job):
    for field in ('inputs', 'voices'):
        values = job.get(field, [])
        for index in range(len(values)):
            yield values, index
    for key in (*FILE_KEYS, 'extend'):
        if job.get(key):
            yield job, key
    if job.get('plan', {}).get('font'):
        yield job['plan'], 'font'
    for guide in job.get('guides', []):
        if isinstance(guide, dict):
            yield guide, 'image'
    for anchor in job.get('anchors', []):
        if isinstance(anchor, dict) and anchor.get('image'):
            yield anchor, 'image'
    control = job.get('control') or {}
    for key in ('video', 'mask'):
        if control.get(key):
            yield control, key


def file_values(job):
    return [container.get(key) if isinstance(container, dict) else container[key] for container, key in file_slots(job)]


def dependencies(job, ids):
    found = set(job.get('depends_on', []))
    for value in file_values(job):
        if isinstance(value, str) and value.startswith('@'):
            found.add(value[1:])
    if job.get('extend') in ids:
        found.add(job['extend'])
    return found


def dependency_order(manifest):
    jobs = manifest.get('jobs', [])
    ids = {j['id'] for j in jobs}
    remaining = {j['id']: dependencies(j, ids) for j in jobs}
    order = []
    while remaining:
        ready = [k for k, v in remaining.items() if v <= set(order)]
        if not ready:
            raise ValueError('作业存在循环依赖或引用不存在的作业：' + ', '.join(remaining))
        for key in ready:
            order.append(key)
            del remaining[key]
    return order


def selected_jobs(manifest, only=None, with_dependents=False):
    ids = {j['id'] for j in manifest['jobs']}
    requested = set(only or ids)
    if requested - ids:
        raise ValueError('不存在的作业：' + ', '.join(requested - ids))
    if only and with_dependents:
        previous = None
        while previous != requested:
            previous = set(requested)
            for job in manifest['jobs']:
                if dependencies(job, ids) & requested:
                    requested.add(job['id'])
    selected = set(requested)
    previous = None
    while previous != selected:
        previous = set(selected)
        for job in manifest['jobs']:
            if job['id'] in selected:
                selected |= dependencies(job, ids)
    return [j for j in manifest['jobs'] if j['id'] in selected]


def validate_manifest(manifest, base_dir):
    errors = []
    if not isinstance(manifest, dict) or not isinstance(manifest.get('jobs'), list) or not manifest['jobs']:
        return ['任务清单必须包含非空 jobs 数组']
    base_dir = Path(base_dir)
    jobs = manifest['jobs']
    all_ids = {j.get('id') for j in jobs if isinstance(j, dict) and isinstance(j.get('id'), str)}
    seen = set()
    for n, job in enumerate(jobs):
        if not isinstance(job, dict):
            errors.append(f'作业 {n + 1} 必须是对象')
            continue
        jid = job.get('id', '')
        if not isinstance(jid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', jid):
            errors.append(f'作业 ID 无效：{jid!r}')
            jid = str(jid)
        if jid in seen:
            errors.append(f'{jid}：ID 重复')
        kind = job.get('type')
        if not isinstance(kind, str) or kind not in TYPES:
            errors.append(f'{jid}：不支持的 type {kind!r}')
            continue
        for key in set(job) - COMMON - FIELDS[kind]:
            errors.append(f'{jid}：未知字段 {key}')
        if kind in {'image', 'video', 'tts'} and not str(job.get('prompt', '')).strip():
            errors.append(f'{jid}：缺少 prompt')
        if kind in {'video', 'tts'}:
            value = job.get('seconds', 10)
            try:
                valid = not isinstance(value, bool) and math.isfinite(float(value)) and 2 <= float(value) <= 15
            except (TypeError, ValueError):
                valid = False
            if not valid:
                errors.append(f'{jid}：seconds 必须在 2–15 秒之间')
        if kind == 'video':
            mode = job.get('mode', 'ref')
            if mode not in {'text', 'ref', 'frames', 'first_last', 'guides', 'control'}:
                errors.append(f'{jid}：不支持的 mode {mode}')
            if mode in {'frames', 'first_last'} and not (job.get('first') or job.get('last')):
                errors.append(f'{jid}：首尾帧模式至少提供 first 或 last')
            if job.get('extend') and mode != 'ref':
                errors.append(f'{jid}：延伸只能用 ref 模式')
            if job.get('extension_output', 'cumulative') not in {'segment', 'cumulative'}:
                errors.append(f'{jid}：extension_output 无效')
            if job.get('intensity', 'off') not in {'off', 'low', 'mid', 'high'}:
                errors.append(f'{jid}：intensity 无效')
        for key, limit in (('inputs', 8 if kind == 'image' else 12), ('voices', 3)):
            value = job.get(key, [])
            if not isinstance(value, list):
                errors.append(f'{jid}：{key} 必须是数组')
            elif len(value) > limit:
                errors.append(f'{jid}：{key} 超过 {limit} 项')
        try:
            deps = dependencies(job, all_ids)
            for dep in deps - seen:
                errors.append(f'{jid}：依赖 {dep} 必须为前序作业，不可自引用或引用未来作业')
            for value in file_values(job):
                if not isinstance(value, str):
                    errors.append(f'{jid}：素材路径必须是字符串')
                    continue
                if value.startswith('@') or (value == job.get('extend') and value in all_ids):
                    continue
                if not (base_dir / value).is_file():
                    errors.append(f'{jid}：找不到素材 {value}')
        except (TypeError, AttributeError):
            errors.append(f'{jid}：素材或依赖结构无效')
        if kind in {'video_edit', 'subtitle_replace', 'voice_replace'} and not job.get('source_video'):
            errors.append(f'{jid}：缺少 source_video')
        if kind == 'video_edit' and not job.get('anchors'):
            errors.append(f'{jid}：video_edit (H3 Ripple) 需提供 anchors 关键帧数组；整片复刻请先运行 remake-anchors')
        seen.add(jid)
    return errors


def load_manifest(path):
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding='utf-8'))
    errors = validate_manifest(manifest, path.parent)
    if errors:
        raise ValueError('任务清单无效：\n- ' + '\n- '.join(errors))
    return manifest
