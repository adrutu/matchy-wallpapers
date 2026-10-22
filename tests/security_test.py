"""Robustness tests for the engine's edges: request headers it must not trust, concurrent master
builds, a picture replaced in place, hand-edited sidecars with the wrong types, and what a captioner
may answer. Runs the engine in-process (no subprocess, no captioner), so it takes a few seconds.

    python3 tests/security_test.py
"""
import http.client, io, json, os, re, shutil, socket, sys, tempfile, threading, time, urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "matchy", "app"))

from PIL import Image  # noqa: E402

import artsources  # noqa: E402
import caption  # noqa: E402
import engine  # noqa: E402
import library  # noqa: E402
import netguard  # noqa: E402

ok = fail = 0
S = tempfile.mkdtemp(prefix="matchy-sec-")


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("PASS", name)
    else:
        fail += 1; print("FAIL", name, extra)


def make_images(folder, n=2):
    os.makedirs(folder, exist_ok=True)
    for i, c in enumerate([(200, 60, 60), (60, 160, 90), (50, 80, 200)][:n]):
        im = Image.new("RGB", (1600, 900), c)
        im.save(os.path.join(folder, "pic-%d.jpg" % i), quality=95)


def make_engine(name, **over):
    data = os.path.join(S, "data-" + name)
    imgs = os.path.join(S, "imgs-" + name)
    make_images(imgs)
    cfg = engine.merge(engine.DEFAULTS, dict({"data_dir": data, "folders": [{"path": imgs}], "captioners": [],
                                              "caption": {"enabled": False}}, **over))
    eng = engine.Engine(cfg)
    eng.lib.rescan()
    eng.rotate("test")
    return eng


def serve(eng):
    """The engine's own handler on a free loopback port, in a thread; returns the port."""
    handler = type("H", (engine.Handler,), {"engine": eng, "log_message": lambda *a: None})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def req(port, method, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, headers=headers or {})
    r = c.getresponse(); body = r.read(); c.close()
    return r.status, body, dict(r.getheaders())


# ---------------- X-Ingress-Path: a header only the Supervisor may set ----------------
# Direct port (the default ingress_peers is the Supervisor's address, so loopback is not ingress).
eng = make_engine("direct")
port = serve(eng)
cur = eng.current()
st, body, h = req(port, "GET", "/current.json", {"X-Ingress-Path": "//evil.example"})
urls = json.loads(body)["urls"]
check("direct port: X-Ingress-Path not reflected into current.json urls",
      all(u.startswith("/img/") for u in urls.values()), urls)
st, _, h = req(port, "GET", "/img/%s/000000/clean/640x360.jpg" % cur["id"], {"X-Ingress-Path": "//evil.example"})
check("direct port: X-Ingress-Path not reflected into the 302 Location",
      st == 302 and h.get("Location", "").startswith("/img/"), (st, h.get("Location")))
# A folded header value carries its line break into the response otherwise.
s = socket.create_connection(("127.0.0.1", port))
s.sendall(("GET /img/%s/000000/clean/640x360.jpg HTTP/1.1\r\nHost: x\r\nX-Ingress-Path: /a\r\n /b\r\n"
           "Connection: close\r\n\r\n" % cur["id"]).encode())
raw = b""
while True:
    chunk = s.recv(65536)
    if not chunk:
        break
    raw += chunk
s.close()
loc = [l for l in raw.split(b"\r\n") if l.lower().startswith(b"location:")]
check("direct port: a folded X-Ingress-Path cannot break the Location header",
      loc and loc[0] == b"Location: /img/%s/%s/clean/640x360.jpg" % (cur["id"].encode(), cur["rev"].encode()), raw[:300])

# Behind ingress (loopback listed as a peer): the real prefix is used, anything else is not.
eng_in = make_engine("ingress", ingress_peers=["127.0.0.0/8"])
port_in = serve(eng_in)
cur_in = eng_in.current()
st, body, _ = req(port_in, "GET", "/current.json", {"X-Ingress-Path": "/api/hassio_ingress/Abc_12-xyz"})
check("ingress: the Supervisor's prefix is used",
      json.loads(body)["urls"]["original"] == "/api/hassio_ingress/Abc_12-xyz/img/%s/original.jpg" % cur_in["id"],
      json.loads(body)["urls"])
for bad in ("//evil.example", "evil", "/a b", "/a\tb", "http://evil.example"):
    st, body, _ = req(port_in, "GET", "/current.json", {"X-Ingress-Path": bad})
    check("ingress: prefix %r is not a path and is ignored" % bad,
          json.loads(body)["urls"]["original"].startswith("/img/"), json.loads(body)["urls"])

# ---------------- every response says what it is ----------------
for path in ("/current.json", "/", "/healthz", "/current/clean/640x360.jpg", "/nope"):
    st, _, h = req(port, "GET", path)
    check("nosniff on %s" % path, h.get("X-Content-Type-Options") == "nosniff", (st, h))

# ---------------- one decode per master, however many ask at once ----------------
lib = eng.lib
iid = sorted(lib.items)[1]
for fn in os.listdir(os.path.join(lib.data, "masters")):
    if fn.startswith(iid + "-"):
        os.remove(os.path.join(lib.data, "masters", fn))
decodes, errors = [], []
real_open = library.open_image


def slow_open(path):
    decodes.append(path)
    time.sleep(0.5)
    return real_open(path)


library.open_image = slow_open
try:
    def build():
        try:
            lib.ensure_master(iid)
        except Exception as e:  # noqa: BLE001 - the test records whatever went wrong
            errors.append(repr(e))
    ts = [threading.Thread(target=build) for _ in range(4)]
    for t in ts: t.start()
    for t in ts: t.join()
finally:
    library.open_image = real_open
with Image.open(lib.master_path(iid)) as im:
    im.load()
    good = im.size == (1600, 900)
check("ensure_master: four concurrent callers decode once and all succeed",
      len(decodes) == 1 and not errors and good, (len(decodes), errors))

# ---------------- a picture replaced in place gets a new rev (its render URLs are immutable) ----------------
path = lib.items[iid]["path"]
rev1 = lib.display(iid)["rev"]
data1 = eng.render_kind(iid, rev1, "clean", 320, 320)
Image.new("RGB", (1600, 900), (10, 240, 10)).save(path, quality=95)
os.utime(path, (time.time() + 5, time.time() + 5))  # the hash cache keys on size and mtime
lib.rescan()
rev2 = lib.display(iid)["rev"]
check("replaced picture: rev changes", rev1 != rev2, (rev1, rev2))
data2 = eng.render_kind(iid, rev2, "clean", 320, 320)
with Image.open(io.BytesIO(data2)) as im:
    px = im.convert("RGB").getpixel((160, 160))
check("replaced picture: the render follows", data2 != data1 and px[1] > 200 > px[0], px)

# ---------------- a hand-edited sidecar with the wrong types degrades, never 500s ----------------
other = sorted(lib.items)[0]
library.write_json(lib.sidecar_path(other), {"title": 123, "place": ["x"], "credit": None, "blurb": {"a": 1},
                                             "caption_failed_at": "yesterday", "caption_attempts": "many",
                                             "caption_error": 7, "sha256": lib.items[other]["sha256"]})
lib.load_sidecars()
try:
    view = eng.library_view()
    page = engine.gallery_html(eng, control=True, admin=True)
    label = eng.render_kind(other, lib.display(other)["rev"], "label", 640, 360)
    queue = eng.queues()[0]  # waiting for a caption; with no tags yet it may wait for tags, which is fine
    check("bad sidecar types: library, page, label and queue still work",
          view and "123" in page and label[:2] == b"\xff\xd8" and other not in queue,
          (len(page), label[:2], queue))
    st, body, _ = req(port, "GET", "/library.json")
    check("bad sidecar types: /library.json answers 200 with text fields", st == 200
          and all(isinstance(i[k], str) for i in json.loads(body)["items"] for k in ("title", "place", "credit", "blurb", "error")),
          (st, body[:300]))
except Exception as e:  # noqa: BLE001
    check("bad sidecar types: library, page, label and queue still work", False, repr(e))

# ---------------- what a captioner may answer ----------------
cap = caption.Captioner({"caption": {"min_words": 30, "max_words": 45}, "captioners": [{"name": "t", "url": "http://x", "model": "m"}]})
meta = {}
good_blurb = " ".join(["word"] * 35)
check("check: a normal blurb passes", cap.check(meta, "Quiet Field", good_blurb)[0] == [])
long_blurb = " ".join(["word"] * 34 + ["x" * 200_000])
check("check: a 200 KB word is refused", any("long" in p for p in cap.check(meta, "Quiet Field", long_blurb)[0]))
check("check: a 200 KB title is refused", any("title" in p for p in cap.check(meta, "x" * 200_000, good_blurb)[0]))


class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = lambda req, timeout=None: FakeResp(b'{"choices": [{"message": {"content": "' + b"x" * 6_000_000 + b'"}}]}')
try:
    cap.post({"name": "t", "url": "http://x", "model": "m"}, b"{}")
    check("post: a 6 MB reply is refused", False, "no error")
except ValueError as e:
    check("post: a 6 MB reply is refused", "large" in str(e), e)
except Exception as e:  # noqa: BLE001
    check("post: a 6 MB reply is refused", False, repr(e))
finally:
    urllib.request.urlopen = real_urlopen

# ---------------- remote sources are http(s) only ----------------
for url in ("file:///etc/hostname", "file://localhost/etc/hostname", "ftp://example.invalid/x", "data:text/plain,hi"):
    try:
        library.http_get(url, 2)
        check("http_get refuses %s" % url, False, "fetched")
    except ValueError as e:
        check("http_get refuses %s" % url, "http" in str(e), e)
    except Exception as e:  # noqa: BLE001
        check("http_get refuses %s" % url, False, repr(e))

# ---------------- a picture removed from the library while a request is on its way ----------------
# The route checks `iid in lib.items`, then a rescan swaps the dict before the render or the original
# reads it: a 404, not a KeyError 500.
class LyingItems(dict):
    def __contains__(self, k): return True  # the check passed before the swap


eng = make_engine("gone")
port = serve(eng)
gone = eng.current()["id"]
real_items = eng.lib.items
eng.lib.items = LyingItems({k: v for k, v in real_items.items() if k != gone})
st, body, _ = req(port, "GET", "/img/%s/original.jpg" % gone)
check("gone mid-request: original.jpg is a 404", st == 404, (st, body[:80]))
rev = eng.lib.display(gone)["rev"]
st, body, _ = req(port, "GET", "/img/%s/%s/clean/640x360.jpg" % (gone, rev))
check("gone mid-request: a render is a 404", st == 404, (st, body[:80]))
st, body, _ = req(port, "GET", "/img/%s/%s/span.jpg?layout=1920x1080%%2B0%%2B0" % (gone, rev))
check("gone mid-request: a span is a 404", st == 404, (st, body[:80]))
eng.lib.items = real_items

# ---------------- render budget on the published port ----------------
# Any LAN client may ask for a size: one picture or a span canvas is held to `size_limits.max_pixels`
# (40 MP: 7680x4320 and three 4K screens in a row fit), and renders waiting for a slot are capped
# (`render_queue`), the rest answered 503 with Retry-After instead of piling up threads and memory.
eng = make_engine("budget")
port = serve(eng)
iid = eng.current()["id"]
rev = eng.lib.display(iid)["rev"]
st, body, _ = req(port, "GET", "/img/%s/%s/clean/7680x7680.jpg" % (iid, rev))
check("budget: 7680x7680 (59 MP) is refused", st == 400 and b"megapixels" in body, (st, body[:80]))
st, body, _ = req(port, "GET", "/img/%s/%s/clean/7680x4320.jpg" % (iid, rev))
check("budget: 7680x4320 (8K) still renders", st == 200, (st, body[:80]))
st, body, _ = req(port, "GET", "/img/%s/%s/clean/5000x5000.jpg?pad=1.3" % (iid, rev))
check("budget: a pad that takes it past the budget is refused", st == 400 and b"megapixels" in body, (st, body[:80]))
st, body, _ = req(port, "GET", "/img/%s/%s/span.jpg?layout=1920x1080%%2B0%%2B0,1920x1080%%2B10000%%2B6900" % (iid, rev))
check("budget: a span canvas stretched by a far-away screen (95 MP) is refused",
      st == 400 and b"megapixels" in body, (st, body[:80]))
st, body, _ = req(port, "GET", "/img/%s/%s/span.jpg?layout=3840x2160%%2B0%%2B0,3840x2160%%2B3840%%2B0,3840x2160%%2B7680%%2B0p"
                  % (iid, rev))
check("budget: three 4K screens in a row still span", st == 200, (st, body[:80]))

release = threading.Event()
eng.cfg["render_queue"] = 1
holder = threading.Thread(target=lambda: eng.cached("hold/slot.jpg", lambda: (release.wait(20), b"x")[1]), daemon=True)
holder.start()
for _ in range(100):
    if eng.render_waiting:
        break
    time.sleep(0.02)
st, body, h = req(port, "GET", "/img/%s/%s/clean/800x600.jpg" % (iid, rev))
check("budget: a render past the queue answers 503 with Retry-After", st == 503 and h.get("Retry-After"), (st, body[:200], h))
st, _, _ = req(port, "GET", "/img/%s/%s/clean/7680x4320.jpg" % (iid, rev))
check("budget: a cached size is still served while the queue is full", st == 200, st)
release.set(); holder.join(5)
st, _, _ = req(port, "GET", "/img/%s/%s/clean/800x600.jpg" % (iid, rev))
check("budget: the queue frees up again", st == 200 and eng.render_waiting == 0, (st, eng.render_waiting))

# ---------------- screens list churn ----------------
# Once the list is full, a client cycling new ?screen= names would replace one screen and write
# screens.json on every request. A full list now takes at most one new name a minute.
eng = make_engine("screens")
writes = []
real_save = eng.save_screens
eng.save_screens = lambda: (writes.append(1), real_save())
for i in range(engine.MAX_SCREENS):
    eng.saw_screen("screen-%d" % i, "192.168.0.%d" % (i + 2), "test")
n = len(writes)
eng.saw_screen("newcomer-1", "192.168.0.99", "test")
check("screens: a full list takes one new name", "newcomer-1" in eng.screens and len(writes) == n + 1, len(writes) - n)
for i in range(50):
    eng.saw_screen("churn-%d" % i, "192.168.0.99", "test")
check("screens: more new names within the minute are not added and not written",
      not any(k.startswith("churn-") for k in eng.screens) and len(writes) == n + 1
      and len(eng.screens) == engine.MAX_SCREENS, (len(writes) - n, len(eng.screens)))
eng.saw_screen("screen-5", "192.168.0.7", "test")
check("screens: a known screen is still seen", "screen-5" in eng.screens and eng.screens["screen-5"]["ip"] == "192.168.0.7")
eng.screens_admitted_at -= 61
eng.saw_screen("newcomer-2", "192.168.0.99", "test")
check("screens: a minute later a new name gets in again", "newcomer-2" in eng.screens and len(writes) == n + 2, len(writes) - n)

# ---------------- original.jpg: streamed from disk, GPS left out ----------------
# A JPEG original was read whole into memory per request, outside any limit, with its EXIF (GPS
# included) served to any client. Now: streamed in chunks under a small limit of its own; a JPEG
# that carries a GPS position is served from a copy without it (lossless: the image data and the
# rest of the EXIF, orientation included, stay as they are).
eng = make_engine("orig")
imgs = os.path.join(S, "imgs-orig")
ex = Image.Exif()
ex[0x0110] = "TestCam"
ex[0x0112] = 6
ex[0x8825] = {1: "N", 2: (51.0, 54.0, 0.0), 3: "W", 4: (8.0, 28.0, 0.0)}
Image.new("RGB", (1600, 900), (90, 120, 30)).save(os.path.join(imgs, "phone.jpg"), quality=90, exif=ex)
eng.lib.rescan()
port = serve(eng)
by_path = {os.path.basename(it["path"]): iid for iid, it in eng.lib.items.items()}
st, body, h = req(port, "GET", "/img/%s/original.jpg" % by_path["phone.jpg"])
got = Image.open(io.BytesIO(body))
gx = got.getexif()
check("original: GPS left out", st == 200 and not dict(gx.get_ifd(0x8825)) and 0x8825 not in gx, (st, dict(gx)))
check("original: the rest of the EXIF kept (model, orientation)", gx.get(0x0110) == "TestCam" and gx.get(0x0112) == 6, dict(gx))
src = open(os.path.join(imgs, "phone.jpg"), "rb").read()
check("original: the picture itself untouched (same scan data)", body[body.index(b"\xff\xda"):] == src[src.index(b"\xff\xda"):]
      and int(h.get("Content-Length", -1)) == len(body))
plain = by_path["pic-0.jpg"]
st, body, h = req(port, "GET", "/img/%s/original.jpg" % plain)
check("original: a JPEG without GPS is the file as it is", st == 200 and body == open(eng.lib.items[plain]["path"], "rb").read()
      and int(h.get("Content-Length", -1)) == len(body), (st, len(body)))
st, body, h = req(port, "HEAD", "/img/%s/original.jpg" % plain)
check("original: HEAD has the length and no body", st == 200 and body == b"" and int(h.get("Content-Length", 0)) > 0, (st, h))
held = [eng.original_sem.acquire() for _ in range(engine.ORIGINAL_STREAMS)]
st, body, h = req(port, "GET", "/img/%s/original.jpg" % plain)
check("original: past the streams limit, 503 with Retry-After", st == 503 and h.get("Retry-After"), (st, body[:80]))
for _ in held:
    eng.original_sem.release()

# ---------------- remote sources cannot be bent into a proxy for the internal network ----------------
# A manifest or daily host (or an art API) answering 302 to the Supervisor, Home Assistant or any other
# private address would have the add-on fetch it and serve the result. Redirects may stay on the host
# you configured or go to public addresses; an address an art API hands out must be public (or one of
# the configured hosts).
from http.server import BaseHTTPRequestHandler  # noqa: E402


class Redirector(BaseHTTPRequestHandler):
    def do_GET(self):
        port = self.server.server_address[1]
        to = {"/to-other": "http://localhost:%d/secret" % port, "/to-same": "/secret", "/to-file": "file:///etc/hostname",
              "/to-meta": "http://169.254.169.254/latest/meta-data/"}.get(self.path)
        if to:
            self.send_response(302); self.send_header("Location", to); self.send_header("Content-Length", "0"); self.end_headers()
            return
        body = b"secret" if self.path == "/secret" else b"ok"
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a): pass


rsrv = ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
rsrv.daemon_threads = True
threading.Thread(target=rsrv.serve_forever, daemon=True).start()
R = "http://127.0.0.1:%d" % rsrv.server_address[1]


def fetch(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return e


check("guard: a configured private host is fetched", fetch(lambda: library.http_get(R + "/plain", 5)) == b"ok")
check("guard: a redirect on the same host is followed", fetch(lambda: library.http_get(R + "/to-same", 5)) == b"secret")
for path in ("/to-other", "/to-meta", "/to-file"):
    got = fetch(lambda: library.http_get(R + path, 5))
    check("guard: http_get refuses a redirect %s" % path, isinstance(got, Exception) and not isinstance(got, bytes)
          and ("refused" in str(got) or "not allowed" in str(got)), repr(got))  # urllib itself refuses file:
cl = artsources.Client(pause=0, trusted=["127.0.0.1"])
check("guard: art client fetches its configured host", fetch(lambda: cl.get(R + "/plain")) == b"ok")
got = fetch(lambda: cl.get("http://localhost:%d/secret" % rsrv.server_address[1]))
check("guard: art client refuses a private address it was handed", isinstance(got, ValueError) and "refused" in str(got), repr(got))
got = fetch(lambda: cl.get(R + "/to-other"))
check("guard: art client refuses a redirect to a private address", isinstance(got, ValueError) and "refused" in str(got), repr(got))
check("guard: public and private addresses told apart",
      [netguard.host_is_public(h) for h in ("8.8.8.8", "2606:4700:4700::1111", "10.1.2.3", "172.30.32.1", "192.168.1.5",
                                            "100.64.1.1", "169.254.169.254", "127.0.0.1", "::1", "::ffff:127.0.0.1",
                                            "0.0.0.0", "fd00::1", "fe80::1")]
      == [True, True] + [False] * 11)
# DNS rebinding: a name that resolves public for the check and private for the connection. The
# address actually connected to is checked too, on every hop.
real_gai = socket.getaddrinfo
answers = []


def rebinding_gai(host, *a, **kw):
    if host == "rebind.test":
        answers.append(1)
        ip = "93.184.216.34" if len(answers) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, a[0] if a and a[0] else 0))]
    return real_gai(host, *a, **kw)


RP = rsrv.server_address[1]
socket.getaddrinfo = rebinding_gai
try:
    got = fetch(lambda: artsources.Client(pause=0, trusted=["127.0.0.1"]).get("http://rebind.test:%d/secret" % RP))
    check("guard: a name that rebinds to a private address after the check is refused",
          isinstance(got, ValueError) and "refused" in str(got) and len(answers) >= 2, (repr(got), len(answers)))
    answers[:] = [1]  # resolves private from the start: a configured host may be on your network
    got = fetch(lambda: library.http_get("http://rebind.test:%d/secret" % RP, 5))
    check("guard: a configured name is trusted wherever it resolves", got == b"secret", repr(got))
finally:
    socket.getaddrinfo = real_gai
rsrv.shutdown(); rsrv.server_close()

# ---------------- Content-Security-Policy on the panel ----------------
# Escaping is complete today; a CSP makes a future slip harmless: only the page's own <script> and
# <style> (with this response's nonce) run, images and fetches go to this origin only.
eng = make_engine("csp")
port = serve(eng)
st, body, h = req(port, "GET", "/")
csp = h.get("Content-Security-Policy", "")
m = re.search(r"script-src 'nonce-([A-Za-z0-9_-]{16,})'", csp)
page = body.decode()
tags = re.findall(r"<(script|style)\b([^>]*)>", page)
check("csp: the panel sends a policy with a script nonce", st == 200 and m and "default-src 'none'" in csp, csp)
check("csp: every <script> and <style> carries the nonce", m and tags and all('nonce="%s"' % m.group(1) in a for _t, a in tags),
      tags)
check("csp: no inline event handlers on the page", not re.search(r"<[^>]+\son[a-z]+\s*=", page))
check("csp: images and fetches from this origin only", "img-src 'self'" in csp and "connect-src 'self'" in csp
      and "base-uri 'none'" in csp, csp)
h2 = req(port, "GET", "/")[2]
check("csp: a new nonce for every response", h2.get("Content-Security-Policy") != csp)
check("csp: not on JSON or images (only the page has inline code)", "Content-Security-Policy" not in req(port, "GET", "/current.json")[2])

print("\n%d passed, %d failed" % (ok, fail))
if not fail:
    shutil.rmtree(S, ignore_errors=True)
sys.exit(1 if fail else 0)
