"""统一作业执行和可恢复记录；未知提交结果绝不盲目重投。"""
import copy
import hashlib
import importlib
import json
import secrets
import time
from pathlib import Path
from .contracts import JobResult
from .manifest import load_manifest, dependencies, file_values
from .media import describe_media, stage_media, normalize_reference
from .model_registry import select_profile, profiles, file_sha256, required_components
from .preflight import check_workflow
from .state import atomic_json, file_lock


class PendingJobError(RuntimeError):
    pass


def adapter_for(job):
    kind = job['type']
    module = {
        'tts': 'tts',
        'video_edit': 'video_edit',
        'subtitle_replace': 'copy',
        'voice_replace': 'copy',
        'media_copy': 'copy',
    }.get(kind, 'h3')
    if kind == 'video' and job.get('mode') in ('guides', 'control'):
        module = 'h3_guides' if job['mode'] == 'guides' else 'h3_control'
    if job.get('profile', '').startswith('fasth3'):
        module = 'fasth3'
    return importlib.import_module('.adapters.' + module, 'studio')


def gpu_jobs(jobs):
    def uses_gpu(job):
        adapter = adapter_for(job)
        return not getattr(adapter, 'LOCAL', False) or getattr(adapter, 'GPU', False)
    return [job for job in jobs if uses_gpu(job)]


def primary_path(result):
    return Path(result['artifacts'][result.get('primary_index', 0)]['path'])


def resolve_path(value, base, results):
    if value.startswith('@'):
        result = results.get(value[1:])
        if not result or result['status'] not in ('done', 'skipped') or not result.get('artifacts'):
            raise ValueError('前序作业没有可用结果：' + value)
        return primary_path(result)
    return (base / value).resolve()


def fingerprint(job, base, results):
    inputs = {}
    for value in file_values(job):
        ref = '@' + value if value == job.get('extend') and value in results else value
        inputs[value] = file_sha256(resolve_path(ref, base, results))
    root = Path(__file__).resolve().parents[1]
    code = hashlib.sha256()
    for p in sorted(list((root / 'studio').rglob('*.py')) + list((root / 'workflows').rglob('*.json'))):
        code.update(p.read_bytes())
    payload = {
        'job': job,
        'inputs': inputs,
        'profile': profiles()[select_profile(job)],
        'dependencies': {dep: results.get(dep, {}).get('metadata', {}).get('fingerprint') for dep in dependencies(job, set(results))},
        'components': required_components([job]),
        'dependency_lock': file_sha256(root / 'dependencies.lock.json'),
        'code': code.hexdigest(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def stage_job(job, context, results, local=False):
    def stage(value):
        path = resolve_path(value, context.base_dir, results)
        original = path
        if not local:
            path = normalize_reference(path, 24, context.output_dir / 'references')
        return dict(describe_media(path), path=str(path)) if local else dict(stage_media(path, context), original_path=str(original))

    out = {key: [stage(v) for v in job[key]] for key in ('inputs', 'voices') if key in job}
    for key in ('first', 'last', 'source_video', 'voice', 'align_mask'):
        if job.get(key):
            out[key] = stage(job[key])
    if job.get('guides'):
        out['guides'] = [dict(g, media=stage(g['image'])) for g in job['guides']]
    if job.get('control'):
        out['control'] = {k: stage(v) if k in ('video', 'mask') else v for k, v in job['control'].items()}
    if job.get('anchors'):
        out['anchors'] = [dict(a, image_media=stage(a['image'])) for a in job['anchors'] if a.get('image')]
    return out


def execute_manifest(path, context, resume=False, only=None, with_dependents=False):
    with file_lock(Path(context.output_dir) / '.executor.lock'):
        return _execute_manifest(path, context, resume, only, with_dependents)


def _execute_manifest(path, context, resume=False, only=None, with_dependents=False):
    path = Path(path).resolve()
    manifest = load_manifest(path)
    context.base_dir = path.parent
    context.output_dir = Path(context.output_dir).resolve()
    context.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = context.output_dir / 'results.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else {'schema_version': 2, 'jobs': {}}
    results = report['jobs']
    ids = {j['id'] for j in manifest['jobs']}
    requested = set(only or ids)
    if requested - ids:
        raise ValueError('不存在的作业：' + ', '.join(requested - ids))
    if only and with_dependents:
        changed = True
        while changed:
            before = set(requested)
            for j in manifest['jobs']:
                if dependencies(j, ids) & requested:
                    requested.add(j['id'])
            changed = before != requested
    selected = set(requested)
    if only:
        changed = True
        while changed:
            before = set(selected)
            for j in manifest['jobs']:
                deps = dependencies(j, ids)
                if j['id'] in selected:
                    selected |= deps
            changed = before != selected
    report.update(schema_version=2, manifest=str(path), backend=context.backend)
    halted = False
    for source_job in manifest['jobs']:
        jid = source_job['id']
        if jid not in selected:
            continue
        old = results.get(jid, {})
        job = copy.deepcopy(source_job)
        if job.get('seed') in (None, -1):
            job['seed'] = old.get('metadata', {}).get('seed', secrets.randbits(48))
        if halted or any(results.get(dep, {}).get('status') not in ('done', 'skipped') for dep in dependencies(job, ids)):
            if old.get('status') in ('running', 'collecting', 'submitting', 'uncertain'):
                results[jid] = dict(old, error='前置条件暂未满足；保留原 prompt 供恢复，不会覆盖记录')
            else:
                results[jid] = JobResult(jid, 'dependency_failed', error='前序作业失败或整批已停止').to_dict()
            atomic_json(report_path, report)
            continue
        started = time.time()
        try:
            fp = fingerprint(job, path.parent, results)
            cached = old.get('metadata', {}).get('fingerprint') == fp
            if old.get('status') in ('running', 'collecting', 'submitting', 'uncertain') and (not cached or not resume):
                raise PendingJobError('该 ID 的上次任务仍在运行或提交状态未知；先用原清单 --resume 或检查服务队列，不会覆盖并重投')
            if (resume or jid not in requested) and cached and old.get('status') in ('done', 'skipped'):
                if old.get('artifacts') and all(Path(a['path']).is_file() and file_sha256(a['path']) == a['sha256'] for a in old['artifacts']):
                    results[jid] = dict(old, status='skipped')
                    continue
            profile_id = select_profile(job)
            adapter = adapter_for(job)
            if not context.allow_unverified and not profiles()[profile_id].get('gpu_verified') and (not getattr(adapter, 'LOCAL', False) or getattr(adapter, 'GPU', False)):
                raise ValueError(f'{profile_id} 尚未 GPU 验收；首次试跑请明确传入 --allow-unverified')
            if old.get('status') in ('submitting', 'uncertain') and cached and not old.get('metadata', {}).get('prompt_id'):
                raise RuntimeError('上次提交结果不明，请先按 request_id 检查服务队列；本次没有重复提交')
            recovering = bool(resume and cached and old.get('metadata', {}).get('prompt_id') and old.get('status') in ('running', 'collecting'))
            staged = stage_job(job, context, results, getattr(adapter, 'LOCAL', False) or recovering)
            if job.get('extend'):
                from .adapters.extension import prepare_extension
                value = job['extend']
                if value in results:
                    value = '@' + value
                if recovering:
                    staged['extension'] = {'source': str(resolve_path(value, context.base_dir, results)), 'context_seconds': 22 / 24}
                else:
                    staged['extension'] = prepare_extension(resolve_path(value, context.base_dir, results), job, context)
            metadata = dict(old.get('metadata', {})) if recovering else {}
            metadata.update({
                'fingerprint': fp,
                'seed': job.get('seed'),
                'profile': profile_id,
                'started': started,
                'request_id': 'h3studio-' + fp,
                'gpu_verified': profiles()[profile_id].get('gpu_verified', False),
            })
            if getattr(adapter, 'LOCAL', False):
                result = adapter.run_local(job, staged, context)
            else:
                if recovering:
                    pid = old['metadata']['prompt_id']
                else:
                    graph = adapter.build_job(job, staged, profile_id)
                    preflight = check_workflow(graph, profile_id, context.client)
                    if not preflight.ready:
                        raise ValueError('工作流预检失败：\n' + '\n'.join(preflight.errors))
                    atomic_json(context.output_dir / 'graphs' / (jid + '.json'), graph)
                    metadata['graph_sha256'] = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
                    results[jid] = dict(id=jid, status='submitting', artifacts=[], metadata=metadata)
                    atomic_json(report_path, report)
                    response = context.client.request('/prompt', {'prompt': graph, 'client_id': metadata['request_id']})
                    pid = response.get('prompt_id')
                    if not pid:
                        raise RuntimeError('提交没有返回 prompt_id；请检查服务器队列')
                metadata['prompt_id'] = pid
                results[jid] = dict(id=jid, status='running', artifacts=[], metadata=metadata)
                atomic_json(report_path, report)
                history_path = context.output_dir / 'history' / (jid + '_' + fp + '.json')
                if old.get('status') == 'collecting' and history_path.is_file():
                    entry = json.loads(history_path.read_text())
                else:
                    _, entry = context.client.wait(pid, job.get('timeout', 3600))
                    atomic_json(history_path, entry)
                results[jid] = dict(id=jid, status='collecting', artifacts=[], metadata=metadata)
                atomic_json(report_path, report)
                result = adapter.collect_job(job, entry, context)
                if job['type'] == 'video_edit' and 'audio' in job.get('preserve', []):
                    from .audio_mix import restore_source_audio
                    original = staged['source_video'].get('original_path', staged['source_video']['path'])
                    result.artifacts[result.primary_index] = restore_source_audio(
                        result.artifacts[result.primary_index].path, original, context.output_dir / (jid + '_source_audio.mp4')
                    )
                if job.get('extend'):
                    from .adapters.extension import finalize_extension
                    ext = staged['extension']
                    result.artifacts[result.primary_index] = finalize_extension(
                        Path(ext['source']),
                        Path(result.artifacts[result.primary_index].path),
                        dict(job, _context_seconds=ext['context_seconds']),
                        context,
                    )
            result.metadata.update(metadata, elapsed=time.time() - started)
            results[jid] = result.to_dict()
        except Exception as exc:
            active = results.get(jid, {})
            from .comfy_client import ComfyExecutionError
            state = active.get('status') if active.get('status') in ('running', 'collecting') and not isinstance(exc, ComfyExecutionError) else 'error'
            if active.get('status') in ('submitting', 'uncertain'):
                state = 'uncertain'
            metadata = active.get('metadata', {}) if active.get('status') in ('running', 'collecting', 'submitting', 'uncertain') else {'seed': job.get('seed')}
            results[jid] = JobResult(jid, state, metadata=metadata, error=str(exc)).to_dict()
            halted = bool(manifest.get('stop_on_error', False))
        atomic_json(report_path, report)
    atomic_json(report_path, report)
    return report
