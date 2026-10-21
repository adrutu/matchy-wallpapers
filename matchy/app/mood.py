"""Mood-aware picks: a fixed tag vocabulary for the captioner, the tags of the moment (time of day,
season and the weather), and the weighting that pulls matching pictures forward in the shuffle.

Nothing here reads the clock, the network or the library: the engine passes everything in, so the
mapping and the weighting can be tested on their own (tests/mood_test.py).
"""
import random
import re

# The vocabulary the captioner picks from, by group. Hand edits in the panel use the same list.
TAG_GROUPS = {
    "time of day": ("dawn", "day", "dusk", "night"),
    "weather": ("sun", "cloud", "rain", "snow", "fog", "storm"),
    "season": ("spring", "summer", "autumn", "winter"),
    "place": ("sea", "mountain", "forest", "city", "desert"),
    "feel": ("warm", "cool", "cosy", "bright", "calm", "dramatic"),
}
TAGS = tuple(t for group in TAG_GROUPS.values() for t in group)
MIN_TAGS, MAX_TAGS = 3, 6  # what the captioner is asked for; a hand edit may use any number

# Near misses a model answers with, folded onto the vocabulary instead of being dropped.
SYNONYMS = {
    "cozy": "cosy", "sunrise": "dawn", "morning": "dawn", "sunset": "dusk", "evening": "dusk", "twilight": "dusk",
    "daytime": "day", "midday": "day", "noon": "day", "nighttime": "night", "midnight": "night", "dark": "night",
    "sunny": "sun", "sunshine": "sun", "sunlight": "sun", "cloudy": "cloud", "clouds": "cloud", "overcast": "cloud",
    "rainy": "rain", "wet": "rain", "drizzle": "rain", "snowy": "snow", "ice": "snow", "frost": "snow",
    "foggy": "fog", "mist": "fog", "misty": "fog", "haze": "fog", "hazy": "fog",
    "stormy": "storm", "thunder": "storm", "lightning": "storm",
    "ocean": "sea", "coast": "sea", "coastal": "sea", "beach": "sea", "seaside": "sea", "waves": "sea",
    "mountains": "mountain", "alpine": "mountain", "peak": "mountain", "peaks": "mountain",
    "woods": "forest", "woodland": "forest", "trees": "forest", "jungle": "forest",
    "urban": "city", "town": "city", "street": "city", "skyline": "city", "dunes": "desert", "arid": "desert",
    "fall": "autumn", "hot": "warm", "cold": "cool", "chilly": "cool", "icy": "cool",
    "peaceful": "calm", "quiet": "calm", "serene": "calm", "still": "calm", "tranquil": "calm",
    "moody": "dramatic", "intense": "dramatic", "vivid": "bright", "sunlit": "bright", "cheerful": "bright",
}

# A Home Assistant weather entity's state, to tags. Conditions not listed add nothing.
CONDITIONS = {
    "sunny": ("sun",), "clear-night": (), "partlycloudy": ("sun", "cloud"), "cloudy": ("cloud",),
    "windy": (), "windy-variant": ("cloud",), "fog": ("fog",), "rainy": ("rain",), "pouring": ("rain", "storm"),
    "lightning": ("storm",), "lightning-rainy": ("rain", "storm"), "hail": ("rain", "storm"),
    "snowy": ("snow",), "snowy-rainy": ("snow", "rain"), "exceptional": (),
}
COLD_C, HOT_C = 8.0, 25.0   # at or below: a warm, cosy picture suits; at or above: a cool one
TWILIGHT_S = 3600           # dawn and dusk last an hour either side of sunrise and sunset

# How often a matching picture is pulled forward instead of the next one in the shuffle.
PULL = {"off": 0.0, "light": 0.6, "strong": 0.9}
LEVELS = tuple(PULL)

WEATHER_ENTITY_RE = re.compile(r"^weather\.[a-z0-9_]+$")


def clean_tags(raw, strict=False):
    """The vocabulary words in `raw` (a list, or one string split on commas and spaces), lower-cased,
    synonyms folded, in order and without repeats. Words outside the vocabulary are dropped, or with
    `strict` (a hand edit) raise ValueError naming the first one. The captioner's answer is cut to
    MAX_TAGS; a hand edit may use the whole vocabulary."""
    if isinstance(raw, str):
        raw = re.split(r"[,\s;/]+", raw)
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for w in raw:
        if not isinstance(w, str):
            if strict:
                raise ValueError("tags must be words")
            continue
        w = w.strip().strip("\"'.").lower()
        if not w:
            continue
        w = SYNONYMS.get(w, w)
        if w not in TAGS:
            if strict:
                raise ValueError("%r is not a tag; the tags are %s" % (w, ", ".join(TAGS)))
            continue
        if w not in out:
            out.append(w)
    return out if strict else out[:MAX_TAGS]


def season(date, latitude=None):
    """Meteorological seasons from the month, the other way round south of the equator."""
    m = date.month
    s = "winter" if m in (12, 1, 2) else "spring" if m <= 5 else "summer" if m <= 8 else "autumn"
    if isinstance(latitude, (int, float)) and latitude < 0:
        s = {"winter": "summer", "summer": "winter", "spring": "autumn", "autumn": "spring"}[s]
    return s


def phase_tags(now, prev_event, next_event):
    """Time-of-day tags from the sun events either side of now: (epoch, name), the names being
    sunrise/sunset (or morning/evening when fixed hours stand in). The hour around each event is dawn
    or dusk, and carries the day or night it borders on, so the change is gradual."""
    up = {"sunrise": True, "morning": True, "sunset": False, "evening": False}
    daylight = up[prev_event[1]]
    tags = {"day" if daylight else "night"}
    if now - prev_event[0] < TWILIGHT_S:
        tags.add("dawn" if up[prev_event[1]] else "dusk")
    if next_event[0] - now < TWILIGHT_S:
        tags.add("dawn" if up[next_event[1]] else "dusk")
    return tags


def weather_tags(condition, temperature_c=None):
    """Tags for a weather reading: the condition's own tags, then what suits the moment: rain, snow, fog
    or a storm ask for a cosy picture, a storm a dramatic one, a cold day a warm picture, a hot day a
    cool one."""
    tags = set(CONDITIONS.get(condition or "", ()))
    if tags & {"rain", "snow", "fog", "storm"}:
        tags.add("cosy")
    if "storm" in tags:
        tags.add("dramatic")
    if isinstance(temperature_c, (int, float)) and not isinstance(temperature_c, bool):
        if temperature_c <= COLD_C:
            tags |= {"warm", "cosy"}
        elif temperature_c >= HOT_C:
            tags.add("cool")
    return tags


def context_tags(now, sun, date, weather=None, latitude=None):
    """The tags of the moment, sorted. `sun`: (previous event, next event) as phase_tags takes them;
    `date`: the local date; `weather`: {"condition", "temperature_c"} or None (no weather entity, or no
    reading yet). A sunny day is bright; the sun at night is not."""
    tags = phase_tags(now, *sun)
    tags.add(season(date, latitude))
    if weather:
        tags |= weather_tags(weather.get("condition"), weather.get("temperature_c"))
        if "sun" in tags and "day" in tags:
            tags.add("bright")
    return sorted(tags)


def choose(bag, tags_of, context, level, exclude=None, rng=random):
    """Index into `bag` (the shuffle, front first) of the picture to show next, or None when it is empty.

    off: the front of the bag, as always. light/strong: with probability PULL[level] a picture whose
    tags share something with `context`, the more shared the likelier (three matching tags is three
    times as likely as one); otherwise the front of the bag. `exclude` (the picture on screen) is
    passed over unless it is all that is left. Every picture still leaves the bag exactly once per
    shuffle, whatever the level, so the rest of the library is never starved."""
    if not bag:
        return None
    cands = [k for k, i in enumerate(bag) if i != exclude] or [0]
    pull = PULL.get(level, 0.0)
    if pull and context:
        ctx = set(context)
        matching = [(k, len(ctx.intersection(tags_of(bag[k]) or ()))) for k in cands]
        matching = [(k, s) for k, s in matching if s]
        if matching and rng.random() < pull:
            r = rng.uniform(0, sum(s for _, s in matching))
            for k, s in matching:
                r -= s
                if r <= 0:
                    return k
            return matching[-1][0]
    return cands[0]


def weather_text(weather):
    """'rainy, 6 °C' for the panel and the log; '' without a reading."""
    if not weather or not weather.get("condition"):
        return ""
    out = str(weather["condition"]).replace("-", " ")
    t = weather.get("temperature_c")
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        out += ", %d °C" % round(t)
    return out
