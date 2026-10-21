"""Tests for mood.py: the tag vocabulary, the moment-to-tags mapping, and the weighting of the picks.

    python3 tests/mood_test.py
"""
import collections, datetime as dt, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "matchy", "app"))

import mood  # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print("PASS", name)
    else:
        fail += 1; print("FAIL", name, detail)


# ---------------- the vocabulary and clean_tags ----------------
check("vocabulary has no repeats", len(set(mood.TAGS)) == len(mood.TAGS))
check("synonyms map into the vocabulary", all(v in mood.TAGS for v in mood.SYNONYMS.values())
      and not any(k in mood.TAGS for k in mood.SYNONYMS))
check("conditions map into the vocabulary", all(t in mood.TAGS for ts in mood.CONDITIONS.values() for t in ts))
check("clean: list, case, synonyms, unknown dropped, no repeats",
      mood.clean_tags(["Cozy", "RAIN", "nonsense", "rain", "ocean", "Night"]) == ["cosy", "rain", "sea", "night"])
check("clean: a string with commas and spaces", mood.clean_tags("snow, mountain dusk;cool") == ["snow", "mountain", "dusk", "cool"])
check("clean: cut to six", mood.clean_tags(list(mood.TAGS)) == list(mood.TAGS[:6]))
check("clean: strict keeps them all", mood.clean_tags(list(mood.TAGS), strict=True) == list(mood.TAGS))
check("clean: not a list", mood.clean_tags(5) == [] and mood.clean_tags(None) == [] and mood.clean_tags([3, None]) == [])
try:
    mood.clean_tags(["rain", "purple"], strict=True); check("clean: strict rejects an unknown word", False)
except ValueError as e:
    check("clean: strict rejects an unknown word", "purple" in str(e) and "rain" in str(e), e)
try:
    mood.clean_tags(["rain", 7], strict=True); check("clean: strict rejects a non-word", False)
except ValueError:
    check("clean: strict rejects a non-word", True)

# ---------------- season ----------------
check("season: north", [mood.season(dt.date(2026, m, 15), 53.3) for m in (1, 4, 7, 10, 12)]
      == ["winter", "spring", "summer", "autumn", "winter"])
check("season: south", [mood.season(dt.date(2026, m, 15), -33.9) for m in (1, 4, 7, 10)]
      == ["summer", "autumn", "winter", "spring"])
check("season: no location is north", mood.season(dt.date(2026, 7, 1)) == "summer")

# ---------------- time of day from the sun events either side ----------------
sunrise, sunset = 1_000_000.0, 1_000_000.0 + 10 * 3600
check("phase: midday", mood.phase_tags(sunrise + 5 * 3600, (sunrise, "sunrise"), (sunset, "sunset")) == {"day"})
check("phase: hour before sunset is dusk and day",
      mood.phase_tags(sunset - 1800, (sunrise, "sunrise"), (sunset, "sunset")) == {"day", "dusk"})
check("phase: hour after sunset is dusk and night",
      mood.phase_tags(sunset + 1800, (sunset, "sunset"), (sunrise + 86400, "sunrise")) == {"night", "dusk"})
check("phase: deep night", mood.phase_tags(sunset + 5 * 3600, (sunset, "sunset"), (sunrise + 86400, "sunrise")) == {"night"})
check("phase: hour before sunrise is dawn and night",
      mood.phase_tags(sunrise - 600, (sunset - 86400, "sunset"), (sunrise, "sunrise")) == {"night", "dawn"})
check("phase: hour after sunrise is dawn and day",
      mood.phase_tags(sunrise + 600, (sunrise, "sunrise"), (sunset, "sunset")) == {"day", "dawn"})
check("phase: fixed hours stand in (morning/evening)",
      mood.phase_tags(sunrise + 5 * 3600, (sunrise, "morning"), (sunset, "evening")) == {"day"})

# ---------------- weather ----------------
check("weather: rainy and cold is rain, cosy, warm", mood.weather_tags("rainy", 6) == {"rain", "cosy", "warm"})
check("weather: sunny and mild is sun", mood.weather_tags("sunny", 18) == {"sun"})
check("weather: sunny and hot wants cool", mood.weather_tags("sunny", 31) == {"sun", "cool"})
check("weather: snow", mood.weather_tags("snowy", -2) == {"snow", "cosy", "warm"})
check("weather: lightning is a dramatic storm", mood.weather_tags("lightning-rainy", 15) == {"rain", "storm", "cosy", "dramatic"})
check("weather: fog", mood.weather_tags("fog", 12) == {"fog", "cosy"})
check("weather: clear night adds nothing", mood.weather_tags("clear-night", 14) == set())
check("weather: unknown condition, no temperature", mood.weather_tags("exceptional", None) == set()
      and mood.weather_tags(None) == set() and mood.weather_tags("rainy", True) == {"rain", "cosy"})

# ---------------- the whole moment ----------------
sun_day = ((sunrise, "sunrise"), (sunset, "sunset"))
sun_night = ((sunset, "sunset"), (sunrise + 86400, "sunrise"))
ctx = mood.context_tags(sunset + 1800, sun_night, dt.date(2026, 11, 3), {"condition": "rainy", "temperature_c": 6}, 53.3)
check("moment: a cold wet autumn evening", ctx == ["autumn", "cosy", "dusk", "night", "rain", "warm"], ctx)
ctx = mood.context_tags(sunrise + 2 * 3600, sun_day, dt.date(2026, 6, 20), {"condition": "sunny", "temperature_c": 19}, 53.3)
check("moment: a sunny summer morning is bright", ctx == ["bright", "day", "summer", "sun"], ctx)
ctx = mood.context_tags(sunset + 3 * 3600, sun_night, dt.date(2026, 6, 20), {"condition": "sunny", "temperature_c": 19}, 53.3)
check("moment: 'sunny' at night is not bright", ctx == ["night", "summer", "sun"], ctx)
ctx = mood.context_tags(sunrise + 5 * 3600, sun_day, dt.date(2026, 1, 20), None, None)
check("moment: no weather, no location", ctx == ["day", "winter"], ctx)
ctx = mood.context_tags(sunrise + 5 * 3600, sun_day, dt.date(2026, 1, 20), {"condition": "cloudy", "temperature_c": None}, -40)
check("moment: south, cloudy, no temperature", ctx == ["cloud", "day", "summer"], ctx)

# ---------------- choose: off is today's pick, light/strong pull matches, nobody starves ----------------
TAGS = {"a": ["rain", "cosy", "night"], "b": ["sun", "bright"], "c": ["forest", "calm"], "d": ["rain"], "e": []}
IDS = sorted(TAGS)
ctx = ["rain", "cosy", "night", "autumn"]

rng = random.Random(1)
bags = [rng.sample(IDS, len(IDS)) for _ in range(200)]
check("off: always the front of the bag, whatever the tags",
      all(mood.choose(bag, TAGS.get, ctx, "off", rng=rng) == 0 for bag in bags))
check("off: the picture on screen is passed over",
      all(mood.choose(bag, TAGS.get, ctx, "off", exclude=bag[0], rng=rng) == 1 for bag in bags))
check("one picture left: it is shown even if it is on screen", mood.choose(["a"], TAGS.get, ctx, "strong", exclude="a") == 0)
check("empty bag", mood.choose([], TAGS.get, ctx, "strong") is None)
check("no context: the front of the bag", all(mood.choose(bag, TAGS.get, [], "strong", rng=rng) == 0 for bag in bags))
check("nothing matches: the front of the bag",
      all(mood.choose(bag, TAGS.get, ["desert"], "strong", rng=rng) == 0 for bag in bags))


def simulate(level, n_shuffles, seed):
    """Run the engine's bag the way pick_next does: a fresh shuffle whenever it is empty, choose() for the
    index. Returns the sequence of picks."""
    rng = random.Random(seed)
    picks, bag, cur = [], [], None
    for _ in range(n_shuffles * len(IDS)):
        if not bag:
            bag = sorted(IDS)
            rng.shuffle(bag)
            if len(bag) > 1 and bag[0] == cur:
                bag.append(bag.pop(0))
        k = mood.choose(bag, TAGS.get, ctx, level, exclude=cur, rng=rng)
        cur = bag.pop(k)
        picks.append(cur)
    return picks


for level in ("off", "light", "strong"):
    picks = simulate(level, 400, 7)
    counts = collections.Counter(picks)
    check("%s: every picture exactly once per shuffle (no starvation)" % level,
          all(counts[i] == 400 for i in IDS) and all(sorted(picks[k:k + 5]) == IDS for k in range(0, len(picks), 5)), counts)
    check("%s: never the same picture twice in a row" % level, all(x != y for x, y in zip(picks, picks[1:])))
    firsts = collections.Counter(picks[0::5])  # the first pick of each shuffle, where the pull shows
    share = (firsts["a"] + firsts["d"]) / 400
    if level == "off":
        check("off: the first pick of a shuffle is unweighted (~2 in 5 match)", 0.3 <= share <= 0.5, share)
    elif level == "light":
        check("light: a match leads about 3 shuffles in 4", 0.68 <= share <= 0.84, share)
        check("light: the richer match (3 tags) beats the one-tag one", firsts["a"] > firsts["d"] * 1.5, firsts)
    else:
        check("strong: a match leads about 19 shuffles in 20", 0.88 <= share <= 0.98, share)
        check("strong: the richer match (3 tags) beats the one-tag one", firsts["a"] > firsts["d"] * 1.5, firsts)

# ---------------- weather_text ----------------
check("weather_text", mood.weather_text({"condition": "lightning-rainy", "temperature_c": 5.6}) == "lightning rainy, 6 °C"
      and mood.weather_text({"condition": "sunny", "temperature_c": None}) == "sunny" and mood.weather_text(None) == "")

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
