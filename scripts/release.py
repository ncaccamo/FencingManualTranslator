#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Copy a book's finished PDFs out under readable names, for sharing.

    python3 scripts/release.py <stem> [<stem> ...]      (on the host)
    python3 scripts/release.py <stem> --dry-run         just show the names

The pipeline's names stay as they are (processed/<stem>_translated/, which
the review tools read). This writes copies to release/<Name>/:

    release/Florio-1844-The-Science-of-Fencing-Explained/
        Florio-1844-The-Science-of-Fencing-Explained-English.pdf
        Florio-1844-The-Science-of-Fencing-Explained-Parallel-Italian-English.pdf

The name is author, year, short English title: how the books are cited
("Florio 1844"), in plain ASCII with hyphens, so it survives URLs, chat
attachments and every file system. From books/<stem>.meta.json:
  author_short   the author as the name should give it ("Joseph-Renaud",
                 "La Marche"); without it, the author part of the stem
  title_short_en the English title for the name, when title_en cut at its
                 first colon or full stop reads badly
  title_en       otherwise (the original title, with a warning, if none)
  language       for "Parallel-<Language>-English"

Warns -- it doesn't refuse -- when the book isn't marked finished
([book] final = true), when its review report still lists pages to check,
or when a PDF is older than the book's translation. release/ is git-ignored.
"""
import json
import os
import re
import shutil
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import _overrides  # noqa: E402

BOOKS = os.path.join(ROOT, "books")
PROCESSED = os.path.join(ROOT, "processed")
OUT = os.path.join(ROOT, "release")


def ascii_name(s: str) -> str:
    """'Fencing, the Duel and the Épée' -> 'Fencing-the-Duel-and-the-Epee'."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"['’`]", "", s)
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")


def meta_for(stem: str) -> dict:
    for d in (BOOKS, os.path.join(BOOKS, "_skip")):
        p = os.path.join(d, f"{stem}.meta.json")
        if os.path.exists(p):
            return json.load(open(p, encoding="utf-8"))
    return {}


def release_name(stem: str, meta: dict) -> tuple:
    """(name, warnings)."""
    warn = []
    parts = stem.split("_")
    author = meta.get("author_short") or "-".join(w.capitalize() for w in parts[0].split("-"))
    year = parts[1] if len(parts) > 1 else ""
    title = meta.get("title_short_en") or re.split(r"[:.;]", meta.get("title_en") or "")[0].strip()
    if not title:
        title = re.split(r"[:.;(]", meta.get("title") or parts[-1])[0].strip()
        warn.append("no title_en in its meta.json -- the name uses the original title")
    return "-".join(p for p in (ascii_name(author), year, ascii_name(title)) if p), warn


def main(argv: list) -> int:
    stems = [a for a in argv if not a.startswith("--")]
    if not stems:
        print(__doc__)
        return 2
    dry = "--dry-run" in argv
    for stem in stems:
        folder = os.path.join(PROCESSED, f"{stem}_translated")
        src = {"English": os.path.join(folder, f"{stem} — English Only (Portrait).pdf"),
               "Parallel": os.path.join(folder, f"{stem} — Side by Side (Landscape).pdf")}
        missing = [k for k, p in src.items() if not os.path.exists(p)]
        if missing:
            print(f"✗ {stem}: no {' / '.join(missing)} PDF in {folder} -- build it first")
            continue
        meta = meta_for(stem)
        name, warn = release_name(stem, meta)
        lang = ascii_name(meta.get("language") or "Original")
        files = {f"{name}-English.pdf": src["English"],
                 f"{name}-Parallel-{lang}-English.pdf": src["Parallel"]}
        try:
            if _overrides.load(stem, BOOKS)["book"].get("final") is not True:
                warn.append("not marked finished ([book] final = true in its overrides file)")
        except _overrides.OverridesError as e:
            warn.append(f"overrides file: {e}")
        qr = os.path.join(folder, "intermediate", "quality_report.json")
        if os.path.exists(qr):
            open_pages = {x["scan"] for x in json.load(open(qr, encoding="utf-8")) if x.get("level") == "CHECK"}
            if open_pages:
                warn.append(f"its review report lists {len(open_pages)} page(s) to check")
        tr = os.path.join(folder, "intermediate", "translation.txt")
        if os.path.exists(tr) and any(os.path.getmtime(p) < os.path.getmtime(tr) for p in src.values()):
            warn.append("a PDF is older than the translation -- rebuild the PDFs first")
        dest = os.path.join(OUT, name)
        print(f"{'(dry run) ' if dry else ''}{stem} -> release/{name}/")
        for new in files:
            print(f"    {new}")
        for w in warn:
            print(f"  ⚠ {w}")
        if not dry:
            os.makedirs(dest, exist_ok=True)
            for new, old in files.items():
                shutil.copy2(old, os.path.join(dest, new))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
