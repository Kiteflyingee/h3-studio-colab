from pathlib import Path
from .media import artifact, describe_media, ffmpeg


def restore_source_audio(video, source, output):
    """Keep edited pictures but restore source sound, padding short source tracks."""
    info = describe_media(video)
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.stem + '.working.mp4')
    args = ['-i', video, '-i', source, '-map', '0:v:0', '-c:v', 'copy']
    if describe_media(source)['has_audio']:
        args += ['-map', '1:a:0', '-af', 'apad', '-c:a', 'aac']
    else: args += ['-an']
    ffmpeg(args + ['-t', info['seconds'], temp]); temp.replace(output)
    return artifact(output)


def mix_voice(video, voice, options, output):
    info = describe_media(video); spoken = describe_media(voice)
    start = float(options.get('start', 0)); end = float(options.get('end', start + spoken['seconds']))
    if not 0 <= start < end <= info['seconds'] + .001: raise ValueError('新配音时间窗超出视频')
    if spoken['seconds'] > end-start + .1 and not options.get('trim_voice'):
        raise ValueError('新配音长于指定时间窗，请调整台词或明确选择 trim_voice；不会自动加速')
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    args = ['-i', video, '-i', voice]
    original = '0:a'
    if not info['has_audio']:
        args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']; original = '2:a'
    mute = f",volume=volume=0:enable='between(t,{start},{end})'" if options.get('replace', True) else ''
    chain = (f'[{original}]aformat=sample_rates=48000:channel_layouts=stereo{mute}[original];'
             f'[1:a]atrim=0:{end-start},asetpts=PTS-STARTPTS,adelay={round(start*1000)}:all=1,apad[voice];'
             f'[original][voice]amix=inputs=2:duration=longest:normalize=0,atrim=0:{info["seconds"]}[mixed]')
    temp = output.with_name(output.stem + '.working.mp4')
    ffmpeg(args + ['-filter_complex', chain, '-map', '0:v:0', '-map', '[mixed]', '-c:v', 'libx264',
                   '-crf', 18, '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-t', info['seconds'], temp])
    temp.replace(output)
    return artifact(output)
