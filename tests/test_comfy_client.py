import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from studio.comfy_client import ComfyClient


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/view?'):
            data = b'output-content'
        else: data = json.dumps({}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(data)

    def log_message(self, *args): pass


class ComfyClientTests(unittest.TestCase):
    def test_audio_and_video_outputs_download_without_duplicate(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        c = ComfyClient(f'http://127.0.0.1:{server.server_port}')
        audio = {'filename': 'voice.flac', 'type': 'output'}
        video = {'filename': 'clip.webm', 'type': 'output'}
        with tempfile.TemporaryDirectory() as d:
            files = c.save_result({'outputs': {'save': {'audio': [audio], 'videos': [video], 'gifs': [video]}}}, d)
            self.assertEqual(sorted(x.suffix for x in files), ['.flac', '.webm'])
            self.assertTrue(all(x.read_bytes() == b'output-content' for x in files))

