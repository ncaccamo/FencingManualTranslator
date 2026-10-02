#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Split a scan of two-page spreads into one book page per scan page.

The pipeline assumes one printed page per scan page: one page number, one
running head, one set of footnotes, one English page beside one original.
A spread scan (an open book photographed flat) breaks all of these -- the
OCR reads both pages as one, keeps only the left page's number, so the
page-number sequence runs at half speed and footnote "(1)"s collide
(Bruno, Lambertini, Masiello 1902 Fioretto, Tiboldi; 2026-10-01).

    python3 /app/split_spreads.py <stem>            # dry run: find the gutters, write previews
    python3 /app/split_spreads.py <stem> --apply    # write the split scan
    (then the printed host commands: books/ is read-only in the container)

Each landscape page is cut at its gutter: the widest band of columns near
the middle with no print in it (low vertical contrast -- a fold shadow is
smooth, text is not). Portrait pages (covers, single plates) pass through
whole. The cut is lossless: each half is the original page clipped, not
re-rendered. Previews (every page, cut line in red) go to
/data/processed/_audit/split_<stem>/ for a look before applying.

Where the automatic cut is wrong -- ruled tables and drawings spanning
the spread defeat it, and a fold-out plate is one page, not two -- the
book's corrections go in books/<stem>.split, one line per scan page:

    140: 0.512        # cut here (share of the width; read off the preview)
    63-94: whole      # leave whole

The original stays at books/_alt/<stem>_original.pdf. The split scan has
new page numbering throughout, so the book is OCR'd again from step 1
(the old processed folder is moved aside first; see --apply's output).
"""
import json
import os
import sys

import fitz
import numpy as np
from PIL import Image, ImageDraw

ZONE = (0.25, 0.75)   # where the fold can be, as a share of the width
DPI = 50              # detection resolution


def profiles(page):
    """Per column: vertical contrast (mean |row-to-row difference|, print is
    high) and brightness, both 0..1, over the middle 80% of the height."""
    pix = page.get_pixmap(matrix=fitz.Matrix(DPI / 72, DPI / 72), colorspace=fitz.csGRAY)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).astype(float)
    h = a.shape[0]
    a = a[int(h * 0.1):int(h * 0.9)]                     # skip top/bottom edges of the photo
    k = np.ones(3) / 3
    ink = np.convolve(np.abs(np.diff(a, axis=0)).mean(axis=0) / 255, k, mode="same")
    light = np.convolve(a.mean(axis=0) / 255, k, mode="same")
    return ink, light, pix


def text_gap(ink):
    """The print-free gap between the two pages' text blocks: a run of
    columns with no print and print on both sides (the outer margins have
    print on one side only). Returns (cut, width) as shares of the width,
    or None when there isn't exactly one clear such gap -- a blank page,
    a ruled table or a drawing gives none or several."""
    w = len(ink)
    base = np.percentile(ink, 10)
    printed = ink > base + 0.3 * (np.percentile(ink, 90) - base)
    mass = np.cumsum(printed)
    total = mass[-1]
    lo, hi = int(w * ZONE[0]), int(w * ZONE[1])
    gaps = []
    k = lo
    while k < hi:
        if printed[k]:
            k += 1
            continue
        a = k
        while k < w and not printed[k]:
            k += 1
        left, right = (mass[a - 1] if a else 0), total - mass[k - 1]
        if min(left, right) >= 0.15 * total:
            gaps.append((k - a, a, k))
    gaps.sort(reverse=True)
    if not gaps or gaps[0][0] < 0.02 * w or (len(gaps) > 1 and gaps[0][0] < 2 * gaps[1][0]):
        return None
    width, a, b = gaps[0]
    return (a + b) / 2 / w, width / w


def fold_line(light, near, reach=0.08):
    """The fold's shadow: the column within `reach` of `near` that is
    darkest compared with the paper a few columns either side."""
    w = len(light)
    best, at = -1.0, near
    for k in range(max(8, int(w * (near - reach))), min(w - 8, int(w * (near + reach)))):
        around = (light[k - 8:k - 2].mean() + light[k + 3:k + 9].mean()) / 2
        if around - light[k] > best:
            best, at = around - light[k], k / w
    return at


def read_corrections(stem):
    """books/<stem>.split -> {scan: cut share | "whole"}."""
    path = f"/data/books/{stem}.split"
    fixes = {}
    if not os.path.exists(path):
        return fixes
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.split("#")[0].strip()
        if not line:
            continue
        pages, _, what = line.partition(":")
        what = what.strip()
        a, _, b = pages.strip().partition("-")
        if what != "whole":
            what = float(what)
            if not 0.2 < what < 0.8:
                sys.exit(f"✗ {path} line {n}: cut {what} is not a share of the width")
        for k in range(int(a), int(b or a) + 1):
            fixes[k] = what
    return fixes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    stem = args[0]
    apply = "--apply" in sys.argv
    src_path = f"/data/books/{stem}.pdf"
    orig = f"/data/books/_alt/{stem}_original.pdf"
    if os.path.exists(orig):
        src_path = orig                                   # re-split from the untouched copy
    src = fitz.open(src_path)
    prev_dir = f"/data/processed/_audit/split_{stem}"
    os.makedirs(prev_dir, exist_ok=True)
    out = fitz.open()
    plan = []
    # pass 1: the text gap where it is clear; pass 2: the fold shadow near
    # the nearest clear cuts (the camera drifts slowly between scans)
    fixes = read_corrections(stem)
    found = {}
    for i, page in enumerate(src):
        if page.rect.width > page.rect.height * 1.1 and fixes.get(i + 1) != "whole":
            ink, light, _ = profiles(page)
            found[i] = (text_gap(ink), light)
    clear = {i: g[0] for i, (g, _) in found.items() if g}
    clear.update({i: fixes[i + 1] for i in found if i + 1 in fixes})   # a correction is a clear cut
    for i, page in enumerate(src):
        r = page.rect
        if i not in found:
            out.insert_pdf(src, from_page=i, to_page=i)
            plan.append({"scan": i + 1, "whole": True})
            continue
        gap, light = found[i]
        if i + 1 in fixes:
            cut, how = fixes[i + 1], "corrected"
        elif gap:
            cut, how = gap[0], "text gap"
        else:
            near = sorted(clear, key=lambda j: abs(j - i))[:4]
            prior = float(np.median([clear[j] for j in near])) if near else 0.5
            cut, how = fold_line(light, prior), "fold shadow"
        x = r.x0 + r.width * cut
        for clip in (fitz.Rect(r.x0, r.y0, x, r.y1), fitz.Rect(x, r.y0, r.x1, r.y1)):
            p = out.new_page(width=clip.width, height=clip.height)
            p.show_pdf_page(p.rect, src, i, clip=clip)
        plan.append({"scan": i + 1, "cut": round(cut, 3), "by": how})
        _, _, pix = profiles(page)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples).convert("RGB")
        ImageDraw.Draw(img).line([(cut * img.width, 0), (cut * img.width, img.height)],
                                 fill={"text gap": (255, 0, 0), "fold shadow": (0, 0, 255)}.get(how, (0, 160, 0)), width=2)
        img.save(f"{prev_dir}/{i + 1:04d}.png")
    cuts = [p for p in plan if "cut" in p]
    narrow = [p["scan"] for p in cuts if p["by"] == "fold shadow"]
    print(f"{stem}: {len(src)} scan pages, {len(cuts)} split, "
          f"{len(src) - len(cuts)} whole -> {len(out)} pages")
    print(f"  cut at {min(p['cut'] for p in cuts):.3f}..{max(p['cut'] for p in cuts):.3f} "
          f"of the width; previews in {prev_dir}/ "
          f"({sum(p['by'] == 'corrected' for p in cuts)} from {stem}.split, green)" if cuts else "  nothing to split")
    if narrow:
        print(f"  cut on the fold shadow (blue in the previews, check them): scans {narrow}")
    json.dump(plan, open(f"{prev_dir}/plan.json", "w"), indent=1)
    if not apply:
        return
    staged = f"/data/processed/_audit/split_{stem}/{stem}.pdf"
    out.save(staged, garbage=3, deflate=True)
    print(f"✓ staged {staged}\n  on the host, from the repo folder:")
    if src_path != orig:
        print(f"    mv books/{stem}.pdf books/_alt/{stem}_original.pdf")
    print(f"    mv processed/{stem}_translated processed/_audit/{stem}_translated.before-split")
    print(f"    cp processed/_audit/split_{stem}/{stem}.pdf books/{stem}.pdf")
    print(f"  then step 1 on the book: PDF_ONLY={stem} python3 /app/1_ocr_and_extract_vlm.py")


if __name__ == "__main__":
    main()
