"""统一 CLI 的只读规划、权重安装、两步式复刻与任务执行。"""
import copy
import json
import shutil
from pathlib import Path
from .comfy_client import ComfyClient
from .contracts import ExecutionContext
from .manifest import load_manifest, dependencies
from .model_registry import select_profile, required_components, profiles, plan_downloads, install_components


def make_plan(path, backend='colab'):
    manifest = load_manifest(path)
    ids = {j['id'] for j in manifest['jobs']}
    components = required_components(manifest['jobs'])
    return {
        'backend': backend,
        'jobs': [
            {
                'id': j['id'],
                'type': j['type'],
                'profile': select_profile(j),
                'depends_on': sorted(dependencies(j, ids)),
                'gpu_verified': profiles()[select_profile(j)].get('gpu_verified', False),
            }
            for j in manifest['jobs']
        ],
        'components': components,
        'download_bytes_before_reuse': sum(c.get('size_bytes') or 0 for c in components),
    }


def plan_command(args):
    print(json.dumps(make_plan(args.manifest, args.backend), ensure_ascii=False, indent=2))


def doctor_command(args):
    from .executor import adapter_for, stage_job
    from .preflight import check_workflow
    from .manifest import file_values
    from .media import ffmpeg_filters
    data = make_plan(args.manifest) if args.manifest else {'jobs': [], 'profiles': profiles()}
    data['offline'] = args.offline
    if not args.offline:
        client = ComfyClient(args.url)
        manifest = load_manifest(args.manifest) if args.manifest else None
        gpu_needed = not manifest or any(not getattr(adapter_for(j), 'LOCAL', False) or getattr(adapter_for(j), 'GPU', False) for j in manifest['jobs'])
        if gpu_needed:
            data['system'] = client.stats()
            data['node_count'] = len(client.info())
        if manifest:
            base = Path(args.manifest).resolve().parent
            context = ExecutionContext(base, base / 'out', None, getattr(args, 'backend', 'current'), client)

            def names(value):
                if isinstance(value, dict):
                    out = {k: names(v) for k, v in value.items()}
                    if out.get('path') and out.get('kind'):
                        out['name'] = 'doctor_' + Path(out['path']).name
                    return out
                if isinstance(value, list):
                    return [names(x) for x in value]
                return value

            checks = []
            for source in manifest['jobs']:
                job = dict(source, seed=source.get('seed') if source.get('seed') not in (None, -1) else 0)
                try:
                    adapter = adapter_for(job)
                    pending = [v for v in file_values(job) if v.startswith('@')]
                    if pending:
                        checks.append({
                            'id': job['id'],
                            'environment_ready': None,
                            'requires_upstream': pending,
                            'warnings': ['前序结果尚未生成，待前序完成后提交前再次预检'],
                        })
                        continue
                    if getattr(adapter, 'LOCAL', False) and not getattr(adapter, 'GPU', False):
                        ready = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))
                        warnings = []
                        if ready and job['type'] == 'subtitle_replace':
                            avail = ffmpeg_filters()
                            if 'subtitles' not in avail or 'drawtext' not in avail:
                                warnings.append('当前 FFmpeg 未编译 libass/drawtext，将自动使用 PNG overlay 合成字幕/叠字')
                        entry = {'id': job['id'], 'environment_ready': ready, 'local_media': True, 'errors': [] if ready else ['缺少 ffmpeg/ffprobe']}
                        if warnings:
                            entry['warnings'] = warnings
                        checks.append(entry)
                        continue
                    staged = names(stage_job(job, context, {}, local=True))
                    if job.get('extend'):
                        checks.append({'id': job['id'], 'environment_ready': None, 'warnings': ['延伸上下文将在执行时生成，提交前进行完整预检']})
                        continue
                    graph = adapter.build_job(job, staged, select_profile(job))
                    report = check_workflow(graph, select_profile(job), client)
                    checks.append({'id': job['id'], 'environment_ready': report.ready, 'errors': report.errors})
                except Exception as exc:
                    checks.append({'id': job['id'], 'environment_ready': False, 'errors': [str(exc)]})
            data['checks'] = checks
    print(json.dumps(data, ensure_ascii=False, indent=2))


def model_command(args):
    manifest = load_manifest(args.manifest)
    components = required_components(manifest['jobs'])
    roots = [Path(args.models_dir)] if args.models_dir else []
    missing = plan_downloads(components, roots)
    unresolved = [name for job in manifest['jobs'] for name in profiles()[select_profile(job)].get('unresolved_components', [])]
    if args.command == 'models-install':
        if not roots:
            raise ValueError('安装模型必须明确指定 --models-dir')
        if unresolved:
            raise ValueError('当前配置还有未锁定组件：' + ', '.join(unresolved))
        missing = install_components(components, roots[0])
    print(json.dumps({'missing' if args.command == 'models-plan' else 'installed': missing, 'unresolved': unresolved}, ensure_ascii=False, indent=2))


def run_command(args):
    from .executor import execute_manifest
    from .manifest import selected_jobs
    manifest = load_manifest(args.manifest)
    only = set(args.only.split(',')) if args.only else None
    jobs = selected_jobs(manifest, only, args.with_dependents)
    if sum(j['type'] in ('video', 'video_edit') for j in jobs) >= 2 and not args.confirmed:
        raise ValueError('多视频任务需要已确认规格；请传入 --confirmed')
    if args.backend == 'colab':
        from .backends import run_colab
        return run_colab(args)
    base = Path(args.manifest).resolve().parent
    context = ExecutionContext(
        base,
        Path(args.output_dir).resolve() if args.output_dir else base / 'out',
        Path(args.input_dir).resolve() if args.input_dir else None,
        args.backend,
        ComfyClient(args.url),
        args.allow_unverified,
    )
    result = execute_manifest(Path(args.manifest), context, args.resume, only, args.with_dependents)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if any(result['jobs'][j['id']]['status'] not in ('done', 'skipped') for j in jobs):
        raise RuntimeError('部分任务未完成，详见 results.json')
    return result


def add_colab_options(parser):
    parser.add_argument('--session')
    parser.add_argument('--gpu', type=str.upper, choices=['CPU', 'T4', 'L4', 'G4', 'A100', 'H100'])
    parser.add_argument('--stop-after', action='store_true')
    parser.add_argument('--idle-minutes', type=float, default=15)
    parser.add_argument('--max-minutes', type=float, default=120)
    parser.add_argument('--setup-runtime', action='store_true', default=True)
    parser.add_argument('--install-models', action='store_true', default=True)
    parser.add_argument('--no-setup-runtime', dest='setup_runtime', action='store_false')
    parser.add_argument('--no-install-models', dest='install_models', action='store_false')


def add_commands(sub):
    for command, desc in (
        ('remake-analyze', '爆款复刻前检：分析源片镜头切点与时长'),
        ('remake-anchors', '爆款复刻 Step 1：切段并生成改后关键帧预览图（先确认形象！）'),
        ('remake-compile', '编译爆款复刻底层任务清单'),
        ('remake-render', '爆款复刻 Step 2：读取已确认关键帧并用 H3 Ripple 扩散合成成片'),
    ):
        p = sub.add_parser(command, help=desc)
        p.add_argument('request')
        p.add_argument('--output')
        p.add_argument('--input-dir')
        p.add_argument('--url', default='http://127.0.0.1:8188')
        default_backend = 'colab' if command in ('remake-anchors', 'remake-render') else 'local'
        p.add_argument('--backend', choices=['colab', 'current', 'local'], default=default_backend)
        p.add_argument('--confirmed', action='store_true')
        p.add_argument('--allow-unverified', action='store_true')
        p.add_argument('--resume', action='store_true')
        p.add_argument('--only')
        p.add_argument('--work-dir')
        add_colab_options(p)
        p.set_defaults(fn=remake_command)
    p = sub.add_parser('plan', help='只读生成任务与权重计划')
    p.add_argument('manifest')
    p.add_argument('--backend', choices=['current', 'local', 'colab'], default='colab')
    p.set_defaults(fn=plan_command)
    for command in ('models-plan', 'models-install'):
        p = sub.add_parser(command, help='规划缺少的权重' if command == 'models-plan' else '安装并校验锁定权重')
        p.add_argument('--manifest', required=True)
        p.add_argument('--models-dir')
        p.set_defaults(fn=model_command)
    p = sub.add_parser('run', help='统一执行单件生成或后期处理任务清单')
    p.add_argument('manifest')
    p.add_argument('--backend', choices=['current', 'local', 'colab'], default='colab')
    p.add_argument('--url', default='http://127.0.0.1:8188')
    p.add_argument('--input-dir')
    p.add_argument('--output-dir')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--only')
    p.add_argument('--with-dependents', action='store_true')
    p.add_argument('--confirmed', action='store_true')
    p.add_argument('--allow-unverified', action='store_true')
    add_colab_options(p)
    p.set_defaults(fn=run_command)


def remake_command(args):
    if args.backend == 'colab':
        from .remake_remote import run_remake_colab
        return run_remake_colab(args)
    from .source_analysis import analyze_source
    from .remake import (
        compile_anchor_plan, materialize_anchor_plan, finalize_anchors,
        compile_remake, materialize_plan,
    )
    from .remake_remote import normalize_request
    from .executor import execute_manifest, atomic_json, primary_path
    from .adapters.extension import concat_segments
    from .media import trim_video

    request_path = Path(args.request).resolve()
    base = request_path.parent
    request = normalize_request(json.loads(request_path.read_text(encoding='utf-8')), base)
    options = request.get('analysis', {})
    analysis = analyze_source(Path(request['source_video']), options)
    root = (
        Path(args.work_dir).resolve() if getattr(args, 'work_dir', None)
        else Path(args.output).resolve() if args.output and args.command not in ('remake-render', 'remake-anchors')
        else base / (request_path.stem + '_remake')
    )
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / 'analysis.json', analysis)
    if args.command == 'remake-analyze':
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
        return

    variants = request.get('variants') or [{}]
    prepared = []

    if args.command == 'remake-anchors':
        for i, row in enumerate(variants):
            name = str(row.get('name', f'variant{i + 1:03}'))
            variant = copy.deepcopy(request)
            variant.update(row)
            folder = root / name
            aplan = compile_anchor_plan(variant, analysis)
            if not aplan['jobs']:
                print(f'ℹ️ 版本 {name} 无需关键帧生成（无画面角色/商品改动或已显式提供 anchors），可直接运行 remake-render。')
                continue
            mpath = materialize_anchor_plan(aplan, folder)
            prepared.append((name, aplan, folder, mpath))
        args.prepared = prepared
        if getattr(args, 'prepare_hook', None) and prepared:
            args.prepare_hook(prepared)
        only = set(args.only.split(',')) if getattr(args, 'only', None) else None
        for name, aplan, folder, mpath in prepared:
            context = ExecutionContext(
                folder, folder / 'out',
                Path(args.input_dir).resolve() if args.input_dir else None,
                args.backend, ComfyClient(args.url), args.allow_unverified,
            )
            result = execute_manifest(mpath, context, args.resume, only)
            if any(v['status'] not in ('done', 'skipped') for v in result['jobs'].values()):
                raise RuntimeError('关键帧生成未全部成功：' + str(folder / 'out/results.json'))
            saved = finalize_anchors(aplan, folder, result)
            for ed_png, cmp_png in saved:
                print('🖼️ 关键帧预览：', cmp_png or ed_png)
        return

    for i, row in enumerate(variants):
        name = str(row.get('name', f'variant{i + 1:03}'))
        variant = copy.deepcopy(request)
        variant.update(row)
        folder = root / name
        plan = compile_remake(
            variant, analysis,
            anchors_dir=folder / 'anchors',
            require_confirmed_anchors=(args.command == 'remake-render'),
        )
        manifest_path = materialize_plan(plan, folder)
        prepared.append((name, plan, folder, manifest_path))
    args.prepared = prepared
    if args.command == 'remake-render' and getattr(args, 'prepare_hook', None):
        args.prepare_hook(prepared)
    for name, plan, folder, manifest_path in prepared:
        if args.command == 'remake-compile':
            print('复刻清单：', manifest_path)
            continue
        if len(plan['timeline']) > 1 and not args.confirmed:
            raise ValueError('多段复刻请在确认规格后添加 --confirmed')
        context = ExecutionContext(
            folder, folder / 'out',
            Path(args.input_dir).resolve() if args.input_dir else None,
            args.backend, ComfyClient(args.url), args.allow_unverified,
        )
        result = execute_manifest(manifest_path, context, args.resume)
        if any(v['status'] not in ('done', 'skipped') for v in result['jobs'].values()):
            raise RuntimeError('复刻任务未全部成功：' + str(folder / 'out/results.json'))
        clips = []
        for shot in plan['timeline']:
            src = primary_path(result['jobs'][shot['id']])
            target = folder / (shot['id'] + '_timeline.mp4')
            trim_video(src, target, shot['seconds'])
            clips.append(target)
        output = Path(args.output).resolve() if args.output else base / (request_path.stem + '_final.mp4')
        if len(variants) > 1:
            output = output.with_name(output.stem + '_' + name + output.suffix)
        concat_segments(clips, output, fps=analysis.get('fps', 24))
        print('✅ 复刻成片：', output)
