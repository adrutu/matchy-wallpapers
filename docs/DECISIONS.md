# Decisions

Choices made before the first public release, with the options that were weighed.

1. Licence: **MIT**.
2. Name: **Matchy Wallpapers**, "Matchy" for short; the add-on slug is `matchy`.
3. Container user: **root**, like official add-ons.
4. Repository: **GitHub, `adrutu/matchy-wallpapers`**; no pre-built images (Home Assistant builds the
   add-on on install).
5. `POST /api/next` ignores its body: **kept** for simple callers.

## 1. Licence

| Option | For | Against |
| :--- | :--- | :--- |
| **MIT** (chosen) | Shortest, most familiar, no friction for anyone (including Home Assistant community add-on repos, which are mostly MIT). | No patent grant; anyone may close-source a fork. |
| **Apache-2.0** | Explicit patent grant and contributor terms; still permissive. | Longer text, needs a `NOTICE` file for attributions; same fork freedom as MIT. |
| **MPL-2.0** | File-level copyleft: changes to these files must stay open, but it can live inside a larger closed product. | Less familiar; some people avoid anything with "copyleft" in it. |
| **GPL-3.0** / **AGPL-3.0** | Whole derived works must stay open (AGPL: even when only run as a network service). | Keeps it out of permissive-only collections; AGPL in particular puts off integrators. |

Bundled content is unaffected: the Noto fonts are OFL-1.1 whatever the code licence, Pillow is
MIT-CMU, libraqm MIT. No pictures ship.

## 2. Name and artwork

The project started as **Matchy** (tagline "One wallpaper, matched across every screen"). A clash
check found no `matchy` slug in the official, community or HACS add-on lists or in Home Assistant's
add-on analytics; the name is taken on PyPI, npm and crates.io by unrelated projects, and a US
trademark MATCHY (reg. 7385917, class 9) covers a mobile puzzle game. To stay clear of that, the full
name is **Matchy Wallpapers** (repository `matchy-wallpapers`), with "Matchy" as the short name.

The slug stays `matchy`: a different slug is a different add-on to Home Assistant, so every install
would start over (settings, captions and screens).

The icon and logo are drawn from code by `tools/make_artwork.py`: a monitor and a portrait tablet
showing the same picture, each cropped to its own shape, one with the gallery label in its corner.

## 3. Container user: root or not

The engine runs as **root** inside its container, like official Home Assistant add-ons. The
Supervisor has no option to change the user, and gives every add-on an unconfined seccomp profile and
Docker's default capabilities.

| Option | For | Against |
| :--- | :--- | :--- |
| **A. Stay root** (chosen) | Nothing to change; identical to official add-ons; no data migration. | A bug in the image decoders that parse your files runs as uid 0 with CHOWN/DAC_OVERRIDE/SETUID and no seccomp. |
| **B. Drop to a fixed uid in `run.sh`** (`s6-setuidgid`; a prototype worked, labels byte-identical) | An exploited decoder can only write `/data`; about 15 lines plus a test. | A `chown -R /data` on every start; files copied into `/media` as mode 600 become unreadable and must be logged; a one-way ownership change for existing installs. |

`/media` and `/share` are mounted read-only either way, and pictures over 50 megapixels are never
decoded. DOCS.md states the posture.

## 4. Repository and images

| Option | For | Against |
| :--- | :--- | :--- |
| **GitHub** (chosen) | Installable by URL from the add-on store; CI on every push (lint, tests, an amd64 and an aarch64 build). | Everything in the history is public once the repository is. It was checked for addresses, keys, host names and personal details before release. |
| **Self-hosted git only** | Stays home. | No store install for anyone else, no hosted CI. |

Images: `image:` stays commented in `config.yaml`, so the Supervisor builds the add-on locally on
install (about a minute on amd64, longer on a Raspberry Pi). Publishing a multi-arch image instead
means setting `image: "ghcr.io/<owner>/matchy-wallpapers"` with the tag equal to `version`; the
workflow's publish job stays off until the repository variable `MATCHY_IMAGE` is set.

## 5. `POST /api/next` and its body

`/api/next` ignores its body and content type, so a plain `rest_command` in Home Assistant, or a
`curl -X POST`, can call it. That also lets any web page open in a browser on your network trigger it
(a simple POST needs no CORS preflight). Every other control requires `Content-Type:
application/json`, which closes that door.

| Option | For | Against |
| :--- | :--- | :--- |
| **Keep it** (chosen) | Simple callers work; the worst case is a skipped picture. | A deliberate inconsistency, documented as a known gap. |
| **Require JSON like the others** | One rule for every control. | Breaks bodyless callers; every recipe needs `content_type` and `payload: "{}"`. |
