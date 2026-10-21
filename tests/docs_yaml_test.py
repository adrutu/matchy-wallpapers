"""Every ```yaml block in the documentation parses, and every Assist sentence template in them is
well formed. The client recipes are copied into Home Assistant as they stand, so a stray tab or an
unquoted Jinja expression there is a bug.

    python tests/docs_yaml_test.py [file.md ...]      # default: README.md, matchy/DOCS.md, docs/**/*.md

Needs PyYAML (the Lint job installs it for validate_config.py). A sentence template is a string under
a `sentences:` list (custom sentences) or a `command:` list (a sentence trigger): brackets must pair
and nest, and it must carry no punctuation beyond an apostrophe, which Assist ignores anyway.
Exits 1 on the first file set with a problem."""
import glob
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FENCE = re.compile(r"^```yaml[^\n]*\n(.*?)^```", re.S | re.M)
PUNCT = re.compile(r"[?!.,;:\"]")


def yaml_blocks(text):
    return [m.group(1) for m in FENCE.finditer(text)]


def sentence_problem(s):
    """Why `s` is not a sound sentence template, or None."""
    depth, stack = 0, []
    for ch in s:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or "([{"[")]}".index(ch)] != stack.pop():
                return "unbalanced brackets"
    if stack:
        return "unbalanced brackets"
    if PUNCT.search(s):
        return "punctuation (Assist ignores it; leave it out of the template)"
    if "|" in s and not re.search(r"[(\[][^()\[\]]*\|", s):
        return "an alternative (|) outside brackets"
    return None


def sentences(doc):
    """Every string under a `sentences` or `command` list anywhere in a parsed document."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("sentences", "command") and isinstance(v, list):
                    out.extend(x for x in v if isinstance(x, str))
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    walk(doc)
    return out


def check_file(path):
    problems = []
    text = open(path, encoding="utf-8").read()
    blocks = yaml_blocks(text)
    for n, block in enumerate(blocks, 1):
        try:
            docs = list(yaml.safe_load_all(block))
        except yaml.YAMLError as e:
            problems.append("block %d does not parse: %s" % (n, str(e).replace("\n", " ")))
            continue
        for doc in docs:
            for s in sentences(doc):
                why = sentence_problem(s)
                if why:
                    problems.append("block %d, sentence %r: %s" % (n, s, why))
    return len(blocks), problems


def main(argv):
    files = argv or [os.path.join(ROOT, "README.md"), os.path.join(ROOT, "matchy", "DOCS.md")] + sorted(
        glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True))
    bad = 0
    for path in files:
        n, problems = check_file(path)
        rel = os.path.relpath(path, ROOT)
        for p in problems:
            print("FAIL %s: %s" % (rel, p))
        bad += len(problems)
        if not problems:
            print("ok   %s: %d yaml block%s" % (rel, n, "" if n == 1 else "s"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
