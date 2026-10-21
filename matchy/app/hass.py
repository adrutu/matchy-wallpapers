"""Home Assistant entities over MQTT discovery.

One device, "Matchy Wallpapers", with: the picture on screen (an `image` fed by URL), its title (a
`sensor` carrying place, credit and blurb as attributes), Next and Love `button`s, a Pause `switch`,
a `select` with the panel's How often stops, one `switch` per screen (follow on or off; gone when the
screen is forgotten) and the captioner's queue (a `sensor`).

Topics, with <p> = `mqtt.topic_prefix` (default `matchy`) and <d> = `mqtt.discovery_prefix`
(default `homeassistant`):

    <d>/<component>/<p>/<object>/config   retained discovery configs
    <p>/status                            online / offline (the last will), retained
    <p>/state                             one JSON blob every entity reads with a template, retained
    <p>/picture                           the labelled picture's URL, retained
    <p>/screen/<key>/state                ON / OFF per screen, retained
    <p>/cmd/next, <p>/cmd/love            PRESS
    <p>/pause/set                         ON / OFF
    <p>/rotation/set                      one of the select's options
    <p>/screen/<key>/set                  ON / OFF

Every retained topic is written to <data>/mqtt.json, so a start with MQTT turned off can take them
all back out of the broker and the entities disappear from Home Assistant.
"""
import hashlib
import json
import os
import re
import threading
import traceback

from library import log, read_json, write_json
from mqtt import MqttClient, MqttError, publish_once

OPTIONS_LABEL = "Add-on options"  # the select's extra stop: the rotation window from the configuration
ORIGIN = {"name": "Matchy Wallpapers", "support_url": "https://github.com/adrutu/matchy-wallpapers"}
POLL_S = 3  # besides the engine's nudges: caption edits, the queue, a picture's colours arriving


def screen_key(name, taken=()):
    """A topic and id for a screen name: lower-case letters and digits, `_` between. A name that
    leaves nothing (another script) or collides with another screen gets a short hash of the name."""
    key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not key or key in taken:
        key = (key + "_" if key else "screen_") + hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
    return key


def pace_label(eng, stops):
    """The select's current option: a stop's label, OPTIONS_LABEL when the configuration's window is
    in force, or "None" (unknown to Home Assistant) for a custom window set in the panel."""
    if eng.rotation_mode() == "daynight":
        return stops[-1][0]
    lo, hi, src = eng.rotation_window()
    if src == "options":
        return OPTIONS_LABEL
    for label, minutes in stops:
        if minutes and lo == hi == minutes:
            return label
    return "None"


class Bridge:
    def __init__(self, eng, stops):
        self.eng, self.stops = eng, stops
        m = eng.cfg["mqtt"]
        self.prefix = m.get("topic_prefix") or "matchy"
        self.disc = (m.get("discovery_prefix") or "homeassistant").strip("/")
        self.base_url = (m.get("base_url") or "").rstrip("/")
        size = m.get("image_size") or [1920, 1080]
        self.image_size = (int(size[0]), int(size[1]))
        self.status_topic = self.prefix + "/status"
        self.device = {"identifiers": [self.prefix], "name": "Matchy Wallpapers",
                       "manufacturer": "Matchy Wallpapers", "model": "Home Assistant add-on"}
        self.record_path = os.path.join(eng.cfg["data_dir"], "mqtt.json")
        self.retained = set((read_json(self.record_path, {}) or {}).get("topics") or [])
        self.record_dirty = False
        self.last = None       # the snapshot last published, None before the first full publish
        self.full = threading.Event()  # republish everything (a fresh connection)
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.client = MqttClient(m["host"], m.get("port") or 1883, m.get("username") or "", broker_password(m),
                                 client_id=self.prefix, will=(self.status_topic, "offline", True), log=log)
        self.client.on_connect = self._connected
        self.client.on_message = self._message

    # ---------------- lifecycle ----------------
    def start(self):
        p = self.prefix
        self.client.subscribe(p + "/cmd/+", p + "/+/set", p + "/screen/+/set")
        self.client.start()
        self.eng.changed_hooks.append(self.wake.set)
        threading.Thread(target=self.run, name="hass", daemon=True).start()
        log("mqtt: Home Assistant entities on, discovery under %s/, topics under %s/" % (self.disc, p))

    def _connected(self):  # on the client's thread
        self.full.set()
        self.wake.set()

    def run(self):
        while True:
            self.wake.wait(POLL_S)
            self.wake.clear()
            if not self.client.connected:
                continue
            full = self.full.is_set()
            if full:
                self.full.clear()
            try:
                with self.lock:
                    self.refresh(full)
            except Exception:
                traceback.print_exc()

    # ---------------- what Home Assistant sees ----------------
    def snapshot(self):
        eng = self.eng
        cur = eng.current() or {}
        attrs = {k: cur.get(k) for k in ("id", "rev", "place", "credit", "blurb", "since", "next_change", "captioned")}
        for k in ("loved", "palette", "accent", "gnome_accent"):  # from sibling features, when present
            if k in cur:
                attrs[k] = cur[k]
        cap = eng.captioner_view()
        screens, taken = {}, set()
        for sc in eng.screens_view():
            key = screen_key(sc["id"], taken)
            taken.add(key)
            screens[key] = (sc["id"], sc["follow"])
        state = {"title": cur.get("title") or cur.get("id") or "None",
                 "attributes": attrs,
                 "paused": bool(cur["paused"] if cur else eng.state.get("paused")),
                 "rotation": pace_label(eng, self.stops),
                 "caption_queue": cap["waiting"], "captioning": cap["busy"],
                 "screens_off": [name for name, follow in screens.values() if not follow]}
        picture = None
        if cur and self.base_url:
            picture = self.base_url + cur["urls"]["label"].format(w=self.image_size[0], h=self.image_size[1])
        return {"state": state, "picture": picture, "screens": screens}

    def entities(self):
        """(component, object) -> discovery payload, for the fixed entities."""
        p, st = self.prefix, self.prefix + "/state"
        out = {
            ("sensor", "title"): {
                "name": "Title", "icon": "mdi:image-text", "state_topic": st,
                "value_template": "{{ value_json.title }}",
                "json_attributes_topic": st, "json_attributes_template": "{{ value_json.attributes | tojson }}"},
            ("button", "next"): {"name": "Next", "icon": "mdi:skip-next", "command_topic": p + "/cmd/next",
                                 "payload_press": "PRESS"},
            ("switch", "pause"): {"name": "Pause", "icon": "mdi:pause", "state_topic": st,
                                  "value_template": "{{ 'ON' if value_json.paused else 'OFF' }}",
                                  "command_topic": p + "/pause/set"},
            ("select", "rotation"): {"name": "Rotation", "icon": "mdi:timer-sand", "state_topic": st,
                                     "value_template": "{{ value_json.rotation }}",
                                     "command_topic": p + "/rotation/set",
                                     "options": [label for label, _m in self.stops] + [OPTIONS_LABEL]},
            ("sensor", "caption_queue"): {
                "name": "Caption queue", "icon": "mdi:text-box-multiple-outline", "state_topic": st,
                "value_template": "{{ value_json.caption_queue }}", "state_class": "measurement",
                "json_attributes_topic": st, "json_attributes_template": "{{ {'writing': value_json.captioning} | tojson }}"},
        }
        if self.base_url:
            out[("image", "picture")] = {"name": "Now showing", "url_topic": p + "/picture",
                                         "content_type": "image/jpeg"}
        if hasattr(self.eng, "love"):  # the favourites feature; no dead button without it
            out[("button", "love")] = {"name": "Love", "icon": "mdi:heart", "command_topic": p + "/cmd/love",
                                       "payload_press": "PRESS"}
        return out

    def screen_entity(self, key, name):
        p = self.prefix
        return {"name": "Screen %s" % name, "icon": "mdi:monitor", "state_topic": "%s/screen/%s/state" % (p, key),
                "command_topic": "%s/screen/%s/set" % (p, key)}

    def config_topic(self, component, obj):
        return "%s/%s/%s/%s/config" % (self.disc, component, self.prefix, obj)

    def discovery(self, component, obj, payload):
        payload = dict(payload, unique_id="%s_%s" % (self.prefix, obj), device=self.device, origin=ORIGIN,
                       availability_topic=self.status_topic)
        self.pub(self.config_topic(component, obj), json.dumps(payload, ensure_ascii=False), retain=True)

    def pub(self, topic, payload, retain=False):
        if retain:  # an empty retained payload is how a topic is taken out of the broker
            before = len(self.retained)
            (self.retained.add if payload else self.retained.discard)(topic)
            self.record_dirty |= len(self.retained) != before
        self.client.publish(topic, payload, retain)

    def refresh(self, full):
        """Publish what changed since the last snapshot; everything, after a (re)connection."""
        snap = self.snapshot()
        last = {} if full or self.last is None else self.last
        if not last:
            for (component, obj), payload in self.entities().items():
                self.discovery(component, obj, payload)
            self.pub(self.status_topic, "online", retain=True)
        old, new = last.get("screens") or {}, snap["screens"]
        # Switches of screens that are gone (forgotten, dropped from a full list, or removed while
        # the broker was away): judged by the record, not by the last snapshot.
        head = self.config_topic("switch", "screen_")[:-len("/config")]
        for topic in sorted(self.retained):
            if topic.startswith(head) and topic.endswith("/config") and topic[len(head):-len("/config")] not in new:
                self.pub(topic, "", retain=True)
                self.pub("%s/screen/%s/state" % (self.prefix, topic[len(head):-len("/config")]), "", retain=True)
        for key, (name, follow) in sorted(new.items()):
            if key not in old or old[key][0] != name:
                self.discovery("switch", "screen_" + key, self.screen_entity(key, name))
            if old.get(key) != (name, follow):
                self.pub("%s/screen/%s/state" % (self.prefix, key), "ON" if follow else "OFF", retain=True)
        if snap["state"] != last.get("state"):
            self.pub(self.prefix + "/state", json.dumps(snap["state"], ensure_ascii=False), retain=True)
        if snap["picture"] and snap["picture"] != last.get("picture"):
            self.pub(self.prefix + "/picture", snap["picture"], retain=True)
        self.last = snap
        if self.record_dirty:
            write_json(self.record_path, {"topics": sorted(self.retained)})
            self.record_dirty = False

    # ---------------- commands from Home Assistant ----------------
    def _message(self, topic, payload):  # on the client's thread
        eng, p = self.eng, self.prefix
        text = payload.decode("utf-8", "replace").strip()
        parts = topic.split("/")
        try:
            if topic == p + "/cmd/next":
                eng.next_now("mqtt")
            elif topic == p + "/cmd/love":
                self.love()
            elif topic == p + "/pause/set" and text in ("ON", "OFF"):
                eng.set_paused(text == "ON")
            elif topic == p + "/rotation/set":
                self.set_pace(text)
            elif len(parts) == 4 and parts[1] == "screen" and parts[3] == "set" and text in ("ON", "OFF"):
                self.set_screen(parts[2], text == "ON")
            else:
                log("mqtt: ignored %s %r" % (topic, text[:40]))
        except Exception as e:  # engine.Refused and friends: say why, keep the connection
            log("mqtt: %s refused: %s" % (topic, e))
        self.wake.set()

    def love(self):
        love = getattr(self.eng, "love", None)
        iid = self.eng.state.get("current")
        if not love:
            log("mqtt: Love pressed, but this version has no favourites; nothing done")
        elif not iid:
            log("mqtt: Love pressed with nothing on screen")
        else:
            love(iid)

    def set_pace(self, label):
        eng = self.eng
        if label == OPTIONS_LABEL:
            return eng.reset_rotation()
        for name, minutes in self.stops:
            if name == label:
                return eng.set_daynight() if minutes is None else eng.set_rotation(minutes, minutes)
        log("mqtt: rotation %r is not one of the options" % label)

    def set_screen(self, key, follow):
        with self.lock:
            screens = (self.last or {}).get("screens") or self.snapshot()["screens"]
        if key not in screens:
            raise KeyError("no screen %s" % key)
        self.eng.set_screen(screens[key][0], follow)


def broker_password(m):
    pf = m.get("password_file")
    if pf:
        try:
            with open(pf, encoding="utf-8") as f:
                return f.read().strip()
        except OSError as e:
            log("mqtt: cannot read the broker password file: %s" % e)
    return m.get("password") or ""


def cleanup(cfg):
    """MQTT is off: take every retained topic from an earlier run out of the broker, then forget them."""
    m, path = cfg["mqtt"], os.path.join(cfg["data_dir"], "mqtt.json")
    topics = (read_json(path, {}) or {}).get("topics") or []
    if not topics:
        return
    if not m.get("host"):
        log("mqtt: off, but %d retained topics from an earlier run are still in the broker and no broker "
            "details were given to remove them" % len(topics))
        return
    try:
        publish_once(m["host"], m.get("port") or 1883, m.get("username") or "", broker_password(m),
                     (m.get("topic_prefix") or "matchy") + "-cleanup", [(t, "", True) for t in topics])
    except (OSError, MqttError) as e:
        log("mqtt: off; could not remove the %d retained topics of an earlier run: %s" % (len(topics), e))
        return
    os.remove(path)
    log("mqtt: off; removed %d retained topics of an earlier run from the broker" % len(topics))


def setup(eng, stops):
    """Start the bridge when MQTT is on; otherwise clean up after an earlier run. Returns the Bridge or None."""
    m = eng.cfg["mqtt"]
    if not m.get("enabled"):
        threading.Thread(target=cleanup, args=(eng.cfg,), name="mqtt-cleanup", daemon=True).start()
        return None
    if m.get("ssl"):
        log("mqtt: the broker wants TLS, which this client does not speak; MQTT stays off")
        return None
    if not m.get("host"):
        log("mqtt: on, but no broker host is known; MQTT stays off")
        return None
    if not m.get("base_url"):
        log("mqtt: no base_url for the picture, so there is no image entity (the others work)")
    bridge = Bridge(eng, stops)
    bridge.start()
    return bridge
