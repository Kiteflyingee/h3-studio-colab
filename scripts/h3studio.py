#!/usr/bin/env python3
"""H3 Studio Colab 统一命令行（会话管理 · 爆款两步式复刻 · 原创导演 · 单件生成与后期）。"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from studio import colab_cli
from studio.director import read_script, write_plan, compose_video

DEFAULT_URL = 'http://127.0.0.1:8188'


def check_cmd(_args):
    print('colab：', colab_cli.colab_exe())
    raw = colab_cli.colab('sessions', timeout=120)
    units, usage_text = colab_cli.usage()
    print('运算单元：', units if units is not None else usage_text[-400:])
    print(raw.strip())


def sessions_cmd(_args):
    print(colab_cli.sessions()[1])
    for f in sorted(colab_cli.KEEP.glob('*.json')) if colab_cli.KEEP.exists() else []:
        info = json.loads(f.read_text(encoding='utf-8'))
        left = '跑批次中' if info.get('expires') is None else f"{max(0, info['expires'] - time.time()) / 60:.0f} 分钟后自动关闭"
        print(f"⏸️ {info['session']}：{left}")


def keep_cmd(args):
    minutes = min(args.minutes, 240)
    colab_cli.set_keep(args.session, time.time() + minutes * 60)
    colab_cli.start_watchdog(args.session)
    print(f'⏸️ {args.session} 再留 {minutes:g} 分钟，之后没有新工作就自动关闭')


def stop_cmd(args):
    names = colab_cli.sessions()[0] if args.all else [args.session]
    if not names or names == [None]:
        raise ValueError('请指定 --session NAME 或 --all')
    for n in names:
        print(n, colab_cli.colab('stop', '-s', n, timeout=300, check=False).strip()[-200:])
        colab_cli.drop_keep(n)


def doctor(args):
    from studio.cli import doctor_command
    return doctor_command(args)


def director_compile(args):
    manifest, out = write_plan(args.script, args.output or Path(args.script).resolve().parent / 'director_preview')
    print('✅ H3 任务清单：', manifest)
    print('✅ 可离线打开的时间线：', out / 'timeline.html')
    print('✅ 镜头清单：', out / 'timeline.edl.json')


def director_render(args):
    script, base = read_script(args.script)
    if len(script['shots']) >= 2 and not args.confirmed:
        raise ValueError('多镜头任务需要先确认每段的秒数、分辨率和数量；确认后加 --confirmed')
    manifest, _ = write_plan(args.script, Path(args.script).resolve().parent / 'director_preview')
    if args.backend in ('current', 'local'):
        from studio.current_runtime import execute_storyboard
        files = execute_storyboard(
            args.script, url=args.url, input_dir=args.input_dir,
            allow_unverified=args.allow_unverified, resume=args.resume, only=args.only, backend=args.backend,
        )
        for f in files:
            print('✅', f)
    else:
        from studio.cli import run_command
        from studio.state import atomic_json
        if args.output:
            if args.only:
                raise ValueError('Colab 合成全片需要所有镜头。局部渲染请省略 --output。')
            jobs = json.loads(manifest.read_text(encoding='utf-8'))
            jobs['director'] = {
                'aspect': script.get('aspect', '9:16'),
                'shots': [{'id': shot['id'], 'seconds': shot['seconds'], 'transition': shot.get('transition', 'cut')} for shot in script['shots']],
            }
            atomic_json(manifest, jobs)
        options = argparse.Namespace(**vars(args))
        options.manifest = str(manifest)
        options.output_dir = str(base / 'out')
        options.with_dependents = bool(args.only)
        report = run_command(options)
        if args.output:
            src = Path(report['jobs']['director-delivery']['file'])
            destination = Path(args.output).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            if src.resolve() != destination:
                shutil.copy2(src, destination)
            print('✅ 全片已合成：', destination)
        return
    if args.output:
        result = compose_video(args.script, args.output)
        print('✅ 全片已合成：', result)


def main():
    ap = argparse.ArgumentParser(description='H3 Studio Colab 统一命令行')
    sub = ap.add_subparsers(dest='command', required=True)

    sub.add_parser('check', help='检查 Colab CLI 登录、剩余运算单元与活跃会话').set_defaults(fn=check_cmd)
    sub.add_parser('sessions', help='查看当前运行中的 Colab 会话与自动释放倒计时').set_defaults(fn=sessions_cmd)
    pk = sub.add_parser('keep', help='延长 Colab 会话保留时间（最多 240 分钟）')
    pk.add_argument('--session', required=True)
    pk.add_argument('--minutes', type=float, required=True)
    pk.set_defaults(fn=keep_cmd)
    ps = sub.add_parser('stop', help='立即释放指定或全部 Colab 会话')
    ps.add_argument('--session')
    ps.add_argument('--all', action='store_true')
    ps.set_defaults(fn=stop_cmd)
    pw = sub.add_parser('_watchdog')
    pw.add_argument('--session', required=True)
    pw.set_defaults(fn=lambda a: colab_cli.watchdog(a.session))

    p = sub.add_parser('doctor', help='只读检测 ComfyUI、GPU、已装节点和模型')
    p.add_argument('--url', default=DEFAULT_URL)
    p.add_argument('--manifest')
    p.add_argument('--offline', action='store_true')
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=doctor)

    p = sub.add_parser('director-compile', help='将导演剧本转换为 H3 清单和离线时间线')
    p.add_argument('script')
    p.add_argument('--output')
    p.set_defaults(fn=director_compile)

    p = sub.add_parser('director-render', help='通过 Colab CLI 渲染分镜，可在远端合成全片')
    p.add_argument('script')
    p.add_argument('--backend', choices=['current', 'local', 'colab'], default='colab')
    p.add_argument('--url', default=DEFAULT_URL)
    p.add_argument('--input-dir')
    p.add_argument('--confirmed', action='store_true')
    from studio.cli import add_colab_options, add_commands
    add_colab_options(p)
    p.add_argument('--output')
    p.add_argument('--allow-unverified', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--only')
    p.set_defaults(fn=director_render)

    p = sub.add_parser('director-compose', help='将已生成的 out/<镜头id>.mp4 拼成完整成片')
    p.add_argument('script')
    p.add_argument('--output', required=True)
    p.set_defaults(fn=lambda a: print('✅ 成片：', compose_video(a.script, a.output)))

    add_commands(sub)
    a = ap.parse_args()
    try:
        a.fn(a)
    except (RuntimeError, ValueError, FileNotFoundError, PermissionError, subprocess.CalledProcessError) as e:
        print('❌', str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
