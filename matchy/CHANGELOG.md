# Changelog

## Unreleased

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
- `run.sh` drops the Supervisor token from the engine's environment: the engine never uses it.

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
