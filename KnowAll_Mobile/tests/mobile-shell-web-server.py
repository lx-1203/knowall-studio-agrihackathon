"""Serve this round's production export with SPA route fallback."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1] / 'dist-calm-shell-web-01a12efd'
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)
    def do_GET(self):
        target = ROOT / self.path.split('?')[0].lstrip('/')
        if not target.is_file():
            self.path = '/index.html'
        super().do_GET()
ThreadingHTTPServer(('127.0.0.1', 8914), Handler).serve_forever()
