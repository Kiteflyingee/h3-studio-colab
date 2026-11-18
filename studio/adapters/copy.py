import shutil
from pathlib import Path
from ..contracts import JobResult
from ..media import artifact
from ..copy_replace import replace_subtitles
from ..audio_mix import mix_voice

LOCAL = True


def run_local(job, staged, context):
    source = Path(staged['source_video']['path'])
    target = context.output_dir / (job['id'] + '.mp4')
    if job['type'] == 'subtitle_replace':
        item = replace_subtitles(source, job.get('plan') or job, target)
    elif job['type'] == 'voice_replace':
        item = mix_voice(source, Path(staged['voice']['path']), job.get('options', {}), target)
    else:
        if source.resolve() != target.resolve(): shutil.copy2(source, target)
        item = artifact(target)
    return JobResult(job['id'], 'done', [item])
