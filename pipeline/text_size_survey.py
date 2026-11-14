#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""How large can a book's side-by-side text be? Builds the side-by-side PDF
at each size asked for, into /tmp (the book's own PDFs and files are not
touched), and shows how many text pages are drawn at that size, how many
step down to a smaller one, and how many spill onto a "(cont.)" sheet.

    python3 /app/text_size_survey.py <stem>                 9, 10, 11 and 12 pt
    python3 /app/text_size_survey.py <stem> 10,10.5,11      these sizes

Then set the one size for the whole book in its overrides file -- the
largest at which all but a few dense pages fit, and none spills:

    [book]
    text_size = 10.5

A page that does not fit at the book's size steps down a point at a time
(to 7 pt), as at the default 9 pt. Only the side-by-side PDF changes.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _books  # noqa: E402


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if not args or len(args) > 2:
        print(__doc__)
        return 2
    stem = _books.stem_of(args[0])
    sizes = [float(s) for s in (args[1].split(",") if len(args) > 1 else ("9", "10", "11", "12"))]
    print(f"{stem}: side-by-side text size survey\n")
    print(f"  {'size':>5}  {'at size':>8}  {'smaller':>8}  {'spill':>6}  sheets   smaller / spilled on pdf")
    for s in sizes:
        out = f"/tmp/fmt_text_size_{stem}_{s:g}.pdf"
        env = dict(os.environ, FMT_TEXT_SIZE=str(s), FMT_SURVEY_OUT=out)
        r = subprocess.run([sys.executable, f"{HERE}/4_side_by_side_pdf.py", stem, "--unlock"],
                           env=env, capture_output=True, text=True)
        try:
            d = json.load(open(out[:-4] + ".sizes.json"))
        except (OSError, ValueError):
            print(f"  {s:>5g}  the build failed:\n{r.stdout[-800:]}{r.stderr[-800:]}")
            continue
        pages = d["pages"]
        spill = [k for k, v in pages.items() if v == "cont."]
        small = [k for k, v in pages.items() if v != "cont." and v < s]
        import fitz
        sheets = fitz.open(out).page_count
        where = ", ".join(sorted(small + [k + " (cont.)" for k in spill], key=lambda k: int(k.split()[0])))
        print(f"  {s:>5g}  {len(pages) - len(small) - len(spill):>8}  {len(small):>8}  {len(spill):>6}  {sheets:>6}   "
              + (where[:300] + (" …" if len(where) > 300 else "")))
        for f in (out, out[:-4] + ".sizes.json"):
            try:
                os.remove(f)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
