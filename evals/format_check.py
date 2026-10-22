#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""How far the English layout is from the print: for every page whose scan
has a trustworthy text layer, the paragraph starts and the gaps the book
prints (_textlayer: line starts and line pitch) against those the English
PDF draws (_render.layout, after _textlayer.apply, as step 3 does).
Changes nothing; a measure, to find what's left and whether it is a pattern.

    python3 evals/format_check.py <stem> [<stem> ...] | --all      (in the image)

Per book: evals/reports/format/<stem>.md -- the pages that differ, by pdf
page, worst first; a summary line per book on stdout. Counts per page, not
a line-by-line match (the English is a translation): a page "differs" when
the numbers of paragraph starts or of gaps differ.

Not counted: pages with figures or tables, contents pages, pages with no
usable layer. A gap in print is a line pitch 1.3x the page's usual one
(more than 3x: a picture or a page break -- left out); a line missing
from the layer can fake one, so gaps are the weaker signal.
"""
import json
import os
import re
import sys

sys.path.insert(0, "/app")
import fitz  # noqa: E402
import _books  # noqa: E402
import _pageio  # noqa: E402
import _render  # noqa: E402
import _textlayer as T  # noqa: E402
import quality_report as q  # noqa: E402

OUT = "/data/evals/reports/format"
SIZE, WIDTH = 10, 480.0             # step 3's body size; the width only changes wrapping


def printed(page, rows):
    """(paragraph starts, gaps) the print shows, or None (no usable layer).
    Starts: lines at the page's paragraph indent that open a paragraph --
    not headings, centred lines or a drop capital's lines."""
    body = T._body(T.page_lines(page), rows)
    if len(body) < 4:
        return None
    kind, where = T.profile(body, page.rect.width)
    if kind is None:
        return None
    starts = sum(1 for k, w in zip(kind, where) if k == "start" and w == "indent")
    ys = [l[1] for l in body]
    pitch = [ys[k] - ys[k - 1] for k in range(1, len(ys))]
    normal = sorted(p for p in pitch if p > 0)[len(pitch) // 2] if pitch else 0
    gaps = sum(1 for p in pitch if normal and 1.3 * normal < p <= 3 * normal)
    return starts, gaps


def drawn(english, paras, centre):
    """(paragraph starts, gaps) the English layout draws. Starts: first
    lines with the paragraph indent (an indented book)."""
    lines = _render.layout(english, WIDTH, SIZE, paras=paras, centre=centre)
    first = SIZE * _render.PARA_INDENT_EM
    starts = sum(1 for ln in lines if not ln.gap and not ln.note and not ln.table
                 and abs(ln.indent - first) < 0.5)
    gaps = sum(1 for ln in lines if ln.gap)
    return starts, gaps


def check(stem):
    F = f"/data/processed/{stem}_translated"
    I = f"{F}/intermediate"
    pdf = next((os.path.join(I, f) for f in os.listdir(I) if f.endswith(".pdf") and "OCR" in f), None)
    if not pdf or not os.path.exists(f"{I}/translation.txt"):
        return None
    src = fitz.open(pdf)
    tr = _pageio.split_pages(open(f"{I}/translation.txt", encoding="utf-8").read())
    ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
    figs = {}
    if os.path.exists(f"{I}/figures.json"):
        try:
            figs = json.load(open(f"{I}/figures.json", encoding="utf-8"))
        except ValueError:
            figs = {}
    pmap = q.page_map(F, len(tr))
    paras = _render.book_paragraphs(stem)
    if paras != "indented":
        return None                          # starts are counted by indent
    centre = _render.centres_headings(F)
    rows, checked = [], 0
    for i, t in enumerate(tr):
        if i >= len(src) or i >= len(ex) or not t.strip() or figs.get(str(i)):
            continue
        body, _ = _render.split_footnotes(t.strip())
        if (_render.toc_page(body) or "<table" in ex[i] or "[[TABLE]]" in body
                or len(body.split()) < 40 or body.startswith("[")):
            continue
        sb = ex[i].split("BODY_TEXT:", 1)[-1]
        p = printed(src[i], T._source_rows(sb))
        if p is None:
            continue
        english = T.apply(body, sb, src[i], src[i - 1] if i else None)
        d = drawn(english, paras, centre)
        checked += 1
        if p != d:
            rows.append((abs(p[0] - d[0]) * 2 + abs(p[1] - d[1]), pmap.get(i + 1, f"scan {i + 1}"), p, d))
    rows.sort(key=lambda r: -r[0])
    starts_off = sum(1 for _, _, p, d in rows if p[0] != d[0])
    gaps_off = sum(1 for _, _, p, d in rows if p[1] != d[1])
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/{stem}.md", "w", encoding="utf-8") as f:
        f.write(f"# Layout against the print: {stem}\n\n"
                f"{checked} pages checked; paragraph starts differ on {starts_off}, gaps on {gaps_off}.\n\n"
                "| pdf | starts: print / English | gaps: print / English |\n|---|---|---|\n")
        for _, pdfn, p, d in rows:
            f.write(f"| {pdfn} | {p[0]} / {d[0]} | {p[1]} / {d[1]} |\n")
    return checked, starts_off, gaps_off


def main():
    for stem in _books.select(sys.argv[1:], __doc__, writes=False):
        r = check(stem)
        if r:
            c, s, g = r
            print(f"{stem:42s} checked {c:4d}  starts differ {s:4d} ({100 * s // max(c, 1):2d}%)  "
                  f"gaps differ {g:4d} ({100 * g // max(c, 1):2d}%)", flush=True)


if __name__ == "__main__":
    main()
