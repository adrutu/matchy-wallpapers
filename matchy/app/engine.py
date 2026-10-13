#!/usr/bin/env python3
"""Wallpaper engine: one rotating, captioned wallpaper served to every screen.

Config: JSON file at $WE_CONFIG (default /config/config.json); WE_PORT / WE_DATA_DIR override.
Stdlib HTTP server + Pillow. See README.md for the API.
"""
import copy
import datetime as dt
import hashlib
import html
import ipaddress
import json
import os
import random
import re
import sys
import threading
import time
import traceback
import urllib.parse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render  # noqa: E402
from caption import Captioner  # noqa: E402
from library import Library, log, read_json, rev_of, write_json  # noqa: E402

DEFAULTS = {
    "port": 8160,
    "bind": "0.0.0.0",
    "data_dir": "/data",
    "master_max_px": 3840,
    "cache_max_mb": 2048,
    "render_threads": 2,
    "rotation": {"min_minutes": 15, "max_minutes": 20, "first_id": "", "caption_priority": []},
    "folders": [],
    "folder_scan_minutes": 10,
    "dedupe": {"avoid_prefixes": [], "ladder_prefer": "4k"},
    "static_manifest": {"enabled": False, "refresh_hours": 24},
    "daily": {"enabled": False, "check_minutes": 60, "id_prefix": "bing"},
    "captioners": [],
    "caption": {"enabled": True, "language": "English", "spelling": "", "min_words": 30, "max_words": 45,
                "max_tokens": 300, "temperature": 0.4, "image_px": 1024, "retry_failed_hours": 6,
                "forbidden_titles": []},
    "overrides": {},
    # Home Assistant's Supervisor proxies authenticated ingress sessions from this address
    # (the official add-on guidance is "allow 172.30.32.2; deny all"). A request from here has
    # already been authenticated by Home Assistant, so it is trusted without the LAN check.
    "ingress_peers": ["172.30.32.2/32"],
    "lan_cidrs": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
                  "::1/128", "fc00::/7", "fe80::/10"],
    "style": {},
    "size_limits": {"min": 320, "max": 7680, "span_w": 12000, "span_h": 8000},
}


def merge(a, b):
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    path = os.environ.get("WE_CONFIG", "/config/config.json")
    cfg = merge(DEFAULTS, read_json(path, {}) or {})
    if os.environ.get("WE_PORT"):
        cfg["port"] = int(os.environ["WE_PORT"])
    if os.environ.get("WE_DATA_DIR"):
        cfg["data_dir"] = os.environ["WE_DATA_DIR"]
    if os.environ.get("WE_FONT_DIR"):
        cfg["style"]["font_dir"] = os.environ["WE_FONT_DIR"]
    return cfg


def iso(ts):
    return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


# ======================================================================
class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lib = Library(cfg)
        self.renderer = render.Renderer(cfg.get("style"))
        self.captioner = Captioner(cfg) if cfg["captioners"] and cfg["caption"].get("enabled", True) else None
        self.state_path = os.path.join(cfg["data_dir"], "state.json")
        self.state = read_json(self.state_path, {}) or {}
        self.state.setdefault("bag", [])
        self.state.setdefault("priority", [])
        self.state.setdefault("history", [])
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.render_sem = threading.Semaphore(int(cfg["render_threads"]))
        self.render_locks = {}
        self.masters = OrderedDict()
        self.masters_lock = threading.Lock()
        self.cache_dir = os.path.join(cfg["data_dir"], "cache")
        self.cache_bytes = None
        self.caption_busy = None
        self.stats = {"renders": 0, "render_ms_last": 0, "cache_hits": 0}

    # ---------------- rotation ----------------
    def save_state(self):
        write_json(self.state_path, self.state)

    def eligible(self):
        """Captioned images, or every image when captions are off or none have landed yet.

        Without the fallback an install with no captioner shows one frozen picture forever,
        because the shuffle bag is always empty.
        """
        lib = self.lib
        captioned = sorted(i for i in lib.items if lib.status(i) == "captioned")
        return captioned or sorted(lib.items)

    def interval(self):
        r = self.cfg["rotation"]
        return random.uniform(float(r["min_minutes"]), float(r["max_minutes"])) * 60

    def pick_next(self, exclude=None):
        el = set(self.eligible())
        st = self.state
        st["priority"] = [i for i in st["priority"] if i in el]
        if st["priority"]:
            return st["priority"].pop(0)
        st["bag"] = [i for i in st["bag"] if i in el]
        if not st["bag"]:
            bag = sorted(el)
            random.shuffle(bag)
            if len(bag) > 1 and bag[0] == exclude:
                bag.append(bag.pop(0))
            st["bag"] = bag
            log("rotation: new shuffle of %d" % len(bag))
        return st["bag"].pop(0) if st["bag"] else None

    def rotate(self, reason):
        with self.lock:
            now = time.time()
            cur = self.state.get("current")
            nxt = self.pick_next(exclude=cur)
            if nxt is None:  # nothing captioned yet: show the first image clean, with its title
                ids = sorted(self.lib.items, key=lambda i: self.lib.items[i]["order"])
                first = self.cfg["rotation"].get("first_id")
                nxt = first if first in self.lib.items else (ids[0] if ids else None)
                if nxt is None:
                    return
            self.state.update(current=nxt, since=now, next_change=now + self.interval())
            self.state["history"] = ([{"id": nxt, "at": int(now)}] + self.state["history"])[:50]
            self.save_state()
        log("rotate (%s) -> %s, next at %s" % (reason, nxt, iso(self.state["next_change"])))

    def rotator(self):
        while True:
            try:
                with self.lock:
                    cur = self.state.get("current")
                    due = self.state.get("next_change", 0) <= time.time()
                    placeholder = cur and self.lib.status(cur) != "captioned"
                    have_captioned = bool(self.eligible())
                    missing = cur and cur not in self.lib.items
                if not cur or due or missing or (placeholder and have_captioned):
                    self.rotate("due" if due else "placeholder" if placeholder else "start")
            except Exception:
                traceback.print_exc()
            self.wake.wait(5)
            self.wake.clear()

    def current(self, base=""):
        with self.lock:
            cur = self.state.get("current")
            if not cur or cur not in self.lib.items:
                return None
            d = self.lib.display(cur)
            since, nxt = self.state["since"], self.state["next_change"]
        rev = d["rev"]
        return {
            "id": cur, "rev": rev, "version": "%s.%s.%d" % (cur, rev, int(since)),
            "title": d["title"], "place": d["place"], "credit": d["credit"], "blurb": d["blurb"],
            "since": iso(since), "next_change": iso(nxt),
            "captioned": self.lib.status(cur) == "captioned",
            # Root-absolute by default, so a direct-port client can use them as-is. Behind Home
            # Assistant ingress the handler passes the ingress prefix, because the page lives
            # under /api/hassio_ingress/<token>/ there.
            "urls": {
                "original": "%s/img/%s/original.jpg" % (base, cur),
                "clean": "%s/img/%s/%s/clean/{w}x{h}.jpg" % (base, cur, rev),
                "label": "%s/img/%s/%s/label/{w}x{h}.jpg" % (base, cur, rev),
                "span": "%s/img/%s/%s/span.jpg?layout={layout}" % (base, cur, rev),
            },
        }

    # ---------------- background workers ----------------
    def scanner(self):
        last_folder = last_static = last_daily = 0
        c = self.cfg
        while True:
            now = time.time()
            try:
                if now - last_folder >= 60 * c["folder_scan_minutes"]:
                    last_folder = now
                    self.lib.rescan()
                else:
                    self.lib.load_sidecars()
            except Exception:
                traceback.print_exc()
            if c["static_manifest"].get("enabled") and now - last_static >= 3600 * c["static_manifest"].get("refresh_hours", 24):
                try:
                    if self.lib.sync_static_manifest():
                        self.lib.rescan()
                    last_static = now
                except Exception as e:
                    log("static manifest sync failed:", e)
                    last_static = now - 3600 * c["static_manifest"].get("refresh_hours", 24) + 600
            if c["daily"].get("enabled") and now - last_daily >= 60 * c["daily"].get("check_minutes", 60):
                try:
                    iid = self.lib.sync_daily()
                    if iid:
                        self.lib.rescan()
                    last_daily = now
                except Exception as e:
                    log("daily sync failed:", e)
                    last_daily = now - 60 * c["daily"].get("check_minutes", 60) + 600
            # masters for everything (cheap after the first pass)
            for iid in list(self.lib.items):
                try:
                    self.lib.ensure_master(iid)
                except Exception as e:
                    log("master", iid, "failed:", e)
            if self.lib.changed.is_set():
                self.lib.changed.clear()
                self.wake.set()
            time.sleep(60)

    def caption_queue(self):
        lib = self.lib
        retry = 3600 * float(self.cfg["caption"].get("retry_failed_hours", 6))
        prio = self.cfg["rotation"].get("caption_priority", [])
        now = time.time()
        todo = []
        for iid, it in lib.items.items():
            stt = lib.status(iid)
            if stt == "captioned":
                continue
            sc = lib.sidecars.get(iid) or {}
            if sc.get("edited"):
                continue
            if stt == "failed" and now - sc.get("caption_failed_at", 0) < retry:
                continue
            rank = (0, prio.index(iid)) if iid in prio else (1, 0) if it["kind"] == "daily" else (2, 0)
            todo.append((rank, it["kind"] != "daily", it["meta"].get("date", "") and -int(it["meta"]["date"]) or 0, it["order"], iid))
        todo.sort()
        return [t[-1] for t in todo]

    def caption_worker(self):
        if not self.captioner:
            log("captioning disabled")
            return
        time.sleep(5)
        while True:
            queue = self.caption_queue()
            if not queue:
                time.sleep(30)
                continue
            iid = queue[0]
            it = self.lib.items.get(iid)
            gap = 30
            try:
                master = self.lib.ensure_master(iid)
                self.caption_busy = iid
                res, ep = self.captioner.caption(it["meta"], master)
                gap = float(ep.get("gap_seconds", 0))
                sc = {"id": iid, "source": it["path"], "sha256": it["sha256"], "title": res["title"],
                      "place": it["meta"].get("place", ""), "credit": it["meta"].get("credit", ""),
                      "blurb": res["blurb"], "caption_model": "%s@%s" % (ep["model"], ep["name"]),
                      "caption_at": iso(time.time()), "caption_seconds": res["seconds"], "edited": False}
                if res.get("accepted_with"):
                    sc["caption_note"] = "; ".join(res["accepted_with"])
                if self.lib.save_sidecar(iid, sc):
                    log("captioned %s: %s | %s" % (iid, res["title"], res["blurb"]))
                    if it["kind"] == "daily":
                        with self.lock:
                            self.state["priority"] = [iid] + [i for i in self.state["priority"] if i != iid]
                            self.save_state()
                    self.wake.set()
            except RuntimeError as e:
                if "no caption endpoint available" in str(e) or not any(
                        ep for ep in self.captioner.available()):
                    log("caption", iid, "postponed:", e)
                    gap = 60
                else:
                    self._mark_failed(iid, it, str(e))
            except ValueError as e:
                self._mark_failed(iid, it, str(e))
            except Exception as e:
                traceback.print_exc()
                self._mark_failed(iid, it, repr(e))
            finally:
                self.caption_busy = None
            time.sleep(gap)

    def _mark_failed(self, iid, it, err):
        sc = dict(self.lib.sidecars.get(iid) or {})
        if sc.get("edited"):
            return
        sc.update({"id": iid, "source": it["path"], "sha256": it["sha256"], "edited": False,
                   "caption_failed_at": time.time(), "caption_error": err[:300],
                   "caption_attempts": sc.get("caption_attempts", 0) + 1})
        sc.pop("blurb", None)
        self.lib.save_sidecar(iid, sc)
        log("caption", iid, "failed:", err)

    # ---------------- rendering + cache ----------------
    def master_image(self, iid):
        p = self.lib.ensure_master(iid)
        with self.masters_lock:
            im = self.masters.get(p)
            if im is not None:
                self.masters.move_to_end(p)
                return im
        im = Image.open(p)
        im.load()
        im = im.convert("RGB")
        with self.masters_lock:
            self.masters[p] = im
            while len(self.masters) > 3:
                self.masters.popitem(last=False)
        return im

    def cached(self, key_path, producer):
        """Return JPEG bytes for cache file key_path, rendering it with producer() on a miss."""
        full = os.path.join(self.cache_dir, key_path)
        try:
            with open(full, "rb") as f:
                data = f.read()
            os.utime(full)
            self.stats["cache_hits"] += 1
            return data
        except OSError:
            pass
        with self.lock:
            lk = self.render_locks.setdefault(key_path, threading.Lock())
        with lk:
            if os.path.exists(full):
                with open(full, "rb") as f:
                    return f.read()
            with self.render_sem:
                t = time.time()
                data = producer()
                ms = int((time.time() - t) * 1000)
            self.stats["renders"] += 1
            self.stats["render_ms_last"] = ms
            log("render %s %d ms %d KB" % (key_path, ms, len(data) // 1024))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full + ".tmp", "wb") as f:
                f.write(data)
            os.replace(full + ".tmp", full)
        with self.lock:
            self.render_locks.pop(key_path, None)
        self.evict(len(data))
        return data

    def evict(self, added):
        cap = int(self.cfg["cache_max_mb"]) * 1024 * 1024
        if self.cache_bytes is not None:
            self.cache_bytes += added
            if self.cache_bytes <= cap:
                return
        files, total = [], 0
        for dp, _dn, fns in os.walk(self.cache_dir):
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, p))
                total += st.st_size
        if total > cap:
            files.sort()
            for _m, size, p in files:
                if total <= cap * 0.85:
                    break
                try:
                    os.remove(p)
                    total -= size
                except OSError:
                    pass
            log("cache evicted down to %d MB" % (total // 1048576))
        self.cache_bytes = total

    def render_kind(self, iid, rev, kind, w, h, s=1.0, layout=None):
        disp = self.lib.display(iid)
        meta = {k: disp[k] for k in ("title", "place", "credit", "blurb")}
        sk = ("%.2f" % s).rstrip("0").rstrip(".")
        if kind == "clean":
            key = "%s/%s/clean-%dx%d.jpg" % (iid, rev, w, h)
            prod = lambda: self.renderer.encode(self.renderer.clean(self.master_image(iid), w, h))
        elif kind == "label":
            key = "%s/%s/label-%dx%d-s%s.jpg" % (iid, rev, w, h, sk)
            prod = lambda: self.renderer.encode(self.renderer.label(self.master_image(iid), meta, w, h, s))
        else:
            layout = layout.replace(" ", "+").strip()
            lk = hashlib.sha1(layout.encode()).hexdigest()[:12]
            key = "%s/%s/span-%s-s%s.jpg" % (iid, rev, lk, sk)
            mons = render.parse_layout(layout, **self.span_limits())
            prod = lambda: self.renderer.encode(self.renderer.span(self.master_image(iid), meta, mons, s))
        return self.cached(key, prod)

    def span_limits(self):
        z = self.cfg["size_limits"]
        return {"lo": z["min"], "hi": z["max"], "max_w": z["span_w"], "max_h": z["span_h"]}

    def original(self, iid):
        it = self.lib.items[iid]
        if it["path"].lower().endswith((".jpg", ".jpeg")):
            with open(it["path"], "rb") as f:
                return f.read()

        def prod():
            with Image.open(it["path"]) as im:
                return self.renderer.encode(im.convert("RGB"), quality=92)
        return self.cached("%s/original-%s.jpg" % (iid, it["sha256"][:8]), prod)


# ======================================================================
SIZE_RE = re.compile(r"^(\d{3,4})x(\d{3,4})\.jpg$")


class Handler(BaseHTTPRequestHandler):
    engine = None
    server_version = "wallpaper-engine/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("WE_ACCESS_LOG"):
            log("http", self.address_string(), fmt % args)

    # ----- plumbing -----
    def send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None, head=False):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "ETag")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head and body:
            self.wfile.write(body)

    def send_json(self, obj, code=200, headers=None, head=False):
        h = {"Cache-Control": "no-store"}
        h.update(headers or {})
        self.send(code, json.dumps(obj, ensure_ascii=False, indent=1), "application/json; charset=utf-8", h, head)

    def ingress_base(self):
        """The path Home Assistant serves this page under, or "" for a direct request."""
        return (self.headers.get("X-Ingress-Path") or "").rstrip("/")

    def client_is_ingress(self):
        """True for a request proxied by the Supervisor, which only forwards authenticated sessions."""
        try:
            ip = ipaddress.ip_address(self.client_address[0].strip())
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in ipaddress.ip_network(n) for n in self.engine.cfg["ingress_peers"])

    def client_is_lan(self):
        # Home Assistant has already authenticated an ingress request, and its X-Forwarded-For
        # carries the browser's own address — which is public for anyone coming in over a tunnel
        # or VPN, and would fail the check below.
        if self.client_is_ingress():
            return True
        nets = [ipaddress.ip_network(n) for n in self.engine.cfg["lan_cidrs"]]

        def inlan(a):
            try:
                ip = ipaddress.ip_address(a.strip())
                if ip.version == 6 and ip.ipv4_mapped:
                    ip = ip.ipv4_mapped
                return any(ip in n for n in nets)
            except ValueError:
                return False
        peer = self.client_address[0]
        if not inlan(peer):
            return False
        xff = self.headers.get("X-Forwarded-For")
        return all(inlan(a) for a in xff.split(",")) if xff else True

    def do_OPTIONS(self):
        self.send(204, headers={"Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
                                "Access-Control-Allow-Headers": "*", "Access-Control-Max-Age": "86400"})

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(min(length, 65536))
        if path == "/api/next":
            who = "%s via %s" % (self.headers.get("X-Forwarded-For", "-"), self.client_address[0])
            if not self.client_is_lan():
                log("api/next refused for", who)
                return self.send(403, "LAN only\n")
            self.engine.rotate("api from %s" % who)
            return self.send_json(self.engine.current(self.ingress_base()))
        self.send(404, "not found\n")

    def do_GET(self, head=False):
        try:
            self.route(head)
        except BrokenPipeError:
            pass
        except Exception as e:
            traceback.print_exc()
            try:
                self.send(500, "error: %s\n" % e.__class__.__name__, head=head)
            except Exception:
                pass

    def parse_scale(self, q):
        try:
            s = float(q.get("s", ["1"])[0])
        except ValueError:
            raise ValueError("bad s")
        if not 0.6 <= s <= 2.0:
            raise ValueError("s must be 0.6-2.0")
        return round(s, 2)

    def parse_size(self, name):
        m = SIZE_RE.match(name)
        if not m:
            return None
        w, h = int(m.group(1)), int(m.group(2))
        z = self.engine.cfg["size_limits"]
        if not (z["min"] <= w <= z["max"] and z["min"] <= h <= z["max"]):
            return None
        return w, h

    def route(self, head):
        eng = self.engine
        u = urllib.parse.urlsplit(self.path)
        path, q = u.path, urllib.parse.parse_qs(u.query)
        parts = [p for p in path.split("/") if p]
        immut = {"Cache-Control": "public, max-age=31536000, immutable"}

        if path == "/healthz":
            return self.send(200, "ok\n", head=head, headers={"Cache-Control": "no-store"})
        if path == "/current.json":
            cur = eng.current(self.ingress_base())
            return self.send_json(cur or {"error": "library empty"}, 200 if cur else 503, head=head)
        if path == "/library.json":
            return self.send_json(self.library_json(), head=head)
        if path in ("/", "/index.html"):
            return self.send(200, gallery_html(eng), "text/html; charset=utf-8", {"Cache-Control": "no-store"}, head)

        try:
            # /current/<kind>/<W>x<H>.jpg
            if len(parts) == 3 and parts[0] == "current" and parts[1] in ("clean", "label"):
                size = self.parse_size(parts[2])
                cur = eng.current()
                if not size or not cur:
                    return self.send(404, "not found\n", head=head)
                s = self.parse_scale(q) if parts[1] == "label" else 1.0
                etag = '"%s-%s-%dx%d-%s"' % (cur["version"], parts[1], size[0], size[1], s)
                hdr = {"Cache-Control": "no-cache", "ETag": etag}
                if self.headers.get("If-None-Match") == etag:
                    return self.send(304, headers=hdr, head=True)
                data = eng.render_kind(cur["id"], cur["rev"], parts[1], size[0], size[1], s)
                return self.send(200, data, "image/jpeg", hdr, head)

            if len(parts) >= 3 and parts[0] == "img" and parts[1] in eng.lib.items:
                iid = parts[1]
                if len(parts) == 3 and parts[2] == "original.jpg":
                    return self.send(200, eng.original(iid), "image/jpeg", {"Cache-Control": "public, max-age=86400"}, head)
                rev = parts[2]
                cur_rev = eng.lib.display(iid)["rev"]
                kind = parts[3] if len(parts) >= 4 else ""
                kind = "span" if kind == "span.jpg" else kind
                if kind not in ("clean", "label", "span"):
                    return self.send(404, "not found\n", head=head)
                if rev != cur_rev:  # caption edited since this URL was handed out
                    loc = self.ingress_base() + "/" + "/".join(["img", iid, cur_rev] + parts[3:]) + ("?" + u.query if u.query else "")
                    return self.send(302, headers={"Location": loc, "Cache-Control": "no-store"}, head=True)
                if kind == "span" and len(parts) == 4:
                    layout = q.get("layout", [""])[0]
                    if not layout:
                        return self.send(400, "layout required\n", head=head)
                    data = eng.render_kind(iid, rev, "span", 0, 0, self.parse_scale(q), layout)
                    return self.send(200, data, "image/jpeg", immut, head)
                if len(parts) == 5:
                    size = self.parse_size(parts[4])
                    if not size:
                        return self.send(400, "size must be <W>x<H>.jpg within limits\n", head=head)
                    s = self.parse_scale(q) if kind == "label" else 1.0
                    data = eng.render_kind(iid, rev, kind, size[0], size[1], s)
                    return self.send(200, data, "image/jpeg", immut, head)
        except ValueError as e:
            return self.send(400, "%s\n" % e, head=head)
        return self.send(404, "not found\n", head=head)

    def library_json(self):
        eng = self.engine
        out = []
        for iid in sorted(eng.lib.items, key=lambda i: eng.lib.items[i]["order"]):
            d = eng.lib.display(iid)
            sc = eng.lib.sidecars.get(iid) or {}
            out.append({"id": iid, "rev": d["rev"], "status": eng.lib.status(iid), "title": d["title"],
                        "place": d["place"], "credit": d["credit"], "blurb": d["blurb"],
                        "caption_model": sc.get("caption_model", ""), "caption_at": sc.get("caption_at", ""),
                        "edited": bool(sc.get("edited")), "error": sc.get("caption_error", "") if not d["blurb"] else ""})
        return {"items": out, "current": (eng.state.get("current")), "captioning": eng.caption_busy}


def gallery_html(eng):
    cur = eng.current() or {}
    esc = html.escape
    lib = Handler.library_json(type("X", (), {"engine": eng})())
    items = lib["items"]
    n_cap = sum(1 for i in items if i["status"] == "captioned")
    cards = []
    for it in items:
        st = it["status"]
        cards.append(
            '<figure class="%s"><a href="img/%s/%s/label/2560x1440.jpg"><img loading="lazy" '
            'src="img/%s/%s/clean/640x360.jpg" alt=""></a><figcaption><b>%s</b>'
            '<span class="st %s">%s</span><small>%s%s</small><p>%s</p></figcaption></figure>' % (
                "is-current" if it["id"] == cur.get("id") else "", it["id"], it["rev"], it["id"], it["rev"],
                esc(it["title"] or it["id"]), st, st + (" (edited)" if it["edited"] else ""),
                esc(it["id"]), (" · " + esc(it["place"])) if it["place"] else "",
                esc(it["blurb"] or it["error"])))
    # Built here rather than taken from current()["urls"], which is root-absolute: a relative
    # URL resolves correctly whether the page is served at / or under an ingress prefix.
    label = ("img/%s/%s/label/1920x1080.jpg" % (cur["id"], cur["rev"])) if cur.get("id") else ""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Matchy</title>
<style>
:root{color-scheme:dark;--bg:#111413;--card:#1b201e;--fg:#e8ece9;--mut:#9aa59f;--acc:#7fb38f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:1500px;margin:auto;padding:24px 16px}h1{font:italic 600 28px/1.2 Georgia,serif;margin:0 0 4px}
.top{display:grid;grid-template-columns:minmax(0,3fr) minmax(260px,1fr);gap:20px;align-items:start}
.top img{width:100%%;border-radius:10px;display:block}.meta p{color:var(--mut);margin:.4em 0}
button{background:var(--acc);color:#0b120e;border:0;border-radius:8px;padding:10px 18px;font-weight:600;cursor:pointer}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;margin-top:28px}
figure{margin:0;background:var(--card);border-radius:10px;overflow:hidden}figure img{width:100%%;aspect-ratio:16/9;display:block;object-fit:cover}
figure.is-current{outline:2px solid var(--acc)}figcaption{padding:10px 12px}figcaption small{display:block;color:var(--mut)}
figcaption p{margin:.4em 0 0;font-size:13px;color:#c9d0cc}.st{float:right;font-size:12px;border-radius:6px;padding:1px 7px;background:#333}
.st.captioned{background:#24402d}.st.failed{background:#5a2a2a}.st.pending{background:#40391f}
@media(max-width:800px){.top{grid-template-columns:1fr}}
</style></head><body><main>
<div class="top"><div><img src="%s" alt="current wallpaper"></div><div class="meta">
<h1>%s</h1><p>%s</p><p>%s</p><p>Shown since %s<br>Next change %s</p>
<p><button id="next">Next wallpaper</button></p>
<p>%d of %d images captioned%s.<br><a style="color:var(--acc)" href="current.json">current.json</a> ·
<a style="color:var(--acc)" href="library.json">library.json</a></p></div></div>
<div class="grid">%s</div></main>
<script>
document.getElementById('next').onclick=async e=>{e.target.disabled=true;
 await fetch('api/next',{method:'POST'});location.reload();};
</script></body></html>""" % (
        esc(label), esc(cur.get("title", "")), esc(" · ".join(x for x in (cur.get("place"), cur.get("credit")) if x)),
        esc(cur.get("blurb", "")), esc(cur.get("since", "")[11:16]), esc(cur.get("next_change", "")[11:16]),
        n_cap, len(items), (" (captioning %s now)" % esc(lib["captioning"])) if lib["captioning"] else "",
        "\n".join(cards))


def main():
    cfg = load_config()
    eng = Engine(cfg)
    log("wallpaper engine starting; raqm=%s port=%d data=%s" % (render.HAVE_RAQM, cfg["port"], cfg["data_dir"]))
    eng.lib.rescan()
    for name, fn in (("scanner", eng.scanner), ("rotator", eng.rotator), ("captioner", eng.caption_worker)):
        threading.Thread(target=fn, name=name, daemon=True).start()
    Handler.engine = eng
    srv = ThreadingHTTPServer((cfg["bind"], cfg["port"]), Handler)
    srv.daemon_threads = True
    log("listening on %s:%d" % (cfg["bind"], cfg["port"]))
    srv.serve_forever()


if __name__ == "__main__":
    main()
