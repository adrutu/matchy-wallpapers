"""Tests for caption.proper_nouns: derived place adjectives pass, invented names still flagged; and
the kids caption's checks (its own word limits, nothing scary, the same grounding).

    python3 tests/caption_test.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "matchy", "app"))

from caption import Captioner, proper_nouns

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print("PASS", name)
    else:
        fail += 1; print("FAIL", name)


allowed = "Heart of the Andes | Frederic Edwin Church | Iceland | Japan | Paris"

check("Andean allowed", proper_nouns("The Andean peaks rise.", allowed) == [])
check("Icelandic allowed", proper_nouns("Light on the Icelandic coast.", allowed) == [])
check("Japanese allowed", proper_nouns("A Japanese garden.", allowed) == [])
check("Parisian allowed", proper_nouns("The Parisian street.", allowed) == [])
check("Montana still flagged", proper_nouns("Light near Montana fades.", allowed) == ["Montana"])
check("Andorran still flagged", proper_nouns("The Andorran hills.", allowed) == ["Andorran"])
check("Spain still flagged", proper_nouns("A view of Spain.", allowed) == ["Spain"])
check("Ecuador still flagged", proper_nouns("Church paints Ecuador.", allowed) == ["Ecuador"])
check("sentence-initial ignored", proper_nouns("Andorra glows.", allowed) == [])
check("possessive of a known name allowed", proper_nouns("The light in Church's sky.", allowed) == [])
check("possessive of an unknown name flagged", proper_nouns("The light in Turner's sky.", allowed) == ["Turner"])
check("Cuban allowed (4-letter stem)", proper_nouns("A Cuban street.", "Cuba") == [])
check("Italian allowed", proper_nouns("An Italian hill town.", "Italy") == [])
check("3-letter stem never stretches", proper_nouns("The Malian dunes.", "Mal") == ["Malian"])

cap = Captioner({"captioners": [], "caption": {"min_words": 30, "max_words": 45, "spelling": "British",
                                              "kids": {"min_words": 15, "max_words": 30}}})
meta = {"title": "Speckled Colour Field", "place": "", "context": ""}
kid = "Look at all the tiny white dots! They sparkle like frost on a window. Fun fact: frost is made of tiny ice crystals."
check("kids blurb passes", cap.check(meta, meta["title"], kid, kids=True)[0] == [])
check("kids blurb too short for the main limits", cap.check(meta, meta["title"], kid)[0] != [])
check("kids limits 15-30", any("15 to 30" in p for p in cap.check(meta, meta["title"], "Tiny dots.", kids=True)[0]))
check("kids: scary words rejected", any("ghost" in p for p in cap.check(
    meta, meta["title"], kid + " A ghost hides there.", kids=True)[0]))
check("kids: grounding still applies", any("Paris" in p for p in cap.check(
    meta, meta["title"], kid.replace("on a window", "in Paris"), kids=True)[0]))
check("kids: an empty hand-written title is not a problem", cap.check({"title": ""}, "", kid, kids=True)[0] == [])
pr = cap.kids_prompt(meta)
check("kids prompt asks for 15 to 30 words and a fun fact", "at least 15 and at most 30" in pr and "fun fact" in pr
      and "children aged 5 to 9" in pr and 'Title: "Speckled Colour Field"' in pr)

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
