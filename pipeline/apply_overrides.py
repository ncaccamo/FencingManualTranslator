#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Apply books/<stem>.overrides.toml to a book's existing outputs -- no model,
no reprocess. Run after editing the overrides file, then rebuild the PDFs:

    python3 /app/apply_overrides.py <stem> [<stem> ...]
    PDF_ONLY=<stem> python3 /app/4_side_by_side_pdf.py   (and 3_ for English-only)

Page-level decisions (page_number, header) are written into extracted.txt /
page_numbers.txt; [headers] into headers.json; [[fix]] into translation.txt.
Step 1 / reprocess_extracted.py / 2_translate.py apply the same file on every
run, so nothing here is lost when a book is regenerated.
"""
import json
import os
import re
import sys

import _overrides
import _pageio

# reuse step 2's helpers without starting a translation server
_src = open(os.path.join(os.path.dirname(__file__), "2_translate.py"), encoding="utf-8").read()
_ns = {"__name__": "_tx_prefix"}
exec(compile(_src.split('\nprint("\\n" + "="*60)\nprint("STEP 2')[0], "2_translate.py", "exec"), _ns)
apply_book_overrides = _ns["apply_book_overrides"]


def apply_page_overrides(folder: str, ov: dict) -> int:
    I = f"{folder}/intermediate"
    ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
    pn = open(f"{I}/page_numbers.txt", encoding="utf-8").read().split("\n")
    _overrides._check_range(ov, len(ex), "overrides")
    changed = 0
    for scan, v in ov["pages"].items():
        i = scan - 1
        page = ex[i]
        if "page_number" in v:
            page = re.sub(r"(?m)^PAGE_NUMBER:.*$", f"PAGE_NUMBER: {v['page_number']}", page, count=1)
            if i < len(pn):
                pn[i] = str(v["page_number"])
        if "header" in v:
            page = re.sub(r"(?m)^HEADER:.*$", f"HEADER: {v['header'] or 'none'}", page, count=1)
        if page != ex[i]:
            ex[i] = page
            changed += 1
    if changed:
        with open(f"{I}/extracted.txt", "w", encoding="utf-8") as fh:
            fh.write(_pageio.join_pages(ex))
        with open(f"{I}/page_numbers.txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(pn))
    return changed


def main(stems):
    for stem in stems:
        folder = f"/data/processed/{stem}_translated"
        if not os.path.isdir(folder):
            print(f"✗ no such book: {folder}")
            continue
        ov = _overrides.load(stem)
        n = apply_page_overrides(folder, ov)
        # a header override naming a French head with no English yet
        hp = f"{folder}/intermediate/headers.json"
        hmap = json.load(open(hp, encoding="utf-8")) if os.path.exists(hp) else {}
        for v in ov["pages"].values():
            h = v.get("header")
            if h and h not in hmap and h not in ov["headers"]:
                print(f"  ⚠ header {h!r} has no English: add it under [headers] "
                      f"in {_overrides.path_for(stem)}")
        apply_book_overrides(folder)
        print(f"✓ {stem}: {n} page setting(s) applied")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
