# Matchy

**One wallpaper, matched across every screen.**

Matchy picks one picture for the whole house and gives every screen that same picture, rendered to
its own shape and size: a spanned composite across a multi-monitor desktop, a portrait crop on a wall
tablet, a dashboard background in a browser. It moves on every 15 to 20 minutes. Each picture carries
an art-gallery label baked into the corner — title, credit, and a short blurb written by a vision
model you point it at, grounded on the metadata you already have so it cannot invent a place it is
not looking at.

It is one small Python service. No account, no cloud, no telemetry: it reads your folders and serves
your pictures on your own network.

## What it looks like

![The gallery label on a wallpaper](docs/screenshot-label.png)

<!-- TODO: screenshot of a rendered label — frosted glass panel, italic serif title, small-caps
     credit, two-line drop cap. -->

## How it works

1. **Scan** — it walks your image folders, hashes what it finds and de-duplicates, keeping the
   largest copy of anything that appears more than once.
2. **Caption** — a vision model writes a 30-45 word blurb per image, once, from the image plus
   whatever title, place and credit you already have. The result is cached beside the image.
3. **Render** — one master copy per image, then crops and composites on demand with Pillow: clean or
   labelled, at any size a client asks for, cached and evicted least-recently-used.
4. **Rotate** — a new picture every 15 to 20 minutes, with the current one exposed as `current.json`.
5. **Serve** — plain HTTP. Every client asks for the size it wants and gets a JPEG.

## Install

### Home Assistant add-on (recommended)

Add this repository to **Settings → Add-ons → Add-on store → ⋮ → Repositories**, install
**Matchy Wallpapers**, point `source_folders` at a folder under `/media` or `/share`, and start it.
Full option reference: [matchy/DOCS.md](matchy/DOCS.md).

### Docker

For anyone not on Home Assistant OS:

```bash
docker run -d --name matchy \
  -p 8160:8160 \
  -v /path/to/your/config.json:/config/config.json:ro \
  -v matchy-data:/data \
  -v /path/to/your/wallpapers:/sources/wallpapers:ro \
  ghcr.io/adrutu/matchy:latest    # TODO: image not published yet
```

The config file is documented in [matchy/DOCS.md](matchy/DOCS.md); the add-on generates the same file
from its options.

## Clients

Each of these is a short recipe, not a separate program:

- **Linux desktop (GNOME)** — a user service follows `current.json` and sets a spanned wallpaper
  matched to your live monitor layout, label on the primary screen. [docs/linux-gnome.md](docs/linux-gnome.md)
- **Home Assistant dashboards and tablets** — a card-mod view background plus a screensaver recipe.
  [docs/home-assistant.md](docs/home-assistant.md)
- **Homepage dashboard** — a background that follows the rotation. [docs/homepage.md](docs/homepage.md)
- **Any browser** — the gallery page itself, with next, pause and show-now controls. No setup.

## Requirements

- Home Assistant OS, or any machine that runs Docker (amd64 or arm64; a Raspberry Pi 5 is plenty).
- Images of your own, somewhere it can read them.
- Optional: an OpenAI-compatible vision endpoint for the captions — Ollama, llama.cpp, LM Studio or a
  cloud API. Without one, Matchy still rotates; the pictures just carry no blurb.

## Licence

Code is MIT. The bundled fonts are Noto, under the SIL Open Font Licence 1.1, and their licence
travels with the image. No pictures ship with Matchy; the ones you point it at stay yours, and their
licences are your business.

**Status: early.** It runs every day in one house. The HTTP API may still change.
