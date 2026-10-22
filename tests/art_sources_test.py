"""Tests for the open-access art sources (matchy/app/artsources.py, Library.sync_art), offline: every
API answer is a fixture from tests/fixtures/art/ and every image is generated here.

Part 1 runs the adapters and Library.sync_art against a fake HTTP client. Part 2 starts the real
engine against a local server that answers like the three APIs, with the fake captioner, and checks
a fresh install fills its library with credited works, writes their facts into the sidecars, and
keeps to the day's budget after a restart.

    python3 tests/art_sources_test.py      # needs Pillow; MATCHY_TEST_PORT moves the ports (18176..18178)
"""
import datetime as dt, http.client, io, json, os, random, re, shutil, subprocess, sys, tempfile, threading, time
import urllib.error, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "matchy", "app")
FIX = os.path.join(HERE, "fixtures", "art")
sys.path.insert(0, APP)

from PIL import Image  # noqa: E402

import artsources  # noqa: E402
from caption import Captioner  # noqa: E402
from library import Library  # noqa: E402

BASE = int(os.environ.get("MATCHY_TEST_PORT", "18160"))
ENGINE_PORT, SERVER_PORT, CAP_PORT = BASE + 16, BASE + 17, BASE + 18
S = tempfile.mkdtemp(prefix="matchy-art-")
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("PASS", name)
    else:
        fail += 1; print("FAIL", name, extra)


def fixture(name, base):
    return json.loads(open(os.path.join(FIX, name), encoding="utf-8").read().replace("{BASE}", base))


def jpeg(w, h, colour=(120, 90, 60)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "JPEG", quality=80)
    return buf.getvalue()


# Image sizes the fake servers hand out, by a piece of the path. The Saint George image claims
# 2000x3200 in the API (1686x2698 at the width asked for) but is served smaller: the decoded size wins.
IMAGES = {"fx-aic-childs-bath": (1686, 2584), "fx-aic-saint-george": (843, 1349), "fx-aic-water-lilies": (1686, 1613),
          "DT1567": (3000, 2400), "3840px-Misty_valley": (3840, 2560)}


def image_for(path):
    for key, size in IMAGES.items():
        if key in path:
            return jpeg(*size)
    return None


def answer(method, path, base):
    """(status, body bytes, content type) for a request to the fake APIs; shared by both parts."""
    u = urllib.parse.urlparse(path)
    if method == "POST" and u.path == "/api/v1/search":
        return 200, json.dumps(fixture("aic-search.json", base)).encode(), "application/json"
    if u.path == "/met/v1.1/search":
        return 200, json.dumps(fixture("met-search.json", base)).encode(), "application/json"
    m = re.match(r"^/met/v1/objects/(\d+)$", u.path)
    if m:
        f = os.path.join(FIX, "met-object-%s.json" % m.group(1))
        if not os.path.exists(f):
            return 404, b'{"message": "ObjectID not found"}', "application/json"
        return 200, json.dumps(fixture(os.path.basename(f), base)).encode(), "application/json"
    if u.path == "/w/api.php":
        return 200, json.dumps(fixture("potd.json", base)).encode(), "application/json"
    if u.path.startswith(("/iiif/", "/met/images/", "/upload/")):
        data = image_for(u.path)
        if data:
            return 200, data, "image/jpeg"
    return 404, b"not found", "text/plain"


class FakeClient(artsources.Client):
    """Answers from the fixtures instead of the network, and records what was asked."""
    BASE_URL = "http://fixtures.invalid"

    def __init__(self, routes=None, **kw):
        super().__init__(pause=0, **kw)
        self.calls = []
        self.routes = routes or {}

    def get(self, url, data=None, headers=None, max_bytes=60_000_000):
        self.calls.append((url, data, dict(headers or {})))
        for prefix, body in self.routes.items():
            if url.startswith(prefix):
                return body
        path = url[len(self.BASE_URL):]
        st, body, _ = answer("POST" if data is not None else "GET", path, self.BASE_URL)
        if st != 200:
            raise urllib.error.HTTPError(url, st, "fixture", {}, None)
        return body


B = FakeClient.BASE_URL
CFG = {"aic": {"enabled": True, "per_day": 3, "min_px": 2560, "batch": 20, "base_url": B + "/api/v1"},
       "met": {"enabled": True, "per_day": 3, "min_px": 2560, "lookups": 15, "search": {"isHighlight": "true"},
               "base_url": B + "/met"},
       "potd": {"enabled": True, "per_day": 1, "min_px": 2560, "days_back": 7, "language": "en",
                "api_url": B + "/w/api.php"}}

# ---------------------------------------------------------------- 1. adapters on fixtures
cl = FakeClient(contact="maintainer@example.org")
check("UA names the project and the contact", artsources.PROJECT_URL in cl.ua and "maintainer@example.org" in cl.ua
      and cl.ua.startswith("MatchyWallpapers") and "bot" in cl.ua, cl.ua)

out = list(artsources.aic(CFG["aic"], cl, random.Random(1), set()))
works = [w for w in out if not w.get("skip")]
ids = [w["id"] for w in works]
check("AIC: public-domain works with an image only", ids == ["aic-16568", "aic-111642", "aic-15468"], ids)
check("AIC: the rest marked to skip, with why", [(w["id"], w["skip"]) for w in out if w.get("skip")]
      == [("aic-111628", "not public domain"), ("aic-4575", "no usable image")], out)
url, body, hdr = cl.calls[0]
q = json.loads(body)
check("AIC: the docs' random public-domain query", url == B + "/api/v1/search" and q["resources"] == "artworks"
      and {"term": {"is_public_domain": True}} in q["query"]["function_score"]["query"]["bool"]["filter"]
      and "random_score" in q["query"]["function_score"] and q["limit"] == 20, q)
check("AIC: AIC-User-Agent header", hdr.get("AIC-User-Agent", "").startswith("matchy-wallpapers (")
      and hdr.get("Content-Type") == "application/json", hdr)
bath = works[1]
check("AIC: IIIF at 1686 wide from config.iiif_url", bath["image_url"] == B + "/iiif/2/fx-aic-childs-bath/full/1686,/0/default.jpg"
      and bath["size"] == (1686, 2584), bath)
check("AIC: facts, label line and credit", bath["facts"] == {"artist": "Mary Cassatt", "date": "1893", "medium": "Oil on canvas",
      "place": "United States"} and bath["place"] == "Mary Cassatt · 1893" and bath["credit"] == "The Art Institute of Chicago · CC0"
      and bath["title"] == "The Child's Bath" and bath["source_url"] == "https://www.artic.edu/artworks/111642", bath)
check("AIC: seen ids are not offered again", [w["id"] for w in artsources.aic(CFG["aic"], cl, random.Random(1), {
      "aic-16568", "aic-111628", "aic-4575"})] == ["aic-111642", "aic-15468"])

cl = FakeClient()
out = list(artsources.met(CFG["met"], cl, random.Random(2), set()))
works = [w for w in out if not w.get("skip")]
check("Met: only the public-domain object with an image", [w["id"] for w in works] == ["met-436535"], works)
check("Met: the rest marked to skip, with why", sorted((w["id"], w["skip"]) for w in out if w.get("skip")) == [
      ("met-1", "not found"), ("met-437133", "not public domain"), ("met-45734", "no image")], out)
search = urllib.parse.urlparse(cl.calls[0][0])
sq = urllib.parse.parse_qs(search.query)
check("Met: v1.1 search, q=*, with images, highlights", search.path == "/met/v1.1/search" and sq["q"] == ["*"]
      and sq["hasImages"] == ["true"] and sq["isHighlight"] == ["true"] and sq["limit"] == ["500"], cl.calls[0][0])
check("Met: a 404 object is skipped, the rest asked one at a time",
      sorted(c[0].rsplit("/", 1)[1] for c in cl.calls[1:]) == ["1", "436535", "437133", "45734"], cl.calls)
w = works[0]
check("Met: facts, label line and credit", w["facts"] == {"artist": "Vincent van Gogh", "date": "1889", "medium": "Oil on canvas",
      "place": ""} and w["place"] == "Vincent van Gogh · 1889" and w["credit"] == "The Metropolitan Museum of Art · metmuseum.org · CC0"
      and w["image_url"] == B + "/met/images/DT1567.jpg" and w["size"] is None, w)
null_then = FakeClient(routes={B + "/met/v1.1/search?q=%2A&hasImages=true&isHighlight=true&offset=0": json.dumps(
    {"total": 1, "objectIDs": [436535]}).encode(), B + "/met/v1.1/search": b'{"total": 0, "objectIDs": null}'})
works = [w for w in artsources.met(CFG["met"], null_then, random.Random(5), set()) if not w.get("skip")]
check("Met: an offset past the end starts again from the top", [w["id"] for w in works] == ["met-436535"]
      and "offset=0" in null_then.calls[1][0], null_then.calls)
cl = FakeClient()
list(artsources.met(dict(CFG["met"], search={"isHighlight": ""}), cl, random.Random(2), set()))
check("Met: an empty search value drops that filter", "isHighlight" not in cl.calls[0][0], cl.calls[0][0])
chair = {"objectID": 200, "isPublicDomain": True, "primaryImage": B + "/met/images/chair.jpg", "title": "Armchair",
         "classification": "", "objectName": "Armchair"}
cl = FakeClient(routes={B + "/met/v1.1/search": json.dumps({"total": 2, "objectIDs": [436535, 200]}).encode(),
                        B + "/met/v1/objects/200": json.dumps(chair).encode()})
out = list(artsources.met(dict(CFG["met"], media=["Paintings", "Drawings"]), cl, random.Random(4), set()))
sq = urllib.parse.parse_qs(urllib.parse.urlparse(cl.calls[0][0]).query)
check("Met media: the search asks for one medium of the list", sq.get("medium") in (["Paintings"], ["Drawings"]), cl.calls[0][0])
check("Met media: the painting passes, the chair is skipped",
      [w["id"] for w in out if not w.get("skip")] == ["met-436535"]
      and [(w["id"], w["skip"]) for w in out if w.get("skip")] == [("met-200", "not a painting or drawing")], out)
check("Met media: picture kinds by classification or object name",
      [artsources.met_is_picture(o) for o in ({"classification": "Paintings-Panels"}, {"objectName": "Watercolor"},
                                                {"classification": "Drawings"}, {"objectName": "Necklace, broad collar"},
                                                {"classification": "Arms and Armor"}, {})]
      == [True, True, True, False, False, False])
cl = FakeClient()
list(artsources.met(dict(CFG["met"], media=[]), cl, random.Random(2), set()))
check("Met media: an empty list asks for no medium", "medium=" not in cl.calls[0][0], cl.calls[0][0])

cl = FakeClient()
day = dt.date(2026, 10, 20)
works = list(artsources.potd(CFG["potd"], cl, random.Random(3), {"potd-20261019"}, today=day))
check("POTD: one API call per day not seen yet", [w["id"] for w in works] == ["potd-20261020", "potd-20261018", "potd-20261017",
      "potd-20261016", "potd-20261015", "potd-20261014"] and len(cl.calls) == 6, [w["id"] for w in works])
pq = urllib.parse.parse_qs(urllib.parse.urlparse(cl.calls[0][0]).query)
check("POTD: Action API with maxlag, the day's template, a 3840 thumbnail",
      pq["generator"] == ["images"] and pq["titles"] == ["Template:Potd/2026-10-20"] and pq["maxlag"] == ["5"]
      and pq["iiurlwidth"] == ["3840"] and "extmetadata" in pq["iiprop"][0], pq)
w = works[0]
check("POTD: author · licence · Wikimedia Commons, HTML stripped", w["credit"] == "Example Photographer · CC BY-SA 4.0 · Wikimedia Commons"
      and w["title"] == "Misty valley at dawn" and w["context"].startswith("English: Morning fog lying in a wooded valley")
      and w["size"] == (3840, 2560) and "3840px-" in w["image_url"] and w["artwork"] is False, w)
cl = FakeClient(routes={B + "/w/api.php": json.dumps(fixture("potd-none.json", B)).encode()})
check("POTD: a day with no picture yields nothing", list(artsources.potd(dict(CFG["potd"], days_back=1), cl, random.Random(), set(), today=day)) == [])
cl = FakeClient(routes={B + "/w/api.php": b'{"error": {"code": "maxlag", "info": "Waiting for a database server: 6 seconds lagged."}}'})
try:
    list(artsources.potd(CFG["potd"], cl, random.Random(), set(), today=day)); raised = ""
except RuntimeError as e:
    raised = str(e)
check("POTD: maxlag is an error for this check, not retried in a loop", "lagged" in raised and len(cl.calls) == 1, raised)

check("clean: tags, entities, whitespace", artsources.clean("<b>Caf&eacute;</b>\n  terrace ") == "Café terrace")
check("clean: cut at a word", artsources.clean("one two three four", 9) == "one two…")

# ---------------------------------------------------------------- 1b. Library.sync_art
data = os.path.join(S, "lib")
lib = Library({"data_dir": data, "folders": [], "art_sources": dict(CFG, contact="", pause_seconds=0)})
orig_client = artsources.Client
artsources.Client = lambda *a, **k: FakeClient()
try:
    new = lib.sync_art(random.Random(4))
finally:
    artsources.Client = orig_client
check("sync: one per source passes 2560 px", sorted(new) == sorted(["aic-111642", "met-436535", "potd-" + time.strftime(
      "%Y%m%d", time.gmtime())]), new)
skipped = lib.art_skipped
check("sync: too small by the API's numbers, no download", "under 2560" in skipped.get("aic-16568", {}).get("reason", ""), skipped)
check("sync: too small once decoded (843x1349)", "843x1349" in skipped.get("aic-15468", {}).get("reason", ""), skipped)
r = lib.remote["aic-111642"]
check("sync: remote.json keeps facts, credit, collection, size", r["kind"] == "aic" and r["facts"]["artist"] == "Mary Cassatt"
      and r["credit"] == "The Art Institute of Chicago · CC0" and r["collection"] == "Art Institute of Chicago"
      and r["size"] == "1686x2584" and os.path.exists(os.path.join(data, "originals", "aic-111642.jpg")), r)
check("sync: POTD keeps its licence", lib.remote[[i for i in new if i.startswith("potd")][0]].get("licence") == "CC BY-SA 4.0")
items = lib.remote_items()
check("sync: items carry facts and the source page", items["met-436535"]["meta"]["facts"]["date"] == "1889"
      and items["met-436535"]["meta"]["artwork"] is True and items["met-436535"]["meta"]["source_url"].endswith("/436535"))
fc = FakeClient()
artsources.Client = lambda *a, **k: fc
try:
    lib2 = Library({"data_dir": data, "folders": [], "art_sources": dict(CFG, contact="", pause_seconds=0)})
    again = lib2.sync_art(random.Random(4))
finally:
    artsources.Client = orig_client
check("sync: skipped works remembered across restarts", {"aic-16568", "aic-111628", "met-437133", "met-1"} <= set(lib2.art_skipped),
      sorted(lib2.art_skipped))
check("sync: AIC and Met (1 of 3) search again but skip what they know; POTD (1 of 1) asks nothing",
      again == [] and [c[0].split("?")[0] for c in fc.calls] == [B + "/api/v1/search", B + "/met/v1.1/search"], (again, fc.calls))
lib3 = Library({"data_dir": data, "folders": [], "art_sources": {k: dict(v, per_day=1) for k, v in CFG.items()}})
fc = FakeClient()
artsources.Client = lambda *a, **k: fc
try:
    lib3.sync_art(random.Random(4))
finally:
    artsources.Client = orig_client
check("sync: no request at all once every source has its day's budget", fc.calls == [], fc.calls)

class Throttled(FakeClient):
    def get(self, url, data=None, headers=None, max_bytes=60_000_000):
        body = super().get(url, data, headers, max_bytes)
        if "/images/" in url:
            raise urllib.error.HTTPError(url, 429, "throttled", {}, None)
        return body


throttled = Throttled()
artsources.Client = lambda *a, **k: throttled
try:
    lib4 = Library({"data_dir": os.path.join(S, "lib4"), "folders": [], "art_sources": {"met": dict(CFG["met"])}})
    got = lib4.sync_art(random.Random(4))
finally:
    artsources.Client = orig_client
check("sync: a 429 on an image stops that source, nothing remembered as unusable",
      got == [] and "met-436535" not in lib4.art_skipped and sum("/images/" in c[0] for c in throttled.calls) == 1, throttled.calls)

# ---------------------------------------------------------------- 1c. the captioner is told the facts
cap = Captioner({"caption": {"min_words": 5, "max_words": 60, "forbidden_titles": []}, "captioners": []})
meta = items["aic-111642"]["meta"]
p = cap.prompt(meta)
check("prompt: artist, date, medium, place of origin; no 'Place:' line", "Artist: Mary Cassatt" in p and "Date: 1893" in p
      and "Medium: Oil on canvas" in p and "Place of origin: United States" in p and "- Place:" not in p
      and "work of art" in p, p)
problems, _ = cap.check(meta, meta["title"], "Cassatt paints a quiet interior in soft light, the Oil on canvas texture plain to see.")
check("grounding: facts allow the artist and a derived adjective", problems == [], problems)
problems, _ = cap.check(meta, meta["title"], "Cassatt painted this in Paris after meeting Degas there.")
check("grounding: names not in the facts are still caught", problems and "Degas" in problems[0] and "Paris" in problems[0], problems)

# ---------------------------------------------------------------- 2. the engine, fresh install, one local "internet"
requests_seen = []


class API(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def handle_one(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        requests_seen.append((method, self.path, self.headers.get("User-Agent", ""), self.headers.get("AIC-User-Agent", "")))
        st, body, ctype = answer(method, self.path, "http://127.0.0.1:%d" % SERVER_PORT)
        self.send_response(st); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self): self.handle_one("GET")
    def do_POST(self): self.handle_one("POST")


srv = ThreadingHTTPServer(("127.0.0.1", SERVER_PORT), API)
threading.Thread(target=srv.serve_forever, daemon=True).start()
fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fakecap.py"), str(CAP_PORT), os.path.join(S, "no-flag")])
local = "http://127.0.0.1:%d" % SERVER_PORT
cfg = {"port": ENGINE_PORT, "bind": "127.0.0.1", "data_dir": os.path.join(S, "data"), "folders": [],
       "ingress_peers": ["127.0.0.0/8"],
       "captioners": [{"name": "fake", "url": "http://127.0.0.1:%d/v1/chat/completions" % CAP_PORT, "model": "x", "timeout": 10}],
       "caption": {"enabled": True, "min_words": 30, "max_words": 45, "spelling": "British"},
       "art_sources": {"pause_seconds": 0, "contact": "maintainer@example.org",
                       "aic": {"enabled": True, "per_day": 1, "min_px": 2560, "base_url": local + "/api/v1"},
                       "met": {"enabled": True, "per_day": 1, "base_url": local + "/met"},
                       "potd": {"enabled": True, "api_url": local + "/w/api.php"}}}
cfg_path = os.path.join(S, "cfg.json")
json.dump(cfg, open(cfg_path, "w"))
log_path = os.path.join(S, "engine.log")


def req(method, path):
    c = http.client.HTTPConnection("127.0.0.1", ENGINE_PORT, timeout=10)
    c.request(method, path)
    r = c.getresponse(); body = r.read(); c.close()
    return r.status, body


def library():
    st, body = req("GET", "/library.json")
    return {i["id"]: i for i in json.loads(body)["items"]} if st == 200 else {}


def start_engine():
    proc = subprocess.Popen([sys.executable, os.path.join(APP, "engine.py")], env=dict(os.environ, WE_CONFIG=cfg_path),
                            stdout=open(log_path, "a"), stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            req("GET", "/healthz"); break
        except OSError:
            time.sleep(0.2)
    return proc


today = "potd-" + time.strftime("%Y%m%d", time.gmtime())
want = {"aic-111642", "met-436535", today}
pe = start_engine()
try:
    L = {}
    for _ in range(90):
        L = library()
        if want <= set(L) and all(L[i]["status"] == "captioned" for i in want):
            break
        time.sleep(1)
    check("engine: fresh install fills the library from all three", want == set(L), sorted(L))
    check("engine: each captioned", all(L.get(i, {}).get("status") == "captioned" for i in want),
          {i: L.get(i, {}).get("status") for i in want})
    a, m, p = L.get("aic-111642", {}), L.get("met-436535", {}), L.get(today, {})
    check("engine: AIC label fields", a.get("title") == "The Child's Bath" and a.get("place") == "Mary Cassatt · 1893"
          and a.get("credit") == "The Art Institute of Chicago · CC0", a)
    check("engine: Met label fields", m.get("title") == "Wheat Field with Cypresses" and m.get("credit", "").startswith(
          "The Metropolitan Museum of Art"), m)
    check("engine: POTD label fields", p.get("title") == "Misty valley at dawn"
          and p.get("credit") == "Example Photographer · CC BY-SA 4.0 · Wikimedia Commons", p)
    sc = json.load(open(os.path.join(cfg["data_dir"], "library", "aic-111642.json")))
    check("engine: sidecar has the facts and the source page", sc.get("facts", {}).get("medium") == "Oil on canvas"
          and sc["facts"]["artist"] == "Mary Cassatt" and sc.get("source_url", "").endswith("/111642") and sc.get("blurb"), sc)
    st, body = req("GET", "/img/met-436535/original.jpg")
    st2, _ = req("GET", "/img/%s/%s/label/1280x720.jpg" % ("aic-111642", a.get("rev")))
    check("engine: original and label render", st == 200 and st2 == 200, (st, st2))
    uas = {r[2] for r in requests_seen}
    check("engine: every request carries the project User-Agent with the contact",
          uas and all(artsources.PROJECT_URL in u and "maintainer@example.org" in u for u in uas), uas)
    aic_reqs = [r for r in requests_seen if r[1].startswith(("/api/v1/", "/iiif/"))]
    check("engine: AIC requests carry AIC-User-Agent", aic_reqs and all(r[3].startswith("matchy-wallpapers (") for r in aic_reqs),
          aic_reqs)
    check("engine: works without a usable image never downloaded or added",
          not any("water-lilies" in r[1] for r in requests_seen) and "aic-16568" not in L and "aic-15468" not in L
          and "met-45734" not in L, sorted(L))
finally:
    pe.terminate(); pe.wait()

before = len(requests_seen)
pe = start_engine()
try:
    time.sleep(3)
    check("engine: restart the same day sends nothing once the budget is met", len(requests_seen) == before,
          requests_seen[before:])
    check("engine: restart keeps the works", want <= set(library()), sorted(library()))
finally:
    pe.terminate(); pe.wait(); fake.terminate(); srv.shutdown()

print("\n%d passed, %d failed (logs in %s)" % (ok, fail, S))
if not fail:
    shutil.rmtree(S, ignore_errors=True)
sys.exit(1 if fail else 0)
