"""Tests for caption.proper_nouns (derived place adjectives pass, invented names still flagged) and
caption.subject_box (the subject box behind focal-point crops).

    python3 tests/caption_test.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "matchy", "app"))

from caption import Captioner, proper_nouns, subject_box

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

# The subject box behind focal-point crops: kept when sane, ignored otherwise.
check("box: kept, rounded", subject_box({"subject": [0.6, 0.1, 0.9, 0.95]}) == [0.6, 0.1, 0.9, 0.95])
check("box: integers 0/1 fine", subject_box({"subject": [0, 0, 1, 0.5]}) == [0.0, 0.0, 1.0, 0.5])
check("box: missing", subject_box({"title": "x"}) is None)
check("box: not a list", subject_box({"subject": "middle"}) is None and subject_box({"subject": {"x": 1}}) is None)
check("box: three numbers", subject_box({"subject": [0.1, 0.2, 0.3]}) is None)
check("box: pixels, not fractions", subject_box({"subject": [120, 40, 640, 900]}) is None)
check("box: negative", subject_box({"subject": [-0.1, 0.2, 0.5, 0.8]}) is None)
check("box: inside out", subject_box({"subject": [0.8, 0.2, 0.3, 0.9]}) is None)
check("box: a sliver", subject_box({"subject": [0.5, 0.2, 0.505, 0.9]}) is None)
check("box: the whole frame says nothing", subject_box({"subject": [0, 0, 1, 1]}) is None)
check("box: booleans and strings", subject_box({"subject": [True, 0, 1, 0.5]}) is None
      and subject_box({"subject": ["0.1", 0.2, 0.5, 0.8]}) is None)
check("box: not an object", subject_box(["subject"]) is None)

cfg = {"caption": {"min_words": 30, "max_words": 45}, "captioners": []}
check("prompt: no box asked for by default", "subject" not in Captioner(cfg).prompt({}))
check("prompt: box asked for with focus_auto", '"subject"' in Captioner(dict(cfg, focus_auto=True)).prompt({}))
check("parse still reads title and blurb next to a box",
      Captioner.parse('{"title": "T", "blurb": "b  c", "subject": [0.1, 0.1, 0.5, 0.5]}') == ("T", "b c"))

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
