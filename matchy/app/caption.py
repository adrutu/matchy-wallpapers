"""Caption images once with an OpenAI-compatible vision model (llama.cpp, Ollama, vLLM...).

Endpoints are tried in order; one that refuses/times out is skipped for `down_minutes`.
The engine never wakes a sleeping endpoint itself.
"""
import base64
import io
import json
import re
import time
import urllib.error
import urllib.request

from PIL import Image

from library import log

UK_SPELLING = {
    "color": "colour", "colors": "colours", "colored": "coloured", "colorful": "colourful", "coloring": "colouring",
    "gray": "grey", "grays": "greys", "grayish": "greyish", "center": "centre", "centered": "centred", "centers": "centres",
    "harbor": "harbour", "harbors": "harbours", "favorite": "favourite", "neighbor": "neighbour",
    "neighboring": "neighbouring", "neighborhood": "neighbourhood", "honor": "honour", "luster": "lustre",
    "theater": "theatre", "meter": "metre", "meters": "metres", "glamor": "glamour", "vapor": "vapour",
    "savor": "savour", "flavor": "flavour", "flavors": "flavours", "jewelry": "jewellery", "mold": "mould",
    "plow": "plough", "somber": "sombre", "saber": "sabre", "fiber": "fibre", "fibers": "fibres",
    "silhouetted": "silhouetted", "realize": "realise", "emphasize": "emphasise", "emphasizes": "emphasises",
    "emphasizing": "emphasising", "mesmerizing": "mesmerising", "harmonize": "harmonise",
    "harmonizes": "harmonises", "symbolizes": "symbolises", "crystallized": "crystallised", "traveler": "traveller",
    "travelers": "travellers", "kilometers": "kilometres", "glowing": "glowing", "textured": "textured",
}

SENT_START = re.compile(r"(^|[.!?]\s+|[—:;]\s*|\"\s*)$")


def to_uk(text):
    def fix(m):
        w = m.group(0)
        r = UK_SPELLING.get(w.lower())
        if not r:
            return w
        return r[:1].upper() + r[1:] if w[:1].isupper() else r
    return re.sub(r"[A-Za-z]+", fix, text)


def proper_nouns(blurb, allowed_text):
    """Capitalised words that are not sentence-initial and do not appear in the known metadata."""
    allowed = {w.lower() for w in re.findall(r"[A-Za-zÀ-ɏ]+", allowed_text)}
    bad = []
    for m in re.finditer(r"[A-Za-zÀ-ɏ][\wÀ-ɏ'-]*", blurb):
        w = m.group(0)
        if not w[:1].isupper() or w in ("I",):
            continue
        before = blurb[:m.start()]
        if SENT_START.search(before):
            continue
        parts = re.findall(r"[A-Za-zÀ-ɏ]+", w)
        if all(p.lower() in allowed for p in parts):
            continue
        bad.append(w)
    return bad


def image_data_url(path, px):
    with Image.open(path) as im:
        im.draft("RGB", (px, px))
        im = im.convert("RGB")
        im.thumbnail((px, px), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class Captioner:
    def __init__(self, cfg):
        self.cfg = cfg["caption"]
        self.endpoints = cfg["captioners"]
        self.down_until = {}
        for ep in self.endpoints:
            ep.setdefault("name", ep.get("url"))
            key = ep.get("api_key", "")
            if not key and ep.get("api_key_file"):
                try:
                    with open(ep["api_key_file"]) as f:
                        key = f.read().strip()
                except OSError as e:
                    log("captioner", ep["name"], "key file unreadable:", e)
            ep["_key"] = key

    def available(self):
        now = time.time()
        return [ep for ep in self.endpoints if self.down_until.get(ep["name"], 0) <= now]

    # ---------- prompt ----------
    def prompt(self, meta, feedback=None):
        c = self.cfg
        facts = []
        if meta.get("title"):
            facts.append('Title: "%s"' % meta["title"])
        if meta.get("place"):
            facts.append("Place: %s" % meta["place"])
        if meta.get("context"):
            facts.append("Source description: %s" % meta["context"])
        if meta.get("collection"):
            facts.append("Collection: %s" % meta["collection"])
        if meta.get("abstract"):
            facts.append("This is digital/illustrated desktop art, not a photograph of a real place.")
        facts_txt = "\n".join("- " + f for f in facts) or "- (no metadata: describe only what is visible)"
        if meta.get("title"):
            title_rule = 'Set "title" to exactly: %s' % meta["title"]
        else:
            title_rule = ('Write a short "title" of 2 to 5 words that names what is visible '
                          '(no place names, no people, and no words from the collection name).')
        lang = c.get("language", "English")
        spelling = c.get("spelling", "")
        sp = (" Use %s spelling (for example colour, grey, centre)." % spelling) if spelling.lower().startswith("brit") else (
            (" Use %s spelling." % spelling) if spelling else "")
        text = (
            "You write the wall label for a picture in a small art gallery.\n"
            "Known facts (the ONLY facts you may state besides what is plainly visible):\n%s\n\n"
            "Rules:\n"
            "- %s\n"
            '- "blurb": at least %d and at most %d words (about %d is ideal) in %s, three sentences, evocative but '
            "factual: light, colour, texture, mood and what is in the frame.%s\n"
            "- Do NOT name any place, landmark, person, species, date or event unless it appears in the known facts. "
            "Never guess a location.\n"
            "- Do not repeat the title or the credit, do not mention the photographer, and do not say 'image', "
            "'photo', 'picture' or 'wallpaper'.\n"
            'Reply with JSON only: {"title": "...", "blurb": "..."}'
        ) % (facts_txt, title_rule, min(c["max_words"] - 4, c["min_words"] + 6), c["max_words"],
             (c["min_words"] + 2 * c["max_words"]) // 3, lang, sp)
        if feedback:
            text += "\n\nYour previous answer was rejected: %s. Fix that and answer again." % feedback
        return text

    # ---------- transport ----------
    def call(self, ep, prompt, data_url):
        c = self.cfg
        body = {
            "model": ep["model"],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}],
            "max_tokens": c["max_tokens"],
            "temperature": c["temperature"],
            "stream": False,
        }
        if ep.get("json_mode", True):
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if ep.get("_key"):
            headers["Authorization"] = "Bearer " + ep["_key"]
        req = urllib.request.Request(ep["url"], data=json.dumps(body).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=ep.get("timeout", 120)) as r:
            resp = json.loads(r.read())
        return resp["choices"][0]["message"]["content"]

    @staticmethod
    def parse(text):
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("no JSON object")
        obj = json.loads(m.group(0))
        return str(obj.get("title", "")).strip().strip('"'), " ".join(str(obj.get("blurb", "")).split())

    def check(self, meta, title, blurb):
        c = self.cfg
        problems = []
        words = len(blurb.split())
        if words < c["min_words"] or words > c["max_words"]:
            problems.append("blurb has %d words, it must have %d to %d" % (words, c["min_words"], c["max_words"]))
        if not meta.get("title"):
            tl = title.lower()
            bad_titles = [t.lower() for t in c.get("forbidden_titles", [])]
            if not title or len(title.split()) > 7 or tl in bad_titles or any(
                    w in tl for w in ("wallpaper", "image", "untitled")):
                problems.append("the title must be 2 to 5 words naming what is visible")
        allowed = " ".join(str(meta.get(k, "")) for k in ("title", "place", "context", "collection", "credit"))
        allowed += " " + title if not meta.get("title") else ""
        nouns = proper_nouns(blurb, allowed)
        if nouns:
            problems.append("it names things not in the known facts (%s)" % ", ".join(sorted(set(nouns))))
        if re.match(r"^(this|the)\s+(image|photo|picture)", blurb, re.I):
            problems.append("do not start with 'this image'")
        return problems, words

    def caption(self, meta, master_path):
        """Return (sidecar_fields, endpoint) or raise RuntimeError when every endpoint failed/declined."""
        c = self.cfg
        data_url = image_data_url(master_path, c.get("image_px", 1024))
        eps = self.available()
        if not eps:
            raise RuntimeError("no caption endpoint available")
        last_err = None
        for ep in eps:
            try:
                return self._caption_with(ep, meta, data_url), ep
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                self.down_until[ep["name"]] = time.time() + 60 * ep.get("down_minutes", 10)
                log("captioner", ep["name"], "unreachable (%s); skipping it for %d min" % (e, ep.get("down_minutes", 10)))
                last_err = e
            except ValueError as e:  # model answered but never usably
                last_err = e
                log("captioner", ep["name"], "gave no usable caption:", e)
                break
        raise RuntimeError(str(last_err))

    def _caption_with(self, ep, meta, data_url):
        c = self.cfg
        best = None
        feedback = None
        for attempt in (1, 2):
            t = time.time()
            raw = self.call(ep, self.prompt(meta, feedback), data_url)
            dt = time.time() - t
            try:
                title, blurb = self.parse(raw)
            except ValueError as e:
                feedback = "it was not valid JSON (%s)" % e
                log("caption attempt", attempt, "bad JSON from", ep["name"])
                continue
            if meta.get("title"):
                title = meta["title"]
            if c.get("spelling", "").lower().startswith("brit"):
                blurb, title = to_uk(blurb), (title if meta.get("title") else to_uk(title))
            problems, words = self.check(meta, title, blurb)
            log("caption attempt %d via %s %.1fs: %d words%s" % (
                attempt, ep["name"], dt, words, (" - " + "; ".join(problems)) if problems else ""))
            target = (c["min_words"] + 2 * c["max_words"]) // 3
            cand = {"title": title, "blurb": blurb, "problems": problems, "words": words, "seconds": round(dt, 1)}
            if not problems:
                return cand
            if best is None or len(problems) < len(best["problems"]):
                best = cand
            feedback = "; ".join(problems)
            if words < c["min_words"] and len(problems) == 1:
                feedback = ('the blurb was too short (%d words): "%s" - keep it and add one more sentence about the '
                            'light, colour or texture so it has about %d words' % (words, blurb, target))
        # Second attempt still imperfect: accept only a near-miss on length, never invented names.
        if best and all(p.startswith("blurb has") for p in best["problems"]) and \
                c["min_words"] - 3 <= best["words"] <= c["max_words"] + 3:
            best["accepted_with"] = best.pop("problems")
            return best
        raise ValueError("; ".join(best["problems"]) if best else "no JSON")
