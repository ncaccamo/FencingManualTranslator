#!/usr/bin/env python3
"""Side-by-side PDF: original scan (left) beside its English translation (right).

Rewritten (see chat log): the old right column ran
`translation_text.replace('\n', ' ')` then a single `insert_textbox`, so it
lost all drill / command-table structure and silently truncated any page
whose translation didn't fit. Now the right column flows through _render
(line breaks, indentation, <i> italics), shrinks 9->8->7 pt to fit, and
spills to "(cont.)" pages if even 7 pt overflows -- text is never dropped.

Paragraph alignment: when the scan carries a usable embedded text layer
(Gallica etc.), each translated paragraph is top-anchored at roughly the
same fraction down the column as the corresponding source paragraph sits
on the scan, so reading across lands near the right place. `reconcile_fracs`
maps the scan's anchors onto the translation's paragraph count, resampling
when the scan splits into line-level rather than paragraph-level blocks
(that mismatch was pinning whole pages' text to the top). The per-page
dry-fit is the real gate -- a patchy layer (Google Books) mostly overflows
it and falls back to plain flow. Books with no embedded layer always get
plain flow.
"""
import fitz
import os
from pathlib import Path
import glob
import re
from datetime import datetime

import _render
import _pageio

print("\n" + "=" * 60)
print("STEP 4: SIDE-BY-SIDE PDF GENERATION")
print("=" * 60 + "\n")

PAGE_W, PAGE_H = 792, 612
LEFT = fitz.Rect(12, 12, 388, PAGE_H - 12)
DIVIDER_X = 396
RIGHT = fitz.Rect(406, 40, PAGE_W - 16, PAGE_H - 20)
SIZES = (9, 8, 7)

SKIP_MARKERS = ("[Empty page]", "[Empty page after cleaning]")


LEADING = 1.28
PARA_GAP = 4.0


def displayed_scan_span(src_page):
    """Vertical extent (y0, y1) that show_pdf_page(LEFT, ...) actually paints
    the scan into, given it fits-and-centres preserving aspect."""
    r = src_page.rect
    disp_h = min(LEFT.height, LEFT.width * r.height / r.width)
    off = (LEFT.height - disp_h) / 2
    return LEFT.y0 + off, LEFT.y0 + off + disp_h


def paragraph_fracs(src_page):
    """y0-of-each-source-paragraph as a fraction of page height, from the
    scan's own embedded text layer. None if there's no usable layer."""
    blocks = [b for b in src_page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    if len(blocks) < 2:
        return None
    H = src_page.rect.height
    blocks.sort(key=lambda b: b[1])
    fracs, prev_y1 = [], -1e9
    for x0, y0, x1, y1, *_ in blocks:
        if y0 - prev_y1 > H * 0.012:
            fracs.append(min(0.98, max(0.0, y0 / H)))
        prev_y1 = max(prev_y1, y1)
    return fracs or None


def reconcile_fracs(fracs, n_paras):
    """Map the scan's paragraph anchors onto the translation's paragraph
    count. None when there's nothing usable (<2 anchors). When the scan
    has MORE anchors than the translation has paragraphs (line-level
    blocks vs. paragraph-level prose), resample evenly so paragraph k
    lands at a sensible fraction down the page rather than at the k-th
    source line -- that mismatch was pinning whole pages' text to the top.
    The per-page dry-fit in render_aligned() stays the real gate; a
    genuinely patchy layer (Google Books) mostly fails that and falls back
    to plain flow -- there's no clean frac-only signal for 'unreliable'."""
    if not fracs or len(fracs) < 2:
        return None
    # Only step in for the pathological mismatch -- the scan's text layer
    # split into line-level blocks (many more anchors than paragraphs),
    # which pins the whole page's text to the top. A mild mismatch is left
    # alone: render_aligned() only anchors paragraph k while k < len(fracs)
    # and that behaviour was fine on clean scans.
    if n_paras >= 2 and len(fracs) > n_paras * 2:
        last = len(fracs) - 1
        return [fracs[round(k * last / (n_paras - 1))] for k in range(n_paras)]
    return fracs


def render_aligned(page, y_top, tr, size, fracs, src_page, bottom=RIGHT.y1):
    """Draw the translation with each paragraph top-anchored to its source
    paragraph's height. Returns True on success, False if it won't fit (so
    the caller can fall back to plain flow)."""
    paras = [p for p in re.split(r"\n\s*\n", tr.strip()) if p.strip()]
    span0, span1 = displayed_scan_span(src_page)
    span_h = span1 - span0
    laid = [_render.layout(p, RIGHT.width, size) for p in paras]

    # dry pass: will it fit with the alignment whitespace?
    cur = y_top
    for k, lines in enumerate(laid):
        if k < len(fracs):
            cur = max(cur, span0 + fracs[k] * span_h)
        cur += sum(size * LEADING if not ln.gap else size * LEADING * 0.55
                   for ln in lines) + PARA_GAP
        if cur > bottom:
            return False

    cur = y_top
    for k, lines in enumerate(laid):
        if k < len(fracs):
            cur = max(cur, min(span0 + fracs[k] * span_h, bottom - size))
        _, cur = _render.draw(page, (RIGHT.x0, cur, RIGHT.x1, bottom), lines, size)
        cur += PARA_GAP
    return True


def parse_vlm_output(text: str) -> dict:
    result = {"page_num": "", "header": ""}
    m = re.search(r'PAGE_NUMBER:\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if m:
        result["page_num"] = m.group(1).strip()
    m = re.search(r'HEADER:\s*(.+?)(?:\n|BODY_TEXT:)', text, re.IGNORECASE | re.DOTALL)
    if m and m.group(1).strip().lower() != "none":
        result["header"] = m.group(1).strip()
    return result


def new_spread(doc, src, src_idx, struct, *, cont=False):
    """One landscape page: original scan left, labels + divider right.
    Returns (page, right_rect_top_y)."""
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    try:
        p.show_pdf_page(LEFT, src, src_idx)
    except Exception as e:
        p.insert_text((LEFT.x0 + 10, LEFT.y0 + 20), f"[original page unavailable: {e}]",
                      fontsize=9, fontname=_render.ROMAN)
    p.draw_line((DIVIDER_X, 10), (DIVIDER_X, PAGE_H - 10), width=1)

    y = RIGHT.y0
    tag = f"Page {struct.get('page_num') or '?'}"
    if cont:
        tag += "  (cont.)"
    p.insert_text((RIGHT.x0, y), tag, fontsize=9, fontname=_render.BOLD)
    y += 14
    if struct.get("header"):
        p.insert_text((RIGHT.x0, y), struct["header"].replace("\n", " "),
                      fontsize=8, fontname=_render.ITALIC)
        y += 12
    p.draw_line((RIGHT.x0, y), (RIGHT.x1, y), width=0.5)
    y += 8
    return p, y


created_count = 0

for folder in [f for f in glob.glob("/data/processed/*_translated") if os.path.isdir(f)]:
    book = Path(folder).name.replace("_translated", "")

    if not os.path.exists(f"{folder}/translation.txt"):
        print(f"⊘ Skip (no translation): {book}")
        continue

    ocr_pdf = next((os.path.join(folder, f) for f in os.listdir(folder)
                    if f.endswith(".pdf") and "OCR" in f), None)
    if not ocr_pdf or not os.path.exists(ocr_pdf):
        print(f"⊘ Skip (no OCR PDF): {book}")
        continue

    with open(f"{folder}/translation.txt", "r", encoding="utf-8") as f:
        translations = [p.strip() for p in _pageio.split_pages(f.read())]
    with open(f"{folder}/extracted.txt", "r", encoding="utf-8") as f:
        extractions = [p.strip() for p in _pageio.split_pages(f.read())]

    src = fitz.open(ocr_pdf)
    n = min(len(src), len(translations))
    print(f"Creating: {book}  ({n} pages)")

    doc = fitz.open()
    cover = doc.new_page(width=PAGE_W, height=PAGE_H)
    cx, cy = 60, 150
    for txt, fs, fn in [
        ("Side-by-Side Translation", 20, _render.BOLD),
        (book, 12, _render.ITALIC),
        ("Left: original scan    Right: English translation", 11, _render.ROMAN),
        (f"Generated {datetime.now():%B %d, %Y}", 10, _render.ROMAN),
        ("Translation model: Qwen2.5-7B-Instruct    OCR model: olmOCR-2-7B-1025", 9, _render.ROMAN),
        ("AI-generated translation for research use. Not authoritative.", 9, _render.ITALIC),
    ]:
        cover.insert_text((cx, cy), txt, fontsize=fs, fontname=fn)
        cy += fs + 12

    cont_pages = aligned = 0
    for i in range(n):
        struct = parse_vlm_output(extractions[i]) if i < len(extractions) else {}
        tr = translations[i]

        page, y = new_spread(doc, src, i, struct)

        if not tr or tr in SKIP_MARKERS:
            page.insert_text((RIGHT.x0, y + 4), "[blank / no text in original]",
                             fontsize=9, fontname=_render.ITALIC)
            continue

        # Split off the glossary/footnote block; it goes in a reserved strip
        # at the bottom of the right column.
        body_txt, notes = _render.split_footnotes(tr)
        fn_lines = _render.footnote_lines(notes, RIGHT.width, 7) if notes else []
        fn_h = _render.measure(fn_lines, 7) + 4 if fn_lines else 0.0
        body_bottom = RIGHT.y1 - fn_h

        def _footer(pg):
            if fn_lines:
                _render.draw(pg, (RIGHT.x0, body_bottom, RIGHT.x1, RIGHT.y1),
                             fn_lines, 7, color=(0.25, 0.25, 0.25))

        # Try paragraph-aligned layout first (needs a usable scan text
        # layer, and the whole page must still fit with the whitespace).
        n_paras = len([p for p in re.split(r"\n\s*\n", body_txt.strip()) if p.strip()])
        fracs = reconcile_fracs(paragraph_fracs(src[i]), n_paras)
        if fracs and len(fracs) >= 2:
            for s in SIZES:
                if render_aligned(page, y, body_txt, s, fracs, src[i], body_bottom):
                    aligned += 1
                    break
            else:
                fracs = None  # didn't fit at any size -> plain flow below
        if fracs and len(fracs) >= 2:
            _footer(page)
            continue  # aligned render already drew the page

        # pick the largest size that fits; fall back to the smallest + spill
        chosen, lines = SIZES[-1], None
        for s in SIZES:
            cand = _render.layout(body_txt, RIGHT.width, s)
            left, _ = _render.draw(page, (RIGHT.x0, y, RIGHT.x1, body_bottom),
                                   cand, s, dry=True)
            if not left:
                chosen, lines = s, cand
                break
        if lines is None:
            lines = _render.layout(body_txt, RIGHT.width, SIZES[-1])

        leftover, _ = _render.draw(page, (RIGHT.x0, y, RIGHT.x1, body_bottom), lines, chosen)
        _footer(page)
        while leftover:
            cont_pages += 1
            leftover = _render.strip_leading_gaps(leftover)
            page, y = new_spread(doc, src, i, struct, cont=True)
            leftover, _ = _render.draw(
                page, (RIGHT.x0, y, RIGHT.x1, RIGHT.y1), leftover, SIZES[-1])

    src.close()
    sheets = doc.page_count
    out = f"{folder}/{book} — Side by Side (Landscape).pdf"
    doc.save(out, garbage=4, deflate=True)
    doc.close()
    extra = f", +{cont_pages} continuation" if cont_pages else ""
    print(f"✓ Created: {Path(out).name}  ({n} spreads{extra} → {sheets} sheets; "
          f"{aligned} para-aligned)\n")
    created_count += 1

if created_count == 0:
    print("✗ No PDFs created (translations missing or failed)\n")
else:
    print(f"✓ Created {created_count} side-by-side PDF(s)\n")

print("=" * 60)
print("SIDE-BY-SIDE PDF GENERATION COMPLETE")
print("=" * 60 + "\n")
