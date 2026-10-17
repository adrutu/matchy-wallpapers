# Decisions

Things only the owner could settle before Matchy went anywhere beyond this house, with the options
that were weighed. **All six were answered on 2026-10-16:**

1. Licence: **MIT**.
2. Name: **Matchy**, kept after a clash check (no Home Assistant add-on or integration uses the
   slug; the nearest is an unrelated US trademark for a mobile puzzle game). Placeholder artwork stays.
   **Renamed 2026-10-17 to Matchy Wallpapers** (repository `matchy-wallpapers`) to keep clear of that
   trademark; "Matchy" stays as the short name, and the add-on slug stays `matchy`, because a new slug
   is a new add-on to Home Assistant and every install would start over.
3. Container user: **A, stay root**, like official add-ons.
4. Repository: **private on GitHub** (`adrutu/matchy-wallpapers`, `adrutu/matchy` until 2026-10-17)
   for now; no images published.
5. `POST /api/next` without a body: **kept**.
6. The existing install and the add-on run **side by side for a few days** first; each client moves
   only on the owner's say-so.

## 1. Licence (ticket #131)

The repository ships `LICENSE` as **MIT, Copyright (c) 2026 Andrei Drutu**, chosen provisionally;
`Dockerfile` carries `org.opencontainers.image.licenses="MIT"` and README says "pending confirmation".

| Option | For | Against |
| :--- | :--- | :--- |
| **MIT** (current) | Shortest, most familiar, no friction for anyone (including Home Assistant community add-on repos, which are mostly MIT). | No patent grant; anyone may close-source a fork. |
| **Apache-2.0** | Explicit patent grant and contributor terms; still permissive. | Longer text, needs a `NOTICE` file for attributions; same fork freedom as MIT. |
| **MPL-2.0** | File-level copyleft: changes to these files must stay open, but it can live inside a larger closed product. | Less familiar; some people avoid anything with "copyleft" in it. |
| **GPL-3.0** / **AGPL-3.0** | Whole derived works must stay open (AGPL: even when only run as a network service). | Keeps it out of permissive-only collections; AGPL in particular puts off integrators. |

Bundled content is unaffected: the Noto fonts are OFL-1.1 whatever the code licence, Pillow is
MIT-CMU, libraqm MIT. No pictures ship.

**Decided 2026-10-16: MIT.**

## 2. Name and artwork

- Name **Matchy** (title "Matchy Wallpapers", slug `matchy`, tagline "One wallpaper, matched across
  every screen"). Not checked against existing projects, trademarks or the HA add-on store beyond a
  glance; a clash would mean renaming the slug, which every install keys on, so settle it before the
  first public install.
- Artwork: `matchy/icon.png` (128x128) and `matchy/logo.png` (250x100) are generated placeholders
  drawn in the label's own style (three screens of different shapes showing one picture), with
  larger copies under `docs/`. Options: keep them; redraw by hand or commission; or a wordmark only.
  Nothing in the code depends on them.

**Decided 2026-10-16: keep Matchy and the placeholder artwork.** Clash check the same day: no `matchy`
slug in the official, community or HACS lists or in Home Assistant's add-on analytics; `matchy` is
taken on PyPI, npm and crates.io by unrelated projects; US trademark MATCHY (reg. 7385917, class 9)
covers a mobile puzzle game.

## 3. Container user: root or not (ticket #130)

The engine runs as **root** inside its container today, like official Home Assistant add-ons; the
Supervisor has no option to change the user, gives every add-on an unconfined seccomp profile and
Docker's default capabilities. The full write-up with evidence is on #130.

| Option | For | Against |
| :--- | :--- | :--- |
| **A. Stay root** (current) | Nothing to change; identical to official add-ons; no data migration. | A bug in the image decoders that parse your files runs as uid 0 with CHOWN/DAC_OVERRIDE/SETUID and no seccomp. |
| **B. Drop to a fixed uid in `run.sh`** (`s6-setuidgid`, prototype works, label byte-identical) | An exploited decoder can only write `/data`; about 15 lines plus a test. | A `chown -R /data` on every start (seconds at most); files copied into `/media` as mode 600 become unreadable and must be logged; a one-way ownership change for existing installs, so it belongs in a minor release (0.3.x), not a patch. |

Recommendation on the ticket: **B, in 0.3.x**, after a test on the real install. DOCS.md states the
current posture either way.

**Decided 2026-10-16: A, stay root.**

## 4. Public or private repository, and where

The canonical repository is a local one (no remote). `config.yaml`, `repository.yaml` and the
Dockerfile already name `https://github.com/adrutu/matchy` as the URL, unverified.

| Option | For | Against |
| :--- | :--- | :--- |
| **Private on GitHub first** | Off-box backup, CI runs (the workflow is written but has never run), issues in one place; nothing visible until flipped. | Private repositories cannot be added to the HA add-on store by others, so it proves CI, not distribution. |
| **Public on GitHub** | Installable by URL from the add-on store; GHCR images free. | Everything in the history is public from then on (scanned 2026-10-15: no addresses, keys or domain names in any version of any file; the only `192.168.0.0/16` string is the RFC 1918 range in the defaults; one commit message names a local machine by its nickname). |
| **Self-hosted git only** | Stays home. | No GitHub Actions, no store install for anyone else, no GHCR. |

Related choices that fall out of this one:

- **Image registry:** leave `image:` commented (the Supervisor builds locally on install, ~50 s on
  amd64), or publish a multi-arch manifest and set `image: "ghcr.io/<owner>/matchy"` with the tag
  equal to `version`. The workflow's publish job is **off** until the repository variable
  `MATCHY_IMAGE` is set.
- **Repository URL** in `config.yaml`, `repository.yaml`, the Dockerfile labels and README.
- **Maintainer line** in `repository.yaml` (currently a name, no e-mail, on purpose).

**Decided 2026-10-16: private on GitHub as `adrutu/matchy`, no images published** (`MATCHY_IMAGE`
unset, `image:` stays commented). Going public is a separate decision.

## 5. `POST /api/next` without a JSON body

For older clients `/api/next` accepts a bare `POST` with no body and no content type. That is what
lets a plain `rest_command` in Home Assistant call it, and also what lets any web page open in a
browser on your LAN trigger it (no CORS preflight for a bodyless POST). Every other control requires
`Content-Type: application/json`, which closes that door.

| Option | For | Against |
| :--- | :--- | :--- |
| **Keep it** (current) | Backwards compatible; worst case is a skipped picture. | A deliberate inconsistency, documented as a known gap. |
| **Require JSON like the others** | One rule for every control. | Breaks bodyless callers; the HA recipe in `docs/clients/home-assistant.md` needs `content_type` and `payload: "{}"` added. |

**Decided 2026-10-16: keep it.**

## 6. The live installation

The Pi stack this add-on was spun out of still runs the older copy of the engine for the tablets,
laptops and dashboard, and the add-on's port on Home Assistant is not published. Either the Pi moves
onto this code (one engine, one set of captions) or the two stay separate. Not a repository decision,
but it decides which install the client recipes point at.

**Decided 2026-10-16: side by side for a few days first.** The plan lives with the installation, not here.
