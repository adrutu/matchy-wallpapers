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
| `love_weight` | int, 1–10 | 3 | How many times as often a **loved** picture (one given a heart in the panel or with `POST /api/love`) comes up. 1: a heart changes nothing. See *Loved pictures*. |
| `quiet_hours` | object | `{}` (off) | Hold and dim the picture overnight: `start` and `end` (local times, `HH:MM`; both needed, an end earlier than the start is the next morning), `hold` (bool, default true) and `dim` (0–1, default 0.6). See *Quiet hours*. |
| `folder_scan_minutes` | int, 1–1440 | 10 | How often it looks for new, changed or removed files. |
| `master_max_px` | int, 1280–7680 | 3840 | Longest edge of the cached master copy every render is cut from. |
| `cache_max_mb` | int, 128–65536 | 2048 | Render cache size. Evicted least-recently-used down to 85 % when it overflows. |
| `render_threads` | int, 1–8 | 2 | Renders running at the same time. |
| `focus_auto` | bool | false | Crop around each picture's main subject instead of the centre (see Focus points). Off: centred crops, except for a focus point set by hand in the panel. |
| `caption_enabled` | bool | true | Write gallery blurbs at all. Off: the captioner never runs, so a picture without a blurb carries the title, place and credit only (from `meta.json` or `sets`); blurbs already written, and ones you write in the panel, still show. |
| `caption_language` | string | English | Language the blurb is asked for. The grounding check rejects capitalised words that are not in the metadata, so a language that capitalises every noun (German) gets most blurbs rejected. |
| `caption_spelling` | `British` or `American` | British | British also rewrites common American spellings in what comes back (colour, grey, centre…). |
| `caption_min_words` | int, 5–200 | 30 | Shortest blurb asked for. Keep it at or below the longest. |
| `caption_max_words` | int, 5–200 | 45 | Longest blurb asked for. After a second try, a blurb within three words of the range is kept. |
| `caption_kids` | bool | false | Also write a **kids caption** for each picture: 15 to 30 simple words for ages 5 to 9, with one fun fact and nothing scary, checked by the same grounding rules. It waits until no normal caption is waiting, because the vision model may be busy with other work. Screens show it with `?label=kids` (see *Kids labels*). |
| `captioners` | list of objects | `[]` | OpenAI-compatible vision endpoints, tried in order. Fields: `name` (required), `url` (required, the full `/v1/chat/completions` URL), `model` (required), `api_key` (optional), `timeout` (seconds, 5–600, default 120). |
| `art_institute_chicago` | object | off | Public-domain works from the Art Institute of Chicago. Fields: `enabled` (false), `per_day` (1–50, default 3, new works a day), `min_px` (640–7680, default 1686, the smallest long side kept; 1686 is the most this source serves). See [Open-access art](#open-access-art). |
| `the_met` | object | off | Public-domain paintings and drawings from The Metropolitan Museum of Art's highlights. Same fields; `min_px` defaults to 2560. |
| `wikimedia_picture_of_the_day` | object | off | Wikimedia Commons' Picture of the Day. Same fields; `per_day` is 1–7, default 1 (today's picture; more fills in the days before). |
| `palette_enabled` | bool | true | Colour match: work out each picture's main colours, an accent and the nearest GNOME accent name (see [Colour match](#colour-match)). Off: `palette` is `[]` and `accent` and `gnome_accent` are `null` everywhere, and nothing is computed. |
| `mood` | `off`, `light` or `strong` | off | Mood-aware picks (see below). `off` is a plain shuffle, exactly as before the option existed. `light` pulls a picture whose tags fit the moment forward about 3 shuffles in 4, `strong` about 19 in 20; every picture still comes up once per shuffle. |
| `weather_entity` | entity id | unset | Optional. A Home Assistant weather entity (`weather.forecast_home`, `weather.open_meteo`…) read every ten minutes for the mood-aware picks. Without it only the time of day and the season count. |
| `remote_manifest_url` | URL | unset | Optional. Base URL of a web folder holding a `manifest.json` (`[{"name": "…", "file": "…"}]`) with the image files next to it. Fetched once a day; new files are downloaded into the add-on's data and get ids `remote-<file name>` (lower-cased, without the extension; a file whose name gives an id another file already has gets `-2`, `-3`…). Only `.jpg`, `.jpeg`, `.png` and `.webp` entries without a `/` are taken. |
| `daily_json_url` | URL | unset | Optional. A JSON document `{date, title or caption, place, copyright}` describing a picture that changes daily. Checked hourly; each new date is stored as `daily-YYYYMMDD`. Needs `daily_image_url` too. |
| `daily_image_url` | URL | unset | Optional. The image URL that goes with `daily_json_url`. |
| `mqtt_enabled` | bool | false | Create a **Matchy Wallpapers** device with entities in Home Assistant over MQTT (see *Home Assistant entities*). Needs the Mosquitto broker add-on; the broker's address and login come from Home Assistant, nothing to type. Turning it off removes the entities again. |
| `mqtt_image_url` | URL | unset | Optional. Where Home Assistant fetches the picture for the image entity, for example `http://<your-ha-host>:<published port>`. Empty: the add-on's own address on Home Assistant's internal network, which needs no published port. |

Only options you set are written to the engine's config; everything else keeps the defaults above.

### Open-access art

Three built-in sources of art with real metadata, each off until you turn it on. Once an hour Matchy
checks each source that is on and, until it has added `per_day` new works that day, asks for more.
A work joins the library only if it is public domain (the two museums) and its image is at least
`min_px` on its long side; works without a usable image are skipped and remembered, so they are not
asked about again.

```yaml
the_met:
  enabled: true
  per_day: 2
  min_px: 2560
```

What each one adds, and how the label credits it:

| Source | Picks | Label (title / second line / credit) | Id |
| :--- | :--- | :--- | :--- |
| Art Institute of Chicago | A random page of public-domain works with an image. | Title / artist · date / *The Art Institute of Chicago · CC0* | `aic-<id>` |
| The Met | A random page of the collection's highlights with images, paintings or drawings (one of the two each time); each object is checked for public domain and for being a painting, drawing, watercolour or pastel. | Title / artist · date / *The Metropolitan Museum of Art · metmuseum.org · CC0* | `met-<id>` |
| Wikimedia Commons Picture of the Day | Today's picture (UTC), then earlier days if `per_day` asks for more. | Title / – / *author · licence · Wikimedia Commons* | `potd-YYYYMMDD` |

The artist, date, medium and place of origin go into the picture's sidecar (`facts`) and the
captioner's prompt, together with the work's page (`source_url`), so a blurb can name the painter
and the grounding check accepts it. Wikimedia's description of the picture is given as context.

Things to know:

- **The Art Institute of Chicago serves public-domain images at most 1686 pixels wide** (its API
  documentation lists 843, 1200 and 1686), so its `min_px` defaults to 1686. That is soft on a 4K
  screen; raise it to 2560 to keep only tall works, which come at 1686 × 2500 or more.
- **The Met's highlights are mostly objects** (vases, armour, furniture), so Matchy asks its search
  for paintings or drawings and checks each object again. The engine-only `art_sources.met.media`
  list sets the kinds (an empty list takes everything).
- **Picture of the Day is not only public domain.** Most pictures are photographs under Creative
  Commons licences (CC BY, CC BY-SA); the label always carries the author and the licence, which
  is what those licences ask for.
- Every request carries a User-Agent naming Matchy and its project page; requests to one source go
  one at a time, at least a second apart. A source that refuses or fails is logged
  (`art source met failed: …`) and asked again at the next hourly check.
- Turning a source off keeps what it already added. To drop those pictures too, leave them out with
  `exclude` (`aic-*`, `met-*`, `potd-*`).
- Images are stored in the add-on's data (`originals/`) like other downloaded pictures, so they are
  in backups. The museums' images of public-domain works are CC0; Commons pictures keep their own
  licence.

These are the terms each source states, as read in October 2026: the Art Institute of Chicago asks
for at most one request a second, an `AIC-User-Agent` header with a contact, and public-domain images
only; The Met asks for no more than 80 requests a second, gives public-domain images and data under
CC0, and asks that its name not be used to imply endorsement; Wikimedia asks for a User-Agent with
contact details, serial requests, and the thumbnail sizes its API hands out.

### Setting up a captioner

Any server that speaks the OpenAI chat-completions API with an `image_url` content part will do.
Matchy sends one request per image, `stream: false`, with `response_format: {"type": "json_object"}`,
and expects `{"title": "...", "blurb": "..."}` back (a JSON object inside a code fence, after an inline
`<think>` block, or in content parts is accepted too). With `focus_auto` on it also asks for
`"subject": [left, top, right, bottom]`, the main subject's box as fractions 0–1; a reply without a
usable box still counts (see Focus points).

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

Along with the blurb, the captioner is asked for 3 to 6 **tags** from a fixed list (`"tags": [...]` in
the same JSON): dawn, day, dusk, night · sun, cloud, rain, snow, fog, storm · spring, summer, autumn,
winter · sea, mountain, forest, city, desert · warm, cool, cosy, bright, calm, dramatic. Words outside
the list are dropped (a few near misses are folded in: cozy, ocean, sunset…); tags never get a caption
rejected. Pictures captioned before tags existed get them through one tag-only request each, at the
back of the captioning queue, without touching the caption (a hand-written one included), but only
while `mood` is on: with it off nothing uses the tags, so no model calls are spent on them. The tags
are what the mood-aware picks match against.

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
- Anything over 50 megapixels is refused (see Troubleshooting), and so is a file that cannot be decoded
  (not really a picture, or cut short); the panel card says why. Phone photos are turned upright
  from their EXIF orientation.
- A `meta.json` next to the pictures gives the captioner facts it may state, per file:
  `{"great-wave.jpg": {"title": "…", "place": "…", "credit": "…", "context": "…"}}`. A known `title`
  is used as is, `place` and `credit` go on the label, and `context` (one sentence on what the
  picture shows) grounds the blurb. Other keys are ignored, so you can keep provenance notes there.
  Without it the captioner describes only what it sees, and never names a place.

## Using it

### The sidebar panel

Open **Matchy Wallpapers** from the sidebar (or **Open Web UI** on the add-on's page). This route is
authenticated by Home Assistant. The
page reloads itself when the picture changes (it checks every 30 seconds). The picture on screen is at the
top; under it are three columns: **Rotation** (with a big **Next picture** button), **On screen now** (what
the picture is, its colours and why it was picked) and **Screens**, then the library. The captioner only
shows up there when it needs you: none set up, or one that stopped answering. From there you can:

- **Next picture**, **Pause rotation** / **Resume rotation**. Paused holds whatever is on screen;
  Next and Show now still work while paused, and Resume starts a full interval from then.
- **Show now** on any picture in the library.
- **Hide** a picture so it never comes up in rotation, and **Unhide** it later. Hiding the picture on
  screen moves on at once. The last visible picture cannot be hidden.
- **Love it** (the heart on the picture on screen and on every thumbnail): a loved picture comes up
  about `love_weight` times as often. Tap the heart again to take it back. **Loved only** above the
  library shows just the loved pictures.
- **Edit caption** by hand (title, place, credit, blurb; limits 100/120/200/600 characters), and tick
  the picture's **tags**. A hand-written caption is never overwritten by the captioner.
- **Re-caption** a picture: it jumps the captioning queue and replaces even a hand-written caption.
  If the captioner fails, the old caption stays.
- **Kids caption** (with `caption_kids` on, or once any picture has one): the card shows the kids
  blurb under the normal one. Write it by hand (up to 400 characters) and the captioner never
  overwrites it; save it empty and the captioner writes a fresh one. Re-caption and editing the normal
  caption leave the kids caption alone.
- **How often**: a slider from every 15 minutes to every 12 hours, plus **Day & night**. It saves
  when you let go. The shortest/longest window is still there under **Custom window**. A choice made
  here lasts until you change the rotation options in the add-on configuration, so whichever was set
  last wins; **Use the options** goes back to them (in Day & night, pick a stop on the slider instead).
- **Quiet until 07:00** under Rotation while quiet hours are on (see below); outside them, when they
  start and end.
- **Screens**: every screen that has named itself (see below) and whether it follows Matchy.
  **Turn off** / **Turn on** one screen, or **Forget** it. When and from where each was last seen is in
  `/screens.json`.
- See whether each captioner is ready or being skipped, what it is writing now and how many pictures
  are waiting (for a caption, or for tags).
- **Focus point**: click a thumbnail to put the picture's focus point there (a filled dot); every crop
  then keeps that point in frame. **Reset focus** drops it again. A hollow dot is a focus point found
  by the captioner or automatically (see Focus points).
- Under the picture on screen, its colours: a swatch per palette colour and the accent, with the
- **Why this one**, under the picture on screen, when `mood` is on: which of its tags fit the moment,
  or that it was simply its turn; and a **Mood** line in the Rotation box with the moment's tags and
  what the weather entity last said.
  GNOME accent name (see Colour match).

Every thumbnail opens the labelled picture at 2560x1440 (in the sidebar panel, where a click sets the
focus point, Ctrl-click or a middle click does).

### Focus points

Every screen gets the picture cropped to its own shape, so a portrait tablet sees a narrow slice of
a landscape picture. By default that slice is the middle. A picture can carry a **focus point**
instead: the crop is centred on it, and slid back inside the picture where the point is near an edge,
so the point is always in frame and as central as the picture allows. The thumbnails in the panel
are 16:9 crops made the same way.

A focus point comes from one of three places, best first:

1. **By hand**: a click on the thumbnail in the panel (`POST /api/focus`). It is never replaced by the
   captioner or the automatic guess, re-caption included, until **Reset focus**. It is used whether
   `focus_auto` is on or not.
2. **The captioner** (`focus_auto` on): the caption request also asks for a box around the main
   subject, as fractions of the width and height. The focus is the box's centre. A missing box, or one
   that is out of range, inside out, a sliver or the whole frame, is ignored.
3. **Automatic** (`focus_auto` on): a quick look at the picture's master for where the detail is
   (the centre of its edges), done for every picture as its master is built, so no picture waits on
   the captioner. A captioner's box replaces it when the caption arrives.

A machine-made focus point belongs to one version of the file: when the file changes it is found
again. Moving a focus point changes the picture's `rev` (and so `version` in `/current.json`), so screens
fetch the new crop the next time they check.


### Other screens

Clients fetch images directly from `http://<your-ha-host>:<published port>`. Each client asks for
the size it wants and gets a JPEG; nothing is pre-rendered. Recipes for a GNOME desktop, a Homepage
dashboard, Home Assistant tablets, Home Assistant Assist by voice ("what's on the wall?") and plain
`curl` are in the repository: <https://github.com/adrutu/matchy-wallpapers/tree/main/docs/clients>.

### Kids labels

With `caption_kids` on, every label URL takes `?label=kids`: `/current/label/WxH.jpg?label=kids`,
`/img/<id>/<rev>/label/WxH.jpg?label=kids` and `span.jpg?layout=…&label=kids`. That label has the same
title, place and credit, with the kids blurb instead of the normal one, and bigger type: with no `s`
in the URL it is drawn at `s=1.3` (an `s` you give wins). Until a picture has its kids caption, the
URL serves the normal label at that same size, so a screen can ask for it from day one. When the kids
caption arrives, that picture's `rev` and `version` change, like after an edit.

### Guess where?

`/current/label/WxH.jpg?reveal=<seconds>` serves the **clean** picture until `<seconds>` after it went up
on screen (`since` in `current.json`), then the labelled one: look first, guess, then read. It combines
with `label=kids` and `s`. `reveal` is whole seconds, 0–86400; `0` shows the label at once. The `ETag`
changes at the reveal, so a client that sends `If-None-Match` gets the label as soon as it is due.

`/current.json?reveal=<seconds>` (same number) adds `reveal_at` and `revealed`, and its `version` ends
in `.veiled` until the reveal, so a client that refetches when `version` changes (a Home Assistant
`image` entity, for example) picks the label up too. The Wallpanel screensaver recipe in
[docs/clients/home-assistant.md](https://github.com/adrutu/matchy-wallpapers/blob/main/docs/clients/home-assistant.md#6-guess-where-on-a-wallpanel-screensaver)
sets this up for one tablet.

### Home Assistant entities

With `mqtt_enabled` on and the **Mosquitto broker** add-on running, Matchy creates its own entities in
Home Assistant through MQTT discovery, with no YAML: one device, **Matchy Wallpapers**, under
**Settings → Devices & services → MQTT**. Home Assistant names the entities after the device, so they
come out as below; rename them as you like.

| Entity | What it is |
| :--- | :--- |
| `image.matchy_wallpapers_now_showing` | The labelled picture on screen, 1920x1080. Home Assistant fetches it itself, so an `https://` dashboard shows it without mixed content, and a screensaver that follows an image entity changes with it. |
| `sensor.matchy_wallpapers_title` | The title (the id until a picture has a caption). Attributes: `id`, `rev`, `place`, `credit`, `blurb`, `since`, `next_change`, `captioned`, `quiet` (in quiet hours), and the picture's `loved` mark and `palette`/`accent` colours. |
| `button.matchy_wallpapers_next` | Next picture. |
| `button.matchy_wallpapers_love` | Mark the picture on screen loved. |
| `switch.matchy_wallpapers_pause` | Pause the rotation (on) or let it run (off). |
| `select.matchy_wallpapers_rotation` | How often: the panel slider's stops (*Every 15 minutes* to *Every 12 hours*, *Day & night*) plus *Add-on options*, which goes back to the configured window. A custom window set in the panel shows as unknown. |
| `switch.matchy_wallpapers_screen_<name>` | One per screen in the Screens list: on while it follows Matchy. It appears when the screen first asks for a picture and goes when you **Forget** it. |
| `sensor.matchy_wallpapers_caption_queue` | Pictures waiting for a caption; attribute `writing` names the one being captioned. |

The device is marked unavailable when the add-on is stopped or loses the broker (an MQTT last will),
and everything comes back on its own when it reconnects. Turn `mqtt_enabled` off and the next start
removes every retained message it published, so the device and its entities disappear from Home
Assistant (as long as the broker is still reachable; the log says what was removed).

The image entity needs an address Home Assistant can fetch from. By default that is the add-on's own
hostname on Home Assistant's internal network (`http://<add-on hostname>:8160`), which works without a
published port. If it does not in your setup, publish the port and put
`http://<your-ha-host>:<published port>` in `mqtt_image_url`.

The topics, should you want them from an automation or another program: state in `matchy/state` (one
JSON object), the picture URL in `matchy/picture`, `matchy/status` for `online`/`offline`, and
commands on `matchy/cmd/next`, `matchy/cmd/love` (payload `PRESS`), `matchy/pause/set` (`ON`/`OFF`),
`matchy/rotation/set` (an option's label) and `matchy/screen/<key>/set` (`ON`/`OFF`). Discovery
configs are retained under `homeassistant/`. The client is a small MQTT 3.1.1 one inside the add-on:
plain TCP only, so a broker that insists on TLS is logged and left alone.

### Day & night

**Day & night** puts one picture up at sunrise and the next at sunset. The add-on reads Home
Assistant's latitude and longitude once at start (that is what its Home Assistant API access,
`homeassistant_api`, is for, together with the weather entity of the mood-aware picks below; nothing
else uses it). Without a location, or on a day the sun does not rise or does not set there, it
changes at 07:00 and 19:00 local time instead. Restart the add-on after moving the home location.

### Mood-aware picks

With `mood` set to `light` or `strong`, the next picture is steered by the moment: cosy on a cold wet
evening, bright on a sunny morning. The moment is a set of tags from the same list the captioner
uses:

- **Time of day**, from the same sunrise and sunset as Day & night (or 07:00 and 19:00 without a
  location): `day` or `night`, and `dawn` or `dusk` for the hour either side of sunrise and sunset
  (together with the day or night it borders on, so the change is gradual).
- **Season**, from the date: meteorological seasons (December to February is `winter`…), the other
  way round south of the equator.
- **Weather**, from `weather_entity`, read every ten minutes: `sunny` gives `sun` (and `bright` by day),
  `partlycloudy` `sun` and `cloud`, `cloudy` and `windy-variant` `cloud`, `rainy` `rain`, `pouring`
  and `hail` `rain` and `storm`, `lightning` `storm`, `snowy` `snow`, `snowy-rainy` `snow` and `rain`,
  `fog` `fog`. Rain, snow, fog or a storm add `cosy`, a storm `dramatic`. At or below 8 °C the moment
  also wants `warm` and `cosy`; at or above 25 °C, `cool`. A reading older than three hours is dropped
  (the last one is kept while the entity cannot be read, and the log says so once); without an entity,
  or without a reading, only the time of day and the season count.

The picker keeps its shuffle: every picture still comes up exactly once per shuffle, hidden ones
aside, so nothing is excluded and nothing starves. What changes is the order within the shuffle: with
`light`, about 3 picks in 4 pull forward a picture whose tags share something with the moment (a
picture matching three tags is three times as likely as one matching one); with `strong`, about 19 in
20. The rest of the picks, and every pick when nothing in the shuffle matches, are the next picture in
the shuffle, as with `off`. The picture on screen is never shown twice in a row. Show now, hide and the
rotation window work as before.

`/current.json` carries the picture's `tags` and `mood_match` (the tags that fit the moment when it
was picked; empty with `mood: off`, for a hand-picked picture, or when nothing fitted), and
`/library.json` a `mood` object (`level`, the moment's `context`, `weather_entity`, the last `weather`
reading as `{"condition", "temperature_c", "at"}` or null, and `weather_error`). The log line for each
rotation names the matched tags (`[mood: cosy, rain, warm]`).

Tags live in the sidecar (`"tags"`), next to the caption, and can be edited in the panel (tick boxes
under the caption) or by hand in the file. They are not part of the label, so changing them does not
change the picture's `rev`.

### Loved pictures

A heart is a vote: a loved picture gets `love_weight` turns in every shuffle instead of one (3 by
default), so over time it is on screen about that many times as often as the others. The extra turns
are spread through the shuffle rather than bunched together, and no picture is ever shown twice in a
row, so a small library with one loved picture alternates rather than repeating it. Loving a picture
takes effect at once: its extra turns are added to the shuffle in progress. Hidden beats loved: a
hidden picture stays off the screen however many hearts it has, and comes back loved when unhidden.

Hearts are kept in `state.json`, show as `loved` in `/current.json` and `/library.json`, and can be
given from any client on your network with `POST /api/love` (a dashboard tile, say), because they
only change how often a picture comes up.

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

### Quiet hours

`quiet_hours` sets a nightly window, for example:

```yaml
quiet_hours:
  start: "23:00"
  end: "07:00"
  hold: true
  dim: 0.6
```

During the window:

- **`hold`** (default true): the rotation stands still on the picture on screen, like a pause that
  ends by itself. **Next picture** and **Show now** still work, and the picture they put up is held
  too. When the window ends, the rotation resumes with a full interval from then, so the night's
  picture stays up for one more interval before the next one. With `hold: false` the rotation carries
  on as usual. A rotation paused before or during the window stays paused afterwards; **Resume**
  starts the full interval.
- **`dim`** (default 0.6): `/current/clean/...` and `/current/label/...` are darkened by that much (0
  is not at all, 0.6 is 60 % darker, 1 is black), label included, and their `ETag` changes, so a client
  polling with `If-None-Match` gets the dimmed picture when the window starts and the bright one when
  it ends. `/img/...` addresses are never dimmed: they are cacheable for a year and keep their meaning.
  A client that builds its addresses from `urls` in `current.json` (the Home Assistant, Homepage and
  GNOME recipes do) therefore keeps the undimmed picture; point it at `/current/...` instead, or let it
  read `quiet` from `current.json`.
- `current.json` says `"quiet": true`; `next_change` is `null` while the picture is held.

Times are the add-on's local time, so the window follows daylight saving time: the night the clocks
go back is an hour longer, the night they go forward an hour shorter. Leave `start` and `end` empty
(the default) to turn quiet hours off.

### Screens

Any client may add `?screen=<name>` to any `GET` (`/current.json`, `/current/...`, `/img/...`). The name
is 1 to 40 characters of letters (in any script), digits, space and `. _ : @ -`, starting with a letter
or digit; a request with any other name is served as usual but not listed. Only clients on your own
network (the Network tier below) are listed. The panel's **Screens** box then shows that screen. Up to
24 are kept: a new one beyond that replaces the following screen unseen the longest, at most one new
name a minute (the others are served but not listed until they ask again), and screens you turned off
stay (so with 24 screens all turned off, a new one is not added).

**Turn off** is advisory: Matchy keeps serving that screen. `/current.json?screen=<name>` answers
`"follow": false`, and every `/current.json` lists the screens that are off in `"screens_off"`. A client
that honours it goes back to its own wallpaper, and follows again when it is turned back on.
`GET /screens.json` lists every screen with `id`, `follow`, `first_seen`,
`last_seen`, `age_s`, `ip`, `agent` and `image` (the last picture it fetched).

For Home Assistant dashboards, a card-mod theme rule can add `?screen={{ user | urlencode }}` to the
background URL, so each tablet's Home Assistant user becomes a screen, and leave the background out for
a user listed in `screens_off`. A tablet that spends the day in a screensaver fed by Home Assistant
(not by the tablet's browser) shows an old *last seen* while it still follows.

### Gentle motion

A dashboard or a screensaver can drift slowly over the picture (a pan and zoom over a minute or two)
and fade from one picture to the next. The browser does the moving with CSS transforms, so it costs
next to nothing; Matchy only supplies a picture with room to move: add `?pad=1.2` (1.0 to 1.3) to a
clean or labelled picture and it comes back that many times larger, `WxH` times the pad, showing the
same view as without it. The client shows it at screen size and zooms in by up to the pad, which is
where the room to pan comes from; at full zoom it is 1:1, never upscaled.

- The padded picture is centred on the picture's **focus point** (the `focus` field of its sidecar,
  `{"x": 0-1, "y": 0-1}` from the top-left), as far as the picture allows; without one, on the centre.
- The **label** is sized for the `WxH` screen and kept inside the part of the padded picture that
  every screen-sized view of it shows. Whatever the client does, as long as it never shows past the
  picture's edge and never zooms in beyond 1:1 (more than the pad), the label stays whole on screen.
- `pad=1.0`, or no `pad`, is the usual picture. The padded size must stay within 7680 pixels a side.
- Nothing in the add-on options turns this on: each client asks for it. Recipes for Home Assistant
  dashboards, the Wallpanel screensaver and Homepage are in the repository's `docs/clients`; they
  stop drifting when the browser reports `prefers-reduced-motion` (on most tablets, the system's
  *Remove animations* or *Reduce motion* setting). GNOME does not animate wallpapers, so its client is unchanged.

### HTTP API

Read endpoints:

| Endpoint | Description |
| :--- | :--- |
| `GET /current.json` | The current picture: `id`, `rev`, `version`, `title`, `place`, `credit`, `blurb`, `tags`, `mood_match` (see Mood-aware picks), `since`, `next_change` (null while paused or held by quiet hours), `paused`, `captioned`, `quiet` (true during quiet hours), `quiet_window` (`{"start", "end", "hold", "dim"}`: the quiet window now, or else the next one; `null` when quiet hours are off), `loved`, `palette`, `accent`, `gnome_accent` (see Colour match), and `urls` (`original`, `clean`, `label`, `span` templates with `{w}`, `{h}`, `{layout}` to fill in). `version` changes on every rotation, every caption edit and every focus point change; clients compare it (the colours do not change it: they arrive for a new picture within seconds of a cold start, so a client that uses them reads them on every poll). `kids_blurb` is the kids caption (empty until there is one). With `?reveal=<seconds>`, also `reveal_at` and `revealed`, and `version` ends in `.veiled` until the reveal (see *Guess where?*). `screens_off` lists the screens turned off; with `?screen=<name>`, `follow` says whether that one should follow. `503` while the library is empty. |
| `GET /library.json` | Every picture with its status, caption, `tags`, `hidden`, `loved` and `recaption_queued` flags and its `palette`, `accent` and `gnome_accent`, the focus point crops use (`focus` = `{"x", "y", "source"}`, `source` being `hand`, `model` or `auto`; null for a centred crop), plus the rotation (`mode` = `minutes` or `daynight`, the window, `love_weight`, and `next_sun` = `{"at", "event"}` in Day & night, `event` being `sunrise`/`sunset`, or `morning`/`evening` without a location), the `mood` object (see Mood-aware picks) and captioner state (`waiting` for a caption, `tagging` for tags). |
| `GET /screens.json` | The Screens list (see Screens). Network tier only, and without CORS, because it carries addresses. |
| `GET /` (also `/index.html`) | The gallery page. |
| `GET /current/clean/WxH.jpg[?pad=]` | The current picture at any size, without the label. Sends an `ETag`; `If-None-Match` gets a `304` until the picture changes or quiet hours start or end. Dimmed during quiet hours. `pad` (1.0–1.3) makes it that many times larger for a client that pans over it (see Gentle motion). |
| `GET /current/label/WxH.jpg[?s=&label=kids&reveal=&pad=]` | The same with the label. `s` scales the label, 0.6–2.0 (wall tablets look right at about 1.35). `label=kids`: the kids label (see *Kids labels*). `reveal`: the clean picture until that many seconds after `since` (see *Guess where?*). Dimmed during quiet hours. |
| `GET /img/<id>/<rev>/clean/WxH.jpg[?pad=]` | One specific picture; cacheable for a year, so never dimmed. With a `pad` other than 1.0 it is sent with an `ETag` and `no-cache` instead, because the focus point can move under the same `<rev>`. |
| `GET /img/<id>/<rev>/label/WxH.jpg[?s=&label=kids&pad=]` | The same with the label. |
| `GET /img/<id>/<rev>/span.jpg?layout=WxH+X+Y[p],...[&s=&label=kids]` | One picture composed across a multi-monitor layout: each monitor as `width x height + x + y`, `p` marks the primary, which gets the label (the largest screen if none is marked). Gaps are black. |
| `GET /img/<id>/original.jpg` | The original: PNG and WebP pictures from your folders are re-encoded as JPEG; pictures downloaded from a remote manifest or the daily URL are served as downloaded. A JPEG with a GPS position in its EXIF (a phone photo) is served without it: the position and any XMP packet naming one are left out, the image data and the rest of the EXIF (orientation, camera) stay as they are. Streamed from disk, four at a time; past that `503` with `Retry-After`. |
| `GET /healthz` | Liveness; answers `ok` even before the first scan. |

A `label` other than `kids`, or a `reveal` that is not whole seconds from 0 to 86400, answers `400`.
Sizes: width and height 320–7680 (outside that, `/img/...` answers `400` and `/current/...` `404`;
a `pad` outside 1.0–1.3, or one that takes a side past 7680, answers `400` on both); a
span canvas up to 12000x8000 with at most 8 monitors and one primary. One picture (padding included)
or span canvas is at most 40 megapixels (`size_limits.max_pixels`; 7680x4320 and three 4K screens in a
row fit), otherwise `400`. When more than 16 renders are already waiting (`render_queue`), a request
that needs a new render answers `503` with `Retry-After: 5`; sizes already rendered are still served. A `<rev>` that is no longer current (the caption or the focus point changed) answers `302` to the current
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
| `POST /api/love` | `{"id": "<id>"}` or `{}` | Network | Give this picture a heart: it comes up `love_weight` times as often (see Loved pictures). `{}` means the picture on screen (`409` while nothing is). |
| `POST /api/unlove` | `{"id": "<id>"}` or `{}` | Network | Take the heart back (an id that is not loved changes nothing). |
| `POST /api/caption` | `{"id", "title", "place", "credit", "blurb", "tags"}` | Panel | Write the caption by hand; `blurb` is required, the others may be empty. `tags` is optional: a list of words from the tag list (case and a few synonyms are folded; an unknown word is a `400` naming it); left out, the tags stay as they were. |
| `POST /api/caption/kids` | `{"id": "<id>", "blurb": "…"}` | Panel | Write the kids caption by hand (up to 400 characters); an empty `blurb` clears it, so the captioner writes a fresh one. |
| `POST /api/recaption` | `{"id": "<id>"}` | Panel | Ask the captioner for a fresh caption. |
| `POST /api/focus` | `{"id": "<id>", "x": 0.7, "y": 0.4}` or `{"id": "<id>", "reset": true}` | Panel | Set the focus point by hand (`x`, `y` fractions of the width and height, 0–1, from the top left), or drop the hand-set one (back to the captioner's or the automatic one with `focus_auto` on, else the centre). |
| `POST /api/rotation` | `{"min_minutes": 10, "max_minutes": 12}`, `{"mode": "daynight"}` or `{"reset": true}` | Panel | Set the rotation window (whole minutes, 1–1440), switch to Day & night, or go back to the options. |
| `POST /api/screen` | `{"id": "<name>", "follow": false}` | Network | Turn one screen off (`true`: on again). `404` for a screen Matchy has not seen. |
| `POST /api/screen/forget` | `{"id": "<name>"}` | Panel | Drop a screen from the list until it asks again. |

With `mqtt_enabled` on, Home Assistant has entities for next, pause, how often and the screens (see
*Home Assistant entities*), so none of this is needed there. Otherwise, for example, a Home Assistant
`rest_command` that pauses the rotation, and one that gives the picture on screen a heart (the id comes
from `current.json`; a one-tap toggle for a wall tablet is in the Home Assistant client recipe in the
repository):

```yaml
rest_command:
  matchy_pause:
    url: "http://<your-ha-host>:<published port>/api/pause"
    method: post
    content_type: "application/json"
    payload: "{}"
  matchy_love:
    url: "http://<your-ha-host>:<published port>/api/love"
    method: post
    content_type: "application/json"
    payload: '{"id": {{ id | to_json }}}'
```

## Access tiers

Who may call what depends only on where the request comes from:

| Tier | Who gets it | What it covers |
| :--- | :--- | :--- |
| **Public** | anything that can reach the port | every `GET` except `/screens.json`: pictures, captions, the library, the gallery page |
| **Network** | the sidebar panel, or a client whose address is private (`10/8`, `172.16/12`, `192.168/16`, `100.64/10`, loopback, IPv6 ULA and link-local); if the request carries `X-Forwarded-For`, every address in it must be private too | next, pause, resume, show now, love and unlove, turn a screen on or off |
| **Panel** | the sidebar panel only: a request proxied by the Supervisor (its address `172.30.32.2`), which only forwards sessions Home Assistant has authenticated | hide, unhide, edit caption (and the kids caption), re-caption, focus point, how often, forget a screen |

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
  real screens out of a full list until they next ask (one a minute at most once the list is full).
  The worst outcome is a skipped or paused wallpaper or a cluttered Screens list, never a changed
  caption or library.
- **Downloads stay where you point them.** A URL you configure (a remote manifest, the daily picture,
  an art source's API) is fetched wherever it is, your own network included. What a source hands
  back is not trusted that far: a redirect may stay on the host you configured or go to a public
  address, and an image address an art API names must be public (or on that API's host). So a source
  answering `302` to the Supervisor, Home Assistant or a router cannot have the add-on fetch that
  and serve it as a picture. The check resolves each name before the request; a name that changes
  its answer in between (DNS rebinding) is not covered. Captioner endpoints and the weather entity
  are your own settings and are not checked.
- An API key you enter for a captioner is written to a file inside the add-on's own data directory
  at mode 600 and re-written on every start. It never appears in the generated config file or in
  the log. Like every add-on option it is also in Home Assistant's own `options.json` for the add-on.
  The MQTT broker password Home Assistant hands the add-on is kept the same way.
- Over MQTT, anyone who can publish to your broker can press Next, pause, change how often and turn
  screens on or off: the same things as the Network tier, and nothing more (no hide, no caption
  edits). The broker's own login is what protects it. A made-up screen name slipped into the Screens
  list (the known gap above) gets a switch in Home Assistant like any other screen, until you
  **Forget** it.
- The engine runs as **root inside its container**, as official add-ons do; the Supervisor offers no
  option to change that, and Matchy does not drop privileges itself. `/media` and `/share` are
  mounted read-only, and the engine process is started without the Supervisor token (the start
  script uses it once, to read the location, and then drops it), unless `mood` is on with a
  `weather_entity`: the engine then keeps it to read that entity every ten minutes.

## Where things live

Everything the add-on writes is under its data directory, which Home Assistant backs up with the
add-on (`backup: hot`, the render cache excluded):

| Path | What |
| :--- | :--- |
| `config.json` | Generated from the options on every start. |
| `secrets/captioner-<n>.key`, `secrets/mqtt.key` | Captioner keys and the MQTT broker password, mode 600. |
| `mqtt.json` | The retained MQTT topics published so far, so they can be removed when MQTT is turned off. |
| `state.json` | What is on screen, the shuffle bag, hidden and loved ids, the panel's rotation choice, whether quiet hours hold the picture, history. |
| `screens.json` | The Screens list: on or off, first and last seen (last seen is written at most every 5 minutes). |
| `library/<id>.json` | One sidecar per picture: the caption, its `tags`, and where it came from, the captioner's `subject` box and the `focus` point (`{"x", "y", "source", "sha256"}`, `source` = `hand`, `model` or `auto`), the picture's colours (`colours`: `palette`, `accent`, `gnome_accent`, the source hash and the method version they were computed with), and the kids caption in `kids_*` fields (`kids_blurb`; `"kids_edited": true` for one written by hand). A hand edit here (set `"edited": true`) is picked up within a minute. A sidecar without a `tags` key is queued for a tag-only request (`tags_failed_at` and `tags_error` after a failed one, retried like a failed caption). |
| `masters/` | The downscaled master copy of each picture. A picture's master goes after the first folder scan that no longer finds it, but not while any picture folder is missing or empty, so an unmounted share costs nothing. |
| `originals/`, `remote.json` | Pictures fetched from a remote manifest, the daily URL or an open-access art source, and their index (with each work's credit and facts). |
| `art-skipped.json` | Works an art source offered that will never do (not public domain, no image, too small), so they are not fetched again. |
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

**The picks do not follow the weather.** The Rotation box's Mood line says what the weather entity
last said, or why it could not be read (`cannot read weather.… (HTTP 404: …)` in the log means the
entity id is wrong; `HTTP 401` that the add-on has no API access). Pictures only match once they have
tags: `tagging` in `/library.json` counts the ones still waiting for them, and a card's tags are under its blurb.
With `mood: light` about one pick in four is deliberately not a match, and once every matching picture
has had its turn in the current shuffle, the rest follow until the next shuffle.

**The same image never changes.** Check how many images it actually found: the panel's library count,
or the `library: N items` line in the log. One usable image means no rotation. Pictures that are
hidden or over the size cap do not count. If the count is right, the rotation window may simply be
long; `POST /api/next` moves it on immediately.

**A picture never appears, and its card says "cannot be decoded".** The file is not a picture Pillow
can read, or it is cut short (an interrupted copy). Replace it; it is picked up again on the next folder
scan.

**A picture never appears, and its card says "image too large".** Matchy refuses to decode any source
over 50 megapixels (8K is 33), because the add-on has no memory limit and one huge file could exhaust
the host's memory. Scale that file down; it is picked up again on the next folder scan.

**Images look soft.** `master_max_px` caps the longest edge of the master copy at 3840 by default.
Raise it if your screens are bigger than 4K, and remember a bigger master costs memory per render
(three masters are held in memory at a time).

**A client shows an old picture.** Clients that use `/current/...` URLs must not cache them beyond
the `ETag`; clients that use `/img/<id>/<rev>/...` URLs must re-read `current.json` to learn the new
id. The sidebar panel does both for you.

**No Matchy device in Home Assistant.** `mqtt_enabled` must be on and the Mosquitto broker add-on
installed and started; the add-on log then says `mqtt: connected`. `no MQTT broker is set up` means
the Supervisor has no broker to offer (start Mosquitto, then restart Matchy); `connection refused:
bad user name or password` means the login the Supervisor handed out is not accepted, which a
restart of both add-ons usually settles. A device whose image entity stays empty cannot fetch the
picture: set `mqtt_image_url` to an address Home Assistant can reach.

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
  "focus_auto": false,
  "folder_scan_minutes": 10,
  "rotation": {"min_minutes": 15, "max_minutes": 20, "love_weight": 3},
  "quiet_hours": {"start": "23:00", "end": "07:00", "hold": true, "dim": 0.6},
  "location": {"latitude": 53.35, "longitude": -6.26},
  "mood": {"level": "off", "weather_entity": "weather.forecast_home"},
  "folders": [{"path": "/sources/wallpapers",
               "sets": {"art": {"id_prefix": "art", "name": "Classic Art", "abstract": false}}}],
  "exclude": [],
  "captioners": [
    {"name": "local", "url": "http://<host>:8080/v1/chat/completions", "model": "<model>",
     "api_key": "", "timeout": 120}
  ],
  "caption": {"enabled": true, "language": "English", "spelling": "British",
              "min_words": 30, "max_words": 45, "kids": {"enabled": false}},
  "palette": {"enabled": true},
  "static_manifest": {"enabled": false},
  "daily": {"enabled": false},
  "art_sources": {"aic": {"enabled": false, "per_day": 3, "min_px": 1686},
                  "met": {"enabled": false, "per_day": 3, "min_px": 2560},
                  "potd": {"enabled": false, "per_day": 1, "min_px": 2560}},
  "mqtt": {"enabled": false, "host": "<broker>", "port": 1883, "username": "", "password": "",
           "base_url": "http://<matchy-host>:8160"}
}
```

The add-on writes a captioner's key to a file and passes `api_key_file` instead of `api_key`; either
works here, and the same goes for the broker's `password` and `password_file`. Leave `location` out
and Day & night changes at 07:00 and 19:00. Leave `quiet_hours` out (or `{}`) for no quiet hours. Times
are local to the container, so set `TZ` (for example `-e TZ=Europe/London`) or they are UTC. For the Home
Assistant entities, set `mqtt.enabled`, the broker's details and `base_url` (the address Home Assistant can
fetch pictures from); `ssl` is not supported, so use the broker's plain port.
For the weather, the engine reads `mood.weather_entity` from Home Assistant's REST API: inside the
add-on through the Supervisor (`mood.api_url` defaults to `http://supervisor/core/api`, with the token
from its environment); elsewhere set `mood.api_url` to `http://<your Home Assistant address>:8123/api`
and `mood.api_token` to a long-lived access token (the token never reaches the log).

Keys the engine understands but the add-on does not expose:

- per folder: `credit`, `meta_file` (default `meta.json`), and `title` in a `sets` entry;
- per id: `overrides` (`{"<id>": {"place": "…"}}`);
- per captioner: `api_key_file`, `down_minutes` (10, how long a failing endpoint is skipped),
  `gap_seconds` (0, a pause after each caption) and `json_mode` (true; false never sends
  `response_format`);
- under `caption`: `max_tokens` (300), `temperature` (0.4), `image_px` (1024, the size sent to the
  model), `retry_failed_hours` (6, for kids captions and a failed tag request too), `forbidden_titles`, and `kids.min_words` /
  `kids.max_words` (15 and 30);
- under `art_sources`: `contact` (an e-mail address or URL added to the User-Agent; the project page
  is always there), `check_minutes` (60), `pause_seconds` (1, between two requests to one source);
  per source `base_url` (`api_url` for `potd`); `aic.batch` (20 works per search) and
  `aic.extra_filters` (Elasticsearch filters added to the search, e.g.
  `[{"term": {"is_on_view": true}}]`); `met.search` (extra search parameters,
  default `{"isHighlight": "true"}`; `""` drops one) and `met.lookups` (15 objects per check at
  most); `potd.days_back` (7) and `potd.language` (`en`, for the description);
- under `mood`: `api_url`, `api_token` (above) and `refresh_minutes` (10, how often the weather
  entity is read; never under a minute);
- under `mqtt`: `discovery_prefix` (`homeassistant`), `topic_prefix` (`matchy`, also the device
  identifier and the start of every unique id, so two engines on one broker need two prefixes) and
  `image_size` (`[1920, 1080]`, the labelled picture the image entity shows);
- `rotation.caption_priority`, `dedupe.avoid_prefixes` and `ladder_prefer` (`"4k"`), `lan_cidrs`,
  `ingress_peers`, `size_limits` (`max_pixels` included), `render_queue` (16), `bind` and `style` (label proportions, fonts, `jpeg_quality`, and
  `kids_scale`, 1.3, the label scale for `?label=kids` without `s`).

Environment: `WE_CONFIG`, `WE_PORT`, `WE_DATA_DIR`, `WE_FONT_DIR`, `WE_ACCESS_LOG`.

Without Home Assistant there is no sidebar panel, so the **Panel** tier (hide, unhide, edit caption,
re-caption, focus point, how often, forget a screen) answers `403` and the gallery page draws those buttons
disabled. To use them from a trusted machine, list its address in `ingress_peers`; that machine then
counts as the panel, so give it only to one you would hand the controls to.

## Licensing and credits

No images ship with this add-on. Images you point it at stay yours, and their licences are your
responsibility. The open-access art sources download public-domain works (CC0) from the two museums
and freely licensed pictures from Wikimedia Commons, each credited on its label; a Commons picture
keeps its own licence. The bundled fonts are Noto, under the SIL Open Font Licence 1.1; their copyright file
travels with the image at `/fonts/COPYRIGHT-fonts-noto-core`. The code licence is in the repository's
`LICENSE` file.
