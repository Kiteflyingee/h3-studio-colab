"""声明式复刻素材传输；支持两步确认式工作流（Step 1 remake-anchors 预览确认 → Step 2 remake-render 扩散合成）。"""
import copy
import json
import re
import shutil
from pathlib import Path
from .model_registry import file_sha256


def request_assets(request):
    yield request, 'source_video'
    for row in [request] + request.get('variants', []):
        for group in ('characters', 'products', 'scenes', 'edits'):
            for edit in row.get(group, []):
                for field in ('reference', 'mask'):
                    if edit.get(field):
                        yield edit, field
        for anchor in row.get('anchors', []):
            if isinstance(anchor, dict) and anchor.get('image'):
                yield anchor, 'image'
        for shot_anchors in (row.get('confirmed_anchors') or {}).values():
            for anchor in shot_anchors:
                if isinstance(anchor, dict) and anchor.get('image'):
                    yield anchor, 'image'
        if row.get('copy', {}).get('font'):
            yield row['copy'], 'font'
    if request.get('analysis', {}).get('analysis_file'):
        yield request['analysis'], 'analysis_file'


def normalize_request(request, base):
    request = copy.deepcopy(request)
    base = Path(base).resolve()
    if not request.get('source_video'):
        raise ValueError('复刻请求缺少 source_video')
    names = set()
    for index, row in enumerate(request.get('variants', [])):
        name = str(row.get('name', f'variant{index + 1:03}'))
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', name) or name in names:
            raise ValueError('版本名必须唯一，且只包含英数字、下划线、连字符')
        if 'source_video' in row:
            raise ValueError('批量改版必须使用同一源视频')
        names.add(name)
    for obj, key in request_assets(request):
        if not isinstance(obj[key], str):
            raise ValueError('素材路径必须是字符串')
        path = (base / obj[key]).resolve()
        if not path.is_file():
            raise ValueError('找不到复刻素材：' + str(path))
        obj[key] = str(path)
    return request


def attach_local_confirmed_anchors(request, out_dir):
    """在 Step 2 (remake-render) 提交到 Colab 前，收集本机已生成并确认的 anchors/*.png 一并上传。"""
    request = copy.deepcopy(request)
    variants = request.get('variants') or [{}]
    for index, row in enumerate(variants):
        merged = dict(request, **row)
        if merged.get('mode', 'edit') != 'edit' or merged.get('anchors'):
            continue
        has_visual = any(
            e.get('kind') != 'in_scene_text'
            for g in ('characters', 'products', 'scenes', 'edits')
            for e in ([dict(x, kind=g[:-1]) for x in merged.get(g, [])] if g != 'edits' else merged.get(g, []))
        )
        if not has_visual:
            continue
        name = str(row.get('name', f'variant{index + 1:03}'))
        plan_file = Path(out_dir) / 'remake' / name / 'anchors_plan.json'
        anchors_dir = Path(out_dir) / 'remake' / name / 'anchors'
        if not plan_file.is_file() or not anchors_dir.is_dir():
            raise ValueError(
                f'版本 {name} 尚未生成并确认关键帧预览图！\n'
                f'👉 请先运行 Step 1：python scripts/h3studio.py remake-anchors <request.json> --gpu L4 --allow-unverified'
            )
        plan_map = json.loads(plan_file.read_text(encoding='utf-8'))
        confirmed = {}
        for jid, entries in plan_map.items():
            shot_list = []
            for entry in entries:
                png = anchors_dir / Path(entry['edited']).name
                if not png.is_file():
                    raise ValueError(f'缺少已确认的关键帧图片：{png}（请先运行 remake-anchors 生成并确认关键帧预览图）')
                shot_list.append({'frame': entry['frame'], 'image': str(png.resolve())})
            confirmed[jid] = shot_list
        if request.get('variants'):
            row['confirmed_anchors'] = confirmed
        else:
            request['confirmed_anchors'] = confirmed
    return request


def build_remake_payload(request, base):
    from .backends import REMOTE
    request = normalize_request(request, base)
    uploads = {}
    for obj, key in request_assets(request):
        path = Path(obj[key])
        remote = REMOTE + '/inputs/' + file_sha256(path) + path.suffix.lower()
        uploads[str(path)] = remote
        obj[key] = remote
    return request, list(uploads.items())


def remake_needs_gpu(request):
    return (
        request.get('mode') == 'recreate'
        or any(request.get(name) for name in ('characters', 'products', 'scenes', 'edits', 'anchors', 'confirmed_anchors'))
        or bool(request.get('copy', {}).get('dialogue'))
    )


def run_remake_colab(args):
    from .backends import _run_colab, legacy_transport
    from .state import file_lock
    path = Path(args.request).resolve()
    raw_request = json.loads(path.read_text(encoding='utf-8'))
    request = normalize_request(raw_request, path.parent)
    folder = args.work_dir or (args.output if args.command not in ('remake-render', 'remake-anchors') else None)
    out = Path(folder).resolve() if folder else path.parent / (path.stem + '_colab')
    out.mkdir(parents=True, exist_ok=True)

    if args.command == 'remake-render':
        request = attach_local_confirmed_anchors(request, out)

    needs_gpu_cmd = args.command in ('remake-anchors', 'remake-render')
    gpu = needs_gpu_cmd and any(remake_needs_gpu(dict(request, **row)) for row in request.get('variants') or [{}])
    if gpu and not args.allow_unverified:
        raise ValueError('首次 GPU 试跑请使用 --allow-unverified')
    args.gpu = (args.gpu or ('L4' if gpu else 'CPU')).upper()
    if gpu and args.gpu == 'CPU':
        raise ValueError('关键帧生成、画面扩散或台词合成需要 GPU')

    transport = legacy_transport()
    args.remote_task = args.command
    args.only = getattr(args, 'only', None)
    args.with_dependents = False
    with file_lock(out / '.remote-controller.lock'):
        result = _run_colab(args, transport, path, request, out)

    if args.command == 'remake-anchors':
        final = result.get('jobs', {}).get('delivery', {}).get('artifacts', [])
        images = [a['path'] for a in final if a.get('kind') == 'image']
        compares = [p for p in images if Path(p).stem.endswith('_compare')]
        for p in (compares or images):
            print('🖼️ 关键帧预览：', p)
        session = result.get('session', '')
        sess_flag = f' --session {session}' if session else ''
        print(f'👉 请检查上述关键帧预览图；确认形象无误后执行 Step 2：\n   python scripts/h3studio.py remake-render {args.request}{sess_flag} --confirmed --allow-unverified --output final.mp4')

    if args.output and args.command == 'remake-render':
        final = result.get('jobs', {}).get('delivery', {}).get('artifacts', [])
        videos = [a for a in final if a.get('kind') == 'video']
        for item in videos:
            destination = Path(args.output).resolve()
            if len(videos) > 1:
                destination = destination.with_name(destination.stem + '_' + item['metadata']['variant'] + destination.suffix)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if Path(item['path']).resolve() != destination:
                shutil.copy2(item['path'], destination)
            print('✅ 复刻成片：', destination)
    return result
