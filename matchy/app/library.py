"""Image library: sources (folders, a static-file manifest, a daily-photo JSON), ids, sidecars, masters."""
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from PIL import Image

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
HASH_SAVE_SECS = 10  # a long cold scan checkpoints hash-cache.json this often
SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with", "over", "under", "from", "into"}


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "img"


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_image(path):
    """Image.open with Pillow's decompression-bomb guard turned into a ValueError (render.py sets the
    pixel cap and raises the 1x-2x warning as an error, so one cap applies everywhere)."""
    try:
        return Image.open(path)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        m = re.search(r"\((\d+) pixels\)", str(e))
        px = int(m.group(1)) // 1_000_000 if m else 0
        raise ValueError("image too large (%d MP, cap %d MP)" % (px, Image.MAX_IMAGE_PIXELS // 1_000_000)) from None


def title_case(text):
    """'A symphony in blue and green' -> 'A Symphony in Blue and Green'. Leaves mixed-case text alone."""
    if text != text[:1] + text[1:].lower():
        return text
    words = text.split()
    out = []
    for i, w in enumerate(words):
        out.append(w if (i and w.lower() in SMALL_WORDS) else w[:1].upper() + w[1:])
    return " ".join(out)


def http_get(url, timeout=30, max_bytes=60_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": "wallpaper-engine/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("response too large")
    return data


def display_fields(sc):
    return {k: sc.get(k, "") or "" for k in ("title", "place", "credit", "blurb")}


def rev_of(sc):
    blob = json.dumps(display_fields(sc), sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()[:6]


class Library:
    """Holds items {id: item}. item = {id, kind, path, sha256, meta{title,place,credit,context,collection}, order}.
    Sidecars (captions) live in <data>/library/<id>.json and are the source of truth for display text."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.data = cfg["data_dir"]
        self.lock = threading.RLock()
        self.items = {}
        self.sidecars = {}
        self._sc_mtime = {}
        for d in ("library", "masters", "originals", "cache"):
            os.makedirs(os.path.join(self.data, d), exist_ok=True)
        self.remote_path = os.path.join(self.data, "remote.json")
        self.remote = read_json(self.remote_path, {}) or {}
        self.hash_cache_path = os.path.join(self.data, "hash-cache.json")
        self.hash_cache = read_json(self.hash_cache_path, {}) or {}
        self.scan_lock = threading.Lock()
        self.skipped = {}  # id -> (sha256, reason) for sources that cannot be decoded (too large)
        self.changed = threading.Event()

    # ---------- sidecars ----------
    def sidecar_path(self, iid):
        return os.path.join(self.data, "library", iid + ".json")

    def load_sidecars(self):
        """(Re)load sidecars whose file changed on disk (hand edits are picked up this way)."""
        changed = False
        d = os.path.join(self.data, "library")
        for name in os.listdir(d):
            if not name.endswith(".json"):
                continue
            p = os.path.join(d, name)
            try:
                m = os.stat(p).st_mtime
            except OSError:
                continue
            iid = name[:-5]
            if self._sc_mtime.get(iid) != m:
                sc = read_json(p)
                if isinstance(sc, dict):
                    with self.lock:
                        self.sidecars[iid] = sc
                        self._sc_mtime[iid] = m
                    changed = True
        if changed:
            self.changed.set()
        return changed

    def save_sidecar(self, iid, sc):
        with self.lock:
            cur = self.sidecars.get(iid)
            if cur and cur.get("edited") and cur.get("blurb"):
                log("sidecar", iid, "is edited:true - not overwritten")
                return False
            p = self.sidecar_path(iid)
            write_json(p, sc)
            self.sidecars[iid] = sc
            self._sc_mtime[iid] = os.stat(p).st_mtime
        self.changed.set()
        return True

    def sync_sidecar_meta(self):
        """Unedited sidecars follow the live metadata for place/credit (and a known title), so a config fix shows up."""
        for iid, it in list(self.items.items()):
            sc = self.sidecars.get(iid)
            if not sc or sc.get("edited") or not sc.get("blurb"):
                continue
            want = {"place": it["meta"].get("place", ""), "credit": it["meta"].get("credit", "")}
            if it["meta"].get("title"):
                want["title"] = it["meta"]["title"]
            if any((sc.get(k) or "") != v for k, v in want.items()):
                new = dict(sc)
                new.update(want)
                self.save_sidecar(iid, new)
                log("sidecar", iid, "metadata refreshed")

    def status(self, iid):
        it = self.items.get(iid)
        if not it:
            return "missing"
        sc = self.sidecars.get(iid) or {}
        if sc.get("blurb") and (sc.get("edited") or sc.get("sha256") in (None, "", it["sha256"])):
            return "captioned"
        if sc.get("caption_failed_at") and sc.get("sha256") == it["sha256"]:
            return "failed"
        return "pending"

    def display(self, iid):
        """Text shown on the label: the sidecar if captioned, else the known metadata."""
        it = self.items.get(iid, {})
        meta = it.get("meta", {})
        sc = self.sidecars.get(iid) or {}
        if self.status(iid) == "captioned":
            d = display_fields(sc)
        else:
            d = {"title": meta.get("title") or meta.get("collection") or "", "place": meta.get("place", ""),
                 "credit": meta.get("credit", ""), "blurb": ""}
        d["rev"] = rev_of(d)
        return d

    # ---------- scanning ----------
    def _hash(self, path, st):
        key = "%s|%d|%d" % (path, st.st_size, int(st.st_mtime))
        h = self.hash_cache.get(key)
        if not h:
            h = sha256_file(path)
            self.hash_cache[key] = h
        return h

    def _walk(self):
        """Every image file under the configured folders as (folder_index, root, relpath, fullpath, sha)."""
        found = []
        n0 = saved = len(self.hash_cache)  # it only grows during the walk: the growth is the fresh hashes
        t0 = t = time.time()
        for fi, folder in enumerate(self.cfg["folders"]):
            root = folder["path"]
            if not os.path.isdir(root):
                log("folder missing:", root)
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for fn in sorted(filenames):
                    if fn.startswith(".") or os.path.splitext(fn)[1].lower() not in IMAGE_EXT:
                        continue
                    full = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    if st.st_size < 10_000:
                        continue
                    found.append((fi, root, os.path.relpath(full, root), full, self._hash(full, st)))
                    # checkpoint a cold scan, so a kill mid-walk keeps the hashes done so far
                    if len(self.hash_cache) > saved and time.time() - t >= HASH_SAVE_SECS:
                        write_json(self.hash_cache_path, self.hash_cache)
                        saved, t = len(self.hash_cache), time.time()
                        log("scan: %d files hashed so far, cache saved" % (saved - n0))
        hashed = len(self.hash_cache) - n0
        live = {k for k in self.hash_cache if k.split("|")[0] in {f[3] for f in found}}
        self.hash_cache = {k: v for k, v in self.hash_cache.items() if k in live}
        write_json(self.hash_cache_path, self.hash_cache)
        if hashed:
            log("scan: %d files, %d newly hashed in %.0fs" % (len(found), hashed, time.time() - t0))
        return found

    def scan_folders(self):
        """Walk every configured folder; dedupe by sha256; collapse -2k/-4k/... ladders; assign ids."""
        with self.scan_lock:  # one walk at a time: it writes the hash cache as it goes
            found = self._walk()
        dcfg = self.cfg["dedupe"]
        avoid = tuple(dcfg.get("avoid_prefixes", []))
        # 1) dedupe identical files: prefer not-avoided names, then folder order, then shortest path.
        by_sha = {}
        for f in found:
            by_sha.setdefault(f[4], []).append(f)
        uniq = []
        for sha, group in by_sha.items():
            group.sort(key=lambda f: (os.path.basename(f[2]).startswith(avoid), f[0], len(f[2]), f[2]))
            uniq.append(group[0])

        # 2) resolution ladders (name-2k, name-4k, ...): keep one per base name.
        prefer = str(dcfg.get("ladder_prefer", "4k")).lower()
        ladders, rest = {}, []
        for f in uniq:
            stem = os.path.splitext(os.path.basename(f[2]))[0]
            m = re.match(r"^(.*?)[-_](\d+)k$", stem, re.I)
            if m:
                ladders.setdefault((f[0], os.path.dirname(f[2]), m.group(1).lower()), []).append((int(m.group(2)), f))
            else:
                rest.append(f)
        want = int(re.sub(r"\D", "", prefer) or 4)
        for key, opts in ladders.items():
            opts.sort(key=lambda o: (abs(o[0] - want), o[0]))
            rest.append(opts[0][1])

        # 3) ids + metadata
        items = {}
        for fi, root, rel, full, sha in sorted(rest, key=lambda f: (f[0], f[2])):
            folder = self.cfg["folders"][fi]
            setname = os.path.dirname(rel).split(os.sep)[0] if os.sep in rel else ""
            scfg = (folder.get("sets") or {}).get(setname, {})
            prefix = scfg.get("id_prefix", slug(setname) if setname else "")
            stem = os.path.splitext(os.path.basename(rel))[0]
            stem = re.sub(r"[-_]\d+k$", "", stem, flags=re.I)
            num = re.match(r"^(\d+)", stem)
            if prefix and num:
                iid = "%s-%s" % (prefix, num.group(1))
            elif prefix and not slug(stem).startswith(prefix):
                iid = "%s-%s" % (prefix, slug(stem))
            else:
                iid = slug(stem)
            base, n = iid, 2
            while iid in items:
                iid, n = "%s-%d" % (base, n), n + 1
            collection = scfg.get("name") or (setname.replace("-", " ").title() if setname else "")
            items[iid] = {
                "id": iid, "kind": "folder", "path": full, "sha256": sha, "order": (0, fi, rel),
                "meta": {"title": scfg.get("title", ""), "place": "", "credit": scfg.get("credit", folder.get("credit", "")),
                         "context": scfg.get("context", ""), "collection": collection, "abstract": scfg.get("abstract", False)},
            }
        return items

    def remote_items(self):
        items = {}
        for iid, r in self.remote.items():
            path = os.path.join(self.data, "originals", r["file"])
            if os.path.exists(path):
                items[iid] = {"id": iid, "kind": r["kind"], "path": path, "sha256": r["sha256"],
                              "order": (1 if r["kind"] == "ha" else 2, 0, iid),
                              "meta": {k: r.get(k, "") for k in ("title", "place", "credit", "context", "collection")}}
        return items

    def rescan(self):
        items = self.scan_folders()
        items.update(self.remote_items())
        for iid, ov in (self.cfg.get("overrides") or {}).items():
            if iid in items:
                items[iid]["meta"].update(ov)
        with self.lock:
            added = set(items) - set(self.items)
            gone = set(self.items) - set(items)
            self.items = items
            self.skipped = {i: v for i, v in self.skipped.items() if i in items and items[i]["sha256"] == v[0]}
        self.load_sidecars()
        self.sync_sidecar_meta()
        if added or gone:
            log("library: %d items (+%d -%d)" % (len(items), len(added), len(gone)))
            self.changed.set()
        return added

    # ---------- remote sources ----------
    def _store_remote(self, iid, kind, data, meta):
        sha = hashlib.sha256(data).hexdigest()
        fn = iid + ".jpg"
        p = os.path.join(self.data, "originals", fn)
        with open(p + ".tmp", "wb") as f:
            f.write(data)
        os.replace(p + ".tmp", p)
        entry = {"kind": kind, "file": fn, "sha256": sha, "fetched_at": int(time.time())}
        entry.update(meta)
        self.remote[iid] = entry
        write_json(self.remote_path, self.remote)
        return sha

    def sync_static_manifest(self):
        """A web folder with manifest.json [{name, file}] and an optional CREDITS.txt ('Name: text')."""
        c = self.cfg.get("static_manifest") or {}
        if not c.get("enabled") or not c.get("base_url"):
            return []
        base = c["base_url"].rstrip("/") + "/"
        manifest = json.loads(http_get(base + c.get("manifest", "manifest.json"), 30))
        credits = {}
        if c.get("credits"):
            try:
                txt = http_get(base + c["credits"], 30).decode("utf-8", "replace")
                for line in txt.splitlines():
                    if ":" in line and not line.startswith(" "):
                        k, v = line.split(":", 1)
                        credits[k.strip()] = v.strip()
            except Exception as e:  # credits are optional
                log("credits fetch failed:", e)
        skip = set(c.get("skip_files", []))
        prefix = c.get("id_prefix", "web")
        new = []
        for entry in manifest:
            name, fn = (entry.get("name") or "").strip(), (entry.get("file") or "").strip()
            if not fn or fn in skip or "/" in fn or ".." in fn or os.path.splitext(fn)[1].lower() not in IMAGE_EXT:
                continue
            iid = "%s-%s" % (prefix, slug(os.path.splitext(fn)[0]))
            meta = {"title": name, "collection": c.get("collection", "")}
            meta.update(parse_credit_line(credits.get(name, "")))
            known = self.remote.get(iid)
            if known:
                if any(known.get(k) != v for k, v in meta.items()):
                    known.update(meta)
                    write_json(self.remote_path, self.remote)
                continue
            try:
                data = http_get(base + urllib.parse.quote(fn), 60)
            except Exception as e:
                log("fetch", fn, "failed:", e)
                continue
            self._store_remote(iid, "ha", data, meta)
            new.append(iid)
            log("added", iid, "from", fn)
        return new

    def sync_daily(self):
        """Daily photo JSON {date, caption|title, place, copyright} + a fixed image URL; one item per date."""
        c = self.cfg.get("daily") or {}
        if not c.get("enabled") or not c.get("json_url"):
            return None
        j = json.loads(http_get(c["json_url"], 30))
        date = re.sub(r"\D", "", str(j.get("date", "")))[:8]
        if len(date) != 8:
            return None
        iid = "%s-%s" % (c.get("id_prefix", "bing"), date)
        if iid in self.remote:
            return None
        data = http_get(c["image_url"], 60)
        sha = hashlib.sha256(data).hexdigest()
        prev = [r for r in self.remote.values() if r["kind"] == "daily"]
        if prev and max(prev, key=lambda r: r.get("date", ""))["sha256"] == sha:
            log("daily", iid, ": image not updated yet, will retry")
            return None
        title = (j.get("title") or j.get("caption") or "").strip()
        meta = {"title": title_case(title) if title else "", "place": (j.get("place") or "").strip(),
                "credit": "", "context": (j.get("copyright") or "").strip(), "collection": c.get("collection", ""),
                "date": date}
        m = re.search(r"\(\s*(©[^)]*)\)", j.get("copyright", ""))
        if m:
            meta["credit"] = re.sub(r"\s*/\s*", " / ", m.group(1).strip())
        self._store_remote(iid, "daily", data, meta)
        log("added daily", iid, meta["title"])
        return iid

    # ---------- masters ----------
    def master_path(self, iid):
        it = self.items[iid]
        return os.path.join(self.data, "masters", "%s-%s.jpg" % (iid, it["sha256"][:8]))

    def ensure_master(self, iid):
        p = self.master_path(iid)
        if os.path.exists(p):
            return p
        it = self.items[iid]
        if iid in self.skipped:  # reported once already; it stays in the library so the gallery can say why
            raise ValueError(self.skipped[iid][1])
        cap = int(self.cfg["master_max_px"])
        t = time.time()
        try:
            im = open_image(it["path"])
        except ValueError as e:
            self.skipped[iid] = (it["sha256"], str(e))
            log("master", iid, "skipped:", e, "-", it["path"])
            raise
        with im:
            if im.format == "JPEG":
                im.draft("RGB", (cap, cap))
            im = im.convert("RGB")
            if max(im.size) > cap:
                im.thumbnail((cap, cap), Image.LANCZOS, reducing_gap=3.0)
            im.save(p + ".tmp", "JPEG", quality=93, optimize=True)
        os.replace(p + ".tmp", p)
        # drop masters of older versions of this id
        for fn in os.listdir(os.path.dirname(p)):
            if fn.startswith(iid + "-") and fn != os.path.basename(p) and re.fullmatch(re.escape(iid) + r"-[0-9a-f]{8}\.jpg", fn):
                os.remove(os.path.join(os.path.dirname(p), fn))
        log("master", iid, "%.1fs" % (time.time() - t))
        return p


def parse_credit_line(text):
    """'<desc> by <author>, <licence>, <url>' (Wikimedia) or '<desc> (© who) via Bing'."""
    out = {}
    if not text:
        return out
    m = re.match(r"^(.*?)\s*\((©[^)]*)\)\s*(?:via\s+(\w+))?\s*$", text)
    if m:
        out["context"] = m.group(1).strip()
        out["place"] = m.group(1).strip()
        out["credit"] = re.sub(r"\s*/\s*", " / ", m.group(2).strip())
        return out
    m = re.match(r"^(.*?)\s+by\s+(.*?),\s*(CC[^,]*|Public domain[^,]*),\s*(https?://\S+)", text, re.I)
    if m:
        desc = re.sub(r"\.(jpe?g|png|tiff?)$", "", m.group(1).strip(), flags=re.I)
        out["context"] = "source file name: " + desc
        host = urllib.parse.urlparse(m.group(4)).hostname or ""
        src = "Wikimedia Commons" if "wikimedia" in host else host
        out["credit"] = "%s · %s · %s" % (m.group(2).strip(), m.group(3).strip(), src)
        return out
    out["context"] = text
    return out
