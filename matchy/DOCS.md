# Matchy Wallpapers

**One wallpaper, matched across every screen.**

## What it does

Matchy picks one picture for the whole house and serves that same picture to every screen, rendered to
each screen's own shape and size. It moves to the next picture every 15 to 20 minutes. Each picture
gets an art-gallery label baked into the bottom-right corner — title, credit and a short blurb written
by a vision model you point it at.

## Installation

1. In Home Assistant, go to **Settings → Add-ons → Add-on store**, open the three-dot menu and choose
   **Repositories**, then add this repository's URL.
2. Install **Matchy Wallpapers** from the store.
3. Put some images under `/media` or `/share` (for example `/media/wallpapers`) and set
   `source_folders` to match.
4. Start the add-on. Open it from the sidebar to see the gallery page.

## Configuration

The container always listens on port 8160. If you want clients other than the sidebar panel to
reach it — desktops, tablets, a dashboard background — publish that port under the add-on's
**Network** section and pick a host port there. Leave it unpublished and only the panel works.

| Option | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `source_folders` | list of paths | none | Folders of images. Must be under `/media` or `/share`, e.g. `/media/wallpapers` — an add-on cannot read arbitrary host paths. |
| `rotation_min_minutes` | int | 15 | Shortest time on one image. |
| `rotation_max_minutes` | int | 20 | Longest time on one image. |
| `folder_scan_minutes` | int | 10 | How often it looks for new files. |
| `master_max_px` | int | 3840 | Longest edge of the cached master copy. |
| `cache_max_mb` | int | 2048 | Render cache size, evicted least-recently-used. |
| `render_threads` | int | 2 | Concurrent renders. |
| `caption_enabled` | bool | true | Write gallery labels at all. |
| `caption_language` | string | English | Language of the blurb. |
| `caption_spelling` | string | British | British or American spelling. |
| `caption_min_words` | int | 30 | Shortest blurb. |
| `caption_max_words` | int | 45 | Longest blurb. |
| `captioners` | list of objects | none | One or more OpenAI-compatible vision endpoints, tried in order. Fields: `name`, `url` (a full `/v1/chat/completions` URL), `model`, `api_key` (optional), `timeout` (seconds, default 120). |
| `remote_manifest_url` | string | unset | Optional. A URL serving a JSON index of extra images. |
| `daily_json_url` | string | unset | Optional. A daily-changing image; must be set together with `daily_image_url`. |
| `daily_image_url` | string | unset | Optional. The image URL for the daily image. |

Any vision model that speaks the OpenAI chat-completions API will do: Ollama, llama.cpp, LM Studio, or
a cloud API. List two endpoints if you have a fast one that is not always awake and a slower one that
is — Matchy tries them in order and skips a dead one for a few minutes.

## First run

The first start does real work:

1. It hashes every image in your source folders **before the port opens**. Tens of seconds for a few
   hundred images; longer on slow storage or a Raspberry Pi. The add-on looks unresponsive meanwhile.
2. It then builds a master copy of each image, one to three seconds each — five to fifteen minutes of
   background work on one CPU core for a few hundred images.
3. `/current.json` answers within seconds of the port opening, with `captioned: false` until the first
   blurb is written. Captioning runs one image at a time, a couple of seconds each on a local
   8-billion-parameter vision model.

So: usable almost immediately, fully captioned after a while.

## Using it

- **Sidebar panel.** Open Matchy from the Home Assistant sidebar. It shows the current wallpaper, the
  library, and a **next wallpaper** button. This route is authenticated by Home Assistant.
- **Published port.** Other clients — desktops, tablets, dashboards, a browser — fetch images directly
  from `http://<your-ha-host>:<the host port you published>`.
- **HTTP API:**

| Endpoint | Description |
| :--- | :--- |
| `GET /current.json` | The current image: title, credit, blurb, and URL templates. |
| `GET /library.json` | Everything in the library. |
| `GET /` | The gallery page, with the next-wallpaper button. |
| `GET /current/clean/WxH.jpg` | The current image at any size, without the label. |
| `GET /current/label/WxH.jpg` | The current image at any size, with the label. |
| `GET /img/<id>/<rev>/clean/WxH.jpg` | One specific image, immutably cacheable. |
| `GET /img/<id>/<rev>/label/WxH.jpg` | The same with the label. |
| `GET /img/<id>/<rev>/span.jpg?layout=WxH+X+Y,...` | One image composed across a multi-monitor layout, label on the primary screen. |
| `GET /img/<id>/original.jpg` | The untouched original. |
| `POST /api/next` | Rotate now. |
| `GET /healthz` | Liveness. |

## Security

- The **sidebar panel is authenticated** by Home Assistant.
- The **published port is not authenticated**. Anything on your network that can reach it can read
  every image and every caption, and responses allow cross-origin requests. That is deliberate: it is
  what lets tablets, desktops and dashboards display the wallpaper without credentials. Do not expose
  that port to the internet. Only `POST /api/next` is restricted, to private network ranges.
- An API key you enter for a captioner is written to a file inside the add-on's own data directory at
  mode 600. It never appears in the generated config file or in the log.

## Troubleshooting

**No images found.** `source_folders` must point at paths under `/media` or `/share` that exist inside
the add-on. Check the log: a missing folder is named there and skipped.

**Captions never appear.** Check `caption_enabled`, then the captioner `url` — it must be the full
`/v1/chat/completions` path, not just the host. The log says when an endpoint is unreachable and when
it will be retried. A blurb that breaks the word-count or grounding rules is retried once immediately,
then that image is retried six hours later.

**The same image never changes.** Rotation prefers captioned images, so with no reachable captioner you
can sit on one picture. Either configure a captioner or set `caption_enabled: false`.

**Images look soft.** `master_max_px` caps the longest edge of the master copy at 3840 by default.
Raise it if your screens are bigger than 4K, and remember a bigger master costs memory per render.

**Slow or unresponsive first start.** Expected: see *First run*. Avoid restarting the add-on during the
first scan — it has to start the hashing over.

**Where the log is.** The add-on's **Log** tab in Home Assistant.

## Licensing and credits

No images ship with this add-on. Images you point it at stay yours, and their licences are your
responsibility. The bundled fonts are Noto, under the SIL Open Font Licence 1.1; the licence travels
with the image. Matchy's own code is MIT.
