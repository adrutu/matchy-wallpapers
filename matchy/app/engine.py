#!/usr/bin/env python3
"""Matchy Wallpapers engine: one rotating, captioned wallpaper served to every screen.

Config: JSON file at $WE_CONFIG (default /config/config.json); WE_PORT / WE_DATA_DIR override.
Stdlib HTTP server + Pillow. See matchy/DOCS.md for the API.
"""
import copy
import datetime as dt
import hashlib
import html
import ipaddress
import json
import math
import os
import random
import re
import string
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mood  # noqa: E402
import render  # noqa: E402
from caption import Captioner  # noqa: E402
from library import Library, log, open_image, read_json, rev_of, write_json  # noqa: E402

DEFAULTS = {
    "port": 8160,
    "bind": "0.0.0.0",
    "data_dir": "/data",
    "master_max_px": 3840,
    "cache_max_mb": 2048,
    "render_threads": 2,
    "rotation": {"min_minutes": 15, "max_minutes": 20, "caption_priority": []},
    "location": None,  # {"latitude", "longitude"}: Day & night changes at sunrise and sunset there
    "quiet_hours": {},  # {"start": "23:00", "end": "07:00", "hold": true, "dim": 0.6}; off without start and end
    "folders": [],
    "exclude": [],  # id globs kept out of the library
    "folder_scan_minutes": 10,
    "dedupe": {"avoid_prefixes": [], "ladder_prefer": "4k"},
    "static_manifest": {"enabled": False, "refresh_hours": 24},
    "daily": {"enabled": False, "check_minutes": 60, "id_prefix": "daily"},
    "captioners": [],
    "caption": {"enabled": True, "language": "English", "spelling": "", "min_words": 30, "max_words": 45,
                "max_tokens": 300, "temperature": 0.4, "image_px": 1024, "retry_failed_hours": 6,
                "forbidden_titles": []},
    "overrides": {},
    # Mood-aware picks: level off (today's shuffle) / light / strong; a Home Assistant weather entity
    # read every refresh_minutes through api_url (the Supervisor's core API proxy; a plain Home
    # Assistant /api with a long-lived api_token works outside the add-on). Without an entity, the
    # time of day and the season still count.
    "mood": {"level": "off", "weather_entity": "", "api_url": "http://supervisor/core/api", "api_token": "",
             "refresh_minutes": 10},
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


class Refused(Exception):
    """A control request the engine turns down; the handler answers with `code` and the message."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


CAPTION_LIMITS = {"title": 100, "place": 120, "credit": 200, "blurb": 600}

# The Screens list: a client names itself with ?screen=<name> on any GET. "Off" is advisory: the
# engine keeps serving it, and the client reads "follow" from /current.json and stands down itself.
SCREEN_RE = re.compile(r"^[^\W_][\w .:@-]{0,39}$")  # letters in any script (a user called Zoë), digits, " ._:@-"
MAX_SCREENS = 24     # the direct port has no login, so nobody may grow the file without bound
SCREEN_SAVE_S = 300  # last-seen times reach the disk at most this often

# The panel's "How often" slider: (label, minutes), and None for Day & night.
PACE_STOPS = [("Every 15 minutes", 15), ("Every 20 minutes", 20), ("Every half hour", 30), ("Every hour", 60),
              ("Every 2 hours", 120), ("Every 4 hours", 240), ("Every 8 hours", 480), ("Every 12 hours", 720),
              ("Day & night", None)]
DAYNIGHT_FALLBACK = (7, 19)  # local hours with no location, or on a day the sun never rises or never sets
WEATHER_STALE_S = 3 * 3600   # a weather reading older than this no longer steers the picks


def fetch_weather(api_url, token, entity, timeout=10):
    """The weather entity's state from Home Assistant's REST API: {"condition", "temperature_c"}.
    °F is converted; a missing or odd temperature is None. Raises on any failure."""
    url = api_url.rstrip("/") + "/states/" + urllib.parse.quote(entity, safe="")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        st = json.loads(r.read(200_000))
    if not isinstance(st, dict) or not isinstance(st.get("state"), str):
        raise ValueError("not an entity state")
    attrs = st.get("attributes") if isinstance(st.get("attributes"), dict) else {}
    temp = attrs.get("temperature")
    if isinstance(temp, bool) or not isinstance(temp, (int, float)):
        temp = None
    elif "f" in str(attrs.get("temperature_unit", "")).lower():
        temp = (temp - 32) * 5 / 9
    return {"condition": st["state"], "temperature_c": temp}


def sun_events(lat, lon, day):
    """(sunrise, sunset) as UTC epoch seconds on the date `day`, or None when the sun does not rise or
    does not set. NOAA's general solar position formulas (zenith 90.833°): about a minute out."""
    g = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    phi = math.radians(lat)
    c = math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(decl)) - math.tan(phi) * math.tan(decl)
    if not -1 <= c <= 1:
        return None
    ha = math.degrees(math.acos(c))
    midnight = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp()
    return (midnight + (720 - 4 * (lon + ha) - eqtime) * 60,
            midnight + (720 - 4 * (lon - ha) - eqtime) * 60)


HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
QUIET_DIM = 0.6  # how much quiet hours darken /current/... when the option leaves `dim` out


def parse_hhmm(text):
    m = HHMM_RE.match(text.strip()) if isinstance(text, str) else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def dim_key(dim):
    """0.6 -> "0.6": the dim amount in a cache file name and an ETag."""
    return ("%.2f" % dim).rstrip("0").rstrip(".")


def parse_quiet(q):
    """{"start", "end", "hold", "dim"} from the quiet_hours option, or None when it is off or unusable (logged).
    Off unless both start and end are set; hold defaults to true and dim to QUIET_DIM."""
    if not isinstance(q, dict) or (q.get("start") in (None, "") and q.get("end") in (None, "")):
        return None
    start, end = parse_hhmm(q.get("start")), parse_hhmm(q.get("end"))
    if not start or not end:
        log("quiet hours: start and end must both be times like 23:00 and 07:00; quiet hours are off")
        return None
    if start == end:
        log("quiet hours: start and end are the same time; quiet hours are off")
        return None
    dim = q.get("dim", QUIET_DIM)
    if isinstance(dim, bool) or not isinstance(dim, (int, float)) or not 0 <= dim <= 1:
        log("quiet hours: dim must be a number from 0 to 1; using %s" % QUIET_DIM)
        dim = QUIET_DIM
    return {"start": start, "end": end, "hold": q.get("hold", True) is not False, "dim": float(dim)}


def local_at(day, hm):
    """Epoch seconds of the local wall-clock time hm = (hour, minute) on the date `day`. A time the clocks skip
    (spring forward) lands after the jump; one they pass twice (fall back) is whichever the C library picks."""
    return time.mktime((day.year, day.month, day.day, hm[0], hm[1], 0, 0, 0, -1))


def quiet_window(now, start, end):
    """(active, start, end) in epoch seconds: the quiet window now is in, or else the next one. An end at or before
    the start is the next morning. Wall-clock times, so the night the clocks change is an hour longer or shorter."""
    today = dt.datetime.fromtimestamp(now).date()
    windows = []
    for offset in (-1, 0, 1):
        d = today + dt.timedelta(days=offset)
        windows.append((local_at(d, start), local_at(d + dt.timedelta(days=1) if end <= start else d, end)))
    for s, e in windows:
        if s <= now < e:
            return True, s, e
    s, e = min(w for w in windows if w[0] > now)
    return False, s, e


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
        self.state.setdefault("hidden", [])     # ids kept out of rotation from the panel
        self.state.setdefault("recaption", [])  # ids queued for a fresh machine caption
        # A window set in the panel lasts until the add-on options change: whichever was set last wins.
        ov, r = self.state.get("rotation_override"), cfg["rotation"]
        if ov and ov.get("options") != [r["min_minutes"], r["max_minutes"]]:
            log("rotation: the add-on options changed since the window was set in the panel; using the options")
            self.state.pop("rotation_override")
        self.quiet_cfg = parse_quiet(cfg.get("quiet_hours"))
        self.quiet_was = None  # whether the last rotator pass was in quiet hours, for the log
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.caption_wake = threading.Event()
        self.render_sem = threading.Semaphore(int(cfg["render_threads"]))
        self.render_locks = {}
        self.masters = OrderedDict()
        self.masters_lock = threading.Lock()
        self.cache_dir = os.path.join(cfg["data_dir"], "cache")
        self.cache_bytes = None
        self.caption_busy = None
        self.stats = {"renders": 0, "render_ms_last": 0, "cache_hits": 0}
        self.screens_path = os.path.join(cfg["data_dir"], "screens.json")
        screens = read_json(self.screens_path, {})
        self.screens = {k: v for k, v in screens.items() if isinstance(v, dict)} if isinstance(screens, dict) else {}
        self.screens_saved = 0.0
        self.screens_full_logged = False
        self.weather = None        # {"condition", "temperature_c", "at"} from the weather entity
        self.weather_error = None  # why the last reading failed, for the panel
        lvl = cfg["mood"].get("level")
        if lvl not in mood.LEVELS:
            log("mood: unknown level %r; off (one of %s)" % (lvl, ", ".join(mood.LEVELS)))
            cfg["mood"]["level"] = "off"

    # ---------------- rotation ----------------
    def save_state(self):
        write_json(self.state_path, self.state)

    def eligible(self):
        """Captioned images not hidden, or every image not hidden when none are captioned.
        Sources too large to decode (lib.skipped) never count: they would only ever be a 400.

        Without the fallback an install with no captioner shows one frozen picture forever,
        because the shuffle bag is always empty.
        """
        lib, hidden = self.lib, set(self.state["hidden"])
        shown = [i for i in lib.items if i not in hidden and i not in lib.skipped]
        captioned = sorted(i for i in shown if lib.status(i) == "captioned")
        return captioned or sorted(shown)

    def rotation_window(self):
        """(min, max, source) in minutes. Day & night has no window, so it reports the options' one."""
        ov = self.state.get("rotation_override")
        if ov and ov.get("mode") != "daynight":
            return ov["min_minutes"], ov["max_minutes"], "panel"
        r = self.cfg["rotation"]
        return r["min_minutes"], r["max_minutes"], "panel" if ov else "options"

    def rotation_mode(self):
        return "daynight" if (self.state.get("rotation_override") or {}).get("mode") == "daynight" else "minutes"

    def latitude(self):
        loc = self.cfg.get("location") or {}
        lat, lon = loc.get("latitude"), loc.get("longitude")
        known = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lon))
        return lat if known else None

    def sun_timeline(self, now):
        """Sunrises and sunsets as (epoch, name) from yesterday to the day after tomorrow in UTC dates, so
        no time zone can skip one. With no location, or on a day the sun never rises or never sets,
        DAYNIGHT_FALLBACK's local hours stand in, named morning and evening."""
        loc = self.cfg.get("location") or {}
        lat, lon = loc.get("latitude"), loc.get("longitude")
        known = self.latitude() is not None
        today_utc = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
        today_local = dt.datetime.fromtimestamp(now).date()
        events = []
        for offset in (-1, 0, 1, 2):
            sun = sun_events(lat, lon, today_utc + dt.timedelta(days=offset)) if known else None
            if sun:
                events += [(sun[0], "sunrise"), (sun[1], "sunset")]
                continue
            d = today_local + dt.timedelta(days=offset)
            events += [(time.mktime((d.year, d.month, d.day, h, 0, 0, 0, 0, -1)), name)
                       for h, name in zip(DAYNIGHT_FALLBACK, ("morning", "evening"))]
        return sorted(events)

    def next_sun_change(self, now):
        """(epoch, name) of the first sunrise or sunset more than a minute after now."""
        return min(e for e in self.sun_timeline(now) if e[0] > now + 60)

    def sun_around(self, now):
        """(the last sun event up to now, the next one after it)."""
        events = self.sun_timeline(now)
        return max(e for e in events if e[0] <= now), min(e for e in events if e[0] > now)

    # ---------------- mood ----------------
    def mood_level(self):
        return self.cfg["mood"].get("level") or "off"

    def weather_now(self, now=None):
        """The last weather reading while it is fresh, else None."""
        w = self.weather
        return w if w and (now or time.time()) - w.get("at", 0) < WEATHER_STALE_S else None

    def mood_context(self, now=None):
        """The tags of the moment: time of day (from the sun, as Day & night sees it), the season, and the
        weather entity's last reading. Computed on the fly: it is a few sun positions."""
        now = now or time.time()
        return mood.context_tags(now, self.sun_around(now), dt.datetime.fromtimestamp(now).date(),
                                 self.weather_now(now), self.latitude())

    def mood_view(self):
        """For library.json and the panel: the level, the moment, and the weather reading behind it."""
        lvl = self.mood_level()
        m = self.cfg["mood"]
        w = self.weather_now()
        out = {"level": lvl, "context": self.mood_context() if lvl != "off" else [],
               "weather_entity": m.get("weather_entity") or "", "weather": None, "weather_error": self.weather_error}
        if w:
            out["weather"] = {"condition": w["condition"], "temperature_c": w["temperature_c"], "at": iso(w["at"])}
        return out

    def interval(self):
        if self.rotation_mode() == "daynight":
            now = time.time()
            return self.next_sun_change(now)[0] - now
        lo, hi, _src = self.rotation_window()
        return random.uniform(float(lo), float(hi)) * 60

    def pick_next(self, exclude=None):
        el = set(self.eligible())
        if not el:  # cold start before the first scan, or everything hidden
            return None
        st = self.state
        st.pop("mood_pick", None)
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
        if not st["bag"]:
            return None
        k = 0
        if self.mood_level() != "off":
            # The moment's tags pull a matching picture forward in the shuffle; the bag itself is
            # untouched, so every picture still comes up once per shuffle (mood.choose).
            ctx = self.mood_context()
            k = mood.choose(st["bag"], self.lib.tags, ctx, self.mood_level(), exclude)
            st["mood_pick"] = {"context": ctx, "matched": sorted(set(ctx) & set(self.lib.tags(st["bag"][k])))}
        return st["bag"].pop(k)

    def _show(self, iid, now, reason, picked=False):
        """Put iid on screen. Caller holds self.lock. `picked` marks a choice made by hand,
        which the rotator must not swap out early for being uncaptioned."""
        self.state.update(current=iid, since=now, next_change=now + self.interval(), picked=picked)
        self.state["history"] = ([{"id": iid, "at": int(now)}] + self.state["history"])[:50]
        self.save_state()
        mp = self.state.get("mood_pick")
        log("rotate (%s) -> %s%s, next at %s%s" % (
            reason, iid, (" [mood: %s]" % (", ".join(mp["matched"]) or "no match")) if mp else "",
            iso(self.state["next_change"]),
            " (rotation paused)" if self.state.get("paused")
            else " (quiet hours: held until they end)" if self.state.get("quiet_hold") else ""))

    def rotate(self, reason):
        with self.lock:
            nxt = self.pick_next(exclude=self.state.get("current"))
            if nxt is None:  # empty library, or every picture hidden: leave the screen as it is
                return
            self._show(nxt, time.time(), reason)

    def quiet(self, now):
        """{"active", "start", "end", "hold", "dim"}, start and end in epoch seconds (the window now is in, or else
        the next one), or None when quiet hours are off."""
        q = self.quiet_cfg
        if not q:
            return None
        active, start, end = quiet_window(now, q["start"], q["end"])
        return {"active": active, "start": start, "end": end, "hold": q["hold"], "dim": q["dim"]}

    def rotator_step(self, now):
        """One pass of the rotator. Quiet hours with hold work like a pause that ends by itself: the rotation
        stands still (Next and Show now still work), and when they end the picture gets a full interval from
        then. state["quiet_hold"] carries that across a restart."""
        with self.lock:
            q = self.quiet(now)
            active = bool(q and q["active"])
            if active != self.quiet_was:
                if active:
                    log("quiet hours until %s%s%s" % (iso(q["end"]), ", holding the picture" if q["hold"] else "",
                                                      ", /current/... dimmed by %d %%" % round(q["dim"] * 100)
                                                      if q["dim"] else ""))
                elif self.quiet_was:
                    log("quiet hours over")
                self.quiet_was = active
            holding = active and q["hold"]
            paused = self.state.get("paused")
            if holding and not self.state.get("quiet_hold"):
                self.state["quiet_hold"] = True
                self.save_state()
            elif not holding and self.state.pop("quiet_hold", None):
                if not paused:  # a paused rotation gets its full interval on resume instead
                    self.state["next_change"] = now + self.interval()
                    log("rotation resumes after quiet hours, next at %s" % iso(self.state["next_change"]))
                self.save_state()
            cur = self.state.get("current")
            still = paused or holding
            due = not still and self.state.get("next_change", 0) <= now
            missing = cur and cur not in self.lib.items
            # An uncaptioned stand-in gives way as soon as a captioned picture exists,
            # unless it was picked by hand or the rotation is paused or held on it.
            placeholder = (cur and not still and not self.state.get("picked")
                           and self.lib.status(cur) != "captioned"
                           and any(self.lib.status(i) == "captioned" for i in self.eligible()))
        if not cur or due or missing or placeholder:
            self.rotate("due" if due else "placeholder" if placeholder else "start")

    def rotator(self):
        while True:
            try:
                self.rotator_step(time.time())
            except Exception:
                traceback.print_exc()
            self.wake.wait(5)
            self.wake.clear()

    # ---------------- controls (the panel and the HTTP API) ----------------
    def _need_item(self, iid):
        if iid not in self.lib.items:
            raise Refused(404, "no picture with id %r" % iid)

    def next_now(self, who):
        self.rotate("api from %s" % who)

    def set_paused(self, paused):
        with self.lock:
            if bool(self.state.get("paused")) == paused:
                return
            self.state["paused"] = paused
            if not paused:  # a full interval from now, not whatever was left before the pause
                self.state["next_change"] = time.time() + self.interval()
            self.save_state()
        log("rotation %s" % ("paused" if paused else "resumed"))
        self.wake.set()

    def show_now(self, iid):
        self._need_item(iid)
        if iid in self.lib.skipped:
            raise Refused(409, "that picture cannot be shown: %s" % self.lib.skipped[iid][1])
        with self.lock:
            self.state["bag"] = [i for i in self.state["bag"] if i != iid]
            self.state["priority"] = [i for i in self.state["priority"] if i != iid]
            self.state.pop("mood_pick", None)
            self._show(iid, time.time(), "picked", picked=True)

    def hide(self, iid):
        self._need_item(iid)
        with self.lock:
            hidden = self.state["hidden"]
            if iid not in hidden:
                if not any(i not in hidden and i not in self.lib.skipped and i != iid for i in self.lib.items):
                    raise Refused(409, "that is the last picture left in rotation")
                hidden.append(iid)
            self.state["bag"] = [i for i in self.state["bag"] if i != iid]
            self.state["priority"] = [i for i in self.state["priority"] if i != iid]
            self.save_state()
            log("hidden from rotation:", iid)
            if self.state.get("current") == iid:
                self.rotate("hidden")

    def unhide(self, iid):
        with self.lock:
            if iid in self.state["hidden"]:
                self.state["hidden"].remove(iid)
                self.save_state()
                log("back in rotation:", iid)

    def set_caption(self, iid, fields):
        """A caption written by hand. edited: true keeps the captioner off it until a re-caption.
        `fields` may carry `tags` (a list from mood.TAGS); without it the tags stay as they were."""
        it = self.lib.items.get(iid)  # one lookup: a rescan can drop the item at any moment
        if not it:
            raise Refused(404, "no picture with id %r" % iid)
        sc = dict(self.lib.sidecars.get(iid) or {})
        for k in ("caption_error", "caption_failed_at", "caption_attempts", "caption_note"):
            sc.pop(k, None)
        sc.update(fields)
        sc.update({"id": iid, "source": it["path"], "sha256": it["sha256"], "edited": True,
                   "edited_at": iso(time.time())})
        with self.lock:
            self.state["recaption"] = [i for i in self.state["recaption"] if i != iid]
            self.save_state()
        self.lib.save_sidecar(iid, sc, force=True)
        log("caption edited by hand:", iid)
        self.wake.set()

    def recaption(self, iid):
        self._need_item(iid)
        if not self.captioner:
            raise Refused(409, "captioning is off, or no captioner is configured")
        with self.lock:
            self.state["recaption"] = [iid] + [i for i in self.state["recaption"] if i != iid]
            self.save_state()
        log("re-caption queued:", iid)
        self.caption_wake.set()

    def set_rotation(self, lo, hi):
        r = self.cfg["rotation"]
        with self.lock:
            if [lo, hi] == [r["min_minutes"], r["max_minutes"]]:
                self.state.pop("rotation_override", None)
            else:
                self.state["rotation_override"] = {"min_minutes": lo, "max_minutes": hi,
                                                   "options": [r["min_minutes"], r["max_minutes"]]}
            if not self.state.get("paused") and self.state.get("current"):
                # Re-roll the current picture's slot in the new window; if that is already past,
                # the rotator moves on within seconds.
                self.state["next_change"] = self.state.get("since", time.time()) + self.interval()
            self.save_state()
        log("rotation window set in the panel: %s-%s minutes" % (lo, hi))
        self.wake.set()

    def reset_rotation(self):
        r = self.cfg["rotation"]
        self.set_rotation(r["min_minutes"], r["max_minutes"])

    def set_daynight(self):
        """One picture for the day, one for the night. Stored like a panel window ("options" included),
        so changing the add-on options takes over again in the same way."""
        r = self.cfg["rotation"]
        with self.lock:
            self.state["rotation_override"] = {"mode": "daynight", "options": [r["min_minutes"], r["max_minutes"]]}
            at, name = self.next_sun_change(time.time())
            if not self.state.get("paused") and self.state.get("current"):
                self.state["next_change"] = at
            self.save_state()
        log("rotation set in the panel: day & night, next change at %s (%s)" % (iso(at), name))
        self.wake.set()

    # ---------------- screens ----------------
    def save_screens(self):
        """Caller holds self.lock."""
        write_json(self.screens_path, self.screens)
        self.screens_saved = time.time()

    def saw_screen(self, sid, ip, agent, image=None):
        if not SCREEN_RE.match(sid):
            return
        now = time.time()
        with self.lock:
            s = self.screens.get(sid)
            new = s is None
            if new:
                if len(self.screens) >= MAX_SCREENS:
                    # Make room by dropping the screen unseen the longest that still follows (it comes back
                    # the next time it asks). Screens turned off are a choice made in the panel: kept.
                    following = [k for k, v in self.screens.items() if v.get("follow") is not False]
                    if not following:
                        if not self.screens_full_logged:
                            log("screens: %d screens, all turned off; %r not added, forget one in the panel"
                                % (MAX_SCREENS, sid))
                            self.screens_full_logged = True
                        return
                    gone = min(following, key=lambda k: self.screens[k].get("last_seen") or 0)
                    del self.screens[gone]
                    log("screens: the list is full (%d); dropped %r, unseen the longest" % (MAX_SCREENS, gone))
                s = self.screens[sid] = {"follow": True, "first_seen": now}
                log("screens: new screen %r from %s" % (sid, ip))
            s.update(last_seen=now, ip=ip, agent=agent[:120])
            if image:
                s["image"] = image
            if new or now - self.screens_saved >= SCREEN_SAVE_S:
                self.save_screens()

    def screen_follows(self, sid):
        return (self.screens.get(sid) or {}).get("follow", True) is not False

    def screens_off(self):
        return sorted(k for k, v in self.screens.items() if v.get("follow") is False)

    def set_screen(self, sid, follow):
        with self.lock:
            if sid not in self.screens:
                raise Refused(404, "no such screen")
            self.screens[sid]["follow"] = follow
            self.save_screens()
        log("screen %s: %s" % (sid, "following" if follow else "off"))

    def forget_screen(self, sid):
        with self.lock:
            if self.screens.pop(sid, None) is None:
                raise Refused(404, "no such screen")
            self.save_screens()
            self.screens_full_logged = False
        log("screen %s: forgotten" % sid)

    def screens_view(self):
        now = time.time()
        with self.lock:
            items = sorted(self.screens.items(), key=lambda kv: kv[0].lower())
            return [{"id": k, "follow": v.get("follow") is not False,
                     "first_seen": iso(v.get("first_seen") or now), "last_seen": iso(v.get("last_seen") or now),
                     "age_s": int(now - (v.get("last_seen") or now)),
                     "ip": v.get("ip", ""), "agent": v.get("agent", ""), "image": v.get("image", "")}
                    for k, v in items]

    def current(self, base=""):
        with self.lock:
            cur = self.state.get("current")
            if not cur or cur not in self.lib.items:
                return None
            d = self.lib.display(cur)
            # .get: a state.json from an older version or a crash mid-write must not 500 this.
            since = self.state.get("since") or time.time()
            nxt = self.state.get("next_change") or since
            paused = bool(self.state.get("paused"))
            tags = self.lib.tags(cur)
            matched = list((self.state.get("mood_pick") or {}).get("matched") or []) if self.mood_level() != "off" else []
        q = self.quiet(time.time())
        quiet = bool(q and q["active"])
        rev = d["rev"]
        return {
            "id": cur, "rev": rev, "version": "%s.%s.%d" % (cur, rev, int(since)),
            "title": d["title"], "place": d["place"], "credit": d["credit"], "blurb": d["blurb"],
            # Held by quiet hours, the next change is a full interval after they end: not known yet.
            "since": iso(since), "next_change": None if paused or (quiet and q["hold"]) else iso(nxt),
            "paused": paused, "captioned": self.lib.status(cur) == "captioned",
            # The window now is in, or else the next one; null when quiet hours are off.
            "quiet": quiet,
            "quiet_window": q and {"start": iso(q["start"]), "end": iso(q["end"]), "hold": q["hold"], "dim": q["dim"]},
            # Mood-aware picks: the picture's tags, and which of them matched the moment when it was
            # picked ([] with mood off, for a hand-picked picture, or when nothing matched).
            "tags": tags, "mood_match": matched,
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

    def captioner_view(self):
        c, now = self.captioner, time.time()
        if not c:
            return {"configured": False, "enabled": bool(self.cfg["caption"].get("enabled", True)),
                    "busy": None, "waiting": 0, "tagging": 0, "endpoints": []}
        captions, tagging = self.queues()
        return {"configured": True, "enabled": True, "busy": self.caption_busy, "waiting": len(captions),
                "tagging": len(tagging),
                "endpoints": [{"name": ep["name"],
                               "skipped_until": iso(c.down_until[ep["name"]]) if c.down_until.get(ep["name"], 0) > now else None}
                              for ep in c.endpoints]}

    def library_view(self):
        lib = self.lib
        hidden, recap = set(self.state["hidden"]), set(self.state["recaption"])
        out = []
        for iid in sorted(lib.items, key=lambda i: lib.items[i]["order"]):
            d = lib.display(iid)
            sc = lib.sidecars.get(iid) or {}
            out.append({"id": iid, "rev": d["rev"], "status": lib.status(iid), "title": d["title"],
                        "place": d["place"], "credit": d["credit"], "blurb": d["blurb"], "tags": lib.tags(iid),
                        "caption_model": sc.get("caption_model", ""), "caption_at": sc.get("caption_at", ""),
                        "edited": bool(sc.get("edited")), "hidden": iid in hidden, "recaption_queued": iid in recap,
                        "error": (lib.skipped[iid][1] if iid in lib.skipped
                                  else sc.get("caption_error", "") if not d["blurb"] else "")})
        lo, hi, src = self.rotation_window()
        r = self.cfg["rotation"]
        rot = {"min_minutes": lo, "max_minutes": hi, "source": src, "mode": self.rotation_mode(),
               "options": {"min_minutes": r["min_minutes"], "max_minutes": r["max_minutes"]}}
        if rot["mode"] == "daynight":
            at, name = self.next_sun_change(time.time())
            rot["next_sun"] = {"at": iso(at), "event": name}
        return {"items": out, "current": self.state.get("current"), "paused": bool(self.state.get("paused")),
                "rotation": rot, "mood": self.mood_view(),
                "captioning": self.caption_busy, "captioner": self.captioner_view()}

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
            if self.lib.changed.is_set():  # first wallpaper right after the cold scan, not after all masters
                self.lib.changed.clear()
                self.wake.set()
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
                if iid in self.lib.skipped:
                    continue
                try:
                    self.lib.ensure_master(iid)
                except ValueError:  # too large: logged once by ensure_master, now in lib.skipped
                    pass
                except Exception as e:
                    log("master", iid, "failed:", e)
            if self.lib.changed.is_set():
                self.lib.changed.clear()
                self.wake.set()
            time.sleep(60)

    def queues(self):
        """(ids waiting for a caption, in order; captioned ids waiting for tags, the backfill)."""
        lib = self.lib
        retry = 3600 * float(self.cfg["caption"].get("retry_failed_hours", 6))
        prio = self.cfg["rotation"].get("caption_priority", [])
        now = time.time()
        # Asked for from the panel: first, whatever their status, and even over a hand edit.
        recap = [i for i in self.state["recaption"] if i in lib.items]
        todo, tagging = [], []
        for iid, it in lib.items.items():
            if iid in recap:
                continue
            stt = lib.status(iid)
            if stt == "captioned":
                if iid not in lib.skipped and lib.needs_tags(iid, retry):
                    tagging.append((it["order"], iid))
                continue
            sc = lib.sidecars.get(iid) or {}
            if sc.get("edited"):
                continue
            if stt == "failed" and now - sc.get("caption_failed_at", 0) < retry:
                continue
            rank = (0, prio.index(iid)) if iid in prio else (1, 0) if it["kind"] == "daily" else (2, 0)
            todo.append((rank, it["kind"] != "daily", it["meta"].get("date", "") and -int(it["meta"]["date"]) or 0, it["order"], iid))
        todo.sort()
        tagging.sort()
        return recap + [t[-1] for t in todo], [t[-1] for t in tagging]

    def caption_queue(self):
        """Everything the caption worker has to do, captions first: pictures captioned before tags
        existed get their tags at the back, one tag-only request each."""
        captions, tagging = self.queues()
        return captions + tagging

    def tag_picture(self, iid, it):
        """The tag-only request for a captioned picture. Returns the endpoint's gap."""
        try:
            master = self.lib.ensure_master(iid)
            self.caption_busy = iid
            tags, ep = self.captioner.tags(it["meta"], master)
            self.lib.set_tags(iid, tags)
            log("tagged %s: %s" % (iid, ", ".join(tags) or "nothing fitted"))
            return float(ep.get("gap_seconds", 0))
        except RuntimeError as e:
            if "no caption endpoint available" in str(e) or not any(ep for ep in self.captioner.available()):
                gap = self.captioner.retry_in()
                log("tags", iid, "postponed %ds:" % gap, e)
                return gap
            self.lib.set_tags(iid, failed=str(e))
            log("tags", iid, "failed:", e)
        except Exception as e:  # noqa: BLE001 - one picture's failure must not stop the queue
            self.lib.set_tags(iid, failed=repr(e))
            log("tags", iid, "failed:", repr(e))
        return 30

    def _recaption_done(self, iid):
        with self.lock:
            if iid in self.state["recaption"]:
                self.state["recaption"].remove(iid)
                self.save_state()

    def caption_worker(self):
        if not self.captioner:
            log("captioning disabled")
            return
        time.sleep(5)
        while True:
            queue = self.caption_queue()
            if not queue:
                self.caption_wake.wait(30)
                self.caption_wake.clear()
                continue
            iid = queue[0]
            it = self.lib.items.get(iid)
            recap = iid in self.state["recaption"]
            if not recap and self.lib.status(iid) == "captioned":  # the tag backfill: the caption stays
                try:
                    gap = self.tag_picture(iid, it)
                finally:
                    self.caption_busy = None
                self.caption_wake.wait(gap)
                self.caption_wake.clear()
                continue
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
                if res.get("tags"):  # none at all: left out, so the tag-only request asks once more
                    sc["tags"] = res["tags"]
                if res.get("accepted_with"):
                    sc["caption_note"] = "; ".join(res["accepted_with"])
                saved = self.lib.save_sidecar(iid, sc, force=recap)
                if recap:
                    self._recaption_done(iid)
                if saved:
                    log("captioned %s: %s | %s | tags: %s" % (iid, res["title"], res["blurb"],
                                                              ", ".join(res.get("tags") or []) or "none"))
                    if it["kind"] == "daily":
                        with self.lock:
                            self.state["priority"] = [iid] + [i for i in self.state["priority"] if i != iid]
                            self.save_state()
                    self.wake.set()
            except RuntimeError as e:
                if "no caption endpoint available" in str(e) or not any(
                        ep for ep in self.captioner.available()):
                    gap = self.captioner.retry_in()
                    log("caption", iid, "postponed %ds:" % gap, e)
                else:
                    self._mark_failed(iid, it, str(e), recap)
            except ValueError as e:
                self._mark_failed(iid, it, str(e), recap)
            except Exception as e:
                traceback.print_exc()
                self._mark_failed(iid, it, repr(e), recap)
            finally:
                self.caption_busy = None
            self.caption_wake.wait(gap)  # a re-caption asked for in the panel cuts the pause short
            self.caption_wake.clear()

    def _mark_failed(self, iid, it, err, recap=False):
        if recap:  # the old caption stays; a failed re-caption is not retried on its own
            self._recaption_done(iid)
            log("re-caption", iid, "failed, keeping the old caption:", err)
            if (self.lib.sidecars.get(iid) or {}).get("blurb"):
                return
        sc = dict(self.lib.sidecars.get(iid) or {})
        if sc.get("edited"):
            return
        sc.update({"id": iid, "source": it["path"], "sha256": it["sha256"], "edited": False,
                   "caption_failed_at": time.time(), "caption_error": err[:300],
                   "caption_attempts": sc.get("caption_attempts", 0) + 1})
        sc.pop("blurb", None)
        self.lib.save_sidecar(iid, sc)
        log("caption", iid, "failed:", err)

    def weather_worker(self):
        """Read the weather entity every refresh_minutes. A failure keeps the last reading (it steers the
        picks for WEATHER_STALE_S) and is logged once until it changes or clears."""
        m = self.cfg["mood"]
        entity = m.get("weather_entity") or ""
        if not entity:
            return
        if not mood.WEATHER_ENTITY_RE.match(entity):
            self.weather_error = "not a weather entity id (weather.<name>)"
            log("mood: weather_entity %r is not a weather entity id (weather.<name>); ignored" % entity)
            return
        token = m.get("api_token") or os.environ.get("SUPERVISOR_TOKEN") or ""
        try:
            wait = max(60.0, 60 * float(m.get("refresh_minutes") or 10))
        except (TypeError, ValueError):
            wait = 600.0
        while True:
            try:
                w = fetch_weather(m.get("api_url") or DEFAULTS["mood"]["api_url"], token, entity)
                w["at"] = time.time()
                old = self.weather
                self.weather = w
                if self.weather_error:
                    log("mood: %s read again" % entity)
                    self.weather_error = None
                if mood.weather_text(old) != mood.weather_text(w):
                    log("mood: %s says %s" % (entity, mood.weather_text(w)))
            except Exception as e:  # noqa: BLE001 - the weather is optional; never stop the thread
                err = "HTTP %d: %s" % (e.code, e.reason) if isinstance(e, urllib.error.HTTPError) else str(e) or repr(e)
                if err != self.weather_error:
                    log("mood: cannot read %s (%s); %s" % (
                        entity, err, "keeping the last reading for a while" if self.weather else "picks go by the time of day and season"))
                    self.weather_error = err
            time.sleep(wait)

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

    def render_kind(self, iid, rev, kind, w, h, s=1.0, layout=None, dim=0.0):
        """dim (quiet hours, clean and label only) darkens the render and gets its own cache file."""
        disp = self.lib.display(iid)
        meta = {k: disp[k] for k in ("title", "place", "credit", "blurb")}
        sk = ("%.2f" % s).rstrip("0").rstrip(".")
        dk = ("-d%s" % dim_key(dim)) if dim else ""
        if kind == "clean":
            key = "%s/%s/clean-%dx%d%s.jpg" % (iid, rev, w, h, dk)
            prod = lambda: self.renderer.encode(self.renderer.dim(self.renderer.clean(self.master_image(iid), w, h), dim))
        elif kind == "label":
            key = "%s/%s/label-%dx%d-s%s%s.jpg" % (iid, rev, w, h, sk, dk)
            prod = lambda: self.renderer.encode(self.renderer.dim(
                self.renderer.label(self.master_image(iid), meta, w, h, s), dim))
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
            with open_image(it["path"]) as im:
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
    def send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None, head=False, cors=True):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "ETag")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head and body:
            self.wfile.write(body)

    def send_json(self, obj, code=200, headers=None, head=False, cors=True):
        h = {"Cache-Control": "no-store"}
        h.update(headers or {})
        self.send(code, json.dumps(obj, ensure_ascii=False, indent=1), "application/json; charset=utf-8", h, head,
                  cors)

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

    def note_screen(self, sid, parts):
        """Remember a client that named itself, and which picture it fetched (images only: a poll of
        current.json is not a picture on screen, least of all for a screen that is turned off).
        Only clients on this network may add or refresh one: the port has no login. The address shown is
        the first X-Forwarded-For hop when a proxy sent one (client_is_lan has checked every hop is
        private), so it is a label, not proof."""
        eng = self.engine
        if not self.client_is_lan():
            return
        xff = self.headers.get("X-Forwarded-For")
        ip = xff.split(",")[0].strip() if xff else self.client_address[0]
        image = None
        if len(parts) >= 3 and parts[0] == "img" and parts[1] in eng.lib.items:
            image = parts[1]
        elif len(parts) == 3 and parts[0] == "current":
            image = eng.state.get("current")
        eng.saw_screen(sid, ip, self.headers.get("User-Agent") or "", image)

    def do_OPTIONS(self):
        if urllib.parse.urlsplit(self.path).path.startswith("/api/"):
            # No CORS on the control API: the browser then refuses another website's JSON POST
            # (which always preflights), even from a machine on this network.
            return self.send(204, cors=False)
        self.send(204, headers={"Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
                                "Access-Control-Allow-Headers": "*", "Access-Control-Max-Age": "86400"})

    def do_HEAD(self):
        self.do_GET(head=True)

    def read_body(self, cap=65536):
        """The whole request body, or None if it is too big or not framed by Content-Length.
        Bytes left unread would be parsed as the next request on a keep-alive connection, so
        a refused body also closes the connection."""
        try:
            length = -1 if self.headers.get("Transfer-Encoding") else int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= cap:
            self.close_connection = True
            return None
        return self.rfile.read(length) if length else b""

    def do_POST(self):
        try:
            self.post()
        except BrokenPipeError:
            pass
        except Exception as e:
            traceback.print_exc()
            try:
                self.send(500, "error: %s\n" % e.__class__.__name__)
            except Exception:
                pass

    def post(self):
        eng = self.engine
        path = urllib.parse.urlsplit(self.path).path
        body = self.read_body()
        if body is None:
            return self.send(413, "body too large, or not sent with a Content-Length\n")
        who = "%s via %s" % (self.headers.get("X-Forwarded-For", "-"), self.client_address[0])
        if path == "/api/next":
            if not self.client_is_lan():
                log("api/next refused for", who)
                return self.send(403, "LAN only\n")
            eng.next_now(who)
            return self.send_json(eng.current(self.ingress_base()))
        action = ACTIONS.get(path)
        if not action:
            return self.send(404, "not found\n")
        tier, fn = action
        if not (self.client_is_ingress() if tier == "panel" else self.client_is_lan()):
            log(path[1:], "refused for", who)
            return self.send(403, "Home Assistant sidebar panel only\n" if tier == "panel" else "LAN only\n")
        # Requiring JSON is what makes the CORS refusal above bite: a form or text/plain POST
        # from another website needs no preflight, a JSON one does.
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self.send(415, "send Content-Type: application/json\n")
        try:
            args = json.loads(body or b"{}")
        except ValueError:
            return self.send(400, "body is not JSON\n")
        if not isinstance(args, dict):
            return self.send(400, "body must be a JSON object\n")
        try:
            fn(eng, args)
        except Refused as e:
            return self.send(e.code, "%s\n" % e)
        return self.send_json({"ok": True, "current": eng.current(self.ingress_base())})

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
        screen = q.get("screen", [""])[0].strip()
        if screen:
            self.note_screen(screen, parts)

        if path == "/healthz":
            return self.send(200, "ok\n", head=head, headers={"Cache-Control": "no-store"})
        if path == "/current.json":
            cur = eng.current(self.ingress_base())
            if cur:
                cur["screens_off"] = eng.screens_off()
                if screen:
                    cur["follow"] = eng.screen_follows(screen)
            return self.send_json(cur or {"error": "library empty"}, 200 if cur else 503, head=head)
        if path == "/library.json":
            return self.send_json(eng.library_view(), head=head)
        if path == "/screens.json":
            # Addresses and browser strings of the house's screens: this network only, and no CORS, so
            # another website open in a browser here cannot read them either.
            if not self.client_is_lan():
                return self.send(403, "LAN only\n", head=head, cors=False)
            return self.send_json({"screens": eng.screens_view()}, head=head, cors=False)
        if path in ("/", "/index.html"):
            page = gallery_html(eng, control=self.client_is_lan(), admin=self.client_is_ingress())
            # No CORS: the page names the house's screens; another website has no business reading it.
            return self.send(200, page, "text/html; charset=utf-8", {"Cache-Control": "no-store"}, head, cors=False)

        try:
            # /current/<kind>/<W>x<H>.jpg
            if len(parts) == 3 and parts[0] == "current" and parts[1] in ("clean", "label"):
                size = self.parse_size(parts[2])
                cur = eng.current()
                if not size or not cur:
                    return self.send(404, "not found\n", head=head)
                s = self.parse_scale(q) if parts[1] == "label" else 1.0
                # Quiet hours dim /current/ only: /img/ URLs are cacheable for a year and keep their meaning.
                dim = cur["quiet_window"]["dim"] if cur["quiet"] else 0.0
                etag = '"%s-%s-%dx%d-%s%s"' % (cur["version"], parts[1], size[0], size[1], s,
                                               ("-dim" + dim_key(dim)) if dim else "")
                hdr = {"Cache-Control": "no-cache", "ETag": etag}
                if self.headers.get("If-None-Match") == etag:
                    return self.send(304, headers=hdr, head=True)
                data = eng.render_kind(cur["id"], cur["rev"], parts[1], size[0], size[1], s, dim=dim)
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


def arg_id(a):
    iid = a.get("id")
    if not isinstance(iid, str) or not iid:
        raise Refused(400, "id required")
    return iid


def arg_caption(a):
    out = {}
    for k, cap in CAPTION_LIMITS.items():
        v = a.get(k, "")
        if not isinstance(v, str):
            raise Refused(400, "%s must be text" % k)
        v = " ".join(v.split())  # one paragraph: the label does its own wrapping
        if len(v) > cap:
            raise Refused(400, "%s is longer than %d characters" % (k, cap))
        out[k] = v
    if not out["blurb"]:
        raise Refused(400, "blurb required")
    if "tags" in a:  # optional: left out, the tags stay as they were
        if not isinstance(a["tags"], list):
            raise Refused(400, "tags must be a list of words")
        try:
            out["tags"] = mood.clean_tags(a["tags"], strict=True)
        except ValueError as e:
            raise Refused(400, str(e))
    return out


def arg_screen(a):
    sid = a.get("id")
    if not isinstance(sid, str) or not SCREEN_RE.match(sid):
        raise Refused(400, "id required")
    return sid


def arg_follow(a):
    if not isinstance(a.get("follow"), bool):
        raise Refused(400, "follow must be true or false")
    return a["follow"]


def rotation_action(eng, a):
    if a.get("reset") is True:
        return eng.reset_rotation()
    if "mode" in a:
        if a["mode"] != "daynight":
            raise Refused(400, "mode must be daynight")
        return eng.set_daynight()
    return eng.set_rotation(*arg_window(a))


def arg_window(a):
    lo, hi = a.get("min_minutes"), a.get("max_minutes")
    if not all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 1440 for v in (lo, hi)):
        raise Refused(400, "min_minutes and max_minutes must be whole minutes from 1 to 1440")
    if lo > hi:
        raise Refused(400, "min_minutes is more than max_minutes")
    return lo, hi


# POST /api/<name> -> (who may call it, what it does). "lan" is the rule /api/next has always had:
# the sidebar panel, or a private address on the direct port, so a dashboard button or an
# automation can drive the screens. "panel" is the sidebar panel only, because the direct port is
# unauthenticated and these change what the library holds or what the labels say.
ACTIONS = {
    "/api/pause": ("lan", lambda eng, a: eng.set_paused(True)),
    "/api/resume": ("lan", lambda eng, a: eng.set_paused(False)),
    "/api/show": ("lan", lambda eng, a: eng.show_now(arg_id(a))),
    "/api/hide": ("panel", lambda eng, a: eng.hide(arg_id(a))),
    "/api/unhide": ("panel", lambda eng, a: eng.unhide(arg_id(a))),
    "/api/caption": ("panel", lambda eng, a: eng.set_caption(arg_id(a), arg_caption(a))),
    "/api/recaption": ("panel", lambda eng, a: eng.recaption(arg_id(a))),
    "/api/rotation": ("panel", rotation_action),
    "/api/screen": ("lan", lambda eng, a: eng.set_screen(arg_screen(a), arg_follow(a))),
    "/api/screen/forget": ("panel", lambda eng, a: eng.forget_screen(arg_screen(a))),
}


def hhmm(stamp):
    return stamp[11:16] if stamp else ""


def join_words(words):
    """'a, b and c'."""
    words = list(words)
    return " and ".join(x for x in (", ".join(words[:-1]), words[-1]) if x) if words else ""


def why_html(cur, picked, m):
    """The "Why this one" line under the picture on screen: with mood on, which of its tags matched the
    moment (or that it was simply its turn); otherwise just its tags, so the words are in view."""
    esc = html.escape
    tags = cur.get("tags") or []
    tagged = ("tagged " + esc(join_words(tags))) if tags else "not tagged yet"
    if m["level"] == "off":
        return ('<p class="mut small why">Tags: %s.</p>' % esc(", ".join(tags))) if tags else ""
    if picked:
        why = "you picked it. It is %s." % tagged
    elif cur.get("mood_match"):
        why = "%s %s the moment (%s)." % (esc(join_words(cur["mood_match"])),
                                          "fit" if len(cur["mood_match"]) > 1 else "fits",
                                          esc(", ".join(m["context"])) or "nothing known")
    else:
        why = "its turn in the shuffle; it is %s, and the moment is %s." % (tagged, esc(", ".join(m["context"])))
    return '<p class="mut small why"><b>Why this one:</b> %s</p>' % why


def mood_html(m):
    """The Mood line in the Rotation box: the level, the moment, and what the weather entity said."""
    esc = html.escape
    if m["level"] == "off":
        return ('Mood: <b>off</b>. Set <code>mood</code> to light or strong in the add-on options and the weather, '
                'the season and the time of day steer which picture comes next.')
    out = "Mood: <b>%s</b>. The moment: %s." % (esc(m["level"]), esc(", ".join(m["context"])) or "nothing known")
    if m["weather"]:
        out += " %s says %s (read at %s)." % (esc(m["weather_entity"]), esc(mood.weather_text(m["weather"])),
                                               esc(hhmm(m["weather"]["at"])))
    elif m["weather_entity"]:
        out += " %s: %s." % (esc(m["weather_entity"]), ("not read yet (%s)" % esc(m["weather_error"])) if m["weather_error"]
                             else "not read yet")
    else:
        out += " No weather entity is set, so only the time of day and the season count."
    return out


def gallery_html(eng, control, admin):
    """The sidebar panel. control/admin: whether this viewer may use the "lan"/"panel" actions;
    the buttons they may not use are drawn disabled, with the reason as a tooltip."""
    esc = html.escape
    cur = eng.current() or {}
    lib = eng.library_view()
    items, rot, cap = lib["items"], lib["rotation"], lib["captioner"]
    no_ctl = "" if control else ' disabled title="Only from your own network"'
    no_admin = "" if admin else ' disabled title="Only in the Home Assistant sidebar panel"'
    no_recap = no_admin or ("" if cap["configured"] else ' disabled title="No captioner is configured"')

    def tags(it):
        out = []
        if it["id"] == cur.get("id"):
            out.append('<span class="tag on">On screen now</span>')
        if it["hidden"]:
            out.append('<span class="tag hid">Hidden from rotation</span>')
        if it["recaption_queued"]:
            st = "Caption: re-caption queued"
        else:
            st = {"captioned": "Caption: written by hand" if it["edited"] else "Caption: done",
                  "pending": "Caption: waiting", "failed": "Caption: failed"}.get(it["status"], it["status"])
        out.append('<span class="tag %s">%s</span>' % (esc(it["status"]), esc(st)))
        return "".join(out)

    def buttons(it, top=False):
        out = [] if top else ['<button data-act="show"%s>Show now</button>' % no_ctl]
        out.append('<button data-act="edit"%s>Edit caption</button>' % no_admin)
        out.append('<button data-act="recaption"%s>Re-caption</button>' % no_recap)
        if it["hidden"]:
            out.append('<button data-act="unhide"%s>Unhide</button>' % no_admin)
        else:
            out.append('<button data-act="hide"%s>%s</button>' % (no_admin, "Hide this picture" if top else "Hide"))
        return "".join(out)

    cards = []
    for it in items:
        cls = " is-current" if it["id"] == cur.get("id") else ""
        cls += " is-hidden" if it["hidden"] else ""
        src = "img/%s/%s" % (it["id"], it["rev"])
        cards.append(
            '<figure class="card%s" data-id="%s"><a class="thumb" href="%s/label/2560x1440.jpg" target="_blank" '
            'rel="noopener"><img loading="lazy" src="%s/clean/640x360.jpg" alt=""></a><figcaption>'
            '<div class="tags">%s</div><b>%s</b><small>%s%s</small><p>%s</p>%s<div class="acts">%s</div>'
            '</figcaption></figure>' % (
                cls, esc(it["id"]), esc(src), esc(src), tags(it), esc(it["title"] or it["id"]), esc(it["id"]),
                (" · " + esc(it["place"])) if it["place"] else "", esc(it["blurb"] or it["error"]),
                ('<small class="words">%s</small>' % esc(" · ".join(it["tags"]))) if it["tags"] else "", buttons(it)))

    by_id = {i["id"]: i for i in items}
    if cur.get("id"):
        # Relative, not current()["urls"] (root-absolute): this resolves at / and under ingress.
        src = "img/%s/%s/label" % (cur["id"], cur["rev"])
        top = ('<section class="top" data-id="%s"><a class="shot" href="%s/2560x1440.jpg" target="_blank" '
               'rel="noopener"><img src="%s/1920x1080.jpg" alt="The wallpaper on screen now"></a><div class="meta">'
               '<p class="kicker">On screen now</p><h1>%s</h1><p class="mut">%s</p><p>%s</p>%s'
               '<div class="acts"><button class="primary" data-act="next"%s>Next picture</button>%s</div></div></section>') % (
            esc(cur["id"]), esc(src), esc(src), esc(cur["title"] or cur["id"]),
            esc(" · ".join(x for x in (cur["place"], cur["credit"]) if x)), esc(cur["blurb"]),
            why_html(cur, bool(eng.state.get("picked")), lib["mood"]),
            no_ctl, buttons(by_id.get(cur["id"], {"hidden": False}), top=True))
    else:
        top = ('<section class="top empty"><div class="meta"><p class="kicker">On screen now</p><h1>Nothing yet</h1>'
               '<p class="mut">No pictures found so far. Check <code>source_folders</code> in the add-on options; '
               'the log names any folder it could not read.</p></div></section>')

    qw = cur.get("quiet_window")
    if cur.get("quiet"):
        held = qw["hold"] and not lib["paused"]
        what = [w for w in ("holding this picture" if held else "",
                            "/current/ pictures dimmed by %d %%" % round(qw["dim"] * 100) if qw["dim"] else "") if w]
        quiet = "Quiet until %s%s." % (esc(hhmm(qw["end"])), (": " + ", ".join(what)) if what else "")
        if held:
            quiet += " Next picture and Show now still work; the rotation resumes with a full interval at %s." % (
                esc(hhmm(qw["end"])))
    elif qw:
        quiet = "Quiet hours from %s to %s." % (esc(hhmm(qw["start"])), esc(hhmm(qw["end"])))
    else:
        quiet = ""
    if lib["paused"]:
        state = 'Rotation: <b>paused</b>, holding this picture until you resume.'
        toggle = '<button class="primary" data-act="resume"%s>Resume rotation</button>' % no_ctl
    elif cur.get("quiet") and qw["hold"]:
        state = 'Rotation: <b>quiet</b>.'
        toggle = '<button data-act="pause"%s>Pause rotation</button>' % no_ctl
    else:
        state = 'Rotation: <b>running</b>%s.' % (
            (", next picture at %s" % esc(hhmm(cur.get("next_change")))) if cur.get("next_change") else "")
        toggle = '<button data-act="pause"%s>Pause rotation</button>' % no_ctl
    if quiet:
        state += "<br>" + quiet
    opt = rot["options"]
    if rot["source"] == "panel":
        source = ('Set here. The add-on options say %s to %s minutes; changing them there takes over again. '
                  '<button class="link" data-act="rotation" data-body=\'{"reset": true}\'%s>Use the options</button>') % (
            opt["min_minutes"], opt["max_minutes"], no_admin)
    else:
        source = "From the add-on options."
    stops = [m for _label, m in PACE_STOPS]
    if rot["mode"] == "daynight":
        pos, pace_label = len(PACE_STOPS) - 1, PACE_STOPS[-1][0]
        ns = rot.get("next_sun") or {}
        if eng.cfg.get("location"):
            source = "Changes at sunrise and sunset; next: %s at %s." % (esc(ns.get("event", "")), esc(hhmm(ns.get("at"))))
        else:
            source = ("Changes at %02d:00 and %02d:00, because Home Assistant's location is not known."
                      % DAYNIGHT_FALLBACK)
    elif rot["min_minutes"] == rot["max_minutes"] and rot["min_minutes"] in stops:
        pos = stops.index(rot["min_minutes"])
        pace_label = PACE_STOPS[pos][0]
    else:  # a window from the options or the custom form: the nearest stop, labelled as it really is
        pos = min((i for i, m in enumerate(stops) if m), key=lambda i: abs(stops[i] - rot["min_minutes"]))
        pace_label = "Custom: %s–%s minutes" % (rot["min_minutes"], rot["max_minutes"])
    window = ('<form id="pace" class="pace"><label for="pace-r">How often</label><input id="pace-r" type="range" '
              'min="0" max="%d" step="1" value="%d" data-name="How often"%s><output id="pace-out" for="pace-r">%s'
              '</output></form><p class="mut small">%s</p>'
              '<details class="custom"><summary>Custom window</summary>'
              '<form id="window" class="window">Change every <input name="min" type="number" min="1" max="1440" '
              'step="1" value="%s" aria-label="Shortest minutes"%s> to <input name="max" type="number" min="1" '
              'max="1440" step="1" value="%s" aria-label="Longest minutes"%s> minutes <button%s>Save</button></form>'
              '</details>') % (
        len(PACE_STOPS) - 1, pos, no_admin, esc(pace_label), source,
        rot["min_minutes"], no_admin, rot["max_minutes"], no_admin, no_admin)

    rows = []
    for sc in eng.screens_view():
        days = sc["age_s"] // 86400
        if not sc["follow"]:
            tag = '<span class="tag off">Off</span>'
        elif days > 7:  # an off screen stops asking, so only a following one can go stale
            tag = '<span class="tag stale">Not seen for %d days</span>' % days
        else:
            tag = '<span class="tag on">Following</span>'
        # Just the name and the state: last seen and the address are in /screens.json (network only).
        rows.append(
            '<li><b>%s</b> %s<div class="acts">'
            '<button data-act="screen" data-body="%s"%s>%s</button>'
            '<button data-act="screen/forget" data-body="%s"%s>Forget</button></div></li>' % (
                esc(sc["id"]), tag,
                esc(json.dumps({"id": sc["id"], "follow": not sc["follow"]}), quote=True), no_ctl,
                "Turn off" if sc["follow"] else "Turn on",
                esc(json.dumps({"id": sc["id"]}), quote=True), no_admin))
    if rows:
        screens = ('<ul class="screens">%s</ul><p class="mut small">Off: that screen stops following Matchy and goes '
                   'back to its own wallpaper. Forget drops it from this list until it asks again.</p>') % "".join(rows)
    else:
        screens = ('<p class="mut">No screens yet. A screen shows up here the first time it asks for a picture '
                   'with <code>?screen=&lt;name&gt;</code> on the address.</p>')

    if not cap["enabled"]:
        capline = 'Captioner: <b>off</b>. <code>caption_enabled</code> is false, so labels carry titles only.'
    elif not cap["configured"]:
        capline = ('Captioner: <b>not set up</b>. Add one under <code>captioners</code> in the add-on options '
                   'to get written blurbs.')
    else:
        eps = []
        for ep in cap["endpoints"]:
            eps.append("%s <b>%s</b>" % (esc(ep["name"]), ("skipped until %s, it did not answer" % esc(hhmm(ep["skipped_until"])))
                                         if ep["skipped_until"] else "ready"))
        capline = "Captioner: %s." % "; ".join(eps)
        if cap["busy"]:
            capline += "<br>Writing a caption for %s now." % esc(cap["busy"])
        if cap["waiting"]:
            capline += "<br>%d picture%s waiting for a caption." % (cap["waiting"], "" if cap["waiting"] == 1 else "s")
        if cap["tagging"]:
            capline += "<br>%d picture%s captioned before tags existed, waiting for tags." % (
                cap["tagging"], "" if cap["tagging"] == 1 else "s")
    n_cap = sum(1 for i in items if i["status"] == "captioned")
    n_hidden = sum(1 for i in items if i["hidden"])
    summary = "%d picture%s · %d captioned%s" % (len(items), "" if len(items) == 1 else "s", n_cap,
                                                 (" · %d hidden" % n_hidden) if n_hidden else "")

    data = {"version": cur.get("version"),
            "items": [{k: i[k] for k in ("id", "title", "place", "credit", "blurb", "tags")} for i in items],
            "pace": [{"label": label, "body": {"min_minutes": m, "max_minutes": m} if m else {"mode": "daynight"}}
                     for label, m in PACE_STOPS]}
    tagboxes = "".join(
        '<div class="taggroup"><span>%s</span>%s</div>' % (esc(group), "".join(
            '<label class="tagbox"><input type="checkbox" name="tags" value="%s"> %s</label>' % (t, t) for t in words))
        for group, words in mood.TAG_GROUPS.items())
    return PAGE.substitute(
        top=top, state=state, toggle=toggle, window=window, screens=screens, capline=capline, summary=esc(summary),
        moodline=mood_html(lib["mood"]), tagboxes=tagboxes,
        cards="\n".join(cards), data=json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"),
        lim_title=CAPTION_LIMITS["title"], lim_place=CAPTION_LIMITS["place"],
        lim_credit=CAPTION_LIMITS["credit"], lim_blurb=CAPTION_LIMITS["blurb"])


# string.Template, so the CSS and JS need no escaping; the script avoids "$" for the same reason.
PAGE = string.Template("""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Matchy Wallpapers</title>
<style>
:root{color-scheme:dark;--bg:#111413;--card:#1b201e;--line:#2c3430;--fg:#e8ece9;--mut:#9aa59f;--acc:#7fb38f;--bad:#e08a7f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:1500px;margin:auto;padding:20px 16px 40px}
header{display:flex;gap:16px;align-items:baseline;margin-bottom:14px}.brand{font:italic 600 20px Georgia,serif;color:var(--acc)}
#msg{color:var(--bad)}code{font-size:.92em;color:#cfe3d5}
h1{font:italic 600 28px/1.2 Georgia,serif;margin:0 0 4px}h2{font-size:15px;margin:0 0 8px;letter-spacing:.02em}
.mut{color:var(--mut)}.small{font-size:13px}.kicker{margin:0 0 6px;color:var(--acc);font-size:12px;text-transform:uppercase;letter-spacing:.08em}
.top{display:grid;grid-template-columns:minmax(0,3fr) minmax(260px,1fr);gap:20px;align-items:start}
.top img{width:100%;border-radius:10px;display:block}.meta p{margin:.4em 0}
.panels{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-top:20px}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.box p{margin:.3em 0 .6em}
button{font:inherit;font-size:13px;background:transparent;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer}
button:hover:not(:disabled){border-color:var(--acc)}button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:var(--acc);border-color:var(--acc);color:#0b120e;font-weight:600}
button.link{border:0;padding:0;color:var(--acc);text-decoration:underline;font-size:inherit}
.acts{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.window input{width:4.5em;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px 6px}
.pace{display:grid;grid-template-columns:auto 1fr;gap:6px 12px;align-items:center;margin:10px 0 4px}
.pace input{width:100%;accent-color:var(--acc)}.pace output{grid-column:1/-1;font-weight:600}
details.custom{margin-top:8px}details.custom summary{cursor:pointer;color:var(--mut);font-size:13px}
.screens{list-style:none;margin:0;padding:0}.screens li{padding:8px 0;border-top:1px solid var(--line)}
.screens li:first-child{border-top:0}.screens .acts{margin-top:6px}
.lib{margin:28px 0 0}.lib small{color:var(--mut);font-weight:400;margin-left:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px;margin-top:12px}
.card{margin:0;background:var(--card);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;aspect-ratio:16/9;display:block;object-fit:cover}
.card.is-current{outline:2px solid var(--acc)}.card.is-hidden .thumb,.card.is-hidden b,.card.is-hidden p{opacity:.45}
figcaption{padding:10px 12px;display:flex;flex-direction:column;flex:1}figcaption small{display:block;color:var(--mut)}
figcaption p{margin:.4em 0 0;font-size:13px;color:#c9d0cc;flex:1}
.tags{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px}.tag{font-size:11px;border-radius:6px;padding:1px 7px;background:#333}
.tag.captioned{background:#24402d}.tag.failed{background:#5a2a2a}.tag.pending{background:#40391f}
.tag.on{background:var(--acc);color:#0b120e}.tag.hid{background:#3a3f55}.tag.off{background:#5a2a2a}.tag.stale{background:#40391f}
.words{margin-top:6px;font-style:italic}.why{margin-top:8px}
.taggroup{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center;margin-top:6px}.taggroup>span{color:var(--mut);font-size:12px;min-width:7em}
.tagbox{font-size:13px;white-space:nowrap}.tagbox input{accent-color:var(--acc);vertical-align:-2px}
dialog{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:12px;width:min(640px,calc(100vw - 32px));padding:18px}
dialog::backdrop{background:#000a}dialog label{display:block;margin:10px 0 0;font-size:13px;color:var(--mut)}
dialog input,dialog textarea{display:block;width:100%;margin-top:4px;font:15px/1.4 system-ui,sans-serif;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 8px}
dialog menu{display:flex;justify-content:flex-end;gap:8px;padding:0;margin:16px 0 0}#ed-msg{color:var(--bad);min-height:1.2em;margin:8px 0 0}
.tagset{border:1px solid var(--line);border-radius:8px;margin:12px 0 0;padding:6px 10px 10px}.tagset legend{padding:0 4px}
.foot{margin-top:24px;font-size:13px}.foot a{color:var(--acc)}
@media(max-width:800px){.top{grid-template-columns:1fr}}
</style></head><body><main>
<header><span class="brand">Matchy Wallpapers</span><span id="msg" role="status"></span></header>
$top
<section class="panels">
<div class="box"><h2>Rotation</h2><p>$state</p><div class="acts">$toggle</div>$window<p class="mut small">$moodline</p></div>
<div class="box"><h2>Captions</h2><p>$capline</p></div>
<div class="box"><h2>Screens</h2>$screens</div>
</section>
<h2 class="lib">Library <small>$summary</small></h2>
<div class="grid">$cards</div>
<p class="foot mut"><a href="current.json">current.json</a> · <a href="library.json">library.json</a></p>
</main>
<dialog id="editor"><form method="dialog" id="edit-form">
<h2>Edit caption: <span id="ed-id"></span></h2>
<p class="mut small">A caption written here is never overwritten by the captioner. Re-caption brings a machine caption back.</p>
<label>Title <input name="title" maxlength="$lim_title"></label>
<label>Place <input name="place" maxlength="$lim_place"></label>
<label>Credit <input name="credit" maxlength="$lim_credit"></label>
<label>Blurb <textarea name="blurb" rows="5" maxlength="$lim_blurb" required></textarea></label>
<fieldset class="tagset"><legend class="mut small">Tags (the mood-aware picks match them to the weather, season and time of day)</legend>$tagboxes</fieldset>
<p id="ed-msg" role="status"></p>
<menu><button value="cancel" formnovalidate>Cancel</button><button class="primary" value="save">Save caption</button></menu>
</form></dialog>
<script type="application/json" id="lib">$data</script>
<script>
const data = JSON.parse(document.getElementById('lib').textContent);
const byId = Object.fromEntries(data.items.map(i => [i.id, i]));
const msg = document.getElementById('msg');
const dlg = document.getElementById('editor'), form = document.getElementById('edit-form');
const edMsg = document.getElementById('ed-msg'), FIELDS = ['title', 'place', 'credit', 'blurb'];

async function post(path, body) {
  const r = await fetch('api/' + path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                        body: JSON.stringify(body || {})});
  if (!r.ok) throw new Error((await r.text()).trim() || 'HTTP ' + r.status);
  return r.json();
}
async function run(btn, path, body) {
  btn.disabled = true;
  msg.textContent = '';
  try { await post(path, body); location.reload(); }
  catch (e) { msg.textContent = (btn.dataset.name || btn.textContent) + ' did not work: ' + e.message; btn.disabled = false; }
}
function edit(id) {
  const it = byId[id];
  if (!it) return;
  form.dataset.id = id;
  edMsg.textContent = '';
  document.getElementById('ed-id').textContent = it.title || id;
  FIELDS.forEach(k => { form.elements[k].value = it[k] || ''; });
  form.querySelectorAll('input[name=tags]').forEach(b => { b.checked = (it.tags || []).includes(b.value); });
  dlg.showModal();
}
form.addEventListener('submit', async e => {
  if (e.submitter && e.submitter.value === 'cancel') return;
  e.preventDefault();
  const body = {id: form.dataset.id};
  FIELDS.forEach(k => { body[k] = form.elements[k].value; });
  body.tags = Array.from(form.querySelectorAll('input[name=tags]:checked'), b => b.value);
  try { await post('caption', body); location.reload(); } catch (err) { edMsg.textContent = err.message; }
});
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-act]');
  if (!b || b.disabled) return;
  const holder = b.closest('[data-id]'), id = holder ? holder.dataset.id : null;
  if (b.dataset.act === 'edit') return edit(id);
  run(b, b.dataset.act, b.dataset.body ? JSON.parse(b.dataset.body) : (id ? {id: id} : {}));
});
// How often: the label follows the slider; letting go saves.
const pace = document.getElementById('pace-r'), paceOut = document.getElementById('pace-out');
pace.addEventListener('input', () => { paceOut.textContent = data.pace[pace.value].label; });
pace.addEventListener('change', () => run(pace, 'rotation', data.pace[pace.value].body));
document.getElementById('pace').addEventListener('submit', e => e.preventDefault());
const win = document.getElementById('window');
win.addEventListener('submit', e => {
  e.preventDefault();
  run(e.submitter || win.querySelector('button'), 'rotation',
      {min_minutes: Number(win.elements.min.value), max_minutes: Number(win.elements.max.value)});
});
// Follow the rotation: reload when the picture on screen changes, but never under an open editor.
setInterval(async () => {
  if (dlg.open || document.hidden) return;
  try {
    const c = await (await fetch('current.json', {cache: 'no-store'})).json();
    if (c.version && c.version !== data.version) location.reload();
  } catch (e) { /* offline for a moment: try again next time */ }
}, 30000);
</script></body></html>""")


def main():
    cfg = load_config()
    eng = Engine(cfg)
    log("wallpaper engine starting; raqm=%s port=%d data=%s" % (render.HAVE_RAQM, cfg["port"], cfg["data_dir"]))
    # Bind before the first scan: a cold library takes minutes to hash, and /healthz must answer
    # at once (the Supervisor watchdog). The scanner thread does the first rescan; until it lands
    # current.json answers 503 "library empty" and the gallery is blank.
    Handler.engine = eng
    srv = ThreadingHTTPServer((cfg["bind"], cfg["port"]), Handler)
    srv.daemon_threads = True
    log("listening on %s:%d" % (cfg["bind"], cfg["port"]))
    for name, fn in (("scanner", eng.scanner), ("rotator", eng.rotator), ("captioner", eng.caption_worker),
                     ("weather", eng.weather_worker)):
        threading.Thread(target=fn, name=name, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
