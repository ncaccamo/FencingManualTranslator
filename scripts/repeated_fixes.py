#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""The corrections that keep being made: every [[fix]] in books/*.overrides.toml
reduced to the words it changes, and the changes counted.

    python3 scripts/repeated_fixes.py                  all books
    python3 scripts/repeated_fixes.py <stem> [...]     these books
    python3 scripts/repeated_fixes.py --min 3          made at least 3 times (default 4)

A change made many times, or in several books, is a rule to add (a line in
pipeline/glossary_*.toml, a code rule, a book's [terms] or [[book_fix]]),
not a list of fixes to keep up. Run it after reviewing a book. Host; reads
only the overrides files.
"""
from collections import Counter, defaultdict
from pathlib import Path
import difflib
import re
import sys
import tomllib

BOOKS = Path(__file__).resolve().parent.parent / "books"


def changes(find: str, replace: str) -> list:
    """The word-level changes of one fix, as (old, new) in small letters,
    tags and edge punctuation dropped; [] for a change of case, tags or
    punctuation only."""
    strip = lambda s: re.sub(r"<[^>]+>", "", s)
    a, b = strip(find), strip(replace)
    if re.sub(r"\W", "", a).lower() == re.sub(r"\W", "", b).lower():
        return []
    aw, bw = a.split(), b.split()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, aw, bw).get_opcodes():
        if tag == "equal":
            continue
        old = " ".join(aw[i1:i2]).lower().strip(".,;:!?“”\"()")
        new = " ".join(bw[j1:j2]).lower().strip(".,;:!?“”\"()")
        if old and new and old != new and len(old.split()) <= 4 and len(new.split()) <= 4:
            out.append((old, new))
    return out


def main(argv: list) -> int:
    least = int(argv[argv.index("--min") + 1]) if "--min" in argv else 4
    stems = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--min")]
    files = [BOOKS / f"{s}.overrides.toml" for s in stems] if stems else sorted(BOOKS.glob("*.overrides.toml"))
    count, books, total, kinds = Counter(), defaultdict(Counter), 0, Counter()
    for f in files:
        if not f.exists():
            print(f"no overrides file: {f.name}")
            return 2
        stem = f.name[:-len(".overrides.toml")]
        for x in tomllib.load(open(f, "rb")).get("fix", []):
            if "find" not in x or "replace" not in x:
                continue
            total += 1
            ch = changes(x["find"], x["replace"])
            kinds["wording" if ch else "case, tags, punctuation or a longer rewrite"] += 1
            for c in ch:
                count[c] += 1
                books[c][stem] += 1
    rows = [(n, c) for c, n in count.items() if n >= least]
    covered = sum(n for n, _ in rows)
    print(f"{total} fixes in {len(files)} book(s); {len(rows)} changes made {least}+ times account for {covered} word changes.\n")
    print(f"{'times':>5}  {'books':>5}  change")
    for n, (old, new) in sorted(rows, key=lambda r: (-r[0], r[1])):
        where = ", ".join(f"{' '.join(s.split('_')[:2])} {k}" for s, k in books[(old, new)].most_common(3))
        print(f"{n:>5}  {len(books[(old, new)]):>5}  {old!r} → {new!r}   ({where})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
