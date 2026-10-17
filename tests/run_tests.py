"""Integration tests for the Matchy engine: rotation, the panel controls, access tiers, request
bodies, and images too large to decode. Runs the real engine as a subprocess against generated
images and a fake OpenAI-compatible captioner (tests/fakecap.py).

    uv run --with pillow python tests/run_tests.py      # or any python3 with Pillow

MATCHY_TEST_PORT moves the ports it uses (default 18160..18174). Takes about three minutes.
"""
import http.client, json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.parse

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
sys.path.insert(0, os.path.dirname(ENGINE))
import engine as engine_mod  # noqa: E402  (the pure helpers only; the server runs as a subprocess)

def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("PASS", name)
    else: fail += 1; print("FAIL", name, extra)

def utc_hm(t):
    return time.strftime("%H:%M", time.gmtime(t))

def req(port, method, path, body=None, ctype="application/json", raw=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": ctype} if ctype and data is not None else {}
    c.request(method, path, body=data, headers=h)
    r = c.getresponse(); txt = r.read().decode(errors="replace")
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

# ---------------- sun_events (pure): Cork at the solstices, Svalbard in midnight sun ----------------
import datetime as _dt
r = engine_mod.sun_events(51.8985, -8.4756, _dt.date(2026, 6, 21))
check("sun: Cork midsummer", "03:50" <= utc_hm(r[0]) <= "04:20" and "20:50" <= utc_hm(r[1]) <= "21:20", [utc_hm(t) for t in r])
r = engine_mod.sun_events(51.8985, -8.4756, _dt.date(2026, 12, 21))
check("sun: Cork midwinter", "08:20" <= utc_hm(r[0]) <= "08:50" and "16:10" <= utc_hm(r[1]) <= "16:40", [utc_hm(t) for t in r])
check("sun: midnight sun has none", engine_mod.sun_events(78.2, 15.6, _dt.date(2026, 6, 21)) is None)

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

    # How often: Day & night, then a slider stop, then back to the options
    st, *_ = req(A, "POST", "/api/rotation", {"mode": "daynight"}); L = lib(A); c = cur(A)
    nc = time.mktime(time.strptime(c["next_change"][:19], "%Y-%m-%dT%H:%M:%S"))
    check("A day & night", st == 200 and L["rotation"]["mode"] == "daynight" and L["rotation"].get("next_sun")
          and 0 < nc - time.time() < 86400, (st, L["rotation"], c["next_change"]))
    page = req(A, "GET", "/")[2]
    check("A page: slider on day & night", 'value="8"' in page and "07:00 and 19:00" in page)
    st, *_ = req(A, "POST", "/api/rotation", {"mode": "weekly"}); check("A unknown mode 400", st == 400, st)
    st, *_ = req(A, "POST", "/api/rotation", {"min_minutes": 60, "max_minutes": 60}); L = lib(A)
    check("A slider stop leaves day & night", st == 200 and L["rotation"]["mode"] == "minutes"
          and L["rotation"]["min_minutes"] == 60, L["rotation"])
    check("A page: slider on every hour", 'value="3"' in req(A, "GET", "/")[2])
    req(A, "POST", "/api/rotation", {"reset": True})
    check("A page: options window labelled custom", "Custom: 30–40 minutes" in req(A, "GET", "/")[2])

    # Screens
    check("A page: no screens yet", "No screens yet" in req(A, "GET", "/")[2])
    st, j, *_ = req(A, "GET", "/current.json?screen=lap-1")
    check("A screen: follow and screens_off", st == 200 and j["follow"] is True and j["screens_off"] == [], j)
    check("A screen: no follow without a name", "follow" not in cur(A) and cur(A)["screens_off"] == [])
    req(A, "GET", "/current.json?screen=%3Cbad%3E")
    ids_seen = [x["id"] for x in req(A, "GET", "/screens.json")[1]["screens"]]
    check("A screen: listed, bad name ignored", ids_seen == ["lap-1"], ids_seen)
    c = cur(A)
    st, *_ = req(A, "GET", "/img/%s/%s/clean/640x360.jpg?screen=tab-1" % (c["id"], c["rev"]))
    sc = {x["id"]: x for x in req(A, "GET", "/screens.json")[1]["screens"]}
    check("A screen: image fetch records the picture", st == 200 and sc.get("tab-1", {}).get("image") == c["id"]
          and sc["lap-1"]["image"] == "", sc)
    st, j, *_ = req(A, "POST", "/api/screen", {"id": "lap-1", "follow": False})
    st2, j2, *_ = req(A, "GET", "/current.json?screen=lap-1")
    check("A screen: turned off", st == 200 and j2["follow"] is False and j2["screens_off"] == ["lap-1"], (st, j2))
    page = req(A, "GET", "/")[2]
    check("A page: screens box", "Turn on" in page and "Turn off" in page and "tag off" in page, page[-3000:])
    st, *_ = req(A, "POST", "/api/screen", {"id": "nope", "follow": False}); check("A screen: unknown 404", st == 404, st)
    st, *_ = req(A, "POST", "/api/screen", {"id": "lap-1", "follow": "no"}); check("A screen: follow not bool 400", st == 400, st)
    st, *_ = req(A, "POST", "/api/screen", {"id": "<x>", "follow": True}); check("A screen: bad id 400", st == 400, st)
    saved = json.load(open(os.path.join(S, "data-A", "screens.json")))
    check("A screen: saved to disk", saved.get("lap-1", {}).get("follow") is False, saved)
    st, *_ = req(A, "POST", "/api/screen/forget", {"id": "tab-1"})
    ids_seen = [x["id"] for x in req(A, "GET", "/screens.json")[1]["screens"]]
    check("A screen: forget", st == 200 and ids_seen == ["lap-1"], (st, ids_seen))
    for n in range(30):
        req(A, "GET", "/current.json?screen=s%d" % n)
    ids_seen = [x["id"] for x in req(A, "GET", "/screens.json")[1]["screens"]]
    check("A screen: capped at 24, the oldest following dropped, off ones kept", len(ids_seen) == 24
          and "lap-1" in ids_seen and "s0" not in ids_seen and "s29" in ids_seen, ids_seen)
    hostile = 'x" onmouseover=alert(1) a'
    req(A, "GET", "/current.json?screen=" + urllib.parse.quote(hostile))  # breaks SCREEN_RE: never listed
    req(A, "GET", "/current.json?screen=" + urllib.parse.quote("Hall Panel"))
    page = req(A, "GET", "/")[2]
    check("A screen: a name with a space, attribute-escaped", '&quot;Hall Panel&quot;' in page and "onmouseover" not in page)
finally:
    pa.terminate(); pa.wait(); fake.terminate()

# restart A: screens and their switches survive
pa = start("A", dict(cfgA, _fresh=False, rotation={"min_minutes": 30, "max_minutes": 40}))
try:
    j = req(A, "GET", "/current.json?screen=lap-1")[1]
    check("A restart keeps screens", j["follow"] is False and j["screens_off"] == ["lap-1"], j)
finally:
    pa.terminate(); pa.wait()

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
    st, *_ = req(B, "POST", "/api/rotation", {"mode": "daynight"}); check("B day & night refused off-panel 403", st == 403, st)
    req(B, "GET", "/current.json?screen=desk")
    st, *_ = req(B, "POST", "/api/screen", {"id": "desk", "follow": False}); check("B screen off from LAN ok", st == 200, st)
    st, *_ = req(B, "POST", "/api/screen/forget", {"id": "desk"}); check("B screen forget refused off-panel 403", st == 403, st)
    txt = req(B, "GET", "/")[2]
    check("B page: Forget and slider disabled", 'data-act="screen/forget" data-body="{&quot;id&quot;: &quot;desk&quot;}" disabled' in txt
          and 'data-name="How often" disabled' in txt and 'data-act="screen" data-body' in txt, txt[-2500:])
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

# ---------------- E: a picky OpenAI-compatible server, key given inline ----------------
# Needs the key, refuses response_format (LM Studio-style), max_tokens and temperature 0.4 (newer
# OpenAI models), and answers in content parts with an inline <think> block.
E = BASE + 8
fe = subprocess.Popen([sys.executable, os.path.join(HERE, "fakecap.py"), str(BASE + 11), FAIL_FLAG,
                       "key=k1", "no-json-mode", "new-openai", "parts", "think"])
pe = start("E", {"port": E, "ingress_peers": ["127.0.0.0/8"],
                 "captioners": [{"name": "picky", "url": "http://127.0.0.1:%d/v1/chat/completions" % (BASE + 11),
                                 "model": "x", "api_key": "k1", "timeout": 10}],
                 "caption": {"enabled": True, "min_words": 30, "max_words": 45}})
try:
    for _ in range(60):
        if all(i["status"] == "captioned" for i in lib(E)["items"]): break
        time.sleep(1)
    L = lib(E); check("E picky server captions everything", all(i["status"] == "captioned" for i in L["items"]),
                      [(i["status"], i["error"]) for i in L["items"]])
    logE = open(os.path.join(S, "log-E.txt")).read()
    for what in ("no response_format", "max_completion_tokens instead of max_tokens", "the server's default temperature"):
        check("E adapted: %s" % what, logE.count("retrying with " + what) == 1, logE[-600:])
    check("E endpoint never skipped", L["captioner"]["endpoints"][0]["skipped_until"] is None, L["captioner"])
finally:
    pe.terminate(); pe.wait(); fe.terminate()

# ---------------- F: every captioner unreachable, then one comes back ----------------
F = BASE + 12
dead = "http://127.0.0.1:%d/v1/chat/completions" % (BASE + 13)  # nothing listens here yet
pf = start("F", {"port": F, "ingress_peers": ["127.0.0.0/8"], "rotation": {"min_minutes": 15, "max_minutes": 20},
                 "captioners": [{"name": "dead", "url": dead, "model": "x", "timeout": 5, "down_minutes": 0.1}],
                 "caption": {"enabled": True, "min_words": 30, "max_words": 45}})
ff = None
try:
    for _ in range(40):
        L = lib(F)
        if L["captioner"]["endpoints"][0]["skipped_until"]: break
        time.sleep(0.5)
    check("F unreachable endpoint is skipped for a while", L["captioner"]["endpoints"][0]["skipped_until"], L["captioner"])
    check("F nothing marked failed", all(i["status"] == "pending" for i in L["items"]), [i["status"] for i in L["items"]])
    c = cur(F); check("F serves uncaptioned", c["captioned"] is False, c)
    seen = {c["id"]}
    for _ in range(4):
        st, j, *_ = req(F, "POST", "/api/next", {}); seen.add(cur(F)["id"])
    check("F still rotates without a captioner", len(seen) >= 2, seen)
    st, *_ = req(F, "GET", "/current/label/640x360.jpg"); check("F label renders uncaptioned", st == 200, st)
    check("F postponed, not failed, in the log", "postponed" in open(os.path.join(S, "log-F.txt")).read())
    ff = subprocess.Popen([sys.executable, os.path.join(HERE, "fakecap.py"), str(BASE + 13), FAIL_FLAG])
    for _ in range(60):
        if all(i["status"] == "captioned" for i in lib(F)["items"]): break
        time.sleep(1)
    check("F captions once the captioner is back", all(i["status"] == "captioned" for i in lib(F)["items"]),
          [i["status"] for i in lib(F)["items"]])
finally:
    pf.terminate(); pf.wait()
    if ff: ff.terminate()

# ---------------- G: per-folder meta.json and exclude globs ----------------
G = BASE + 14
gdir = os.path.join(S, "imgs-meta")
shutil.copytree(IMGS, gdir)
json.dump({"test-0.jpg": {"title": "Red Field", "place": "Nowhere, Testshire", "credit": "Photo: Tester",
                          "context": "A red test card.", "rating": "ignored"},
           "missing.jpg": {"title": "Not here"}}, open(os.path.join(gdir, "meta.json"), "w"))
pg = start("G", {"port": G, "ingress_peers": ["127.0.0.0/8"], "captioners": [], "caption": {"enabled": False},
                 "exclude": ["test-3", "nomatch-*"], "_imgs": gdir,
                 # names no system has: Pillow searches the system font folders by file name otherwise
                 "style": {"font_dir": os.path.join(S, "no-fonts"),
                           "fonts": {r: "Matchy-Missing-%s.ttf" % r for r in ("title", "place", "body", "dropcap")}}})
try:
    L = lib(G); items = {i["id"]: i for i in L["items"]}
    check("G meta.json fills title/place/credit", items.get("test-0", {}).get("title") == "Red Field"
          and items["test-0"]["place"] == "Nowhere, Testshire" and items["test-0"]["credit"] == "Photo: Tester", items.get("test-0"))
    check("G meta.json leaves others alone", items.get("test-1", {}).get("title") != "Red Field", items.get("test-1"))
    check("G exclude glob drops the id", "test-3" not in items and len(items) == 3, sorted(items))
    st, _, txt, _ = req(G, "GET", "/"); check("G page shows the meta title", "Red Field" in txt)
    st, _, _, h = req(G, "GET", "/current/label/640x360.jpg")
    check("G label renders without the fonts", st == 200 and h.get("Content-Type") == "image/jpeg", (st, h))
    req(G, "GET", "/current/label/1280x720.jpg")
    missing = [l for l in open(os.path.join(S, "log-G.txt")).read().splitlines() if "unusable" in l]
    check("G missing font logged once per file", missing and len(missing) == len({l.split()[3] for l in missing}), missing)
finally:
    pg.terminate(); pg.wait()

# ---------------- H: a client off this network cannot add screens or read the list ----------------
H = BASE + 5
ph = start("H", {"port": H, "lan_cidrs": ["10.0.0.0/8"], "captioners": [], "caption": {"enabled": False}})
try:
    st, j, *_ = req(H, "GET", "/current.json?screen=outsider")
    check("H screen from off-network still served", st == 200 and j["follow"] is True, (st, j))
    st, _, txt, hdrs = req(H, "GET", "/screens.json")
    check("H screens.json refused off-network, no CORS", st == 403 and "Access-Control-Allow-Origin" not in hdrs, (st, hdrs))
    check("H off-network screen not listed", "outsider" not in req(H, "GET", "/")[2])
finally:
    ph.terminate(); ph.wait()

print("\n%d passed, %d failed (logs in %s)" % (ok, fail, S))
if not fail:
    shutil.rmtree(S, ignore_errors=True)
sys.exit(1 if fail else 0)
