"""Integration tests for the Matchy engine: rotation, quiet hours, the panel controls, access tiers, request
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
    # named like a picture, but not one Pillow can decode
    open(os.path.join(S, "imgs-big", "broken.jpg"), "wb").write(b"not a picture at all " * 1000)


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
finally:
    ph.terminate(); ph.wait()

print("\n%d passed, %d failed (logs in %s)" % (ok, fail, S))
if not fail:
    shutil.rmtree(S, ignore_errors=True)
sys.exit(1 if fail else 0)
