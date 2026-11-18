"""爆款视频复刻两步式编译器：
- Step 1 (remake-anchors)：按镜头/编辑时间窗切段 → 抽取 24fps 锚点帧 → P-H3-Image 编辑 + 仿射对齐锁死背景 → 输出关键帧预览图与左右对比图供用户确认。
- Step 2 (remake-render)：读取已确认的关键帧锚点图 → WepeNerd H3 Ripple 6 步 LCM 扩散全段（或 recreate 模式走 Ref2VA 640p 静音代理重制）→ 串联 TTS/混音/字幕并合成成片。
"""
import copy
import json
import shutil
from pathlib import Path
from .media import trim_video, describe_media, ffmpeg


def _collect_edits(request, analysis):
    mode = request.get('mode', 'edit')
    if mode not in ('edit', 'recreate'):
        raise ValueError('复刻 mode 只能是 edit/recreate')
    edits = []
    for field, kind in [('characters', 'character'), ('products', 'product'), ('scenes', 'scene')]:
        for value in request.get(field, []):
            if not value.get('target_id'):
                raise ValueError(f'{field} 必须明确 target_id，不能盲替换所有目标')
            edits.append(dict(value, kind=kind))
    edits += request.get('edits', [])
    boundaries = set()
    for edit in edits:
        if not edit.get('target_id') or edit.get('kind') not in ('character', 'product', 'scene', 'in_scene_text'):
            raise ValueError('每个修改需明确 kind 与 target_id')
        unknown = set(edit) - {'kind', 'target_id', 'reference', 'prompt', 'mask', 'time_range'}
        if unknown:
            raise ValueError('不支持的编辑字段：' + ', '.join(sorted(unknown)))
        if edit.get('time_range'):
            a, b = map(float, edit['time_range'])
            if not 0 <= a < b <= analysis['seconds']:
                raise ValueError('编辑 time_range 超出源片')
            boundaries.update((a, b))
    return mode, edits, boundaries


def _pack_recreate_shots(shots, target_min=6.0, target_max=9.4):
    """recreate 模式将相邻快切短镜头合并打包至 6~9.4 秒黄金窗口，避免碎切成几十个微任务。"""
    if len(shots) <= 1:
        return shots
    packed = []
    cur_start = float(shots[0]['start'])
    cur_end = float(shots[0]['end'])
    for s in shots[1:]:
        s_start, s_end = float(s['start']), float(s['end'])
        if abs(s_start - cur_end) < 0.05 and (s_end - cur_start) <= target_max and (cur_end - cur_start) < target_min:
            cur_end = s_end
        else:
            packed.append({'start': cur_start, 'end': cur_end})
            cur_start, cur_end = s_start, s_end
    packed.append({'start': cur_start, 'end': cur_end})
    return packed


def _iter_segments(request, analysis):
    mode, edits, boundaries = _collect_edits(request, analysis)
    raw_shots = analysis.get('shots') or [{'start': 0, 'end': analysis['seconds']}]
    shots = _pack_recreate_shots(raw_shots) if (mode == 'recreate' and not boundaries) else raw_shots
    maximum = 9.4 if mode == 'recreate' else 14.0
    segments = []
    for shot in shots:
        start = float(shot['start'])
        end = float(shot['end'])
        if not 0 <= start < end <= float(analysis['seconds']) + 0.001:
            raise ValueError('分析时间码超出源视频')
        while start < end - 0.001:
            upcoming = [t for t in boundaries if t > start + 0.001]
            stop = min(end, start + maximum, min(upcoming) if upcoming else end)
            duration = stop - start
            index = len(segments) + 1
            active = [
                {k: v for k, v in e.items() if k != 'time_range' and e.get('kind') != 'in_scene_text'}
                for e in edits
                if e.get('kind') != 'in_scene_text'
                and (not e.get('time_range') or (float(e['time_range'][0]) < stop and float(e['time_range'][1]) > start))
            ]
            segments.append((f'remake_{index:03}', start, stop, duration, active))
            start = stop
    return mode, segments


def _anchor_frames_for(request, duration):
    actual_frames = max(1, round(max(1 / 24, duration) * 24))
    length = max(5, round(max(5 / 24, duration) * 24))
    length += (5 - length) % 17
    max_valid = min(length - 1, actual_frames - 1)
    custom = request.get('anchor_frames')
    if custom is not None:
        frames = sorted({min(max(0, int(f)), max_valid) for f in custom})
    else:
        frames = [0] if duration < 4.0 or max_valid == 0 else [0, max_valid]
    if not 1 <= len(frames) <= 4:
        raise ValueError('每段锚点帧数量需在 1–4 之间')
    return length, frames


def _build_anchor_prompt(request, active_edits, preserve):
    lines = [
        'Edit @图片1 while strictly preserving its camera framing, perspective, subject pose, lighting, and all unedited background areas.'
    ]
    pic_idx = 2
    for edit in active_edits:
        clause = f'Replace/modify the {edit["kind"]} ({edit["target_id"]})'
        if edit.get('reference'):
            clause += f' to match the exact identity and appearance in @图片{pic_idx}'
            pic_idx += 1
        if edit.get('prompt'):
            clause += f': {edit["prompt"]}'
        lines.append(clause + '.')
    if preserve:
        lines.append('Keep unchanged: ' + ', '.join(preserve) + '.')
    if request.get('prompt'):
        lines.append(str(request['prompt']).strip())
    return '\n'.join(lines)


def compile_anchor_plan(request, analysis):
    mode, segments = _iter_segments(request, analysis)
    if mode != 'edit':
        return {'schema_version': 2, 'jobs': [], 'anchors_map': {}, 'source_video': request['source_video']}
    jobs = []
    anchors_map = {}
    for jid, start, stop, duration, active in segments:
        if not active or request.get('anchors'):
            continue
        preserve = request.get('preserve', ['characters', 'camera', 'background', 'timing', 'audio'])
        changed = {e['kind'] for e in active}
        preserve = [p for p in preserve if (p != 'characters' or 'character' not in changed) and (p != 'background' or 'scene' not in changed)]
        _, frames = _anchor_frames_for(request, duration)
        refs = [e['reference'] for e in active if e.get('reference')]
        mask = next((e['mask'] for e in active if e.get('mask')), None)
        align = bool(request.get('align_composite', not any(e['kind'] == 'scene' for e in active)))
        prompt = _build_anchor_prompt(request, active, preserve)
        entries = []
        for idx, frame in enumerate(frames, 1):
            aid = f'{jid}_anchor{idx}'
            src_rel = f'inputs/{aid}_src.png'
            ed_rel = f'anchors/{aid}.png'
            job = {
                'id': aid,
                'type': 'image',
                'profile': 'ph3-lora-int8',
                'size': '1.3K',
                'aspect': 'auto',
                'inputs': [src_rel, *refs],
                'prompt': prompt,
                'align_to_first': align,
                'metadata': {'shot_id': jid, 'source_start': start, 'source_end': stop, 'frame': frame},
            }
            if mask:
                job['align_mask'] = mask
            if 'seed' in request:
                job['seed'] = int(request['seed']) + idx - 1
            jobs.append(job)
            entries.append({'frame': frame, 'job_id': aid, 'src': src_rel, 'edited': ed_rel})
        anchors_map[jid] = entries
    return {'schema_version': 2, 'jobs': jobs, 'anchors_map': anchors_map, 'source_video': request['source_video']}


def materialize_anchor_plan(anchor_plan, destination):
    destination = Path(destination).resolve()
    (destination / 'inputs').mkdir(parents=True, exist_ok=True)
    (destination / 'anchors').mkdir(parents=True, exist_ok=True)
    ready = copy.deepcopy(anchor_plan)
    source_video = ready['source_video']
    for job in ready['jobs']:
        meta = job['metadata']
        src_png = destination / job['inputs'][0]
        shot_clip = destination / 'inputs' / f"{meta['shot_id']}.mp4"
        shot_dur = meta['source_end'] - meta['source_start']
        if not shot_clip.is_file():
            trim_video(source_video, shot_clip, shot_dur, meta['source_start'])
        at_sec = min(max(0.0, meta['frame'] / 24.0), max(0.0, shot_dur - 1.5 / 24.0))
        ffmpeg(['-ss', at_sec, '-i', shot_clip, '-vf', 'fps=24', '-frames:v', 1, src_png])
        job['inputs'][0] = str(src_png)
    manifest_path = destination / 'anchor_manifest.json'
    manifest_path.write_text(json.dumps({'jobs': ready['jobs']}, ensure_ascii=False, indent=2), encoding='utf-8')
    (destination / 'anchors_plan.json').write_text(json.dumps(ready['anchors_map'], ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest_path


def finalize_anchors(anchor_plan, destination, results):
    destination = Path(destination).resolve()
    anchors_dir = destination / 'anchors'
    anchors_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for entries in anchor_plan.get('anchors_map', {}).values():
        for entry in entries:
            aid = entry['job_id']
            job_res = results['jobs'].get(aid, {})
            if job_res.get('status') not in ('done', 'skipped') or not job_res.get('artifacts'):
                raise RuntimeError(f'关键帧生成未成功：{aid}')
            gen_path = Path(job_res['artifacts'][job_res.get('primary_index', 0)]['path'])
            dst_png = destination / entry['edited']
            shutil.copy2(gen_path, dst_png)
            src_png = destination / entry['src']
            cmp_jpg = anchors_dir / f'{aid}_compare.jpg'
            (anchors_dir / f'{aid}_compare.png').unlink(missing_ok=True)
            if src_png.is_file():
                src_info = describe_media(src_png)
                w, h = src_info['width'], src_info['height']
                ffmpeg([
                    '-i', src_png, '-i', dst_png,
                    '-filter_complex', f'[1:v]scale={w}:{h}[ed];[0:v][ed]hstack=inputs=2',
                    '-frames:v', 1, '-q:v', '3', cmp_jpg,
                ])
            saved.append((dst_png, cmp_jpg if cmp_jpg.is_file() else None))
    return saved


def _build_recreate_prompt(request, active_edits):
    refs = []
    lines = ['[reference generation] Follow @视频1 camera choreography, shot cuts, staging, and rhythm closely.']
    pic_idx = 1
    for edit in active_edits:
        if edit.get('reference'):
            refs.append(edit['reference'])
            lines.append(f'Replace {edit["kind"]} ({edit["target_id"]}) with the exact subject/design from @图片{pic_idx}. ' + edit.get('prompt', ''))
            pic_idx += 1
        elif edit.get('prompt'):
            lines.append(f'For {edit["target_id"]}: {edit["prompt"]}')
    if request.get('prompt'):
        lines.append(str(request['prompt']).strip())
    return '\n'.join(lines), refs


def compile_remake(request, analysis, anchors_dir=None, require_confirmed_anchors=False):
    mode, segments = _iter_segments(request, analysis)
    jobs = []
    timeline = []
    copy_request = request.get('copy', {})
    confirmed_map = request.get('confirmed_anchors') or {}

    for jid, start, stop, duration, active in segments:
        generated = max(2.0, duration) if mode == 'recreate' else max(5 / 24, duration)
        metadata = {'source_start': start, 'source_end': stop, 'timeline_seconds': duration}
        preserve = request.get('preserve', ['characters', 'camera', 'background', 'timing', 'audio'])
        changed = {e['kind'] for e in active}
        preserve = [p for p in preserve if p != 'characters' or 'character' not in changed]
        preserve = [p for p in preserve if p != 'background' or 'scene' not in changed]

        if mode == 'recreate':
            prompt, refs = _build_recreate_prompt(request, active)
            visual = {
                'id': jid,
                'type': 'video',
                'mode': 'ref',
                'profile': request.get('profile', 'h3-ref2va-int8'),
                'inputs': [*refs, request['source_video']],
                'prompt': prompt,
                'seconds': round(min(15.0, generated), 4),
                'resolution': request.get('resolution', '480P'),
                'aspect': request.get('aspect', 'auto'),
                'metadata': dict(metadata, recreate_ref_proxy=True),
            }
            jobs.append(visual)
            output_id = jid
        elif active or request.get('anchors') or jid in confirmed_map:
            if request.get('anchors'):
                shot_anchors = copy.deepcopy(request['anchors'])
            elif jid in confirmed_map:
                shot_anchors = copy.deepcopy(confirmed_map[jid])
            else:
                _, frames = _anchor_frames_for(request, duration)
                shot_anchors = []
                for idx, frame in enumerate(frames, 1):
                    aid = f'{jid}_anchor{idx}'
                    candidate = (Path(anchors_dir) / f'{aid}.png') if anchors_dir else Path(f'anchors/{aid}.png')
                    if require_confirmed_anchors and not candidate.is_file():
                        raise ValueError(
                            f'尚未生成或确认关键帧预览图（缺少 {candidate.name}）！'
                            '请先运行 `python scripts/h3studio.py remake-anchors <request.json>` 生成关键帧，确认形象无误后再执行 `remake-render`。'
                        )
                    shot_anchors.append({'frame': frame, 'image': str(candidate)})
            visual = {
                'id': jid,
                'type': 'video_edit',
                'profile': 'h3-ripple',
                'source_video': request['source_video'],
                'anchors': shot_anchors,
                'preserve': preserve,
                'seconds': round(min(15.0, max(2.0, duration)), 4),
                'resolution': request.get('resolution', '480P'),
                'aspect': request.get('aspect', 'auto'),
                'metadata': dict(metadata, edits=copy.deepcopy(active)),
            }
            for opt in ('align_composite', 'comparison', 'crop', 'megapixels'):
                if opt in request:
                    visual[opt] = copy.deepcopy(request[opt])
            jobs.append(visual)
            output_id = jid
        else:
            visual = {'id': jid, 'type': 'media_copy', 'source_video': request['source_video'], 'metadata': metadata}
            jobs.append(visual)
            output_id = jid

        dialogue = copy_request.get('dialogue', [])
        if isinstance(dialogue, str):
            if analysis['seconds'] > 14:
                raise ValueError('长片的新台词需要带 start/end 的分段数组，避免重复整篇文案')
            dialogue = [{'start': 0, 'end': analysis['seconds'], 'text': dialogue}]
        for line_no, line in enumerate(dialogue):
            if float(line['start']) < stop and float(line['end']) > start:
                if float(line['start']) < start - 0.001 or float(line['end']) > stop + 0.001:
                    raise ValueError('一条台词跨越生成分段，请在镜头/分段边界拆分台词')
                ttsid = f'{jid}_tts{line_no}'
                seconds = float(line['end']) - float(line['start'])
                if not 2 <= seconds <= 15:
                    raise ValueError('每条合成台词时间窗需在 2–15 秒，请合并短台词或拆分长台词')
                description = line.get('voice_description', 'An adult woman speaks clearly and naturally.')
                jobs.append({'id': ttsid, 'type': 'tts', 'prompt': description + '\nSay exactly: "' + line['text'] + '"', 'seconds': seconds})
                mixedid = f'{jid}_voice{line_no}'
                jobs.append({
                    'id': mixedid,
                    'type': 'voice_replace',
                    'source_video': '@' + output_id,
                    'voice': '@' + ttsid,
                    'options': {'start': float(line['start']) - start, 'end': float(line['end']) - start, 'replace': True},
                })
                output_id = mixedid

        subtitles = []
        for item in copy_request.get('subtitles', []):
            a, b = max(start, float(item['start'])), min(stop, float(item['end']))
            if b > a:
                subtitles.append(dict(item, start=a - start, end=b - start))
        overlays = []
        for item in copy_request.get('in_scene_text', []):
            if item.get('tracking') or item.get('perspective'):
                raise ValueError('画面文字目前支持固定位置精确叠字；跟踪/透视文字暂不支持')
            a, b = max(start, float(item['start'])), min(stop, float(item['end']))
            if b > a:
                overlays.append(dict(item, start=a - start, end=b - start))
        if subtitles or overlays or copy_request.get('clear_regions'):
            subid = jid + '_subtitles'
            jobs.append({
                'id': subid,
                'type': 'subtitle_replace',
                'source_video': '@' + output_id,
                'plan': {
                    'subtitles': subtitles,
                    'overlays': overlays,
                    'clear_regions': copy_request.get('clear_regions', []),
                    'font': copy_request.get('font'),
                },
            })
            output_id = subid
        timeline.append({'id': output_id, 'visual_id': jid, 'start': start, 'end': stop, 'seconds': duration})
        start = stop

    return {
        'schema_version': 2,
        'jobs': jobs,
        'timeline': timeline,
        'mode': mode,
        'source_video': request['source_video'],
        'source_fingerprint': analysis.get('source_fingerprint'),
    }


def materialize_plan(plan, destination):
    destination = Path(destination).resolve()
    (destination / 'inputs').mkdir(parents=True, exist_ok=True)
    ready = copy.deepcopy(plan)
    for job in ready['jobs']:
        meta = job.get('metadata', {})
        if 'source_start' not in meta:
            continue
        dur = meta['source_end'] - meta['source_start']
        if meta.get('recreate_ref_proxy'):
            # 640p 静音参考代理：降低 75% 参考视频 Token 并避免原片配乐干扰隐空间
            src_vid = job['inputs'][-1]
            proxy = destination / 'inputs' / f"{job['id']}_ref640.mp4"
            ffmpeg([
                '-ss', meta['source_start'], '-i', src_vid, '-t', dur,
                '-vf', 'fps=24,scale=640:-2:flags=lanczos,scale=trunc(iw/32)*32:trunc(ih/32)*32',
                '-an', '-c:v', 'libx264', '-crf', 18, '-pix_fmt', 'yuv420p', proxy,
            ])
            job['inputs'][-1] = str(proxy)
        else:
            target = destination / 'inputs' / (job['id'] + '.mp4')
            if not target.is_file():
                trim_video(job['source_video'], target, dur, meta['source_start'])
            job['source_video'] = str(target)
    path = destination / 'manifest.json'
    path.write_text(json.dumps(ready, ensure_ascii=False, indent=2), encoding='utf-8')
    return path
