# Matchy Wallpapers

**One wallpaper, matched across every screen.**

## What it does

Matchy picks one picture for the whole house and serves that same picture to every screen, rendered to
each screen's own shape and size. It moves to the next picture every 15 to 20 minutes by default; the
panel's **How often** slider goes from every 15 minutes to every 12 hours, or sunrise and sunset.
Each picture gets an art-gallery label baked into the bottom-right corner: title, credit and a short
blurb written by a vision model you point it at. The blurb is grounded on the metadata the picture
already carries: a blurb that names something (a capitalised word) that is not in that metadata is
rejected and asked for again.

## Installation

It runs on Home Assistant OS or a Supervised install on **amd64** or **aarch64**. A Raspberry Pi 4 or 5
needs the 64-bit Home Assistant OS; 32-bit (armv7) systems cannot install it.

1. In Home Assistant, go to **Settings → Add-ons → Add-on store** (newer releases call them *Apps*),
   open the three-dot menu, choose **Repositories** and add
   `https://github.com/adrutu/matchy-wallpapers`.
2. Install **Matchy Wallpapers** from the store. No pre-built image is published, so Home Assistant
   builds it on your machine: about a minute on a PC, several on a Raspberry Pi, and again on each
   update. A failed build is reported under **Settings → System → Logs → Supervisor**, not in the
   add-on's own log.
3. Create the picture folder **before the first start**, for example `/media/wallpapers` (the default
   in `source_folders`): upload pictures under **Media → My media**, copy them onto the `media` Samba
   share, or use an SSH add-on. If none of the folders in `source_folders` exists, and no remote or
   daily source is set, the add-on refuses to start and its log says why.
4. Start the add-on. Turn on **Show in sidebar** on its page for a **Matchy Wallpapers** entry in the
   sidebar (or use **Open Web UI**), and **Watchdog** if Home Assistant should restart it when its
   health check fails.
5. Optional: a captioner for the blurbs (below), and the port for screens other than the panel.

## Configuration

The container always listens on port **8160**. If you want clients other than the sidebar panel to
reach it (desktops, tablets, a dashboard background), type a host port (8160 is fine) in the add-on's
**Network** section, save and restart. Clients then use `http://<your Home Assistant address>:<that
port>`. Leave it empty and only the sidebar panel works.

| Option | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `source_folders` | list of paths | `["/media/wallpapers"]` | Folders of images, scanned recursively. Must be under `/media` or `/share`; an add-on cannot read arbitrary host paths. A folder that does not exist is logged and skipped; if none exists, the add-on does not start. |
| `exclude` | list of patterns | `[]` | Picture ids to leave out, as shell-style globs (`aether-*`, `art-great-wave`). Matching pictures never enter the library, so they are not shown, captioned or rendered. Ids are listed in the panel and in `library.json`. |
| `sets` | list | `[]` | Settings for a sub-folder, matched by name in every source folder: `folder` (required), `id_prefix` (ids become `<prefix>-<file name>`; lower-case letters, digits and hyphens, anything else becomes a hyphen), `name` (the collection name the captioner is told), `credit` (shown on the label), `context` (one sentence of facts), `abstract` (true for digital or illustrated art, so the captioner describes it as art, never as a real place). |
| `rotation_min_minutes` | int, 1–1440 | 15 | Shortest time on one image. Keep it at or below the longest. |
| `rotation_max_minutes` | int, 1–1440 | 20 | Longest time on one image. The actual interval is drawn at random between the two. |
| `folder_scan_minutes` | int, 1–1440 | 10 | How often it looks for new, changed or removed files. |
| `master_max_px` | int, 1280–7680 | 3840 | Longest edge of the cached master copy every render is cut from. |
| `cache_max_mb` | int, 128–65536 | 2048 | Render cache size. Evicted least-recently-used down to 85 % when it overflows. |
| `render_threads` | int, 1–8 | 2 | Renders running at the same time. |
| `caption_enabled` | bool | true | Write gallery blurbs at all. Off: labels carry the title, place and credit only (from `meta.json` or `sets`). |
| `caption_language` | string | English | Language the blurb is asked for. The grounding check rejects capitalised words that are not in the metadata, so a language that capitalises every noun (German) gets most blurbs rejected. |
| `caption_spelling` | `British` or `American` | British | British also rewrites common American spellings in what comes back (colour, grey, centre…). |
| `caption_min_words` | int, 5–200 | 30 | Shortest blurb asked for. Keep it at or below the longest. |
| `caption_max_words` | int, 5–200 | 45 | Longest blurb asked for. After a second try, a blurb within three words of the range is kept. |
| `captioners` | list of objects | `[]` | OpenAI-compatible vision endpoints, tried in order. Fields: `name` (required), `url` (required, the full `/v1/chat/completions` URL), `model` (required), `api_key` (optional), `timeout` (seconds, 5–600, default 120). |
| `palette_enabled` | bool | true | Colour match: work out each picture's main colours, an accent and the nearest GNOME accent name (see [Colour match](#colour-match)). Off: `palette` is `[]` and `accent` and `gnome_accent` are `null` everywhere, and nothing is computed. |
| `remote_manifest_url` | URL | unset | Optional. Base URL of a web folder holding a `manifest.json` (`[{"name": "…", "file": "…"}]`) with the image files next to it. Fetched once a day; new files are downloaded into the add-on's data and get ids `remote-<file name>` (lower-cased, without the extension). Only `.jpg`, `.jpeg`, `.png` and `.webp` entries without a `/` are taken. |
| `daily_json_url` | URL | unset | Optional. A JSON document `{date, title or caption, place, copyright}` describing a picture that changes daily. Checked hourly; each new date is stored as `daily-YYYYMMDD`. Needs `daily_image_url` too. |
| `daily_image_url` | URL | unset | Optional. The image URL that goes with `daily_json_url`. |

Only options you set are written to the engine's config; everything else keeps the defaults above.

### Setting up a captioner

Any server that speaks the OpenAI chat-completions API with an `image_url` content part will do.
Matchy sends one request per image, `stream: false`, with `response_format: {"type": "json_object"}`,
and expects `{"title": "...", "blurb": "..."}` back (a JSON object inside a code fence, after an inline
`<think>` block, or in content parts is accepted too).

- **llama.cpp** (`llama-server` with a vision model and its mmproj):
  `url: http://<llama-host>:8080/v1/chat/completions`, `model` as the server names it.
- **Ollama**: `url: http://<ollama-host>:11434/v1/chat/completions`, `model` as in `ollama list`
  (a vision model, e.g. a Qwen-VL or LLaVA family model).
- **LM Studio**: `url: http://<lm-studio-host>:1234/v1/chat/completions`, `model` as shown in its
  server tab.
- **A cloud API**: the provider's `/v1/chat/completions` URL, its model name, and the key in `api_key`.

Use the server's address on your network, never `localhost`: inside the add-on, `localhost` is the
add-on itself. In YAML (the options page, **⋮ → Edit in YAML**):

```yaml
captioners:
  - name: desktop
    url: http://<llm-host>:8080/v1/chat/completions
    model: <vision model>
  - name: cloud
    url: https://<provider>/v1/chat/completions
    model: <vision model>
    api_key: <key>
```

List two endpoints if you have a fast one that is not always awake and a slower one that is: Matchy
tries them in order, and an endpoint that refuses or times out is skipped for ten minutes. Matchy
never wakes a sleeping machine itself.

Servers differ in what they accept, so a `400` or `422` that names a parameter is handled once per
endpoint and logged as `retrying with …`: `response_format` is left out (LM Studio; the prompt still
asks for JSON), `max_tokens` becomes `max_completion_tokens`, and `temperature` is left at the
server's default (newer OpenAI models). A `400` that names none of them is blamed on
`response_format`.

A local 8-billion-parameter vision model takes a couple of seconds per picture on a desktop GPU.

## First run

The first start does real work:

1. The port opens at once. Meanwhile it hashes every image in your source folders: tens of seconds
   for a few hundred images, longer on slow storage or a Raspberry Pi. Until that lands,
   `/current.json` answers `503 library empty` and the panel says *Nothing yet*. Hashing progress is
   saved every ten seconds, so a restart mid-scan picks up where it left off.
2. The first wallpaper appears the moment the scan finishes. It then builds a master copy of each
   image in the background, one to three seconds each.
3. `/current.json` says `captioned: false` until the first blurb is written. Captioning runs one
   image at a time. As soon as any picture has a caption, an uncaptioned picture on screen gives way
   to a captioned one (unless you picked it by hand or paused on it), and from then on only
   captioned pictures rotate.

So: usable almost immediately, fully captioned after a while.

### Which files count

- Formats: `.jpg`, `.jpeg`, `.png`, `.webp`. Files under 10 KB, and files or folders whose name starts
  with a dot, are ignored.
- Identical files (same bytes) collapse to one. A `name-2k.jpg` / `name-4k.jpg` ladder keeps the 4k.
- The picture's id comes from its file name: `great-wave.jpg` in `/media/wallpapers/art/` becomes
  `art-great-wave`. The first sub-folder under a source folder becomes the collection name the
  captioner is told about. Inside a sub-folder, a file name that starts with a number becomes
  `<folder>-<number>` and the rest of the name is dropped, so give files real names.
- Anything over 50 megapixels is refused (see Troubleshooting).
- A `meta.json` next to the pictures gives the captioner facts it may state, per file:
  `{"great-wave.jpg": {"title": "…", "place": "…", "credit": "…", "context": "…"}}`. A known `title`
  is used as is, `place` and `credit` go on the label, and `context` (one sentence on what the
  picture shows) grounds the blurb. Other keys are ignored, so you can keep provenance notes there.
  Without it the captioner describes only what it sees, and never names a place.

## Using it

### The sidebar panel

Open **Matchy Wallpapers** from the sidebar (or **Open Web UI** on the add-on's page). This route is
authenticated by Home Assistant. The
page reloads itself when the picture changes (it checks every 30 seconds). From there you can:

- **Next picture**, **Pause rotation** / **Resume rotation**. Paused holds whatever is on screen;
  Next and Show now still work while paused, and Resume starts a full interval from then.
- **Show now** on any picture in the library.
- **Hide** a picture so it never comes up in rotation, and **Unhide** it later. Hiding the picture on
  screen moves on at once. The last visible picture cannot be hidden.
- **Edit caption** by hand (title, place, credit, blurb; limits 100/120/200/600 characters). A
  hand-written caption is never overwritten by the captioner.
- **Re-caption** a picture: it jumps the captioning queue and replaces even a hand-written caption.
  If the captioner fails, the old caption stays.
- **How often**: a slider from every 15 minutes to every 12 hours, plus **Day & night**. It saves
  when you let go. The shortest/longest window is still there under **Custom window**. A choice made
  here lasts until you change the rotation options in the add-on configuration, so whichever was set
  last wins; **Use the options** goes back to them (in Day & night, pick a stop on the slider instead).
- **Screens**: every screen that has named itself (see below) and whether it follows Matchy.
  **Turn off** / **Turn on** one screen, or **Forget** it. When and from where each was last seen is in
  `/screens.json`.
- See whether each captioner is ready or being skipped, what it is writing now and how many pictures
  are waiting.
- Under the picture on screen, its colours: a swatch per palette colour and the accent, with the
  GNOME accent name (see Colour match).

Every thumbnail opens the labelled picture at 2560x1440.

### Other screens

Clients fetch images directly from `http://<your-ha-host>:<published port>`. Each client asks for
the size it wants and gets a JPEG; nothing is pre-rendered. Recipes for a GNOME desktop, a Homepage
dashboard, Home Assistant tablets and plain `curl` are in the repository:
<https://github.com/adrutu/matchy-wallpapers/tree/main/docs/clients>.

### Day & night

**Day & night** puts one picture up at sunrise and the next at sunset. The add-on reads Home
Assistant's latitude and longitude once at start (that is what its Home Assistant API access,
`homeassistant_api`, is for; nothing else uses it). Without a location, or on a day the sun does not
rise or does not set there, it changes at 07:00 and 19:00 local time instead. Restart the add-on after
moving the home location.

### Colour match

Matchy works out each picture's colours once, from its master copy, and serves them with the picture
so the room can follow it: a desktop's accent colour, a dashboard theme, the lights. Three fields, in
`/current.json` and per picture in `/library.json`:

| Field | What |
| :--- | :--- |
| `palette` | Up to five hex colours, most prominent first. Near-black, near-white and grey are left out unless the picture is mostly that (a black-and-white or a night picture keeps its greys); otherwise they only fill up a palette with fewer than five colours of its own. |
| `accent` | The one colour to use for a UI: the most vivid colour with real presence in the picture, then nudged to where GNOME's own accents sit, at least 3:1 contrast against both white and black (so it reads on a light and a dark theme) and saturated enough to look like a colour. It is a usable tint of a palette colour, not always a colour in the palette. `null` for a picture with no colour in it. |
| `gnome_accent` | The nearest of GNOME's accent colours by hue: `blue`, `teal`, `green`, `yellow`, `orange`, `red`, `pink` or `purple`, and `slate` for grey or for a picture with no accent. Always a name once the colours are known. |

Until a picture's colours are computed, `palette` is `[]` and the other two `null` (a cold start: the
picture on screen gets its colours first, within seconds; the rest follow the master copies). A
changed source file gets fresh colours on the next folder scan. The colours live in the picture's
sidecar (`colours`), next to the caption; a hand-written caption keeps them, and a re-caption does not
touch them. They take a few milliseconds per picture.

Clients: the GNOME follower can set the desktop's accent colour from `gnome_accent` (off by default),
and the Home Assistant recipe has a theme accent and a lights automation that only tints lights
already on. See the client recipes in the repository.

### Screens

Any client may add `?screen=<name>` to any `GET` (`/current.json`, `/current/...`, `/img/...`). The name
is 1 to 40 characters of letters (in any script), digits, space and `. _ : @ -`, starting with a letter
or digit; a request with any other name is served as usual but not listed. Only clients on your own
network (the Network tier below) are listed. The panel's **Screens** box then shows that screen. Up to
24 are kept: a new one beyond that replaces the following screen unseen the longest, and screens you
turned off stay (so with 24 screens all turned off, a new one is not added).

**Turn off** is advisory: Matchy keeps serving that screen. `/current.json?screen=<name>` answers
`"follow": false`, and every `/current.json` lists the screens that are off in `"screens_off"`. A client
that honours it goes back to its own wallpaper, and follows again when it is turned back on.
`GET /screens.json` lists every screen with `id`, `follow`, `first_seen`,
`last_seen`, `age_s`, `ip`, `agent` and `image` (the last picture it fetched).

For Home Assistant dashboards, a card-mod theme rule can add `?screen={{ user | urlencode }}` to the
background URL, so each tablet's Home Assistant user becomes a screen, and leave the background out for
a user listed in `screens_off`. A tablet that spends the day in a screensaver fed by Home Assistant
(not by the tablet's browser) shows an old *last seen* while it still follows.

### HTTP API

Read endpoints:

| Endpoint | Description |
| :--- | :--- |
| `GET /current.json` | The current picture: `id`, `rev`, `version`, `title`, `place`, `credit`, `blurb`, `since`, `next_change` (null while paused), `paused`, `captioned`, `palette`, `accent`, `gnome_accent` (see Colour match), and `urls` (`original`, `clean`, `label`, `span` templates with `{w}`, `{h}`, `{layout}` to fill in). `version` changes on every rotation and every caption edit; clients compare it (the colours do not change it: they arrive for a new picture within seconds of a cold start, so a client that uses them reads them on every poll). `screens_off` lists the screens turned off; with `?screen=<name>`, `follow` says whether that one should follow. `503` while the library is empty. |
| `GET /library.json` | Every picture with its status, caption, `hidden` and `recaption_queued` flags and its `palette`, `accent` and `gnome_accent`, plus the rotation (`mode` = `minutes` or `daynight`, the window, and `next_sun` = `{"at", "event"}` in Day & night, `event` being `sunrise`/`sunset`, or `morning`/`evening` without a location) and captioner state. |
| `GET /screens.json` | The Screens list (see Screens). Network tier only, and without CORS, because it carries addresses. |
| `GET /` (also `/index.html`) | The gallery page. |
| `GET /current/clean/WxH.jpg` | The current picture at any size, without the label. Sends an `ETag`; `If-None-Match` gets a `304` until the picture changes. |
| `GET /current/label/WxH.jpg[?s=]` | The same with the label. `s` scales the label, 0.6–2.0 (wall tablets look right at about 1.35). |
| `GET /img/<id>/<rev>/clean/WxH.jpg` | One specific picture; cacheable for a year. |
| `GET /img/<id>/<rev>/label/WxH.jpg[?s=]` | The same with the label. |
| `GET /img/<id>/<rev>/span.jpg?layout=WxH+X+Y[p],...[&s=]` | One picture composed across a multi-monitor layout: each monitor as `width x height + x + y`, `p` marks the primary, which gets the label (the largest screen if none is marked). Gaps are black. |
| `GET /img/<id>/original.jpg` | The original: PNG and WebP pictures from your folders are re-encoded as JPEG; pictures downloaded from a remote manifest or the daily URL are served as downloaded. |
| `GET /healthz` | Liveness; answers `ok` even before the first scan. |

Sizes: width and height 320–7680 (outside that, `/img/...` answers `400` and `/current/...` `404`); a
span canvas up to 12000x8000 with at most 8 monitors and one primary. A `<rev>` that is no longer current (the caption was edited) answers `302` to the current
URL. `HEAD` works everywhere `GET` does. Every read response carries
`Access-Control-Allow-Origin: *`, except the gallery page and `/screens.json`, which name the screens (and the latter their addresses).

Control endpoints are `POST`s with a JSON object body and **must** be sent with
`Content-Type: application/json` (the one exception is `/api/next`, which ignores the body and its
content type, for older clients). They answer `{"ok": true, "current": {...}}`
(`/api/next` answers the new `current.json`), or `403` (wrong tier, see Access tiers), `415` (not
JSON), `400` (bad arguments), `404` (unknown id), `409` (refused: hiding the last picture, showing an
undecodable one, re-captioning with no captioner) or `413` (body over 64 KB, or chunked).

| Endpoint | Body | Tier | Description |
| :--- | :--- | :--- | :--- |
| `POST /api/next` | `{}` | Network | Rotate now. |
| `POST /api/pause` | `{}` | Network | Hold the picture on screen. |
| `POST /api/resume` | `{}` | Network | Rotate again, a full interval from now. |
| `POST /api/show` | `{"id": "<id>"}` | Network | Put this picture on screen now. |
| `POST /api/hide` | `{"id": "<id>"}` | Panel | Keep this picture out of rotation. |
| `POST /api/unhide` | `{"id": "<id>"}` | Panel | Put it back (an id that is not hidden changes nothing). |
| `POST /api/caption` | `{"id", "title", "place", "credit", "blurb"}` | Panel | Write the caption by hand; `blurb` is required, the others may be empty. |
| `POST /api/recaption` | `{"id": "<id>"}` | Panel | Ask the captioner for a fresh caption. |
| `POST /api/rotation` | `{"min_minutes": 10, "max_minutes": 12}`, `{"mode": "daynight"}` or `{"reset": true}` | Panel | Set the rotation window (whole minutes, 1–1440), switch to Day & night, or go back to the options. |
| `POST /api/screen` | `{"id": "<name>", "follow": false}` | Network | Turn one screen off (`true`: on again). `404` for a screen Matchy has not seen. |
| `POST /api/screen/forget` | `{"id": "<name>"}` | Panel | Drop a screen from the list until it asks again. |

For example, a Home Assistant `rest_command` that pauses the rotation:

```yaml
rest_command:
  matchy_pause:
    url: "http://<your-ha-host>:<published port>/api/pause"
    method: post
    content_type: "application/json"
    payload: "{}"
```

## Access tiers

Who may call what depends only on where the request comes from:

| Tier | Who gets it | What it covers |
| :--- | :--- | :--- |
| **Public** | anything that can reach the port | every `GET` except `/screens.json`: pictures, captions, the library, the gallery page |
| **Network** | the sidebar panel, or a client whose address is private (`10/8`, `172.16/12`, `192.168/16`, `100.64/10`, loopback, IPv6 ULA and link-local); if the request carries `X-Forwarded-For`, every address in it must be private too | next, pause, resume, show now, turn a screen on or off |
| **Panel** | the sidebar panel only: a request proxied by the Supervisor (its address `172.30.32.2`), which only forwards sessions Home Assistant has authenticated | hide, unhide, edit caption, re-caption, how often, forget a screen |

The gallery page draws the buttons a viewer may not use as disabled, with the reason as a tooltip.

## Security

- The **sidebar panel is authenticated** by Home Assistant.
- The **published port is not authenticated**. Anything on your network that can reach it can read
  every picture and every caption. That is deliberate: it is what lets tablets, desktops and
  dashboards show the wallpaper without credentials. Do not expose that port to the internet.
- The **Panel** tier exists because the published port has no login: everything that changes what the
  library holds or what the labels say works only through the sidebar.
- The JSON control endpoints are not reachable from another website open in a browser on your
  network: a cross-site JSON `POST` is preflighted, and the add-on answers the preflight on `/api/`
  without CORS headers, so the browser refuses it. Known gaps: a page could still issue a simple
  `POST /api/next` (no preflight needed), a page using DNS rebinding could reach the Network tier,
  and a page can add made-up names to the Screens list (an image tag with `?screen=`), which can push
  real screens out of a full list until they next ask. The worst outcome is a skipped or paused
  wallpaper or a cluttered Screens list, never a changed caption or library.
- An API key you enter for a captioner is written to a file inside the add-on's own data directory
  at mode 600 and re-written on every start. It never appears in the generated config file or in
  the log. Like every add-on option it is also in Home Assistant's own `options.json` for the add-on.
- The engine runs as **root inside its container**, as official add-ons do; the Supervisor offers no
  option to change that, and Matchy does not drop privileges itself. `/media` and `/share` are
  mounted read-only.

## Where things live

Everything the add-on writes is under its data directory, which Home Assistant backs up with the
add-on (`backup: hot`, the render cache excluded):

| Path | What |
| :--- | :--- |
| `config.json` | Generated from the options on every start. |
| `secrets/captioner-<n>.key` | Captioner keys, mode 600. |
| `state.json` | What is on screen, the shuffle bag, hidden ids, the panel's rotation choice, history. |
| `screens.json` | The Screens list: on or off, first and last seen (last seen is written at most every 5 minutes). |
| `library/<id>.json` | One sidecar per picture: the caption and where it came from, and the picture's colours (`colours`: `palette`, `accent`, `gnome_accent`, the source hash and the method version they were computed with). A hand edit here (set `"edited": true`) is picked up within a minute; the colours are recomputed if you delete them. |
| `masters/` | The downscaled master copy of each picture. |
| `originals/`, `remote.json` | Pictures fetched from a remote manifest or the daily URL, and their index. |
| `cache/` | Rendered JPEGs, evicted LRU. Safe to delete; not in backups. |
| `hash-cache.json` | File hashes, so a restart does not re-hash unchanged files. |

## Troubleshooting

**No images found.** `source_folders` must point at paths under `/media` or `/share` that exist inside
the add-on. Check the log: a missing folder is named there and skipped, and with no existing folder at
all the add-on stops with `No image source`. The panel says *Nothing yet* until the first scan
finishes.

**Captions never appear.** Check `caption_enabled`, then the captioner `url`: it must be the full
`/v1/chat/completions` path, not just the host. The log says `failed (…); skipping it for 10 min`
when an endpoint refuses, times out or answers an HTTP error (with the server's own reason, e.g.
`HTTP 404: model not found`), and `postponed` while no endpoint is available; captioning resumes
by itself when the skip runs out, and the wallpaper keeps rotating uncaptioned meanwhile. `empty
reply (finish_reason: length)` means a reasoning model spent its token budget thinking: pick a
non-thinking model (the budget, `caption.max_tokens`, can only be raised when running without Home
Assistant). A blurb that breaks the word-count or grounding rules is retried once immediately with the
reason, then that picture is retried six hours later; the panel card shows the last error.

**The same image never changes.** Check how many images it actually found: the panel's library count,
or the `library: N items` line in the log. One usable image means no rotation. Pictures that are
hidden or over the size cap do not count. If the count is right, the rotation window may simply be
long; `POST /api/next` moves it on immediately.

**A picture never appears, and its card says "image too large".** Matchy refuses to decode any source
over 50 megapixels (8K is 33), because the add-on has no memory limit and one huge file could exhaust
the host's memory. Scale that file down; it is picked up again on the next folder scan.

**Images look soft.** `master_max_px` caps the longest edge of the master copy at 3840 by default.
Raise it if your screens are bigger than 4K, and remember a bigger master costs memory per render
(three masters are held in memory at a time).

**A client shows an old picture.** Clients that use `/current/...` URLs must not cache them beyond
the `ETag`; clients that use `/img/<id>/<rev>/...` URLs must re-read `current.json` to learn the new
id. The sidebar panel does both for you.

**Slow first start.** Expected: see *First run*. The panel answers straight away and fills in when
the scan finishes.

**Where the log is.** The add-on's **Log** tab in Home Assistant. Rotations, renders, captions and
every refused control request are logged there; HTTP access is not, unless the engine is started
with `WE_ACCESS_LOG=1`.

## Running it without Home Assistant

The image is a normal Docker image, but its start script (`/run.sh`) talks to the Home Assistant
Supervisor to read the options, so outside Home Assistant start the engine directly and give it a
config file:

```bash
docker build -t matchy ./matchy
docker run -d --name matchy -p 8160:8160 \
  -v /path/to/config.json:/config/config.json:ro \
  -v matchy-data:/data \
  -v /path/to/wallpapers:/sources/wallpapers:ro \
  --entrypoint python3 matchy /app/engine.py
```

`config.json` is deep-merged over the engine's defaults. This is the shape of what the add-on
generates, with its defaults:

```json
{
  "port": 8160,
  "data_dir": "/data",
  "master_max_px": 3840,
  "cache_max_mb": 2048,
  "render_threads": 2,
  "folder_scan_minutes": 10,
  "rotation": {"min_minutes": 15, "max_minutes": 20},
  "location": {"latitude": 53.35, "longitude": -6.26},
  "folders": [{"path": "/sources/wallpapers",
               "sets": {"art": {"id_prefix": "art", "name": "Classic Art", "abstract": false}}}],
  "exclude": [],
  "captioners": [
    {"name": "local", "url": "http://<host>:8080/v1/chat/completions", "model": "<model>",
     "api_key": "", "timeout": 120}
  ],
  "caption": {"enabled": true, "language": "English", "spelling": "British",
              "min_words": 30, "max_words": 45},
  "palette": {"enabled": true},
  "static_manifest": {"enabled": false},
  "daily": {"enabled": false}
}
```

The add-on writes a captioner's key to a file and passes `api_key_file` instead of `api_key`; either
works here. Leave `location` out and Day & night changes at 07:00 and 19:00. Times are local to the
container, so set `TZ` (for example `-e TZ=Europe/London`) or they are UTC.

Keys the engine understands but the add-on does not expose:

- per folder: `credit`, `meta_file` (default `meta.json`), and `title` in a `sets` entry;
- per id: `overrides` (`{"<id>": {"place": "…"}}`);
- per captioner: `api_key_file`, `down_minutes` (10, how long a failing endpoint is skipped),
  `gap_seconds` (0, a pause after each caption) and `json_mode` (true; false never sends
  `response_format`);
- under `caption`: `max_tokens` (300), `temperature` (0.4), `image_px` (1024, the size sent to the
  model), `retry_failed_hours` (6) and `forbidden_titles`;
- `rotation.caption_priority`, `dedupe.avoid_prefixes` and `ladder_prefer` (`"4k"`), `lan_cidrs`,
  `ingress_peers`, `size_limits`, `bind` and `style` (label proportions, fonts, `jpeg_quality`).

Environment: `WE_CONFIG`, `WE_PORT`, `WE_DATA_DIR`, `WE_FONT_DIR`, `WE_ACCESS_LOG`.

Without Home Assistant there is no sidebar panel, so the **Panel** tier (hide, unhide, edit caption,
re-caption, how often, forget a screen) answers `403` and the gallery page draws those buttons
disabled. To use them from a trusted machine, list its address in `ingress_peers`; that machine then
counts as the panel, so give it only to one you would hand the controls to.

## Licensing and credits

No images ship with this add-on. Images you point it at stay yours, and their licences are your
responsibility. The bundled fonts are Noto, under the SIL Open Font Licence 1.1; their copyright file
travels with the image at `/fonts/COPYRIGHT-fonts-noto-core`. The code licence is in the repository's
`LICENSE` file.
