"""Pillow renderer: cover crops, the gallery wall label, spanned multi-monitor composites.

Geometry of the label (all in pixels of the target screen):
  card width  = card_frac_landscape * min(W, H*16/9) * s   (landscape)
              = card_frac_portrait  * W * s                (portrait)
  u           = card width / (card_frac_landscape * 1920)  -> 1.0 on a 1920x1080 screen at s=1
  margin      = margin_frac * min(W, H)
Type sizes, padding, radius and blur are multiples of u, so the card always keeps the
approved proportions whatever the screen size.
"""
import io
import os
import re
import threading
import warnings

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, features

from library import log

# 8K is 33 MP; 50 MP leaves room for a wide panorama and decodes to ~200 MB, which the host survives
# (the add-on has no memory limit). The 1x-2x band is only a warning in Pillow: make it an error, so
# one cap applies and library.open_image can skip the file instead of decoding it.
Image.MAX_IMAGE_PIXELS = 50_000_000
warnings.simplefilter("error", Image.DecompressionBombWarning)

HAVE_RAQM = features.check("raqm")
_LAYOUT = ImageFont.Layout.RAQM if HAVE_RAQM else ImageFont.Layout.BASIC
_font_missing = set()  # font files already reported unusable

DEFAULT_STYLE = {
    "card_frac_landscape": 0.24,
    "card_frac_portrait": 0.58,
    "margin_frac": 0.05,
    "title_px": 23.8,
    "place_px": 13.3,
    "place_tracking_em": 0.06,
    "body_px": 15.75,
    "line_height": 1.5,
    "padding_px": 22,
    "radius_px": 18,
    "blur_px": 24,
    "tint_alpha": 0.40,
    "border_alpha": 0.18,
    "title_alpha": 1.0,
    "place_alpha": 0.70,
    "body_alpha": 0.92,
    "shadow_alpha": 0.35,
    "jpeg_quality": 88,
    "kids_scale": 1.3,  # the label scale s for ?label=kids when the URL gives none
    "fonts": {
        "title": "NotoSerifDisplay-Italic.ttf",
        "place": "NotoSerif-Regular.ttf",
        "body": "NotoSans-Regular.ttf",
        "dropcap": "NotoSerifDisplay-Regular.ttf",
    },
    "font_dir": "/usr/share/fonts/truetype/noto",
}

_font_cache = {}
_font_lock = threading.Lock()


class Renderer:
    def __init__(self, style=None):
        st = dict(DEFAULT_STYLE)
        if style:
            fonts = dict(DEFAULT_STYLE["fonts"])
            fonts.update(style.get("fonts", {}))
            st.update(style)
            st["fonts"] = fonts
        self.st = st

    # ---------- fonts ----------
    def font(self, role, px):
        px = max(6, int(round(px)))
        key = (role, px)
        with _font_lock:
            f = _font_cache.get(key)
            if f is None:
                path = os.path.join(self.st["font_dir"], self.st["fonts"][role])
                try:
                    f = ImageFont.truetype(path, px, layout_engine=_LAYOUT)
                except OSError as e:
                    # Outside the image the fonts may be missing: a plainer label beats an HTTP 500.
                    if path not in _font_missing:
                        _font_missing.add(path)
                        log("font", path, "unusable (%s); using Pillow's built-in font" % e)
                    f = ImageFont.load_default(size=px).font_variant(layout_engine=_LAYOUT)
                _font_cache[key] = f
        return f

    # ---------- crops ----------
    @staticmethod
    def cover(master, w, h, focus=None):
        """Scale + crop the master to exactly w x h: centred, or around focus (see crop_box)."""
        box = crop_box(master.size[0], master.size[1], w, h, focus)
        return master.resize((w, h), Image.LANCZOS, box=box, reducing_gap=3.0)

    @staticmethod
    def padded(master, w, h, pad, focus=(0.5, 0.5)):
        """The w x h cover crop drawn pad times larger, centred on focus (x, y in 0-1) as far as the
        picture allows: (round(w * pad), round(h * pad)) pixels showing what a cover crop shows.
        A client shows it at screen size and zooms in by up to pad, so it can pan without an edge."""
        mw, mh = master.size
        pw, ph = int(round(w * pad)), int(round(h * pad))
        scale = max(pw / mw, ph / mh)
        cw, ch = min(mw, pw / scale), min(mh, ph / scale)
        left = min(max(focus[0] * mw - cw / 2, 0.0), mw - cw)
        top = min(max(focus[1] * mh - ch / 2, 0.0), mh - ch)
        box = (left, top, min(mw, left + cw), min(mh, top + ch))
        return master.resize((pw, ph), Image.LANCZOS, box=box, reducing_gap=3.0)

    # ---------- the label ----------
    def geometry(self, w, h, s):
        st = self.st
        short = min(w, h)
        if w >= h:
            card_w = st["card_frac_landscape"] * min(w, h * 16 / 9) * s
        else:
            card_w = st["card_frac_portrait"] * w * s
        margin = st["margin_frac"] * short
        card_w = min(card_w, w - 2 * margin)
        u = card_w / (st["card_frac_landscape"] * 1920)
        return card_w, margin, u

    def draw_label(self, img, meta, s=1.0, screen=None):
        """Composite the gallery label onto img (RGB, modified copy returned).

        screen: (w, h) of the screen when img is a padded canvas larger than it. The label is then
        sized for that screen and kept inside the part of the canvas that every screen-sized window
        shows (bottom-right corner at (w, h), top-left at the canvas size minus (w, h)), so no pan
        or zoom that stays inside the canvas at no more than 1:1 can take it out of frame."""
        st = self.st
        iw, ih = img.size
        w, h = screen or img.size
        lx, ly = iw - w, ih - h  # the window's leftmost and topmost start
        card_w, margin, u = self.geometry(w, h, s)
        card_w = int(round(min(card_w, w - lx - 2 * margin)))
        pad = st["padding_px"] * u
        inner = card_w - 2 * pad

        title = (meta.get("title") or "").strip()
        place_lines = [x for x in [(meta.get("place") or "").strip(), (meta.get("credit") or "").strip()] if x]
        blurb = (meta.get("blurb") or "").strip()

        f_title = self.font("title", st["title_px"] * u)
        f_place = self.font("place", st["place_px"] * u)
        f_body = self.font("body", st["body_px"] * u)

        ops = []  # (kind, (x, baseline), text, font, alpha, tracking) relative to the card
        last = pad  # last baseline drawn
        # Title: cap top sits on the padding line; wraps if it must.
        if title:
            t_lh = st["title_px"] * u * 1.2
            for i, line in enumerate(wrap_words(title, lambda t: f_title.getlength(t), inner)):
                last = pad + 0.8 * st["title_px"] * u + i * t_lh
                ops.append(("text", (pad, last), line, f_title, st["title_alpha"], None))
        # Place + credit: small caps, letter-spaced, one line each (wrapped if long).
        if place_lines:
            track = st["place_tracking_em"] * st["place_px"] * u
            step = 23 * u if title else (pad + (-f_place.getbbox("H", anchor="ls")[1]) - last)
            for raw in place_lines:
                for line in wrap_words(raw, lambda t: smallcaps_length(f_place, t, track), inner):
                    last += step
                    step = 18 * u
                    ops.append(("caps", (pad, last), line, f_place, st["place_alpha"], track))
        # Body with a two-line drop cap (top = line-1 cap height, baseline = line 2).
        if blurb:
            lh = st["body_px"] * u * st["line_height"]
            cap_body = -f_body.getbbox("H", anchor="ls")[1]
            b1 = last + 37.5 * u if (title or place_lines) else pad + cap_body
            first_char, rest = split_dropcap(blurb)
            if first_char:
                dc_h = lh + cap_body
                probe = self.font("dropcap", 200)
                dc_ratio = -probe.getbbox("H", anchor="ls")[1] / 200.0
                f_dc = self.font("dropcap", dc_h / dc_ratio)
                dc_box = f_dc.getbbox(first_char, anchor="ls")
                indent = (dc_box[2] - min(0, dc_box[0])) + 0.5 * st["body_px"] * u
                ops.append(("text", (pad - min(0, dc_box[0]), b1 + lh), first_char, f_dc, st["body_alpha"], None))
                lines = wrap_words(rest, lambda t: f_body.getlength(t), inner, first_widths=[inner - indent] * 2)
                for i, line in enumerate(lines):
                    ops.append(("text", (pad + (indent if i < 2 else 0), b1 + i * lh), line, f_body,
                                st["body_alpha"], None))
                nlines = max(2, len(lines))
            else:
                lines = wrap_words(blurb, lambda t: f_body.getlength(t), inner)
                for i, line in enumerate(lines):
                    ops.append(("text", (pad, b1 + i * lh), line, f_body, st["body_alpha"], None))
                nlines = len(lines)
            last = b1 + (nlines - 1) * lh
        y = last + 0.25 * st["body_px"] * u  # room for descenders
        card_h = int(round(y + pad * 0.75))

        x0 = int(round(w - margin - card_w))
        y0 = int(round(h - margin - card_h))
        if y0 < ly + margin:  # absurdly small screen: pin to the top margin
            y0 = int(ly + margin)
        radius = st["radius_px"] * u
        out = img.copy()

        # Soft shadow under the card.
        sh_blur = 14 * u
        sh_off = 6 * u
        ext = int(sh_blur * 3)
        sx0, sy0 = max(0, x0 - ext), max(0, y0 - ext)
        sx1, sy1 = min(iw, x0 + card_w + ext), min(ih, int(y0 + card_h + sh_off + ext))
        sh = Image.new("L", (sx1 - sx0, sy1 - sy0), 0)
        ImageDraw.Draw(sh).rounded_rectangle(
            (x0 - sx0, y0 - sy0 + sh_off, x0 - sx0 + card_w, y0 - sy0 + card_h + sh_off),
            radius=radius, fill=int(255 * st["shadow_alpha"]))
        sh = sh.filter(ImageFilter.GaussianBlur(sh_blur))
        region = out.crop((sx0, sy0, sx1, sy1))
        region = Image.composite(Image.new("RGB", region.size, (0, 0, 0)), region, sh)
        out.paste(region, (sx0, sy0))

        # Frosted glass: blur what is behind the card (sampled from the clean image), then tint.
        blur = st["blur_px"] * u
        be = int(blur * 2.5)
        bx0, by0 = max(0, x0 - be), max(0, y0 - be)
        bx1, by1 = min(iw, x0 + card_w + be), min(ih, y0 + card_h + be)
        behind = img.crop((bx0, by0, bx1, by1)).filter(ImageFilter.GaussianBlur(blur))
        glass = behind.crop((x0 - bx0, y0 - by0, x0 - bx0 + card_w, y0 - by0 + card_h))
        glass = Image.blend(glass, Image.new("RGB", glass.size, (0, 0, 0)), st["tint_alpha"])
        glass = glass.convert("RGBA")

        # Text layer.
        layer = Image.new("RGBA", glass.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for kind, (tx, ty), text, font, alpha, track in ops:
            fill = (255, 255, 255, int(255 * min(1.0, alpha)))
            if kind == "caps":
                draw_smallcaps(d, (tx, ty), text, font, fill, track)
            else:
                d.text((tx, ty), text, font=font, fill=fill, anchor="ls")
        glass = Image.alpha_composite(glass, layer)

        # Border (1 px, white 18 %), drawn at 4x for smooth corners.
        mask, border = rounded_masks(card_w, card_h, radius, max(1.0, u * 0.75))
        bcol = Image.new("RGBA", glass.size, (255, 255, 255, 0))
        bcol.putalpha(border.point(lambda v: int(v * st["border_alpha"])))
        glass = Image.alpha_composite(glass, bcol)
        out.paste(glass.convert("RGB"), (x0, y0), mask)
        return out

    # ---------- public entry points ----------
    def clean(self, master, w, h, focus=None):
        return self.cover(master, w, h, focus)

    def label(self, master, meta, w, h, s=1.0, focus=None):
        return self.draw_label(self.cover(master, w, h, focus), meta, s)

    def label_padded(self, master, meta, w, h, s, pad, focus):
        return self.draw_label(self.padded(master, w, h, pad, focus), meta, s, screen=(w, h))

    def span(self, master, meta, monitors, s=1.0, focus=None):
        cw = max(m[0] + m[2] for m in monitors)
        ch = max(m[1] + m[3] for m in monitors)
        canvas = Image.new("RGB", (cw, ch), (0, 0, 0))
        primary = next((i for i, m in enumerate(monitors) if m[4]), None)
        if primary is None:  # no 'p': the biggest screen gets the label
            primary = max(range(len(monitors)), key=lambda i: monitors[i][2] * monitors[i][3])
        for i, (x, y, mw, mh, _p) in enumerate(monitors):
            tile = self.label(master, meta, mw, mh, s, focus) if i == primary else self.cover(master, mw, mh, focus)
            canvas.paste(tile, (x, y))
        return canvas

    @staticmethod
    def dim(img, amount):
        """img darkened by amount: 0 leaves it as it is, 1 is black. Quiet hours, label and all."""
        return ImageEnhance.Brightness(img).enhance(1 - amount) if amount else img

    def encode(self, img, quality=None):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality or self.st["jpeg_quality"], progressive=True,
                 optimize=True, subsampling="4:2:0")
        return buf.getvalue()


# ---------- helpers ----------
def crop_box(mw, mh, w, h, focus=None):
    """The part of an mw x mh picture that cover() scales to w x h, as (left, top, right, bottom).

    Without a focus the box is centred. With focus = (x, y), fractions of the picture's width and
    height, the box is centred on that point and then slid back inside the picture, so the point is
    always in the frame and as near the middle of it as the picture's edges allow."""
    scale = max(w / mw, h / mh)
    # w / (w / mw) can come out a hair above mw, which makes the box start at -1e-13 and Pillow
    # refuses it ("box offset can't be negative"): about 4 % of picture and size pairs.
    cw, ch = min(mw, w / scale), min(mh, h / scale)
    if focus is None:
        left, top = (mw - cw) / 2, (mh - ch) / 2
    else:
        left = min(max(0.0, focus[0] * mw - cw / 2), mw - cw)
        top = min(max(0.0, focus[1] * mh - ch / 2), mh - ch)
    # left + cw can round a hair past the edge once clamped to mw - cw: Pillow refuses that too.
    return (left, top, min(mw, left + cw), min(mh, top + ch))


def edge_focus(img, px=96):
    """A cheap guess at where the subject is: the centre of mass of edge strength (squared, so
    strong detail outweighs texture) on a small grey copy. A flat picture gives the centre.
    Returns (x, y) as fractions of the width and height."""
    small = img.convert("L")
    small.thumbnail((px, px), Image.BILINEAR)
    edges = small.filter(ImageFilter.FIND_EDGES)
    w, h = edges.size
    data = edges.load()
    total = sx = sy = 0.0
    for y in range(1, h - 1):  # FIND_EDGES rings the border: leave it out
        for x in range(1, w - 1):
            v = data[x, y] ** 2
            total += v
            sx += v * x
            sy += v * y
    if not total or w < 3 or h < 3:
        return 0.5, 0.5
    return (sx / total + 0.5) / w, (sy / total + 0.5) / h


def rounded_masks(w, h, radius, border_w):
    k = 4
    big = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=radius * k, fill=255)
    ring = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(ring).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=radius * k, outline=255,
                                           width=max(1, int(round(border_w * k))))
    return big.resize((w, h), Image.LANCZOS), ring.resize((w, h), Image.LANCZOS)


def split_dropcap(text):
    m = re.match(r"^([\"'‘“]?)([A-Za-zÀ-ɏ])(.*)$", text, re.S)
    if not m or m.group(1):
        return "", text
    return m.group(2).upper(), m.group(3)


def wrap_words(text, measure, width, first_widths=None):
    words = text.split()
    lines, cur = [], ""
    widths = list(first_widths or [])

    def limit():
        return widths[len(lines)] if len(lines) < len(widths) else width

    for word in words:
        cand = word if not cur else cur + " " + word
        if measure(cand) <= limit() or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _smallcaps_runs(font, text):
    """Split text into (chunk, font_features) runs; true small caps when raqm is present."""
    return [(text, ["smcp", "c2sc"])] if HAVE_RAQM else None


def smallcaps_length(font, text, track):
    if HAVE_RAQM:
        n = len(text)
        return sum(font.getlength(ch, features=["smcp", "c2sc"]) for ch in text) + track * max(0, n - 1)
    # faux small caps: uppercase at 80 %
    small = font.font_variant(size=max(6, int(font.size * 0.8)))
    total = 0.0
    for ch in text:
        f = font if ch.isupper() or not ch.isalpha() else small
        total += f.getlength(ch.upper()) + track
    return total


def draw_smallcaps(d, xy, text, font, fill, track):
    x, y = xy
    if HAVE_RAQM:
        for ch in text:
            d.text((x, y), ch, font=font, fill=fill, anchor="ls", features=["smcp", "c2sc"])
            x += font.getlength(ch, features=["smcp", "c2sc"]) + track
        return
    small = font.font_variant(size=max(6, int(font.size * 0.8)))
    for ch in text:
        f = font if ch.isupper() or not ch.isalpha() else small
        d.text((x, y), ch.upper(), font=f, fill=fill, anchor="ls")
        x += f.getlength(ch.upper()) + track


def focus_point(sc):
    """The sidecar's focus {x, y} (0-1, from the picture's top-left) as a tuple, or the centre."""
    f = (sc or {}).get("focus")
    if isinstance(f, dict):
        x, y = f.get("x"), f.get("y")
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in (x, y)):
            return float(x), float(y)
    return 0.5, 0.5


def parse_layout(spec, lo=320, hi=7680, max_w=12000, max_h=8000, max_monitors=8, max_pixels=None):
    mons = []
    spec = spec.replace(" ", "+")  # an unencoded "+" arrives as a space
    for part in spec.split(","):
        m = re.fullmatch(r"\s*(\d+)x(\d+)\+(\d+)\+(\d+)(p?)\s*", part)
        if not m:
            raise ValueError("bad monitor spec: %r" % part)
        mw, mh, x, y = (int(m.group(i)) for i in range(1, 5))
        if not (lo <= mw <= hi and lo <= mh <= hi):
            raise ValueError("monitor size out of range")
        mons.append((x, y, mw, mh, bool(m.group(5))))
    if not mons or len(mons) > max_monitors:
        raise ValueError("1-%d monitors" % max_monitors)
    if sum(1 for m in mons if m[4]) > 1:
        raise ValueError("only one primary")
    cw = max(m[0] + m[2] for m in mons)
    ch = max(m[1] + m[3] for m in mons)
    if cw > max_w or ch > max_h:
        raise ValueError("canvas too large")
    if max_pixels and cw * ch > max_pixels:  # the whole canvas is allocated, gaps included
        raise ValueError("canvas over %g megapixels" % (max_pixels / 1e6))
    return mons
