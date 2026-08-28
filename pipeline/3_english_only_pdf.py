#!/usr/bin/env python3
"""English-only PDF: the translated text as a continuous flowing document.

Rewritten (see chat log): the old version laid every page into a fixed
356 pt slot, 2 per sheet, after `translation_text.replace('\n', ' ')` --
which silently dropped anything that overflowed the slot and destroyed all
drill / command-table structure. Now:
  * one flowing text column; consecutive source pages pack onto a sheet
    instead of wasting half of it (the original "flow as many as fit"
    requirement)
  * line breaks, indentation and <i> italics preserved via _render
  * overflow is impossible -- text that doesn't fit continues on the next
    sheet
  * illustrations are kept with the reading. The VLM decides *whether* a
    page has a picture and reads its caption (figures.json, written by
    1_ocr_and_extract_vlm.py; caption translated by 2_translate.py). A
    "plate" is embedded whole; an "inset" is cropped using the row-density
    profiler for geometry (the VLM's coordinates weren't reliable), and if
    the profiler finds nothing the whole page is embedded so the picture
    is never dropped. For a book OCR'd before figures.json existed, the
    profiler alone drives detection (misses line art / faint half-tones).
"""
import fitz
import os
import json
from pathlib import Path
import glob
import re
from datetime import datetime

import _render

print("\n" + "=" * 60)
print("STEP 3: ENGLISH-ONLY PDF GENERATION")
print("=" * 60 + "\n")

PAGE_W, PAGE_H = 612, 792
MARGIN = 56
COL = fitz.Rect(MARGIN, MARGIN, PAGE_W - MARGIN, PAGE_H - MARGIN)
FS = 10
FIGURE_MAX_H = COL.height * 0.82

SKIP_MARKERS = ("[Empty page]", "[Empty page after cleaning]")


def find_figure_band(page):
    """Locate a dominant illustration band on a scanned page by row-density
    profiling. Returns (y0f, y1f, x0f, x1f) fractions, or None.

    Only the vertical interior (8%-92% of height) is searched -- the outer
    strips are where scanner-bed black and binding shadow live and they
    would otherwise register as a solid 'figure'. A hit is a contiguous run
    of near-solid rows that is either tall (>8% of page) or shorter but very
    dense (engravings run ~0.9 black), and spans most of the text width."""
    pix = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), colorspace=fitz.csGRAY)
    w, h, s = pix.width, pix.height, pix.samples
    if not s:
        return None
    mx = int(w * 0.06)
    ytop, ybot = int(h * 0.08), int(h * 0.92)
    rows = [sum(1 for x in range(mx, w - mx) if s[y * w + x] < 170) / (w - 2 * mx)
            for y in range(ytop, ybot)]
    best = (0, 0)
    run0 = None
    for k, d in enumerate(rows + [0.0]):
        if d > 0.30:
            if run0 is None:
                run0 = k
        elif run0 is not None:
            if k - run0 > best[1] - best[0]:
                best = (run0, k)
            run0 = None
    k0, k1 = best
    if k1 <= k0:
        return None
    seg = rows[k0:k1]
    mean = sum(seg) / len(seg)
    frac_h = (k1 - k0) / h
    step = max(1, (k1 - k0) // 30)
    cols = [x for x in range(w)
            if any(s[(ytop + y) * w + x] < 170 for y in range(k0, k1, step))]
    wext = (max(cols) - min(cols)) / w if cols else 0.0
    if not ((frac_h > 0.08 or (frac_h > 0.03 and mean > 0.60)) and wext > 0.30):
        return None
    y0, y1 = ytop + k0, ytop + k1
    pad = h * 0.006
    return (max(0.0, (y0 - pad) / h), min(1.0, (y1 + pad) / h),
            max(0.0, (min(cols) / w) - 0.01), min(1.0, (max(cols) / w) + 0.01))


def band_for_figure(fig, src_page):
    """Turn a figures.json record into (band, caption) for Flow.figure().
    The VLM gives presence + kind + caption only; geometry comes from the
    density profiler. 'plate' -> whole page (band None). 'inset' -> the
    profiler's band; if the profiler finds nothing on a page the VLM says
    has an inset, fall back to embedding the whole page so the picture is
    never lost."""
    cap = (fig.get("caption_en") or fig.get("caption") or "").strip() \
        or "[illustration from the original]"
    if fig.get("kind") == "plate" or src_page is None:
        return None, cap
    prof = find_figure_band(src_page)
    if prof is None:
        return None, cap            # VLM saw an inset the profiler missed
    y0f, y1f, *_ = prof
    return (None if (y1f - y0f) > 0.85 else prof), cap


def parse_vlm_output(text: str) -> dict:
    result = {"page_num": "", "header": ""}
    m = re.search(r'PAGE_NUMBER:\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if m:
        result["page_num"] = m.group(1).strip()
    m = re.search(r'HEADER:\s*(.+?)(?:\n|BODY_TEXT:)', text, re.IGNORECASE | re.DOTALL)
    if m and m.group(1).strip().lower() != "none":
        result["header"] = m.group(1).strip()
    return result


def interior_ink_fraction(page) -> float:
    """Dark-pixel fraction over the centre 84% of the page (excludes binding
    shadow / edge grime that fools a whole-page measure)."""
    pix = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2), colorspace=fitz.csGRAY)
    w, h, s = pix.width, pix.height, pix.samples
    if not s:
        return 0.0
    mx, my = int(w * 0.08), int(h * 0.08)
    tot = dark = 0
    for y in range(my, h - my):
        base = y * w
        for x in range(mx, w - mx):
            tot += 1
            if s[base + x] < 180:
                dark += 1
    return dark / tot if tot else 0.0


class Flow:
    """Owns the current output page and vertical cursor; opens new pages
    on demand so callers never have to think about page breaks."""

    def __init__(self, doc):
        self.doc = doc
        self.page = None
        self.y = COL.y1  # forces a new page on first write

    def _newpage(self):
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.y = COL.y0

    LABEL_H = FS * 2.4

    def section_label(self, page_num, header, reserve=0.0):
        # `reserve` = extra height the caller will use right after the label;
        # if the label + that reserve won't fit, start a fresh sheet so the
        # label is never orphaned from its content (e.g. a figure).
        if self.page is None or self.y + self.LABEL_H + reserve > COL.y1:
            self._newpage()
        else:
            self.y += FS * 0.8  # breathing room above a new section
        label = f"— {page_num} —" if page_num else "—"
        if header:
            label += f"    {header}"
        self.page.insert_text((COL.x0, self.y + FS + 2), label,
                              fontsize=FS + 1, fontname=_render.BOLD)
        self.y += FS * 1.5
        self.page.draw_line((COL.x0, self.y), (COL.x1, self.y), width=0.4)
        self.y += FS * 0.7

    def body(self, text):
        body_txt, notes = _render.split_footnotes(text)
        self._flow(_render.layout(body_txt, COL.width, FS), FS)
        if notes:
            # end-of-section glossary block, smaller type
            self.y += FS * 0.4
            self._flow(_render.footnote_lines(notes, COL.width, FS - 2), FS - 2,
                       color=(0.25, 0.25, 0.25))

    def _flow(self, lines, size, color=(0, 0, 0)):
        while True:
            lines = _render.strip_leading_gaps(lines)
            if not lines:
                return
            if self.page is None or COL.y1 - self.y < size * 1.4:
                self._newpage()
            before = len(lines)
            lines, self.y = _render.draw(
                self.page, (COL.x0, self.y, COL.x1, COL.y1), lines, size, color=color)
            if lines and len(lines) == before:
                self._newpage()  # guard against a zero-progress loop

    @staticmethod
    def _fig_size(src_page, band):
        r = src_page.rect
        if band:
            y0f, y1f, x0f, x1f = band
            cw, ch = (x1f - x0f) * r.width, (y1f - y0f) * r.height
        else:
            cw, ch = r.width, r.height
        w = COL.width
        h = w * (ch / cw)
        if h > FIGURE_MAX_H:
            h, w = FIGURE_MAX_H, FIGURE_MAX_H * (cw / ch)
        return w, h

    def figure(self, src_page, band=None, caption="[illustration from the original]"):
        """Embed source_page (optionally cropped to `band`, a
        (y0f,y1f,x0f,x1f) fraction rect) scaled into the column."""
        r = src_page.rect
        if band:
            y0f, y1f, x0f, x1f = band
            clip = fitz.Rect(r.x0 + x0f * r.width, r.y0 + y0f * r.height,
                             r.x0 + x1f * r.width, r.y0 + y1f * r.height)
        else:
            clip = r
        w, h = self._fig_size(src_page, band)
        need = h + FS * 1.6
        if self.page is None or self.y + need > COL.y1:
            self._newpage()
        pix = src_page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), clip=clip)
        x0 = COL.x0 + (COL.width - w) / 2
        self.page.insert_image(fitz.Rect(x0, self.y, x0 + w, self.y + h),
                               stream=pix.tobytes("jpeg", jpg_quality=72))
        self.y += h + 4
        for vl in _render.layout(caption, COL.width, FS - 1):
            _, self.y = _render.draw(self.page,
                                     (COL.x0, self.y, COL.x1, COL.y1), [vl], FS - 1)
        self.y += FS * 0.6


def make_cover(doc, book):
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    x, y = MARGIN, 150
    for txt, fs, fn in [
        ("English Translation", 20, _render.BOLD),
        (book, 12, _render.ITALIC),
        (f"Generated {datetime.now():%B %d, %Y}", 10, _render.ROMAN),
        ("Translation model: Qwen2.5-7B-Instruct", 9, _render.ROMAN),
        ("OCR model: olmOCR-2-7B-1025", 9, _render.ROMAN),
        ("AI-generated translation for research use. Not authoritative.", 9, _render.ITALIC),
    ]:
        p.insert_text((x, y), txt, fontsize=fs, fontname=fn)
        y += fs + 12


created_count = 0

for folder in [f for f in glob.glob("/data/processed/*_translated") if os.path.isdir(f)]:
    book = Path(folder).name.replace("_translated", "")

    if not os.path.exists(f"{folder}/translation.txt"):
        print(f"⊘ Skip (no translation): {book}")
        continue

    with open(f"{folder}/translation.txt", "r", encoding="utf-8") as f:
        translations = [p.strip() for p in f.read().split("\n---\n")]
    with open(f"{folder}/extracted.txt", "r", encoding="utf-8") as f:
        extractions = [p.strip() for p in f.read().split("\n---\n")]

    # VLM-flagged figures (kind / vertical extent / translated caption),
    # keyed by 0-based page index. When present it is the source of truth
    # for figures; when absent (a book OCR'd before this existed) fall back
    # to per-page density profiling.
    fig_map = {}
    if os.path.exists(f"{folder}/figures.json"):
        with open(f"{folder}/figures.json", encoding="utf-8") as f:
            fig_map = json.load(f)
    use_vlm_figs = bool(fig_map) or os.path.exists(f"{folder}/figures.json")

    src_doc = None
    ocr_pdf = next((os.path.join(folder, f) for f in os.listdir(folder)
                    if f.endswith(".pdf") and "OCR" in f), None)
    if ocr_pdf:
        src_doc = fitz.open(ocr_pdf)

    print(f"Creating: {book}  ({len(translations)} pages)")

    doc = fitz.open()
    make_cover(doc, book)
    flow = Flow(doc)

    rendered = figures = 0
    for i, tr in enumerate(translations):
        st = parse_vlm_output(extractions[i]) if i < len(extractions) else {}
        is_empty = (not tr) or tr in SKIP_MARKERS
        src_page = src_doc[i] if (src_doc is not None and i < len(src_doc)) else None

        # Collect (band, caption) figure placements for this page.
        placements = []
        if use_vlm_figs:
            for fig in fig_map.get(str(i), []):
                if src_page is None:
                    break
                placements.append(band_for_figure(fig, src_page))
        elif src_page is not None:
            band = find_figure_band(src_page)
            if band is not None:
                y0f, y1f, *_ = band
                placements.append(
                    (None if (y1f - y0f) > 0.85 else band,
                     "[illustration from the original]"))
            elif is_empty and interior_ink_fraction(src_page) > 0.04:
                placements.append((None, "[illustration from the original]"))

        if is_empty and not placements:
            continue  # genuinely blank sheet

        first_h = flow._fig_size(src_page, placements[0][0])[1] if placements else 0.0
        flow.section_label(st.get("page_num", ""), st.get("header", ""),
                           reserve=(first_h + FS * 1.6) if placements else 0.0)
        for band, caption in placements:
            flow.figure(src_page, band, caption=caption)
            figures += 1
        if not is_empty:
            flow.body(tr)
            rendered += 1

    sheets = doc.page_count
    out = f"{folder}/{book} — English Only (Portrait).pdf"
    doc.save(out, garbage=4, deflate=True, clean=True)
    doc.close()
    if src_doc is not None:
        src_doc.close()
    print(f"✓ Created: {Path(out).name}  "
          f"({rendered} text pages + {figures} figures → {sheets} sheets)\n")
    created_count += 1

if created_count == 0:
    print("✗ No PDFs created (translations missing or failed)\n")
else:
    print(f"✓ Created {created_count} English-only PDF(s)\n")

print("=" * 60)
print("ENGLISH-ONLY PDF GENERATION COMPLETE")
print("=" * 60 + "\n")
