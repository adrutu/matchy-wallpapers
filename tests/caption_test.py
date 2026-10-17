"""Tests for caption.proper_nouns: derived place adjectives pass, invented names still flagged.

    python3 tests/caption_test.py
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "matchy", "app"))

from caption import proper_nouns

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

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
