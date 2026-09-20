"""Redirect old local demo tabs to the single prepared judging server.

python3 scripts/redirect_demo.py --port 8787 --target-port 8795
This serves no files and changes no policies. Stop the old server on --port first.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port',type=int,default=8787)
parser.add_argument('--target-port',type=int,default=8795)
args=parser.parse_args()
class Redirect(BaseHTTPRequestHandler):
    def do_GET(self):
        path=urlsplit(self.path).path
        if path in ('/', '/index.html', '/queue.html', '/demo.html', '/company/index.html'):
            destination='/queue.html?track=stage&present=1'
        elif path == '/close.html':
            destination='/close.html?track=stage&period=2026-05&present=1'
        else:
            destination=self.path
        self.send_response(307)
        self.send_header('Location',f'http://127.0.0.1:{args.target_port}'+destination)
        self.send_header('Cache-Control','no-store')
        self.end_headers()
    do_HEAD=do_GET
    def log_message(self,*args): pass
print(f'Old port {args.port} redirects to judging port {args.target_port}',flush=True)
ThreadingHTTPServer(('127.0.0.1',args.port),Redirect).serve_forever()
