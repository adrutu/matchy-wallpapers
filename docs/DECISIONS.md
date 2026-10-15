# Open decisions

Things only the owner can settle before Matchy goes anywhere beyond this house. Each one lists the
options with a one-line trade-off; the current state of the repository is marked. Answer them in one
go and the rest is mechanical.

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

**Needed:** confirm MIT, or name another; then `LICENSE`, the Dockerfile label and README change
together.

## 2. Name and artwork

- Name **Matchy** (title "Matchy Wallpapers", slug `matchy`, tagline "One wallpaper, matched across
  every screen"). Not checked against existing projects, trademarks or the HA add-on store beyond a
  glance; a clash would mean renaming the slug, which every install keys on, so settle it before the
  first public install.
- Artwork: `matchy/icon.png` (128x128) and `matchy/logo.png` (250x100) are generated placeholders
  drawn in the label's own style (three screens of different shapes showing one picture), with
  larger copies under `docs/`. Options: keep them; redraw by hand or commission; or a wordmark only.
  Nothing in the code depends on them.

**Needed:** keep the name or not; keep the artwork or replace it (and with what).

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

**Needed:** A or B, and if B, whether 0.3.0 may ship it.

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

**Needed:** private, public or neither; the owner/name; whether to publish images.

## 5. `POST /api/next` without a JSON body

For older clients `/api/next` accepts a bare `POST` with no body and no content type. That is what
lets a plain `rest_command` in Home Assistant call it, and also what lets any web page open in a
browser on your LAN trigger it (no CORS preflight for a bodyless POST). Every other control requires
`Content-Type: application/json`, which closes that door.

| Option | For | Against |
| :--- | :--- | :--- |
| **Keep it** (current) | Backwards compatible; worst case is a skipped picture. | A deliberate inconsistency, documented as a known gap. |
| **Require JSON like the others** | One rule for every control. | Breaks bodyless callers; the HA recipe in `docs/clients/home-assistant.md` needs `content_type` and `payload: "{}"` added. |

**Needed:** keep or tighten (if tightened, in which release).

## 6. The live installation

The Pi stack this add-on was spun out of still runs the older copy of the engine for the tablets,
laptops and dashboard, and the add-on's port on Home Assistant is not published. Either the Pi moves
onto this code (one engine, one set of captions) or the two stay separate. Not a repository decision,
but it decides which install the client recipes point at.
