"""Image library: sources (folders, a static-file manifest, a daily-photo JSON, open-access art), ids,
sidecars, masters."""
import fnmatch
import hashlib
import io
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageOps

import artsources

import palette

import mood

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ART_SKIPPED_MAX = 5000  # remembered works that had no usable image, newest kept
HASH_SAVE_SECS = 10  # a long cold scan checkpoints hash-cache.json this often
# Bump when masters are made differently, so the ones on disk are rebuilt (2: EXIF orientation, 16-bit greyscale).
MASTER_FORMAT = 2
SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with", "over", "under", "from", "into"}


def log(*a):
    # A file name that is not UTF-8 arrives with surrogates, which a strict stdout refuses to print.
    line = " ".join(str(x) for x in a).encode("utf-8", "backslashreplace").decode("utf-8")
    print(time.strftime("%Y-%m-%d %H:%M:%S"), line, flush=True)


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "img"


def write_json(path, obj):
    tmp = path + ".tmp"
    # surrogateescape: file names that are not UTF-8 (held as surrogates) go back to disk as their own bytes
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
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


def num(v, default=0.0):
    """A JSON number, or `default` for anything else (a hand-edited sidecar may hold any type)."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def http_get(url, timeout=30, max_bytes=60_000_000):
    # urllib would also open file:, data: and ftp: URLs; a remote source is a web server.
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("only http(s) URLs are fetched: %r" % url)
    req = urllib.request.Request(url, headers={"User-Agent": "wallpaper-engine/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("response too large")
    return data


def display_fields(sc):
    """The four label fields as text. A hand-edited sidecar may hold a number or a list there; the
    label, the page and the JSON views all expect strings, so anything else is shown as its text."""
    out = {}
    for k in ("title", "place", "credit", "blurb"):
        v = sc.get(k) or ""
        out[k] = v if isinstance(v, str) else str(v)
    return out


NO_COLOURS = {"palette": [], "accent": None, "gnome_accent": None}


def colour_fields(sc, sha256):
    """The picture's colours from its sidecar, or NO_COLOURS until they are computed for this version of
    the source (`sc["colours"]`: palette, accent, gnome_accent, sha256, version)."""
    c = sc.get("colours")
    if not isinstance(c, dict) or c.get("sha256") != sha256 or c.get("version") != palette.VERSION:
        return dict(NO_COLOURS)
    pal = [x for x in c.get("palette") or [] if isinstance(x, str)]
    acc = c.get("accent") if isinstance(c.get("accent"), str) else None
    name = c.get("gnome_accent") if c.get("gnome_accent") in palette.GNOME_NAMES else None
    return {"palette": pal, "accent": acc, "gnome_accent": name}


def rev_of(sc, picture="", focus=None):
    """Six hex digits that change with the label text, with `picture` (what the master is cut from, so a
    replaced file or a new master format gets new URLs and cache keys even when the label text stays the
    same) and with the focus point in use (a picture without one keeps the rev it would have had)."""
    fields = display_fields(sc)
    if focus:
        fields["focus"] = [round(focus["x"], 3), round(focus["y"], 3)]
    blob = json.dumps(fields, sort_keys=True, ensure_ascii=False).encode() + picture.encode()
    return hashlib.sha256(blob).hexdigest()[:6]


FOCUS_SOURCES = ("hand", "model", "auto")  # best first; a hand focus is never overwritten


def valid_focus(f):
    """A sidecar's focus {x, y, source, ...} if it is usable (x and y fractions 0-1), else None."""
    if not isinstance(f, dict) or f.get("source") not in FOCUS_SOURCES:
        return None
    xy = (f.get("x"), f.get("y"))
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in xy):
        return None
    return f


class Library:
    """Holds items {id: item}. item = {id, kind, path, sha256, meta{title,place,credit,context,collection}, order}.
    Sidecars (captions) live in <data>/library/<id>.json and are the source of truth for display text.
    They also carry the picture's colours (`colours`, see palette.py) and its `tags` (words from mood.TAGS,
    for the mood-aware picks), which are not a caption: a hand-edited caption keeps them, a re-caption
    does not touch the colours, and neither changes the rev."""

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
        self.art_skipped_path = os.path.join(self.data, "art-skipped.json")
        self.art_skipped = read_json(self.art_skipped_path, {}) or {}
        self.scan_lock = threading.Lock()
        self.master_locks = {}  # id -> lock while its master is built: two builds would share one .tmp file
        self.skipped = {}  # id -> (sha256, reason) for sources that cannot be decoded (too large, or broken)
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

    def save_sidecar(self, iid, sc, force=False):
        """force: replace even a hand-edited caption (a hand edit, or a re-caption asked for in the panel)."""
        with self.lock:
            cur = self.sidecars.get(iid)
            if cur and cur.get("edited") and cur.get("blurb") and not force:
                log("sidecar", iid, "is edited:true - not overwritten")
                return False
            # The colours are not part of the caption: a caption written from scratch keeps them.
            if cur and "colours" in cur and "colours" not in sc:
                sc = dict(sc, colours=cur["colours"])
            p = self.sidecar_path(iid)
            write_json(p, sc)
            self.sidecars[iid] = sc
            self._sc_mtime[iid] = os.stat(p).st_mtime
        self.changed.set()
        return True

    def colours(self, iid):
        """palette / accent / gnome_accent for the picture, or NO_COLOURS until computed."""
        it = self.items.get(iid)
        if not it:
            return dict(NO_COLOURS)
        return colour_fields(self.sidecars.get(iid) or {}, it["sha256"])

    def ensure_colours(self, iid):
        """Compute the picture's colours from its master once per version of the source, into the sidecar.
        Returns the sidecar's colours, or None when the feature is off. Raises what ensure_master raises."""
        if not (self.cfg.get("palette") or {}).get("enabled", True):
            return None
        it = self.items.get(iid)
        if not it:
            return None
        c = (self.sidecars.get(iid) or {}).get("colours")
        if isinstance(c, dict) and c.get("sha256") == it["sha256"] and c.get("version") == palette.VERSION:
            return c
        master = self.ensure_master(iid)
        t = time.time()
        with Image.open(master) as im:
            im.draft("RGB", (palette.THUMB * 4, palette.THUMB * 4))  # a JPEG decodes at 1/8 scale: fast
            c = palette.analyse(im)
        c.update(sha256=it["sha256"], version=palette.VERSION)
        with self.lock:
            sc = dict(self.sidecars.get(iid) or {})
            sc["colours"] = c
            p = self.sidecar_path(iid)
            write_json(p, sc)
            self.sidecars[iid] = sc
            self._sc_mtime[iid] = os.stat(p).st_mtime
        log("colours %s: %s, accent %s (%s) %.0f ms" % (iid, " ".join(c["palette"]), c["accent"] or "none",
                                                        c["gnome_accent"], (time.time() - t) * 1000))
        return c

    def tags(self, iid):
        """The picture's tags, vocabulary words only (a sidecar edited by hand may hold anything)."""
        return mood.clean_tags((self.sidecars.get(iid) or {}).get("tags"))

    def needs_tags(self, iid, retry_s):
        """A captioned picture whose sidecar has no `tags` yet (captioned before tags existed), unless a
        tag request for it failed less than retry_s ago. A tag-only request fills it in, whatever the caption's
        `edited` flag: tags are not the caption."""
        if self.status(iid) != "captioned":
            return False
        sc = self.sidecars.get(iid) or {}
        return "tags" not in sc and time.time() - num(sc.get("tags_failed_at")) >= retry_s

    def set_tags(self, iid, tags=None, failed=None):
        """Write the tags (or note a failed tag request) into the sidecar, leaving the caption as it is:
        this bypasses save_sidecar's hand-edit guard on purpose."""
        with self.lock:
            sc = dict(self.sidecars.get(iid) or {})
            if failed:
                sc["tags_failed_at"] = time.time()
                sc["tags_error"] = failed[:300]
            else:
                sc["tags"] = list(tags or [])
                sc.pop("tags_failed_at", None)
                sc.pop("tags_error", None)
            p = self.sidecar_path(iid)
            write_json(p, sc)
            self.sidecars[iid] = sc
            self._sc_mtime[iid] = os.stat(p).st_mtime

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

    def kids_status(self, iid):
        """The kids caption, kept in the same sidecar under kids_*: written for this file (or by hand),
        failed for this file, or pending."""
        it = self.items.get(iid)
        if not it:
            return "missing"
        sc = self.sidecars.get(iid) or {}
        if sc.get("kids_blurb") and (sc.get("kids_edited") or sc.get("kids_sha256") == it["sha256"]):
            return "captioned"
        if sc.get("kids_caption_failed_at") and sc.get("kids_sha256") == it["sha256"]:
            return "failed"
        return "pending"

    def update_sidecar(self, iid, fields, drop=()):
        """Merge fields into the sidecar (and remove the keys in drop), whatever its edited flag: for the
        kids_* fields, which a hand edit of the main caption does not cover."""
        with self.lock:
            sc = dict(self.sidecars.get(iid) or {})
            for k in drop:
                sc.pop(k, None)
            sc.update(fields)
            p = self.sidecar_path(iid)
            write_json(p, sc)
            self.sidecars[iid] = sc
            self._sc_mtime[iid] = os.stat(p).st_mtime
        self.changed.set()

    def display(self, iid):
        """Text shown on the label: the sidecar if captioned, else the known metadata. kids_blurb is
        the kids caption, or "" until there is one."""
        it = self.items.get(iid, {})
        meta = it.get("meta", {})
        sc = self.sidecars.get(iid) or {}
        if self.status(iid) == "captioned":
            d = display_fields(sc)
        else:
            d = {"title": meta.get("title") or meta.get("collection") or "", "place": meta.get("place", ""),
                 "credit": meta.get("credit", ""), "blurb": ""}
        d["rev"] = rev_of(d, os.path.basename(self.master_path(iid)) if it else "", self.focus(iid))
        d["kids_blurb"] = (sc.get("kids_blurb") or "") if self.kids_status(iid) == "captioned" else ""
        if d["kids_blurb"]:  # only then, so a library without kids captions keeps its URLs
            d["rev"] = hashlib.sha256((d["rev"] + "|kids|" + d["kids_blurb"]).encode()).hexdigest()[:6]
        return d

    # ---------- focus points ----------
    def stored_focus(self, iid):
        """The sidecar's focus whatever its source, or None."""
        return valid_focus((self.sidecars.get(iid) or {}).get("focus"))

    def focus(self, iid):
        """The focus point crops follow, or None for a centred crop. A hand focus always counts; one
        from the captioner or the picture itself only with focus_auto on, and only for the version of
        the picture it was found on."""
        f = self.stored_focus(iid)
        if not f or f["source"] == "hand":
            return f
        it = self.items.get(iid)
        if not self.cfg.get("focus_auto") or not it or f.get("sha256") != it["sha256"]:
            return None
        return f

    def save_focus(self, iid, focus, keep_hand=True):
        """Write (or, with None, clear) the focus in iid's sidecar and leave the rest of it alone. A
        focus set by hand stays unless keep_hand is False. True when something was written."""
        with self.lock:
            it = self.items.get(iid)
            if not it:
                return False
            sc = dict(self.sidecars.get(iid) or {"id": iid, "source": it["path"]})
            cur = valid_focus(sc.get("focus"))
            if keep_hand and cur and cur["source"] == "hand":
                return False
            if focus is None:
                if "focus" not in sc:
                    return False
                sc.pop("focus")
            else:
                sc["focus"] = focus
            # force: the caption in the same file stays as it is, edited or not
            return self.save_sidecar(iid, sc, force=True)

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
        own = os.path.realpath(self.data)  # our own masters and cache must never come back as pictures
        for fi, folder in enumerate(self.cfg["folders"]):
            root = folder["path"]
            if not os.path.isdir(root):
                log("folder missing:", root)
                continue
            if os.path.realpath(root) == own or os.path.realpath(root).startswith(own + os.sep):
                log("folder skipped, it is the add-on's own data:", root)
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")
                                     and os.path.realpath(os.path.join(dirpath, d)) != own)
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
                    try:
                        sha = self._hash(full, st)
                    except OSError as e:  # gone since the listing, or unreadable: the rest of the scan goes on
                        log("scan: cannot read, skipped:", full, "-", e)
                        continue
                    found.append((fi, root, os.path.relpath(full, root), full, sha))
                    # checkpoint a cold scan, so a kill mid-walk keeps the hashes done so far
                    if len(self.hash_cache) > saved and time.time() - t >= HASH_SAVE_SECS:
                        write_json(self.hash_cache_path, self.hash_cache)
                        saved, t = len(self.hash_cache), time.time()
                        log("scan: %d files hashed so far, cache saved" % (saved - n0))
        hashed = len(self.hash_cache) - n0
        paths = {f[3] for f in found}
        live = {k for k in self.hash_cache if k.rsplit("|", 2)[0] in paths}  # a path may hold "|" itself
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
        # ids + metadata. A folder may hold meta.json {"file.jpg": {title, place, credit, context}}.
        items = {}
        meta_files = {}
        for fi, root, rel, full, sha in sorted(rest, key=lambda f: (f[0], f[2])):
            folder = self.cfg["folders"][fi]
            setname = os.path.dirname(rel).split(os.sep)[0] if os.sep in rel else ""
            scfg = (folder.get("sets") or {}).get(setname, {})
            # slugged: an id ends up in URL paths, so "My Art" must become my-art, not "My Art-1"
            prefix = slug(scfg["id_prefix"]) if scfg.get("id_prefix") else (slug(setname) if setname else "")
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
            # a folder name that is not UTF-8 holds surrogates, which no page or label can show
            shown = os.fsencode(setname).decode("utf-8", "replace")
            collection = scfg.get("name") or (shown.replace("-", " ").title() if setname else "")
            items[iid] = {
                "id": iid, "kind": "folder", "path": full, "sha256": sha, "order": (0, fi, rel),
                "meta": {"title": scfg.get("title", ""), "place": "", "credit": scfg.get("credit", folder.get("credit", "")),
                         "context": scfg.get("context", ""), "collection": collection, "abstract": scfg.get("abstract", False)},
            }
            mpath = os.path.join(os.path.dirname(full), folder.get("meta_file", "meta.json"))
            if mpath not in meta_files:
                meta_files[mpath] = read_json(mpath, {}) or {}
            per = meta_files[mpath].get(os.path.basename(rel)) if isinstance(meta_files[mpath], dict) else None
            if isinstance(per, dict):
                items[iid]["meta"].update({k: str(v) for k, v in per.items()
                                           if k in ("title", "place", "credit", "context") and v is not None})
        return items

    def remote_items(self):
        items = {}
        for iid, r in self.remote.items():
            path = os.path.join(self.data, "originals", r["file"])
            if os.path.exists(path):
                items[iid] = {"id": iid, "kind": r["kind"], "path": path, "sha256": r["sha256"],
                              "order": (1 if r["kind"] == "ha" else 2, 0, iid),
                              "meta": {k: r.get(k, "") for k in ("title", "place", "credit", "context", "collection")}}
                # open-access art: the facts the captioner may state, and where the work came from
                items[iid]["meta"].update({k: r[k] for k in ("facts", "artwork", "source_url") if r.get(k)})
        return items

    def rescan(self):
        items = self.scan_folders()
        # Every folder there and holding pictures: only then can a missing picture be taken as deleted, not as
        # an unmounted share or an empty mount point.
        whole = {it["order"][1] for it in items.values()} == set(range(len(self.cfg["folders"])))
        items.update(self.remote_items())
        for iid, ov in (self.cfg.get("overrides") or {}).items():
            if iid in items:
                items[iid]["meta"].update(ov)
        excl = self.cfg.get("exclude") or []  # id globs kept out of the library, e.g. "aether-*"
        for iid in [i for i in items if any(fnmatch.fnmatchcase(i, pat) for pat in excl)]:
            del items[iid]
        with self.lock:
            added = set(items) - set(self.items)
            gone = set(self.items) - set(items)
            self.items = items
            self.skipped = {i: v for i, v in self.skipped.items() if i in items and items[i]["sha256"] == v[0]}
        self.load_sidecars()
        self.sync_sidecar_meta()
        if whole:
            self.drop_orphan_masters(items)
        if added or gone:
            log("library: %d items (+%d -%d)" % (len(items), len(added), len(gone)))
            self.changed.set()
        return added

    def drop_orphan_masters(self, items):
        """Masters of pictures no longer in the library (deleted, renamed or excluded): they are in backups."""
        d = os.path.join(self.data, "masters")
        want = {os.path.basename(self.master_path(i)) for i in items}
        gone = [fn for fn in os.listdir(d) if fn.endswith(".jpg") and fn not in want]  # never a .tmp being written
        for fn in gone:
            try:
                os.remove(os.path.join(d, fn))
            except OSError:
                pass
        if gone:
            log("masters: removed %d of pictures no longer in the library" % len(gone))

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
            iid = first = "%s-%s" % (prefix, slug(os.path.splitext(fn)[0]))
            n = 2
            while (self.remote.get(iid) or {}).get("manifest_file", fn) != fn:  # another file has that slug
                iid, n = "%s-%d" % (first, n), n + 1
            meta = {"title": name, "collection": c.get("collection", ""), "manifest_file": fn}
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
        iid = "%s-%s" % (c.get("id_prefix", "daily"), date)
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
        m = re.search(r"\(\s*(©[^)]*)\)", j.get("copyright") or "")
        if m:
            meta["credit"] = re.sub(r"\s*/\s*", " / ", m.group(1).strip())
        self._store_remote(iid, "daily", data, meta)
        log("added daily", iid, meta["title"])
        return iid

    def sync_art(self, rng=None):
        """Open-access art (artsources.py): up to `per_day` new works a day from each enabled source,
        each at least `min_px` on its long side. Returns the new ids; a failing source is logged and
        waits for the next check."""
        a = self.cfg.get("art_sources") or {}
        rng = rng or random.Random()
        new = []
        for name in artsources.SOURCES:
            c = a.get(name) or {}
            if not c.get("enabled"):
                continue
            client = artsources.Client(a.get("contact", ""), a.get("pause_seconds", 1.0))
            try:
                new += self._sync_art_source(name, c, client, rng)
            except Exception as e:
                log("art source", name, "failed:", str(e)[:300])
        return new

    def _sync_art_source(self, name, c, client, rng):
        today = time.strftime("%Y-%m-%d")
        got = sum(1 for r in self.remote.values() if r.get("kind") == name
                  and time.strftime("%Y-%m-%d", time.localtime(r.get("fetched_at", 0))) == today)
        want = int(c.get("per_day", 1)) - got
        if want <= 0:
            return []
        min_px = int(c.get("min_px", 2560))
        seen = set(self.remote) | set(self.art_skipped)
        new = []
        for w in artsources.ADAPTERS[name](c, client, rng, seen):
            iid = w["id"]
            if iid in seen:
                continue
            seen.add(iid)
            if w.get("skip"):
                self._art_skip(iid, w["skip"])
                continue
            if w.get("size") and max(w["size"]) < min_px:  # the API says how big it is: no download
                self._art_skip(iid, "%dx%d is under %d px" % (*w["size"], min_px))
                continue
            try:
                data = client.get(w["image_url"], headers=w.get("image_headers"))
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):  # refused or throttled: stop asking this source until the next check
                    raise
                log("art", iid, "image download failed:", e)
                continue
            except Exception as e:  # a network error may pass: not remembered, tried again later
                log("art", iid, "image download failed:", str(e)[:200])
                continue
            try:
                with open_image(io.BytesIO(data)) as im:
                    size = im.size
            except (ValueError, OSError) as e:
                self._art_skip(iid, "no usable image (%s)" % str(e)[:120])
                continue
            if max(size) < min_px:
                self._art_skip(iid, "%dx%d is under %d px" % (*size, min_px))
                continue
            meta = {k: w[k] for k in ("title", "place", "credit", "context", "facts", "artwork", "source_url",
                                      "licence") if w.get(k) not in (None, "")}
            meta.update(collection={"aic": "Art Institute of Chicago", "met": "The Metropolitan Museum of Art",
                                    "potd": "Wikimedia Commons Picture of the Day"}[name], size="%dx%d" % size)
            self._store_remote(iid, name, data, meta)
            log("added", iid, "%dx%d:" % size, meta.get("title", ""), "|", meta.get("credit", ""))
            new.append(iid)
            if len(new) >= want:
                break
        return new

    def _art_skip(self, iid, reason):
        log("art", iid, "skipped:", reason)
        self.art_skipped[iid] = {"reason": reason, "at": int(time.time())}
        if len(self.art_skipped) > ART_SKIPPED_MAX:  # the oldest go first
            keep = sorted(self.art_skipped.items(), key=lambda kv: (kv[1] or {}).get("at", 0))[-ART_SKIPPED_MAX:]
            self.art_skipped = dict(keep)
        write_json(self.art_skipped_path, self.art_skipped)

    # ---------- masters ----------
    def master_path(self, iid):
        it = self.items[iid]
        tag = hashlib.sha256(("%s|%d" % (it["sha256"], MASTER_FORMAT)).encode()).hexdigest()[:8]
        return os.path.join(self.data, "masters", "%s-%s.jpg" % (iid, tag))

    def ensure_master(self, iid):
        p = self.master_path(iid)
        if os.path.exists(p):
            return p
        with self.lock:
            lk = self.master_locks.setdefault(iid, threading.Lock())
        try:
            with lk:
                if os.path.exists(p):  # built by the thread we waited for
                    return p
                return self._make_master(iid, p)
        finally:
            with self.lock:
                self.master_locks.pop(iid, None)

    def _make_master(self, iid, p):
        it = self.items[iid]
        if iid in self.skipped:  # reported once already; it stays in the library so the gallery can say why
            raise ValueError(self.skipped[iid][1])
        cap = int(self.cfg["master_max_px"])
        t = time.time()
        try:
            im = open_image(it["path"])
            with im:
                if im.format == "JPEG":
                    im.draft("RGB", (cap, cap))
                ImageOps.exif_transpose(im, in_place=True)  # phone photos: portrait stored landscape
                if im.mode in ("I", "I;16", "I;16B", "I;16L"):  # 16-bit greyscale PNG, which convert() clips white
                    im = im.point(lambda v: v / 256)
                im = im.convert("RGB")
        except FileNotFoundError:  # gone since the scan; the next scan drops it
            raise
        except (ValueError, OSError, SyntaxError) as e:  # too large, not a picture, or a broken file
            why = str(e) if isinstance(e, ValueError) else "cannot be decoded (%s)" % e.__class__.__name__
            self.skipped[iid] = (it["sha256"], why)
            log("master", iid, "skipped:", e, "-", it["path"])
            raise ValueError(why) from None
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
