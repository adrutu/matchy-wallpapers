"""Fake OpenAI-compatible captioner for run_tests.py.

Usage: fakecap.py <port> <flag-file>. Answers a valid caption, or garbage while <flag-file> exists.
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
FLAG = sys.argv[2]
BLURB = ("a field of colour scattered with small pale specks that catch the light like frost on a window, "
         "the tone deepening toward the edges where the specks thin out and the surface settles into a calm "
         "and even glow")
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        content = "not json at all" if os.path.exists(FLAG) else json.dumps({"title": "Speckled Colour Field", "blurb": BLURB.capitalize() + "."})
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
