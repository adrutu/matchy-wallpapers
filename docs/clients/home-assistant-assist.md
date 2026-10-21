# Home Assistant Assist: "what's on the wall?"

Ask a voice satellite (or the Assist dialog) about the picture on the wall, and drive the rotation
by voice. Five phrases:

| Say | What happens |
| :--- | :--- |
| *What's on the wall?* / *What's this picture?* | Assist reads the title, the place and the blurb. |
| *Next wallpaper* | Rotates now and names the new picture. |
| *Pause the wallpaper* / *Resume the wallpaper* | Holds the picture on screen, or lets it rotate again. |
| *I love this wallpaper* | Marks the picture **Loved** (a tag in the sidebar panel and `"loved": true` in the JSON). |

It is two files on top of the package from [home-assistant.md](home-assistant.md) section 1, which
gives you `sensor.matchy_feed` (the picture's title, place and blurb as attributes) and
`rest_command.matchy_next`: a **custom sentences** file that teaches Assist the phrases, and an
**`intent_script`** block that says what each one does. Nothing needs to be exposed to Assist; these
are custom intents, not entity commands. Replace `<matchy-url>` as in that recipe.

## 1. Sentences: `custom_sentences/en/matchy.yaml`

The folder is `config/custom_sentences/<language>/`; the file name is yours. Round brackets are
alternatives, square brackets optional words. Matching ignores case and punctuation, so *What's on the
wall?* matches `what's on the wall`.

```yaml
language: "en"
intents:
  MatchyWhatsOnTheWall:
    data:
      - sentences:
          - "(what's|what is|whats) on the wall"
          - "(what's|what is|whats) (this|that|the) (picture|wallpaper|painting|photo)"
          - "(what|which) (picture|wallpaper) is (this|that|on the wall)"
          - "tell me about (this|the) (picture|wallpaper)"
  MatchyNext:
    data:
      - sentences:
          - "[show [me] [the]] next (picture|wallpaper)"
          - "(change|skip) (the|this) (picture|wallpaper)"
  MatchyPause:
    data:
      - sentences:
          - "pause the (wallpaper|wallpapers|pictures|rotation)"
          - "(keep|hold) (this|the) (picture|wallpaper)"
          - "stop changing the (picture|pictures|wallpaper)"
  MatchyResume:
    data:
      - sentences:
          - "(resume|unpause) the (wallpaper|wallpapers|pictures|rotation)"
          - "(start|keep) changing the (picture|pictures|wallpaper) [again]"
  MatchyLove:
    data:
      - sentences:
          - "I (love|like) this (picture|wallpaper)"
          - "this (picture|wallpaper) is (lovely|beautiful|great|gorgeous)"
```

Add your own wording as more lines; keep each sentence free of punctuation. For another language,
put a file with that language code in its own folder and translate the sentences (the intent names
stay the same).

## 2. Actions and answers: `intent_script`

Add this to the package (`packages/matchy.yaml`). The `rest_command:` block is additions to the one
already there: merge them under the existing key rather than repeating it, which YAML does not allow.
The read-only intent refreshes the feed first, so a picture that changed in the last minute is the
one described (the REST sensor polls every 60 seconds).

```yaml
rest_command:
  # matchy_next is in the package already. The JSON controls need the content type and a body.
  matchy_pause:
    url: <matchy-url>/api/pause
    method: POST
    content_type: application/json
    payload: "{}"
    timeout: 15
  matchy_resume:
    url: <matchy-url>/api/resume
    method: POST
    content_type: application/json
    payload: "{}"
    timeout: 15
  matchy_love:
    url: <matchy-url>/api/love
    method: POST
    content_type: application/json
    payload: "{}"             # no id: the picture on screen
    timeout: 15

intent_script:
  MatchyWhatsOnTheWall:
    action:
      - action: homeassistant.update_entity
        target:
          entity_id: sensor.matchy_feed
    speech:
      text: >-
        {%- set s = 'sensor.matchy_feed' -%}
        {%- if not has_value(s) -%}
          I can't reach the wallpaper right now.
        {%- else -%}
          {%- set place = state_attr(s, 'place') -%}
          This is {{ state_attr(s, 'title') or 'an untitled picture' }}
          {%- if place %}, {{ place }}{% endif %}.
          {{ state_attr(s, 'blurb') or '' }}
        {%- endif -%}

  MatchyNext:
    # /api/next answers the new current.json; `stop` hands it to the speech template as action_response.
    action:
      - action: rest_command.matchy_next
        response_variable: answer
      - action: homeassistant.update_entity
        target:
          entity_id: sensor.matchy_feed
      - stop: ""
        response_variable: answer
    speech:
      text: >-
        {%- set c = action_response.content
               if action_response is defined and action_response.content is mapping else {} -%}
        {%- if c.title is defined and c.title -%}
          Now showing {{ c.title }}{% if c.place %}, {{ c.place }}{% endif %}.
        {%- else -%}
          Changing the wallpaper.
        {%- endif -%}

  MatchyPause:
    action:
      - action: rest_command.matchy_pause
      - action: homeassistant.update_entity
        target:
          entity_id: sensor.matchy_feed
    speech:
      text: "Holding {{ state_attr('sensor.matchy_feed', 'title') or 'this picture' }} on the wall."

  MatchyResume:
    action:
      - action: rest_command.matchy_resume
      - action: homeassistant.update_entity
        target:
          entity_id: sensor.matchy_feed
    speech:
      text: "The wallpaper will change again."

  MatchyLove:
    action:
      - action: rest_command.matchy_love
      - action: homeassistant.update_entity
        target:
          entity_id: sensor.matchy_feed
    speech:
      text: "Noted. {{ state_attr('sensor.matchy_feed', 'title') or 'This picture' }} is marked loved."
```

Then restart Home Assistant, or run **conversation.reload** and **intent_script.reload** from
Developer tools → Actions. To have the artist read out too, add `credit` to the first answer the
way `place` is; it is already an attribute of the feed.

`POST /api/pause`, `/api/resume` and `/api/love` are *Network* tier (see
[DOCS.md](../../matchy/DOCS.md#access-tiers)), so Home Assistant may call them from its private
address. **Loved** is the panel's heart: the picture comes up `love_weight` times as often (see
*Loved pictures* in DOCS.md), shows a **Loved** tag in the panel (where the heart takes it back) and
`current.json` says `"loved": true`.

## 3. The kids caption on some satellites

With kids captions on (the `?label=kids` label, which adds `kids_blurb` to `current.json`), a
satellite in a child's room can read the kids caption while the others read the gallery blurb. An
`intent_script` does not know which satellite asked, so this one phrase becomes an automation with a
**sentence trigger**, which does (`trigger.device_id`). Two changes:

1. In the package, add `kids_blurb` to the feed's `json_attributes` list (section 1 of
   [home-assistant.md](home-assistant.md)).
2. Remove the `MatchyWhatsOnTheWall` intent from the sentences file and from `intent_script`, and
   add this automation instead (in the package, or through the automation editor in YAML mode). Name
   the kids satellites by their `assist_satellite` entity.

```yaml
automation:
  - alias: "Matchy: what's on the wall"
    description: The kids caption on the satellites listed, the gallery blurb everywhere else.
    triggers:
      - trigger: conversation
        command:
          - "(what's|what is|whats) on the wall"
          - "(what's|what is|whats) (this|that|the) (picture|wallpaper|painting|photo)"
          - "(what|which) (picture|wallpaper) is (this|that|on the wall)"
          - "tell me about (this|the) (picture|wallpaper)"
    actions:
      - action: homeassistant.update_entity
        target:
          entity_id: sensor.matchy_feed
      - set_conversation_response: >-
          {%- set kids_satellites = ['assist_satellite.playroom'] -%}
          {%- set s = 'sensor.matchy_feed' -%}
          {%- if not has_value(s) -%}
            I can't reach the wallpaper right now.
          {%- else -%}
            {%- set for_kids = trigger.device_id is not none
                   and trigger.device_id in kids_satellites | map('device_id') | list -%}
            {%- set blurb = state_attr(s, 'kids_blurb') if for_kids else none -%}
            {%- set place = state_attr(s, 'place') -%}
            This is {{ state_attr(s, 'title') or 'an untitled picture' }}
            {%- if place %}, {{ place }}{% endif %}.
            {{ blurb or state_attr(s, 'blurb') or '' }}
          {%- endif -%}
```

A satellite that is not in the list, the Assist dialog in a browser and the companion app all get
the gallery blurb; so does a kids satellite while a picture has no kids caption yet.

## 4. With MQTT entities

Matchy does not publish MQTT entities yet. When it does, the recipe above stays the same shape and
three things change: the speech templates read the picture's attributes from the MQTT sensor instead
of `sensor.matchy_feed`; **Next** and **Love** press the MQTT buttons (`button.press`) and
**Pause**/**Resume** turn the rotation switch off and on (`switch.turn_off`, `switch.turn_on`)
instead of calling `rest_command`s; and the `homeassistant.update_entity` steps go, because MQTT
pushes every change. Until then, and on any install without an MQTT broker, the REST recipe above
is the one to use.

## Checking

- **Settings → Voice assistants → your assistant → ⋮ → Debug**: type *what's on the wall* and the
  matched intent (`MatchyWhatsOnTheWall`) and the spoken answer are shown, without touching the
  screens. The read-only phrase is safe to try as often as you like; the other four change what the
  screens show, so check those in the debug view and fire them once on purpose.
- If your assistant uses an LLM as its conversation agent, turn on **Prefer handling commands
  locally** in the assistant's settings, so these sentences are matched by Assist itself first.
- *I can't reach the wallpaper right now* means `sensor.matchy_feed` is unavailable: the add-on is
  down, the published port is not set, or `<matchy-url>` is wrong.
