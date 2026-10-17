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

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
