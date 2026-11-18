"""按任务规划 MiniMax H3 + WepeNerd H3 Ripple 模型权重，显式安装时校验锁定 revision 与 SHA256。"""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def catalog():
    return json.loads((ROOT / 'models/catalog.json').read_text(encoding='utf-8'))['components']


def profiles():
    return json.loads((ROOT / 'models/profiles.json').read_text(encoding='utf-8'))['profiles']


def select_profile(job, inventory=None):
    chosen = job.get('profile')
    if chosen and chosen != 'auto':
        if chosen not in profiles():
            raise ValueError(f'未知模型配置：{chosen}')
        kind = job['type']
        mode = job.get('mode', 'ref')
        allowed = {
            'image': {'ph3-lora-int8', 'ph3-standalone-w4a8'},
            'tts': {'h3-tts32-native'},
            'video_edit': {'h3-ripple'},
            'subtitle_replace': {'media-only'},
            'voice_replace': {'media-only'},
            'media_copy': {'media-only'},
            'video': {'h3-fl2va-int8', 'h3-ref2va-int8', 'h3-guides', 'h3-control-union', 'fasth3', 'fasth3-omni-int8', 'prism-dmad-pdmd-int8'},
        }
        if chosen not in allowed.get(kind, set()):
            raise ValueError(f'{chosen} 不兼容作业类型 {kind}')
        if kind == 'video':
            if (mode in ('frames', 'first_last') or job.get('extend')) and chosen != ('h3-ref2va-int8' if job.get('extend') else 'h3-fl2va-int8'):
                raise ValueError(f'{chosen} 不兼容当前首尾帧/延伸模式')
            if mode == 'ref' and chosen == 'h3-fl2va-int8':
                raise ValueError('Ref 模式需要 Ref2VA 配置')
            if chosen == 'h3-guides' and mode != 'guides' or chosen == 'h3-control-union' and mode != 'control':
                raise ValueError('配置与 guides/control 模式不匹配')
        return chosen
    kind = job['type']
    if kind == 'image':
        return 'ph3-lora-int8'
    if kind == 'tts':
        return 'h3-tts32-native'
    if kind == 'video_edit':
        return 'h3-ripple'
    if kind in ('subtitle_replace', 'voice_replace', 'media_copy'):
        return 'media-only'
    if job.get('extend'):
        return 'h3-ref2va-int8'
    mode = job.get('mode', 'ref')
    if mode == 'guides':
        return 'h3-guides'
    if mode == 'control':
        return 'h3-control-union'
    return 'h3-ref2va-int8' if mode == 'ref' else 'h3-fl2va-int8'


def resolve_video_vae_key(profile_id, job=None):
    cfg = profiles()[profile_id]
    components = catalog()
    default_key = next(
        (k for k in cfg['components'] if components[k]['relative_target'].startswith('vae/') and 'video_vae' in components[k]['file']),
        None,
    )
    override = (job or {}).get('video_vae')
    if not override:
        return default_key
    norm = str(override).strip().replace('\\', '/')
    aliases = {
        'fp16': 'vae__minimax_h3_video_vae_fp16',
        'int8': 'vae__minimax_h3_video_vae_int8_convrot',
    }
    if norm.lower() in aliases:
        return aliases[norm.lower()]
    if norm in components and components[norm]['relative_target'].startswith('vae/'):
        return norm
    fname = norm.split('/')[-1]
    for key, item in components.items():
        if item['relative_target'].startswith('vae/') and (item['relative_target'] == f'vae/{fname}' or Path(item['file']).name == fname):
            return key
    raise ValueError(f'未知或未锁定的 video_vae：{override}')


def required_components(jobs):
    cfg = profiles()
    components = catalog()
    result = {}
    for job in jobs:
        profile_id = select_profile(job)
        vae_override = resolve_video_vae_key(profile_id, job) if job.get('video_vae') else None
        for key in cfg[profile_id]['components']:
            if vae_override and components[key]['relative_target'].startswith('vae/') and 'video_vae' in components[key]['file']:
                result[vae_override] = components[vae_override]
            else:
                result[key] = components[key]
        if job.get('upsample'):
            for key in cfg[profile_id].get('upsampler_components', []):
                result[key] = components[key]
    return list(result.values())


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as fp:
        for block in iter(lambda: fp.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def plan_downloads(components, model_roots):
    result = []
    for item in components:
        hits = [Path(r) / item['relative_target'] for r in model_roots]
        ready = any(p.is_file() and p.stat().st_size == item['size_bytes'] for p in hits)
        if not ready:
            result.append(item)
    return result


def install_components(components, model_root):
    from huggingface_hub import hf_hub_download
    model_root = Path(model_root).resolve()
    installed = []
    for item in components:
        if not item.get('revision') or not item.get('sha256') or not item.get('size_bytes'):
            raise ValueError(f'组件尚未锁定，不能安装：{item["id"]}')
        target = model_root / item['relative_target']
        if not target.resolve().is_relative_to(model_root):
            raise ValueError('模型目标路径越界')
        if target.is_file() and target.stat().st_size == item['size_bytes'] and file_sha256(target) == item['sha256']:
            installed.append(str(target))
            continue
        cached = Path(hf_hub_download(repo_id=item['repo'], filename=item['file'], revision=item['revision']))
        if cached.stat().st_size != item['size_bytes'] or file_sha256(cached) != item['sha256']:
            raise ValueError(f'模型大小或 SHA256 校验失败：{item["file"]}')
        import shutil
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + '.part')
        shutil.copy2(cached, tmp)
        os.replace(tmp, target)
        installed.append(str(target))
    return installed
