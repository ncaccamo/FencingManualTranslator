#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Rebuild a ruled table's rows from the scan itself, where the OCR model
lost, merged or shifted them -- no model, no GPU.

    python3 /app/table_from_scan.py <stem> <scan pages>            show the rebuilt rows
    python3 /app/table_from_scan.py <stem> <scan pages> --write    replace them in raw_ocr.txt
    python3 /app/table_from_scan.py <stem> 421:11,433:10 --write   each page with its row count

For tables printed with a rule between columns and blank space between
rows (Masiello 1887's synoptic tables: 64 of 135 table pages had rows lost
or shifted by the OCR, 2026-11-06 check). The page's own text layer (the
digitiser's OCR, with each word's place) gives the words; the printed
column rules, found in the page image, give the columns; a blank band
across the whole table gives a row break; inside a cell, a line that
starts at the cell's left edge is a new item (a <br> in the cell) and an
indented one continues the item above (a word broken over the line end is
rejoined). The OCR model's header rows (its <th> rows) are kept: they are
right on these pages, and they say how many columns there are.

A page is rebuilt only when the rules found give exactly the header's
number of columns; otherwise it is reported and left alone. With --write
the page's <table> in intermediate/raw_ocr.txt is replaced (the previous
file kept as raw_ocr.before-table-<date>.txt); then reprocess the book
(reprocess_extracted.py) and translate its tables (queue: tables <stem>).
A page given as "<scan>:<rows>" (or every page, with --rows=N) must come
out with that many rows -- the partner page's numbered rows -- and every
cell filled (the book prints "—" for nothing), or it is not written. Give
all the pages in one run: the backup is taken once, before the first.
"""
import datetime
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _books  # noqa: E402
import _overrides  # noqa: E402
import _pageio  # noqa: E402

DPI = 150


def header_rows(table_html: str) -> tuple:
    """(the <tr>…</tr> rows holding <th>, leaf column count) of the OCR's
    table. The leaves are the bottom header row's cells plus the cells that
    span down to it from above; a group heading spanning more columns than
    there are under it is narrowed to fit (Masiello pdf 432: «USCITE IN
    TEMPO» colspan 4 over three sub-headings)."""
    rows = re.findall(r"(?is)<tr\b.*?</tr>", table_html)
    head = [r for r in rows if "<th" in r.lower()]
    if not head:
        return [], 0
    span = lambda attrs, k: int((re.search(k + r'="?(\d+)', attrs) or [0, 1])[1])
    cells = [[(m.group(0), m.group(1)) for m in re.finditer(r"(?is)<th\b([^>]*)>", r)] for r in head]
    last = len(head) - 1
    leaves = len(cells[last]) + sum(1 for r_i in range(last) for _, at in cells[r_i]
                                    if r_i + span(at, "rowspan") - 1 >= last)
    fixed = []
    for r_i, r in enumerate(head):
        width = sum(span(at, "colspan") for _, at in cells[r_i])
        if r_i < last and width > leaves:
            excess = width - leaves
            for tag, at in cells[r_i]:
                cs = span(at, "colspan")
                if excess and cs > 1:
                    cut = min(excess, cs - 1)
                    new_at = re.sub(r'colspan="?\d+"?', f'colspan="{cs - cut}"', at)
                    r = r.replace(tag, tag.replace(at, new_at), 1)
                    excess -= cut
        fixed.append(r)
    return fixed, leaves


def column_rules(gray: np.ndarray, top: int, bottom: int) -> list:
    """x positions (pixels) of the printed vertical rules between `top` and
    `bottom`: thin lines darker than the paper for most of that height,
    standing out from the columns beside them (a yellowed page's tone and
    the scan's dark edges are not rules)."""
    dark = gray[top:bottom] < np.median(gray) - 35
    frac = dark.mean(axis=0)
    w = len(frac)
    xs = [x for x in range(int(0.015 * w), int(0.985 * w))
          if frac[x] > 0.28 and frac[x] - min(frac[max(0, x - 8):x + 9]) > 0.2]
    rules = []
    for x in xs:
        if rules and x - rules[-1][-1] <= 15:          # a double rule is one
            rules[-1].append(x)
        else:
            rules.append([x])
    return [int(sum(r) / len(r)) for r in rules]


def header_bottom(gray: np.ndarray, x0: int, x1: int, top: int, bottom: int) -> int:
    """y of the lowest horizontal rule in the table's upper third: the rule
    under the header."""
    band = gray[top:bottom, x0:x1] < np.median(gray) - 35
    frac = band.mean(axis=1)
    ys = [top + y for y in np.where(frac > 0.55)[0] if y < (bottom - top) / 3]
    return max(ys) if ys else top


def rebuild(page, table_html: str, want: int = None) -> tuple:
    """(rows as [[cell html]], message). Empty rows with a message when the
    page can't be rebuilt safely. `want`: the row count the page must have;
    the blank band that breaks a row is then taken at the height that gives
    it (rows sit closer on some pages than others)."""
    head, ncols = header_rows(table_html)
    if not ncols:
        return [], "no header row in the OCR's table"
    pm = page.get_pixmap(dpi=DPI, colorspace="gray")
    gray = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)
    sx, sy = pm.width / page.rect.width, pm.height / page.rect.height
    # the table's height: where the vertical rules run
    first = column_rules(gray, 0, gray.shape[0])
    if not first:
        return [], "no vertical rules found"
    dark = gray < np.median(gray) - 35
    on = dark[:, first].mean(axis=1) > 0.5
    ys = np.where(on)[0]
    if not len(ys):
        return [], "no table height found"
    top, bottom = int(ys.min()), int(ys.max())
    rules = column_rules(gray, top, bottom)
    if len(rules) < 2:
        return [], "fewer than two column rules"
    # the outer frame may be ruled or not: columns are the gaps between rules,
    # plus the text outside the first / last rule when the frame is open
    # each word with the text layer's own (block, line): its line boxes can
    # overlap in a centred cell (Masiello pdf 422: "passata sotto" and "o
    # controazione" a third of a line apart), so lines go by those numbers
    words = [(w[0] * sx, w[1] * sy, w[2] * sx, w[3] * sy, w[4], (w[5], w[6])) for w in page.get_text("words")
             if re.search(r"\w|[—–-]", w[4])            # a speck read as "." or "'" is no word
             and (len(w[4]) > 1 or w[4] in "oeaiyàèOEAIY0123456789—–-")]   # nor one read as "w", "M"
    left = int(min((w[0] for w in words if top < w[1] < bottom), default=rules[0]))
    hb = header_bottom(gray, min(left, rules[0]), rules[-1], top, bottom)
    # the rules can stop short of the last row's last lines: go on down
    # while lines follow at a line's spacing (Masiello pdf 704)
    lh0 = sorted(w[3] - w[1] for w in words)[len(words) // 2] if words else 10
    # -- a word that fits inside one column is still the table's; a line of
    # running text under it (a note, a paragraph) crosses the rules
    below = sorted((w for w in words if w[1] >= bottom), key=lambda w: w[1])
    lines_below = {}
    for w in below:
        lines_below.setdefault(w[5], []).append(w)
    for ln in sorted(lines_below.values(), key=lambda l: min(x[1] for x in l)):
        x0, x1 = min(x[0] for x in ln), max(x[2] for x in ln)
        if any(x0 < r < x1 for r in rules) or ln[0][1] > 0.95 * gray.shape[0]:
            break
        bottom = max(bottom, int(max(x[3] for x in ln)))
    body = [w for w in words if w[1] > hb + 2 and w[3] < bottom + 4]
    edges = list(rules)
    if any(w[2] <= rules[0] for w in body):
        edges = [min(w[0] for w in body) - 2] + edges
    if any(w[0] >= rules[-1] for w in body):
        edges = edges + [max(w[2] for w in body) + 2]
    cols = [(edges[k], edges[k + 1]) for k in range(len(edges) - 1) if edges[k + 1] - edges[k] > 8]
    if len(cols) != ncols:
        return [], f"{len(cols)} columns between the rules, the header has {ncols}"
    # text lines: words grouped by their middle, within a column
    def col_of(w):
        mid = (w[0] + w[2]) / 2
        return next((k for k, (a, b) in enumerate(cols) if a <= mid < b), None)
    heights = sorted(w[3] - w[1] for w in body) or [10]
    lh = heights[len(heights) // 2]
    # row breaks: blank bands across the whole table, taller than a line gap
    occupied = np.zeros(bottom + 10, dtype=bool)
    for w in body:
        occupied[int(w[1]):int(w[3]) + 1] = True
    y0 = int(hb) + 2

    def bands_at(gap):
        breaks, run = [], 0
        for y in range(y0, bottom + 5):
            if not occupied[y]:
                run += 1
            else:
                if run > gap * lh and y - run > y0:
                    breaks.append(y - run // 2)
                run = 0
        out = [(a, b) for a, b in zip([y0] + breaks, breaks + [bottom + 5])
               if any(a <= (w[1] + w[3]) / 2 < b for w in body)]
        # the header's last line when its rule was not found: words all in capitals
        while out and all(re.sub(r"[^\w]", "", w[4]).isupper() for w in body
                          if out[0][0] <= (w[1] + w[3]) / 2 < out[0][1] and re.search(r"[^\W\d_]", w[4])):
            out = out[1:]
        return out
    spacings = [0.9]
    if want is not None and len(bands_at(0.9)) != want:
        spacings = [g / 20 for g in range(6, 41) if len(bands_at(g / 20)) == want]
        if not spacings:
            # rows set too close for a blank band: a column with one line a
            # row gives them -- each line a row, the breaks halfway between
            for k in range(1, len(cols)):
                lines = {}
                for w in body:
                    if col_of(w) == k:
                        lines.setdefault(w[5], []).append(w)
                tops = sorted((min(x[1] for x in g) + max(x[3] for x in g)) / 2 for g in lines.values()
                              if not all(re.sub(r"[^\w]", "", x[4]).isupper() for x in g))   # header lines
                cells = []                         # a cell's lines (a phrase wrapped) sit a line apart
                for m in tops:
                    if cells and m - cells[-1][-1] < 1.8 * lh:
                        cells[-1].append(m)
                    else:
                        cells.append([m])
                mids = [sum(c) / len(c) for c in cells]
                if len(mids) == want:
                    cuts = [(mids[n] + mids[n + 1]) / 2 for n in range(want - 1)]
                    first = max(y0, mids[0] - (cuts[0] - mids[0]) if cuts else y0)    # not the header above
                    rows, why = _rows(list(zip([first] + cuts, cuts + [bottom + 5])), body, cols, col_of, lh)
                    if rows:
                        return rows, ""
            return [], f"no row spacing gives {want} rows (at the usual spacing {len(bands_at(0.9))})"
        spacings.sort(key=lambda g: abs(g - 0.9))   # nearest the usual spacing first
    why = ""
    for gap in spacings:
        rows, why = _rows(bands_at(gap), body, cols, col_of, lh)
        if rows:
            return rows, ""
    return [], why


def _rows(bands, body, cols, col_of, lh):
    rows = []
    for a, b in bands:
        ws = [w for w in body if a <= (w[1] + w[3]) / 2 < b]
        if not ws:
            continue
        cells = []
        for k, (ca, cb) in enumerate(cols):
            groups = {}
            for w in ws:
                if col_of(w) == k:
                    groups.setdefault(w[5], []).append(w)
            lines = sorted(([min(x[1] for x in g), min(x[0] for x in g), g] for g in groups.values()),
                           key=lambda l: l[0])
            lines = [(l[0], l[1], " ".join(x[4] for x in sorted(l[2], key=lambda x: x[0]))) for l in lines]
            left = min((l[1] for l in lines), default=ca)
            # a left-aligned column (the actions: one item a line, a run-on
            # line indented) or a centred one (one phrase over two lines)
            aligned = len(lines) > 1 and (cb - ca) > 0.25 * (cols[-1][1] - cols[0][0])
            items = []
            for y, x, text in lines:
                if items and (not aligned or x > left + 0.8 * lh):    # the item above goes on
                    if items[-1].endswith("-"):
                        items[-1] = items[-1][:-1] + text
                    else:
                        items[-1] += " " + text
                else:
                    items.append(text)
            cells.append("<br>".join(items))
        rows.append(cells)
    # every cell holds something (the book prints "—" for nothing): a row
    # with empty cells is a multi-line cell cut in two, or rows run together
    for r in rows:
        # a row of one note mark or a dash in every column («(2)», «—»): a
        # mark the text layer missed is the same mark (Masiello pdf 414)
        marks = {c.strip() for c in r[1:] if c.strip()}
        if len(marks) == 1 and re.fullmatch(r"\(\d\)|[—–-]", next(iter(marks))):
            r[1:] = [c if c.strip() else next(iter(marks)) for c in r[1:]]
    bare = [n + 1 for n, r in enumerate(rows) if any(not c.strip() for c in r)]
    if bare:
        return [], f"row(s) {', '.join(map(str, bare[:6]))} with empty cells"
    return rows, ""


def _edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def book_vocab(raw_pages: list) -> dict:
    """{word: count} over the book's OCR text, lowercased."""
    from collections import Counter
    return Counter(w.lower() for p in raw_pages for w in re.findall(r"[^\W\d_]{3,}", p))


def tidy(cell: str, vocab: dict) -> str:
    """A lone speck read as punctuation dropped; a word the book never
    prints, corrected when exactly one word it prints often is within two
    letters, same first letter («sceonda» -> «seconda», «avversano» ->
    «avversario») -- the digitiser's typos; a lone "0" between words is the
    word «o» (Masiello pdf 404: "passata sotto 0 controazione"). Anything
    less sure stays."""
    cell = re.sub(r"(?<=\s)[.,;:'`]+(?=\s|$|<br>)|(?<=>)\s*[.,;:'`]+\s+", " ", cell)
    cell = re.sub(r"([^\W\d_],?) 0(?= [^\W\d_])", r"\1 o", cell)
    cell = re.sub(r"(?<=[\s,])-(?=[a-zà-ÿ])", "", cell)              # a speck stuck to a word: "-fianconata"
    cell = re.sub(r"(^|<br>)\s*[iI]\s+(?=[a-zà-ÿ])", r"\1", cell)  # an item never opens on a lone "i"
    def fix(m):
        w = m.group(0)
        lw = w.lower()
        if vocab.get(lw, 0) >= 2 or len(w) < 5:
            return w
        near = [v for v, n in vocab.items() if n >= 3 and v[0] == lw[0] and abs(len(v) - len(lw)) <= 2
                and _edits(v, lw) <= 2]
        if len(near) != 1:
            return w
        v = near[0]
        return v.capitalize() if w[0].isupper() else v
    return re.sub(r"\s{2,}", " ", re.sub(r"[^\W\d_]+", fix, cell)).strip()


def table_html(head: list, rows: list) -> str:
    out = ["<table>"] + ["  " + h.strip() for h in head]
    for r in rows:
        out.append("  <tr>")
        out += [f"    <td>{c}</td>" for c in r]
        out.append("  </tr>")
    out.append("</table>")
    return "\n".join(out)


def main(argv: list) -> int:
    args = [a for a in argv if not a.startswith("--")]
    want = next((int(a.split("=", 1)[1]) for a in argv if a.startswith("--rows=")), None)
    if len(args) != 2:
        print(__doc__)
        return 2
    name, spec = args
    stem = _books.stem_of(name)
    _books.check_unlocked(stem, argv)
    I = f"{_books.folder_of(name)}/intermediate"
    import fitz
    pdf = next((os.path.join(I, f) for f in os.listdir(I) if f.endswith(".pdf") and "OCR" in f), None)
    raw = _pageio.split_pages(open(f"{I}/raw_ocr.txt", encoding="utf-8").read())
    doc = fitz.open(pdf)
    vocab = book_vocab(raw)
    done = 0
    counts = {}
    for tok in spec.split(","):
        page, _, n = tok.partition(":")
        for p in _overrides.parse_page_spec(page):
            counts[p] = int(n) if n else want
    for scan, want in counts.items():
        page_raw = raw[scan - 1]
        m = re.search(r"(?is)<table\b.*?</table\s*>", page_raw)
        if not m:
            print(f"  ✗ scan {scan}: the OCR has no <table> on this page")
            continue
        rows, why = rebuild(doc[scan - 1], m.group(0), want)
        rows = [[tidy(c, vocab) for c in r] for r in rows]
        if not rows:
            print(f"  ✗ scan {scan}: left as it is -- {why}")
            continue
        if want is not None and len(rows) != want:
            print(f"  ✗ scan {scan}: {len(rows)} rows, {want} expected -- left as it is")
            continue
        head, _ = header_rows(m.group(0))
        old_n = len([r for r in re.findall(r"(?is)<tr\b.*?</tr>", m.group(0)) if "<th" not in r.lower()])
        print(f"  ✓ scan {scan}: {len(rows)} rows (the OCR had {old_n})")
        for r in rows:
            print("      | " + " | ".join(c.replace("<br>", " / ") for c in r))
        raw[scan - 1] = page_raw[:m.start()] + table_html(head, rows) + page_raw[m.end():]
        done += 1
    if "--write" in argv and done:
        stamp = datetime.date.today().isoformat()
        n = 1
        while os.path.exists(f"{I}/raw_ocr.before-table-{stamp}{'' if n == 1 else f'-{n}'}.txt"):
            n += 1                                 # an earlier run's backup is never replaced
        stamp += "" if n == 1 else f"-{n}"
        os.replace(f"{I}/raw_ocr.txt", f"{I}/raw_ocr.before-table-{stamp}.txt")
        with open(f"{I}/raw_ocr.txt", "w", encoding="utf-8") as fh:
            fh.write(_pageio.join_pages(raw))
        print(f"✓ {done} table(s) rebuilt in raw_ocr.txt; previous kept as raw_ocr.before-table-{stamp}.txt\n"
              f"  next: reprocess_extracted.py {stem}, then translate its tables (queue: tables {stem})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
