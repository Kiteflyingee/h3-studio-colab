"""使用原生 AddGuide 固定上一段尾部；明确区分新增与累计输出。"""
from pathlib import Path
from ..media import artifact, describe_media, ffmpeg, trim_video, stage_media
from .. import geometry as G
from .h3 import node


def prepare_extension(source, job, context):
    source = Path(source)
    info = describe_media(source)
    if info['kind'] != 'video': raise ValueError('延伸源必须是视频')
    prefix_frames = 22
    prefix_seconds = prefix_frames / 24
    if float(job.get('seconds', 10)) + prefix_seconds > 15:
        raise ValueError('本段延伸加上上下文超过 15 秒窗口，请将新增内容拆成更短的片段')
    w, h = G.dims(job.get('aspect', 'auto'), job.get('resolution', '480P'), (info['width'], info['height']))
    work = context.output_dir / 'work'; work.mkdir(exist_ok=True)
    tail = work / (job['id'] + '_tail.mp4')
    args = ['-ss', max(0, info['seconds'] - prefix_seconds), '-i', source, '-t', prefix_seconds,
            '-vf', f'fps=24,scale={w}:{h}', '-c:v', 'libx264', '-crf', 18, '-pix_fmt', 'yuv420p', '-c:a', 'aac', tail]
    ffmpeg(args)
    return {'source': str(source), 'tail': stage_media(tail, context),
            'context_frames': prefix_frames, 'context_seconds': prefix_seconds}


def apply_context(graph, extension):
    graph['context_video'] = node('LoadVideo', file=extension['tail']['name'])
    graph['context_parts'] = node('GetVideoComponents', video=['context_video', 0])
    args = dict(positive=['cond', 0], latent=['cond', 1], vae=['vae', 0],
                image=['context_parts', 0], frame_idx=0)
    if extension['tail'].get('has_audio'):
        args.update(audio_vae=['audio_vae', 0], audio=['context_parts', 1])
    graph['context_guide'] = node('MiniMaxH3AddGuide', **args)
    graph['guider']['inputs']['conditioning'] = ['context_guide', 0]
    return graph


def concat_segments(sources, output, fps=24):
    output = Path(output)
    work = output.parent / 'work'; work.mkdir(exist_ok=True)
    media=[describe_media(source) for source in sources]
    dims=media[0];normalized=[];include_audio=any(info['has_audio'] for info in media)
    for i,(source,info) in enumerate(zip(sources,media)):
        target=work/f'{output.stem}_concat{i}.mp4'
        args = ['-i', source]
        if include_audio and not info['has_audio']:args+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        args+=['-map','0:v:0']
        if include_audio:args+=['-map','0:a:0' if info['has_audio'] else '1:a:0','-c:a','aac','-ar',48000,'-ac',2]
        args += ['-t', info['seconds'],
                 '-vf', f'fps={fps},scale={dims["width"]}:{dims["height"]}:force_original_aspect_ratio=decrease,pad={dims["width"]}:{dims["height"]}:(ow-iw)/2:(oh-ih)/2,setsar=1',
                 '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', 18, target]
        ffmpeg(args); normalized.append(target.resolve())
    listing = work / f'{output.stem}_concat.txt'
    listing.write_text(''.join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in normalized))
    temp = output.with_name(output.stem + '.joined.mp4')
    ffmpeg(['-f', 'concat', '-safe', 0, '-i', listing, '-c', 'copy', '-movflags', '+faststart', temp])
    temp.replace(output)
    return output


def finalize_extension(source, generated, job, context):
    target = context.output_dir / (job['id'] + '.mp4')
    prefix = float(job['_context_seconds'])
    seconds = float(job['seconds'])
    info = describe_media(generated)
    if info['seconds'] + 2/24 < prefix + seconds: raise RuntimeError('延伸结果未包含请求的新内容时长')
    segment = context.output_dir / 'work' / (job['id'] + '_segment.mp4')
    trim_video(generated, segment, seconds, start=prefix)
    if job.get('extension_output', 'cumulative') == 'cumulative':
        concat_segments([source, segment], target, fps=describe_media(source)['fps'])
    else:
        segment.replace(target)
    return artifact(target)
