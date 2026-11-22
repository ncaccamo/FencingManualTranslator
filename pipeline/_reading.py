# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""The early reading guide: notes on how to read a book printed before 1800.

The model reads 19th- and 20th-century French well; a 1740 book trips it on
constructions that are rare later («ou» printed for «où», the «ne» that is
not a negation, «je fais tirer» for the author's own thrust …). The notes for
a language live in pipeline/early_reading_<code>.toml, one [[note]] each:

    id    = "ou-ou"                    a name, for the log and the tests
    when  = '\\bou,?\\s+venant'         optional: a regex on the page's source
                                       text; the note is sent only on pages
                                       where it matches (none = every page)
    line  = "«ou» is often «où» …"     what the model is told, with an example
    seen  = "Girard pdf 96, 97"        where the problem was found

Which books get it: a book printed before 1800 (its year from meta.json
"year", else from its name, girard_1740_armes), unless its overrides say
otherwise with [book] reading = "early" or "modern". A language with no
notes file gets nothing. A modern book's prompt is never touched.

The notes go with the page's own context (after the shared prompt), so the
part every page shares stays the same and the model server reuses it.
"""
import json
import os
import re
import tomllib

EARLY_BEFORE = 1800
_CODES = {"French": "fr", "Italian": "it"}
_HERE = os.path.dirname(os.path.abspath(__file__))
HEADING = "READING EARLY {lang} -- this book was printed before 1800; read these constructions as follows:"


class ReadingError(ValueError):
    pass


def notes_path(lang: str, base: str = _HERE) -> str:
    return os.path.join(base, f"early_reading_{_CODES.get(lang, lang.lower()[:2])}.toml")


def load(lang: str, base: str = _HERE) -> list:
    """The language's notes, validated; [] when it has no notes file."""
    p = notes_path(lang, base)
    if not os.path.exists(p):
        return []
    with open(p, "rb") as fh:
        raw = tomllib.load(fh)
    out, ids = [], set()
    for n, e in enumerate(raw.get("note") or [], 1):
        if set(e) - {"id", "when", "line", "seen"} or not e.get("id") or not e.get("line"):
            raise ReadingError(f"{p}: [[note]] #{n} needs id and line (optional: when, seen)")
        if e["id"] in ids:
            raise ReadingError(f"{p}: [[note]] id {e['id']!r} used twice")
        ids.add(e["id"])
        try:
            rx = re.compile(e["when"], re.IGNORECASE) if e.get("when") else None
        except re.error as err:
            raise ReadingError(f"{p}: [[note]] {e['id']}: when is not a valid regex: {err}") from None
        out.append({"id": e["id"], "when": rx, "line": e["line"].strip(), "seen": e.get("seen", "")})
    return out


def book_year(stem: str, books_dir: str = "/data/books"):
    """The year the book was printed: meta.json "year", else the 4-digit
    year in its name (author_1740_title); None when neither gives one."""
    for base in (books_dir, os.path.join(books_dir, "_skip")):
        mp = os.path.join(base, f"{stem}.meta.json")
        if os.path.exists(mp):
            try:
                y = json.load(open(mp, encoding="utf-8")).get("year")
                if isinstance(y, int) or (isinstance(y, str) and y.isdigit()):
                    return int(y)
            except (OSError, ValueError):
                pass
    m = re.search(r"_(1[4-9]\d\d)_", f"_{stem}_")
    return int(m.group(1)) if m else None


def is_early(stem: str, book_settings: dict, books_dir: str = "/data/books") -> bool:
    """[book] reading = "early" / "modern" decides; else printed before 1800."""
    r = (book_settings or {}).get("reading")
    if r in ("early", "modern"):
        return r == "early"
    y = book_year(stem, books_dir)
    return y is not None and y < EARLY_BEFORE


def for_page(notes: list, text: str) -> list:
    """The notes that apply to this page's source text."""
    return [n for n in notes if n["when"] is None or n["when"].search(text)]


def block(notes: list, text: str, lang: str = "French") -> tuple:
    """(the text to put before the page, the ids sent): ("", []) when no
    note applies."""
    hit = for_page(notes, text)
    if not hit:
        return "", []
    lines = "\n".join(f"- {n['line']}" for n in hit)
    return f"{HEADING.format(lang=lang.upper())}\n{lines}\n\n", [n["id"] for n in hit]
