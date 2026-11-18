import shutil
import json
from pathlib import Path
from ..contracts import JobResult
from ..media import artifact, describe_media, trim_video, ffmpeg


def collect_job(job, entry, context):
    files = context.client.save_result(entry, context.output_dir, job['id'] + '_download')
    expected = {'image': 'image', 'tts': 'audio', 'faceswap_image': 'image'}.get(job['type'], 'video')
    described = [(p, describe_media(p)) for p in files]
    identities={Path(v['path']).resolve():v for v in getattr(context.client,'last_downloads',[])}
    canonical={'image':{'isave'},'video':{'save'},'audio':{'save_audio','save'}}[expected]
    candidates=[p for p,info in described if info['kind']==expected]
    primary=next((p for p in candidates if identities.get(p.resolve(),{}).get('node') in canonical),None)
    snapshot=context.output_dir/'graphs'/(job['id']+'.json')
    if primary is None and snapshot.is_file():
        classes={'image':('SaveImage',),'video':('SaveVideo','WN_SaveVideo','WN_RippleOutput'),'audio':('SaveAudio',)}[expected]
        graph=json.loads(snapshot.read_text())
        nodes={n for n,v in graph.items() if v['class_type'].startswith(classes)}
        primary=next((p for p in candidates if identities.get(p.resolve(),{}).get('node') in nodes),None)
    if primary is None:primary=next(iter(candidates),None)
    if primary is None: raise RuntimeError(f'{job["id"]} 没有返回预期的 {expected} 主输出')
    target = context.output_dir / (job['id'] + ('.mp4' if expected == 'video' else primary.suffix))
    if target.exists():
        old = artifact(target)
        versions = context.output_dir / 'versions'; versions.mkdir(exist_ok=True)
        backup = versions / f'{job["id"]}_{old.sha256[:16]}{target.suffix}'
        if not backup.exists(): shutil.copy2(target, backup)
    if expected == 'video' and job.get('seconds') and not job.get('extend'):
        info = describe_media(primary)
        if info['seconds'] + 2 / (info.get('fps_float') or 24) < float(job['seconds']):
            raise RuntimeError(f'输出时长不足：{info["seconds"]} < {job["seconds"]} 秒')
        trim_video(primary, target, job['seconds'])
    elif expected == 'video':
        trim_video(primary, target, describe_media(primary)['seconds'])
    elif expected == 'audio' and job.get('seconds'):
        temp = target.with_name(target.stem + '.working' + target.suffix)
        ffmpeg(['-i', primary, '-t', job['seconds'], temp]); temp.replace(target)
    elif primary.resolve() != target.resolve():
        primary.replace(target)
    artifacts=[artifact(target)] + [artifact(p) for p in files if p != primary and p.exists()]
    artifacts[0].metadata['source_output']=identities.get(primary.resolve(),{})
    for item in artifacts[1:]:item.metadata['source_output']=identities.get(Path(item.path).resolve(),{})
    return JobResult(job['id'], 'done', artifacts)
