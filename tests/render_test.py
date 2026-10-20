"""Tests for render.Renderer.cover and .padded: every picture shape crops cleanly to every client
size, a padded crop follows the focus point, and a padded label stays where every pan can see it.

    python3 tests/render_test.py
"""
import itertools, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "matchy", "app"))

from PIL import Image
from render import Renderer, focus_point

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print("PASS", name)
    else:
        fail += 1; print("FAIL", name, detail)


# Masters as the engine stores them (longest edge 3840) in every shape from 2.5:1 to 1:2.5, against
# the sizes clients ask for. Before the fix, w / (w / mw) rounding above mw made ~4 % of these raise
# "box offset can't be negative" (seen on The Great Wave at 640x360, the panel's thumbnail size).
SIZES = [(640, 360), (1280, 720), (1920, 1080), (2560, 1440), (2560, 1600), (1200, 1920), (3440, 1440),
         (1920, 1200), (3840, 2160), (320, 7680), (7680, 320)]
MASTERS = ([(3840, h) for h in range(1536, 3841, 7)] + [(w, 3840) for w in range(1536, 3840, 7)]
           + [(w, h) for w in range(3600, 3841, 13) for h in range(2400, 2700, 11)])


class Master:
    """Stands in for a master image: records the box cover() asks for, and checks it the way Pillow does."""
    def __init__(self, w, h):
        self.size = (w, h)

    def resize(self, size, resample=None, box=None, reducing_gap=None):
        self.box = box
        l, t, r, b = box
        if l < 0 or t < 0:
            raise ValueError("box offset can't be negative")
        if r > self.size[0] or b > self.size[1]:
            raise ValueError("box can't exceed original image size")
        return Master(*size)


errors = []
for (mw, mh), (w, h) in itertools.product(MASTERS, SIZES):
    try:
        out = Renderer.cover(Master(mw, mh), w, h)
        if out.size != (w, h):
            errors.append(((mw, mh), (w, h), "size %s" % (out.size,)))
    except ValueError as e:
        errors.append(((mw, mh), (w, h), str(e)))
check("cover: %d master shapes x %d sizes" % (len(MASTERS), len(SIZES)), not errors, errors[:3])

# The case that showed it, with a real image: The Great Wave's master (3743x2543) cut to the panel's
# 640x360 thumbnail.
img = Image.new("RGB", (3743, 2543))
check("cover: 3743x2543 to 640x360", Renderer.cover(img, 640, 360).size == (640, 360))

# ?pad=: the same sweep for every pad and a focus in each corner, which pushes the box to the edges.
errors = []
for (mw, mh), (w, h) in itertools.product(MASTERS[::5], SIZES):
    for pad, focus in itertools.product((1.0, 1.15, 1.3), ((0, 0), (1, 1), (0.5, 0.5), (0.2, 0.9))):
        try:
            out = Renderer.padded(Master(mw, mh), w, h, pad, focus)
            if out.size != (round(w * pad), round(h * pad)):
                errors.append(((mw, mh), (w, h), pad, "size %s" % (out.size,)))
        except ValueError as e:
            errors.append(((mw, mh), (w, h), pad, focus, str(e)))
check("padded: %d master shapes x %d sizes x 3 pads x 4 focus points" % (len(MASTERS[::5]), len(SIZES)),
      not errors, errors[:3])

# A wide picture on a 16:10 screen: the crop slides towards the focus, as far as the picture allows.
m = Master(3840, 2160)
Renderer.padded(m, 1920, 1200, 1.2)
check("padded: centre focus is the centre crop", abs((m.box[0] + m.box[2]) / 2 - 1920) < 1e-6, m.box)
Renderer.padded(m, 1920, 1200, 1.2, (0.9, 0.5))
check("padded: focus on the right slides the crop to the right edge", abs(m.box[2] - 3840) < 1e-6, m.box)
Renderer.padded(m, 1920, 1200, 1.2, (0.52, 0.5))
check("padded: a focus with room is centred", abs((m.box[0] + m.box[2]) / 2 - 0.52 * 3840) < 1e-6, m.box)

check("focus: read from the sidecar", focus_point({"focus": {"x": 0.25, "y": 1, "source": "hand"}}) == (0.25, 1.0))
check("focus: missing or bad falls back to the centre",
      all(focus_point(sc) == (0.5, 0.5) for sc in (None, {}, {"focus": None}, {"focus": {"x": 2, "y": 0.5}},
                                                    {"focus": {"x": True, "y": 0.5}}, {"focus": [0.1, 0.2]},
                                                    {"focus": {"x": "0.1", "y": 0.2}}, {"focus": {"x": float("nan"), "y": 0}})))

# The padded label: sized for the screen, and inside the region every screen-sized window of the canvas
# shows (from canvas - screen to screen on each axis). A flat picture makes the card easy to find: the
# glass darkens it by 40 %, more than the soft shadow around it ever does.
META = {"title": "A Test Field", "place": "Nowhere", "credit": "Photo: Tester",
        "blurb": "A grey test picture with nothing on it, long enough to wrap the label onto a few lines."}
r = Renderer({"font_dir": "/nonexistent"})


def card_box(img):
    mask = img.convert("L").point(lambda v: 255 if v < 200 * 0.63 else 0)
    return mask.getbbox()


grey = Image.new("RGB", (1600, 1000), (200, 200, 200))
for (w, h), pad, s in itertools.product([(1920, 1200), (1200, 1920), (640, 360), (2560, 1080)], (1.05, 1.2, 1.3),
                                        (1.0, 1.35, 2.0)):
    img = r.label_padded(grey, META, w, h, s, pad, (0.5, 0.5))
    iw, ih = img.size
    b = card_box(img)
    inside = b and b[0] >= iw - w and b[1] >= ih - h and b[2] <= w and b[3] <= h
    check("padded label %dx%d pad %s s %s stays in the always-visible area" % (w, h, pad, s), inside,
          (b, (iw - w, ih - h, w, h)))
plain = r.label(grey, META, 1920, 1200)
padded = r.label_padded(grey, META, 1920, 1200, 1.0, 1.2, (0.5, 0.5))
pb, qb = card_box(plain), card_box(padded)
check("padded label is the screen's label size", (pb[2] - pb[0], pb[3] - pb[1]) == (qb[2] - qb[0], qb[3] - qb[1]),
      (pb, qb))
base = Image.new("RGB", (1920, 1200), (200, 200, 200))
check("draw_label: screen = the image size changes nothing",
      r.draw_label(base, META).tobytes() == r.draw_label(base, META, screen=(1920, 1200)).tobytes())

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
