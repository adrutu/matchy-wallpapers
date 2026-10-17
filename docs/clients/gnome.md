# GNOME desktop: `wallpaper-follow`

A small user service that keeps a GNOME desktop on Matchy's current picture. It reads the live
monitor layout from Mutter, asks the engine for **one spanned JPEG** that covers every monitor
(label on the primary screen, clean crops on the others), and sets it as a `spanned` background.
GNOME has no per-monitor wallpaper, so the composite is how every screen gets a proper crop.

Files in this folder:

- [`gnome/wallpaper-follow`](gnome/wallpaper-follow): the client (Python 3, uses the `gi` bindings
  that ship with Ubuntu GNOME and Fedora Workstation; nothing to install).
- [`gnome/wallpaper-follow.service`](gnome/wallpaper-follow.service): the systemd user unit.

## What it does

Every 30 seconds, and 3 seconds after a monitor change:

1. `GetCurrentState` from `org.gnome.Mutter.DisplayConfig` (read-only; it never changes your layout).
2. Turns it into the engine's span spec, e.g. `1200x1920+0+0,3440x1440+1200+0p` (canvas pixels,
   `p` = the primary monitor, which gets the label). Logical layout mode and scaled monitors are
   handled: the canvas is the logical layout times the largest scale, so the highest-DPI screen is
   1:1.
3. Reads `current.json`. When the `version` or the layout changed, downloads
   `/img/<id>/<rev>/span.jpg?layout=<spec>` to `~/.local/share/backgrounds/matchy/<version>-<layouthash>.jpg`
   (a new file name each time, because GNOME caches by URI) and sets `picture-uri`,
   `picture-uri-dark` and `picture-options=spanned` in `org.gnome.desktop.background`.
4. Keeps the current and previous file, plus the newest image of other recent layouts (six layouts
   in all), and deletes the rest.

Engine unreachable (asleep, or the laptop is away): nothing changes, one journal line per outage,
quiet retries. If the monitors changed meanwhile (undocked, then woke up elsewhere) it reuses the
newest saved image made for that exact layout rather than leaving a stretched span.

Before its first change it writes `~/.local/share/backgrounds/matchy-undo.txt` with the `gsettings`
lines that restore what you had. It never touches the lock screen (`org.gnome.desktop.screensaver`).

## Install

```bash
install -Dm755 docs/clients/gnome/wallpaper-follow ~/.local/bin/wallpaper-follow
install -Dm644 docs/clients/gnome/wallpaper-follow.service ~/.config/systemd/user/wallpaper-follow.service
# your Matchy address: Home Assistant's address and the host port you published for the add-on
# (or the Docker port); change homeassistant.local:8160 if yours differs
sed -i 's#http://<your-ha-host>:<port>#http://homeassistant.local:8160#' ~/.config/systemd/user/wallpaper-follow.service
systemctl --user daemon-reload
systemctl --user enable --now wallpaper-follow
```

Check it:

```bash
wallpaper-follow --spec                 # prints the layout spec it would send, e.g. 2560x1440+0+0p
MATCHY_URL=http://<your-ha-host>:8160 wallpaper-follow --once   # one tick, logs what it did
journalctl --user -u wallpaper-follow -f
```

A rotation reaches the desktop within about 30 seconds.

## Pause and undo

- Pause without stopping the service: `mkdir -p ~/.config/matchy && touch ~/.config/matchy/disabled`.
  Remove the file to resume.
- Undo completely:

```bash
systemctl --user disable --now wallpaper-follow
bash ~/.local/share/backgrounds/matchy-undo.txt
```

## Screens (0.3.0)

Every request names the machine with `?screen=<hostname>`, so it shows up in the panel's **Screens**
box under its host name. **Turn off** there and within a tick the follower puts back the wallpaper
saved in `~/.local/share/backgrounds/matchy-undo.txt` and stands by. **Turn on** and it saves the undo
file again (from whatever wallpaper you picked meanwhile) and puts the current picture back at once. With no undo
file it leaves the wallpaper as it is while off.

Turning the machine off in Matchy and the local pause file are separate: the pause file stops the
follower from asking at all, so Matchy stops seeing that machine.

## Notes

- The engine's limits apply to the span: each monitor side 320–7680 px, canvas up to 12000x8000,
  at most 8 monitors. A layout larger than that is scaled down proportionally (GNOME stretches the
  spanned picture over the stage anyway).
- If Matchy is behind a reverse proxy with TLS, give `MATCHY_URL` the `https://` name; nothing else
  changes.
- The `version` field in `current.json` changes on every rotation and on every caption edit, so an
  edited label shows up on the desktop within a tick.
