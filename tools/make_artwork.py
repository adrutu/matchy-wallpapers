#!/usr/bin/env python3
"""Draw the Matchy Wallpapers add-on artwork from code.

Usage:
    python3 tools/make_artwork.py <outdir>      (needs Pillow)

The add-on uses icon.png and logo.png (copy them to matchy/); icon-256.png and logo-500.png go to
docs/ for the README; preview.png is a contact sheet for checking the sizes by eye.

Writes icon.png (128), icon-256.png, logo.png (250x100), logo-500.png
(500x200) and preview.png (contact sheet) into <outdir>. Everything is drawn
with flat shapes at 4x and downsampled with LANCZOS; the only external input
is the Noto fonts (Debian/Ubuntu package fonts-noto-core, or set MATCHY_FONT_DIR).
Output is deterministic: no randomness, no timestamps.
"""

import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

SS = 4  # supersampling factor

# ---------------------------------------------------------------- palette
CHARCOAL = (27, 29, 32)  # #1b1d20  tile / logo plate
RED = (180, 71, 59)  # #b4473b  near hill
ORANGE = (210, 122, 52)  # #d27a34  far hill
MUSTARD = (211, 172, 63)  # #d3ac3f  sun
TEAL = (47, 136, 132)  # #2f8884  sky
CREAM = (230, 220, 198)  # #e6dcc6  bezels, label, wordmark
INK = (27, 29, 32)  # label text bars

FONT_DIR = os.environ.get("MATCHY_FONT_DIR", "/usr/share/fonts/truetype/noto")
FONT_WORD = "NotoSerifDisplay-Bold.ttf"  # "Matchy"
FONT_SUB = "NotoSans-Bold.ttf"  # "WALLPAPERS"
FONT_UI = "NotoSans-Regular.ttf"  # contact sheet captions

# ------------------------------------------------------------- the picture
# One scene, defined in picture units: 1.6 wide x 1.0 tall (16:10).
PIC_W, PIC_H = 1.6, 1.0
SUN = (0.80, 0.50, 0.215)  # cx, cy, r


def far_hill(x):
    # Low ridge with a shoulder on each side and a saddle where the sun sets.
    return (0.635
            - 0.135 * math.exp(-((x - 0.20) / 0.30) ** 2)
            - 0.085 * math.exp(-((x - 1.45) / 0.26) ** 2))


def near_hill(x):
    # One broad hill rising to the right, dipping low on the left.
    return (0.86
            - 0.150 * math.exp(-((x - 1.20) / 0.42) ** 2)
            + 0.030 * math.exp(-((x - 0.25) / 0.25) ** 2))


def render_picture(w, h, focus_x=0.5):
    """Render the scene cover-cropped to a w x h image (like Matchy does)."""
    s = max(w / PIC_W, h / PIC_H)
    vw, vh = w / s, h / s
    ox = min(max(focus_x * PIC_W - vw / 2, 0), PIC_W - vw)
    oy = (PIC_H - vh) / 2

    def P(x, y):
        return ((x - ox) * s, (y - oy) * s)

    img = Image.new("RGB", (w, h), TEAL)
    d = ImageDraw.Draw(img)
    cx, cy, r = SUN
    x0, y0 = P(cx - r, cy - r)
    x1, y1 = P(cx + r, cy + r)
    d.ellipse([x0, y0, x1, y1], fill=MUSTARD)
    for fn, col in ((far_hill, ORANGE), (near_hill, RED)):
        n = 240
        pts = [P(ox + vw * i / n, fn(ox + vw * i / n)) for i in range(n + 1)]
        pts += [(w + 2, h + 2), (-2, h + 2)]
        d.polygon(pts, fill=col)
    return img


# --------------------------------------------------------------- helpers
def rmask(size, box, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle(box, radius=radius, fill=255)
    return m


def screen(canvas, box, bezel, radius, focus_x=0.5, label=None):
    """Cream-bezelled screen at box (x0,y0,x1,y1) showing the picture."""
    d = ImageDraw.Draw(canvas)
    x0, y0, x1, y1 = [round(v) for v in box]
    bezel = round(bezel)
    d.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=CREAM)
    ix0, iy0, ix1, iy1 = x0 + bezel, y0 + bezel, x1 - bezel, y1 - bezel
    iw, ih = ix1 - ix0, iy1 - iy0
    pic = render_picture(iw, ih, focus_x)
    if label:
        draw_label(pic, label)
    inner_r = max(radius - bezel, 1)
    canvas.paste(pic, (ix0, iy0), rmask((iw, ih), [0, 0, iw - 1, ih - 1], inner_r))


def draw_label(pic, spec):
    """The gallery label: a small cream card with two ink lines."""
    w, h = pic.size
    lw, lh, margin = spec["w"] * w, spec["h"] * w, spec["margin"] * w
    x1, y1 = w - margin, h - margin
    x0, y0 = x1 - lw, y1 - lh
    d = ImageDraw.Draw(pic)
    d.rounded_rectangle([x0, y0, x1, y1], radius=lh * 0.14, fill=CREAM)
    pad = lh * 0.22
    bar = lh * 0.19
    d.rectangle([x0 + pad, y0 + pad, x1 - pad, y0 + pad + bar], fill=INK)
    d.rectangle([x0 + pad, y1 - pad - bar * 0.75, x0 + (x1 - x0) * 0.58, y1 - pad], fill=INK)


# ------------------------------------------------------------ the mark
# Layout in mark units: content spans x 0..1 and y 0..MARK_H exactly.
MON = (0.000, 0.000, 0.700, 0.4375)  # 16:10 monitor screen
NECK = (0.305, 0.430, 0.395, 0.535)
FOOT = (0.205, 0.522, 0.495, 0.574)
TAB = (0.610, 0.205, 1.000, 0.725)  # 3:4 portrait tablet
MARK_H = 0.725
LABEL = {"w": 0.50, "h": 0.22, "margin": 0.085}


def draw_mark(canvas, ox, oy, width):
    """Monitor + portrait tablet, the same picture cover-cropped to each.

    The mark is `width` wide and MARK_H * width tall, top-left at (ox, oy),
    drawn over a charcoal ground.
    """
    u = width
    d = ImageDraw.Draw(canvas)

    def B(b):
        return (ox + b[0] * u, oy + b[1] * u, ox + b[2] * u, oy + b[3] * u)

    screen(canvas, B(MON), 0.024 * u, 0.045 * u)
    d.rectangle(B(NECK), fill=CREAM)
    d.rounded_rectangle(B(FOOT), radius=0.024 * u, fill=CREAM)

    # Tablet in front of the monitor's right edge, with a charcoal gap ring
    # so the two read as separate objects.
    tab = B(TAB)
    gap = 0.032 * u
    rad = 0.070 * u
    d.rounded_rectangle((tab[0] - gap, tab[1] - gap, tab[2] + gap, tab[3] + gap),
                        radius=rad + gap, fill=CHARCOAL)
    screen(canvas, tab, 0.034 * u, rad, label=LABEL)


def make_icon(px):
    n = px * SS
    plate = Image.new("RGB", (n, n), CHARCOAL)
    w = 0.84 * n
    draw_mark(plate, (n - w) / 2, (n - MARK_H * w) / 2, w)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    img.paste(plate, (0, 0), rmask((n, n), [0, 0, n - 1, n - 1], round(0.22 * n)))
    return img.resize((px, px), Image.LANCZOS)


# ------------------------------------------------------------- the logo
def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


def make_logo(w, h):
    """Mark on the left; "Matchy" over a tracked "WALLPAPERS" of equal width."""
    W, H = w * SS, h * SS
    plate = Image.new("RGB", (W, H), CHARCOAL)
    d = ImageDraw.Draw(plate)

    pad = 0.13 * H
    mw = (H - 2 * pad) / MARK_H
    draw_mark(plate, pad, (H - MARK_H * mw) / 2, mw)

    tx = pad + mw + 0.15 * H
    avail = W - tx - pad
    size = round(0.46 * H)
    while True:
        word = font(FONT_WORD, size)
        wb = d.textbbox((0, 0), "Matchy", font=word, anchor="ls")
        if wb[2] - wb[0] <= avail:
            break
        size -= 1
    word_w = wb[2] - wb[0]

    sub_text = "WALLPAPERS"
    sub = font(FONT_SUB, round(0.145 * H))
    glyphs = sum(d.textlength(c, font=sub) for c in sub_text)
    track = (word_w - glyphs) / (len(sub_text) - 1)  # justify to the word
    cap = -d.textbbox((0, 0), "W", font=sub, anchor="ls")[1]

    asc = -wb[1]  # cap/ascender height of "Matchy" above its baseline
    gap = 0.15 * H
    block = asc + gap + cap
    base1 = (H - block) / 2 + asc
    d.text((tx - wb[0], base1), "Matchy", font=word, fill=CREAM, anchor="ls")
    x, base2 = tx, base1 + gap + cap
    for c in sub_text:
        d.text((x, base2), c, font=sub, fill=MUSTARD, anchor="ls")
        x += d.textlength(c, font=sub) + track

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.paste(plate, (0, 0), rmask((W, H), [0, 0, W - 1, H - 1], round(0.16 * H)))
    return img.resize((w, h), Image.LANCZOS)


# ---------------------------------------------------------- contact sheet
def make_preview(icon256, icon128, logo500, logo250):
    pw, ph = 860, 670
    sheet = Image.new("RGB", (pw * 2, ph), (255, 255, 255))
    cap = font(FONT_UI, 15)
    for i, (bg, fg) in enumerate((((255, 255, 255), (90, 90, 90)),
                                  ((17, 17, 17), (170, 170, 170)))):
        panel = Image.new("RGB", (pw, ph), bg)
        d = ImageDraw.Draw(panel)
        x, base = 40, 330
        sizes = [(256, icon256), (128, icon128),
                 (64, icon128.resize((64, 64), Image.LANCZOS)),
                 (32, icon128.resize((32, 32), Image.LANCZOS))]
        for s, im in sizes:
            panel.paste(im, (x, base - s), im)
            d.text((x, base + 12), f"{s} px", font=cap, fill=fg)
            x += s + 50
        panel.paste(logo500, (40, 400), logo500)
        d.text((40, 612), "logo-500.png  500 x 200", font=cap, fill=fg)
        panel.paste(logo250, (580, 400), logo250)
        d.text((580, 512), "logo.png  250 x 100", font=cap, fill=fg)
        d.text((40, 24), "on #ffffff" if i == 0 else "on #111111", font=cap, fill=fg)
        sheet.paste(panel, (i * pw, 0))
    return sheet


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: make_artwork.py <outdir>")
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    icon256 = make_icon(256)
    icon128 = make_icon(128)
    logo500 = make_logo(500, 200)
    logo250 = make_logo(250, 100)
    icon128.save(os.path.join(out, "icon.png"), optimize=True)
    icon256.save(os.path.join(out, "icon-256.png"), optimize=True)
    logo250.save(os.path.join(out, "logo.png"), optimize=True)
    logo500.save(os.path.join(out, "logo-500.png"), optimize=True)
    make_preview(icon256, icon128, logo500, logo250).save(
        os.path.join(out, "preview.png"), optimize=True)
    print("wrote", ", ".join(sorted(f for f in os.listdir(out) if f.endswith(".png"))))


if __name__ == "__main__":
    main()
