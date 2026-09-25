#!/usr/bin/env python3
"""Run the page-layout detector over a book's scan -> intermediate/layout.json.

    python3 /app/layout_scan.py <stem> [<stem> ...]      (or --all)

CPU only, ~0.5 s/page, resumable. Step 1 runs this itself for new books;
use it to add layout data to books OCR'd before it existed. See _layout.py.
"""
import glob
import os
import sys

import _layout


def main(stems):
    for stem in stems:
        folder = f"/data/processed/{stem}_translated"
        pdfs = glob.glob(f"{folder}/intermediate/*_OCR.pdf")
        if not pdfs:
            print(f"✗ {stem}: no scan PDF in {folder}/intermediate")
            continue
        print(f"Layout: {stem}")
        data = _layout.scan_book(folder, pdfs[0])
        print(f"✓ {stem}: {len(data['pages'])} page(s) in intermediate/layout.json")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1:] == ["--all"]:
        main(sorted(os.path.basename(f)[:-len("_translated")]
                    for f in glob.glob("/data/processed/*_translated")))
    else:
        main(sys.argv[1:])
