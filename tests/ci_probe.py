#!/usr/bin/env python3
"""Smoke test run INSIDE a built add-on image, once per architecture (see .github/workflows/build.yml):

    docker run --rm --platform linux/arm64 -v "$PWD/tests:/tests:ro" -v "$PWD/out:/out" \
        --entrypoint python3 matchy:aarch64 /tests/ci_probe.py --out /out/label-aarch64.jpg

Checks that Pillow has raqm (true small caps on the label), that the four bundled fonts load, and
renders one deterministic label (synthetic picture, fixed text) so the workflow can diff the amd64
and aarch64 outputs with tests/label_diff.py. Exits 1 on any failure.
"""
import argparse
import os
import platform
import sys

sys.path.insert(0, os.environ.get("WE_APP_DIR", "/app"))

import PIL  # noqa: E402
from PIL import Image, features  # noqa: E402

import render  # noqa: E402

META = {
    "title": "Speckled Colour Field",
    "place": "Nowhere in particular",
    "credit": "Synthetic test picture · CI",
    "blurb": ("A field of colour scattered with small pale specks that catch the light like frost on a "
              "window, the tone deepening toward the edges where the specks thin out and the surface "
              "settles into a calm and even glow."),
}


def synthetic(w=1600, h=900):
    """A smooth, deterministic picture (no randomness): gradients on each channel."""
    g = Image.linear_gradient("L")  # 256x256, dark at the top
    r = g.resize((w, h))
    b = g.rotate(90, expand=True).resize((w, h))
    gr = g.rotate(180).resize((w, h)).point(lambda v: v * 3 // 4)
    return Image.merge("RGB", (r, gr, b))


def main():
    p = argparse.ArgumentParser(description="probe a built Matchy image")
    p.add_argument("--out", metavar="LABEL.jpg", help="write the rendered 1920x1080 label here")
    p.add_argument("--allow-no-raqm", action="store_true", help="do not fail when raqm is missing")
    ns = p.parse_args()

    ok = True
    print("machine", platform.machine(), "python", platform.python_version(), "pillow", PIL.__version__)
    print("HAVE_RAQM", render.HAVE_RAQM, "raqm", features.version("raqm"), "freetype", features.version("freetype2"))
    if not render.HAVE_RAQM and not ns.allow_no_raqm:
        print("FAIL: raqm missing, the label would get faux small caps")
        ok = False

    style = {"font_dir": os.environ.get("WE_FONT_DIR", "/fonts")}
    renderer = render.Renderer(style)
    for role in sorted(renderer.st["fonts"]):
        # render falls back to Pillow's built-in font rather than fail, so check the file and family.
        path = os.path.join(renderer.st["font_dir"], renderer.st["fonts"][role])
        name = renderer.font(role, 24).getname()
        if os.path.isfile(path) and name[0].startswith("Noto"):
            print("font %-8s %-32s %s ok" % (role, renderer.st["fonts"][role], name))
        else:
            print("FAIL: font %s: %s not loaded (got %s)" % (role, path, name))
            ok = False

    if ok:
        data = renderer.encode(renderer.label(synthetic(), META, 1920, 1080))
        print("label 1920x1080: %d bytes" % len(data))
        if ns.out:
            with open(ns.out, "wb") as f:
                f.write(data)
            print("written", ns.out)

    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
