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

1. The port opens at once. Meanwhile it hashes every image in your source folders: tens of seconds
   for a few hundred images, longer on slow storage or a Raspberry Pi. Until that lands,
   `/current.json` answers `503 library empty` and the panel says *Nothing yet*. Progress is saved
   every few seconds, so a restart mid-scan picks up where it left off.
2. The first wallpaper appears the moment the scan finishes. It then builds a master copy of each
   image in the background, one to three seconds each.
3. `/current.json` says `captioned: false` until the first blurb is written. Captioning runs one image
   at a time, a couple of seconds each on a local 8-billion-parameter vision model.

So: usable almost immediately, fully captioned after a while.

## Using it

- **Sidebar panel.** Open Matchy from the Home Assistant sidebar. This route is authenticated by Home
  Assistant. From there you can:
  - **Next picture**, **Pause rotation** / **Resume rotation**. Paused holds whatever is on screen;
    Next and Show now still work while paused, and Resume starts a full interval from then.
  - **Show now** on any picture in the library.
  - **Hide** a picture so it never comes up in rotation, and **Unhide** it later. Hiding the picture on
    screen moves on at once. The last visible picture cannot be hidden.
  - **Edit caption** by hand. A hand-written caption is never overwritten by the captioner.
  - **Re-caption** a picture: it jumps the captioning queue and replaces even a hand-written caption.
    If the captioner fails, the old caption stays.
  - **Change every … to … minutes.** This lasts until you change the rotation options in the add-on
    configuration, so whichever was set last wins; **Use the options** goes back to them.
  - See whether the captioner is ready, what it is writing and how many pictures are waiting.
- **Published port.** Other clients — desktops, tablets, dashboards, a browser — fetch images directly
  from `http://<your-ha-host>:<the host port you published>`.
- **HTTP API:**

| Endpoint | Description |
| :--- | :--- |
| `GET /current.json` | The current image: title, credit, blurb, `paused`, `next_change` (null while paused), and URL templates (prefixed automatically when fetched through the sidebar panel). |
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

Control endpoints take a JSON object and **must** be sent with `Content-Type: application/json`.
*Network* means the sidebar panel or any private address on the published port, so a dashboard
button or an automation can use it. *Panel* means the sidebar panel only.

| Endpoint | Body | Who | Description |
| :--- | :--- | :--- | :--- |
| `POST /api/pause` | `{}` | Network | Hold the picture on screen. |
| `POST /api/resume` | `{}` | Network | Rotate again, a full interval from now. |
| `POST /api/show` | `{"id": "<id>"}` | Network | Put this picture on screen now. |
| `POST /api/hide` | `{"id": "<id>"}` | Panel | Keep this picture out of rotation. |
| `POST /api/unhide` | `{"id": "<id>"}` | Panel | Put it back. |
| `POST /api/caption` | `{"id", "title", "place", "credit", "blurb"}` | Panel | Write the caption by hand; `blurb` is required. |
| `POST /api/recaption` | `{"id": "<id>"}` | Panel | Ask the captioner for a fresh caption. |
| `POST /api/rotation` | `{"min_minutes": 10, "max_minutes": 12}` or `{"reset": true}` | Panel | Set the rotation window, or go back to the options. |

For example, a Home Assistant `rest_command` that pauses the rotation:

```yaml
rest_command:
  matchy_pause:
    url: "http://<your-ha-host>:<published port>/api/pause"
    method: post
    content_type: "application/json"
    payload: "{}"
```

## Security

- The **sidebar panel is authenticated** by Home Assistant.
- The **published port is not authenticated**. Anything on your network that can reach it can read
  every image and every caption, and responses allow cross-origin requests. That is deliberate: it is
  what lets tablets, desktops and dashboards display the wallpaper without credentials. Do not expose
  that port to the internet.
- **Controls on the published port** — next, pause, resume and show now — accept private network
  addresses only. Everything that changes the library or its captions (hide, unhide, edit caption,
  re-caption, rotation window) works **only through the sidebar panel**, because the published port
  has no login.
- The control endpoints need a JSON body and refuse cross-origin requests, so another website open in
  a browser on your network cannot use them directly. A determined page using DNS rebinding could
  still reach the *network* controls (it could skip or pause the wallpaper), never the panel-only ones.
- An API key you enter for a captioner is written to a file inside the add-on's own data directory at
  mode 600. It never appears in the generated config file or in the log.

## Troubleshooting

**No images found.** `source_folders` must point at paths under `/media` or `/share` that exist inside
the add-on. Check the log: a missing folder is named there and skipped.

**Captions never appear.** Check `caption_enabled`, then the captioner `url` — it must be the full
`/v1/chat/completions` path, not just the host. The log says when an endpoint is unreachable and when
it will be retried. A blurb that breaks the word-count or grounding rules is retried once immediately,
then that image is retried six hours later.

**The same image never changes.** Check how many images it actually found: open `library.json` from the
panel, or look for the `library: N items` line in the log. One usable image means no rotation. If the
count is right, the rotation window may simply be long — `POST /api/next` moves it on immediately.

**A picture never appears, and its card says "image too large".** Matchy refuses to decode any source
over 50 megapixels (8K is 33), because the add-on has no memory limit and one huge file could exhaust
the host's memory. Scale that file down; it is picked up again on the next folder scan.

**Images look soft.** `master_max_px` caps the longest edge of the master copy at 3840 by default.
Raise it if your screens are bigger than 4K, and remember a bigger master costs memory per render.

**Slow first start.** Expected: see *First run*. The panel answers straight away and fills in when
the scan finishes.

**Where the log is.** The add-on's **Log** tab in Home Assistant.

## Licensing and credits

No images ship with this add-on. Images you point it at stay yours, and their licences are your
responsibility. The bundled fonts are Noto, under the SIL Open Font Licence 1.1; the licence travels
with the image. Matchy's own code is MIT.
