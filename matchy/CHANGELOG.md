# Changelog

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
