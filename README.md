# Matchy

**One wallpaper, matched across every screen.**

Matchy picks one picture for the whole house and gives every screen that same picture, rendered to
its own shape and size: a spanned composite across a multi-monitor desktop, a portrait crop on a wall
tablet, a dashboard background in a browser. It moves on every 15 to 20 minutes. Each picture carries
an art-gallery label baked into the corner: title, credit, and a short blurb written by a vision
model you point it at, grounded on the metadata you already have so it cannot invent a place it is
not looking at.

It is one small Python service (standard library HTTP server plus Pillow). No account, no cloud, no
telemetry: it reads your folders and serves your pictures on your own network.

## How it works

1. **Scan**: it walks your image folders, hashes what it finds and de-duplicates, keeping one copy of
   anything that appears more than once and the 4k step of a `-2k`/`-4k` ladder.
2. **Caption**: a vision model writes a 30 to 45 word blurb per image, once, from the image plus
   whatever title, place and credit you already have. The result is kept beside the image; a blurb
   that names something not in the known facts is rejected and asked for again.
3. **Render**: one master copy per image, then crops and composites on demand with Pillow, clean or
   labelled, at any size a client asks for, cached and evicted least-recently-used.
4. **Rotate**: a new picture every 15 to 20 minutes, with the current one exposed as `current.json`.
5. **Serve**: plain HTTP. Every client asks for the size it wants and gets a JPEG.

## Install

### Home Assistant add-on

Add this repository under **Settings → Add-ons → Add-on store → ⋮ → Repositories** (newer releases
say *Apps*), install **Matchy Wallpapers**, point `source_folders` at a folder under `/media` or
`/share`, and start it. The sidebar panel shows the gallery and the controls. Publish the add-on's
port in its Network section when other screens should follow the picture.

Full option reference, API, access tiers and troubleshooting: [matchy/DOCS.md](matchy/DOCS.md).

### Docker, without Home Assistant

The same image runs anywhere Docker does; you start the engine directly with a `config.json` instead
of the add-on's start script. The recipe and the config format are in
[matchy/DOCS.md](matchy/DOCS.md#running-it-without-home-assistant). No pre-built image is published;
build it from this repository (`docker build -t matchy ./matchy`).

## Clients

Each of these is a short recipe, not a separate program, under [docs/clients/](docs/clients/):

- **GNOME desktop**: a user service that follows `current.json` and sets a spanned wallpaper matched
  to your live monitor layout, label on the primary screen. [docs/clients/gnome.md](docs/clients/gnome.md)
- **Home Assistant dashboards and tablets**: a package with the sensors, a card-mod view background
  that follows the rotation, and a Wallpanel screensaver with the label.
  [docs/clients/home-assistant.md](docs/clients/home-assistant.md)
- **Homepage dashboard**: a background that follows the rotation, plus a tile.
  [docs/clients/homepage.md](docs/clients/homepage.md)
- **Anything else**: `curl` examples for the API. [docs/clients/curl.md](docs/clients/curl.md)
- **Any browser**: the gallery page itself, with next, pause and show-now controls. No setup.

## Requirements

- Home Assistant OS, or any machine that runs Docker (amd64 or arm64; a Raspberry Pi 5 is plenty).
- Images of your own, somewhere it can read them.
- Optional: an OpenAI-compatible vision endpoint for the captions (llama.cpp, Ollama, LM Studio or a
  cloud API). Without one, Matchy still rotates; the labels carry the title and credit only.

## Status

**Early.** It runs every day in one house, as a Home Assistant add-on on amd64, and the image builds
and renders identically on aarch64. The HTTP API may still change. Nothing is published yet: no
registry image, no add-on store listing; the add-on builds locally from this repository.

## Limitations

- The published port has no authentication. Anyone who can reach it sees every picture and caption;
  the controls that change the library work only from the sidebar panel. Keep it on your own network.
- Images must live under `/media` or `/share` when running as an add-on.
- Formats: JPEG, PNG and WebP, up to 50 megapixels.
- The captioner must accept `response_format: {"type": "json_object"}`; one that rejects it is
  treated as unreachable.
- One rotation for every screen: no per-screen pictures, no quiet hours yet.
- The engine runs as root inside its container, as Home Assistant add-ons do.

## Development

- `tests/run_tests.py`: integration tests against the real engine (rotation, panel controls, access
  tiers, request bodies, oversized images). `uv run --with pillow python tests/run_tests.py`, or any
  Python 3 with Pillow; about a minute.
- `tests/label_diff.py`: compares two rendered labels and reports JPEG noise against real layout
  differences; `tests/ci_probe.py` renders one inside a built image.
- `.github/workflows/build.yml`: lint, tests, an amd64 and an aarch64 image build with a per-arch
  render probe, and a cross-arch label diff. Publishing is opt-in and off.
- Open decisions (licence, name and artwork, container user, public repository):
  [docs/DECISIONS.md](docs/DECISIONS.md).

## Licence

Code: see [LICENSE](LICENSE) (MIT, pending confirmation; see `docs/DECISIONS.md`). The bundled fonts
are Noto, under the SIL Open Font Licence 1.1, and their licence travels with the image. No pictures
ship with Matchy; the ones you point it at stay yours, and their licences are your business.
