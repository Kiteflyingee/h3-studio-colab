"""参考视频的真实时间轴分析；语义工具未配置时明确报告缺项。"""
import json
import re
import subprocess
import tempfile
from pathlib import Path
from .media import describe_media, ffmpeg
from .model_registry import file_sha256


def analyze_source(video, options=None):
    video = Path(video).resolve(); options = options or {}; info = describe_media(video)
    if info['kind'] != 'video': raise ValueError('复刻素材必须是视频')
    result = dict(info, source_video=str(video), source_fingerprint=file_sha256(video),
                  transcript=[], text_regions=[], targets=[], audio_tracks=['mixed'] if info['has_audio'] else [],
                  capabilities={'scene_detection': True, 'asr': False, 'ocr': False, 'vision': False}, warnings=[])
    scan = subprocess.run(['ffmpeg', '-nostdin', '-i', str(video), '-vf',
                           f"select='gt(scene,{float(options.get('scene_threshold', .3))})',showinfo", '-an', '-f', 'null', '-'],
                          capture_output=True, text=True, check=True)
    cuts = sorted({0., info['seconds'], *[float(x) for x in re.findall(r'pts_time:([\d.]+)', scan.stderr)]})
    result['shots'] = [{'start': a, 'end': b, 'seconds': b-a, 'source': 'ffmpeg_scene_detection'}
                       for a, b in zip(cuts, cuts[1:]) if b > a]
    if options.get('analysis_file'):
        imported = json.loads(Path(options['analysis_file']).read_text(encoding='utf-8'))
        if imported.get('source_fingerprint') != result['source_fingerprint']:
            raise ValueError('分析结果与当前视频的内容哈希不匹配')
        for key in ('shots', 'transcript', 'text_regions', 'targets'):
            if key in imported: result[key] = imported[key]
        result['capabilities'].update(imported.get('capabilities', {}))
    if options.get('asr'):
        cfg = options['asr']
        try:
            from faster_whisper import WhisperModel
            model_path = Path(cfg['model'])
            if not model_path.is_dir(): raise ValueError('ASR model 必须指向已有的本地模型目录，禁止隐式下载')
            model = WhisperModel(str(model_path), device=cfg.get('device', 'cpu'), local_files_only=True)
            segments, _ = model.transcribe(str(video), language=cfg.get('language'))
            result['transcript'] = [{'start': s.start, 'end': s.end, 'text': s.text, 'source': 'faster_whisper'} for s in segments]
            result['capabilities']['asr'] = True
        except Exception as exc: result['warnings'].append('ASR 不可用：' + str(exc))
    if options.get('ocr'):
        cfg = options['ocr']
        try:
            with tempfile.TemporaryDirectory(prefix='h3_ocr_') as d:
                for i, shot in enumerate(result['shots']):
                    frame = Path(d) / f'{i}.png'; at = (shot['start'] + shot['end']) / 2
                    ffmpeg(['-ss', at, '-i', video, '-frames:v', 1, frame])
                    found = subprocess.run(['tesseract', str(frame), 'stdout', '-l', cfg.get('language', 'eng'), 'tsv'], capture_output=True, text=True, check=True)
                    import csv, io
                    for row in csv.DictReader(io.StringIO(found.stdout), delimiter='\t'):
                        if row.get('text', '').strip():
                            result['text_regions'].append({'start': shot['start'], 'end': shot['end'], 'text': row['text'],
                                'x': int(row['left']), 'y': int(row['top']), 'w': int(row['width']), 'h': int(row['height']), 'source': 'tesseract_sample'})
            result['capabilities']['ocr'] = True
        except Exception as exc: result['warnings'].append('OCR 不可用：' + str(exc))
    for key in ('asr', 'ocr', 'vision'):
        if not result['capabilities'][key]: result['warnings'].append(f'{key} 未配置/未执行；该部分可导入人工修订分析')
    return result
