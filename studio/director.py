"""简体中文 AI 导演：分镜校验、H3 作业转换、离线时间线及影片拼接。"""
from __future__ import annotations
import json
import re
import shutil
import subprocess
from pathlib import Path
from html import escape

SUPPORTED = {'text', 'ref', 'frames'}
TRANSITIONS = {'cut', 'fade_black'}

def read_script(path):
    p = Path(path).resolve()
    obj = json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(obj, dict) or not isinstance(obj.get('shots'), list):
        raise ValueError('导演剧本必须包含 shots 数组')
    if not obj['shots']:
        raise ValueError('至少需要一个镜头')
    ids = set()
    for i, shot in enumerate(obj['shots'], 1):
        name = shot.get('id')
        if not isinstance(name, str) or not re.fullmatch('[A-Za-z0-9_-]{1,60}', name):
            raise ValueError(f'镜头 {i} 的 id 必须为英数字、下划线或连字符')
        if name in ids:
            raise ValueError(f'镜头 id 重复：{name}')
        ids.add(name)
        try:
            duration = float(shot['seconds'])
        except (ValueError, KeyError, TypeError):
            raise ValueError(f'{name} 的 seconds 必须是数字') from None
        if not 2 <= duration <= 15:
            raise ValueError(f'{name} 的时长必须在 2–15 秒，长镜头请拆分')
        if not str(shot.get('prompt', '')).strip():
            raise ValueError(f'{name} 缺少提示词 prompt')
        mode = shot.get('mode', 'text')
        if mode not in SUPPORTED:
            raise ValueError(f'{name} 不支持模式 {mode}')
        if mode == 'frames' and not (shot.get('first') or shot.get('last')):
            raise ValueError(f'{name} 首尾帧模式至少要提供 first 或 last')
        if mode == 'ref' and shot.get('inputs') and not re.search(r'@(?:图片|圖片|Image|影片|视频|Video|音频|音訊|Audio)\s*\d+|<(?:Picture|Video|Audio) \d+>', shot['prompt'], re.I):
            raise ValueError(f'{name} 有参考素材，但提示词没有通过 @图片1 等引用（将被忽略）')
        if shot.get('transition', 'cut') not in TRANSITIONS:
            raise ValueError(f'{name} 的 transition 仅支持 cut / fade_black')
        if shot.get('continue_previous') and (i == 1 or mode != 'ref'):
            raise ValueError(f'{name} continue_previous 仅用于第 2 镜头起的 ref 模式')
        if shot.get('aspect', obj.get('aspect', '9:16')) not in {'9:16', '16:9', '1:1', '4:3', '3:4', '21:9', 'auto'}:
            raise ValueError(f'{name} 画幅比例不受支持')
        for key in ['first', 'last']:
            value = shot.get(key)
            if value and not str(value).startswith('@') and not (p.parent / value).is_file():
                raise ValueError(f'{name} 找不到 {key}: {value}')
        for value in shot.get('inputs', []):
            if not str(value).startswith('@') and not (p.parent / value).is_file():
                raise ValueError(f'{name} 找不到素材：{value}')
    return obj, p.parent


def compile_manifest(script):
    jobs = []
    prev = None
    for s in script['shots']:
        job = {'id': s['id'], 'type': 'video', 'mode': s.get('mode', 'text'), 'prompt': s['prompt'],
               'seconds': s['seconds'], 'aspect': s.get('aspect', script.get('aspect', '9:16')),
               'resolution': s.get('resolution', script.get('resolution', '480P')),
               'intensity': s.get('intensity', 'off'), 'fast': s.get('fast', True)}
        for key in ('first', 'last', 'inputs', 'seed', 'steps', 'preserve_ref', 'profile', 'motion_repair', 'sparse_attention'):
            if key in s:
                job[key] = s[key]
        if s.get('continue_previous'):
            job['extend'] = prev
            job['extension_output'] = 'segment'
        jobs.append(job)
        prev = s['id']
    return {'gpu': script.get('gpu', 'L4'), 'jobs': jobs}


def timeline_data(script):
    cursor = 0.0
    rows = []
    for s in script['shots']:
        d = float(s['seconds'])
        rows.append({'id': s['id'], 'title': s.get('title', s['id']), 'start': round(cursor, 3),
                     'end': round(cursor + d, 3), 'duration': d, 'transition': s.get('transition', 'cut'),
                     'mode': s.get('mode', 'text'), 'camera': s.get('camera', '')})
        cursor += d
    return {'total_seconds': round(cursor, 3), 'shots': rows}


def write_plan(script_path, output):
    script, base = read_script(script_path)
    man = compile_manifest(script)
    out = Path(output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Manifest 里的素材路径相对于 manifest 文件；输出到原剧本目录时不会破坏相对路径。
    target = base / f'{Path(script_path).stem}.h3-manifest.json'
    target.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'timeline.edl.json').write_text(json.dumps(timeline_data(script), ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'timeline.html').write_text(make_timeline_html(script), encoding='utf-8')
    return target, out


def make_timeline_html(script):
    payload = json.dumps(script, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&','\\u0026')
    template = (Path(__file__).with_name('timeline.html')).read_text(encoding='utf-8')
    return template.replace('__PAYLOAD__', payload)


def _run(args):
    subprocess.run(args, check=True)


def compose_video(script_path, output, ffmpeg='ffmpeg', ffprobe='ffprobe'):
    """拼接每段生成的镜头：分别转标准格式，裁切/黑场淡入淡出，再 concat。"""
    script, base = read_script(script_path)
    output_dir=base/'out'
    report=output_dir/'results.json'
    results=json.loads(report.read_text(encoding='utf-8'))['jobs'] if report.is_file() else {}
    return compose_shots(script,results,output,output_dir,ffmpeg,ffprobe)


def compose_shots(script, results, output, output_dir, ffmpeg='ffmpeg', ffprobe='ffprobe'):
    if not shutil.which(ffmpeg) or not shutil.which(ffprobe):
        raise RuntimeError('需要先安装 ffmpeg 和 ffprobe')
    out = Path(output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = out.parent / f'.{out.stem}-segments'
    scratch.mkdir(parents=True, exist_ok=True)
    aspect = script.get('aspect', '9:16')
    shapes = {'9:16': (720,1280), '16:9':(1280,720), '1:1':(960,960),
              '4:3':(960,720), '3:4':(720,960), '21:9':(1344,576)}
    if aspect == 'auto':
        aspect = '16:9'
    width,height = shapes.get(aspect,(720,1280))
    paths = []
    for i, shot in enumerate(script['shots']):
        src = output_dir / (shot['id'] + '.mp4')
        result=results.get(shot['id'],{})
        if result.get('artifacts'):
            src=Path(result['artifacts'][result.get('primary_index',0)]['path'])
        if not src.is_file():
            raise FileNotFoundError(f'缺少镜头成品：{src}')
        dst = scratch / f'{i:03}.mp4'
        d = float(shot['seconds'])
        from .media import describe_media
        source_info = describe_media(src)
        if source_info['seconds'] + 2 / 24 < d:
            raise ValueError(f'{shot["id"]} 成品时长不足，不能静默拉长或重复画面')
        vf = (f'fps=24,scale={width}:{height}:force_original_aspect_ratio=decrease,'
              f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p')
        # 只有画面淡入/淡出；音轨处理保持一致以便 concat
        if shot.get('transition') == 'fade_black':
            vf += f',fade=t=in:st=0:d=0.2,fade=t=out:st={max(0,d-0.2):g}:d=0.2'
        probing = subprocess.run([ffprobe,'-v','error','-select_streams','a','-show_entries','stream=index','-of','csv=p=0',str(src)],
                                 capture_output=True,text=True,check=True)
        cmd = [ffmpeg,'-y','-v','error','-i',str(src)]
        if not probing.stdout.strip():
            cmd += ['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        cmd += ['-map','0:v:0','-map','0:a:0' if probing.stdout.strip() else '1:a:0','-vf',vf,'-r','24','-af',f'apad=whole_dur={d}', '-t', str(d),
                '-c:v','libx264','-pix_fmt','yuv420p','-crf','19','-preset','veryfast','-c:a','aac',
                '-ar','48000','-ac','2','-b:a','160k','-shortest',str(dst)]
        _run(cmd)
        paths.append(dst)
    concat = scratch / 'concat.txt'
    # ffmpeg concat 的文件名用单引号保护
    concat.write_text(''.join("file '"+str(p).replace("'", "'\\''")+"'\n" for p in paths),encoding='utf-8')
    _run([ffmpeg,'-y','-v','error','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(out)])
    return out
