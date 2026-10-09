#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Paper for reviewing a book: a checklist section, or caption entries to
fill in -- printed, to paste where you keep your notes.

    python3 /app/review_sheet.py <stem>              checklist section (Markdown)
    python3 /app/review_sheet.py <stem> --captions   overrides entries for the
                                                     captions the report flags

The checklist section lists the review report's checks in side-by-side PDF
page order as tick boxes ("pdf 44 (43) — caption: ..."), then the report's
notes in one line each, a sample-read item, and an empty "Your additions"
for what you find yourself. Run quality_report.py first.

The caption entries are commented out, one block per flagged page, with the
caption the second reading found (verify_captions.py) filled in: correct it
against the scan (the reading is OCR -- "Bolly dritte" for "Botte dritte"),
add caption_en if you want your own English, uncomment, and paste into
books/<stem>.overrides.toml; then apply_overrides.py and rebuild the PDFs.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _book(stem):
    F = f"/data/processed/{stem}_translated"
    I = f"{F}/intermediate"
    if not os.path.exists(f"{I}/quality_report.json"):
        sys.exit(f"✗ {stem}: no review report -- run quality_report.py {stem}")
    import quality_report
    import _pageio
    n = len(_pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read()))
    return F, I, json.load(open(f"{I}/quality_report.json", encoding="utf-8")), quality_report.page_map(F, n)


def checklist(stem):
    F, I, items, pm = _book(stem)
    meta = {}
    for d in ("/data/books", "/data/books/_skip"):
        if os.path.exists(f"{d}/{stem}.meta.json"):
            meta = json.load(open(f"{d}/{stem}.meta.json", encoding="utf-8"))
            break
    who = meta.get("author_short") or stem.split("_")[0].replace("-", " ").title()
    year = stem.split("_")[1] if "_" in stem else ""
    checks = sorted((x for x in items if x["level"] == "CHECK"), key=lambda x: (pm.get(x["scan"], 10**6), x["scan"]))
    notes = [x for x in items if x["level"] == "NOTE"]
    out = [f"## {who} {year}", "",
           f"Review report: **{len({x['scan'] for x in checks})}** page(s) to check, {len(notes)} note(s). "
           f"Page refs = side-by-side pdf page (scan page in brackets).", ""]
    for x in checks:
        where = f"pdf {pm[x['scan']]} ({x['scan']})" if x["scan"] in pm else f"scan {x['scan']}"
        out.append(f"- [ ] {where} — {x['kind']}: {x['detail']}")
    if notes:
        from collections import Counter
        c = Counter(x["kind"] for x in notes)
        out.append(f"- [ ] Notes in the report ({', '.join(f'{n} {k}' for k, n in c.most_common())}) — glance through")
    out += ["- [ ] Sample read: ~15 pages spread through the book, side by side; front matter, plates and captions.",
            "", "### Your additions", "", "", "---"]
    print("\n".join(out))


def captions(stem):
    F, I, items, pm = _book(stem)
    cr = json.load(open(f"{I}/caption_read.json", encoding="utf-8")) if os.path.exists(f"{I}/caption_read.json") else {}
    pages = sorted({x["scan"] for x in items if x["kind"] == "caption" and x["level"] == "CHECK"})
    if not pages:
        print(f"# {stem}: no caption checks in the review report")
        return
    out = [f"# --- captions to set ({len(pages)} page(s), from review_sheet.py) ---",
           "# Correct each caption against the scan, uncomment the block, then",
           "# apply_overrides.py and rebuild the PDFs.", ""]
    for s in pages:
        detail = next(x["detail"] for x in items if x["scan"] == s and x["kind"] == "caption")
        found = " — ".join(" ".join(f.split()) for f in cr.get(str(s), {}).get("found", []))
        where = f"pdf {pm[s]} (scan {s})" if s in pm else f"scan {s}"
        out += [f"# {where}: {detail[:110]}",
                f"# [pages.{s}]",
                f"# caption = {json.dumps(found or 'as printed', ensure_ascii=False)}",
                "# caption_en = \"\"            # optional: your own English", ""]
    print("\n".join(out))


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    (captions if "--captions" in argv else checklist)(args[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
