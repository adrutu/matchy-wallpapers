# Changelog

## 0.5.0 (unreleased, on trial at home)

New features, all documented in DOCS.md; the client recipes are in `docs/clients`.

- **Quiet hours:** `quiet_hours` holds the picture overnight and dims `/current/...` (`start`, `end`,
  `hold`, `dim`). Off by default.
- **Focus points:** each screen's crop is centred on the picture's subject. Click a thumbnail in the
  panel to set one; with `focus_auto` on, the captioner or a quick look at the picture finds one.
- **Open-access art:** new pictures every day from the Art Institute of Chicago
  (`art_institute_chicago`), The Met (`the_met`) and Wikimedia's Picture of the Day
  (`wikimedia_picture_of_the_day`). Each is off by default. The Met adds paintings and drawings only
  (its highlights are mostly objects), and the Art Institute's smallest size defaults to 1686 px, the
  most it serves.
- **Gentle motion:** `?pad=1.0`–`1.3` serves a larger picture for a slow pan and zoom, with the label
  kept on screen. The Home Assistant, Wallpanel and Homepage recipes drift and crossfade.
- **Kids labels:** with `caption_kids` on, `?label=kids` serves a simpler blurb in bigger type.
- **Guess where?:** `?reveal=<seconds>` shows the clean picture first and the label later.
- **Loved pictures:** a heart in the panel, or `POST /api/love` from any client on your network. A loved
  picture comes up `love_weight` times as often (3 by default).
- **Voice:** a Home Assistant Assist recipe for "what's on the wall?", "next wallpaper" and a heart by
  voice (`docs/clients/home-assistant-assist.md`).
- **Colour match:** `palette`, `accent` and `gnome_accent` for every picture, in `/current.json` and
  `/library.json` (`palette_enabled`, on by default). The GNOME follower can set the desktop accent
  (off by default). The Home Assistant recipe can tint the theme and any lights that are already on.
- **Mood-aware picks:** `mood: light` or `strong` lets the time of day, the season and
  `weather_entity` choose the next picture, using tags you can edit in the panel. Off by default;
  pictures captioned before tags existed get theirs only once mood is on.
- **Home Assistant entities:** with `mqtt_enabled` on and the Mosquitto broker running, Matchy creates
  its own device by MQTT discovery: now showing, title, next, love, pause, rotation, one switch per
  screen and the caption queue. Off by default.

Fix found on trial: the `image` entity was refused by Home Assistant (`content_type` cannot be used
together with `url_topic`), so there was no *Now showing* entity.

Panel, from the trial: the picture on screen goes across the top, with a big **Next picture** button in
Rotation and *On screen now* between Rotation and Screens; each screen is one row; bigger type throughout.
The Captions box is gone: the captioner only speaks up when it is not set up or stopped answering, and
the Library summary counts pictures waiting for a caption.

Bug fixes from a review of the add-on; each has a test in `tests/run_tests.py`.

- A file named like a picture that Pillow cannot decode (not really an image, or cut short by an
  interrupted copy) stayed in rotation, and every render of it answered `500`. It is now kept out
  like a picture over 50 megapixels: the panel card says "cannot be decoded", `Show now` answers
  `409`, its renders and its original `400`, and if it was already on screen the rotation moves on.
- A file that disappeared or could not be read between the folder listing and its hash (moved
  during a long first scan) aborted the whole scan, so the library stayed empty or stale until the
  next one. It is now skipped and logged.
- A file or folder name that is not valid UTF-8 (old archives, a share in another character set)
  made every scan fail while saving the hash cache, and such a folder name broke `library.json`
  and the panel. The data files now keep those names as their own bytes.
- A `|` in a file name made the hash cache forget that file, so it was hashed again on every
  folder scan.
- Phone photos with an EXIF orientation showed sideways: masters are now turned upright.
- A 16-bit greyscale PNG rendered as plain white.
- A picture replaced by a new version under the same file name kept showing the old one wherever
  its label text did not change (captions off, or a caption written by hand): its renders and
  `/img/` URLs did not change. The `rev` in those URLs now follows the file too.
- After updating, every master is made once more in the background (so the two fixes above reach
  pictures already in the library), and every `rev` changes once; clients holding an old `/img/`
  URL get the usual `302` to the new one.
- Two requests that needed the same new master at once (a screen and the panel right after a new
  picture went up) could both write it, and one of them failed with `500`, or a caption was marked
  failed for six hours. Masters are now built one at a time.
- A render that failed left a lock behind for every size asked for, memory that grew with each
  request.
- `/current.json` could answer `500` while another request added a screen to the Screens list.
- A source folder that contains the add-on's own data (for example `/`) had its masters scanned as
  new pictures, one more set every scan. The data directory is now never scanned.
- Remote manifest: two file names that give the same id (names in a non-Latin script, or
  `Sunset.jpg` and `sunset.png`) were one picture, and it took the other one's title. The second
  now gets `-2`, `-3`…
- Daily picture: a JSON document with `"copyright": null` stopped the daily source.
- A keep-alive connection whose client disappeared held a server thread for good; idle
  connections now close after a minute.
- Masters of pictures taken out of the folders (deleted, renamed or excluded) stayed in `masters/`,
  and in every backup, for good. They are now removed after a folder scan, except while any picture
  folder is missing or empty, so a share that is briefly unmounted does not cost them.
- `tools/retro_themes.py` wrote `meta.json` in the system's encoding, which the engine (reading
  UTF-8) rejected on Windows, losing every title and credit.
- Docs: `caption_enabled: false` stops the captioner; it does not hide blurbs already written or
  written in the panel, as DOCS.md, the option's description and the panel said.

Robustness review of the engine's edges (`tests/security_test.py`, also in CI):

- `X-Ingress-Path` is honoured only from the Supervisor's address, and only as one plain absolute
  path; a direct request's header no longer reaches `Location` or the `urls` in `current.json`.
- Every response carries `X-Content-Type-Options: nosniff`.
- A hand-edited sidecar with the wrong type in a field (a number for `title`, text for
  `caption_failed_at`) degrades to text or is ignored instead of failing the page, the label, the
  library JSON or the caption worker.
- A captioner's reply is read up to 4 MB, and a title or blurb far longer than its word count allows
  is rejected like any other bad answer.
- Remote sources (`remote_manifest_url`, `daily_json_url`, `daily_image_url`) are fetched over
  `http(s)` only.
- `run.sh` drops the Supervisor token from the engine's environment, except when the mood-aware
  picks read a `weather_entity` (the only thing the engine uses it for; without it that read
  answered `401`).
- A picture removed from the library while a request for it was on its way (a folder scan in
  between) answers `404` instead of `500`.
- Render budget on the published port: one picture or span canvas is at most 40 megapixels
  (`size_limits.max_pixels`; 8K and three 4K screens fit), and past 16 renders waiting
  (`render_queue`) a new render answers `503` with `Retry-After`, so a loop of huge requests can no
  longer run the add-on out of memory. The GNOME follower scales a larger layout down to fit.
- A full Screens list takes at most one new name a minute, so a client cycling made-up `?screen=`
  names no longer rewrites `screens.json` on every request.
- `original.jpg` is streamed from disk, four at a time, instead of being read whole into memory for
  every request, and a JPEG with a GPS position in its EXIF is served without it (losslessly: the
  picture and the rest of its EXIF are unchanged).
- Remote sources can no longer be bent into a proxy for your network: a redirect from a manifest,
  daily or art source host may stay on that host or go to a public address, and an image address
  an art API hands back must be public. A `302` to the Supervisor or Home Assistant is refused.
- The panel page carries a Content-Security-Policy with a per-response nonce: only its own script
  and stylesheet run, images and requests stay on its origin.
- The captioner's prompt marks the picture's source text (titles, museum facts, Wikimedia's
  description) off as data in a fenced block, each value on one line and capped, so a source cannot
  pass the captioner instructions; the grounding check is unchanged.

- Colour match: each picture gets a five-colour `palette`, a usable `accent` (3:1 contrast on both
  light and dark) and the nearest `gnome_accent` name, computed once from the master into the sidecar
  and served in `current.json` and `library.json`; swatches under the picture in the panel. New option
  `palette_enabled` (default on). The GNOME follower can set the desktop's accent colour from it
  (`MATCHY_ACCENT=1`, off by default) and hands your own back when the screen is turned off or the
  service stops; the Home Assistant recipe gets a theme accent and a lights automation that only tints
  lights already on. `tests/palette_test.py`.

- Mood-aware picks: `mood: light` or `strong` lets the weather (a Home Assistant `weather_entity`,
  read every ten minutes through the add-on's API access), the season and the time of day steer which
  picture comes next; `off` (the default) is the plain shuffle as before. The captioner tags each
  picture with 3 to 6 words from a fixed list (stored in the sidecar; pictures captioned earlier get
  theirs through one tag-only request each, at the back of the queue); tags can be ticked by hand in
  the caption editor. Matching pictures are pulled forward within the shuffle, so every picture still
  comes up once per shuffle. The panel shows **Why this one** under the picture on screen and a Mood
  line in the Rotation box; `/current.json` gains `tags` and `mood_match`, `/library.json` `tags` per
  picture and a `mood` object. `tests/mood_test.py`, also in CI.

## 0.4.0 (2026-10-17)

First public release.

- Fix: a picture could answer `400` ("box offset can't be negative") at some sizes, about 4 % of
  picture and size pairs, so a screen got no wallpaper for that rotation and the panel showed a
  broken thumbnail. The crop box is now kept inside the picture; `tests/render_test.py` sweeps
  picture shapes against client sizes.
- Screens: names may use letters in any script (a Home Assistant user called Zoë was not listed).
- `sets`: `id_prefix` is turned into a URL-safe slug like the rest of the id, so a prefix with a
  space no longer breaks that set.
- The configuration tab names and explains every option (`translations/en.yaml`, checked against
  the Supervisor's schema in CI).
- New icon and logo, drawn from code (`tools/make_artwork.py`).
- Documentation checked line by line against the code: installation from zero (the local build,
  the picture folder before the first start, the sidebar toggle, 64-bit only), a captioner example,
  the HTTP API's edge cases, running without Home Assistant, and the per-tablet screensaver.
- Running without Home Assistant: the daily picture's default id prefix is `daily`, as in the add-on.

## 0.3.2 (2026-10-17)

- Renamed **Matchy Wallpapers** (repository `matchy-wallpapers`; "Matchy" stays the short name). The
  sidebar panel and the gallery page carry the full name. The slug stays `matchy`, so an install
  keeps its settings, captions and screens.
- `tools/retro_themes.py`: no brand names. The four sets are now *Games console*, *Instant camera*,
  *Videotape* and *Hi-fi* colours (folders `theme-console`, `theme-instant`, `theme-videotape`,
  `theme-hifi`), and titles and descriptions name the era instead of a product.

## 0.3.1 (2026-10-17)

- Captions: a blurb may use a place adjective or plural of a name in the known facts (Andes ->
  Andean, Iceland -> Icelandic, Cuba -> Cuban, Italy -> Italian) and the possessive of a known name
  (Church's); invented names are still rejected. `tests/caption_test.py`, also in CI.
- Client recipes: Screens in each one. The GNOME follower names itself `?screen=<hostname>` and
  puts back your own wallpaper while it is turned off; Homepage is the screen `homepage`; Home
  Assistant gets one switch and one screensaver image per tablet, so a tablet that is turned off no
  longer shows Matchy when it goes idle.

## 0.3.0 (2026-10-17)

- Screens: a client can name itself with `?screen=<name>` on any request, and the sidebar panel lists
  those screens (following or off) with **Turn off**, **Turn on** and **Forget**. Off is
  advisory: `/current.json` gains `screens_off` and, for a named screen, `follow`. New
  `GET /screens.json`, `POST /api/screen` (network tier) and `POST /api/screen/forget` (panel only).
  Only clients on the network tier are listed; `/screens.json` is network-tier and sends no CORS;
  a full list (24) drops the following screen unseen the longest.
- Rotation: a **How often** slider (every 15 minutes to every 12 hours) replaces the minutes form,
  which stays under **Custom window**; `POST /api/rotation` takes `{"mode": "daynight"}`.
- Day & night: one picture at sunrise, the next at sunset, at Home Assistant's location (read once at
  start through the new `homeassistant_api` access); 07:00 and 19:00 when the location is not known.

## 0.2.4 (2026-10-17)

- Health check: a Docker `HEALTHCHECK` on `/healthz` replaces the `watchdog:` URL, which the app
  linter reports as obsolete; the Watchdog toggle restarts an unhealthy container just the same.
- `backup`, `boot` and `startup` left at their defaults instead of restating them.
- A missing label font falls back to Pillow's built-in font (logged once) instead of failing the
  render with HTTP 500; `tests/ci_probe.py` now checks the font files and families itself.

## 0.2.3 (2026-10-17)

- `sets` option: per sub-folder id prefix, collection name, credit, context and `abstract`, as the
  engine config already allowed.
- `tests/run_sh_test.py`: the add-on's option handling tested with bashio stubbed (16 checks), also in CI.

## 0.2.2 (2026-10-16)

- `meta.json` next to the pictures (title, place, credit, context per file) grounds the captions and
  fills the label, as in the engine this add-on came from.
- `exclude` option: id globs kept out of the library.

## 0.2.1 (2026-10-16)
- `watchdog:` on `/healthz` (active once the Watchdog toggle is on).
- Captioner: works with servers that refuse `response_format: json_object` (LM Studio), `max_tokens`
  or a non-default `temperature` (newer OpenAI models): the parameter a `400`/`422` names is dropped
  or renamed for that endpoint and the request retried. Replies in content parts or with an inline
  `<think>` block are read; an empty reply says so (`finish_reason`) instead of failing to parse.
- Captioner errors are logged with the server's own reason (`HTTP 404: …`), and with every endpoint
  skipped, captioning resumes as soon as the first skip runs out instead of a minute later.
- Tests for a picky server (key inline, all three refusals, content parts, `<think>`) and for every
  captioner unreachable: the wallpaper keeps rotating uncaptioned and captions arrive once one is back.
- Integration tests in the repository (`tests/run_tests.py`, 63 checks against the real engine) and a
  cross-architecture label diff (`tests/label_diff.py`, `tests/ci_probe.py`).
- The image builds natively on amd64 and aarch64 from the same Dockerfile; the four fonts load and
  the label render is byte-identical on both.
- Documentation: DOCS.md rewritten against the code (options, API, access tiers, data layout,
  running without Home Assistant), client recipes under `docs/clients/`, `docs/DECISIONS.md`.
- A GitHub Actions workflow (lint, tests, per-arch image build and render probe, cross-arch diff;
  publishing opt-in and off). Not yet run anywhere.

## 0.2.0 (2026-10-13)

- Panel controls: pause/resume, show now, hide/unhide, edit caption by hand, re-caption, and a
  rotation window set from the panel that lasts until the add-on options change.
- Access tiers: next/pause/resume/show from any private address or the panel; hide, unhide, caption,
  re-caption and rotation from the sidebar panel only. Control endpoints require
  `Content-Type: application/json`; `OPTIONS` on `/api/` sends no CORS headers.
- Source images over 50 megapixels are refused, kept out of rotation and of the controls, and the
  gallery card says why.
- The engine binds its port before the first scan, checkpoints the hash cache every ten seconds
  during a cold scan, and re-reads a captioner key file after a 401/403.
- The sidebar page follows the rotation (reloads when `version` changes).

## 0.1.0 (2026-10-13)

- First cut of the Home Assistant add-on: `config.yaml` with the option schema, `run.sh` turning the
  options into the engine's `config.json` (captioner keys to mode-600 files), a Debian-based
  multi-arch Dockerfile with four Noto faces and libraqm, store artwork.
- Ingress: relative URLs on the gallery page, `X-Ingress-Path` prefixing in `current.json`, the
  Supervisor's address trusted for `/api/next`.
- Rotation without captions: with no captioner configured every picture rotates; captioned pictures
  take over as captions arrive.
