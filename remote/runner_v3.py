"""在选定 Colab VM 内运行共享执行器；自动按任务准备 ComfyUI、ComfyUI-WepeNerd 与锁定权重。"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.executor import execute_manifest, atomic_json
from studio.contracts import ExecutionContext
from studio.comfy_client import ComfyClient
from studio.manifest import load_manifest
from studio.model_registry import required_components, install_components, profiles, select_profile


def apply_gpu_overrides(jobs, gpu):
    if str(gpu or '').upper() != 'A100':
        return False
    changed = False
    for job in jobs:
        if not job.get('video_vae') and job.get('type') in ('video', 'video_edit', 'tts'):
            pid = select_profile(job)
            if 'vae__minimax_h3_video_vae_int8_convrot' in profiles()[pid].get('components', []):
                job['video_vae'] = 'minimax_h3_video_vae_fp16.safetensors'
                changed = True
    return changed


def prepare_jobs(manifest, args, set_stage=None):
    from studio.executor import gpu_jobs
    from studio.state import file_lock
    gpu = gpu_jobs(manifest['jobs'])
    if not gpu:
        return
    selected = {select_profile(job) for job in gpu}
    if not args.allow_unverified and any(not profiles()[p].get('gpu_verified') for p in selected):
        raise ValueError('首次 GPU 验收请使用 --allow-unverified')
    unresolved = [name for p in selected for name in profiles()[p].get('unresolved_components', [])]
    if unresolved:
        raise ValueError('配置包含未锁定组件：' + ', '.join(unresolved))
    with file_lock(Path('/content/h3studio_job/setup.lock')):
        if args.setup_runtime:
            if set_stage:
                set_stage('setup_runtime')
            setup_runtime(manifest)
        if args.install_models:
            if set_stage:
                set_stage('install_models')
            install_components(required_components(gpu), Path('/content/ComfyUI/models'))


def execute_remake(args, base, set_stage=None):
    from studio.cli import remake_command
    from studio.model_registry import file_sha256
    from studio.media import describe_media
    prepared = []
    report = {'schema_version': 2, 'jobs': {}}

    def prepare(rows):
        prepared.extend(rows)
        jobs = []
        for _, _, _, path in rows:
            m = load_manifest(path)
            if apply_gpu_overrides(m['jobs'], args.gpu):
                atomic_json(path, m)
            jobs.extend(m['jobs'])
        if args.action == 'remake-render' and sum(j['type'] in ('video', 'video_edit') for j in jobs) >= 2 and not args.confirmed:
            raise ValueError('多视频复刻需先确认规格并使用 --confirmed')
        prepare_jobs({'jobs': jobs}, args, set_stage=set_stage)
        if set_stage:
            set_stage('generating')

    options = argparse.Namespace(
        command=args.action,
        request=args.request,
        backend='current',
        work_dir=str(base / 'remake'),
        output=str(base / 'remake-final.mp4') if args.action == 'remake-render' else None,
        input_dir='/content/ComfyUI/input',
        url='http://127.0.0.1:8188',
        confirmed=args.confirmed,
        allow_unverified=args.allow_unverified,
        resume=args.resume,
        only=args.only,
        prepare_hook=prepare,
    )
    error = None
    try:
        remake_command(options)
    except Exception as exc:
        error = exc
    rows = getattr(options, 'prepared', prepared)
    for name, _, folder, _ in rows:
        path = folder / 'out/results.json'
        if path.exists():
            for jid, item in json.loads(path.read_text())['jobs'].items():
                report['jobs'][name + '__' + jid] = item
    artifacts = []
    inputs_cache = (base.parent.parent / 'inputs') if base.parent.name == 'runs' else None
    for path in sorted((base / 'remake').rglob('*')):
        if not path.is_file():
            continue
        if path.suffix in ('.json', '.srt'):
            if not getattr(args, 'download_intermediates', False) and ('graphs' in path.parts or 'history' in path.parts):
                continue
            artifacts.append({'kind': 'data', 'path': str(path), 'sha256': file_sha256(path), 'metadata': {}})
        elif args.action == 'remake-anchors' and 'anchors' in path.parts and path.suffix.lower() in ('.png', '.jpg', '.jpeg'):
            sha = file_sha256(path)
            if inputs_cache is not None and path.suffix.lower() == '.png' and not path.stem.endswith('_compare'):
                inputs_cache.mkdir(parents=True, exist_ok=True)
                cached_anchor = inputs_cache / f'{sha}{path.suffix.lower()}'
                if not cached_anchor.is_file():
                    try:
                        os.link(path, cached_anchor)
                    except OSError:
                        shutil.copy2(path, cached_anchor)
            artifacts.append({'kind': 'image', 'path': str(path), 'sha256': sha, 'metadata': describe_media(path)})
    videos = []
    for path in sorted(base.glob('remake-final*.mp4')):
        variant = path.stem.removeprefix('remake-final_') if path.stem != 'remake-final' else (rows[0][0] if rows else 'variant001')
        videos.append({'kind': 'video', 'path': str(path), 'sha256': file_sha256(path), 'metadata': dict(describe_media(path), variant=variant)})
    report['jobs']['delivery'] = {
        'id': 'delivery',
        'status': 'error' if error else 'done',
        'artifacts': videos + artifacts,
        'primary_index': 0,
        'error': str(error) if error else None,
    }
    atomic_json(base / 'out/results.json', report)
    if error:
        raise error


def setup_runtime(manifest):
    client = ComfyClient()
    selected_profiles = {select_profile(j) for j in manifest['jobs']}
    need_wepenerd = ('h3-ripple' in selected_profiles) or any(j.get('align_to_first') for j in manifest['jobs'])
    try:
        client.stats()
    except Exception:
        pass
    else:
        if need_wepenerd and 'WN_RippleApply' not in client.info():
            raise RuntimeError('当前 ComfyUI 未加载 ComfyUI-WepeNerd 节点，请新建会话或安装后重启重试')
        return
    comfy = Path('/content/ComfyUI')
    if comfy.exists():
        raise RuntimeError('已有 ComfyUI 目录但服务未运行；请启动现有环境，自动安装不会覆盖它')
    lock = json.loads((ROOT / 'dependencies.lock.json').read_text())

    def run(args):
        subprocess.run(args, check=True)

    run(['git', 'clone', '--no-checkout', 'https://github.com/Comfy-Org/ComfyUI.git', str(comfy)])
    run(['git', '-C', str(comfy), 'checkout', '--detach', lock['comfyui']['commit']])
    run([sys.executable, '-m', 'pip', 'install', 'torch', 'torchvision', 'torchaudio', '--index-url', 'https://download.pytorch.org/whl/cu130'])
    run([sys.executable, '-m', 'pip', 'install', '-r', str(comfy / 'requirements.txt'), 'huggingface_hub'])
    if need_wepenerd:
        dependency = lock.get('custom_nodes', {}).get('wepenerd')
        if not dependency:
            raise RuntimeError('wepenerd 节点尚未固定版本，拒绝安装浮动 main')
        dest = comfy / 'custom_nodes/ComfyUI-WepeNerd'
        run(['git', 'clone', '--no-checkout', dependency['url'], str(dest)])
        run(['git', '-C', str(dest), 'checkout', '--detach', dependency['commit']])
        if (dest / 'requirements.txt').is_file():
            run([sys.executable, '-m', 'pip', 'install', '-r', str(dest / 'requirements.txt')])
    with open('/content/h3studio_job/comfy.log', 'w') as log:
        subprocess.Popen(
            [sys.executable, str(comfy / 'main.py'), '--listen', '127.0.0.1', '--port', '8188', '--lowvram'],
            cwd=str(comfy), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    for _ in range(120):
        try:
            client.stats()
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError('ComfyUI 未能启动，详见 comfy.log')


def execute_jobs(args, base, set_stage=None):
    from studio.contracts import JobResult
    from studio.director import compose_shots
    from studio.media import artifact
    manifest = load_manifest(args.manifest)
    if apply_gpu_overrides(manifest['jobs'], args.gpu):
        atomic_json(Path(args.manifest), manifest)
    prepare_jobs(manifest, args, set_stage=set_stage)
    if set_stage:
        set_stage('generating')
    out = base / 'out'
    context = ExecutionContext(base, out, Path('/content/ComfyUI/input'), 'current', ComfyClient(), args.allow_unverified)
    report = execute_manifest(Path(args.manifest), context, args.resume, set(args.only.split(',')) if args.only else None, args.with_dependents)
    if manifest.get('director') and all(job['status'] in ('done', 'skipped') for job in report['jobs'].values()):
        if set_stage:
            set_stage('composing')
        target = compose_shots(manifest['director'], report['jobs'], out / 'director-final.mp4', out)
        jid = 'director-delivery'
        report['jobs'][jid] = JobResult(jid, 'done', [artifact(target)]).to_dict()
        atomic_json(out / 'results.json', report)


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--manifest')
    source.add_argument('--request')
    parser.add_argument('--action', choices=['remake-analyze', 'remake-anchors', 'remake-compile', 'remake-render'], default='remake-render')
    parser.add_argument('--confirmed', action='store_true')
    parser.add_argument('--allow-unverified', action='store_true')
    parser.add_argument('--setup-runtime', action='store_true')
    parser.add_argument('--install-models', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--gpu', default='')
    parser.add_argument('--attempt-id', default='start')
    parser.add_argument('--only')
    parser.add_argument('--with-dependents', action='store_true')
    parser.add_argument('--download-intermediates', action='store_true')
    args = parser.parse_args()
    base = Path(args.manifest or args.request).resolve().parent
    status = base / 'status.json'
    from studio.state import file_lock
    with file_lock(base / 'runner.lock'):
        state = {'running': True, 'done': False, 'stage': 'starting', 'run_id': args.run_id, 'attempt_id': args.attempt_id}
        atomic_json(status, state)

        def set_stage(name):
            state['stage'] = name
            atomic_json(status, state)

        try:
            if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
                raise RuntimeError('Colab 运行时缺少 ffmpeg 或 ffprobe')
            if args.request:
                execute_remake(args, base, set_stage=set_stage)
            else:
                execute_jobs(args, base, set_stage=set_stage)
            state['stage'] = 'finished'
        except Exception as exc:
            state['stage'] = 'error'
            state['error'] = str(exc)
            raise
        finally:
            state.update(running=False, done=True)
            atomic_json(status, state)


if __name__ == '__main__':
    main()
