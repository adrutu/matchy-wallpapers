# Any client: the API with `curl`

`MATCHY` below is where you can reach the add-on's published port, e.g. `http://<your-ha-host>:<port>`.

```bash
MATCHY='http://<your-ha-host>:<port>'   # replace with your address, e.g. http://homeassistant.local:8160
```

## What is on screen

```bash
curl -s $MATCHY/current.json
```

```json
{
 "id": "art-great-wave", "rev": "73bdfe", "version": "art-great-wave.73bdfe.1760000000",
 "title": "The Great Wave off Kanagawa", "place": "", "credit": "Katsushika Hokusai",
 "blurb": "…", "since": "2026-10-15T10:12:30+01:00", "next_change": "2026-10-15T10:29:48+01:00",
 "paused": false, "captioned": true,
 "urls": {
  "original": "/img/art-great-wave/original.jpg",
  "clean": "/img/art-great-wave/73bdfe/clean/{w}x{h}.jpg",
  "label": "/img/art-great-wave/73bdfe/label/{w}x{h}.jpg",
  "span": "/img/art-great-wave/73bdfe/span.jpg?layout={layout}"
 },
 "screens_off": []
}
```

Compare `version` between polls: it changes on every rotation and every caption edit. The `urls`
are templates; fill in `{w}` and `{h}` (320–7680) or `{layout}`. They are root-absolute paths on the
published port. `503` means the library is still empty (first scan running).

## Fetch a picture

```bash
# the picture on screen now, with the label, at 2560x1440; the label 35 % larger (s: 0.6-2.0)
curl -s -o wall.jpg "$MATCHY/current/label/2560x1440.jpg?s=1.35"

# the same without the label
curl -s -o wall.jpg "$MATCHY/current/clean/2560x1440.jpg"

# a specific picture by id and caption revision (cacheable for a year; 302 to the new rev if the
# caption was edited since)
curl -sL -o wall.jpg "$MATCHY/img/art-great-wave/73bdfe/label/1920x1080.jpg"

# one composite for two monitors: a portrait 1200x1920 on the left, a 3440x1440 primary (p, gets the
# label) to its right; the "+" must be URL-encoded as %2B
curl -s -o span.jpg "$MATCHY/img/art-great-wave/73bdfe/span.jpg?layout=1200x1920%2B0%2B0,3440x1440%2B1200%2B0p"

# the untouched original
curl -s -o original.jpg "$MATCHY/img/art-great-wave/original.jpg"
```

## Poll cheaply with the ETag

`/current/...` URLs send an `ETag` and answer `304` until the picture changes, so a loop can ask every
minute and download only on a change:

```bash
etag=""
while true; do
  code=$(curl -s -o new.jpg -w '%{http_code}' -D headers.txt \
         -H "If-None-Match: $etag" "$MATCHY/current/label/1920x1080.jpg")
  if [ "$code" = 200 ]; then
    etag=$(sed -n 's/^[Ee][Tt]ag: *//p' headers.txt | tr -d '\r')
    mv new.jpg wall.jpg
    echo "new wallpaper $(date +%T)"        # set it with your desktop's tool here
  fi
  sleep 60
done
```

A `HEAD` request works on every `GET` URL if you only want the headers.

## Controls

These need a private source address (your LAN, Tailscale, or the sidebar panel); from elsewhere
they answer `403`. JSON endpoints must be sent as `application/json`.

```bash
# next picture now (answers the new current.json; a bare POST is accepted here)
curl -s -X POST $MATCHY/api/next

# hold the picture on screen / rotate again
curl -s -X POST -H 'Content-Type: application/json' -d '{}' $MATCHY/api/pause
curl -s -X POST -H 'Content-Type: application/json' -d '{}' $MATCHY/api/resume

# put one picture on screen (ids from library.json)
curl -s -X POST -H 'Content-Type: application/json' -d '{"id": "art-great-wave"}' $MATCHY/api/show
```

Hide, unhide, edit caption, re-caption and the rotation window work only from the Home Assistant
sidebar panel (`403 Home Assistant sidebar panel only` from anywhere else); see *Access tiers* in
[DOCS.md](../../matchy/DOCS.md#access-tiers).

## Screens (0.3.0)

Add `?screen=<name>` to any `GET` and the client shows up in the panel's **Screens** box. The name is 1
to 40 characters of letters, digits, space and `. _ : @ -`, starting with a letter or digit (quote a
space as `%20`). Only clients with a private address are listed.

```bash
# this client is the screen "desk"; with ?screen=, current.json also answers "follow"
curl -s "$MATCHY/current.json?screen=desk"

# every screen: id, follow, first_seen, last_seen, age_s, ip, agent, image (private addresses only)
curl -s $MATCHY/screens.json

# turn "desk" off, then on again (404 for a name Matchy has never seen)
curl -s -X POST -H 'Content-Type: application/json' -d '{"id": "desk", "follow": false}' $MATCHY/api/screen
curl -s -X POST -H 'Content-Type: application/json' -d '{"id": "desk", "follow": true}' $MATCHY/api/screen
```

Off is advisory: Matchy keeps serving "desk", and `current.json?screen=desk` answers `"follow": false`.
Every `current.json` also lists the screens that are off in `"screens_off"`. What off means is up to
the client: the GNOME follower puts back the wallpaper it found, a dashboard drops the background.
**Forget** (dropping a screen from the list) is in the sidebar panel only.

## The library

```bash
curl -s $MATCHY/library.json | python3 -c 'import json,sys; [print(i["status"], i["id"], "-", i["title"]) for i in json.load(sys.stdin)["items"]]'
curl -s $MATCHY/healthz        # ok
```
