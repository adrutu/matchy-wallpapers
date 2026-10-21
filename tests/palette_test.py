"""Tests for palette.py (colour match): the palette is stable for the same picture, sensible for
monochrome, sepia and night pictures, the accent is usable on a light and a dark UI, and the GNOME
accent name mapping table.

    python3 tests/palette_test.py
"""
import os, random, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "matchy", "app"))

from PIL import Image, ImageDraw
import palette as P

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print("PASS", name)
    else:
        fail += 1; print("FAIL", name, detail)


def hexes_ok(pal):
    return isinstance(pal, list) and all(isinstance(c, str) and P.HEX_RE.match(c) for c in pal)


def is_grey(hex_colour):
    return P.chroma(P.from_hex(hex_colour)) < P.NEUTRAL_CHROMA


# ---------------- pictures ----------------
def landscape():
    """Sky over a field, a red barn, a small sun."""
    im = Image.new("RGB", (800, 500))
    d = ImageDraw.Draw(im)
    for y in range(250):
        d.line([(0, y), (800, y)], fill=(120 + y // 5, 170 + y // 8, 230))
    for y in range(250, 500):
        d.line([(0, y), (800, y)], fill=(60 + y % 7, 140 - (y - 250) // 10, 50))
    d.rectangle([520, 200, 640, 290], fill=(190, 40, 35))
    d.rectangle([700, 60, 760, 110], fill=(250, 240, 200))
    return im


def mono():
    """A grey ramp with noise: no colour at all."""
    im = Image.new("L", (800, 500))
    d = ImageDraw.Draw(im)
    for y in range(500):
        d.line([(0, y), (800, y)], fill=int(255 * y / 500))
    rnd = random.Random(1)
    for _ in range(300):
        d.point((rnd.randrange(800), rnd.randrange(500)), fill=rnd.randrange(256))
    return im.convert("RGB")


def sepia():
    im = mono()
    px = im.load()
    for x in range(800):
        for y in range(500):
            g = px[x, y][0]
            px[x, y] = (min(255, int(g * 1.07 + 20)), int(g * 0.9 + 5), int(g * 0.65))
    return im


def night():
    """Nearly black, with a moon and a lit window under 1 % of the picture."""
    im = Image.new("RGB", (800, 500), (8, 10, 20))
    d = ImageDraw.Draw(im)
    d.ellipse([600, 60, 660, 120], fill=(255, 230, 150))
    d.rectangle([300, 380, 340, 420], fill=(255, 180, 60))
    return im


def flat(rgb):
    return Image.new("RGB", (640, 360), rgb)


# ---------------- stable ----------------
for name, fn in (("landscape", landscape), ("mono", mono), ("sepia", sepia), ("night", night)):
    a, b = P.analyse(fn()), P.analyse(fn())
    check("stable: %s analysed twice is the same" % name, a == b, (a, b))
    check("stable: %s palette is 1-5 hex colours" % name, hexes_ok(a["palette"]) and 1 <= len(a["palette"]) <= 5, a)
    check("stable: %s gnome_accent is a GNOME name" % name, a["gnome_accent"] in P.GNOME_NAMES, a)
# A thumbnail from a JPEG at 1/8 scale (what the library feeds it) gives the same answer as the full picture.
tmp = tempfile.mkdtemp(prefix="matchy-palette-")
jpg = os.path.join(tmp, "l.jpg")
landscape().save(jpg, quality=92)
with Image.open(jpg) as full:
    r_full = P.analyse(full)
with Image.open(jpg) as small:
    small.draft("RGB", (P.THUMB * 4, P.THUMB * 4))
    r_small = P.analyse(small)
check("stable: JPEG draft decode gives the same accent and name", r_full["accent"] == r_small["accent"]
      and r_full["gnome_accent"] == r_small["gnome_accent"], (r_full, r_small))
os.remove(jpg); os.rmdir(tmp)

# ---------------- sensible ----------------
r = P.analyse(landscape())
check("landscape: sky and field lead the palette", len(r["palette"]) >= 3 and not any(is_grey(c) for c in r["palette"][:2]), r)
check("landscape: accent is the sky's blue", r["gnome_accent"] == "blue" and r["accent"], r)
check("landscape: the sun (grey-white) is not in the palette", not any(is_grey(c) for c in r["palette"]), r)

r = P.analyse(mono())
check("mono: palette is all greys", r["palette"] and all(is_grey(c) for c in r["palette"]), r)
check("mono: no accent, slate", r["accent"] is None and r["gnome_accent"] == "slate", r)

r = P.analyse(sepia())
acc = P.from_hex(r["accent"]) if r["accent"] else None
check("sepia: palette is warm browns, not greys", r["palette"] and not any(is_grey(c) for c in r["palette"])
      and all(P.from_hex(c)[0] >= P.from_hex(c)[2] for c in r["palette"]), r)
check("sepia: a warm accent, GNOME yellow or orange", acc and acc[0] > acc[2] and r["gnome_accent"] in ("yellow", "orange"), r)

r = P.analyse(night())
check("night: mostly black keeps its black", r["palette"] and is_grey(r["palette"][0]) and max(P.from_hex(r["palette"][0])) < 40, r)
check("night: specks under 1 % give no accent", r["accent"] is None and r["gnome_accent"] == "slate", r)

r = P.analyse(flat((200, 60, 60)))
check("flat red: one colour, red accent", r["palette"] == ["#c83c3c"] and r["accent"] == "#c83c3c" and r["gnome_accent"] == "red", r)

# ---------------- the accent is usable on both a light and a dark UI ----------------
for name, rgb in (("pure blue", (0, 0, 255)), ("navy", (10, 20, 70)), ("pale pink", (250, 215, 225)), ("lime", (190, 255, 60)),
                  ("pastel sky", (200, 225, 240)), ("white-ish yellow", (255, 250, 200)), ("dark olive", (40, 50, 10))):
    u = P.usable(rgb)
    cw, cb = P.contrast(u, (255, 255, 255)), P.contrast(u, (0, 0, 0))
    h0 = P.colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0]
    h1 = P.colorsys.rgb_to_hsv(*(c / 255 for c in u))[0]
    check("usable: %s -> %s has 3:1 on white (%.2f) and black (%.2f)" % (name, P.to_hex(u), cw, cb), cw >= 3 and cb >= 3)
    check("usable: %s keeps its hue" % name, P.hue_distance(h0 * 360, h1 * 360) < 3, (h0 * 360, h1 * 360))
check("usable: an already usable colour is left alone", P.usable((53, 132, 228)) == (53, 132, 228), P.usable((53, 132, 228)))
# GNOME's own accents all sit inside the band the accent is pushed into.
for name, hx in P.GNOME_ACCENTS:
    check("band: GNOME %s is within it" % name, P.LUM_LO <= P.luminance(P.from_hex(hx)) <= P.LUM_HI + 0.001, P.luminance(P.from_hex(hx)))

# ---------------- gnome_accent mapping table ----------------
TABLE = [("#3584e4", "blue"), ("#1e90ff", "blue"), ("#000080", "blue"), ("#2190a4", "teal"), ("#00ffff", "teal"), ("#008080", "teal"),
         ("#3a944a", "green"), ("#00ff00", "green"), ("#228b22", "green"), ("#c88800", "yellow"), ("#ffd700", "yellow"), ("#808000", "yellow"),
         ("#ed5b00", "orange"), ("#ff8c00", "orange"), ("#8b4513", "orange"), ("#e62d42", "red"), ("#ff0000", "red"), ("#800000", "red"),
         ("#d56199", "pink"), ("#ff69b4", "pink"), ("#ff007f", "pink"), ("#9141ac", "purple"), ("#8a2be2", "purple"), ("#ff00ff", "purple"),
         ("#6f8396", "slate"), ("#808080", "slate"), ("#ffffff", "slate"), ("#000000", "slate"), (None, "slate")]
for hx, want in TABLE:
    got = P.gnome_accent(hx)
    check("gnome_accent %s -> %s" % (hx, want), got == want, got)
check("gnome names: the nine GNOME ships (no brown)", P.GNOME_NAMES == ["blue", "teal", "green", "yellow", "orange", "red", "pink", "purple", "slate"])

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
