"""A picture's colours: a five-colour palette, one usable accent, and the nearest GNOME accent name.

Pure Pillow + stdlib. `analyse(image)` is what the library stores in a sidecar; the rest are the
pieces, kept separate so the tests can pin each one down.

- The palette is Pillow's median-cut quantisation of a small thumbnail, close shades merged, most
  prominent first. Near-black, near-white and grey are left out unless the picture is mostly that
  (a monochrome or a night picture keeps its greys).
- The accent is the most vivid colour with real presence, then nudged to where GNOME's own accents
  sit: at least 3:1 contrast against both white and black, so it reads on a light and a dark UI.
  None for a picture with no colour in it at all.
- The GNOME name is the accent's nearest hue among the desktop's accent colours; grey is slate.
"""
import colorsys
import re

from PIL import Image

HEX_RE = re.compile(r"^#[0-9a-f]{6}$")  # the only form a colour leaves this module in
VERSION = 1       # bump when the method changes: every stored palette is then recomputed
THUMB = 96        # the longest edge analysed; the palette does not get better with more pixels
CLUSTERS = 32     # median-cut buckets before merging
MERGE_DIST = 32   # RGB distance under which two buckets are one colour
SIZE = 5          # colours in the palette
NEUTRAL_CHROMA = 0.09   # below this (max - min of the RGB channels, 0..1) a colour is grey
NEUTRAL_DARK = 0.09     # below this value (0..1) a colour is black
MOSTLY_NEUTRAL = 0.65   # share of pixels that makes a picture "mostly grey"
MIN_SHARE = 0.015       # a colour must cover this much of the picture to be the accent
MIN_PALETTE_SHARE = 0.01  # or to be in the palette (JPEG edge blends are below this)
ACCENT_SAT = 0.45       # the accent's least saturation (HSV)
LUM_LO, LUM_HI = 0.10, 0.30  # relative luminance giving >= 3:1 against both black and white

# GNOME's accent colours (libadwaita's light-mode values); the names are what
# `org.gnome.desktop.interface accent-color` takes.
GNOME_ACCENTS = [("blue", "#3584e4"), ("teal", "#2190a4"), ("green", "#3a944a"), ("yellow", "#c88800"),
                 ("orange", "#ed5b00"), ("red", "#e62d42"), ("pink", "#d56199"), ("purple", "#9141ac"),
                 ("slate", "#6f8396")]
GNOME_NAMES = [name for name, _hex in GNOME_ACCENTS]
# Where each name sits on the hue circle. libadwaita's own hues, except yellow: its yellow is an
# amber (41°), and a centre there would call a true orange (33°) yellow, so it sits at 50°.
GNOME_HUES = {"red": 357, "orange": 25, "yellow": 50, "green": 131, "teal": 189, "blue": 213, "purple": 285, "pink": 331}
SLATE_SAT = 0.30  # HSV saturation under which a colour is slate (GNOME's own slate is 0.26)


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def from_hex(text):
    t = text.lstrip("#")
    return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))


def chroma(rgb):
    return (max(rgb) - min(rgb)) / 255.0


def is_neutral(rgb):
    return chroma(rgb) < NEUTRAL_CHROMA or max(rgb) / 255.0 < NEUTRAL_DARK


def luminance(rgb):
    """WCAG relative luminance, 0..1."""
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def clusters(im):
    """[(share, (r, g, b))] most prominent first: the picture's colour buckets after merging."""
    im = im.convert("RGB")
    im.thumbnail((THUMB, THUMB), Image.BILINEAR)
    q = im.quantize(colors=CLUSTERS, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    total = im.size[0] * im.size[1]
    found = sorted(((n, tuple(pal[i * 3:i * 3 + 3])) for n, i in q.getcolors(CLUSTERS)), reverse=True)
    merged = []  # [count, (r, g, b)], big first; a bucket close to a bigger one joins it
    for n, rgb in found:
        for m in merged:
            if sum((a - b) ** 2 for a, b in zip(rgb, m[1])) ** 0.5 < MERGE_DIST:
                m[0] += n
                break
        else:
            merged.append([n, rgb])
    merged.sort(key=lambda m: -m[0])
    return [(n / total, rgb) for n, rgb in merged]


def palette(cl):
    """Up to SIZE colours from clusters(): the chromatic ones, most prominent first; greys only when the
    picture is mostly grey, or to fill up when it has fewer than SIZE colours of its own."""
    cl = [c for c in cl if c[0] >= MIN_PALETTE_SHARE] or cl[:1]
    colour = [c for c in cl if not is_neutral(c[1])]
    grey = [c for c in cl if is_neutral(c[1])]
    neutral_share = sum(s for s, _ in grey)
    if neutral_share >= MOSTLY_NEUTRAL:
        ranked = sorted(cl, key=lambda c: -c[0])
    else:
        ranked = colour + grey
    return [to_hex(rgb) for _s, rgb in ranked[:SIZE]]


def usable(rgb):
    """The same hue, as a UI accent: saturated enough, and a relative luminance in LUM_LO..LUM_HI."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    s = max(s, ACCENT_SAT)

    def rgb_of(s_, v_):
        return tuple(c * 255.0 for c in colorsys.hsv_to_rgb(h, s_, v_))

    # Aim a little inside the band, so rounding to whole channels cannot push it back out.
    top, bottom = LUM_HI - 0.005, LUM_LO + 0.005
    lum = luminance(rgb_of(s, v))
    if lum > LUM_HI:  # too light: darker, same saturation
        lo, hi = 0.0, v
        for _ in range(24):
            mid = (lo + hi) / 2
            if luminance(rgb_of(s, mid)) > top:
                hi = mid
            else:
                lo = mid
        v = lo
    elif lum < LUM_LO:  # too dark: brighter, then paler if full brightness is still too dark
        lo, hi = v, 1.0
        if luminance(rgb_of(s, 1.0)) >= bottom:
            for _ in range(24):
                mid = (lo + hi) / 2
                if luminance(rgb_of(s, mid)) < bottom:
                    lo = mid
                else:
                    hi = mid
            v = hi
        else:
            v = 1.0
            lo, hi = 0.0, s
            for _ in range(24):
                mid = (lo + hi) / 2
                if luminance(rgb_of(mid, v)) < bottom:
                    hi = mid
                else:
                    lo = mid
            s = lo
    return tuple(int(round(c)) for c in rgb_of(s, v))


def accent(cl):
    """The most vivid colour with real presence, made usable; None when the picture has no colour."""
    best, score = None, 0.0
    for share, rgb in cl:
        if share < MIN_SHARE or is_neutral(rgb):
            continue
        s = chroma(rgb) * share ** 0.5
        if s > score:
            best, score = rgb, s
    return to_hex(usable(best)) if best else None


def hue_distance(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def gnome_accent(hex_colour):
    """The nearest of GNOME's accent names by hue; slate for grey, or for no colour at all."""
    if not hex_colour:
        return "slate"
    rgb = from_hex(hex_colour)
    h, s, _v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    if s < SLATE_SAT or chroma(rgb) < NEUTRAL_CHROMA:
        return "slate"
    return min(GNOME_HUES, key=lambda name: hue_distance(h * 360, GNOME_HUES[name]))


def analyse(im):
    """{"palette": [hex...], "accent": hex or None, "gnome_accent": name} for a PIL image."""
    cl = clusters(im)
    acc = accent(cl)
    return {"palette": palette(cl), "accent": acc, "gnome_accent": gnome_accent(acc)}
