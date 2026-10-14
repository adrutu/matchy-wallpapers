"""Compare two rendered wallpaper labels (same size, same renderer, built on different CPU
architectures) and report whether they differ only by JPEG noise or by real layout or
font differences.
Usage: python3 tests/label_diff.py A.jpg B.jpg [--out DIFF.png] [--tolerance 16] [--max-frac 0.001]
Exits 0 when at most --max-frac of the pixels differ by more than --tolerance, 1 otherwise,
2 when the two inputs have different sizes or modes."""
import argparse
import sys

from PIL import Image, ImageChops


def main():
    p = argparse.ArgumentParser(description="diff two rendered wallpaper labels")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--out", metavar="DIFF.png")
    p.add_argument("--tolerance", type=float, default=16)
    p.add_argument("--max-frac", type=float, default=0.001)
    ns = p.parse_args()
    A = Image.open(ns.a)
    B = Image.open(ns.b)
    if A.size != B.size or A.mode != B.mode:
        print("error: %s is %dx%d %s but %s is %dx%d %s" % (ns.a, *A.size, A.mode, ns.b, *B.size, B.mode))
        sys.exit(2)
    a = A.convert("RGB")
    b = B.convert("RGB")
    diff = ImageChops.difference(a, b)
    r, g, bl = diff.split()
    d = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    hist = d.histogram()
    total = A.size[0] * A.size[1]
    mean = sum(i * c for i, c in enumerate(hist)) / total
    over = sum(hist[int(ns.tolerance) + 1:])
    frac = over / total
    mask = d.point(lambda v: 255 if v > ns.tolerance else 0)
    box = mask.getbbox()
    print("size: %dx%d" % A.size)
    print("mean difference: %.4f" % mean)
    print("max difference: %d" % d.getextrema()[1])
    print("frac > %g: %.6f" % (ns.tolerance, frac))
    print("box: %s" % (str(box) if box else "none",))
    if ns.out:
        heat = d.point(lambda v: min(255, int(v * 255.0 / max(ns.tolerance, 1))))
        Image.blend(a.convert("L"), heat, 0.4).save(ns.out)
    ok = frac <= ns.max_frac
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
