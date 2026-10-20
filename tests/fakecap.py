"""Fake OpenAI-compatible captioner for run_tests.py.

Usage: fakecap.py <port> <flag-file> [key=<k>] [no-json-mode] [new-openai] [parts] [think]

Answers a valid caption, or garbage while <flag-file> exists. A kids prompt (it mentions children) gets
a kids blurb, or garbage while <flag-file>.kids exists. The options make it behave like
servers that are pickier than llama.cpp:
  key=<k>       401 unless the request carries `Authorization: Bearer <k>`
  no-json-mode  400 when the request has `response_format` (an LM Studio-style refusal)
  new-openai    400 on `max_tokens` and on a non-default `temperature` (newer OpenAI models)
  parts         the reply content is a list of content parts instead of a string
  think         the reply starts with an inline <think>...</think> block containing braces
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
FLAG, OPTS = sys.argv[2], sys.argv[3:]
KEY = next((o[4:] for o in OPTS if o.startswith("key=")), None)
BLURB = ("a field of colour scattered with small pale specks that catch the light like frost on a window, "
         "the tone deepening toward the edges where the specks thin out and the surface settles into a calm "
         "and even glow")
KIDS = ("Look at all the tiny white dots! They sparkle like frost on a window. Fun fact: frost is made of "
        "tiny ice crystals that grow on cold glass.")
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if KEY and self.headers.get("Authorization") != "Bearer " + KEY:
            return self.send(401, {"error": {"message": "invalid api key"}})
        if "no-json-mode" in OPTS and "response_format" in req:
            return self.send(400, {"error": "'response_format.type' must be 'json_schema' or 'text'"})
        if "new-openai" in OPTS and "max_tokens" in req:
            return self.send(400, {"error": {"message": "Unsupported parameter: 'max_tokens' is not supported with "
                                             "this model. Use 'max_completion_tokens' instead.", "param": "max_tokens"}})
        if "new-openai" in OPTS and req.get("temperature", 1) != 1:
            return self.send(400, {"error": {"message": "Unsupported value: 'temperature' does not support 0.4 with "
                                             "this model. Only the default (1) value is supported.", "param": "temperature"}})
        prompt = json.dumps(req.get("messages", ""))
        if "children" in prompt:
            content = "not json at all" if os.path.exists(FLAG + ".kids") else json.dumps({"blurb": KIDS})
        else:
            content = "not json at all" if os.path.exists(FLAG) else json.dumps({"title": "Speckled Colour Field", "blurb": BLURB.capitalize() + "."})
        if "think" in OPTS:
            content = '<think>The user wants {"title", "blurb"}; keep it short.</think>\n' + content
        if "parts" in OPTS:
            content = [{"type": "text", "text": content}]
        self.send(200, {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
