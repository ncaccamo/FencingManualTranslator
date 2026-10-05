#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Hand-edit a page's English -- for the last few errors in a book that is
nearly done, where a rerun would cost more than a fix by hand.

Run on the HOST, from the repo (books/ is read-only in the container):

    python3 pipeline/edit_page.py <stem> <scan pages>      e.g. 91  or  12,40-42

For each page this writes books/<stem>.edits/scan-0091.txt: a header with
the page's original text for reference, a "---" line, then the page's
current English. Edit the English below the line in any editor and save.
A page that already has an edit file is left as it is (its path is shown).

Then put the edits into the book and rebuild its PDFs (in the container):

    python3 /app/apply_overrides.py <stem>
    python3 /app/3_english_only_pdf.py <stem>
    python3 /app/4_side_by_side_pdf.py <stem>

The edit wins over the machine English on every later run. If the page's
source text changes afterwards (a re-read, a pipeline fix), the review
report lists the edit as possibly out of date. Delete the file to go back
to the machine translation. For a one-word fix, a [[fix]] find/replace in
the overrides file is lighter (manual III.5b).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _overrides  # noqa: E402
import _pageio  # noqa: E402

ROOT = os.path.dirname(HERE)
BOOKS = os.path.join(ROOT, "books")
PROCESSED = os.path.join(ROOT, "processed")


def main(argv: list) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        return 2
    if HERE == "/app" or not os.access(BOOKS, os.W_OK):
        sys.exit(f"✗ {BOOKS} is not writable here -- run this on the host, from the repo")
    stem, spec = args
    I = os.path.join(PROCESSED, f"{stem}_translated", "intermediate")
    tp = os.path.join(I, "translation.txt")
    if not os.path.exists(tp):
        sys.exit(f"✗ {stem}: not translated yet ({tp})")
    tr = _pageio.split_pages(open(tp, encoding="utf-8").read())
    ex = _pageio.split_pages(open(os.path.join(I, "extracted.txt"), encoding="utf-8").read())
    fp_path = os.path.join(I, "translated_from.json")
    fps = json.load(open(fp_path, encoding="utf-8"))["pages"] if os.path.exists(fp_path) else []
    d = _overrides.edits_dir(stem, BOOKS)
    os.makedirs(d, exist_ok=True)
    for scan in _overrides.parse_page_spec(spec):
        if not 1 <= scan <= len(tr):
            print(f"  ✗ scan {scan}: the book has {len(tr)} pages")
            continue
        path = os.path.join(d, f"scan-{scan:04d}.txt")
        if os.path.exists(path):
            print(f"  = {path}  (already being edited)")
            continue
        original = ex[scan - 1].split("BODY_TEXT:", 1)[-1] if scan <= len(ex) else ""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_overrides.edit_file_text(stem, scan, fps[scan - 1] if scan <= len(fps) else "",
                                               tr[scan - 1], original))
        print(f"  + {path}")
    print(f"\nEdit the English below the --- line, save, then in the container:\n"
          f"  python3 /app/apply_overrides.py {stem}\n"
          f"  python3 /app/3_english_only_pdf.py {stem} && python3 /app/4_side_by_side_pdf.py {stem}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
