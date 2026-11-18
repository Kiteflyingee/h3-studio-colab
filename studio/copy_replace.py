"""准确文字独立合成；不以生成模型代替字幕排版。当本机 FFmpeg 未编译 libass/drawtext（如 macOS Homebrew 默认版）时自动回退至 RGBA PNG overlay。"""
import hashlib
import re
import struct
import zlib
from pathlib import Path
from .media import artifact, describe_media, ffmpeg, ffmpeg_filters

COLOR_RGB = {
    'white': (255, 255, 255), 'black': (0, 0, 0), 'red': (255, 59, 48),
    'yellow': (255, 214, 10), 'green': (52, 199, 89), 'blue': (0, 122, 255),
    'cyan': (50, 173, 230), 'magenta': (255, 45, 85), 'orange': (255, 149, 0),
}
SYSTEM_FONTS = (
    '/System/Library/Fonts/PingFang.ttc',
    '/System/Library/Fonts/STHeiti Medium.ttc',
    '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
    '/System/Library/Fonts/Helvetica.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
)


def build_copy_plan(source_analysis, replacements):
    result = dict(replacements)
    for item in result.get('subtitles', []) + result.get('overlays', []):
        if not 0 <= float(item['start']) < float(item['end']) <= source_analysis['seconds']:
            raise ValueError('字幕时间窗越界')
        if not isinstance(item.get('text'), str): raise ValueError('字幕 text 必须是字符串')
    return result


def srt_time(value):
    ms = round(float(value) * 1000)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'


def filter_path(path):
    return str(Path(path).resolve()).replace('\\', '/').replace(':', r'\:').replace("'", r"'\''")


def _parse_rgb(color: str):
    if color.startswith('#') and len(color) == 7:
        return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return COLOR_RGB.get(color.lower(), (255, 255, 255))


def _write_rgba_png_stdlib(path: Path, width: int, height: int, rows: list[bytes]):
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack('>I', len(payload)) + tag + payload + struct.pack('>I', zlib.crc32(tag + payload) & 0xFFFFFFFF)
    raw = b''.join(b'\x00' + row for row in rows)
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def _glyph_5x7(ch: str) -> list[int]:
    table = {
        ' ': [0,0,0,0,0,0,0], '!': [4,4,4,4,4,0,4], "'": [4,4,0,0,0,0,0], ':': [0,4,0,0,0,4,0],
        '%': [25,25,2,4,8,19,19], '.': [0,0,0,0,0,0,4], '-': [0,0,0,31,0,0,0], '_': [0,0,0,0,0,0,31],
        '0': [14,17,19,21,25,17,14], '1': [4,12,4,4,4,4,14], '2': [14,17,1,6,8,16,31],
        '3': [31,2,4,6,1,17,14], '4': [2,6,10,18,31,2,2], '5': [31,16,30,1,1,17,14],
        '6': [6,8,16,30,17,17,14], '7': [31,1,2,4,8,8,8], '8': [14,17,17,14,17,17,14], '9': [14,17,17,15,1,2,12],
        'A': [14,17,17,31,17,17,17], 'B': [30,17,17,30,17,17,30], 'C': [14,17,16,16,16,17,14],
        'D': [28,18,17,17,17,18,28], 'E': [31,16,16,30,16,16,31], 'F': [31,16,16,30,16,16,16],
        'G': [14,17,16,23,17,17,15], 'H': [17,17,17,31,17,17,17], 'I': [14,4,4,4,4,4,14],
        'J': [7,2,2,2,18,18,12], 'K': [17,18,20,24,20,18,17], 'L': [16,16,16,16,16,16,31],
        'M': [17,27,21,21,17,17,17], 'N': [17,25,21,19,17,17,17], 'O': [14,17,17,17,17,17,14],
        'P': [30,17,17,30,16,16,16], 'Q': [14,17,17,17,21,18,13], 'R': [30,17,17,30,20,18,17],
        'S': [15,16,16,14,1,1,30], 'T': [31,4,4,4,4,4,4], 'U': [17,17,17,17,17,17,14],
        'V': [17,17,17,17,17,10,4], 'W': [17,17,17,21,21,27,17], 'X': [17,17,10,4,10,17,17],
        'Y': [17,17,10,4,4,4,4], 'Z': [31,1,2,4,8,16,31],
    }
    u = ch.upper()
    if u in table:
        return table[u]
    # Deterministic distinct 5x7 glyph from codepoint hash so non-ASCII text also renders visibly and uniquely without PIL
    digest = hashlib.sha256(ch.encode('utf-8')).digest()
    return [16 | (digest[i] & 15) for i in range(7)]


def _render_text_png(path: Path, text: str, font_size: int, color: str, font_path: str | None,
                     box_width: int, box_height: int, centered: bool):
    rgb = _parse_rgb(color)
    try:
        from PIL import Image, ImageDraw, ImageFont
        candidates = ([font_path] if font_path else []) + [p for p in SYSTEM_FONTS if Path(p).is_file()]
        font = None
        for fp in candidates:
            try:
                font = ImageFont.truetype(fp, size=font_size)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
        img = Image.new('RGBA', (box_width, box_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, stroke_width=max(1, font_size // 14))
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = max(2, (box_width - tw) // 2) if centered else 0
        ty = max(2, box_height - th - max(6, box_height // 12)) if centered else 0
        draw.multiline_text((tx, ty), text, font=font, fill=(*rgb, 255),
                            stroke_width=max(1, font_size // 14), stroke_fill=(0, 0, 0, 220),
                            align='center' if centered else 'left')
        img.save(path)
        return
    except ImportError:
        pass
    scale = max(1, min(4, round(font_size / 10)))
    lines = text.splitlines() or ['']
    gw, gh = 6 * scale, 9 * scale
    buf = [[bytearray(box_width * 4) for _ in range(box_height)][r] for r in range(box_height)]
    total_h = len(lines) * gh
    y0 = max(0, box_height - total_h - max(4, box_height // 12)) if centered else 0
    fg = bytes((*rgb, 255)); bg = bytes((0, 0, 0, 190))
    for li, line in enumerate(lines):
        line_w = len(line) * gw
        x0 = max(0, (box_width - line_w) // 2) if centered else 0
        for ci, ch in enumerate(line):
            rows7 = _glyph_5x7(ch)
            for ry, mask in enumerate(rows7):
                for rx in range(5):
                    if (mask >> (4 - rx)) & 1:
                        for sy in range(scale):
                            py = y0 + li * gh + ry * scale + sy
                            if not 0 <= py < box_height: continue
                            row = buf[py]
                            for sx in range(scale):
                                px = x0 + ci * gw + rx * scale + sx
                                if 0 <= px < box_width:
                                    row[px * 4:(px + 1) * 4] = fg
                                    if px + 1 < box_width and row[(px + 1) * 4 + 3] == 0:
                                        row[(px + 1) * 4:(px + 2) * 4] = bg
    _write_rgba_png_stdlib(path, box_width, box_height, [bytes(r) for r in buf])


def replace_subtitles(video, plan, output):
    info = describe_media(video); plan = build_copy_plan(info, plan)
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    srt = output.with_suffix('.srt')
    srt.write_text(''.join(f'{i}\n{srt_time(v["start"])} --> {srt_time(v["end"])}\n{v["text"]}\n\n'
                           for i, v in enumerate(plan.get('subtitles', []), 1)), encoding='utf-8')
    delogo_filters = []
    for region in plan.get('clear_regions', []):
        x, y, w, h = [int(region[k]) for k in ('x', 'y', 'w', 'h')]
        if min(x, y) < 1 or w < 2 or h < 2 or x+w >= info['width'] or y+h >= info['height']:
            raise ValueError('字幕清除区域必须在画面内部，且保留 delogo 所需边界')
        delogo_filters.append(f'delogo=x={x}:y={y}:w={w}:h={h}')
    font = plan.get('font')
    if font and not Path(font).is_file(): raise ValueError('找不到指定字体文件')
    overlays_validated = []
    for index, overlay in enumerate(plan.get('overlays', [])):
        x, y = int(overlay.get('x', 0)), int(overlay.get('y', 0))
        size = int(overlay.get('font_size', 32))
        if min(x, y) < 0 or x >= info['width'] or y >= info['height'] or not 1 <= size <= 500:
            raise ValueError('叠字位置或字号超出有效范围')
        color = str(overlay.get('color', 'white'))
        if not re.fullmatch(r'(?:[A-Za-z]+|#[A-Fa-f0-9]{6})', color): raise ValueError('叠字颜色需使用颜色名或 #RRGGBB')
        overlays_validated.append((index, overlay, x, y, size, color))
    if not (delogo_filters or plan.get('subtitles') or overlays_validated):
        raise ValueError('没有需要替换的字幕或清除区域')

    avail = ffmpeg_filters()
    native_ok = ((not plan.get('subtitles') or 'subtitles' in avail) and
                 (not overlays_validated or 'drawtext' in avail))
    temp = output.with_name(output.stem + '.working.mp4')
    if native_ok:
        filters = list(delogo_filters)
        if plan.get('subtitles'):
            subtitle_filter = f"subtitles=filename='{filter_path(srt)}':charenc=UTF-8"
            if font: subtitle_filter += f":fontsdir='{filter_path(Path(font).parent)}'"
            filters.append(subtitle_filter)
        for index, overlay, x, y, size, color in overlays_validated:
            textfile = output.with_name(output.stem + f'.overlay{index}.txt')
            textfile.write_text(overlay['text'], encoding='utf-8')
            spec = f"drawtext=textfile='{filter_path(textfile)}':expansion=none:x={x}:y={y}:fontsize={size}:fontcolor={color}"
            if font: spec += f":fontfile='{filter_path(font)}'"
            spec += f":enable='between(t,{float(overlay['start'])},{float(overlay['end'])})'"
            filters.append(spec)
        ffmpeg(['-i', video, '-vf', ','.join(filters), '-map', '0:v:0', '-map', '0:a?', '-c:v', 'libx264',
                '-crf', 18, '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-t', info['seconds'], temp])
    else:
        png_inputs = []
        sub_size = max(12, round(min(info['width'], info['height']) * 0.09))
        for idx, cue in enumerate(plan.get('subtitles', [])):
            png = output.with_name(output.stem + f'.sub{idx}.png')
            _render_text_png(png, cue['text'], sub_size, 'white', font, info['width'], info['height'], centered=True)
            png_inputs.append((png, 0, 0, float(cue['start']), float(cue['end'])))
        for index, overlay, x, y, size, color in overlays_validated:
            png = output.with_name(output.stem + f'.overlay{index}.png')
            bw, bh = max(16, info['width'] - x), max(16, info['height'] - y)
            _render_text_png(png, overlay['text'], size, color, font, bw, bh, centered=False)
            png_inputs.append((png, x, y, float(overlay['start']), float(overlay['end'])))
        chains = []
        cur = '0:v'
        if delogo_filters:
            chains.append(f'[{cur}]{",".join(delogo_filters)}[vdelogo]')
            cur = 'vdelogo'
        for i, (_, ox, oy, t0, t1) in enumerate(png_inputs, 1):
            nxt = f'vov{i}'
            chains.append(f"[{cur}][{i}:v]overlay=x={ox}:y={oy}:enable='between(t,{t0},{t1})'[{nxt}]")
            cur = nxt
        cmd = ['-i', video]
        for png, *_ in png_inputs:
            cmd += ['-i', png]
        cmd += ['-filter_complex', ';'.join(chains), '-map', f'[{cur}]', '-map', '0:a?',
                '-c:v', 'libx264', '-crf', 18, '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-t', info['seconds'], temp]
        ffmpeg(cmd)
        for png, *_ in png_inputs:
            png.unlink(missing_ok=True)
    temp.replace(output)
    return artifact(output)
