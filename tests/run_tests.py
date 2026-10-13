"""Integration tests for the Matchy engine: rotation, the panel controls, access tiers, request
bodies, and images too large to decode. Runs the real engine as a subprocess against generated
images and a fake OpenAI-compatible captioner (tests/fakecap.py).

    uv run --with pillow python tests/run_tests.py      # or any python3 with Pillow

MATCHY_TEST_PORT moves the six ports it uses (default 18160..18170). Takes about three minutes.
"""
import http.client, json, os, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "matchy", "app", "engine.py")
S = tempfile.mkdtemp(prefix="matchy-test-")
IMGS = os.path.join(S, "imgs")
BASE = int(os.environ.get("MATCHY_TEST_PORT", "18160"))
FAIL_FLAG = os.path.join(S, "fail")


def make_images():
    import random
    from PIL import Image, ImageDraw
    os.makedirs(IMGS)
    os.makedirs(os.path.join(S, "imgs-big"))
    for i, c in enumerate([(200, 60, 60), (60, 160, 90), (50, 80, 200), (200, 180, 40)]):
        im = Image.new("RGB", (1600, 900), c)
        d = ImageDraw.Draw(im)
        for _ in range(400):
            d.point((random.randrange(1600), random.randrange(900)), fill=(255, 255, 255))
        im.save(os.path.join(IMGS, "test-%d.jpg" % i), quality=95)
        shutil.copy(os.path.join(IMGS, "test-%d.jpg" % i), os.path.join(S, "imgs-big"))
    # 56 MP: over the engine's 50 MP cap, cheap to write as a flat PNG
    Image.new("RGB", (8000, 7000), (90, 30, 120)).save(os.path.join(S, "imgs-big", "huge.png"))


make_images()
ok = fail = 0

def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("PASS", name)
    else: fail += 1; print("FAIL", name, extra)

def req(port, method, path, body=None, ctype="application/json", raw=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": ctype} if ctype and data is not None else {}
    c.request(method, path, body=data, headers=h)
    r = c.getresponse(); txt = r.read().decode()
    hdrs = dict(r.getheaders()); c.close()
    try: j = json.loads(txt)
    except ValueError: j = None
    return r.status, j, txt, hdrs

def start(name, cfg):
    data = os.path.join(S, "data-" + name)
    if cfg.pop("_fresh", True): shutil.rmtree(data, ignore_errors=True)
    os.makedirs(data, exist_ok=True)
    cfg.update(bind="127.0.0.1", data_dir=data, folders=[{"path": cfg.pop("_imgs", IMGS)}])
    p = os.path.join(S, "cfg-%s.json" % name); json.dump(cfg, open(p, "w"))
    log = open(os.path.join(S, "log-%s.txt" % name), "a")
    proc = subprocess.Popen([sys.executable, ENGINE], env=dict(os.environ, WE_CONFIG=p), stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            if req(cfg["port"], "GET", "/current.json")[0] == 200: break
        except OSError: pass
        time.sleep(0.2)
    return proc

def cur(port): return req(port, "GET", "/current.json")[1]
def lib(port): return req(port, "GET", "/library.json")[1]

# ---------------- A: the sidebar panel (localhost treated as the Supervisor) ----------------
fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fakecap.py"), str(BASE + 10), FAIL_FLAG])
A = BASE
cfgA = {"port": A, "ingress_peers": ["127.0.0.0/8"], "rotation": {"min_minutes": 15, "max_minutes": 20},
        "captioners": [{"name": "fake", "url": "http://127.0.0.1:%d/v1/chat/completions" % (BASE + 10), "model": "x", "timeout": 10}],
        "caption": {"enabled": True, "min_words": 30, "max_words": 45, "spelling": "British"}}
pa = start("A", dict(cfgA))
try:
    # captioner should caption all 4 within ~30 s
    for _ in range(60):
        if all(i["status"] == "captioned" for i in lib(A)["items"]): break
        time.sleep(1)
    L = lib(A); check("A all captioned by fake", all(i["status"] == "captioned" for i in L["items"]), [i["status"] for i in L["items"]])
    check("A library has new fields", {"hidden", "recaption_queued"} <= set(L["items"][0]) and "rotation" in L and "captioner" in L)
    c0 = cur(A); check("A current has paused=false + next_change", c0["paused"] is False and c0["next_change"])
    st, _, txt, _ = req(A, "GET", "/"); check("A page renders", st == 200 and "Pause rotation" in txt and "disabled title" not in txt, txt[:200])

    # pause / resume
    st, j, *_ = req(A, "POST", "/api/pause", {}); check("A pause 200", st == 200 and j["current"]["paused"] is True and j["current"]["next_change"] is None, j)
    check("A page shows paused", "Resume rotation" in req(A, "GET", "/")[2])
    st, j, *_ = req(A, "POST", "/api/next", {}); check("A next while paused stays paused", st == 200 and j["paused"] is True and j["id"] != c0["id"], j)
    st, j, *_ = req(A, "POST", "/api/resume", {}); check("A resume", st == 200 and j["current"]["paused"] is False and j["current"]["next_change"], j)

    # show now
    ids = [i["id"] for i in lib(A)["items"]]
    target = [i for i in ids if i != cur(A)["id"]][0]
    st, j, *_ = req(A, "POST", "/api/show", {"id": target}); check("A show", st == 200 and j["current"]["id"] == target, j)
    st, *_ = req(A, "POST", "/api/show", {"id": "nope"}); check("A show unknown 404", st == 404)
    st, *_ = req(A, "POST", "/api/show", {}); check("A show no id 400", st == 400)

    # caption edit
    old_rev = cur(A)["rev"]
    st, j, txt, _ = req(A, "POST", "/api/caption", {"id": target, "title": "My Title", "place": "Cork", "credit": "me",
                                                     "blurb": "Line one\n  line two <b>bold</b>"})
    c = cur(A); it = [i for i in lib(A)["items"] if i["id"] == target][0]
    check("A caption edit", st == 200 and c["rev"] != old_rev and c["blurb"] == "Line one line two <b>bold</b>" and it["edited"], (st, txt, c))
    check("A edited caption escaped in page", "&lt;b&gt;bold" in req(A, "GET", "/")[2] and "<b>bold" not in req(A, "GET", "/")[2].split('id="lib"')[0])
    st, *_ = req(A, "POST", "/api/caption", {"id": target, "blurb": ""}); check("A caption empty blurb 400", st == 400)
    st, *_ = req(A, "POST", "/api/caption", {"id": target, "blurb": "x" * 601}); check("A caption too long 400", st == 400)
    st, *_ = req(A, "POST", "/api/caption", {"id": target, "blurb": 5}); check("A caption non-text 400", st == 400)
    # old URL redirects to the new rev
    st, _, _, h = req(A, "GET", "/img/%s/%s/label/640x360.jpg" % (target, old_rev)); check("A stale rev 302", st == 302)

    # re-caption: failing captioner keeps the hand edit
    open(FAIL_FLAG, "w").close()
    st, *_ = req(A, "POST", "/api/recaption", {"id": target}); check("A recaption queued", st == 200 and [i for i in lib(A)["items"] if i["id"] == target][0]["recaption_queued"])
    for _ in range(40):
        it = [i for i in lib(A)["items"] if i["id"] == target][0]
        if not it["recaption_queued"]: break
        time.sleep(0.5)
    check("A failed recaption keeps hand caption", not it["recaption_queued"] and it["blurb"].startswith("Line one") and it["edited"], it)
    os.remove(FAIL_FLAG)
    req(A, "POST", "/api/recaption", {"id": target})
    for _ in range(40):
        it = [i for i in lib(A)["items"] if i["id"] == target][0]
        if not it["recaption_queued"]: break
        time.sleep(0.5)
    check("A recaption replaces hand caption", not it["edited"] and it["blurb"].startswith("A field"), it)

    # hide
    c = cur(A)["id"]
    st, j, *_ = req(A, "POST", "/api/hide", {"id": c}); check("A hide current rotates away", st == 200 and j["current"]["id"] != c, j)
    others = [i for i in ids if i != c]
    for i in others[:-1]: req(A, "POST", "/api/hide", {"id": i})
    st, _, txt, _ = req(A, "POST", "/api/hide", {"id": others[-1]}); check("A hide last 409", st == 409, (st, txt))
    check("A only visible one on screen", cur(A)["id"] == others[-1])
    L = lib(A); check("A hidden flags", sum(i["hidden"] for i in L["items"]) == 3)
    req(A, "POST", "/api/next", {}); check("A next with one visible keeps it", cur(A)["id"] == others[-1])
    for i in ids: req(A, "POST", "/api/unhide", {"id": i})
    check("A unhide all", not any(i["hidden"] for i in lib(A)["items"]))
    st, *_ = req(A, "POST", "/api/unhide", {"id": "ghost"}); check("A unhide unknown is fine", st == 200)

    # rotation window
    st, j, *_ = req(A, "POST", "/api/rotation", {"min_minutes": 2, "max_minutes": 3}); L = lib(A)
    check("A rotation set", st == 200 and L["rotation"]["source"] == "panel" and L["rotation"]["min_minutes"] == 2, L["rotation"])
    for bad in ({"min_minutes": 5, "max_minutes": 3}, {"min_minutes": 0, "max_minutes": 3}, {"min_minutes": 1.5, "max_minutes": 3},
                {"min_minutes": True, "max_minutes": 3}, {}):
        st, *_ = req(A, "POST", "/api/rotation", bad); check("A rotation bad %s 400" % bad, st == 400)
    check("A page shows panel window", "Use the options" in req(A, "GET", "/")[2])

    # transport rules
    st, *_ = req(A, "POST", "/api/pause", raw=b"{}", ctype="text/plain"); check("A text/plain 415", st == 415)
    st, *_ = req(A, "POST", "/api/pause", raw=b"[1]"); check("A non-object 400", st == 400)
    st, *_ = req(A, "POST", "/api/pause", raw=b"{nope"); check("A bad json 400", st == 400)
    st, _, _, h = req(A, "OPTIONS", "/api/hide"); check("A OPTIONS /api no CORS", st == 204 and "Access-Control-Allow-Origin" not in h, h)
    st, _, _, h = req(A, "OPTIONS", "/current.json"); check("A OPTIONS elsewhere keeps CORS", "Access-Control-Allow-Origin" in h)
    # oversized body: 413, connection closed, next request on a new socket works
    s = socket.create_connection(("127.0.0.1", A)); big = b"x" * 70000
    s.sendall(b"POST /api/pause HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(big) + big[:1000])
    resp = s.recv(4096).decode(errors="replace"); check("A oversized 413", resp.startswith("HTTP/1.1 413"), resp[:80])
    s.close()
    # keep-alive: two posts with bodies on one connection both answered
    c = http.client.HTTPConnection("127.0.0.1", A, timeout=5)
    c.request("POST", "/api/pause", body=b'{"pad": "' + b"y" * 5000 + b'"}', headers={"Content-Type": "application/json"}); r1 = c.getresponse(); r1.read()
    c.request("POST", "/api/resume", body=b"{}", headers={"Content-Type": "application/json"}); r2 = c.getresponse(); r2.read()
    check("A keep-alive two bodies", r1.status == 200 and r2.status == 200 and cur(A)["paused"] is False)
finally:
    pa.terminate(); pa.wait()

# restart A with the same options: panel window survives
pa = start("A", dict(cfgA, _fresh=False))
try:
    L = lib(A); check("A restart keeps panel window", L["rotation"]["source"] == "panel" and L["rotation"]["max_minutes"] == 3, L["rotation"])
finally:
    pa.terminate(); pa.wait()
# restart A with changed options: options win
pa = start("A", dict(cfgA, _fresh=False, rotation={"min_minutes": 30, "max_minutes": 40}))
try:
    L = lib(A); check("A options change wins", L["rotation"]["source"] == "options" and L["rotation"]["min_minutes"] == 30, L["rotation"])
    req(A, "POST", "/api/rotation", {"min_minutes": 5, "max_minutes": 9})
    req(A, "POST", "/api/rotation", {"reset": True}); L = lib(A)
    check("A reset to options", L["rotation"]["source"] == "options" and L["rotation"]["min_minutes"] == 30, L["rotation"])
finally:
    pa.terminate(); pa.wait(); fake.terminate()

# ---------------- B: direct port, no captioner, captions off ----------------
B = BASE + 2
pb = start("B", {"port": B, "rotation": {"min_minutes": 15, "max_minutes": 20}, "captioners": [], "caption": {"enabled": False}})
try:
    first = cur(B)["id"]; time.sleep(12); c = cur(B)
    check("B no 5-second placeholder churn", c["id"] == first, (first, c["id"]))
    st, _, txt, _ = req(B, "GET", "/")
    check("B page: admin buttons disabled", 'data-act="hide" disabled' in txt and 'data-act="pause">' in txt, "")
    check("B page: captioner off line", "Captioner: <b>off</b>" in txt)
    st, *_ = req(B, "POST", "/api/pause", {}); check("B pause from LAN ok", st == 200)
    st, *_ = req(B, "POST", "/api/resume", {}); check("B resume from LAN ok", st == 200)
    for path, body in (("/api/hide", {"id": first}), ("/api/caption", {"id": first, "blurb": "x"}),
                       ("/api/recaption", {"id": first}), ("/api/rotation", {"reset": True}), ("/api/unhide", {"id": first})):
        st, *_ = req(B, "POST", path, body); check("B %s refused off-panel 403" % path, st == 403, st)
    st, *_ = req(B, "POST", "/api/next"); check("B legacy bodyless /api/next still ok", st == 200)
finally:
    pb.terminate(); pb.wait()

# ---------------- C: panel, captions on but no captioner -> recaption 409 ----------------
C = BASE + 4
pc = start("C", {"port": C, "ingress_peers": ["127.0.0.0/8"], "captioners": [], "caption": {"enabled": True}})
try:
    i0 = lib(C)["items"][0]["id"]
    st, _, txt, _ = req(C, "POST", "/api/recaption", {"id": i0}); check("C recaption without captioner 409", st == 409, txt)
    check("C page: not set up line", "Captioner: <b>not set up</b>" in req(C, "GET", "/")[2])
    # show an uncaptioned one by hand, after hand-captioning another: no placeholder swap
    ids = [i["id"] for i in lib(C)["items"]]
    req(C, "POST", "/api/caption", {"id": ids[0], "title": "T", "blurb": "hand written"})
    req(C, "POST", "/api/show", {"id": ids[1]}); time.sleep(8)
    check("C picked uncaptioned is held", cur(C)["id"] == ids[1], cur(C)["id"])
    req(C, "POST", "/api/next", {}); time.sleep(1)
    check("C rotation prefers captioned", cur(C)["id"] == ids[0], cur(C)["id"])
finally:
    pc.terminate(); pc.wait()

# ---------------- D: an image too large to decode stays out of rotation ----------------
D = BASE + 6
pd = start("D", {"port": D, "ingress_peers": ["127.0.0.0/8"], "captioners": [], "caption": {"enabled": False},
                 "_imgs": os.path.join(S, "imgs-big")})
try:
    for _ in range(60):
        h = [i for i in lib(D)["items"] if i["id"] == "huge"]
        if h and h[0]["error"]: break
        time.sleep(1)
    check("D huge shows its reason", h and "too large" in h[0]["error"], h)
    seen = set()
    for _ in range(12):
        req(D, "POST", "/api/next", {}); seen.add(cur(D)["id"])
    check("D huge never rotates in", "huge" not in seen and len(seen) >= 2, seen)
    st, _, txt, _ = req(D, "POST", "/api/show", {"id": "huge"}); check("D show huge 409", st == 409, (st, txt))
    for i in [x for x in seen][:3]: req(D, "POST", "/api/hide", {"id": i})
    left = [i["id"] for i in lib(D)["items"] if not i["hidden"] and i["id"] != "huge"]
    st, *_ = req(D, "POST", "/api/hide", {"id": left[0]}); check("D cannot hide last decodable one", st == 409 and len(left) == 1, (st, left))
finally:
    pd.terminate(); pd.wait()

print("\n%d passed, %d failed (logs in %s)" % (ok, fail, S))
if not fail:
    shutil.rmtree(S, ignore_errors=True)
sys.exit(1 if fail else 0)
