"""This round's production SPA, bound only to loopback."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'dist-calm-discovery-web-01a12efd'
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(root),**kwargs)
    def do_GET(self):
        if not (root/self.path.split('?')[0].lstrip('/')).is_file():
            self.path='/index.html'
        super().do_GET()
ThreadingHTTPServer(('127.0.0.1',8920),Handler).serve_forever()
