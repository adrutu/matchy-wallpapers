# Home Assistant dashboards and wall tablets

Three pieces, each optional, a fourth for the Screens list (section 4) and a fifth for gentle motion
(section 5):

1. A **package** that turns `current.json` into entities: a sensor with the picture's title, blurb
   and image URLs, an `image` entity with the labelled render, and a *Next wallpaper* script.
2. A **theme** with a [card-mod](https://github.com/thomasloven/lovelace-card-mod) view rule that
   paints the clean picture behind a dashboard view and follows the rotation live, with no theme
   reload.
3. A **Wallpanel** screensaver ([j-a-n/lovelace-wallpanel](https://github.com/j-a-n/lovelace-wallpanel),
   via HACS) that shows the labelled picture when a wall tablet goes idle.

Replace `<matchy-url>` with where Home Assistant can reach the add-on's published port, for example
`http://<your-ha-host>:<port>`. Two addresses may be needed: Home Assistant fetches `current.json` and
the labelled image **server-side**, so a plain `http://` LAN address is fine there; the card-mod
background is loaded by the **browser**, so if your dashboards are served over `https://` that URL
must be `https://` too (put Matchy behind your reverse proxy), or the browser blocks it as mixed
content.

## 1. Package: `packages/matchy.yaml`

Needs `packages: !include_dir_named packages` under `homeassistant:` in `configuration.yaml`.

```yaml
# Matchy: GET /current.json, POST /api/next; rotates every 15-20 min.
rest:
  - resource: <matchy-url>/current.json
    scan_interval: 60
    timeout: 10
    sensor:
      # Raw feed. A REST sensor cannot template its attributes, so the URLs are built below.
      - name: Matchy feed
        unique_id: matchy_feed
        icon: mdi:image-sync
        value_template: "{{ value_json.version }}"
        json_attributes:
          - id
          - rev
          - title
          - place
          - credit
          - blurb
          - since
          - next_change
          - urls

template:
  - sensor:
      # State = the engine's `version` (changes on every rotation or caption edit).
      - name: Matchy
        unique_id: matchy
        icon: mdi:image-frame
        state: "{{ states('sensor.matchy_feed') }}"
        availability: >-
          {{ states('sensor.matchy_feed') not in ['unknown', 'unavailable']
             and state_attr('sensor.matchy_feed', 'urls') is mapping }}
        attributes:
          id: "{{ state_attr('sensor.matchy_feed', 'id') }}"
          title: "{{ state_attr('sensor.matchy_feed', 'title') }}"
          place: "{{ state_attr('sensor.matchy_feed', 'place') }}"
          credit: "{{ state_attr('sensor.matchy_feed', 'credit') }}"
          blurb: "{{ state_attr('sensor.matchy_feed', 'blurb') }}"
          since: "{{ state_attr('sensor.matchy_feed', 'since') }}"
          next_change: "{{ state_attr('sensor.matchy_feed', 'next_change') }}"
          # For browsers (the card-mod background): the clean picture at a tablet-friendly size.
          # Use an https:// address here if your dashboards are https.
          background_url: >-
            {% set u = state_attr('sensor.matchy_feed', 'urls') %}
            {{ '<matchy-url>' ~ (u.clean | replace('{w}', '2560') | replace('{h}', '1600'))
               if u is mapping and u.clean is defined else none }}
          # For Home Assistant (fetched server-side by image.matchy_label): the labelled picture,
          # s=1.35 = a label scale that reads well on a wall tablet.
          label_url: >-
            {% set u = state_attr('sensor.matchy_feed', 'urls') %}
            {{ '<matchy-url>' ~ (u.label | replace('{w}', '1920') | replace('{h}', '1200')) ~ '?s=1.35'
               if u is mapping and u.label is defined else none }}

  - image:
      # Home Assistant proxies it (/api/image_proxy), so https dashboards get no mixed content. Its
      # state changes only when label_url changes, which is what makes Wallpanel show the new picture.
      - name: Matchy label
        unique_id: matchy_label
        url: "{{ state_attr('sensor.matchy', 'label_url') }}"
        availability: "{{ state_attr('sensor.matchy', 'label_url') is string }}"

rest_command:
  matchy_next:
    url: <matchy-url>/api/next
    method: POST
    timeout: 15

script:
  matchy_next:
    alias: Next wallpaper
    icon: mdi:skip-next
    description: Rotate Matchy now; every screen follows within about a minute.
    mode: single
    sequence:
      - action: rest_command.matchy_next
      - delay: "00:00:02"
      - action: homeassistant.update_entity
        target:
          entity_id: sensor.matchy_feed
```

The `urls` attribute holds templates (`/img/<id>/<rev>/clean/{w}x{h}.jpg`), which is why the two
URLs are built with `replace`. Pick sizes that match your tablets; the engine renders any size from
320 to 7680 on a side and caches it. `POST /api/next` works from Home Assistant because it comes
from a private address (the *Network* tier in [DOCS.md](../../matchy/DOCS.md#access-tiers)). To pause or show a specific picture from an
automation, add `rest_command`s for `/api/pause`, `/api/resume` and `/api/show` with
`content_type: "application/json"` and a JSON `payload`.

## 2. Theme: a view background that follows the rotation

card-mod reads a `card-mod-view` rule from the **view's** theme and re-renders its template on every
state change of the entities it references, so the background changes with the sensor and no theme
reload is needed. Add this to a theme file under `themes/` (and `frontend: themes: !include_dir_merge_named themes`):

```yaml
Matchy Background:
  card-mod-theme: Matchy Background
  # Layers: a dark tint for legibility, the live picture, and a fixed fallback image for when the
  # sensor is unavailable or the browser cannot load the picture (off the LAN, engine down).
  card-mod-view: |
    {%- set u = state_attr('sensor.matchy', 'background_url') %}
    {%- if u and states('sensor.matchy') not in ['unknown', 'unavailable'] %}
    hui-view-background {
      --view-background: linear-gradient(rgba(0, 0, 0, 0.22), rgba(0, 0, 0, 0.22)) fixed,
                         center / cover no-repeat fixed url('{{ u }}'),
                         center / cover no-repeat fixed url('/local/fallback.jpg') !important;
    }
    {%- endif %}
```

Then set the theme on each dashboard **view** (view settings → Theme), not only on the user profile:
card-mod resolves `card-mod-view` from the view theme. If you already use a card-mod theme (a
"frosted glass" style, say), append the rule to its existing `card-mod-view` block instead of making
a new theme; a view takes one theme. Reload themes once after editing the file.

Tiles cover most of the picture on a busy dashboard, which is why the background uses the clean
render; the label is for the screensaver.

## 3. Wallpanel screensaver with the label

With Wallpanel installed from HACS, add to the dashboard's YAML (raw configuration editor, top
level). Off by default, then on only for the tablet's user, so phones and desktops never get it:

```yaml
wallpanel:
  enabled: false
  alert_errors: false            # no alert() boxes on a wall tablet; errors still reach the HA log
  idle_time: 600                 # seconds before the screensaver starts
  image_url: media-entity-image://image.matchy_label
  media_entity_load_unchanged: false
  display_time: 3600
  image_fit_landscape: cover
  image_fit_portrait: cover
  crossfade_time: 3
  fade_in_time: 3
  show_image_info: false
  show_progress_bar: false
  badges: []
  cards: []
  views: []
  hide_toolbar: false
  hide_sidebar: false
  touch_zone_size_next_image: 0
  touch_zone_size_previous_image: 0
  style:
    wallpanel-screensaver-overlay:
      background: '#00000000'
  profiles:
    user.<the tablet user's id>:
      enabled: true
```

The profile key is `user.` plus the tablet user's ID (Settings → People → **Users** tab, open the
user; the tab appears with Advanced mode on in your profile). Wallpanel also accepts the user's name
in lower case with spaces as underscores, e.g. `user.hall_tablet`.

`media-entity-image://` follows the `image` entity: the screensaver switches pictures when
`image.matchy_label` changes, i.e. on every rotation, with no reload. The label is baked into that
render, so it shows on the screensaver even though the dashboard tiles hide it.

## 4. Screens (0.3.0): one switch per tablet

Matchy's **Screens** list (see [DOCS.md](../../matchy/DOCS.md#screens)) lets you turn one screen off without touching the others. On
Home Assistant each wall tablet logs in as its own user, so the user name makes a good screen name.
Three changes, all optional.

**Package.** Add `screens_off` to the feed and to `sensor.matchy`, a `rest_command`, and one template
switch per tablet (here a tablet whose Home Assistant user is called `Hall tablet`). These are
additions to the package from section 1: merge each block into the matching top-level key there
(`rest:`, `template:`, `rest_command:`) rather than repeating the key, which YAML does not allow.

```yaml
rest:
  - resource: <matchy-url>/current.json
    sensor:
      - name: Matchy feed
        json_attributes:
          # ... the list from section 1, plus:
          - screens_off

template:
  - sensor:
      - name: Matchy
        attributes:
          # ... as in section 1, plus:
          screens_off: "{{ state_attr('sensor.matchy_feed', 'screens_off') or [] }}"
  - switch:
      - name: Matchy on the hall tablet
        unique_id: matchy_screen_hall_tablet
        icon: mdi:tablet
        state: "{{ 'Hall tablet' not in (state_attr('sensor.matchy', 'screens_off') or []) }}"
        availability: "{{ has_value('sensor.matchy') }}"
        turn_on:
          - action: rest_command.matchy_screen
            data: {screen: Hall tablet, follow: true}
          - action: homeassistant.update_entity
            target: {entity_id: sensor.matchy_feed}
        turn_off:
          - action: rest_command.matchy_screen
            data: {screen: Hall tablet, follow: false}
          - action: homeassistant.update_entity
            target: {entity_id: sensor.matchy_feed}

rest_command:
  matchy_screen:
    url: <matchy-url>/api/screen
    method: POST
    content_type: application/json
    payload: '{"id": {{ screen | to_json }}, "follow": {{ follow | bool | to_json }}}'
    timeout: 15
```

`POST /api/screen` answers `404` until that tablet has asked for a picture with its name once, so
set up the theme below first.

**Theme.** In the `card-mod-view` rule from section 2, name the screen after the user and render
nothing for a user that is turned off; the theme's own background then shows. Leave personal
accounts out of the list (`personal` below): a phone is not a screen.

```yaml
  card-mod-view: |
    {%- set u = state_attr('sensor.matchy', 'background_url') %}
    {%- set off = state_attr('sensor.matchy', 'screens_off') or [] %}
    {%- set personal = ['Alex', 'Sam'] %}
    {%- set q = '' if user in personal else '?screen=' ~ (user | urlencode) %}
    {%- if u and states('sensor.matchy') not in ['unknown', 'unavailable'] and user not in off %}
    hui-view-background {
      --view-background: linear-gradient(rgba(0, 0, 0, 0.22), rgba(0, 0, 0, 0.22)) fixed,
                         center / cover no-repeat fixed url('{{ u }}{{ q }}'),
                         center / cover no-repeat fixed url('/local/fallback.jpg') !important;
    }
    {%- endif %}
```

**Screensaver.** `image.matchy_label` is shared, so a tablet turned off would still show Matchy when
it goes idle. Give each tablet its own `image` entity (added to the `template:` block of the
package) and point that tablet's Wallpanel at it:

```yaml
template:
  - image:
      # The labelled picture while the hall tablet follows Matchy, your own picture while it is off.
      - name: Matchy screensaver hall tablet
        unique_id: matchy_screensaver_hall_tablet
        url: >-
          {% set off = 'Hall tablet' in (state_attr('sensor.matchy', 'screens_off') or []) %}
          {{ 'http://127.0.0.1:8123/local/fallback.jpg' if off else state_attr('sensor.matchy', 'label_url') }}
        availability: "{{ has_value('sensor.matchy') }}"
```

And in that tablet's Wallpanel profile (section 3), so other users of the dashboard keep theirs:

```yaml
wallpanel:
  profiles:
    user.<the tablet user's id>:
      enabled: true
      image_url: media-entity-image://image.matchy_screensaver_hall_tablet
```

The `image` entity is fetched by Home Assistant itself, so the fallback needs an address Home
Assistant can reach without a login: `/local` files are served without one, and
`http://127.0.0.1:8123` works when Home Assistant serves plain HTTP on its default port (use your
`https://` name otherwise). Its picture only changes when the URL does: add a `?d=` that changes daily
if the fallback file is replaced in place.

One thing to know: the screensaver's picture is fetched by Home Assistant, not by the tablet, so a
tablet that spends the day in its screensaver shows an old *last seen* in `/screens.json` while it
still follows Matchy.

## 5. Gentle motion: a slow drift and a crossfade

The background and the screensaver can drift slowly over the picture and fade into the next one.
Only CSS transforms and opacity move, which the browser hands to the graphics chip, so a wall tablet
does no more work than showing a still picture. Both stop drifting (and hold the usual framing) when
the tablet asks for reduced motion: the browser's `prefers-reduced-motion`, which most tablets
switch on with the system's *Remove animations* or *Reduce motion* setting. The fades stay.

The pictures are asked for with `?pad=1.2` (see *Gentle motion* in
[DOCS.md](../../matchy/DOCS.md#gentle-motion)): 1.2 times larger than the screen, so there is room
to move without ever showing an edge, and the label stays in frame whatever the drift does.

**The drift, and why it is safe.** Both recipes use the same keyframes. The picture is shown at
screen size and zoomed between 1.04 and 1.18 times while it slides a few percent: below the pad (1.2),
so it never zooms past 1:1 and the label stays whole, and the slide is always less than the zoom
leaves spare, so no edge shows. To use another pad, scale the numbers with it: zoom at most to the
pad, and keep each translate (in percent) under `(zoom - 1) / (2 * zoom) * 100` at that end.

```css
@keyframes matchy-drift {
  from { transform: scale(1.04) translate(1.5%, 1%); }
  to   { transform: scale(1.18) translate(-6%, -4%); }
}
```

90 seconds each way (`alternate`) is calm on a wall; anything from 60 to 120 reads as still at a
glance.

**Package.** Add a `previous_background_url` attribute to `sensor.matchy` (section 1), next to
`background_url`: it remembers the picture before the current one, which the theme fades from. It
must build the URL the same way as `background_url`.

```yaml
          # The picture before this one, for the crossfade. `this` is the sensor before this update.
          previous_background_url: >-
            {% set u = state_attr('sensor.matchy_feed', 'urls') %}
            {% set new = '<matchy-url>' ~ (u.clean | replace('{w}', '2560') | replace('{h}', '1600'))
                         if u is mapping and u.clean is defined else none %}
            {% set old = this.attributes.background_url | default(none) %}
            {{ old if old and old != new else this.attributes.previous_background_url | default(none) }}
```

**Theme.** This `card-mod-view` rule replaces the one from section 2 (or section 4: it keeps the
screen names and the switch). The previous picture sits underneath, the new one fades in on top of it,
and the whole background drifts. The fade's name changes with every picture, which is what makes the
browser play it again.

```yaml
Matchy Background:
  card-mod-theme: Matchy Background
  card-mod-view: |
    {%- set u = state_attr('sensor.matchy', 'background_url') %}
    {%- set p = state_attr('sensor.matchy', 'previous_background_url') or u %}
    {%- set off = state_attr('sensor.matchy', 'screens_off') or [] %}
    {%- set personal = ['Alex', 'Sam'] %}
    {%- set q = '?pad=1.2' ~ ('' if user in personal else '&screen=' ~ (user | urlencode)) %}
    {%- set fade = 'matchy-in-' ~ (states('sensor.matchy') | regex_replace('[^A-Za-z0-9_-]', '-')) %}
    {%- if u and states('sensor.matchy') not in ['unknown', 'unavailable'] and user not in off %}
    hui-view-background {
      position: fixed;
      --view-background: center / cover no-repeat url('/local/fallback.jpg') !important;
      animation: matchy-drift 90s ease-in-out infinite alternate;
      will-change: transform;
    }
    hui-view-background::before,
    hui-view-background::after {
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      background: linear-gradient(rgba(0, 0, 0, 0.22), rgba(0, 0, 0, 0.22)),
                  center / cover no-repeat url('{{ p }}{{ q }}');
    }
    hui-view-background::after {
      background-image: linear-gradient(rgba(0, 0, 0, 0.22), rgba(0, 0, 0, 0.22)), url('{{ u }}{{ q }}');
      animation: {{ fade }} 3s ease-in-out both;
    }
    @keyframes {{ fade }} {
      from { opacity: 0; }
      to   { opacity: 1; }
    }
    @keyframes matchy-drift {
      from { transform: scale(1.04) translate(1.5%, 1%); }
      to   { transform: scale(1.18) translate(-6%, -4%); }
    }
    @media (prefers-reduced-motion: reduce) {
      hui-view-background { animation: none; transform: scale(1.2); }
    }
    {%- endif %}
```

Without the Screens list, drop `off` and `personal` and set `q` to `'?pad=1.2'`. The fallback image
now sits under the pictures rather than in the same layer list: it shows while a picture loads, or if
it cannot. The background is pinned to the screen (`position: fixed`) so the drift has a fixed frame;
a dashboard taller than the screen scrolls over it, as with the `fixed` background before.

**Screensaver.** Wallpanel already crossfades (`crossfade_time`). For the drift, ask for the padded
label in `label_url` (section 1), so the `image` entity serves it:

```yaml
          label_url: >-
            {% set u = state_attr('sensor.matchy_feed', 'urls') %}
            {{ '<matchy-url>' ~ (u.label | replace('{w}', '1920') | replace('{h}', '1200')) ~ '?s=1.35&pad=1.2'
               if u is mapping and u.label is defined else none }}
```

and give the tablet's Wallpanel profile (section 3) the keyframes through `custom_css`, which
Wallpanel adds to its own style sheet. `!important` because Wallpanel resets the animation on the
picture it is about to show.

```yaml
wallpanel:
  profiles:
    user.<the tablet user's id>:
      enabled: true
      custom_css: |
        #wallpanel-screensaver-image-one,
        #wallpanel-screensaver-image-two {
          animation: matchy-drift 90s ease-in-out infinite alternate !important;
          will-change: transform;
        }
        @keyframes matchy-drift {
          from { transform: scale(1.04) translate(1.5%, 1%); }
          to   { transform: scale(1.18) translate(-6%, -4%); }
        }
        @media (prefers-reduced-motion: reduce) {
          #wallpanel-screensaver-image-one,
          #wallpanel-screensaver-image-two { animation: none !important; transform: scale(1.2); }
        }
```

Every screensaver fed by `label_url` now gets the padded picture; one without the drift shows a
little more of the picture around it and a label about a sixth smaller. Give such a tablet its own
`image` entity without `&pad=1.2` (as in section 4) if that matters.

Leave Wallpanel's own `image_animation_ken_burns` off: it zooms past the pad, so it can push the label
off the screen. With `image_fit_landscape: cover` and a picture size of the tablet's shape, the whole
padded picture fills the screen before the zoom.

**Older tablets.** The padded picture is 1.44 times the pixels (at pad 1.2), held twice during a
fade. On a tablet that struggles, ask for its real resolution rather than a larger one (say
`1280x800` in `background_url`, `previous_background_url` and `label_url`) and watch that it stays
smooth and cool; a smaller pad (1.1, with the zoom kept under 1.1) moves less but costs less.

## Checking

- Developer tools → States: `sensor.matchy` should show the version string with `title`, `blurb`,
  `background_url` and `label_url` attributes within a minute of a restart.
- Open `image.matchy_label` from the entity dialog: the labelled picture, tablet-sized.
- Run `script.matchy_next`: the sensor updates within a few seconds; a dashboard on the theme follows
  on its next state change, a Wallpanel screensaver at its next crossfade.
