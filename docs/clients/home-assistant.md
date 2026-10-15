# Home Assistant dashboards and wall tablets

Three pieces, each optional:

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
from a private address (the *Network* tier in DOCS.md). To pause or show a specific picture from an
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
    user.<the tablet user's id>:   # Settings → People → the user → the id in the URL
      enabled: true
```

`media-entity-image://` follows the `image` entity: the screensaver switches pictures when
`image.matchy_label` changes, i.e. on every rotation, with no reload. The label is baked into that
render, so it shows on the screensaver even though the dashboard tiles hide it.

## Checking

- Developer tools → States: `sensor.matchy` should show the version string with `title`, `blurb`,
  `background_url` and `label_url` attributes within a minute of a restart.
- Open `image.matchy_label` from the entity dialog: the labelled picture, tablet-sized.
- Run `script.matchy_next`: the sensor updates within a few seconds; a dashboard on the theme follows
  on its next state change, a Wallpanel screensaver at its next crossfade.
