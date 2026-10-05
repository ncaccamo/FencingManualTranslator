#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Run the page-layout detector over a book's scan -> intermediate/layout.json.

    python3 /app/layout_scan.py <stem> [<stem> ...]      (or --all)
    python3 /app/layout_scan.py --margins <stem> ...     also read margin notes
    python3 /app/layout_scan.py --heads <stem> ...       read running heads the OCR
                                                          reply left out (-> heads.json)

Layout: CPU only, ~0.5 s/page, resumable. --margins also reads each margin
note with the OCR model (GPU, ~0.5 s/note) into intermediate/margins.json;
run reprocess_extracted.py afterwards to place them in the page text. Step 1
does both itself for new books. See _layout.py and _margins.py.
"""
import glob
import os
import sys

import _layout
import _margins


def main(stems, margins=False, heads=False):
    for stem in stems:
        folder = f"/data/processed/{stem}_translated"
        pdfs = glob.glob(f"{folder}/intermediate/*_OCR.pdf")
        if not pdfs:
            print(f"✗ {stem}: no scan PDF in {folder}/intermediate")
            continue
        print(f"Layout: {stem}")
        data = _layout.scan_book(folder, pdfs[0])
        print(f"✓ {stem}: {len(data['pages'])} page(s) in intermediate/layout.json")
        if heads:
            got = _margins.read_heads(folder, pdfs[0])
            print(f"✓ {stem}: {len(got)} running head(s) read into intermediate/heads.json")
        if margins:
            notes = _margins.read_book(folder, pdfs[0])
            print(f"✓ {stem}: {sum(len(v) for v in notes.values())} margin note(s) "
                  f"in intermediate/margins.json -- now run reprocess_extracted.py")


if __name__ == "__main__":
    args = sys.argv[1:]
    margins = "--margins" in args
    heads = "--heads" in args
    args = [a for a in args if a not in ("--margins", "--heads")]
    import _books
    main([_books.stem_of(n) for n in _books.select(args, __doc__)], margins, heads)
