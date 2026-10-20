"""Tests for render.Renderer.cover: every picture shape crops cleanly to every client size.

    python3 tests/render_test.py
"""
import itertools, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "matchy", "app"))

from PIL import Image
from render import Renderer

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

# ---------- focus points ----------
from render import crop_box, edge_focus

# The same sweep with focus points everywhere, edges and corners included: never a box outside the picture.
FOCI = [(0, 0), (1, 1), (0, 1), (1, 0), (0.5, 0.5), (0.13, 0.87), (0.999, 0.001)]
errors = []
for (mw, mh), (w, h), f in itertools.product(MASTERS[::3], SIZES, FOCI):
    try:
        if Renderer.cover(Master(mw, mh), w, h, f).size != (w, h):
            errors.append(((mw, mh), (w, h), f, "size"))
    except ValueError as e:
        errors.append(((mw, mh), (w, h), f, str(e)))
check("cover with focus: %d shapes x %d sizes x %d points" % (len(MASTERS[::3]), len(SIZES), len(FOCI)),
      not errors, errors[:3])

check("no focus: the same centred box as before", crop_box(3840, 2160, 1080, 1920) == crop_box(3840, 2160, 1080, 1920, None)
      and abs(crop_box(3840, 2160, 1080, 1920)[0] - (3840 - 2160 * 9 / 16) / 2) < 1e-9)


def inside(box, f, mw, mh):
    return box[0] <= f[0] * mw <= box[2] and box[1] <= f[1] * mh <= box[3]


# Portrait (9:16, a tall tablet) and ultrawide (21:9, 32:9) crops of landscape, portrait and square
# pictures keep every focus point in frame, and centre it whenever the picture leaves room.
shapes = [(3840, 2160), (2160, 3840), (3840, 3840), (3840, 1600)]
screens = [(1080, 1920), (1200, 1920), (3440, 1440), (5120, 1440)]
grid = [(x / 10, y / 10) for x in range(11) for y in range(11)]
bad, off_centre = [], []
for (mw, mh), (w, h), f in itertools.product(shapes, screens, grid):
    b = crop_box(mw, mh, w, h, f)
    if not inside(b, f, mw, mh):
        bad.append(((mw, mh), (w, h), f, b))
    cw, ch = b[2] - b[0], b[3] - b[1]
    if cw / 2 <= f[0] * mw <= mw - cw / 2 and abs((b[0] + b[2]) / 2 - f[0] * mw) > 1e-6:
        off_centre.append(((mw, mh), (w, h), f, b))
check("portrait and ultrawide crops keep the focus inside", not bad, bad[:3])
check("the focus is centred when the picture has room", not off_centre, off_centre[:3])

# Clamping: a focus at an edge pins the box to that edge, never past it.
l, t, r, b = crop_box(3840, 2160, 1080, 1920, (0.0, 0.5))
check("clamp: focus on the left edge", l == 0 and t == 0 and b == 2160, (l, t, r, b))
l, t, r, b = crop_box(3840, 2160, 1080, 1920, (1.0, 0.5))
check("clamp: focus on the right edge", r == 3840 and abs(r - l - 2160 * 9 / 16) < 1e-6, (l, t, r, b))
l, t, r, b = crop_box(2160, 3840, 3440, 1440, (0.5, 0.02))
check("clamp: ultrawide of a portrait picture, focus at the top", t == 0 and l == 0 and r == 2160, (l, t, r, b))
l, t, r, b = crop_box(2160, 3840, 3440, 1440, (0.5, 0.98))
check("clamp: ultrawide of a portrait picture, focus at the bottom", b == 3840, (l, t, r, b))


def subject_picture(cx):
    """1600x900, flat grey, with a busy red-and-white subject 160 px wide centred at x = cx."""
    from PIL import ImageDraw
    im = Image.new("RGB", (1600, 900), (128, 128, 128))
    d = ImageDraw.Draw(im)
    for i in range(16):
        d.rectangle((cx - 80 + i * 10, 300, cx - 75 + i * 10, 600), fill=(220, 20, 20))
        d.rectangle((cx - 75 + i * 10, 300 + i * 18, cx - 70 + i * 10, 318 + i * 18), fill=(255, 255, 255))
    return im


def red_share(im):
    raw = im.convert("RGB").tobytes()
    n = len(raw) // 3
    return sum(1 for i in range(0, 3 * n, 3) if raw[i] > 180 and raw[i + 1] < 80 and raw[i + 2] < 80) / n


# The case that started it: a 9:16 crop of a picture whose subject is off to the right.
pic = subject_picture(1360)
fx, fy = edge_focus(pic)
check("edge_focus finds an off-centre subject", 0.78 < fx < 0.92 and 0.4 < fy < 0.6, (fx, fy))
check("edge_focus: a flat picture gives the centre", edge_focus(Image.new("RGB", (800, 600), (40, 90, 200))) == (0.5, 0.5))
check("9:16 centred crop loses the subject", red_share(Renderer.cover(pic, 360, 640)) == 0)
check("9:16 crop around the focus keeps the subject", red_share(Renderer.cover(pic, 360, 640, (fx, fy))) > 0.02)
check("span: every tile keeps the subject", red_share(Renderer().span(pic, {}, [(0, 0, 360, 640, False), (360, 0, 640, 360, True)],
                                                                      focus=(fx, fy)).crop((0, 0, 360, 640))) > 0.02)

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
