#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Everything about one page, to diagnose a flagged page from its real data
rather than from a similar-looking earlier one.

    python3 /app/show_page.py <stem> <scan page>
    python3 /app/show_page.py <stem> <scan page> --no-images

Prints, for that scan page: the page numbers (scan / printed / side-by-side
pdf), the OCR's raw reply (raw_ocr.txt), what step 1 made of it (page number,
running head, text -- extracted.txt), the English (translation.txt), its
figures and captions (figures.json, and the caption reading if run), the
layout detector's boxes, the review report's items for the page, and the
book's decisions for it (overrides file, hand edit). Also writes two
pictures to processed/_audit/show/: the scan page, and the side-by-side PDF
sheet as built -- open them next to the text.

Read only: changes nothing.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _overrides  # noqa: E402
import _pageio  # noqa: E402

OUT = "/data/processed/_audit/show"


def _load(path, split=True):
    if not os.path.exists(path):
        return None
    t = open(path, encoding="utf-8").read()
    return _pageio.split_pages(t) if split else t


def section(title, body):
    print(f"\n── {title} " + "─" * max(4, 66 - len(title)))
    print(body.rstrip() if body and body.strip() else "  (none)")


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2 or not args[1].isdigit():
        print(__doc__)
        return 2
    stem, s = args[0], int(args[1])
    F = f"/data/processed/{stem}_translated"
    I = f"{F}/intermediate"
    if not os.path.isdir(I):
        sys.exit(f"✗ no such book: {F}")
    raw, ex, tr = _load(f"{I}/raw_ocr.txt"), _load(f"{I}/extracted.txt"), _load(f"{I}/translation.txt")
    n = len(ex or raw or [])
    if not 1 <= s <= n:
        sys.exit(f"✗ {stem} has {n} scan pages")
    i = s - 1
    pn = (_load(f"{I}/page_numbers.txt", split=False) or "").split("\n")
    pdf_page = None
    try:
        import quality_report
        pdf_page = quality_report.page_map(F, n).get(s)
    except Exception:
        pass
    print(f"{stem} — scan {s} of {n}  ·  printed page {pn[i] if i < len(pn) and pn[i] else '?'}"
          f"  ·  side-by-side pdf {pdf_page or '?'}")

    section("OCR reply (raw_ocr.txt)", raw[i] if raw else None)
    if ex:
        e = ex[i]
        head = (re.search(r"(?m)^HEADER: (.*)$", e) or [None, ""])[1]
        num = (re.search(r"(?m)^PAGE_NUMBER: (.*)$", e) or [None, ""])[1]
        section("Step 1 (extracted.txt)", f"  page number: {num}\n  running head: {head.replace(chr(9), '  |  ')}\n\n"
                + e.split("BODY_TEXT:", 1)[-1].strip())
    section("English (translation.txt)", tr[i] if tr and i < len(tr) else None)

    figs = json.load(open(f"{I}/figures.json", encoding="utf-8")) if os.path.exists(f"{I}/figures.json") else {}
    lines = []
    for f in figs.get(str(i), []):
        lines.append(f"  {f.get('kind', '?')}: {f.get('caption') or '(no caption)'}"
                     + (f"\n      English: {f['caption_en']}" if f.get("caption_en") else "")
                     + ("   [decided in the overrides file]" if f.get("decided") else ""))
    cr = json.load(open(f"{I}/caption_read.json", encoding="utf-8")) if os.path.exists(f"{I}/caption_read.json") else {}
    if str(s) in cr:
        d = cr[str(s)]
        lines.append("  caption reading: " + (" / ".join(c["read"] for c in d["crops"] if c["read"]) or "nothing printed found")
                     + f"   (verdicts: {', '.join(d['verdicts']) or '-'})")
    section("Figures and captions", "\n".join(lines))

    lay = json.load(open(f"{I}/layout.json", encoding="utf-8"))["pages"].get(str(i)) if os.path.exists(f"{I}/layout.json") else None
    if lay:
        section("Layout detector boxes (label, score, x0 y0 x1 y1 as page fractions)",
                "\n".join(f"  {b[0]:15} {b[1]:.2f}  {' '.join(f'{x:.2f}' for x in b[2])}"
                          for b in sorted(lay["boxes"], key=lambda b: b[2][1]) if b[1] >= 0.4))

    rep = json.load(open(f"{I}/quality_report.json", encoding="utf-8")) if os.path.exists(f"{I}/quality_report.json") else []
    section("Review report", "\n".join(f"  {x['level']} · {x['kind']}: {x['detail']}" for x in rep if x["scan"] == s))

    try:
        ov = _overrides.load(stem)
        dec = []
        if s in ov["pages"]:
            dec += [f"  [pages.{s}] {k} = {v!r}" for k, v in ov["pages"][s].items()]
        dec += [f"  [[fix]] {f.get('find', '')!r} -> {f.get('replace', f.get('prepend', ''))!r}"
                for f in ov["fix"] if f["scan"] == s]
        edits = _overrides.load_edits(stem)
        if s in edits:
            dec.append(f"  hand edit: {_overrides.edits_dir(stem)}/scan-{s:04d}.txt")
        section(f"Decisions ({_overrides.path_for(stem)})", "\n".join(dec))
    except _overrides.OverridesError as e:
        section("Decisions", f"  ✗ {e}")

    if "--no-images" not in argv:
        import fitz
        os.makedirs(OUT, exist_ok=True)
        made = []
        src = next((f"{I}/{x}" for x in os.listdir(I) if x.endswith("_OCR.pdf")), None)
        if src:
            p = f"{OUT}/{stem}_scan{s}.png"
            fitz.open(src)[i].get_pixmap(dpi=100).save(p)
            made.append(p)
        sbs = f"{F}/{stem} — Side by Side (Landscape).pdf"
        if pdf_page and os.path.exists(sbs):
            p = f"{OUT}/{stem}_scan{s}_pdf{pdf_page}.png"
            fitz.open(sbs)[pdf_page - 1].get_pixmap(dpi=80).save(p)
            made.append(p)
        section("Pictures", "\n".join("  " + m.replace("/data/processed", "processed") for m in made))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
