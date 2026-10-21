"""Integration tests for the Matchy engine: rotation, quiet hours, the panel controls, access tiers, request
bodies, and images too large to decode. Runs the real engine as a subprocess against generated
images and a fake OpenAI-compatible captioner (tests/fakecap.py).

    uv run --with pillow python tests/run_tests.py      # or any python3 with Pillow

MATCHY_TEST_PORT moves the ports it uses (default 18160..18174). Takes about three minutes.
"""
import collections, http.client, json, os, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    # named like a picture, but not one Pillow can decode
    open(os.path.join(S, "imgs-big", "broken.jpg"), "wb").write(b"not a picture at all " * 1000)
    # Focal-point crops: a busy red-and-white subject far right on a quiet grey ground (a centred 9:16
    # crop misses it entirely), and one ordinary picture.
    os.makedirs(os.path.join(S, "imgs-focus"))
    im = Image.new("RGB", (1600, 900), (128, 128, 128))
    d = ImageDraw.Draw(im)
    for _ in range(3000):  # faint grain, so the file is not tiny; too weak to pull the focus
        x, y = random.randrange(1600), random.randrange(900)
        d.point((x, y), fill=(131, 131, 131))
    for i in range(16):
        d.rectangle((1280 + i * 10, 300, 1285 + i * 10, 600), fill=(220, 20, 20))
        d.rectangle((1285 + i * 10, 300 + i * 18, 1290 + i * 10, 318 + i * 18), fill=(255, 255, 255))
    im.save(os.path.join(S, "imgs-focus", "subject-right.jpg"), quality=95)
    shutil.copy(os.path.join(IMGS, "test-0.jpg"), os.path.join(S, "imgs-focus"))


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
def item(port, iid): return {i["id"]: i for i in lib(port)["items"]}.get(iid)

def red_share(port, path):
    """Share of clearly red pixels in the JPEG at path (the focus test picture's subject)."""
    from PIL import Image
    import io
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("GET", path); r = c.getresponse(); data = r.read(); c.close()
    if r.status != 200: return -1
    raw = Image.open(io.BytesIO(data)).convert("RGB").tobytes(); n = len(raw) // 3
    return sum(1 for i in range(0, 3 * n, 3) if raw[i] > 150 and raw[i] - raw[i + 1] > 60) / n

# ---------------- sun_events (pure): Cork at the solstices, Svalbard in midnight sun ----------------
import datetime as _dt
r = engine_mod.sun_events(51.8985, -8.4756, _dt.date(2026, 6, 21))
check("sun: Cork midsummer", "03:50" <= utc_hm(r[0]) <= "04:20" and "20:50" <= utc_hm(r[1]) <= "21:20", [utc_hm(t) for t in r])
r = engine_mod.sun_events(51.8985, -8.4756, _dt.date(2026, 12, 21))
check("sun: Cork midwinter", "08:20" <= utc_hm(r[0]) <= "08:50" and "16:10" <= utc_hm(r[1]) <= "16:40", [utc_hm(t) for t in r])
check("sun: midnight sun has none", engine_mod.sun_events(78.2, 15.6, _dt.date(2026, 6, 21)) is None)

# ---------------- in-process: odd files and names, races, leaks ----------------
import contextlib, functools, http.server, io, threading, ast
import library as library_mod  # noqa: E402
from PIL import Image as _Image


def noisy(path, size=(1600, 900), **kw):
    _Image.effect_noise(size, 60).convert("RGB").save(path, **kw)


def lib_engine(name, files=(), **extra):
    """An Engine on its own folder and data dir, never served: for the library and render paths."""
    root = os.path.join(S, "unit-" + name)
    src = os.path.join(root, "src")
    os.makedirs(src)
    for rel, make in files:
        full = os.fsdecode(os.path.join(os.fsencode(src), rel if isinstance(rel, bytes) else rel.encode()))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        make(full)
    cfg = engine_mod.merge(engine_mod.DEFAULTS, dict(dict(data_dir=os.path.join(root, "data"), folders=[{"path": src}],
                                                          captioners=[], caption={"enabled": False}), **extra))
    os.makedirs(cfg["data_dir"], exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()):
        return engine_mod.Engine(cfg)


quiet = functools.partial(contextlib.redirect_stdout, io.StringIO())
garbage = lambda p: open(p, "wb").write(b"not a picture at all " * 1000)


def truncated(p):
    noisy(p)
    data = open(p, "rb").read()
    open(p, "wb").write(data[:len(data) // 2])


# A picture Pillow cannot decode (not an image, or cut short) is skipped with its reason, like one too large:
# before, it stayed in rotation and every render of it answered 500.
e = lib_engine("corrupt", [("good.jpg", noisy), ("broken.jpg", garbage), ("trunc.jpg", truncated)])
with quiet():
    e.lib.rescan()
    errs = {}
    for iid in ("broken", "trunc"):
        try:
            e.lib.ensure_master(iid)
        except Exception as x:
            errs[iid] = x
check("unit corrupt: ValueError with a reason", all(isinstance(x, ValueError) for x in errs.values()) and len(errs) == 2,
      {k: repr(v) for k, v in errs.items()})
check("unit corrupt: out of rotation", e.eligible() == ["good"] and set(e.lib.skipped) == {"broken", "trunc"}, e.eligible())
check("unit corrupt: no file path in the public reason", all(S not in v[1] for v in e.lib.skipped.values()), e.lib.skipped)
e = lib_engine("corrupt-png", [("good.jpg", noisy), ("broken.png", garbage)])
with quiet():
    e.lib.rescan()
    try:
        e.original("broken"); orig = "no error"
    except Exception as x:
        orig = x
check("unit corrupt: its original is a 400 (ValueError), not a 500", isinstance(orig, ValueError), repr(orig))
# ...and one already on screen gives way, as a removed one does.
e = lib_engine("corrupt-current", [("good.jpg", noisy), ("broken.jpg", garbage)])
with quiet():
    e.lib.rescan()
    e.state.update(current="broken", since=time.time(), next_change=time.time() + 3600)
    threading.Thread(target=e.rotator, daemon=True).start()
    try:
        e.lib.ensure_master("broken")
    except Exception:
        pass
    for _ in range(80):
        if e.state.get("current") != "broken":
            break
        time.sleep(0.1)
check("unit corrupt: the one on screen gives way", e.state.get("current") == "good", e.state.get("current"))

# A file that disappears (or cannot be read) between the folder listing and its hash: the rest still loads.
e = lib_engine("vanish", [("a.jpg", noisy), ("b.jpg", noisy), ("c.jpg", noisy)])
real_sha = library_mod.sha256_file
def flaky_sha(p):
    if p.endswith("b.jpg"):
        raise FileNotFoundError(2, "No such file or directory", p)
    return real_sha(p)
library_mod.sha256_file = flaky_sha
try:
    with quiet():
        e.lib.rescan()
    check("unit vanished file: the others load", sorted(e.lib.items) == ["a", "c"], sorted(e.lib.items))
except Exception as x:
    check("unit vanished file: the others load", False, repr(x))
finally:
    library_mod.sha256_file = real_sha

# File and folder names that are not UTF-8 (old archives, a Samba share in another charset): Python hands
# them over with surrogates, which json.dump to a UTF-8 file refuses, so the whole scan failed.
e = lib_engine("bytes", [(b"caf\xe9.jpg", noisy), (b"Gem\xe4lde/one.jpg", noisy), ("plain.jpg", noisy)])
try:
    with quiet():
        e.lib.rescan()
        page = engine_mod.gallery_html(e, True, True).encode()
        lib_json = json.dumps(e.library_view(), ensure_ascii=False).encode()
        cache_back = library_mod.read_json(e.lib.hash_cache_path, {})
    check("unit non-UTF-8 names: scanned", len(e.lib.items) == 3, sorted(e.lib.items))
    check("unit non-UTF-8 names: hash cache reloads whole", len(cache_back) == 3, cache_back)
    check("unit non-UTF-8 names: library.json and the page encode", b"gem" in lib_json.lower() and b"Gem" in page)
except Exception as x:
    check("unit non-UTF-8 names: scanned", False, repr(x))
try:
    buf = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    with contextlib.redirect_stdout(buf):
        library_mod.log("master", "x", "skipped:", os.fsdecode(b"/media/caf\xe9.jpg"))
    check("unit non-UTF-8 names: a log line with one does not raise", True)
except Exception as x:
    check("unit non-UTF-8 names: a log line with one does not raise", False, repr(x))

# A '|' in a file name: the hash cache split its key on the first '|' and dropped the entry after every
# scan, so that file was hashed again every folder_scan_minutes.
e = lib_engine("pipe", [("sun|set.jpg", noisy), ("plain.jpg", noisy)])
hashed = []
library_mod.sha256_file = lambda p: hashed.append(p) or real_sha(p)
try:
    with quiet():
        e.lib.rescan(); first = len(hashed); e.lib.rescan()
    check("unit '|' in a name: hashed once, not every scan", first == 2 and len(hashed) == 2 and len(e.lib.hash_cache) == 2,
          (first, len(hashed), list(e.lib.hash_cache)))
finally:
    library_mod.sha256_file = real_sha

# Phone photos store portrait pictures landscape with an EXIF Orientation tag; the master ignored it.
exif = _Image.Exif(); exif[0x0112] = 6  # rotate 90° clockwise to view
e = lib_engine("exif", [("phone.jpg", lambda p: noisy(p, exif=exif.tobytes()))])
with quiet():
    e.lib.rescan()
    size = _Image.open(e.lib.ensure_master("phone")).size
check("unit EXIF orientation: portrait master", size == (900, 1600), size)

# A 16-bit greyscale PNG (0-65535): convert("RGB") clips it, so the wallpaper came out plain white.
def grey16(p):
    import random
    im = _Image.new("I;16", (800, 600))
    im.putdata([random.randrange(20000, 40000) for _ in range(800 * 600)])
    im.save(p)
e = lib_engine("grey16", [("scan.png", grey16)])
with quiet():
    e.lib.rescan()
    m = _Image.open(e.lib.ensure_master("scan")).convert("L")
mean = sum(m.resize((80, 60)).tobytes()) / 4800
check("unit 16-bit greyscale PNG: mid grey, not white", 90 < mean < 145, mean)

# A master made before those two fixes (named <id>-<first 8 of sha256>.jpg) is made again, and the old one goes.
e = lib_engine("upgrade", [("phone.jpg", lambda p: noisy(p, exif=exif.tobytes()))])
with quiet():
    e.lib.rescan()
    old = os.path.join(e.cfg["data_dir"], "masters", "phone-%s.jpg" % e.lib.items["phone"]["sha256"][:8])
    _Image.new("RGB", (1600, 900)).save(old)  # sideways, as 0.4.0 made it
    size = _Image.open(e.lib.ensure_master("phone")).size
check("unit masters from before are rebuilt", size == (900, 1600) and not os.path.exists(old), (size, os.path.exists(old)))

# Masters of pictures taken out of the folders stayed in masters/ (and in every backup) for good. They now go
# after a scan, but never while a picture folder is missing or empty: an unmounted share must not cost them.
e = lib_engine("orphans", [("a.jpg", noisy), ("b.jpg", noisy), ("sub/c.jpg", noisy)])
src = e.cfg["folders"][0]["path"]
mdir = os.path.join(e.cfg["data_dir"], "masters")
masters = lambda: sorted(f.split("-")[0] for f in os.listdir(mdir))
with quiet():
    e.lib.rescan()
    for i in list(e.lib.items):
        e.lib.ensure_master(i)
    os.remove(os.path.join(src, "b.jpg"))
    e.lib.rescan()
check("unit masters of a deleted picture are removed", masters() == ["a", "sub"], masters())
with quiet():
    os.rename(src, src + ".away")  # the share is unmounted
    e.lib.rescan()
    kept_missing = masters()
    os.makedirs(src)  # an empty mount point
    e.lib.rescan()
    kept_empty = masters()
    os.rmdir(src); os.rename(src + ".away", src)
check("unit masters kept while the folder is missing or empty", kept_missing == kept_empty == ["a", "sub"],
      (kept_missing, kept_empty))

# A picture replaced by a new version under the same name, with the label text unchanged (captions off, or a
# caption written by hand): the rev, and so the render cache key and the /img/ URL, stayed the same, so the
# old picture was served on (and browsers keep /img/ URLs for a year).
def tinted(p, rgb):
    _Image.merge("RGB", [_Image.effect_noise((1600, 900), 20).point(lambda v, c=c: min(255, v // 4 + c)) for c in rgb]).save(p)
e = lib_engine("replaced", [("sunset.png", lambda p: tinted(p, (200, 0, 0))), ("pad.jpg", noisy)])
with quiet():
    e.lib.rescan()
    rev1 = e.lib.display("sunset")["rev"]
    e.render_kind("sunset", rev1, "clean", 640, 360)
    tinted(os.path.join(e.cfg["folders"][0]["path"], "sunset.png"), (0, 0, 200))
    os.utime(os.path.join(e.cfg["folders"][0]["path"], "sunset.png"), (1, 1))  # a copy brings its own mtime
    e.lib.rescan()
    rev2 = e.lib.display("sunset")["rev"]
    px = _Image.open(io.BytesIO(e.render_kind("sunset", rev2, "clean", 640, 360))).getpixel((320, 180))
check("unit a replaced picture gets a new rev and is rendered anew", rev1 != rev2 and px[2] > px[0], (rev1, rev2, px))

# Two threads building the same master (a screen and the panel asking for the new picture at once, or
# the scanner and the captioner): both wrote <master>.tmp, and the loser's os.replace found it gone.
e = lib_engine("race", [("big.jpg", lambda p: noisy(p, size=(5000, 3000), quality=95))])
with quiet():
    e.lib.rescan()
    race_errs = []
    for _ in range(4):
        for f in os.listdir(os.path.join(e.cfg["data_dir"], "masters")):
            os.remove(os.path.join(e.cfg["data_dir"], "masters", f))
        def build():
            try:
                _Image.open(e.lib.ensure_master("big")).load()
            except Exception as x:
                race_errs.append(repr(x))
        ts = [threading.Thread(target=build) for _ in range(3)]
        [t.start() for t in ts]; [t.join() for t in ts]
check("unit concurrent master builds: no errors", not race_errs, race_errs[:2])

# A render that fails (an undecodable picture) left its per-key lock in render_locks for good, one per
# size asked for: memory any client on the port could grow.
e = lib_engine("locks", [("ok.jpg", noisy), ("bad.jpg", garbage)])
with quiet():
    e.lib.rescan()
    rev = e.lib.display("bad")["rev"]
    for w in range(320, 340):
        try:
            e.render_kind("bad", rev, "clean", w, 400)
        except Exception:
            pass
check("unit failed renders leave no locks behind", not e.render_locks, len(e.render_locks))

# screens_off() walked the screens dict without the lock while a request added a screen: "dictionary
# changed size during iteration", a 500 on /current.json.
e = lib_engine("screens", [("a.jpg", noisy)])
e.save_screens = lambda: None
stop, scr_errs = [], []
def add_screens():
    n = 0
    while not stop:
        e.saw_screen("s%d" % n, "10.0.0.1", "ua"); n += 1
def read_screens():
    while not stop:
        try:
            e.screens_off()
        except RuntimeError as x:
            scr_errs.append(str(x))
with quiet():
    ts = [threading.Thread(target=add_screens), threading.Thread(target=read_screens)]
    [t.start() for t in ts]; time.sleep(3); stop.append(1); [t.join() for t in ts]
check("unit screens list read while one is added", not scr_errs, scr_errs[:1])

# A source folder that holds the add-on's own data (source_folders: ["/"], or the data dir itself): its
# masters were scanned as new pictures, whose masters were scanned again, one more set every scan.
root = os.path.join(S, "unit-selfscan")
os.makedirs(os.path.join(root, "pics"))
for n in "ab":
    noisy(os.path.join(root, "pics", n + ".jpg"))
cfg = engine_mod.merge(engine_mod.DEFAULTS, {"data_dir": os.path.join(root, "data"), "folders": [{"path": root}],
                                            "captioners": [], "caption": {"enabled": False}})
os.makedirs(cfg["data_dir"])
with quiet():
    e = engine_mod.Engine(cfg)
    counts = []
    for _ in range(3):
        e.lib.rescan()
        for i in list(e.lib.items):
            try:
                e.lib.ensure_master(i)
            except Exception:
                pass
        counts.append(len(e.lib.items))
check("unit the data dir is never scanned as pictures", counts == [2, 2, 2], counts)

# Remote manifest: two file names with the same slug (non-Latin names, Sunset.jpg and sunset.png) got one
# id; only the first was downloaded, and it ended up with the other one's title.
web = os.path.join(S, "unit-web")
os.makedirs(web)
manifest = [("Moscow", "Москва.jpg"), ("Kyiv", "Київ.jpg"), ("Sunset JPEG", "Sunset.jpg"), ("Sunset PNG", "sunset.png")]
for _title, fn in manifest:
    noisy(os.path.join(web, fn), size=(800, 600))
json.dump([{"name": t, "file": f} for t, f in manifest], open(os.path.join(web, "manifest.json"), "w"))
json.dump({"date": "20261020", "title": "a quiet lake", "place": "Somewhere", "copyright": None},
          open(os.path.join(web, "daily.json"), "w"))
class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
web_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=web))
threading.Thread(target=web_srv.serve_forever, daemon=True).start()
web_url = "http://127.0.0.1:%d/" % web_srv.server_port
e = lib_engine("remote", static_manifest={"enabled": True, "base_url": web_url, "id_prefix": "remote"})
with quiet():
    e.lib.sync_static_manifest(); e.lib.sync_static_manifest()
titles = sorted(v["title"] for v in e.lib.remote.values())
shas = {v["sha256"] for v in e.lib.remote.values()}
check("unit remote names with the same slug: every picture, its own title",
      titles == sorted(t for t, _f in manifest) and len(shas) == 4, {k: v["title"] for k, v in e.lib.remote.items()})

# Daily JSON with "copyright": null (the other fields already allowed null): TypeError on every check.
e = lib_engine("daily", daily={"enabled": True, "json_url": web_url + "daily.json", "image_url": web_url + "Sunset.jpg",
                               "id_prefix": "daily"})
try:
    with quiet():
        got = e.lib.sync_daily()
    check("unit daily with a null copyright", got == "daily-20261020", got)
except Exception as x:
    check("unit daily with a null copyright", False, repr(x))
web_srv.shutdown()

# A keep-alive connection whose client vanished (a tablet dropping off Wi-Fi) held a server thread forever.
check("unit idle connections time out", isinstance(engine_mod.Handler.timeout, (int, float))
      and 0 < engine_mod.Handler.timeout <= 300, engine_mod.Handler.timeout)

# tools/: meta.json is read by the engine as UTF-8, so the tools must write it as UTF-8 whatever the
# locale (Windows: cp1252 turned "·" in every credit into a byte the engine rejects, losing the file).
for tool in ("retro_themes.py", "make_artwork.py"):
    tree = ast.parse(open(os.path.join(HERE, "..", "tools", tool), encoding="utf-8").read())
    bad = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"
           and not any(k.arg == "encoding" for k in n.keywords)
           and not any(isinstance(a, ast.Constant) and "b" in str(a.value) for a in n.args[1:2])]
    check("tools/%s opens text files as UTF-8" % tool, not bad, bad)
# ---------------- loved pictures (pure): pick_next on a stand-in engine, thousands of picks ----------------
class _Stub:
    """Just what Engine.pick_next, new_bag and love_weight touch."""
    def __init__(self, ids, loved=(), weight=3, hidden=()):
        self.ids = list(ids)
        self.cfg = {"rotation": {"love_weight": weight}}
        self.state = {"bag": [], "priority": [], "hidden": list(hidden), "loved": list(loved)}
    def eligible(self): return sorted(i for i in self.ids if i not in self.state["hidden"])
    def mood_level(self): return "off"
    def mood_context(self): return []
    lib = type("_Lib", (), {"tags": staticmethod(lambda iid: [])})()
    love_weight = engine_mod.Engine.love_weight
    new_bag = engine_mod.Engine.new_bag
    pick_next = engine_mod.Engine.pick_next

def picks(stub, n):
    out, cur, quiet = [], None, engine_mod.log
    engine_mod.log = lambda *a: None  # a new shuffle is logged; thousands of them are noise here
    try:
        for _ in range(n):
            cur = stub.pick_next(exclude=cur); out.append(cur)
    finally:
        engine_mod.log = quiet
    return out

ids = ["p%02d" % i for i in range(20)]
seq = picks(_Stub(ids, loved=["p03", "p17"], weight=3), 6000)
counts = {i: seq.count(i) for i in ids}
plain = sum(counts[i] for i in ids if i not in ("p03", "p17")) / 18
check("love: loved come up about love_weight times as often", all(2.8 <= counts[i] / plain <= 3.2 for i in ("p03", "p17")),
      (counts["p03"] / plain, counts["p17"] / plain))
check("love: the others come up evenly", max(counts[i] for i in ids if i not in ("p03", "p17")) <= 1.2 * plain, counts)
check("love: never the same picture twice in a row", all(a != b for a, b in zip(seq, seq[1:])))
gaps = [b - a for a, b in zip([k for k, i in enumerate(seq) if i == "p03"], [k for k, i in enumerate(seq) if i == "p03"][1:])]
check("love: a loved picture's turns are spread, not bunched", min(gaps) >= 2 and sum(gaps) / len(gaps) < 9, (min(gaps), sum(gaps) / len(gaps)))
seq = picks(_Stub(ids, loved=["p03", "p17"], weight=1), 2000)
counts = {i: seq.count(i) for i in ids}
check("love: love_weight 1 means no weighting", max(counts.values()) <= 1.2 * min(counts.values()), counts)
seq = picks(_Stub(ids, loved=["p03", "p17"], weight=3, hidden=["p03"]), 1500)
check("love: hidden beats loved", "p03" not in seq and seq.count("p17") > 150, seq.count("p17"))
seq = picks(_Stub(ids[:2], loved=["p00"], weight=3), 300)
check("love: two pictures, one loved: alternate, never repeat", all(a != b for a, b in zip(seq, seq[1:])) and 140 <= seq.count("p00") <= 160, seq.count("p00"))
seq = picks(_Stub(ids[:1], loved=["p00"], weight=3), 5)
check("love: one picture left stays on", seq == ["p00"] * 5, seq)
seq = picks(_Stub(ids, loved=ids, weight=4), 4000)
counts = {i: seq.count(i) for i in ids}
check("love: everything loved is nobody loved", max(counts.values()) <= 1.2 * min(counts.values()) and all(a != b for a, b in zip(seq, seq[1:])), counts)
bag = engine_mod.spread(["a", "b", "c", "d", "e", "f", "g", "h"], "x", 2)
check("love: extra turns go one per stretch of the shuffle", len(bag) == 10 and bag.count("x") == 2 and bag.index("x") <= 4 <= len(bag) - 1 - bag[::-1].index("x")
      and [i for i in bag if i != "x"] == list("abcdefgh"), bag)
check("love: spread into an empty bag", engine_mod.spread([], "x", 2) == ["x", "x"])

# ---------------- quiet hours (pure): windows across midnight, and the clocks changing in Europe/Dublin ----------------
def at(y, mo, d, h, mi=0): return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))
def hours(w): return (w[2] - w[1]) / 3600

tz_before = os.environ.get("TZ")
os.environ["TZ"] = "Europe/Dublin"; time.tzset()
NIGHT = ((23, 0), (7, 0))
qw = engine_mod.quiet_window
w = qw(at(2026, 10, 20, 23, 30), *NIGHT)
check("quiet: 23:30 is inside, the window ends the next morning", w == (True, at(2026, 10, 20, 23), at(2026, 10, 21, 7)), w)
check("quiet: an ordinary night is 8 hours", hours(w) == 8, hours(w))
w = qw(at(2026, 10, 21, 2), *NIGHT)
check("quiet: 02:00 is in the window that started the evening before", w == (True, at(2026, 10, 20, 23), at(2026, 10, 21, 7)), w)
w = qw(at(2026, 10, 21, 7), *NIGHT)
check("quiet: over at 07:00, next window tonight", w == (False, at(2026, 10, 21, 23), at(2026, 10, 22, 7)), w)
w = qw(at(2026, 10, 21, 22, 59), *NIGHT)
check("quiet: not yet at 22:59", w == (False, at(2026, 10, 21, 23), at(2026, 10, 22, 7)), w)
w = qw(at(2026, 10, 25, 3), *NIGHT)
check("quiet: the night the clocks go back (2026-10-25) is 9 hours, ending 07:00", w[0] and hours(w) == 9
      and time.localtime(w[2])[3:5] == (7, 0) and time.localtime(w[1])[2:5] == (24, 23, 0), (w, hours(w)))
w = qw(at(2026, 3, 29, 3), *NIGHT)
check("quiet: the night the clocks go forward (2026-03-29) is 7 hours", w[0] and hours(w) == 7, (w, hours(w)))
w = qw(at(2026, 10, 21, 12), (13, 0), (14, 30))
check("quiet: a window inside one day", w == (False, at(2026, 10, 21, 13), at(2026, 10, 21, 14, 30)), w)
w = qw(at(2026, 10, 21, 14, 30), (13, 0), (14, 30))
check("quiet: a window inside one day, next one tomorrow", w == (False, at(2026, 10, 22, 13), at(2026, 10, 22, 14, 30)), w)
w = qw(at(2026, 3, 28, 20), (1, 30), (6, 0))  # 01:30 does not exist in Dublin on 2026-03-29
check("quiet: a start the clocks skip still gives a window that ends at 06:00", not w[0] and 0 < hours(w) <= 4.5
      and time.localtime(w[2])[2:5] == (29, 6, 0), (w, hours(w)))
pq = engine_mod.parse_quiet
check("quiet: off by default and with one time", pq({}) is None and pq(None) is None and pq({"start": "23:00"}) is None
      and pq({"start": "23:00", "end": "23:00"}) is None and pq({"start": "25:00", "end": "07:00"}) is None)
check("quiet: defaults hold and dim", pq({"start": "23:00", "end": "7:00"}) == {"start": (23, 0), "end": (7, 0),
                                                                                "hold": True, "dim": 0.6})
check("quiet: hold off, dim 0, a bad dim", pq({"start": "1:00", "end": "2:00", "hold": False, "dim": 0})["hold"] is False
      and pq({"start": "1:00", "end": "2:00", "dim": 0})["dim"] == 0 and pq({"start": "1:00", "end": "2:00", "dim": 3})["dim"] == 0.6)
if tz_before is None: del os.environ["TZ"]
else: os.environ["TZ"] = tz_before
time.tzset()

# ---------------- quiet hours (one engine in this process): hold, Next, resume, paused, restart ----------------
def quiet_engine(**quiet):
    cfg = engine_mod.merge(engine_mod.DEFAULTS, {
        "data_dir": os.path.join(S, "data-quiet"), "folders": [{"path": IMGS}], "captioners": [],
        "caption": {"enabled": False}, "rotation": {"min_minutes": 15, "max_minutes": 20},
        "quiet_hours": dict({"start": "23:00", "end": "07:00"}, **quiet)})
    eng = engine_mod.Engine(cfg); eng.lib.rescan()
    return eng
eng = quiet_engine()
night, morning = at(2026, 10, 21, 1), at(2026, 10, 21, 7)
eng.rotator_step(night)
first = eng.state.get("current")
check("quiet: an empty screen still gets a picture", first and eng.state.get("quiet_hold") is True, eng.state)
eng.state["next_change"] = night - 60
eng.rotator_step(night + 60)
check("quiet: an overdue picture is held", eng.state["current"] == first, eng.state["current"])
eng.next_now("test")
second = eng.state["current"]
eng.rotator_step(night + 120)
check("quiet: Next still works, and the new picture is held too", second != first and eng.state["current"] == second
      and eng.state.get("quiet_hold") is True, eng.state)
eng = quiet_engine()  # a restart in the night: the hold is in state.json
eng.rotator_step(morning + 5)
check("quiet: hold resumes with a full interval at the end, across a restart", eng.state["current"] == second
      and "quiet_hold" not in eng.state and morning + 5 + 15 * 60 <= eng.state["next_change"] <= morning + 5 + 20 * 60,
      (eng.state.get("quiet_hold"), eng.state["next_change"] - morning))
eng.set_paused(True)
eng.state["next_change"] = 1234.0
eng.rotator_step(at(2026, 10, 21, 23, 30))
check("quiet: a window that starts while paused", eng.state.get("quiet_hold") is True and eng.state["paused"])
eng.rotator_step(at(2026, 10, 22, 7, 1))
check("quiet: ends still paused, the interval left to Resume", "quiet_hold" not in eng.state and eng.state["paused"]
      and eng.state["next_change"] == 1234.0 and eng.state["current"] == second, eng.state)
eng = quiet_engine(hold=False)
eng.set_paused(False)
eng.state["next_change"] = night - 60
eng.rotator_step(night)
check("quiet: hold off keeps rotating", eng.state["current"] != second and "quiet_hold" not in eng.state, eng.state)

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

    # colour match: every (flat red/green/blue/yellow) picture has its colours, in both JSON views, the
    # sidecar and the panel, and a caption written from scratch did not wipe them
    for _ in range(30):
        if all(i["palette"] for i in lib(A)["items"]): break
        time.sleep(1)
    L = lib(A); by_id = {i["id"]: i for i in L["items"]}
    check("A colours: every picture has a palette, accent and gnome_accent after captioning",
          all(i["palette"] and i["accent"] and i["gnome_accent"] for i in L["items"]),
          [(i["id"], i["palette"], i["accent"], i["gnome_accent"]) for i in L["items"]])
    check("A colours: the flat colours map to GNOME red/green/blue/yellow",
          [by_id["test-%d" % n]["gnome_accent"] for n in range(4)] == ["red", "green", "blue", "yellow"],
          [by_id["test-%d" % n]["gnome_accent"] for n in range(4)])
    c0 = cur(A)
    check("A colours: current.json carries them", c0["palette"] == by_id[c0["id"]]["palette"]
          and c0["accent"] == by_id[c0["id"]]["accent"] and c0["gnome_accent"] == by_id[c0["id"]]["gnome_accent"], c0)
    sc0 = json.load(open(os.path.join(S, "data-A", "library", c0["id"] + ".json")))
    check("A colours: sidecar holds them with the source hash, next to the caption", sc0.get("blurb")
          and sc0.get("colours", {}).get("palette") == c0["palette"] and sc0["colours"].get("sha256") == sc0.get("sha256"), sc0)
    page = req(A, "GET", "/")[2]
    check("A page: swatches under the picture", 'class="swatches"' in page and 'style="background:%s"' % c0["accent"] in page
          and "GNOME %s" % c0["gnome_accent"] in page, page[page.find("swatches") - 50:page.find("swatches") + 400])

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

    # loved: a mark on a picture, {} meaning the one on screen
    c0 = cur(A); check("A current has loved=false", c0["loved"] is False, c0)
    st, j, *_ = req(A, "POST", "/api/love", {}); check("A love the one on screen", st == 200 and j["current"]["loved"] is True, j)
    check("A library shows loved", [i["loved"] for i in lib(A)["items"] if i["id"] == c0["id"]] == [True])
    page = req(A, "GET", "/")[2]
    check("A page: loved tag and Unlove button", 'class="tag love">Loved' in page and 'data-act="unlove"' in page
          and "1 loved" in page, page[:1500])
    st, j, *_ = req(A, "POST", "/api/love", {}); check("A love twice is fine", st == 200 and j["current"]["loved"] is True)
    other = [i for i in ids if i != c0["id"]][0]
    st, j, *_ = req(A, "POST", "/api/love", {"id": other}); check("A love by id leaves the screen alone", st == 200 and j["current"]["id"] == c0["id"])
    check("A two loved", sum(i["loved"] for i in lib(A)["items"]) == 2)
    st, *_ = req(A, "POST", "/api/love", {"id": "nope"}); check("A love unknown 404", st == 404, st)
    st, *_ = req(A, "POST", "/api/love", {"id": 5}); check("A love non-text id 400", st == 400, st)
    st, j, *_ = req(A, "POST", "/api/unlove", {}); check("A unlove the one on screen", st == 200 and j["current"]["loved"] is False, j)
    st, *_ = req(A, "POST", "/api/unlove", {"id": other}); check("A unlove by id", st == 200 and not any(i["loved"] for i in lib(A)["items"]))
    st, *_ = req(A, "POST", "/api/unlove", {"id": other}); check("A unlove again is fine", st == 200)

    # caption edit
    old_rev = cur(A)["rev"]
    st, j, txt, _ = req(A, "POST", "/api/caption", {"id": target, "title": "My Title", "place": "Cork", "credit": "me",
                                                     "blurb": "Line one\n  line two <b>bold</b>"})
    c = cur(A); it = [i for i in lib(A)["items"] if i["id"] == target][0]
    check("A caption edit", st == 200 and c["rev"] != old_rev and c["blurb"] == "Line one line two <b>bold</b>" and it["edited"], (st, txt, c))
    check("A caption edit keeps the colours", it["palette"] and it["accent"] == c["accent"], it)
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
    check("A recaption keeps the colours", it["palette"] and it["gnome_accent"], it)

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

    # love: a heart, the flags in both JSON views and on the page, and hidden beats loved
    loved_id = cur(A)["id"]
    st, j, *_ = req(A, "POST", "/api/love", {"id": loved_id}); L = lib(A)
    check("A love current", st == 200 and j["current"]["loved"] is True and [i for i in L["items"] if i["id"] == loved_id][0]["loved"], (st, j))
    check("A library has loved flags and love_weight", all("loved" in i for i in L["items"]) and L["rotation"]["love_weight"] == 3, L["rotation"])
    page = req(A, "GET", "/")[2]
    check("A page: heart on the picture on screen and on every card", page.count('data-act="unlove"') == 2 and page.count('data-act="love"') == 3
          and 'class="card is-current is-loved"' in page and "Loved only" in page and "1 loved" in page, page.count('data-act="love"'))
    st, _, txt, _ = req(A, "POST", "/api/hide", {"id": loved_id}); it = [i for i in lib(A)["items"] if i["id"] == loved_id][0]
    check("A hide a loved picture: off screen, still loved", st == 200 and cur(A)["id"] != loved_id and it["hidden"] and it["loved"], (st, it))
    seen = set()
    for _ in range(10): req(A, "POST", "/api/next", {}); seen.add(cur(A)["id"])
    check("A hidden beats loved in rotation", loved_id not in seen and len(seen) == 3, seen)
    req(A, "POST", "/api/unhide", {"id": loved_id})
    st, *_ = req(A, "POST", "/api/love", {"id": "nope"}); check("A love unknown 404", st == 404, st)
    st, *_ = req(A, "POST", "/api/unlove", {"id": "ghost"}); check("A unlove unknown is fine", st == 200, st)
    saved = json.load(open(os.path.join(S, "data-A", "state.json")))
    check("A loved saved to state.json", saved.get("loved") == [loved_id], saved.get("loved"))

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
    check("A restart keeps the heart", [i["id"] for i in L["items"] if i["loved"]] == [loved_id], [i["id"] for i in L["items"] if i["loved"]])
    st, *_ = req(A, "POST", "/api/unlove", {"id": loved_id})
    check("A unlove", st == 200 and not any(i["loved"] for i in lib(A)["items"]) and cur(A)["loved"] is False
          and 'data-act="unlove"' not in req(A, "GET", "/")[2], st)
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
    box = page.split("<h2>Screens</h2>")[1].split("</section>")[0]
    check("A page: no last seen or address in the screens box", "127.0.0.1" not in box and "seen" not in box, box)
    hdrs = req(A, "GET", "/")[3]
    check("A page: no CORS (it lists addresses)", "Access-Control-Allow-Origin" not in hdrs
          and "Access-Control-Allow-Origin" in req(A, "GET", "/current.json")[3], hdrs)
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
    req(A, "GET", "/current.json?screen=" + urllib.parse.quote("Zoë"))
    ids_seen = [x["id"] for x in req(A, "GET", "/screens.json")[1]["screens"]]
    check("A screen: a name in another script (Zoë) is listed", "Zoë" in ids_seen
          and not any("onmouseover" in i for i in ids_seen), ids_seen)
finally:
    pa.terminate(); pa.wait(); fake.terminate()

# restart A: screens and their switches survive
pa = start("A", dict(cfgA, _fresh=False, rotation={"min_minutes": 30, "max_minutes": 40}))
try:
    j = req(A, "GET", "/current.json?screen=lap-1")[1]
    check("A restart keeps screens", j["follow"] is False and j["screens_off"] == ["lap-1"], j)
finally:
    pa.terminate(); pa.wait()

# ---------------- B: direct port, no captioner, captions off, colour match off ----------------
B = BASE + 2
pb = start("B", {"port": B, "rotation": {"min_minutes": 15, "max_minutes": 20}, "captioners": [], "caption": {"enabled": False},
                 "palette": {"enabled": False}})
try:
    first = cur(B)["id"]; time.sleep(12); c = cur(B)
    check("B no 5-second placeholder churn", c["id"] == first, (first, c["id"]))
    check("B colour match off: empty fields, no sidecar written", c["palette"] == [] and c["accent"] is None
          and c["gnome_accent"] is None and not os.listdir(os.path.join(S, "data-B", "library")), c)
    check("B page: no swatches", 'class="swatches"' not in req(B, "GET", "/")[2])
    st, _, txt, _ = req(B, "GET", "/")
    check("B page: admin buttons disabled", 'data-act="hide" disabled' in txt and 'data-act="pause">' in txt, "")
    check("B page: captioner off line", "Captioner: <b>off</b>" in txt)
    st, *_ = req(B, "POST", "/api/pause", {}); check("B pause from LAN ok", st == 200)
    st, *_ = req(B, "POST", "/api/resume", {}); check("B resume from LAN ok", st == 200)
    st, j, *_ = req(B, "POST", "/api/love", {"id": first})
    check("B love from LAN ok", st == 200 and [i for i in lib(B)["items"] if i["id"] == first][0]["loved"], (st, j))
    check("B page: heart usable off-panel", 'aria-pressed="true" title="Loved:' in req(B, "GET", "/")[2])
    st, *_ = req(B, "POST", "/api/unlove", {"id": first}); check("B unlove from LAN ok", st == 200 and not any(i["loved"] for i in lib(B)["items"]), st)
    st, *_ = req(B, "POST", "/api/love", {}); check("B love from LAN ok", st == 200 and cur(B)["loved"] is True, st)
    st, *_ = req(B, "POST", "/api/unlove", {}); check("B unlove from LAN ok", st == 200 and cur(B)["loved"] is False, st)
    for path, body in (("/api/hide", {"id": first}), ("/api/caption", {"id": first, "blurb": "x"}),
                       ("/api/recaption", {"id": first}), ("/api/rotation", {"reset": True}), ("/api/unhide", {"id": first})):
        st, *_ = req(B, "POST", path, body); check("B %s refused off-panel 403" % path, st == 403, st)
    st, *_ = req(B, "POST", "/api/rotation", {"mode": "daynight"}); check("B day & night refused off-panel 403", st == 403, st)
    st, *_ = req(B, "POST", "/api/focus", {"id": first, "x": 0.5, "y": 0.5}); check("B focus refused off-panel 403", st == 403, st)
    check("B page: thumbnails are not focus pickers", "thumb pick" not in req(B, "GET", "/")[2])
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

    # Focus points with focus_auto off (the default): no machine focus, a hand one still counts
    check("C focus_auto off: every crop centred", all(i["focus"] is None for i in lib(C)["items"]), lib(C)["items"])
    fid = ids[2]; rev0 = item(C, fid)["rev"]
    for bad in ({"id": fid, "x": 1.5, "y": 0.5}, {"id": fid, "x": True, "y": 0.5}, {"id": fid, "x": 0.5},
                {"id": fid, "x": "0.5", "y": 0.5}, {"id": fid, "reset": "yes"}, {"x": 0.5, "y": 0.5}):
        st, *_ = req(C, "POST", "/api/focus", bad); check("C focus bad %s 400" % bad, st == 400, st)
    st, *_ = req(C, "POST", "/api/focus", {"id": "nope", "x": 0.5, "y": 0.5}); check("C focus unknown id 404", st == 404, st)
    st, *_ = req(C, "POST", "/api/focus", {"id": fid, "x": 0.25, "y": 0.75}); it = item(C, fid)
    check("C focus by hand", st == 200 and it["focus"] == {"x": 0.25, "y": 0.75, "source": "hand"} and it["rev"] != rev0, (st, it))
    st, *_ = req(C, "GET", "/img/%s/%s/label/1080x1920.jpg" % (fid, it["rev"])); check("C focused label renders", st == 200, st)
    st, *_ = req(C, "GET", "/img/%s/%s/clean/640x360.jpg" % (fid, rev0)); check("C old rev 302 after a focus change", st == 302, st)
    page = req(C, "GET", "/")[2]
    check("C page: dot, picker and Reset focus", 'class="dot hand"' in page and "thumb pick" in page
          and "Reset focus" in page and 'data-crop="0.00000 ' in page, page[-3000:])
    st, *_ = req(C, "POST", "/api/focus", {"id": fid, "reset": True}); it = item(C, fid)
    check("C reset focus: centred, the old rev back", st == 200 and it["focus"] is None and it["rev"] == rev0, it)
    check("C reset leaves no focus in the sidecar", "focus" not in json.load(open(os.path.join(S, "data-C", "library", fid + ".json"))))
finally:
    pc.terminate(); pc.wait()

# ---------------- D: images too large, or impossible, to decode stay out of rotation ----------------
D = BASE + 6
pd = start("D", {"port": D, "ingress_peers": ["127.0.0.0/8"], "captioners": [], "caption": {"enabled": False},
                 "_imgs": os.path.join(S, "imgs-big")})
try:
    for _ in range(60):
        bad = {i["id"]: i for i in lib(D)["items"] if i["id"] in ("huge", "broken")}
        if len(bad) == 2 and all(i["error"] for i in bad.values()): break
        time.sleep(1)
    check("D huge shows its reason", "too large" in bad.get("huge", {}).get("error", ""), bad.get("huge"))
    check("D broken shows its reason", "cannot be decoded" in bad.get("broken", {}).get("error", ""), bad.get("broken"))
    seen = set()
    for _ in range(12):
        req(D, "POST", "/api/next", {}); seen.add(cur(D)["id"])
    check("D huge and broken never rotate in", not {"huge", "broken"} & seen and len(seen) >= 2, seen)
    st, _, txt, _ = req(D, "POST", "/api/show", {"id": "huge"}); check("D show huge 409", st == 409, (st, txt))
    st, _, txt, _ = req(D, "POST", "/api/show", {"id": "broken"}); check("D show broken 409", st == 409, (st, txt))
    st, _, txt, _ = req(D, "GET", "/img/broken/%s/clean/640x360.jpg" % bad["broken"]["rev"])
    check("D broken render is a 400 with the reason, not a 500", st == 400 and "cannot be decoded" in txt, (st, txt))
    for i in [x for x in seen][:3]: req(D, "POST", "/api/hide", {"id": i})
    left = [i["id"] for i in lib(D)["items"] if not i["hidden"] and i["id"] not in ("huge", "broken")]
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
    # caption_enabled false stops the captioner, not the blurbs: one written by hand still shows (DOCS says so)
    req(G, "POST", "/api/caption", {"id": "test-1", "title": "Green", "blurb": "Written by hand."})
    req(G, "POST", "/api/show", {"id": "test-1"})
    c = cur(G); check("G captions off: a hand-written blurb still shows", c["blurb"] == "Written by hand." and c["captioned"], c)
finally:
    pg.terminate(); pg.wait()

# ---------------- I: focal-point crops from the captioner's subject box, and a hand focus over it ----------------
I = BASE + 1
FD = os.path.join(S, "imgs-focus")
fi = subprocess.Popen([sys.executable, os.path.join(HERE, "fakecap.py"), str(BASE + 3), FAIL_FLAG, "subject=0.05,0.1,0.35,0.9"])
pi = start("I", {"port": I, "ingress_peers": ["127.0.0.0/8"], "focus_auto": True, "_imgs": FD,
                 "captioners": [{"name": "fake", "url": "http://127.0.0.1:%d/v1/chat/completions" % (BASE + 3), "model": "x", "timeout": 10}],
                 "caption": {"enabled": True, "min_words": 30, "max_words": 45}})
try:
    for _ in range(60):
        L = lib(I)
        if all(i["status"] == "captioned" and (i["focus"] or {}).get("source") == "model" for i in L["items"]): break
        time.sleep(1)
    check("I the captioner's box becomes the focus", L["items"] and all(i["focus"] == {"x": 0.2, "y": 0.5, "source": "model"}
                                                                      for i in L["items"]), [i["focus"] for i in L["items"]])
    sc = json.load(open(os.path.join(S, "data-I", "library", "subject-right.json")))
    check("I sidecar keeps the box", sc.get("subject") == [0.05, 0.1, 0.35, 0.9] and sc["focus"]["source"] == "model", sc)
    check("I page: a hollow dot for the model focus", 'class="dot model"' in req(I, "GET", "/")[2])
    req(I, "POST", "/api/focus", {"id": "subject-right", "x": 0.85, "y": 0.5})
    rev = item(I, "subject-right")["rev"]
    check("I hand focus: a 9:16 crop keeps the subject", red_share(I, "/img/subject-right/%s/clean/360x640.jpg" % rev) > 0.02)
    req(I, "POST", "/api/recaption", {"id": "subject-right"})
    for _ in range(40):
        it = item(I, "subject-right")
        if not it["recaption_queued"]: break
        time.sleep(0.5)
    check("I re-caption keeps the hand focus", not it["recaption_queued"] and it["focus"] == {"x": 0.85, "y": 0.5, "source": "hand"}, it)
    req(I, "POST", "/api/focus", {"id": "subject-right", "reset": True})
    check("I reset: back to the captioner's box", item(I, "subject-right")["focus"] == {"x": 0.2, "y": 0.5, "source": "model"},
          item(I, "subject-right"))
finally:
    pi.terminate(); pi.wait(); fi.terminate()

# ---------------- J: automatic focus with no captioner, then focus_auto turned off ----------------
J = BASE + 7
pj = start("J", {"port": J, "ingress_peers": ["127.0.0.0/8"], "focus_auto": True, "_imgs": FD, "captioners": [],
                 "caption": {"enabled": False}})
try:
    for _ in range(60):
        it = item(J, "subject-right")
        if it and it["focus"]: break
        time.sleep(1)
    f = (it or {}).get("focus") or {}
    check("J automatic focus finds the subject", f.get("source") == "auto" and 0.75 < f["x"] < 0.95 and 0.35 < f["y"] < 0.65, it)
    rev = it["rev"]
    check("J 9:16 crop keeps the off-centre subject", red_share(J, "/img/subject-right/%s/clean/360x640.jpg" % rev) > 0.02)
    check("J 9:16 label keeps it too", red_share(J, "/img/subject-right/%s/label/1080x1920.jpg" % rev) > 0.02)
    check("J span: the portrait tile keeps it", red_share(J, "/img/subject-right/%s/span.jpg?layout=360x640+0+0,640x360+360+0p" % rev) > 0.01)
    req(J, "POST", "/api/focus", {"id": "subject-right", "x": 0.1, "y": 0.5})
    check("J hand focus on the left: the crop moves off the subject",
          red_share(J, "/img/subject-right/%s/clean/360x640.jpg" % item(J, "subject-right")["rev"]) == 0)
    check("J other pictures get an automatic focus too", (item(J, "test-0")["focus"] or {}).get("source") == "auto", item(J, "test-0"))
finally:
    pj.terminate(); pj.wait()
pj = start("J", {"port": J, "ingress_peers": ["127.0.0.0/8"], "focus_auto": False, "_imgs": FD, "captioners": [],
                 "caption": {"enabled": False}, "_fresh": False})
try:
    check("J focus_auto off: the automatic focus is not used", item(J, "test-0")["focus"] is None, item(J, "test-0"))
    check("J focus_auto off: the hand focus still is", (item(J, "subject-right")["focus"] or {}).get("source") == "hand",
          item(J, "subject-right"))
finally:
    pj.terminate(); pj.wait()

# ---------------- Q: quiet hours over HTTP: /current/ dimmed with its own ETag, /img/ not ----------------
def get_bytes(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse(); data = r.read(); hdrs = dict(r.getheaders()); c.close()
    return r.status, data, hdrs

def brightness(data):
    import io
    from PIL import Image, ImageStat
    return sum(ImageStat.Stat(Image.open(io.BytesIO(data)).convert("L")).mean)

Q = BASE + 9
now = time.time()
q_start, q_end = time.strftime("%H:%M", time.localtime(now - 7200)), time.strftime("%H:%M", time.localtime(now + 7200))
cfgQ = {"port": Q, "ingress_peers": ["127.0.0.0/8"], "captioners": [], "caption": {"enabled": False},
        "quiet_hours": {"start": q_start, "end": q_end}}
pq = start("Q", dict(cfgQ))
try:
    c = cur(Q)
    check("Q current.json: quiet, its window, no next change while held", c["quiet"] is True and c["next_change"] is None
          and c["paused"] is False and c["quiet_window"]["end"][11:16] == q_end and c["quiet_window"]["hold"] is True
          and c["quiet_window"]["dim"] == 0.6, c)
    page = req(Q, "GET", "/")[2]
    check("Q page: Quiet until", "Quiet until %s" % q_end in page and "Rotation: <b>quiet</b>" in page
          and 'data-act="next">' in page, page[page.find("<h2>Rotation"):][:600])
    img = "/img/%s/%s" % (c["id"], c["rev"])
    dimmed = {}
    for kind in ("clean", "label"):
        st, data, h = get_bytes(Q, "/current/%s/640x360.jpg" % kind)
        st2, plain, _ = get_bytes(Q, "%s/%s/640x360.jpg" % (img, kind))
        ratio = brightness(data) / brightness(plain)
        check("Q /current/%s dimmed, /img/ not" % kind, st == 200 and st2 == 200 and "-dim0.6" in h["ETag"]
              and 0.3 < ratio < 0.5, (st, st2, h.get("ETag"), ratio))
        dimmed[kind] = h["ETag"]
    st, _, h = get_bytes(Q, "/current/clean/640x360.jpg", {"If-None-Match": dimmed["clean"]})
    check("Q dimmed ETag answers 304", st == 304, st)
    st, j, *_ = req(Q, "POST", "/api/next", {}); c2 = cur(Q)
    check("Q Next works in quiet hours, still held", st == 200 and c2["id"] != c["id"] and c2["quiet"]
          and c2["next_change"] is None, c2)
    req(Q, "POST", "/api/show", {"id": c["id"]}); c = cur(Q)
    st, _, h = get_bytes(Q, "/current/clean/640x360.jpg"); dimmed["clean"] = h["ETag"]
finally:
    pq.terminate(); pq.wait()
# the same picture with quiet hours off: a different ETag for the same version
cfgQ.pop("quiet_hours")
pq = start("Q", dict(cfgQ, _fresh=False))
try:
    c3 = cur(Q)
    check("Q off: quiet false, no window, a next change again", c3["quiet"] is False and c3["quiet_window"] is None
          and c3["next_change"] and c3["version"] == c["version"], (c3, c["version"]))
    st, data, h = get_bytes(Q, "/current/clean/640x360.jpg", {"If-None-Match": dimmed["clean"]})
    st2, plain, _ = get_bytes(Q, "/img/%s/%s/clean/640x360.jpg" % (c3["id"], c3["rev"]))
    check("Q undimmed ETag differs from the dimmed one", st == 200 and h["ETag"] != dimmed["clean"]
          and "dim" not in h["ETag"] and abs(brightness(data) / brightness(plain) - 1) < 0.02, (st, h.get("ETag")))
    check("Q page: no quiet line when off", "Quiet" not in req(Q, "GET", "/")[2])
finally:
    pq.terminate(); pq.wait()

# ---------------- H: a client off this network cannot add screens or read the list ----------------
H = BASE + 5
ph = start("H", {"port": H, "lan_cidrs": ["10.0.0.0/8"], "captioners": [], "caption": {"enabled": False}})
try:
    st, j, *_ = req(H, "GET", "/current.json?screen=outsider")
    check("H screen from off-network still served", st == 200 and j["follow"] is True, (st, j))
    st, _, txt, hdrs = req(H, "GET", "/screens.json")
    check("H screens.json refused off-network, no CORS", st == 403 and "Access-Control-Allow-Origin" not in hdrs, (st, hdrs))
    check("H off-network screen not listed", "outsider" not in req(H, "GET", "/")[2])
    st, *_ = req(H, "POST", "/api/love", {"id": "test-0"}); check("H love refused off-network 403", st == 403, st)
    check("H page: heart disabled off-network, with the reason first", 'aria-pressed="false" disabled title="Only from your own network" title="Love it' in req(H, "GET", "/")[2])
finally:
    ph.terminate(); ph.wait()

# ---------------- I: ?pad= for gentle motion, around the sidecar's focus point ----------------
I = BASE + 7
dataI = os.path.join(S, "data-I")
shutil.rmtree(dataI, ignore_errors=True)
os.makedirs(os.path.join(dataI, "library"))
json.dump({"id": "test-0", "focus": {"x": 0.25, "y": 0.75, "source": "hand"}},
          open(os.path.join(dataI, "library", "test-0.json"), "w"))
pi = start("I", {"port": I, "captioners": [], "caption": {"enabled": False}, "_fresh": False})
try:
    # Masters first: a request racing the scanner's first master build can 500 (ensure_master shares
    # one .tmp file), which is not what this section tests.
    for _ in range(100):
        ms = os.listdir(os.path.join(dataI, "masters"))
        if len([m for m in ms if m.endswith(".jpg")]) == 4 and not any(m.endswith(".tmp") for m in ms): break
        time.sleep(0.2)
    from PIL import Image as _Image
    import io as _io

    def jpeg_size(path):
        c = http.client.HTTPConnection("127.0.0.1", I, timeout=30); c.request("GET", path)
        r = c.getresponse(); data = r.read(); h = dict(r.getheaders()); c.close()
        return r.status, (_Image.open(_io.BytesIO(data)).size if r.status == 200 else None), data, h

    st, size, _, h = jpeg_size("/current/clean/640x360.jpg?pad=1.2")
    check("I clean pad=1.2 is 1.2 times larger", st == 200 and size == (768, 432), (st, size))
    st, size, _, h2 = jpeg_size("/current/label/640x400.jpg?pad=1.3&s=1.35")
    check("I label pad=1.3 is 1.3 times larger", st == 200 and size == (832, 520), (st, size))
    _, _, plain, hp = jpeg_size("/current/clean/640x360.jpg")
    _, _, one, h1 = jpeg_size("/current/clean/640x360.jpg?pad=1.0")
    check("I pad=1.0 is today's picture, same ETag", plain == one and hp["ETag"] == h1["ETag"], (hp, h1))
    check("I padded ETag differs and names the pad", h["ETag"] != hp["ETag"] and "-p1.2-" in h["ETag"], h["ETag"])
    c = http.client.HTTPConnection("127.0.0.1", I, timeout=10)
    c.request("GET", "/current/clean/640x360.jpg?pad=1.2", headers={"If-None-Match": h["ETag"]})
    r = c.getresponse(); r.read(); c.close()
    check("I padded If-None-Match 304", r.status == 304, r.status)
    for bad in ("0.9", "1.31", "abc", "nan", "inf"):
        st, _, txt, _ = req(I, "GET", "/current/clean/640x360.jpg?pad=" + bad)
        check("I pad=%s refused 400" % bad, st == 400 and "pad" in txt, (st, txt))
    st, _, txt, _ = req(I, "GET", "/current/clean/7000x4000.jpg?pad=1.2")
    check("I pad past the size limit 400", st == 400 and "larger than 7680" in txt, (st, txt))
    st, _, txt, _ = req(I, "GET", "/current/clean/6000x3000.jpg?pad=1.28")
    check("I pad up to the size limit ok", st == 200, (st, txt[:80]))
    items = {i["id"]: i for i in lib(I)["items"]}
    st, size, _, hf = jpeg_size("/img/test-0/%s/clean/640x360.jpg?pad=1.2" % items["test-0"]["rev"])
    check("I /img padded: focus from the sidecar, revalidated not immutable",
          st == 200 and size == (768, 432) and "-f0.25,0.75" in hf["ETag"] and hf["Cache-Control"] == "no-cache", hf)
    st, _, _, hc = jpeg_size("/img/test-1/%s/label/640x360.jpg?pad=1.2" % items["test-1"]["rev"])
    check("I /img padded: no focus is the centre", st == 200 and "-f0.5,0.5" in hc["ETag"], hc)
    st, _, _, hi = jpeg_size("/img/test-1/%s/clean/640x360.jpg" % items["test-1"]["rev"])
    check("I /img unpadded stays immutable", "immutable" in hi["Cache-Control"] and "ETag" not in hi, hi)
    names = os.listdir(os.path.join(dataI, "cache", "test-0", items["test-0"]["rev"]))
    check("I padded cache file names the pad and focus", "clean-640x360-p1.2-f0.25,0.75.jpg" in names, names)
finally:
    pi.terminate(); pi.wait()

# ---------------- K: kids captions (?label=kids) and "Guess where?" (?reveal=) ----------------
K = BASE + 1
KIDS_FLAG = FAIL_FLAG + ".kids"
open(KIDS_FLAG, "w").close()  # kids captions fail at first, so the kids label has to fall back
fk = subprocess.Popen([sys.executable, os.path.join(HERE, "fakecap.py"), str(BASE + 3), FAIL_FLAG])
pk = start("K", {"port": K, "ingress_peers": ["127.0.0.0/8"], "rotation": {"min_minutes": 15, "max_minutes": 20},
                 "captioners": [{"name": "fake", "url": "http://127.0.0.1:%d/v1/chat/completions" % (BASE + 3),
                                 "model": "x", "timeout": 10}],
                 # a failed kids caption is tried again after about 7 seconds
                 "caption": {"enabled": True, "min_words": 30, "max_words": 45, "retry_failed_hours": 0.002,
                             "kids": {"enabled": True}}})
def img(port, path, etag=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("GET", path, headers={"If-None-Match": etag} if etag else {})
    r = c.getresponse(); body = r.read(); h = dict(r.getheaders()); c.close()
    return r.status, body, h
def kids_items(port):
    return {i["id"]: i for i in lib(port)["items"]}
try:
    for _ in range(60):  # the picture on screen is first in the kids queue
        L = kids_items(K); on = cur(K)["id"]
        if all(i["status"] == "captioned" for i in L.values()) and L[on]["kids_status"] == "failed": break
        time.sleep(0.5)
    check("K captions done, the kids caption on screen failed", all(i["status"] == "captioned" and i["kids_blurb"] == ""
                                                                   for i in L.values()) and L[on]["kids_status"] == "failed",
          [(i["status"], i["kids_status"]) for i in L.values()])
    lines = [l.split(" ", 2)[2] for l in open(os.path.join(S, "log-K.txt")).read().splitlines() if l.count(" ") >= 2]
    main_at = [n for n, l in enumerate(lines) if l.startswith("captioned ")]
    kids_at = [n for n, l in enumerate(lines) if l.startswith("kids caption attempt")]
    check("K kids captions queued behind every normal caption", len(main_at) == 4 and kids_at and kids_at[0] > main_at[-1],
          (main_at, kids_at[:3]))
    c0 = cur(K)
    check("K current.json: kids_blurb empty before there is one", c0["kids_blurb"] == "" and "reveal_at" not in c0, c0)
    st, kids_fb, _ = img(K, "/current/label/640x360.jpg?label=kids")
    st2, big, _ = img(K, "/current/label/640x360.jpg?s=1.3")
    st3, plain, _ = img(K, "/current/label/640x360.jpg")
    check("K ?label=kids falls back to the normal label, bigger type", st == st2 == st3 == 200 and kids_fb == big
          and kids_fb != plain, (st, st2, st3))
    st, *_ = img(K, "/img/%s/%s/label/640x360.jpg?label=kids" % (c0["id"], c0["rev"])); check("K /img kids fallback 200", st == 200, st)
    st, *_ = img(K, "/current/label/640x360.jpg?label=big"); check("K unknown label 400", st == 400, st)

    os.remove(KIDS_FLAG)
    for _ in range(120):  # after the failure the worker pauses 30 s, as it does after any failed caption
        L = kids_items(K)
        if all(i["kids_status"] == "captioned" for i in L.values()): break
        time.sleep(0.5)
    check("K kids captions written once the model answers", all(i["kids_status"] == "captioned"
          and i["kids_blurb"].startswith("Look at") and not i["kids_edited"] for i in L.values()),
          [(i["kids_status"], i["kids_blurb"][:20]) for i in L.values()])
    c1 = cur(K)
    check("K kids caption changes rev and version", c1["kids_blurb"].startswith("Look at") and c1["rev"] != c0["rev"]
          and c1["version"] != c0["version"] and c1["blurb"] == c0["blurb"], (c0["version"], c1["version"]))
    st, kids_lab, _ = img(K, "/current/label/640x360.jpg?label=kids")
    check("K ?label=kids now carries the kids caption", st == 200 and kids_lab != img(K, "/current/label/640x360.jpg?s=1.3")[1])
    st, *_ = img(K, "/img/%s/%s/label/640x360.jpg?label=kids" % (c0["id"], c0["rev"])); check("K stale rev 302 (kids)", st == 302, st)
    st, *_ = img(K, "/img/%s/%s/span.jpg?layout=640x360+0+0p&label=kids" % (c1["id"], c1["rev"])); check("K span with kids 200", st == 200, st)

    # the panel: hand edits win, an empty one brings a machine caption back
    cid = c1["id"]
    st, *_ = req(K, "POST", "/api/caption/kids", {"id": cid, "blurb": "A hand  written\nkids line."})
    it = kids_items(K)[cid]
    check("K kids hand edit", st == 200 and it["kids_blurb"] == "A hand written kids line." and it["kids_edited"], it)
    st, *_ = req(K, "POST", "/api/caption/kids", {"id": cid, "blurb": "x" * 401}); check("K kids too long 400", st == 400, st)
    st, *_ = req(K, "POST", "/api/caption/kids", {"id": cid, "blurb": 3}); check("K kids non-text 400", st == 400, st)
    st, *_ = req(K, "POST", "/api/caption/kids", {"id": "nope", "blurb": "x"}); check("K kids unknown 404", st == 404, st)
    req(K, "POST", "/api/caption", {"id": cid, "title": "T", "blurb": "Main by hand"})
    it = kids_items(K)[cid]
    check("K main hand edit keeps the kids caption", it["blurb"] == "Main by hand" and it["kids_blurb"].startswith("A hand"), it)
    req(K, "POST", "/api/recaption", {"id": cid})
    for _ in range(40):
        it = kids_items(K)[cid]
        if not it["recaption_queued"]: break
        time.sleep(0.5)
    check("K re-caption keeps the hand kids caption", it["blurb"].startswith("A field") and it["kids_blurb"].startswith("A hand")
          and it["kids_edited"], it)
    page = req(K, "GET", "/")[2]
    check("K page: kids caption shown and editable", "Kids caption" in page and 'id="kids-editor"' in page
          and "A hand written kids line." in page, "")
    req(K, "POST", "/api/caption/kids", {"id": cid, "blurb": ""})
    for _ in range(40):
        it = kids_items(K)[cid]
        if it["kids_status"] == "captioned": break
        time.sleep(0.5)
    check("K empty kids edit brings a machine one back", it["kids_blurb"].startswith("Look at") and not it["kids_edited"], it)

    # Guess where?: the clean picture until since + reveal, then the label; the ETag changes at the reveal
    req(K, "POST", "/api/show", {"id": cid}); t0 = time.time()
    st, veiled, h1 = img(K, "/current/label/640x360.jpg?reveal=5")
    clean = img(K, "/current/clean/640x360.jpg")[1]
    cv = req(K, "GET", "/current.json?reveal=5")[1]
    check("K reveal: clean before", st == 200 and veiled == clean and cv["revealed"] is False
          and cv["version"] == cur(K)["version"] + ".veiled" and cv["reveal_at"], cv)
    st, *_ = img(K, "/current/label/640x360.jpg?reveal=5", h1["ETag"]); check("K reveal: 304 before", st == 304, st)
    time.sleep(max(0, t0 + 5.5 - time.time()))
    st, shown, h2 = img(K, "/current/label/640x360.jpg?reveal=5", h1["ETag"])
    cv = req(K, "GET", "/current.json?reveal=5")[1]
    check("K reveal: label after, new ETag", st == 200 and shown == img(K, "/current/label/640x360.jpg")[1]
          and h2["ETag"] != h1["ETag"], (st, h1.get("ETag"), h2.get("ETag")))
    check("K reveal: version changes at the reveal", cv["revealed"] is True and cv["version"] == cur(K)["version"], cv)
    st, kshown, _ = img(K, "/current/label/640x360.jpg?label=kids&reveal=5")
    check("K reveal with the kids label", st == 200 and kshown == img(K, "/current/label/640x360.jpg?label=kids")[1], st)
    st, zero, _ = img(K, "/current/label/640x360.jpg?reveal=0"); check("K reveal=0 is the label", st == 200 and zero == shown, st)
    for bad in ("abc", "-1", "1.5", "86401"):
        st, *_ = img(K, "/current/label/640x360.jpg?reveal=" + bad); check("K reveal=%s 400" % bad, st == 400, st)
    check("K current.json bad reveal 400", req(K, "GET", "/current.json?reveal=x")[0] == 400)
finally:
    pk.terminate(); pk.wait(); fk.terminate()

# ---------------- M: mood-aware picks: tags from the captioner, a weather entity, the weighting ----------------
# The fake captioner tags by colour (test-0 red: warm, cosy, rain; test-1 green: forest, calm, cloud;
# test-2 blue: cool, sea, snow; test-3 yellow: bright, sun, desert). A fake Home Assistant API answers
# the weather entity with rain at 6 °C, so the red picture is the only one that fits the moment.
M = BASE + 7
fm = subprocess.Popen([sys.executable, os.path.join(HERE, "fakecap.py"), str(BASE + 3), FAIL_FLAG])
WEATHER = {"entity_id": "weather.test_home", "state": "rainy", "attributes": {"temperature": 42.8, "temperature_unit": "°F"}}

class FakeHA(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer t":
            body, code = b'{"message": "Unauthorized"}', 401
        elif self.path == "/api/states/weather.test_home":
            body, code = json.dumps(WEATHER).encode(), 200
        else:
            body, code = b'{"message": "Entity not found."}', 404
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

ha = ThreadingHTTPServer(("127.0.0.1", BASE + 1), FakeHA)
threading.Thread(target=ha.serve_forever, daemon=True).start()
cfgM = {"port": M, "ingress_peers": ["127.0.0.0/8"], "rotation": {"min_minutes": 15, "max_minutes": 20},
        "location": {"latitude": 53.35, "longitude": -6.26},
        "captioners": [{"name": "fake", "url": "http://127.0.0.1:%d/v1/chat/completions" % (BASE + 3), "model": "x", "timeout": 10}],
        "caption": {"enabled": True, "min_words": 30, "max_words": 45},
        "mood": {"level": "strong", "weather_entity": "weather.test_home", "api_url": "http://127.0.0.1:%d/api" % (BASE + 1),
                 "api_token": "t", "refresh_minutes": 1}}
RED, GREEN, BLUE, YELLOW = ["warm", "cosy", "rain"], ["forest", "calm", "cloud"], ["cool", "sea", "snow"], ["bright", "sun", "desert"]
pm = start("M", dict(cfgM))
try:
    for _ in range(60):
        if all(i["status"] == "captioned" for i in lib(M)["items"]): break
        time.sleep(1)
    tags = {i["id"]: i["tags"] for i in lib(M)["items"]}
    check("M tags from the captioner, in the sidecar and library.json",
          tags == {"test-0": RED, "test-1": GREEN, "test-2": BLUE, "test-3": YELLOW}, tags)
    sc = json.load(open(os.path.join(S, "data-M", "library", "test-0.json")))
    check("M sidecar carries tags", sc.get("tags") == RED, sc)
    for _ in range(40):
        m = lib(M)["mood"]
        if m["weather"]: break
        time.sleep(0.25)
    check("M weather read through the API with the token, °F converted",
          m["level"] == "strong" and m["weather"] and m["weather"]["condition"] == "rainy"
          and abs(m["weather"]["temperature_c"] - 6) < 0.1 and m["weather_entity"] == "weather.test_home", m)
    ctx = set(m["context"])
    check("M the moment: rain, cosy, warm, plus a time of day and a season",
          {"rain", "cosy", "warm"} <= ctx and ctx & {"day", "night", "dawn", "dusk"} and ctx & {"spring", "summer", "autumn", "winter"}, m["context"])
    # The pull: the red picture (the only match) leads most shuffles; every picture still comes up once per shuffle.
    state_path = os.path.join(S, "data-M", "state.json")
    firsts, seen = [], collections.Counter()
    for _ in range(64):
        c = req(M, "POST", "/api/next", {})[1]
        seen[c["id"]] += 1
        if len(json.load(open(state_path))["bag"]) == 3:  # a fresh shuffle of 4, one just taken
            firsts.append((c["id"], c["mood_match"]))
    check("M every picture once per shuffle (no starvation)", len(seen) == 4 and all(v >= 14 for v in seen.values()), seen)
    red_first = sum(1 for i, _ in firsts if i == "test-0")
    check("M strong: the matching picture leads most shuffles", len(firsts) >= 14 and red_first >= 9, (len(firsts), red_first))
    check("M mood_match names the matched tags", firsts and all(mm == ["cosy", "rain", "warm"] for i, mm in firsts if i == "test-0")
          and all(mm == [] for i, mm in firsts if i != "test-0"), firsts)
    check("M log: the match on the rotate line", "[mood: cosy, rain, warm]" in open(os.path.join(S, "log-M.txt")).read())
    page = req(M, "GET", "/")[2]
    check("M page: why this one, the mood line, tags on the cards, tag boxes in the editor",
          "Why this one:" in page and "Mood: <b>strong</b>" in page and "weather.test_home says rainy, 6 °C" in page
          and "warm · cosy · rain" in page and 'name="tags" value="cosy"' in page, page[-3000:])
    # tags edited by hand: folded onto the vocabulary, unknown words refused, left out means unchanged
    st, j, txt, _ = req(M, "POST", "/api/caption", {"id": "test-1", "title": "T", "blurb": "hand", "tags": ["Fog", "cosy", "ocean"]})
    it = item(M, "test-1")
    check("M hand-edited tags", st == 200 and it["tags"] == ["fog", "cosy", "sea"] and it["edited"] and it["blurb"] == "hand", (st, txt, it))
    st, _, txt, _ = req(M, "POST", "/api/caption", {"id": "test-1", "blurb": "hand", "tags": ["purple"]})
    check("M unknown tag 400 names it", st == 400 and "purple" in txt, (st, txt))
    st, *_ = req(M, "POST", "/api/caption", {"id": "test-1", "blurb": "hand", "tags": "fog"}); check("M tags not a list 400", st == 400, st)
    req(M, "POST", "/api/caption", {"id": "test-1", "blurb": "hand two"}); it = item(M, "test-1")
    check("M an edit without tags keeps them", it["tags"] == ["fog", "cosy", "sea"] and it["blurb"] == "hand two", it)
    req(M, "POST", "/api/show", {"id": "test-2"}); c = cur(M)
    check("M current.json: tags, and no match for a hand pick", c["tags"] == BLUE and c["mood_match"] == [], c)
    check("M page: you picked it", "you picked it" in req(M, "GET", "/")[2])
finally:
    pm.terminate(); pm.wait()

# restart M with the weather entity gone: pictures captioned before tags existed get them at the back of
# the queue (the caption, hand-edited or not, untouched), and the picks go by the time of day and season.
ha.shutdown(); ha.server_close()
for iid in ("test-1", "test-3"):
    p = os.path.join(S, "data-M", "library", iid + ".json"); sc = json.load(open(p)); del sc["tags"]; json.dump(sc, open(p, "w"))
pm = start("M", dict(cfgM, _fresh=False))
try:
    for _ in range(120):
        its = {i["id"]: i for i in lib(M)["items"]}
        if its["test-1"]["tags"] and its["test-3"]["tags"]: break
        time.sleep(0.5)
    check("M backfill: tags return, the captions untouched", its["test-3"]["tags"] == YELLOW and its["test-3"]["blurb"].startswith("A field")
          and not its["test-3"]["edited"] and its["test-1"]["tags"] == GREEN and its["test-1"]["edited"] and its["test-1"]["blurb"] == "hand two",
          {k: (v["tags"], v["edited"], v["blurb"][:20]) for k, v in its.items()})
    logM = open(os.path.join(S, "log-M.txt")).read()
    check("M backfill logged", "tagged test-3: bright, sun, desert" in logM, logM[-800:])
    m = lib(M)["mood"]
    check("M weather entity unreachable: said so, the moment goes by time and season",
          m["weather"] is None and m["weather_error"] and not ({"rain", "cosy", "warm"} & set(m["context"])) and m["context"], m)
    check("M page: weather not read yet", "weather.test_home: not read yet" in req(M, "GET", "/")[2])
    check("M log: cannot read the entity", "cannot read weather.test_home" in logM, logM[-800:])
finally:
    pm.terminate(); pm.wait(); fm.terminate()

# M with mood off: nothing matched, no "Why this one", the tags still listed
pm = start("M", dict(cfgM, _fresh=False, mood={"level": "off"}))
try:
    c = cur(M); m = lib(M)["mood"]; page = req(M, "GET", "/")[2]
    check("M off: no match reported", c["mood_match"] == [] and c["tags"] and m["level"] == "off" and m["context"] == [], (c, m))
    check("M off: no why line, tags shown", "Why this one" not in page and "Mood: <b>off</b>" in page and "Tags: " in page, page[-3000:])
    seen = set()
    for _ in range(8):
        seen.add(req(M, "POST", "/api/next", {})[1]["id"])
    check("M off: still rotates", len(seen) == 4, seen)
finally:
    pm.terminate(); pm.wait()

print("\n%d passed, %d failed (logs in %s)" % (ok, fail, S))
if not fail:
    shutil.rmtree(S, ignore_errors=True)
sys.exit(1 if fail else 0)
