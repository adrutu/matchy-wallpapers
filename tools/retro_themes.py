#!/usr/bin/env python3
"""Retro stripe wallpapers: solid 80s colour bands on dark grey or beige-grey.

Original geometric artwork in palettes borrowed from games consoles, instant cameras, videotape
and hi-fi of the 70s-90s. No logos, no text. Deterministic: same script, same pixels.

    python3 retro_themes.py OUT_DIR [--only ID ...] [--size 3840x2160]

Writes OUT_DIR/theme-<theme>/<name>.jpg plus a meta.json per folder (Matchy format).
"""
import argparse
import json
import math
import os
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SS = 2  # supersampling factor for clean edges


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def shade(c, f):
    """f < 1 darkens, f > 1 lightens toward white."""
    c = hexrgb(c) if isinstance(c, str) else c
    if f <= 1:
        return tuple(int(v * f) for v in c)
    return tuple(int(v + (255 - v) * (f - 1)) for v in c)


# ---------------------------------------------------------------- backgrounds
BG = {
    "charcoal": "#2a2a2b",
    "warm-charcoal": "#2e2c29",
    "graphite": "#343538",
    "slate": "#3a3a3d",
    "putty": "#cfc9ba",
    "beige-grey": "#c8c2b2",
    "light-putty": "#d8d3c6",
    "console-grey": "#bfbdb8",
}


# ---------------------------------------------------------------- geometry
def sample_path(start, heading_deg, segs, step):
    """Turtle path. segs: ('L', length) or ('A', radius, degrees; + = clockwise on screen).
    Returns a list of (x, y, theta) with theta the heading in radians."""
    x, y = start
    th = math.radians(heading_deg)
    pts = [(x, y, th)]
    for seg in segs:
        if seg[0] == "L":
            n = max(1, int(seg[1] / step))
            dx, dy = math.cos(th), math.sin(th)
            for i in range(1, n + 1):
                t = seg[1] * i / n
                pts.append((x + dx * t, y + dy * t, th))
            x, y = pts[-1][0], pts[-1][1]
        else:
            r, a = seg[1], math.radians(seg[2])
            sgn = 1 if a > 0 else -1
            nx, ny = -math.sin(th), math.cos(th)
            cx, cy = x + sgn * r * nx, y + sgn * r * ny
            n = max(2, int(abs(a) * r / step))
            for i in range(1, n + 1):
                t = th + a * i / n
                px = cx - sgn * r * (-math.sin(t))
                py = cy - sgn * r * math.cos(t)
                pts.append((px, py, t))
            th = th + a
            x, y = pts[-1][0], pts[-1][1]
    return pts


def offset_poly(pts, d1, d2):
    left = [(x - math.sin(t) * d1, y + math.cos(t) * d1) for x, y, t in pts]
    right = [(x - math.sin(t) * d2, y + math.cos(t) * d2) for x, y, t in pts]
    return left + right[::-1]


def band(draw, pts, colours, w, gap, track=None, pad=0):
    """Parallel stripes centred on the path, first colour on the +normal (right-hand) side."""
    n = len(colours)
    total = n * w + (n - 1) * gap
    if track is not None:
        draw.polygon(offset_poly(pts, -total / 2 - pad, total / 2 + pad), fill=track)
    for i, c in enumerate(colours):
        d1 = total / 2 - i * (w + gap)
        draw.polygon(offset_poly(pts, d1 - w, d1), fill=c)
    return total


def chevron_column(draw, cx, top, hw, colours, t, gap, bottom, point="up"):
    """Stacked ^-shaped stripes in a column of half-width hw; the last one runs to `bottom`."""
    y = top
    for i, c in enumerate(colours):
        last = i == len(colours) - 1
        y2 = bottom if last else y + t
        poly = [(cx - hw, y + hw), (cx, y), (cx + hw, y + hw)]
        if last:
            poly += [(cx + hw, y2), (cx - hw, y2)]
        else:
            poly += [(cx + hw, y2 + hw), (cx, y2), (cx - hw, y2 + hw)]
        if point == "right":
            poly = [(py, px) for px, py in poly]
        draw.polygon(poly, fill=c)
        y = y2 + gap


# ---------------------------------------------------------------- motifs
# Every motif gets (img, draw, W, H, spec) at supersampled size and paints on top of the background.

def m_bend_u(img, d, W, H, s):
    """Band enters from the left, makes a U-turn and leaves to the left again."""
    cols = s["colours"]
    w = H * s.get("w", 0.05)
    gap = w * s.get("gap", 0.25)
    total = len(cols) * w + (len(cols) - 1) * gap
    hole = w * s.get("hole", 1.0)
    r = total / 2 + hole
    y0 = H * s.get("y", 0.5) - r
    xturn = W * s.get("x", 0.62)
    pts = sample_path((-W * 0.1, y0), 0, [("L", xturn + W * 0.1), ("A", r, 180), ("L", W * 1.3)], 3 * SS)
    band(d, pts, cols, w, gap, track=s.get("track"), pad=w * s.get("pad", 0.3))


def m_swoop(img, d, W, H, s):
    """Band enters from one edge and bends 90 degrees off another (corner swoop / S-curve)."""
    cols = s["colours"]
    w = H * s.get("w", 0.05)
    gap = w * s.get("gap", 0.25)
    total = len(cols) * w + (len(cols) - 1) * gap
    r = total / 2 + w * s.get("hole", 2.0)
    segs = [("L", W * s.get("run", 0.5) + W * 0.1)]
    for turn in s.get("turns", [90]):
        segs += [("A", r, turn), ("L", H * s.get("leg", 0.35))]
    segs[-1] = ("L", W * 1.4)
    start = (-W * 0.1, H * s.get("y", 0.3))
    pts = sample_path(start, s.get("heading", 0), segs, 3 * SS)
    band(d, pts, cols, w, gap, track=s.get("track"), pad=w * s.get("pad", 0.3))


def m_stairs(img, d, W, H, s):
    """Band rising across the screen in rounded 45-degree steps."""
    cols = s["colours"]
    w = H * s.get("w", 0.035)
    gap = w * s.get("gap", 0.25)
    total = len(cols) * w + (len(cols) - 1) * gap
    r = total / 2 + w * 1.5
    flat, rise = W * s.get("flat", 0.2), H * s.get("rise", 0.25)
    segs = [("L", flat + W * 0.15)]
    for _ in range(s.get("steps", 3)):
        segs += [("A", r, -45), ("L", rise), ("A", r, 45), ("L", flat)]
    segs[-1] = ("L", W)
    pts = sample_path((-W * 0.15, H * s.get("y", 0.85)), 0, segs, 3 * SS)
    band(d, pts, cols, w, gap, track=s.get("track"), pad=w * s.get("pad", 0.3))


def m_sash(img, d, W, H, s):
    """Straight diagonal band through (x, y) at `angle`; optionally cut square at `end`."""
    cols = s["colours"]
    w = H * s.get("w", 0.045)
    gap = w * s.get("gap", 0.22)
    a = math.radians(s.get("angle", -28))
    cx, cy = W * s.get("x", 0.5), H * s.get("y", 0.55)
    back = W * 1.5
    length = back + (W * s["end"] if "end" in s else W * 1.5)
    start = (cx - math.cos(a) * back, cy - math.sin(a) * back)
    pts = sample_path(start, math.degrees(a), [("L", length)], 20 * SS)
    band(d, pts, cols, w, gap, track=s.get("track"), pad=w * s.get("pad", 0.3))


def m_hbands(img, d, W, H, s):
    """Horizontal stripes; ends either full bleed, staggered steps or rounded pills."""
    cols = s["colours"]
    w = H * s.get("w", 0.04)
    gap = w * s.get("gap", 0.3)
    y = H * s.get("y", 0.6)
    ends = s.get("ends", "bleed")
    x0 = W * s.get("x0", -0.05)
    for i, c in enumerate(cols):
        if ends == "bleed":
            x1 = W * 1.05
        else:
            x1 = W * (s.get("x1", 0.7) + s.get("stagger", 0.04) * i * s.get("dir", 1))
        if ends == "pill":
            d.rounded_rectangle([x0, y, x1, y + w], radius=w / 2, fill=c)
        else:
            d.rectangle([x0, y, x1, y + w], fill=c)
        y += w + gap


def m_vbands(img, d, W, H, s):
    """Full-height vertical stripes (instant-camera body stripe)."""
    cols = s["colours"]
    w = W * s.get("w", 0.03)
    gap = w * s.get("gap", 0.0)
    x = W * s.get("x", 0.3)
    if s.get("track"):
        tot = len(cols) * w + (len(cols) - 1) * gap
        p = w * s.get("pad", 0.25)
        d.rectangle([x - p, -10, x + tot + p, H + 10], fill=s["track"])
    for c in cols:
        d.rectangle([x, -10, x + w, H + 10], fill=c)
        x += w + gap


def m_rings(img, d, W, H, s):
    """Concentric rings around (x, y) (a corner or a bottom-edge sunrise)."""
    cols = s["colours"]
    w = H * s.get("w", 0.06)
    gap = w * s.get("gap", 0.22)
    cx, cy = W * s.get("x", 0.0), H * s.get("y", 1.0)
    r = H * s.get("r0", 0.18) + len(cols) * (w + gap)
    gapc = s.get("gapc", s["bg"])
    for c in cols:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
        r -= w
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=gapc)
        r -= gap
    if s.get("core"):
        r += gap
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=s["core"])


def m_chevrons(img, d, W, H, s):
    cols = s["colours"]
    if s.get("point", "up") == "up":
        hw = W * s.get("hw", 0.07)
        chevron_column(d, W * s.get("x", 0.78), H * s.get("top", 0.12), hw, cols,
                       H * s.get("t", 0.075), H * s.get("gap", 0.014), H * 1.1)
    else:
        hw = H * s.get("hw", 0.13)
        chevron_column(d, H * s.get("y", 0.5), W * s.get("top", 0.55), hw, cols,
                       W * s.get("t", 0.045), W * s.get("gap", 0.008), -W * 0.1, point="right")


def m_chevrons_left(img, d, W, H, s):
    """Chevrons pointing right, running in from the left edge."""
    cols = s["colours"]
    hw = H * s.get("hw", 0.13)
    cy = H * s.get("y", 0.5)
    t, g = W * s.get("t", 0.04), W * s.get("gap", 0.008)
    tip = W * s.get("tip", 0.6)
    x = tip
    for i, c in enumerate(cols):
        x2 = x - t
        poly = [(x2, cy - hw), (x2 + hw, cy), (x2, cy + hw)]
        if i == len(cols) - 1:
            poly = [(-10, cy - hw), (x - hw, cy - hw), (x, cy), (x - hw, cy + hw), (-10, cy + hw)]
        else:
            poly = [(x - hw, cy - hw), (x, cy), (x - hw, cy + hw), (x2 - hw, cy + hw), (x2, cy), (x2 - hw, cy - hw)]
        d.polygon(poly, fill=c)
        x = x2 - g


def m_ribs(img, d, W, H, s):
    """Horizontal ribbing (console fronts, corrugated sleeves) over a y-range."""
    y0, y1 = H * s.get("ry0", 0.0), H * s.get("ry1", 1.0)
    p = H * s.get("pitch", 0.018)
    c = s.get("ribc") or shade(s["bg"], s.get("ribf", 0.82))
    y = y0
    while y < y1:
        d.rectangle([-10, y, W + 10, y + p * s.get("duty", 0.35)], fill=c)
        y += p


def m_aperture(img, d, W, H, s):
    """Aperture-grille vertical RGB stripes filling a block, framed by silver rules."""
    x0, x1 = W * s.get("ax0", 0.58), W * s.get("ax1", 1.02)
    y0, y1 = H * s.get("ay0", -0.02), H * s.get("ay1", 1.02)
    pitch = W * s.get("pitch", 0.012)
    cols = s["colours"]
    x = x0
    i = 0
    while x < x1:
        d.rectangle([x, y0, x + pitch * 0.62, y1], fill=cols[i % len(cols)])
        x += pitch
        i += 1
    if s.get("rule"):
        rw = W * 0.012
        d.rectangle([x0 - rw * 2.2, y0, x0 - rw * 1.2, y1], fill=s["rule"])
        d.rectangle([x0 - rw * 4.4, y0, x0 - rw * 2.8, y1], fill=s["rule"])


def m_colour_bars(img, d, W, H, s):
    """Muted test-card bars with gaps, plus a lower strip of short bars."""
    cols = s["colours"]
    n = len(cols)
    x0, x1 = W * 0.08, W * 0.92
    gap = W * 0.008
    bw = (x1 - x0 - gap * (n - 1)) / n
    y0, y1 = H * 0.14, H * 0.6
    for i, c in enumerate(cols):
        x = x0 + i * (bw + gap)
        d.rectangle([x, y0, x + bw, y1], fill=c)
    y2, y3 = y1 + H * 0.025, y1 + H * 0.1
    rev = s.get("lower", cols[::-1])
    for i, c in enumerate(rev):
        x = x0 + i * (bw + gap)
        d.rectangle([x, y2, x + bw, y3], fill=c)
    y4 = y3 + H * 0.025
    steps = s.get("scale", [])
    sw = (x1 - x0 - gap * (len(steps) - 1)) / max(1, len(steps))
    for i, c in enumerate(steps):
        x = x0 + i * (sw + gap)
        d.rectangle([x, y4, x + sw, H * 0.86], fill=c)


def m_frames(img, d, W, H, s):
    """Instant-photo frames (thick bottom border) holding small stripe compositions."""
    frames = s["frames"]
    for f in frames:
        fw = H * f.get("size", 0.42)
        border, bottom = fw * 0.06, fw * 0.26
        fh = fw + border + bottom - border  # square picture area
        tile = Image.new("RGBA", (int(fw), int(fw * 1.2)), (0, 0, 0, 0))
        td = ImageDraw.Draw(tile)
        td.rectangle([0, 0, fw, fw * 1.2], fill=s.get("frame", "#ece7dc"))
        px0, py0, px1, py1 = border, border, fw - border, fw - border
        inner = Image.new("RGB", (int(px1 - px0), int(py1 - py0)), f["bg"])
        idr = ImageDraw.Draw(inner)
        iw, ih = inner.size
        kind = f["kind"]
        if kind == "rings":
            cols = f["colours"]
            r = ih * 0.95
            for c in cols:
                idr.ellipse([iw / 2 - r, ih * 1.05 - r, iw / 2 + r, ih * 1.05 + r], fill=c)
                r -= ih * 0.13
            idr.ellipse([iw / 2 - r, ih * 1.05 - r, iw / 2 + r, ih * 1.05 + r], fill=f["bg"])
        elif kind == "stripes":
            cols = f["colours"]
            sw = ih / (len(cols) * 1.5 + 1)
            y = ih * 0.5 - (len(cols) * sw * 1.3) / 2
            for c in cols:
                idr.rectangle([0, y, iw, y + sw], fill=c)
                y += sw * 1.3
        elif kind == "diag":
            cols = f["colours"]
            sw = iw * 0.12
            for i, c in enumerate(cols):
                o = (i - len(cols) / 2) * sw * 1.15
                idr.polygon([(o, ih), (o + sw, ih), (o + sw + ih, 0), (o + ih, 0)], fill=c)
        elif kind == "block":
            cols = f["colours"]
            bh = ih / len(cols)
            for i, c in enumerate(cols):
                idr.rectangle([0, i * bh, iw, (i + 1) * bh], fill=c)
        tile.paste(inner, (int(px0), int(py0)))
        tile = tile.rotate(f.get("rot", 0), resample=Image.BICUBIC, expand=True)
        # soft shadow
        sh = Image.new("RGBA", tile.size, (0, 0, 0, 0))
        alpha = tile.split()[3].point(lambda v: int(v * s.get("shadow", 0.45)))
        sh.putalpha(alpha)
        sh = sh.filter(ImageFilter.GaussianBlur(H * 0.012))
        x, y = int(W * f["x"] - tile.size[0] / 2), int(H * f["y"] - tile.size[1] / 2)
        off = int(H * 0.012)
        img.paste(sh, (x + off, y + off * 2), sh)
        img.paste(tile, (x, y), tile)


def m_dots(img, d, W, H, s):
    """A row of solid dots (button colours) as a small accent."""
    cols = s["dots"]
    r = H * s.get("dr", 0.022)
    x, y = W * s.get("dx", 0.08), H * s.get("dy", 0.12)
    for c in cols:
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)
        x += r * 2.9


def m_pinlines(img, d, W, H, s):
    """Thin full-width rules (like the bezel lines around a handheld's screen)."""
    for (y, c, t) in s["pins"]:
        d.rectangle([-10, H * y, W + 10, H * (y + t)], fill=c)


MOTIFS = {
    "bend_u": m_bend_u, "swoop": m_swoop, "stairs": m_stairs, "sash": m_sash,
    "hbands": m_hbands, "vbands": m_vbands, "rings": m_rings, "chevrons": m_chevrons,
    "chevrons_left": m_chevrons_left, "ribs": m_ribs, "aperture": m_aperture,
    "colour_bars": m_colour_bars, "frames": m_frames, "dots": m_dots, "pinlines": m_pinlines,
}


# ---------------------------------------------------------------- finishing
def finish(img, bg, seed, grain=3.2, scanlines=0.0, vignette=0.10):
    a = np.asarray(img).astype(np.float32)
    h, w, _ = a.shape
    rng = np.random.default_rng(seed)
    # low-frequency mottling: printed/plastic surface, not flat digital
    lo = rng.normal(0, 1, (h // 64 + 2, w // 64 + 2)).astype(np.float32)
    lo = np.asarray(Image.fromarray(lo).resize((w, h), Image.BICUBIC))
    a += lo[..., None] * 2.2
    # fine monochrome grain
    a += rng.normal(0, grain, (h, w)).astype(np.float32)[..., None]
    if scanlines:
        rows = (np.arange(h) % 4 < 2).astype(np.float32)
        a *= (1 - scanlines * rows)[:, None, None]
    if vignette:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        rr = ((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2
        a *= (1 - vignette * np.clip(rr - 0.35, 0, None) / 1.65)[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def render(spec, size):
    W, H = size[0] * SS, size[1] * SS
    bg = BG.get(spec["bg"], spec["bg"])
    img = Image.new("RGB", (W, H), hexrgb(bg))
    d = ImageDraw.Draw(img)
    for layer in spec["layers"]:
        s = dict(layer)
        s["bg"] = hexrgb(bg)
        for k in ("colours", "dots", "lower", "scale"):
            if k in s:
                s[k] = [hexrgb(c) for c in s[k]]
        for k in ("track", "gapc", "core", "rule", "ribc"):
            if isinstance(s.get(k), str):
                s[k] = hexrgb(s[k])
        if "pins" in s:
            s["pins"] = [(y, hexrgb(c), t) for y, c, t in s["pins"]]
        MOTIFS[s["motif"]](img, d, W, H, s)
    img = img.resize(size, Image.LANCZOS)
    if spec.get("mirror"):
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    return finish(img, bg, seed=zlib.crc32(spec["name"].encode()),
                  grain=spec.get("grain", 3.2), scanlines=spec.get("scanlines", 0.0),
                  vignette=spec.get("vignette", 0.10))


# ---------------------------------------------------------------- palettes
# Muted, printed-plastic versions of the hardware colours: nothing neon, nothing glowing.
CART_MAROON, CART_GOLD, CART_CREAM = "#9b2335", "#c8a24a", "#e6dcc6"
PANEL_RED, PANEL_BLACK, PANEL_LIGHT = "#b8302e", "#1f1f20", "#b3b1ac"
FOUR_BUTTONS = ["#b83a3a", "#d9a936", "#3e8a55", "#3a5c9e"]           # red, yellow, green, blue buttons
SASH_DK, SASH_PURPLE, SASH_LAV = "#4a4a52", "#5b4e9c", "#9c93c9"
HANDHELD_MAGENTA, HANDHELD_NAVY = "#8e2557", "#2f3277"
HANDHELD_GREENS = ["#0f380f", "#306230", "#6b8c2a", "#a3b04a"]
RAINBOW = ["#c4433a", "#d97a33", "#e2b23a", "#559a4f", "#33689f"]  # red, orange, yellow, green, blue
TAPE = ["#b4473b", "#d27a34", "#d3ac3f", "#2f8884"]             # red, orange, mustard, teal
EARTH = ["#5a3e2b", "#a4502a", "#c9762f", "#c9a066"]           # brown, rust, orange, tan
BLUELINE = ["#e5e1d6", "#5b8fc2", "#c2423a"]                   # cream, sky, red
STEREO_STEEL, STEREO_SILVER, STEREO_ORANGE = "#3f5f86", "#b4b8bc", "#dd6b2a"
SPORTS_YELLOW = "#e0b225"
GRILLE = ["#b8443b", "#4e9157", "#3f62a5"]
CONSOLE = ["#c9363e", "#e2a63a", "#2e9c7e", "#2f66aa"]               # red, yellow, green, blue
CONSOLE_PASTEL = ["#5aa98a", "#cf5a63", "#7097c9", "#c98bb0"]        # green, red, blue, pink
PRIMARY = ["#c8362f", "#e2b02c", "#2d5aa6"]

# theme -> (folder, label). Folders follow the library's theme-* convention; each wants a Matchy
# `sets` entry (id_prefix = theme, abstract: true) so ids stay short and captions treat it as art.
THEMES = {
    "console": ("theme-console", "Games console colours"),
    "instant": ("theme-instant", "Instant camera colours"),
    "videotape": ("theme-videotape", "Videotape colours"),
    "hifi": ("theme-hifi", "Hi-fi colours"),
}

SPECS = [
    # ------------------------------------------------------------ Games console
    {"theme": "console", "name": "cartridge-bend", "title": "Cartridge Bend", "bg": "warm-charcoal",
     "context": "Graphic of thick maroon, gold and cream stripes making a rounded U-turn on a dark grey background, in the colours of an early-1980s games console and its controllers.",
     "layers": [{"motif": "bend_u", "colours": [CART_MAROON, CART_GOLD, CART_CREAM], "w": 0.07, "gap": 0.2,
                 "track": "#1b1a19", "x": 0.58, "y": 0.5, "hole": 0.9}]},
    {"theme": "console", "name": "handheld-greens", "title": "Handheld Greens", "bg": "#c4bfb0",
     "context": "Graphic of four rounded bars in the dark-to-pale greens of an early handheld game screen, under two thin maroon and navy rules, with two magenta dots, on a beige-grey background.",
     "layers": [{"motif": "pinlines", "pins": [(0.17, HANDHELD_MAGENTA, 0.007), (0.19, HANDHELD_NAVY, 0.007)]},
                {"motif": "hbands", "colours": HANDHELD_GREENS, "ends": "pill", "x0": -0.1, "x1": 0.38,
                 "stagger": 0.08, "y": 0.36, "w": 0.075, "gap": 0.3},
                {"motif": "dots", "dots": [HANDHELD_MAGENTA, HANDHELD_MAGENTA], "dx": 0.8, "dy": 0.42, "dr": 0.05}]},
    {"theme": "console", "name": "four-button-chevrons", "title": "Four-Button Chevrons", "bg": "charcoal",
     "context": "Graphic of a column of stacked chevrons in red, yellow, green and blue, the four button colours of an early-1990s games controller, on a dark grey background.",
     "layers": [{"motif": "chevrons", "colours": FOUR_BUTTONS, "x": 0.76, "top": 0.1, "hw": 0.075, "t": 0.085, "gap": 0.016}]},
    {"theme": "console", "name": "console-front", "title": "Console Front Panel", "bg": "#3b3b3d",
     "context": "Graphic of a pale grey block over a black band, a thin red line and dark grey ribbing, laid out like the front panel of a mid-1980s games console.",
     "layers": [{"motif": "hbands", "colours": [PANEL_LIGHT], "y": -0.05, "w": 0.5, "x0": -0.05},
                {"motif": "hbands", "colours": [PANEL_BLACK], "y": 0.45, "w": 0.07},
                {"motif": "hbands", "colours": [PANEL_RED], "y": 0.545, "w": 0.01},
                {"motif": "ribs", "ry0": 0.6, "ry1": 1.05, "pitch": 0.022, "duty": 0.4, "ribf": 0.8}]},
    {"theme": "console", "name": "console-sash", "title": "Console Sash", "bg": "light-putty",
     "context": "Graphic of a diagonal sash of dark grey, purple and lavender stripes ending in a square cut, on a pale beige-grey background, in the colours of an early-1990s games console.",
     "layers": [{"motif": "sash", "colours": [SASH_DK, SASH_PURPLE, SASH_LAV], "angle": -24, "x": 0.45,
                 "y": 0.6, "w": 0.065, "gap": 0.2, "end": 0.28}]},
    {"theme": "console", "name": "cartridge-arcs", "title": "Cartridge Arcs", "bg": "putty",
     "context": "Graphic of thick concentric arcs in maroon, gold and dark brown rising from the bottom-left corner of a beige-grey background, in early-1980s games console colours.",
     "layers": [{"motif": "rings", "colours": [CART_MAROON, CART_GOLD, "#3a3533"], "x": 0.06, "y": 1.0,
                 "r0": 0.34, "w": 0.12, "gap": 0.18}]},

    # ------------------------------------------------------------ Instant camera
    {"theme": "instant", "name": "instant-stripe", "title": "Instant Stripe", "bg": "#262626",
     "context": "Graphic of a full-height vertical rainbow stripe in red, orange, yellow, green and blue beside dark concentric rings, on a black-grey background, after a late-1970s instant camera.",
     "layers": [{"motif": "vbands", "colours": RAINBOW, "x": 0.26, "w": 0.026},
                {"motif": "rings", "colours": ["#1e1e1e", "#2c2c2c"], "x": 0.68, "y": 0.5,
                 "r0": 0.1, "w": 0.05, "gap": 0.0, "core": "#181818"}]},
    {"theme": "instant", "name": "instant-frames", "title": "Instant Frames", "bg": "warm-charcoal",
     "context": "Graphic of three tilted cream instant-photo frames, each holding a simple rainbow-coloured pattern of arcs, stripes or diagonal bands, on a dark grey background.",
     "layers": [{"motif": "frames", "frames": [
         {"x": 0.29, "y": 0.5, "rot": 7, "size": 0.5, "bg": "#e2dac6", "kind": "rings", "colours": RAINBOW},
         {"x": 0.51, "y": 0.46, "rot": -4, "size": 0.5, "bg": "#2a2a2b", "kind": "stripes", "colours": RAINBOW},
         {"x": 0.73, "y": 0.53, "rot": 4, "size": 0.5, "bg": "#d6cfbd", "kind": "diag", "colours": RAINBOW}]}]},
    {"theme": "instant", "name": "spectrum-swoop", "title": "Spectrum Swoop", "bg": "putty",
     "context": "Graphic of a band of red, orange, yellow, green and blue stripes running in from the left and bending down off the bottom edge, on a beige-grey background, in instant-camera rainbow colours.",
     "layers": [{"motif": "swoop", "colours": RAINBOW, "y": 0.24, "run": 0.42, "turns": [90],
                 "w": 0.042, "gap": 0.18, "hole": 2.5}]},
    {"theme": "instant", "name": "rainbow-sunrise", "title": "Rainbow Sunrise", "bg": "charcoal",
     "context": "Graphic of a half-circle rainbow of red, orange, yellow, green and blue rings rising from the bottom edge of a dark grey background, in instant-camera colours.",
     "layers": [{"motif": "rings", "colours": RAINBOW, "x": 0.5, "y": 1.04, "r0": 0.16, "w": 0.075, "gap": 0.16}]},
    {"theme": "instant", "name": "spectrum-pills", "title": "Spectrum Pills", "bg": "warm-charcoal",
     "context": "Graphic of five rounded bars in red, orange, yellow, green and blue stepping out from the left edge, each longer than the last, on a dark grey background.",
     "layers": [{"motif": "hbands", "colours": RAINBOW, "ends": "pill", "x0": -0.1, "x1": 0.4,
                 "stagger": 0.06, "y": 0.27, "w": 0.07, "gap": 0.32}]},
    {"theme": "instant", "name": "spectrum-stairs", "title": "Spectrum Stairs", "bg": "beige-grey",
     "context": "Graphic of a thin rainbow band of red, orange, yellow, green and blue stripes climbing across a beige-grey background in rounded steps.",
     "layers": [{"motif": "stairs", "colours": RAINBOW, "w": 0.03, "gap": 0.2, "flat": 0.17, "rise": 0.2,
                 "steps": 3, "y": 0.9}]},

    # ------------------------------------------------------------ Videotape
    {"theme": "videotape", "name": "sleeve-sash", "title": "Sleeve Sash", "bg": "charcoal", "scanlines": 0.025,
     "context": "Graphic of a diagonal sash of red, orange, mustard and teal stripes ending in a square cut, on a dark grey background, like the stripe on a 1980s video-cassette sleeve.",
     "layers": [{"motif": "sash", "colours": TAPE, "angle": -32, "x": 0.42, "y": 0.62, "w": 0.05,
                 "gap": 0.2, "end": 0.22, "track": "#1c1c1d", "pad": 0.35}]},
    {"theme": "videotape", "name": "tape-racetrack", "title": "Tape Racetrack", "bg": "graphite", "scanlines": 0.025,
     "mirror": True,
     "context": "Graphic of teal, mustard, orange and red stripes running in from the right and making a rounded U-turn on a dark grey background, in 1980s video-tape colours.",
     "layers": [{"motif": "bend_u", "colours": TAPE[::-1], "w": 0.05, "gap": 0.22, "track": "#1f2022",
                 "x": 0.55, "y": 0.5, "hole": 1.4}]},
    {"theme": "videotape", "name": "e180-earth", "title": "E-180 Earth Tones", "bg": "putty",
     "context": "Graphic of four horizontal bars in brown, rust, orange and tan with stepped ends, on a beige-grey background, in the earth tones of early video-tape labels.",
     "layers": [{"motif": "hbands", "colours": EARTH, "ends": "step", "x0": -0.05, "x1": 0.58,
                 "stagger": 0.05, "y": 0.5, "w": 0.055, "gap": 0.22},
                {"motif": "pinlines", "pins": [(0.42, EARTH[0], 0.006)]}]},
    {"theme": "videotape", "name": "corrugate-chevrons", "title": "Corrugate Chevrons", "bg": "charcoal",
     "scanlines": 0.025,
     "context": "Graphic of red, orange, mustard and teal chevrons pointing right over fine horizontal ribbing on a dark grey background, in video-cassette colours.",
     "layers": [{"motif": "ribs", "pitch": 0.014, "duty": 0.4, "ribf": 0.86},
                {"motif": "chevrons_left", "colours": TAPE, "tip": 0.66, "y": 0.5, "hw": 0.15, "t": 0.045,
                 "gap": 0.009}]},
    {"theme": "videotape", "name": "blue-line", "title": "Blue Line", "bg": "graphite", "scanlines": 0.025,
     "context": "Graphic of cream, sky-blue and red stripes curving down and back across a dark grey background in an S-bend, in the colours of a 1980s video-tape label.",
     "layers": [{"motif": "swoop", "colours": BLUELINE, "y": 0.22, "run": 0.3, "turns": [90, -90],
                 "leg": 0.22, "w": 0.05, "gap": 0.2, "hole": 2.0, "track": "#202124", "pad": 0.3}]},
    {"theme": "videotape", "name": "test-card-bars", "title": "Test Card Bars", "bg": "charcoal", "scanlines": 0.03,
     "context": "Graphic of muted television test-card colour bars in grey, yellow, cyan, green, magenta, red and blue, with short reversed bars and a grey step scale below, on a dark grey background.",
     "layers": [{"motif": "colour_bars",
                 "colours": ["#bdbab2", "#c9b542", "#4a9aa0", "#5a9a4c", "#a24d86", "#b2463d", "#3c5a9e"],
                 "lower": ["#3c5a9e", "#1e1e1f", "#a24d86", "#1e1e1f", "#4a9aa0", "#1e1e1f", "#bdbab2"],
                 "scale": ["#e3ded2", "#bdbab2", "#8f8c86", "#5e5c58", "#3a3937"]}]},

    # ------------------------------------------------------------ Hi-fi
    {"theme": "hifi", "name": "stereo-bands", "title": "Stereo Bands", "bg": "graphite",
     "context": "Graphic of wide silver and steel-blue bands with a thin orange line over dark ribbing on a dark grey background, in the colours of a late-1970s personal cassette player.",
     "layers": [{"motif": "hbands", "colours": [STEREO_SILVER], "y": 0.3, "w": 0.13},
                {"motif": "hbands", "colours": [STEREO_STEEL], "y": 0.445, "w": 0.13},
                {"motif": "hbands", "colours": [STEREO_ORANGE], "y": 0.6, "w": 0.012},
                {"motif": "ribs", "ry0": 0.66, "ry1": 1.05, "pitch": 0.02, "duty": 0.4, "ribf": 0.8}]},
    {"theme": "hifi", "name": "aperture-grille", "title": "Aperture Grille", "bg": "#2f3033",
     "context": "Graphic of a block of fine vertical red, green and blue stripes beside two silver rules, on a dark grey background, after the aperture grille of an old colour television tube.",
     "layers": [{"motif": "aperture", "colours": GRILLE, "ax0": 0.6, "pitch": 0.011, "rule": STEREO_SILVER}]},
    {"theme": "hifi", "name": "sports-yellow", "title": "Sports Yellow", "bg": "slate",
     "context": "Graphic of a diagonal band of yellow, pale grey and mid grey stripes on a dark outline across a dark grey background, in the colours of a 1980s waterproof cassette player.",
     "layers": [{"motif": "sash", "colours": [SPORTS_YELLOW, "#a9a8a4", "#6a6a6c"], "angle": -20, "x": 0.5,
                 "y": 0.5, "w": 0.075, "gap": 0.18, "track": "#1c1c1d", "pad": 0.3}]},
    {"theme": "hifi", "name": "grey-console-stairs", "title": "Grey Console Stairs", "bg": "console-grey",
     "context": "Graphic of a band of red, yellow, green and blue stripes climbing in rounded steps across a light grey background, in the colours of a mid-1990s grey games console.",
     "layers": [{"motif": "stairs", "colours": CONSOLE, "w": 0.036, "gap": 0.22, "flat": 0.2, "rise": 0.22,
                 "steps": 2, "y": 0.82}]},
    {"theme": "hifi", "name": "pastel-rings", "title": "Pastel Rings", "bg": "#c9c6bf",
     "context": "Graphic of concentric arcs in soft green, red, blue and pink spreading from the top-right corner of a light grey background, in the button colours of a mid-1990s games controller.",
     "layers": [{"motif": "rings", "colours": CONSOLE_PASTEL, "x": 1.0, "y": 0.0, "r0": 0.22, "w": 0.08, "gap": 0.2}]},
    {"theme": "hifi", "name": "primary-chevrons", "title": "Primary Chevrons", "bg": "charcoal",
     "context": "Graphic of a column of chunky red, yellow and blue chevrons on a dark grey background, in the primary colours of 1980s children's electronics.",
     "layers": [{"motif": "chevrons", "colours": PRIMARY, "x": 0.24, "top": 0.16, "hw": 0.09, "t": 0.11,
                 "gap": 0.018}]},
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--size", default="3840x2160")
    ap.add_argument("--quality", type=int, default=90)
    a = ap.parse_args()
    size = tuple(int(v) for v in a.size.split("x"))
    meta = {}
    for spec in SPECS:
        if a.only and spec["name"] not in a.only and spec["theme"] not in a.only:
            continue
        folder = os.path.join(a.out, THEMES[spec["theme"]][0])
        os.makedirs(folder, exist_ok=True)
        fn = spec["name"] + ".jpg"
        render(spec, size).save(os.path.join(folder, fn), quality=a.quality, optimize=True,
                                subsampling=0)
        meta.setdefault(spec["theme"], {})[fn] = {
            "title": spec["title"], "place": "",
            "credit": "Matchy retro themes · " + THEMES[spec["theme"]][1],
            "context": spec["context"],
            "source": "generated by retro_themes.py", "licence": "original artwork",
        }
        print(spec["theme"], fn, flush=True)
    for theme, entries in meta.items():
        path = os.path.join(a.out, THEMES[theme][0], "meta.json")
        old = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        old.update(entries)
        with open(path, "w", encoding="utf-8") as f:  # the engine reads it as UTF-8 whatever the locale
            json.dump(old, f, indent=1, ensure_ascii=False)
            f.write("\n")


if __name__ == "__main__":
    main()
