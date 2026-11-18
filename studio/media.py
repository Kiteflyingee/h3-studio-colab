"""媒体元数据与内容寻址暂存；路径相同不代表素材未改变。"""
import functools
import json
import struct
import subprocess
from fractions import Fraction
from pathlib import Path
from .contracts import Artifact
from .model_registry import file_sha256

IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
VIDEO_EXT = {'.mp4', '.mov', '.mkv', '.webm', '.avi'}
AUDIO_EXT = {'.wav', '.flac', '.mp3', '.ogg', '.m4a', '.aac'}


class FFmpegError(subprocess.CalledProcessError):
    def __str__(self):
        base = super().__str__()
        detail = (self.stderr or b'').decode('utf-8', 'replace').strip() if isinstance(self.stderr, bytes) else str(self.stderr or '').strip()
        return f'{base}\n{detail[-600:]}' if detail else base


def _probe_image_size_stdlib(path: Path):
    data = path.read_bytes()[:65536]
    if len(data) >= 24 and data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR':
        return struct.unpack('>II', data[16:24])
    if len(data) >= 26 and data[:2] == b'BM':
        w, h = struct.unpack('<ii', data[18:26])
        return abs(w), abs(h)
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                             '-show_entries', 'stream=width,height', '-of', 'json', str(path)],
                            capture_output=True, text=True, check=True)
    stream = (json.loads(result.stdout).get('streams') or [{}])[0]
    if not stream.get('width') or not stream.get('height'):
        raise ValueError('无法读取图片尺寸：' + str(path))
    return int(stream['width']), int(stream['height'])


def describe_media(path):
    path = Path(path).resolve()
    if not path.is_file(): raise FileNotFoundError(path)
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        try:
            from PIL import Image
            with Image.open(path) as image:
                w, h = image.size
        except ImportError:
            w, h = _probe_image_size_stdlib(path)
        return {'kind': 'image', 'width': w, 'height': h, 'has_audio': False, 'seconds': 0}
    if ext not in VIDEO_EXT | AUDIO_EXT: raise ValueError('不支持的媒体格式：' + ext)
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                            capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    video = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    audio = next((s for s in data['streams'] if s['codec_type'] == 'audio'), None)
    if not video and not audio: raise ValueError('文件没有可识别的音视频流：' + str(path))
    value = {'kind': 'video' if video else 'audio', 'has_audio': bool(audio), 'width': None, 'height': None,
             'seconds': float(data.get('format', {}).get('duration') or (video or audio).get('duration') or 0)}
    if video:
        rate = video.get('avg_frame_rate') or video.get('r_frame_rate') or '0/1'
        value.update(width=video['width'], height=video['height'], fps=str(rate),
                     fps_float=float(Fraction(rate)) if rate != '0/0' else 0,
                     frames=int(video['nb_frames']) if str(video.get('nb_frames', '')).isdigit() else None,
                     variable_fps=video.get('avg_frame_rate') != video.get('r_frame_rate'))
    if audio: value.update(sample_rate=int(audio.get('sample_rate', 0)), channels=audio.get('channels'))
    return value


def stage_media(path, context):
    path = Path(path).resolve()
    info = describe_media(path)
    sha = file_sha256(path)
    cache = getattr(context, '_staged', {})
    if sha in cache: return cache[sha]
    if info['kind'] == 'image':
        name = context.client.upload_image(path)
    else:
        if context.input_dir is None: raise ValueError('视频/音频素材需要 --input-dir 指向同一台 ComfyUI 的 input 目录')
        name = context.client.stage_local_file(path, context.input_dir)
    item = dict(info, name=name, path=str(path), sha256=sha)
    cache[sha] = item; context._staged = cache
    return item


def artifact(path):
    info = describe_media(path)
    return Artifact(info['kind'], str(Path(path).resolve()), file_sha256(path), info)


@functools.lru_cache(maxsize=1)
def ffmpeg_filters():
    try:
        res = subprocess.run(['ffmpeg', '-hide_banner', '-filters'], capture_output=True, text=True, check=True)
    except Exception:
        return frozenset()
    names = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].replace('.', '').isalpha() and '->' in parts[2]:
            names.add(parts[1])
    return frozenset(names)


def ffmpeg(args):
    try:
        subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', *map(str, args)], check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(exc.returncode, exc.cmd, output=exc.output, stderr=exc.stderr) from exc


def trim_video(source, target, seconds, start=0):
    target = Path(target); target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.stem + '.working' + target.suffix)
    ffmpeg(['-ss', start, '-i', source, '-t', seconds, '-map', '0:v:0', '-map', '0:a?',
            '-c:v', 'libx264', '-crf', 18, '-pix_fmt', 'yuv420p', '-c:a', 'aac', tmp])
    tmp.replace(target)
    return target


def normalize_reference(source, fps, output_dir):
    """Resample reference frames at the model rate without changing playback time."""
    source = Path(source).resolve()
    info = describe_media(source)
    if info['kind'] != 'video': return source
    if not info.get('variable_fps') and abs(info['fps_float'] - fps) < 1e-6: return source
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / (file_sha256(source) + f'-{fps}fps.mp4')
    if target.is_file(): return target
    temp = target.with_suffix('.working.mp4')
    ffmpeg(['-i', source, '-map', '0:v:0', '-map', '0:a?', '-vf', f'fps={fps}',
            '-c:v', 'libx264', '-crf', 18, '-pix_fmt', 'yuv420p', '-c:a', 'aac', temp])
    temp.replace(target)
    return target
