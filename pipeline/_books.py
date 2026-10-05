# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Which books a pipeline script works on -- the same rule in every script.

Every step names its books; none runs on "everything in the folder" by
default any more (a finished book must not be reprocessed because a new
one was added -- 2026-10-05):

    python3 /app/2_translate.py <stem> [<stem> ...]
    python3 /app/2_translate.py --all          # every book there is
    PDF_ONLY=<stem>,<stem> python3 /app/...    # the older form, still read

A book can be marked finished in its overrides file:

    [book]
    final = true

Scripts that would change it then skip it -- named or under --all -- until
the run is given --unlock (or that line is removed). Scripts that only read
(the review report) are not stopped.

A name may also be a book folder (/data/processed/<stem>_translated, or a
scratch copy of one): reprocess_extracted.py is given folders that way.
"""
import glob
import os
import sys

import _overrides

PROCESSED = "/data/processed"
BOOKS = "/data/books"


def stem_of(name: str) -> str:
    n = os.path.basename(name.rstrip("/"))
    for suffix in ("_translated", ".pdf"):
        if n.endswith(suffix):
            n = n[:-len(suffix)]
    return n


def folder_of(name: str) -> str:
    """A name's processed folder: the folder itself when one was given."""
    if "/" in name:
        return name.rstrip("/")
    return f"{PROCESSED}/{name}_translated"


def is_final(stem: str, books_dir: str = BOOKS) -> bool:
    try:
        return _overrides.load(stem, books_dir)["book"].get("final") is True
    except _overrides.OverridesError:
        return False


def _all(source: str) -> list:
    if source == "pdf":
        return sorted(os.path.basename(f)[:-4] for f in glob.glob(f"{BOOKS}/*.pdf"))
    return sorted(stem_of(f) for f in glob.glob(f"{PROCESSED}/*_translated") if os.path.isdir(f))


def select(argv: list, usage: str, source: str = "processed", writes: bool = True,
           books_dir: str = BOOKS) -> list:
    """The books (names as given, so a folder stays a folder) this run works
    on, from the positional arguments in `argv` (flags are the caller's),
    --all, or PDF_ONLY. No books named -> the usage and exit. `source`:
    "pdf" = books/*.pdf (step 1), "processed" = processed/<stem>_translated.
    `writes`: skip books marked final unless --unlock."""
    names = [a for a in argv if not a.startswith("--")]
    names += os.environ.get("PDF_ONLY", "").replace(",", " ").split()
    every = "--all" in argv
    if every:
        names = _all(source) + [n for n in names if n not in _all(source)]
    if not names:
        print(usage.strip() + "\n\nName the book(s) to work on, or --all.", file=sys.stderr)
        sys.exit(2)
    known = set(_all(source))
    unknown = [n for n in names if "/" not in n and n not in known]
    if unknown:
        where = f"{BOOKS}/<stem>.pdf" if source == "pdf" else f"{PROCESSED}/<stem>_translated"
        sys.exit(f"✗ no such book: {', '.join(unknown)} (looked for {where})")
    out, locked = [], []
    for n in dict.fromkeys(names):                       # de-duplicated, in order
        if writes and "--unlock" not in argv and is_final(stem_of(n), books_dir):
            locked.append(stem_of(n))
        else:
            out.append(n)
    if locked:
        print(f"⊘ finished, left alone: {', '.join(locked)} "
              f"([book] final = true in its overrides file; --unlock to change it anyway)")
    if not out:
        sys.exit(1 if not every else 0)
    return out


def check_unlocked(stem: str, argv: list, books_dir: str = BOOKS) -> None:
    """For a script given exactly one book (reread_pages, --pages, ...)."""
    if "--unlock" not in argv and is_final(stem, books_dir):
        sys.exit(f"⊘ {stem} is marked finished ([book] final = true in its overrides "
                 f"file) -- add --unlock to change it anyway")
