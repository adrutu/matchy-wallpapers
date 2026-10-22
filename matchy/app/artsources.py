"""Open-access art sources: the Art Institute of Chicago, The Met, Wikimedia Commons' Picture of the Day.

Each adapter turns one source's API into candidate works, lazily, one request at a time:
{id, title, place, credit, context, facts{artist, date, medium, place}, source_url, artwork, image_url,
size (w, h) or None when the API does not say}, or {id, skip: reason} for a work that will never do
(not public domain, no image), so it is not asked about again. Library.sync_art decides what to download.

The terms each one follows, as read in October 2026 (DOCS.md, "Open-access art"):
- every source: serial requests at least `pause_seconds` apart, a User-Agent naming the project and a
  contact, nothing retried in a loop (a refusal waits for the next check);
- Art Institute of Chicago: public-domain works only, the AIC-User-Agent header, images at the IIIF
  widths its docs allow for public-domain works (1686 px at most), credited "The Art Institute of Chicago";
- The Met: the v1.1 search (v1 answers 410 since 2026-10-01), isPublicDomain checked on every object
  because search ignores it, credited "The Metropolitan Museum of Art" with no logo and no endorsement;
- Wikimedia Commons: the Action API with maxlag, only the thumbnail URL the API returns (standard
  widths), credited author · licence · Wikimedia Commons.
"""
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import netguard

PROJECT_URL = "https://github.com/adrutu/matchy-wallpapers"
SOURCES = ("aic", "met", "potd")  # the order they are synced in

AIC_FIELDS = ["id", "title", "artist_title", "date_display", "medium_display", "place_of_origin",
              "image_id", "thumbnail", "is_public_domain"]
AIC_WIDTHS = (1686, 1200, 843)  # the IIIF widths the AIC docs list for public-domain works, largest first
AIC_CREDIT = "The Art Institute of Chicago · CC0"
MET_CREDIT = "The Metropolitan Museum of Art · metmuseum.org · CC0"
MET_SEARCH_LIMIT = 500    # the most v1.1 hands out per page
MET_SEARCH_WINDOW = 10000  # nothing past offset + limit = 10000 is served
POTD_WIDTH = 3840         # the largest of Wikimedia's standard thumbnail widths


def clean(text, limit=0):
    """Plain text from an API value that may hold HTML (Commons' extmetadata does): tags dropped,
    entities decoded, whitespace collapsed, cut at a word boundary to `limit` characters."""
    s = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", str(text or ""))).split())
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return s


def label_line(facts):
    """The label's second line for a museum work: artist · date."""
    return " · ".join(x for x in (facts.get("artist"), facts.get("date")) if x)


class Client:
    """Serial HTTP for one sync: a User-Agent with a contact, and a pause before every request after
    the first, so no source sees more than one request a `pause` seconds."""

    def __init__(self, contact="", pause=1.0, timeout=60, trusted=()):
        who = PROJECT_URL + ("; " + contact if contact else "")
        self.ua = "MatchyWallpapers (bot; %s) Python-urllib/%d.%d" % (who, *sys.version_info[:2])
        self.aic_ua = "matchy-wallpapers (%s)" % who
        self.pause, self.timeout, self.last = float(pause), timeout, 0.0
        self.requests = 0
        # The configured API hosts; an image or object URL an API hands back must be public otherwise.
        self.trusted = {str(h).lower() for h in trusted}

    def get(self, url, data=None, headers=None, max_bytes=60_000_000):
        if not re.match(r"^https?://", url or ""):
            raise ValueError("not an http(s) URL: %r" % url)
        netguard.check_url(url, self.trusted)
        wait = self.last + self.pause - time.time()
        if self.requests and wait > 0:
            time.sleep(wait)
        h = {"User-Agent": self.ua, "Accept-Encoding": "identity"}
        h.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
        try:
            with netguard.urlopen(req, self.trusted, self.timeout) as r:
                body = r.read(max_bytes + 1)
        finally:
            self.last = time.time()
            self.requests += 1
        if len(body) > max_bytes:
            raise ValueError("response over %d MB" % (max_bytes // 1_000_000))
        return body

    def json(self, url, body=None, headers=None):
        h = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        return json.loads(self.get(url, data, h, max_bytes=20_000_000))


# ---------------------------------------------------------------- Art Institute of Chicago
def aic(cfg, client, rng, seen):
    """A random page of public-domain works with an image (the docs' own random-sample query)."""
    filters = [{"term": {"is_public_domain": True}}, {"exists": {"field": "image_id"}},
               {"exists": {"field": "thumbnail.width"}}, {"exists": {"field": "thumbnail.height"}}]
    filters += cfg.get("extra_filters") or []
    body = {"resources": "artworks", "fields": AIC_FIELDS, "boost": False, "limit": int(cfg.get("batch", 20)),
            "query": {"function_score": {"query": {"bool": {"filter": filters}}, "boost_mode": "replace",
                                         "random_score": {"field": "id", "seed": rng.randrange(1, 2 ** 31)}}}}
    hdr = {"AIC-User-Agent": client.aic_ua}
    j = client.json(cfg["base_url"].rstrip("/") + "/search", body, hdr)
    iiif = str((j.get("config") or {}).get("iiif_url") or "").rstrip("/")
    for w in j.get("data") or []:
        iid = "aic-%s" % w.get("id")
        thumb = w.get("thumbnail") or {}
        tw, th = thumb.get("width") or 0, thumb.get("height") or 0
        if iid in seen:
            continue
        if w.get("is_public_domain") is not True:
            yield {"id": iid, "skip": "not public domain"}
            continue
        width = next((x for x in AIC_WIDTHS if x <= tw), 0)
        if not w.get("image_id") or not th or not width:
            yield {"id": iid, "skip": "no usable image"}
            continue
        facts = {"artist": clean(w.get("artist_title")), "date": clean(w.get("date_display")),
                 "medium": clean(w.get("medium_display")), "place": clean(w.get("place_of_origin"))}
        yield {"id": iid, "title": clean(w.get("title")), "place": label_line(facts), "credit": AIC_CREDIT,
               "facts": facts, "artwork": True, "source_url": "https://www.artic.edu/artworks/%s" % w.get("id"),
               "image_url": "%s/%s/full/%d,/0/default.jpg" % (iiif, urllib.parse.quote(w["image_id"]), width),
               "image_headers": hdr, "size": (width, round(th * width / tw))}


# ---------------------------------------------------------------- The Met
def met(cfg, client, rng, seen):
    """A random page of the v1.1 search (highlights with images by default), then objects one by one
    until the caller has enough; at most `lookups` object requests.

    `media` keeps it to pictures: the highlights are mostly vases, armour and furniture. The search
    takes one medium at a time ("Paintings|Drawings" matches almost nothing), so each sync picks one,
    and every object is checked again, since the medium search lets the odd necklace through."""
    base = cfg["base_url"].rstrip("/")
    params = {"q": "*", "hasImages": "true"}
    media = [str(m) for m in cfg.get("media") or []]
    if media:
        params["medium"] = rng.choice(media)
    params.update({k: str(v) for k, v in (cfg.get("search") or {}).items()})
    params = {k: v for k, v in params.items() if v not in ("", "None")}  # "" drops a default filter
    pages = (MET_SEARCH_WINDOW - MET_SEARCH_LIMIT) // MET_SEARCH_LIMIT
    ids = []
    for offset in (rng.randint(0, pages) * MET_SEARCH_LIMIT, 0):
        q = dict(params, offset=offset, limit=MET_SEARCH_LIMIT)
        ids = client.json("%s/v1.1/search?%s" % (base, urllib.parse.urlencode(q))).get("objectIDs") or []
        if ids or not offset:
            break  # an offset past the end answers null: start again from the top once
    ids = [i for i in ids if "met-%s" % i not in seen]
    rng.shuffle(ids)
    for oid in ids[:int(cfg.get("lookups", 15))]:
        try:
            o = client.json("%s/v1/objects/%d" % (base, int(oid)))
        except urllib.error.HTTPError as e:
            if e.code == 404:  # listed by search but gone: it happens
                yield {"id": "met-%s" % oid, "skip": "not found"}
                continue
            raise
        if o.get("isPublicDomain") is not True:
            yield {"id": "met-%s" % oid, "skip": "not public domain"}
            continue
        if not o.get("primaryImage"):
            yield {"id": "met-%s" % oid, "skip": "no image"}
            continue
        if media and not met_is_picture(o):
            yield {"id": "met-%s" % oid, "skip": "not a painting or drawing"}
            continue
        place = ", ".join(x for x in (clean(o.get("city")), clean(o.get("country"))) if x) or clean(o.get("culture"))
        facts = {"artist": clean(o.get("artistDisplayName")), "date": clean(o.get("objectDate")),
                 "medium": clean(o.get("medium")), "place": place}
        yield {"id": "met-%s" % o.get("objectID", oid), "title": clean(o.get("title")), "place": label_line(facts),
               "credit": MET_CREDIT, "facts": facts, "artwork": True, "source_url": clean(o.get("objectURL")),
               "image_url": o["primaryImage"], "size": None}


def met_is_picture(o):
    """A painting, drawing, watercolour or pastel by its classification or object name (The American
    Wing leaves the classification empty and says "Painting" in the name)."""
    kind = ("%s %s" % (o.get("classification") or "", o.get("objectName") or "")).lower()
    return any(w in kind for w in ("paint", "draw", "watercolo", "pastel"))


# ---------------------------------------------------------------- Wikimedia Commons Picture of the Day
def potd(cfg, client, rng, seen, today=None):
    """Today's picture, then earlier days not taken yet (at most `days_back` of them), one API call each."""
    today = today or dt.datetime.now(dt.timezone.utc).date()  # Commons changes it at 00:00 UTC
    for back in range(int(cfg.get("days_back", 7))):
        day = today - dt.timedelta(days=back)
        iid = "potd-%s" % day.strftime("%Y%m%d")
        if iid in seen:
            continue
        q = {"action": "query", "format": "json", "formatversion": "2", "maxlag": "5",
             "generator": "images", "titles": "Template:Potd/%s" % day.isoformat(),
             "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": str(POTD_WIDTH),
             "iiextmetadatalanguage": cfg.get("language", "en")}
        j = client.json("%s?%s" % (cfg["api_url"], urllib.parse.urlencode(q)))
        if j.get("error"):  # maxlag, or anything else the API refuses: try at the next check
            raise RuntimeError("Commons API: %s" % clean((j["error"] or {}).get("info") or j["error"]))
        pages = [p for p in (j.get("query") or {}).get("pages") or []
                 if p.get("imageinfo") and str(p["imageinfo"][0].get("mime", "")).startswith("image/")]
        if not pages:
            continue  # no picture set for that day (yet)
        p = pages[0]
        ii = p["imageinfo"][0]
        em = {k: (v or {}).get("value", "") for k, v in (ii.get("extmetadata") or {}).items()}
        author = clean(em.get("Attribution") or em.get("Artist"), 120)
        licence = clean(em.get("LicenseShortName"), 60)
        name = re.sub(r"^File:|\.[A-Za-z0-9]+$", "", p.get("title", ""))
        title = clean(em.get("ObjectName"), 100) or clean(name.replace("_", " "), 100)
        if ii.get("thumburl"):
            url, size = ii["thumburl"], (ii.get("thumbwidth") or 0, ii.get("thumbheight") or 0)
        else:
            url, size = ii.get("url"), (ii.get("width") or 0, ii.get("height") or 0)
        date = clean(em.get("DateTimeOriginal"), 40)
        facts = {"artist": author, "date": date, "medium": "", "place": ""}
        yield {"id": iid, "title": title, "place": "", "credit": " · ".join(
                   x for x in (author, licence, "Wikimedia Commons") if x),
               "context": clean(em.get("ImageDescription"), 400), "facts": facts, "artwork": False,
               "source_url": ii.get("descriptionurl", ""), "licence": licence, "image_url": url,
               "size": size if all(size) else None}


ADAPTERS = {"aic": aic, "met": met, "potd": potd}
