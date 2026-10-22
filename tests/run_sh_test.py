"""Tests for matchy/run.sh: add-on options in, the engine's config.json out.

run.sh talks to the Supervisor through bashio, so this runs a copy of it under bash with bashio
stubbed by jq over an options file, /data moved to a temp dir, and the final `exec` of the engine
replaced by an exit. Needs bash and jq.

    python3 tests/run_sh_test.py
"""
import json, os, shutil, stat, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_SH = os.path.join(HERE, "..", "matchy", "run.sh")

# bashio, as far as run.sh uses it. `config KEY` prints the value (array items one per line, "null"
# when absent, like bashio); `exists` = not null; `has_value` = not null and not empty.
STUB = r'''
bashio::config() {
    jq -r "(.${1}) as \$v | if \$v == null then \"null\" elif (\$v|type) == \"array\" then \$v[] else \$v end" "$OPTIONS"
}
# A missing key and a JSON null both print `null`; a boolean false prints `false` (jq's `//` would turn
# it into null, which bashio does not do).
_opt_raw() { jq -c "(.${1}) as \$v | if \$v == null then null else \$v end" "$OPTIONS"; }
bashio::config.exists() { [[ "$(_opt_raw "${1}")" != "null" ]]; }
bashio::config.has_value() {
    local v; v="$(_opt_raw "${1}")"
    [[ "$v" != "null" && "$v" != '""' && "$v" != "[]" ]]
}
bashio::addon.hostname() { printf '%s' "${ADDON_HOSTNAME:-local-matchy}"; }
# The Supervisor's services API: MQTT_SERVICE is a JSON object (the Mosquitto add-on's answer) or unset.
bashio::services.available() { [[ -n "${MQTT_SERVICE:-}" ]]; }
bashio::services() { jq -r ".${2}" <<<"${MQTT_SERVICE}"; }
bashio::log.info() { echo "INFO: $*" >&2; }
bashio::log.warning() { echo "WARNING: $*" >&2; }
bashio::log.error() { echo "ERROR: $*" >&2; }
bashio::exit.nok() { echo "FATAL: $*" >&2; exit 1; }
'''

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("PASS", name)
    else:
        fail += 1; print("FAIL", name, extra)


def run(options, env=None):
    """Run run.sh with these options (and extra environment); return (exit code, config dict or None,
    stderr, data dir)."""
    tmp = tempfile.mkdtemp(prefix="matchy-runsh-")
    data = os.path.join(tmp, "data")
    script = open(RUN_SH).read()
    script = script.replace("readonly DATA_DIR=/data", "readonly DATA_DIR=%s" % data)
    assert "exec python3 /app/engine.py" in script
    # The engine itself is not started: what it would have inherited is written out instead.
    script = script.replace("exec python3 /app/engine.py", 'env > "${DATA_DIR}/engine-env.txt"; exit 0')
    open(os.path.join(tmp, "run.sh"), "w").write(script)
    open(os.path.join(tmp, "stub.sh"), "w").write(STUB)
    json.dump(options, open(os.path.join(tmp, "options.json"), "w"))
    p = subprocess.run(["bash", "-c", 'source "$1/stub.sh"; source "$1/run.sh"', "_", tmp],
                       env=dict(os.environ, OPTIONS=os.path.join(tmp, "options.json"), **(env or {})),
                       capture_output=True, text=True, timeout=60)
    cfg_path = os.path.join(data, "config.json")
    cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else None
    return p.returncode, cfg, p.stderr, data, tmp


def has_null(x):
    if x is None:
        return True
    if isinstance(x, dict):
        return any(has_null(v) for v in x.values())
    if isinstance(x, list):
        return any(has_null(v) for v in x)
    return False


src = tempfile.mkdtemp(prefix="matchy-src-")
base = {"source_folders": [src], "rotation_min_minutes": 15, "rotation_max_minutes": 20, "folder_scan_minutes": 10,
        "master_max_px": 3840, "cache_max_mb": 2048, "render_threads": 2, "caption_enabled": True,
        "caption_language": "English", "caption_spelling": "British", "caption_min_words": 30,
        "caption_max_words": 45, "captioners": [], "exclude": [], "sets": []}
temps = []

# defaults: one folder, no sets, no excludes, no nulls
rc, cfg, err, data, tmp = run(dict(base)); temps.append(tmp)
check("defaults run", rc == 0 and cfg is not None, err[-400:])
if cfg:
    check("defaults: folder with empty sets", cfg["folders"] == [{"path": src, "sets": {}}], cfg["folders"])
    check("defaults: empty exclude", cfg["exclude"] == [], cfg.get("exclude"))
    check("defaults: no nulls", not has_null(cfg))
    check("defaults: rotation and caption", cfg["rotation"] == {"min_minutes": 15, "max_minutes": 20, "love_weight": 3}
          and cfg["caption"]["spelling"] == "British", cfg)
    check("defaults: focus_auto off (centred crops)", cfg["focus_auto"] is False, cfg.get("focus_auto"))
    check("defaults: kids captions off", cfg["caption"]["kids"] == {"enabled": False}, cfg["caption"])
    check("defaults: colour match on (option absent)", cfg.get("palette") == {"enabled": True}, cfg.get("palette"))
    check("defaults: mood off, no weather entity", cfg["mood"] == {"level": "off"}, cfg.get("mood"))
rc, cfg, err, data, tmp = run(dict(base, focus_auto=True)); temps.append(tmp)
check("focus_auto on reaches the engine", rc == 0 and cfg and cfg["focus_auto"] is True, (rc, cfg and cfg.get("focus_auto")))
rc, cfg, err, data, tmp = run(dict(base, caption_kids=True)); temps.append(tmp)
check("caption_kids on", rc == 0 and cfg and cfg["caption"]["kids"] == {"enabled": True}, (rc, cfg and cfg["caption"]))

# colour match switched off
rc, cfg, err, data, tmp = run(dict(base, palette_enabled=False)); temps.append(tmp)
check("palette_enabled false reaches the engine", rc == 0 and cfg and cfg.get("palette") == {"enabled": False},
      (rc, cfg and cfg.get("palette")))

# mood-aware picks: the level and a weather entity
rc, cfg, err, data, tmp = run(dict(base, mood="light", weather_entity="weather.forecast_home")); temps.append(tmp)
check("mood light with a weather entity", rc == 0 and cfg and cfg["mood"] == {"level": "light", "weather_entity": "weather.forecast_home"},
      (rc, cfg and cfg.get("mood"), err[-300:]))
rc, cfg, err, data, tmp = run(dict(base, mood="loud", weather_entity="sensor.outside_temperature")); temps.append(tmp)
check("bad mood level and entity: off, entity ignored, both warned", rc == 0 and cfg and cfg["mood"] == {"level": "off"}
      and "'mood' must be off, light or strong" in err and "'weather_entity' is not a weather entity id" in err,
      (rc, cfg and cfg.get("mood"), err[-400:]))

# sets, exclude, a keyed captioner, a missing folder
opts = dict(base, source_folders=[src, "/nonexistent/matchy"], exclude=["aether-*", "test-3"], love_weight=5,
            sets=[{"folder": "theme-forest-green", "id_prefix": "fg", "name": "Forest Green",
                   "credit": "Omarchy · Forest Green theme", "abstract": True},
                  {"folder": "geology", "id_prefix": "geo", "name": "Geology", "abstract": False},
                  {"folder": "plain"}],
            captioners=[{"name": "box", "url": "http://box:8080/v1/chat/completions", "model": "m",
                         "api_key": "s3cr3t-key", "timeout": 90},
                        {"name": "vm", "url": "http://vm:8080/v1/chat/completions", "model": "n"}])
rc, cfg, err, data, tmp = run(opts); temps.append(tmp)
check("full run", rc == 0 and cfg is not None, err[-400:])
if cfg:
    want_sets = {"theme-forest-green": {"id_prefix": "fg", "name": "Forest Green",
                                        "credit": "Omarchy · Forest Green theme", "abstract": True},
                 "geology": {"id_prefix": "geo", "name": "Geology"}, "plain": {}}
    check("sets reach every usable folder", cfg["folders"] == [{"path": src, "sets": want_sets}], cfg["folders"])
    check("missing folder skipped with a warning", "Source folder not found" in err, err[-300:])
    check("exclude list", cfg["exclude"] == ["aether-*", "test-3"], cfg["exclude"])
    check("love_weight reaches rotation", cfg["rotation"]["love_weight"] == 5, cfg["rotation"])
    caps = cfg["captioners"]
    check("two captioners in order", [c["name"] for c in caps] == ["box", "vm"], caps)
    check("key goes to a file, not the config", "api_key" not in caps[0] and caps[0].get("api_key_file", "").endswith(
        "captioner-0.key") and "s3cr3t-key" not in json.dumps(cfg), caps[0])
    kf = os.path.join(data, "secrets", "captioner-0.key")
    check("key file mode 600 with the key", os.path.exists(kf) and stat.S_IMODE(os.stat(kf).st_mode) == 0o600
          and open(kf).read().strip() == "s3cr3t-key")
    check("unkeyed captioner has no key file", "api_key_file" not in caps[1] and caps[1]["timeout"] == 120, caps[1])
    check("full: no nulls", not has_null(cfg))
    check("key never logged", "s3cr3t-key" not in err)

# quiet hours: off by default (an empty object), only the fields set, an explicit hold: false kept
rc, cfg, err, data, tmp = run(dict(base)); temps.append(tmp)
check("quiet hours: default is an empty object", rc == 0 and cfg and cfg.get("quiet_hours") == {}, cfg and cfg.get("quiet_hours"))
rc, cfg, err, data, tmp = run(dict(base, quiet_hours={})); temps.append(tmp)
check("quiet hours: {} stays off", rc == 0 and cfg and cfg.get("quiet_hours") == {}, cfg and cfg.get("quiet_hours"))
rc, cfg, err, data, tmp = run(dict(base, quiet_hours={"start": "23:00", "end": "07:00", "hold": False, "dim": 0.6}))
temps.append(tmp)
check("quiet hours: every field", rc == 0 and cfg and cfg.get("quiet_hours") == {"start": "23:00", "end": "07:00",
                                                                                  "hold": False, "dim": 0.6},
      (cfg and cfg.get("quiet_hours"), err[-300:]))
rc, cfg, err, data, tmp = run(dict(base, quiet_hours={"start": "22:30", "end": "6:15", "hold": True, "dim": 1}))
temps.append(tmp)
check("quiet hours: hold true, dim 1", rc == 0 and cfg and cfg.get("quiet_hours") == {"start": "22:30", "end": "6:15",
                                                                                      "hold": True, "dim": 1},
      (cfg and cfg.get("quiet_hours"), err[-300:]))
rc, cfg, err, data, tmp = run(dict(base, quiet_hours={"start": "23:00", "end": "07:00", "dim": "lots"})); temps.append(tmp)
check("quiet hours: a bad dim is dropped with a warning", rc == 0 and cfg and cfg.get("quiet_hours") == {
    "start": "23:00", "end": "07:00"} and "quiet_hours.dim" in err, (cfg and cfg.get("quiet_hours"), err[-300:]))

# Home Assistant's location (Day & night rotation), through a fake curl on PATH; sleep made instant
fake = tempfile.mkdtemp(prefix="matchy-bin-"); temps.append(fake)
def fake_bin(curl_body):
    for name, body in (("curl", curl_body), ("sleep", "exit 0")):
        f = os.path.join(fake, name)
        open(f, "w").write("#!/bin/bash\n" + body + "\n")
        os.chmod(f, 0o755)
    return {"SUPERVISOR_TOKEN": "t", "PATH": fake + os.pathsep + os.environ["PATH"]}
check("no token: no location key", "location" not in (run(dict(base))[1] or {"location": 1}))
rc, cfg, err, data, tmp = run(dict(base), fake_bin(
    'echo \'{"latitude": 51.8985, "longitude": -8.4756, "location_name": "Home", "time_zone": "Europe/Dublin"}\''))
temps.append(tmp)
check("location from Home Assistant", rc == 0 and cfg and cfg.get("location") == {"latitude": 51.8985, "longitude": -8.4756},
      (rc, cfg and cfg.get("location"), err[-300:]))
env_txt = open(os.path.join(data, "engine-env.txt")).read() if os.path.exists(os.path.join(data, "engine-env.txt")) else ""
check("the engine does not inherit the Supervisor token", env_txt and "SUPERVISOR_TOKEN" not in env_txt
      and "WE_CONFIG=" in env_txt, env_txt[:300])
# ... except when it reads a weather entity for the mood-aware picks (and only then)
for opts, keeps, name in ((dict(base, mood="light", weather_entity="weather.home"), True, "mood light + weather entity"),
                          (dict(base, mood="off", weather_entity="weather.home"), False, "mood off + weather entity"),
                          (dict(base, mood="strong"), False, "mood strong, no weather entity")):
    rc, cfg, err, data, tmp = run(opts, fake_bin("exit 7")); temps.append(tmp)
    p = os.path.join(data, "engine-env.txt")
    txt = open(p).read() if os.path.exists(p) else ""
    check("token for the engine: " + name, rc == 0 and ("SUPERVISOR_TOKEN=t" in txt) == keeps, (rc, err[-300:]))
rc, cfg, err, data, tmp = run(dict(base), fake_bin("exit 7")); temps.append(tmp)
check("Home Assistant not answering: no location, still starts", rc == 0 and cfg and "location" not in cfg
      and "location is not known" in err, (rc, err[-300:]))

# open-access art: off by default with the documented budgets; one source on is enough to start
check("defaults: every art source off", (run(dict(base))[1] or {}).get("art_sources") == {
      "aic": {"enabled": False, "per_day": 3, "min_px": 1686}, "met": {"enabled": False, "per_day": 3, "min_px": 2560},
      "potd": {"enabled": False, "per_day": 1, "min_px": 2560}})
rc, cfg, err, data, tmp = run(dict(base, source_folders=["/nonexistent/matchy"],
                                   the_met={"enabled": True, "per_day": 2, "min_px": 3000})); temps.append(tmp)
check("one art source and no folder starts", rc == 0 and cfg and cfg["art_sources"]["met"] == {
      "enabled": True, "per_day": 2, "min_px": 3000} and cfg["art_sources"]["aic"]["enabled"] is False
      and "1 art source(s)" in err, (rc, cfg and cfg.get("art_sources"), err[-300:]))

# MQTT: the broker from the Supervisor's services API, the password to a 600 file, the picture address
mq = {"host": "core-mosquitto", "port": 1883, "ssl": False, "username": "addons", "password": "br0ker-pw", "protocol": "3.1.1"}
rc, cfg, err, data, tmp = run(dict(base)); temps.append(tmp)
check("mqtt: off and no broker: enabled false only", rc == 0 and cfg and cfg.get("mqtt") == {"enabled": False}, (rc, cfg and cfg.get("mqtt")))
rc, cfg, err, data, tmp = run(dict(base, mqtt_enabled=True)); temps.append(tmp)
check("mqtt: on with no broker: a warning, enabled false", rc == 0 and cfg and cfg["mqtt"] == {"enabled": False}
      and "no MQTT broker is set up" in err, (rc, cfg and cfg.get("mqtt"), err[-300:]))
rc, cfg, err, data, tmp = run(dict(base, mqtt_enabled=True), {"MQTT_SERVICE": json.dumps(mq)}); temps.append(tmp)
check("mqtt: on with a broker", rc == 0 and cfg and cfg["mqtt"] == {
    "enabled": True, "host": "core-mosquitto", "port": 1883, "username": "addons", "ssl": False,
    "password_file": os.path.join(data, "secrets", "mqtt.key"), "base_url": "http://local-matchy:8160"}, (rc, cfg and cfg.get("mqtt"), err[-300:]))
if cfg:
    kf = os.path.join(data, "secrets", "mqtt.key")
    check("mqtt: password in a 600 file, not in the config or the log", os.path.exists(kf) and stat.S_IMODE(os.stat(kf).st_mode) == 0o600
          and open(kf).read().strip() == "br0ker-pw" and "br0ker-pw" not in json.dumps(cfg) and "br0ker-pw" not in err)
    check("mqtt: on in the start line", "MQTT entities true" in err, err[-300:])
rc, cfg, err, data, tmp = run(dict(base, mqtt_enabled=True, mqtt_image_url="https://pictures.example/matchy/"),
                              {"MQTT_SERVICE": json.dumps(dict(mq, port="odd", ssl=True)), "ADDON_HOSTNAME": "a1b2c3d4-matchy"}); temps.append(tmp)
check("mqtt: the picture address given wins, an odd port falls back, ssl passed through", rc == 0 and cfg
      and cfg["mqtt"]["base_url"] == "https://pictures.example/matchy" and cfg["mqtt"]["port"] == 1883 and cfg["mqtt"]["ssl"] is True,
      (rc, cfg and cfg.get("mqtt"), err[-300:]))
rc, cfg, err, data, tmp = run(dict(base, mqtt_enabled=False), {"MQTT_SERVICE": json.dumps(mq)}); temps.append(tmp)
check("mqtt: off with a broker: details still passed for the clean-up", rc == 0 and cfg and cfg["mqtt"]["enabled"] is False
      and cfg["mqtt"]["host"] == "core-mosquitto" and cfg["mqtt"]["password_file"], (rc, cfg and cfg.get("mqtt")))

# no usable source at all: refuses to start
rc, cfg, err, data, tmp = run(dict(base, source_folders=["/nonexistent/matchy"])); temps.append(tmp)
check("no source refuses to start", rc != 0 and "No image source" in err, (rc, err[-300:]))

for t in temps + [src]:
    shutil.rmtree(t, ignore_errors=True)
print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
