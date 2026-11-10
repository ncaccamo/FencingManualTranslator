#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Bring [[fix]] entries the page has outgrown back in line with it -- so a
decision that is still in the text stops being reported "not found".

Run on the HOST, from the repo (books/ is read-only in the container):

    python3 pipeline/repoint_fixes.py <stem> [<stem> ...]      show what it would change
    python3 pipeline/repoint_fixes.py --all --write            change the overrides files

A fix is "not found" when neither its find nor its replace is on its page
(step 2 prints "[[fix]] text not found on scan page(s) …", the review
report lists it). Two causes are safe to repair by machine, and only those
are touched:

  - a later change rewrote its result: a later [[fix]] on the same page
    (Gomard pdf 95: "Paris, are nothing but little works" rewritten
    again), or a case rule (_tx_case) raised a letter in it ("high prime"
    -> "high Prime", 2026-11-10). The replace is set to the wording on the
    page now: the later fixes applied to it, or the page's own text when
    it differs from the replace only in letter case.
  - nothing else. A fix whose result is gone from the page (the page was
    retranslated and says something else) is listed as "unresolved" and
    left for a person: its decision may need writing again.

Without --write nothing is changed. With it, the old overrides file is
kept as processed/<stem>_translated/intermediate/overrides.before-repoint.toml.
Then run apply_overrides.py in the container to check that nothing is
reported any more. A page with a hand edit (books/<stem>.edits/) is skipped:
its edit replaces the page.
"""
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _books  # noqa: E402
import _overrides  # noqa: E402
import _pageio  # noqa: E402

ROOT = os.path.dirname(HERE)
BOOKS = os.path.join(ROOT, "books")
PROCESSED = os.path.join(ROOT, "processed")


def on_page(s: str, page: str) -> bool:
    """As apply_overrides.py counts it, italics aside: step 2 sets its house
    italics after the fixes ("<i>RIPOSTES</i> AT …" for a replace without)."""
    bare = lambda t: re.sub(r"</?i>", "", t)
    return any(x in page or bare(x) in bare(page) for x in (s, _overrides._q(s)))


def repoint(stem: str, write: bool) -> tuple:
    """(repointed, unresolved) for one book; the file is written when asked."""
    path = _overrides.path_for(stem, BOOKS)
    if not os.path.exists(path):
        return 0, 0
    I = os.path.join(PROCESSED, f"{stem}_translated", "intermediate")
    tp = os.path.join(I, "translation.txt")
    if not os.path.exists(tp):
        return 0, 0
    tr = _pageio.split_pages(open(tp, encoding="utf-8").read())
    ov = _overrides.load(stem, BOOKS)
    edited = set(_overrides.load_edits(stem, BOOKS))
    raw = ov["fix"]
    fx = _overrides.book_worded(raw, ov.get("book_fix", []))
    src = open(path, encoding="utf-8").read()
    done = unresolved = 0
    for k, f in enumerate(fx):
        s = f["scan"]
        if "find" not in f or s in edited or s > len(tr):
            continue
        page = tr[s - 1]
        if on_page(f["find"], page) or on_page(f["replace"], page):
            continue
        new = f["replace"]
        for g in fx[k + 1:]:                     # the later fixes on the page, in order
            if g["scan"] == s and "find" in g and g["find"] in new:
                new = new.replace(g["find"], g["replace"])
        if not on_page(new, page):
            hits = [m.group(0) for m in re.finditer(re.escape(new), page, re.I)]
            new = hits[0] if len(set(hits)) == 1 else None    # the same words, another case
        label = f"scan {s}: {f['find'][:60]!r}"
        if new is None:
            print(f"  ✗ {label}  -- its result is not on the page: unresolved, check by hand")
            unresolved += 1
            continue
        old_line = "replace = " + json.dumps(raw[k]["replace"], ensure_ascii=False)
        if src.count(old_line) != 1:
            print(f"  ✗ {label}  -- replace written another way in the file: set it by hand to {new[:60]!r}")
            unresolved += 1
            continue
        src = src.replace(old_line, "replace = " + json.dumps(new, ensure_ascii=False))
        print(f"  ✓ {label}  replace -> {new[:60]!r}")
        done += 1
    if write and done:
        shutil.copy(path, os.path.join(I, "overrides.before-repoint.toml"))
        open(path, "w", encoding="utf-8").write(src)
    return done, unresolved


def main(argv: list) -> int:
    if HERE == "/app" or not os.access(BOOKS, os.W_OK):
        sys.exit(f"✗ {BOOKS} is not writable here -- run this on the host, from the repo")
    _books.PROCESSED, _books.BOOKS = PROCESSED, BOOKS
    write = "--write" in argv
    total = [0, 0]
    for name in _books.select(argv, __doc__, books_dir=BOOKS):
        stem = _books.stem_of(name)
        print(f"{stem}:")
        d, u = repoint(stem, write)
        total[0] += d
        total[1] += u
    verb = "repointed" if write else "to repoint (run again with --write)"
    print(f"\n{total[0]} fix(es) {verb}; {total[1]} unresolved")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
