#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
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
import sys
import json
from pathlib import Path
import glob
import re

import _render
import _pageio
import _cover

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
    strips are where scanner-bed black and binding shadow live.

    Two stages: find a dense SEED run (rows >30% black -- a photo band or
    the crowded core of an engraving), then GROW it up and down while rows
    still carry ink (>4% black), stopping at a gap of clearly-blank rows.
    The grow stage is what pulls in the sparse top/bottom of a line
    engraving and its caption -- density profiling alone clipped those
    (page 23 of Joseph-Renaud came out as a 9%-tall strip)."""
    pix = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), colorspace=fitz.csGRAY)
    w, h, s = pix.width, pix.height, pix.samples
    if not s:
        return None
    mx = int(w * 0.06)
    ytop, ybot = int(h * 0.08), int(h * 0.92)
    rows = [sum(1 for x in range(mx, w - mx) if s[y * w + x] < 170) / (w - 2 * mx)
            for y in range(ytop, ybot)]
    n = len(rows)

    # --- seed: longest run of rows over the dense threshold, at least 4%
    # of the page tall (a real figure core -- a stray dense row on a text
    # page won't form a run this long) ---
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
    if (k1 - k0) < h * 0.04:
        return None
    seed_mean = sum(rows[k0:k1]) / (k1 - k0)
    seed_h = (k1 - k0) / h

    # --- grow: extend up to 22% of the page each way while rows carry
    # ink, stopping at a blank gap. Pulls in the top of a tall photo or
    # the sparse frame+caption of a line engraving whose only >30%-dense
    # rows were its crowded core. ---
    limit = int(h * 0.22)
    blank = max(4, int(h * 0.025))
    lo = max(0, k0 - limit)
    gap = 0
    while k0 > lo and gap < blank:
        k0 -= 1
        gap = gap + 1 if rows[k0] < 0.04 else 0
    k0 += gap
    hi = min(n, k1 + limit)
    gap = 0
    while k1 < hi and gap < blank:
        gap = gap + 1 if rows[k1] < 0.04 else 0
        k1 += 1
    k1 -= gap

    band = rows[k0:k1]
    frac_h = (k1 - k0) / h
    b_mean = sum(band) / len(band)
    b_std = (sum((r - b_mean) ** 2 for r in band) / len(band)) ** 0.5
    # A uniform, moderately-light band is a text column, not a picture:
    # reject unless it's dark (photo) or highly uneven (line art).
    if 0.04 < b_mean < 0.20 and b_std < b_mean * 0.8:
        return None
    step = max(1, (k1 - k0) // 40)
    cols = [x for x in range(w)
            if any(s[(ytop + y) * w + x] < 170 for y in range(k0, k1, step))]
    wext = (max(cols) - min(cols)) / w if cols else 0.0
    if not (wext > 0.30 and (frac_h > 0.08 or (seed_h > 0.03 and seed_mean > 0.55))):
        return None
    y0, y1 = ytop + k0, ytop + k1
    pad = h * 0.008
    return (max(0.0, (y0 - pad) / h), min(1.0, (y1 + pad) / h),
            max(0.0, (min(cols) / w) - 0.015), min(1.0, (max(cols) / w) + 0.015))


def band_for_figure(fig, src_page, body_words=0):
    """Turn a figures.json record into (band, caption) for Flow.figure(),
    or None for "no placement". The VLM gives presence + kind + caption
    only; geometry comes from the density profiler. 'plate' -> whole page
    (band None). 'inset' -> the profiler's band; if the profiler can't
    localize it, fall back to the whole page -- UNLESS the page carries a
    substantial translated body, in which case a small drawing/vignette
    isn't worth blanketing the (untranslated) source scan over the reading:
    skip it (the side-by-side PDF still shows the original)."""
    cap = _render.figure_caption(fig)     # cleaned; "" for a description of the picture
    if src_page is None:
        return None, cap
    prof = find_figure_band(src_page)
    if fig.get("kind") == "plate":
        # Whole-page plate: the profiler's band only exists to crop off the
        # running header, page number and scan margins -- worth ~15% of the
        # sheet. If the band it found is tighter than that (a faint line
        # engraving with no dense core clips to seed +/-22%), it is cropping
        # the art, not the apparatus -- embed the whole page instead.
        if prof is None:
            return None, cap
        y0f, y1f, *_ = prof
        return (prof if (y1f - y0f) >= 0.60 else None), cap
    whole_page = prof is None or (prof[1] - prof[0]) > 0.85
    if whole_page and body_words >= 60 and not cap:
        # An un-localizable inset with NO caption on a full text page is a
        # decorative vignette (JR folio 74) -- not worth blanketing the
        # source scan over the reading. A captioned one ("Schéma A", a
        # labelled line diagram the profiler can't see) is referenced by
        # the text and must appear: fall through and embed the whole page.
        return None
    if prof is None:
        return None, cap            # VLM saw an inset the profiler missed
    return (None if whole_page else prof), cap


def parse_vlm_output(text: str) -> dict:
    result = {"page_num": "", "header": ""}
    # [ \t]*, not \s* -- see 4_side_by_side_pdf.py's copy of this function
    # for why: \s* used to eat through a blank value's newline into the
    # next field's label and capture that instead.
    m = re.search(r'PAGE_NUMBER:[ \t]*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if m:
        result["page_num"] = m.group(1).strip()
    m = re.search(r'HEADER:[ \t]*(.+?)(?:\n|BODY_TEXT:)', text, re.IGNORECASE | re.DOTALL)
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

    def __init__(self, doc, rotate=0):
        self.doc = doc
        self.page = None
        self.rotate = rotate               # per-book plate rotation (0/90/180/270)
        self.y = COL.y1  # forces a new page on first write

    def _newpage(self):
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.y = COL.y0

    LABEL_H = FS * 2.4

    def section_label(self, page_num, header, reserve=0.0, compact=False, side=None):
        # `reserve` = extra height the caller will use right after the label;
        # if the label + that reserve won't fit, start a fresh sheet so the
        # label is never orphaned from its content (e.g. a figure).
        # `compact` (figure-only pages) drops the breathing room above and
        # tightens the rule, so a run of small/landscape plates can pack
        # two to a sheet when they genuinely fit (portrait plates hit the
        # 82%-height cap and still get one each -- no shrinking to force it).
        label_h = self.LABEL_H * (0.6 if compact else 1.0)
        if self.page is None or self.y + label_h + reserve > COL.y1:
            self._newpage()
        elif not compact:
            self.y += FS * 0.8  # breathing room above a new section
        # Only show a folio we actually trust. reconcile_page_numbers()
        # brackets anything it inferred ("[5]") -- for front matter and
        # skipped-number pages that's a meaningless sheet ordinal, so drop
        # it and leave just the section rule.
        trusted = page_num and not str(page_num).startswith("[")
        two_part = "\t" in (header or "")
        header_left, header_right = (
            [s.strip() for s in header.split("\t", 1)] if two_part
            else (header or "", ""))
        long_hdr = (not two_part and bool(header)
                    and len(header.split()) > 12)   # a title, not a running head
        head_inline = "" if long_hdr else header_left
        if trusted and head_inline:
            label = f"— {page_num} —    {head_inline}"
        elif trusted:
            label = f"— {page_num} —"
        else:                              # no trusted folio: header alone, or nothing
            label = head_inline
        if side and not two_part and not long_hdr and trusted:
            # the book's places (reviewer, 2026-10-18): the number on its
            # side, the head where the book sets it; one line when they fit.
            # (Top or bottom alike: this text runs on across sheets, so the
            # number stays at its section's start.)
            items = [(f"— {page_num} —", side["folio"][1], _render.BOLD, FS + 1)]
            if head_inline:
                items.append((head_inline, side["head"], _render.BOLD, FS + 1))
            rows = _render.place_row(items, COL.x0, COL.x1)
            for txt, x, font, size, line in rows:
                self.page.insert_text((x, self.y + FS + 2 + (FS + 3) * line), txt,
                                      fontsize=size, fontname=font)
            self.y += (FS + 3) * rows[-1][4]
            label = ""
        if label:
            self.page.insert_text((COL.x0, self.y + FS + 2), label,
                                  fontsize=FS + 1, fontname=_render.BOLD)
        if two_part and header_right:      # sub-topic, flush right (F13)
            self.page.insert_text(
                (COL.x1 - _render._w(header_right, 0, FS + 1), self.y + FS + 2),
                header_right, fontsize=FS + 1, fontname=_render.BOLD)
        self.y += FS * (1.0 if compact else 1.5)
        self.page.draw_line((COL.x0, self.y), (COL.x1, self.y), width=0.4)
        self.y += FS * (0.4 if compact else 0.7)
        if long_hdr:
            self._flow(_render.layout(f"<i>{header}</i>", COL.width, FS - 1),
                       FS - 1, color=(0.3, 0.3, 0.3))
            self.y += FS * 0.3

    def body(self, text):
        body_txt, notes = _render.split_footnotes(text)
        self._flow(_render.layout(body_txt, COL.width, FS, paras=PARAS, centre=CENTRE), FS)
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
    def _clip_dims(src_page, band):
        r = src_page.rect
        if band:
            y0f, y1f, x0f, x1f = band
            return (x1f - x0f) * r.width, (y1f - y0f) * r.height
        return r.width, r.height

    def _fig_size(self, src_page, band, rotate=None):
        rotate = self.rotate if rotate is None else rotate
        cw, ch = self._clip_dims(src_page, band)
        if rotate in (90, 270):
            cw, ch = ch, cw
        w = COL.width
        h = w * (ch / cw)
        if h > FIGURE_MAX_H:
            h, w = FIGURE_MAX_H, FIGURE_MAX_H * (cw / ch)
        return w, h

    def figure(self, src_page, band=None, caption="", rotate=None):
        """Embed src_page (optionally cropped to `band`, a
        (y0f,y1f,x0f,x1f) fraction rect) scaled into the column. Figures
        are embedded in their source orientation -- auto-rotation was
        unreliable (a landscape photo leaf reads fine wide; a sideways
        fold-out engraving on a portrait leaf can't be told apart from
        page geometry)."""
        r = src_page.rect
        if band:
            y0f, y1f, x0f, x1f = band
            clip = fitz.Rect(r.x0 + x0f * r.width, r.y0 + y0f * r.height,
                             r.x0 + x1f * r.width, r.y0 + y1f * r.height)
        else:
            clip = r
        rotate = self.rotate if rotate is None else rotate
        w, h = self._fig_size(src_page, band, rotate)
        need = h + FS * 1.6
        if self.page is None or self.y + need > COL.y1:
            self._newpage()
        pix = src_page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), clip=clip)
        x0 = COL.x0 + (COL.width - w) / 2
        self.page.insert_image(fitz.Rect(x0, self.y, x0 + w, self.y + h),
                               stream=pix.tobytes("jpeg", jpg_quality=72),
                               rotate=rotate)
        self.y += h + 4
        if caption.strip():
            for vl in _render.layout(caption, COL.width, FS - 1):
                _, self.y = _render.draw(self.page,
                                         (COL.x0, self.y, COL.x1, COL.y1), [vl], FS - 1)
            self.y += FS * 0.6


created_count = 0
PARAS = "indented"      # the book's paragraph style, set per book below
CENTRE = False          # the book centres its headings, set per book below

# the books named on the command line, or --all (_books.py; PDF_ONLY= still read)
import _books
for folder in [_books.folder_of(n) for n in _books.select(sys.argv[1:],
               "usage: python3 /app/3_english_only_pdf.py <stem> [<stem> ...] | --all")]:
    book = _books.stem_of(folder)

    int_dir = f"{folder}/intermediate"

    if not os.path.exists(f"{int_dir}/translation.txt"):
        print(f"⊘ Skip (no translation): {book}")
        continue

    with open(f"{int_dir}/translation.txt", "r", encoding="utf-8") as f:
        translations = [p.strip() for p in _pageio.split_pages(f.read())]
    with open(f"{int_dir}/extracted.txt", "r", encoding="utf-8") as f:
        extractions = [p.strip() for p in _pageio.split_pages(f.read())]

    # VLM-flagged figures (kind / vertical extent / translated caption),
    # keyed by 0-based page index. When present it is the source of truth
    # for figures; when absent (a book OCR'd before this existed) fall back
    # to per-page density profiling.
    fig_map = {}
    if os.path.exists(f"{int_dir}/figures.json"):
        with open(f"{int_dir}/figures.json", encoding="utf-8") as f:
            fig_map = json.load(f)
    use_vlm_figs = bool(fig_map) or os.path.exists(f"{int_dir}/figures.json")

    # headers.json, plus two-part heads printed the other way round (_render)
    header_map = _render.header_map(int_dir)
    TABLES = _render.load_tables(int_dir)

    PARAS = _render.book_paragraphs(book)
    CENTRE = _render.centres_headings(folder)
    import _layout
    TWO_PART = _layout.two_part_heads(folder, [parse_vlm_output(e).get("header", "") for e in extractions])
    sides = _render.folio_sides(folder, [parse_vlm_output(e).get("page_num", "") for e in extractions])
    if any(sides):
        print("  page number / running head: in the book's places")

    src_doc = None
    ocr_pdf = next((os.path.join(int_dir, f) for f in os.listdir(int_dir)
                    if f.endswith(".pdf") and "OCR" in f), None)
    if ocr_pdf:
        src_doc = fitz.open(ocr_pdf)

    print(f"Creating: {book}  ({len(translations)} pages)")

    doc = fitz.open()
    _cover.render(doc, book, page_w=PAGE_W, page_h=PAGE_H, margin=MARGIN)
    _rot, _rot_pages = _render.rotation_plan(folder)
    flow = Flow(doc, rotate=_rot[1] if _rot else 0)

    rendered = figures = 0
    for i, tr in enumerate(translations):
        st = parse_vlm_output(extractions[i]) if i < len(extractions) else {}
        src_page = src_doc[i] if (src_doc is not None and i < len(src_doc)) else None

        # A scoring grid / poule table: step 2 left a [[TABLE]] sentinel.
        # Embed the source page image for it and strip the sentinel from
        # the body so any surrounding prose still renders.
        # Translated tables (tables.json) are drawn in the text; only a
        # table without one still gets the scan image.
        tr, _all = _render.place_tables(tr or "", TABLES.get(str(i)))
        body_is_table = "[[TABLE]]" in (tr or "")
        if body_is_table:
            tr = re.sub(r"(?m)^\s*\[\[TABLE\]\]\s*$", "", tr or "").strip()

        is_empty = (not tr) or tr in SKIP_MARKERS
        # A page whose whole body is ~one line is a plate caption, not prose.
        caption_only_page = (not is_empty) and len(tr.split()) <= 20

        # Collect (band, caption) figure placements for this page.
        placements = []
        if use_vlm_figs:
            figs_here = [
                f for f in fig_map.get(str(i), [])
                # a library accession stamp the VLM mistook for a caption
                # ("BIBLIOTHEQUE NATIONALE DE FRANCE 37502 ...")
                if not re.search(r"biblioth[eè]que nationale|\b\d{6,}\b",
                                 f.get("caption", ""), re.I)]
            if figs_here and src_page is not None:
                # One page = one embedded image. Several figures.json rows
                # for a page are almost always the VLM splitting ONE plate's
                # multi-line caption (a "FIG. N —" title row + a
                # "Nᵉ TEMPS : ..." row). Embed once. On a caption-only page
                # the full translated body IS the caption -- both lines,
                # rendered as prose -- otherwise join the rows.
                if caption_only_page and not is_empty:
                    cap = tr
                else:
                    cap = " ".join(_render.figure_caption(f) for f in figs_here).strip()
                bf = band_for_figure(figs_here[0], src_page,
                                     0 if is_empty else len(tr.split()))
                # F8b: don't let a decorative vignette on a text page swallow
                # the whole sheet (P20/P21 -- "Figure 1" over 40+ words of
                # translated prose). A full-page band + substantial body +
                # empty/bare caption -> keep the text, drop the picture.
                _bare_cap = re.match(
                    r"(?i)^\s*(?:fig(?:ure|\.)?|pl(?:ate|\.)?|planche|sch[ée]ma)"
                    r"\s*\.?\s*[ivxlcdm\d]*\s*\.?\s*$", cap or "")
                full_page = bf is not None and bf[0] is None
                if bf is not None and not (
                        full_page and not caption_only_page
                        and len(tr.split()) > 40 and (not cap or _bare_cap)):
                    placements.append((bf[0], cap))
            # The VLM under-reports -- runs of near-identical plates, and
            # engravings dropped into text pages. On any page it did NOT
            # flag, fall back to the density profiler.
            if not placements and src_page is not None:
                prof = find_figure_band(src_page)
                if prof is not None:
                    y0f, y1f, *_ = prof
                    band = None if (y1f - y0f) > 0.85 else prof
                    # same F8b guard: a full-page profiler band must not
                    # bury a page of real translated prose.
                    if not (band is None and not caption_only_page
                            and len(tr.split()) > 40):
                        placements.append(
                            (band, tr if caption_only_page else ""))
                elif is_empty and interior_ink_fraction(src_page) > 0.04:
                    placements.append((None, ""))
        elif src_page is not None:
            band = find_figure_band(src_page)
            if band is not None:
                y0f, y1f, *_ = band
                placements.append(
                    (None if (y1f - y0f) > 0.85 else band, ""))
            elif is_empty and interior_ink_fraction(src_page) > 0.04:
                placements.append((None, ""))

        if body_is_table and src_page is not None and not placements:
            placements.append((None, "[table — reproduced from the original]"))

        if is_empty and not placements:
            continue  # genuinely blank sheet

        rot = _rot_pages.get(i)      # a listed page's own direction, else the book's
        first_h = flow._fig_size(src_page, placements[0][0], rot)[1] if placements else 0.0
        hdr = st.get("header", "")
        hdr_en = re.sub(r"</?i>", "", header_map.get(hdr, hdr))
        if not TWO_PART:                 # one printed line, split by step 1
            hdr_en = hdr_en.replace("\t", " ")
        flow.section_label(st.get("page_num", ""), hdr_en,
                           reserve=(first_h + FS * 1.6) if placements else 0.0,
                           compact=bool(is_empty and placements),
                           side=sides[i] if i < len(sides) else None)
        for band, caption in placements:
            flow.figure(src_page, band, caption=caption, rotate=rot)
            figures += 1

        # Skip the body when it's just the plate caption again (the OCR body
        # of a plate page is its caption line; figure() already drew it).
        def _norm(s):
            return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
        body_is_caption = placements and caption_only_page and any(
            _norm(tr) and _norm(c) and (_norm(tr) in _norm(c) or _norm(c) in _norm(tr))
            for _, c in placements)

        if not is_empty and not body_is_caption:
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
