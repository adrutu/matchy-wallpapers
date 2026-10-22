"""Validate matchy/config.yaml with the Home Assistant Supervisor's own schema, at a pinned release.

Runs the same checks a store reload does (supervisor/store/data.py -> SCHEMA_APP_CONFIG), then
the start-time check of the default `options` against `schema` (supervisor/apps/options.py ->
AppOptions). On top of that it fails where the Supervisor only logs or stays silent:
  - a key that the schema drops (`extra=vol.REMOVE_EXTRA`) or a migration renames;
  - any advisory the Supervisor logs for a local app (deprecated field, legacy map type, ...);
  - an option with no matching schema entry (AppOptions ignores it with a warning);
  - a translations/*.yaml key that is not an option, field or port, and an option English leaves unnamed.

Usage: python3 tests/validate_config.py [--config matchy/config.yaml] [--supervisor-src DIR]
Without --supervisor-src it clones the pinned tag into ~/.cache/matchy/ (needs git) and checks the
commit. Needs Python 3.14 (the Supervisor source uses `except A, B:`) and the Supervisor's pins:
  pip install --require-hashes -r tests/requirements/validate.txt   (voluptuous 0.16.0, awesomeversion
  25.8.0, aiohttp 3.14.5, PyYAML 6.0.3 and their dependencies, hash-pinned)
Exits 0 when valid, 1 when not, 2 when the Supervisor source could not be loaded."""
import argparse
import copy
import importlib
import logging
import os
import subprocess
import sys
import types
from pathlib import Path

# The Supervisor release this file was checked against. Bump both together.
SUPERVISOR_TAG = "2026.10.2"
SUPERVISOR_COMMIT = "c9e000812b7131fa1f1b72b483555083929d7f83"
SUPERVISOR_REPO = "https://github.com/home-assistant/supervisor.git"

ROOT = Path(__file__).resolve().parent.parent


def fetch_source():
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "matchy"
    src = cache / ("supervisor-" + SUPERVISOR_TAG)
    if not (src / "supervisor").is_dir():
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", SUPERVISOR_TAG,
                        "-c", "advice.detachedHead=false", SUPERVISOR_REPO, str(src)], check=True)
    return src


def check_commit(src):
    if not (src / ".git").exists():
        print("warning: %s is not a git checkout; cannot confirm it is %s" % (src, SUPERVISOR_TAG))
        return True
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    if head != SUPERVISOR_COMMIT:
        print("error: %s is at %s, expected %s (tag %s)" % (src, head[:12], SUPERVISOR_COMMIT[:12],
                                                         SUPERVISOR_TAG))
        return False
    return True


def load_supervisor(src):
    """Import supervisor.apps.validate and supervisor.apps.options from src without running the
    Supervisor. Package __init__ files are skipped (they pull in the whole runtime), and modules
    that only matter at runtime are stubbed: nothing the config schema uses comes from a stub."""
    pkg = src / "supervisor"
    for name in ("supervisor", "supervisor.apps", "supervisor.docker", "supervisor.hardware",
                 "supervisor.jobs", "supervisor.utils"):
        mod = types.ModuleType(name)
        mod.__path__ = [str(pkg.joinpath(*name.split(".")[1:]))]
        sys.modules[name] = mod
    # Third-party runtime libraries imported at module level by leaf modules we need.
    for name in ("aiodocker", "pyudev", "ciso8601"):
        sys.modules[name] = types.ModuleType(name)
    # options.py subclasses CoreSysAttributes; the real coresys imports the whole Supervisor.
    # AppOptions only touches coresys for "!secret" values and device options, neither used here.
    coresys = types.ModuleType("supervisor.coresys")
    coresys.CoreSys = type("CoreSys", (), {})
    coresys.CoreSysAttributes = type("CoreSysAttributes", (), {})
    sys.modules["supervisor.coresys"] = coresys
    validate = importlib.import_module("supervisor.apps.validate")
    options = importlib.import_module("supervisor.apps.options")
    return validate, options


class Advisories(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        if record.levelno >= logging.WARNING:
            self.messages.append(record.getMessage())


def dropped_keys(raw, kept, path=""):
    """Keys present in the raw config but missing after validation, recursively."""
    out = []
    if isinstance(raw, dict) and isinstance(kept, dict):
        for key, value in raw.items():
            where = "%s.%s" % (path, key) if path else str(key)
            if key not in kept:
                out.append(where)
            else:
                out += dropped_keys(value, kept[key], where)
    elif isinstance(raw, list) and isinstance(kept, list) and len(raw) == len(kept):
        for i, (a, b) in enumerate(zip(raw, kept)):
            out += dropped_keys(a, b, "%s[%d]" % (path, i))
    return out


def main():
    p = argparse.ArgumentParser(description="validate config.yaml with the Supervisor's schema")
    p.add_argument("--config", default=str(ROOT / "matchy" / "config.yaml"))
    p.add_argument("--supervisor-src", metavar="DIR",
                   help="a checkout of home-assistant/supervisor at tag " + SUPERVISOR_TAG)
    ns = p.parse_args()

    if sys.version_info < (3, 14):
        print("error: needs Python 3.14+, the Supervisor %s source does not parse on %d.%d"
              % (SUPERVISOR_TAG, *sys.version_info[:2]))
        sys.exit(2)
    try:
        import voluptuous as vol
        from voluptuous.humanize import humanize_error
        import yaml
        src = Path(ns.supervisor_src) if ns.supervisor_src else fetch_source()
        if not check_commit(src):
            sys.exit(2)
        validate, options = load_supervisor(src)
    except (ImportError, OSError, subprocess.CalledProcessError) as e:
        print("error: cannot load the Supervisor schema: %s" % e)
        sys.exit(2)

    advisories = Advisories()
    logging.getLogger("supervisor").addHandler(advisories)
    logging.getLogger("supervisor").setLevel(logging.DEBUG)

    with open(ns.config, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    errors = []

    # 1. What `ha store reload` does for a local app (always the verbose schema).
    try:
        config = validate.SCHEMA_APP_CONFIG(copy.deepcopy(raw))
    except vol.Invalid as e:
        print("FAIL %s: %s" % (ns.config, humanize_error(raw, e)))
        sys.exit(1)
    for key in dropped_keys(raw, config):
        errors.append("key '%s' is not in the Supervisor schema and would be silently dropped" % key)

    # 2. What install/start does with the default options.
    try:
        opts = options.AppOptions(None, config["schema"] or {}, config["name"], config["slug"])
        opts(copy.deepcopy(config["options"]))
    except vol.Invalid as e:
        errors.append("default options do not match the schema: %s" % e)

    # 3. translations/*.yaml, as the store reads them (an invalid file is only logged and skipped).
    #    On top: every translated key must be a real option or port, and English must cover them all.
    tdir = Path(ns.config).parent / "translations"
    translations = sorted(tdir.glob("*.yaml")) if tdir.is_dir() else []
    schema = config["schema"] or {}
    for path in translations:
        with open(path, encoding="utf-8") as f:
            traw = yaml.safe_load(f) or {}
        try:
            tr = validate.SCHEMA_APP_TRANSLATIONS(copy.deepcopy(traw))
        except vol.Invalid as e:
            errors.append("%s: %s" % (path.name, humanize_error(traw, e)))
            continue
        for key in dropped_keys(traw, tr):
            errors.append("%s: key '%s' is not in the translation schema" % (path.name, key))
        conf = tr.get("configuration") or {}
        for key, entry in conf.items():
            if key not in schema:
                errors.append("%s: '%s' is not an option" % (path.name, key))
                continue
            sub = schema[key][0] if isinstance(schema[key], list) and schema[key] else schema[key]
            for field in entry.get("fields") or {}:
                if not isinstance(sub, dict) or field not in sub:
                    errors.append("%s: '%s.%s' is not a field of that option" % (path.name, key, field))
        for port in tr.get("network") or {}:
            if port not in (config.get("ports") or {}):
                errors.append("%s: network '%s' is not a port" % (path.name, port))
        if path.stem == "en":
            for key in schema:
                if key not in conf:
                    errors.append("en.yaml: option '%s' has no name" % key)
                elif isinstance(schema[key], list) and schema[key] and isinstance(schema[key][0], dict):
                    for field in schema[key][0]:
                        if field not in (conf[key].get("fields") or {}):
                            errors.append("en.yaml: field '%s.%s' has no name" % (key, field))

    errors += ["Supervisor advisory: " + m for m in advisories.messages]
    for e in errors:
        print("FAIL " + e)
    if errors:
        sys.exit(1)
    print("OK %s: valid for Supervisor %s (%s), %d keys kept, default options match the schema, %d "
          "translation file(s)" % (os.path.relpath(ns.config), SUPERVISOR_TAG, SUPERVISOR_COMMIT[:7],
                                   len(raw), len(translations)))


if __name__ == "__main__":
    main()
