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
bashio::config.exists() { [[ "$(jq -c "(.${1}) // null" "$OPTIONS")" != "null" ]]; }
bashio::config.has_value() {
    local v; v="$(jq -c "(.${1}) // null" "$OPTIONS")"
    [[ "$v" != "null" && "$v" != '""' && "$v" != "[]" ]]
}
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
    script = script.replace("exec python3 /app/engine.py", "exit 0")
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
rc, cfg, err, data, tmp = run(dict(base), fake_bin("exit 7")); temps.append(tmp)
check("Home Assistant not answering: no location, still starts", rc == 0 and cfg and "location" not in cfg
      and "location is not known" in err, (rc, err[-300:]))

# no usable source at all: refuses to start
rc, cfg, err, data, tmp = run(dict(base, source_folders=["/nonexistent/matchy"])); temps.append(tmp)
check("no source refuses to start", rc != 0 and "No image source" in err, (rc, err[-300:]))

for t in temps + [src]:
    shutil.rmtree(t, ignore_errors=True)
print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
