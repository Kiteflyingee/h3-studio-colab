"""Minimal stdlib RGB PNG shim used by unit tests when Pillow is not installed in system Python."""
import struct
import zlib
from pathlib import Path

COLORS = {'red': (255, 0, 0), 'blue': (0, 0, 255), 'green': (0, 255, 0), 'black': (0, 0, 0), 'white': (255, 255, 255)}


class _SimpleImage:
    def __init__(self, width: int, height: int, rgb=(0, 0, 0)):
        self.size = (width, height)
        self._rgb = rgb

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def getpixel(self, xy): return self._rgb

    def save(self, path):
        w, h = self.size
        row = b'\x00' + bytes(self._rgb) * w
        raw = row * h
        def chunk(tag: bytes, payload: bytes) -> bytes:
            return struct.pack('>I', len(payload)) + tag + payload + struct.pack('>I', zlib.crc32(tag + payload) & 0xFFFFFFFF)
        ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
        Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class _ImageNamespace:
    @staticmethod
    def new(mode, size, color=(0, 0, 0)):
        rgb = COLORS.get(color, (0, 0, 0)) if isinstance(color, str) else tuple(color[:3])
        return _SimpleImage(size[0], size[1], rgb)

    @staticmethod
    def open(path):
        data = Path(path).read_bytes()
        w, h = struct.unpack('>II', data[16:24])
        pos = 8
        idat = bytearray()
        while pos < len(data):
            ln = struct.unpack('>I', data[pos:pos + 4])[0]
            tag = data[pos + 4:pos + 8]
            if tag == b'IDAT': idat.extend(data[pos + 8:pos + 8 + ln])
            pos += 12 + ln
        raw = zlib.decompress(bytes(idat))
        rgb = (raw[1], raw[2], raw[3]) if len(raw) >= 4 else (0, 0, 0)
        return _SimpleImage(w, h, rgb)


try:
    from PIL import Image  # type: ignore
except ImportError:
    Image = _ImageNamespace
