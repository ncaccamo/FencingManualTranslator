#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""English-only PDF: the translated text as a continuous flowing document.

Rewritten: the old version laid every page into a fixed
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
    With a layout.json (every book since 2026-10) the layout detector's
    picture boxes place and crop every picture (docs/pdf-layout.md).
"""
import fitz
import os
import sys
import json
from pathlib import Path
import glob
import re

import _overrides
import _render
import _textlayer
import _columns
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

SKIP_MARKERS = ("[Empty page]", "[Empty page after cleaning]", _render.LEFT_OUT)


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


def page_contrast(page) -> float:
    """Share of the page's interior darker than its paper by 20+ grey
    levels: about 0.001-0.008 on a blank leaf (Jacob-André), 0.01+ on
    Saint-Martin's faint pencil-grey plates."""
    r = page.rect
    clip = fitz.Rect(r.x0 + .12 * r.width, r.y0 + .1 * r.height, r.x1 - .12 * r.width, r.y1 - .1 * r.height)
    s = page.get_pixmap(matrix=fitz.Matrix(.5, .5), colorspace=fitz.csGRAY, clip=clip).samples
    if not s:
        return 0.0
    med = sorted(s)[len(s) // 2]
    return sum(1 for v in s if v < med - 20) / len(s)


def page_tone(page) -> str:
    """"dark" for a cover or a marbled endpaper (median grey under 168:
    74-158 on Gomard, Danet, Cordelois, Vauresmont), "blank" for a white
    leaf with no ink, else "paper". A plate is light paper with ink:
    Lambertini's 181-190, Robaglia's faint blue drawings 217-222 (contrast
    near 0 but not white), 2026-11-04."""
    r = page.rect
    clip = fitz.Rect(r.x0 + .12 * r.width, r.y0 + .1 * r.height, r.x1 - .12 * r.width, r.y1 - .1 * r.height)
    s = page.get_pixmap(matrix=fitz.Matrix(.5, .5), colorspace=fitz.csGRAY, clip=clip).samples
    if not s:
        return "blank"
    med = sorted(s)[len(s) // 2]
    if med < 168:
        return "dark"
    if med >= 240 and sum(1 for v in s if v < med - 20) / len(s) < 0.006:
        return "blank"
    return "paper"


def box_ink(page, box) -> float:
    """Share of dark pixels inside a detector box (0-1): a near-blank box
    (Joseph-Renaud pdf 113, 316) holds no picture."""
    r = page.rect
    clip = fitz.Rect(r.x0 + box[0] * r.width, r.y0 + box[1] * r.height,
                     r.x0 + box[2] * r.width, r.y0 + box[3] * r.height)
    s = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY, clip=clip).samples
    if not s:
        return 0.0
    # darker than the box's own paper: a faint line diagram on yellowed
    # paper has few truly dark pixels (Masiello pdf 182, 253)
    paper = sorted(s)[int(len(s) * 0.9)]
    return sum(1 for v in s if v < paper - 40) / len(s)


def runs_on(src_body, next_tr):
    """The page's sentence runs on to the next page: the scan's text stops
    mid-sentence (its last line, the page's own notes left out, ends in a
    letter, a comma or a hyphen), or the next English page starts with a
    small letter. The English can end a page on a full stop where the
    French runs on (Joseph-Renaud pdf 34, 52, 74; 2026-11-04)."""
    lines = [l.strip() for l in (src_body or "").strip().splitlines() if l.strip()]
    note = re.compile(r"^(\(\d+\)|\d+\)|\*|†)")
    starts = [j for j, l in enumerate(lines) if j >= len(lines) * 0.5 and note.match(l)]
    if starts:                           # the page's own notes, at its foot, can run several lines
        lines = lines[:starts[0]]
    src_open = bool(lines) and bool(re.search(r"[a-zà-ÿ,\-]$", re.sub(r"</?i>|\*", "", lines[-1])))
    nxt = re.sub(r"^\s*(<i>)?", "", next_tr or "")
    return src_open or bool(re.match(r"[a-zà-ÿ]", nxt))


DECOR_LOG = []      # (page index, n-th picture, box, figure?, dropped?) per book: every decoration,
                    # and every picture the .pictures file drops -> intermediate/decorations.json
                    # for the decoration review sheet (decoration_sheet.py)


# a page whose English opens on a plate heading: "PLATE V." over a two-line
# caption (Gomard's unrecorded Planches, too long for a caption-only page)
PLATE_HEAD = re.compile(r"^\s*(?:<i>)?(?:plate|planche)\s+[ivxlcdm\d]+\b", re.I)


FIG_LABEL = re.compile(r"(?im)^[ \t]*(?:<i>)?(fig(?:ure|\.)?\s*\d+[a-z]?\.?)(?:</i>)?[ \t]*$")


class Flow:
    """Owns the current output page and vertical cursor; opens new pages
    on demand so callers never have to think about page breaks."""

    def __init__(self, doc, rotate=0):
        self.doc = doc
        self.page = None
        self.rotate = rotate               # per-book plate rotation (0/90/180/270)
        self.y = COL.y1  # forces a new page on first write
        self.open = False                  # the last page's text stopped mid-sentence
        self.held = []                     # its notes, drawn once the sentence ends
        self.cur = None                    # the book page being set
        self.on_sheet = set()              # the book pages whose text / pictures this sheet holds

    def _newpage(self):
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.y = COL.y0
        self.on_sheet = set()

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

    def _notes(self, notes):
        self.y += FS * 0.4
        self._flow(_render.footnote_lines(notes, COL.width, FS - 2), FS - 2,
                   color=(0.25, 0.25, 0.25))

    def release(self):
        """Draw notes held back from a page whose sentence ran on."""
        if self.held:
            held, self.held = self.held, []
            self._notes(held)

    def body(self, text, src_body="", src_page=None, prev_page=None, ornaments=(), title="", centred=False,
             interject=None, after=None, runs_on=False, after_para=None):
        """`interject` (figures) and notes held from the page before are drawn
        after this page's first paragraph when the last page's sentence ran
        on to this one -- not in the middle of it (Barbasetti pdf 63:
        "(fig. [picture] 24)"; 27 note blocks in the release books,
        2026-11-04)."""
        body_txt, notes = _render.split_footnotes(text)
        if not _render.toc_page(body_txt):
            # a passage set in two columns (master | pupil): a table, rows as printed
            lay = _layout._lay(folder)
            body_txt = _columns.column_tables(body_txt, src_body, src_page,
                                              ((lay or {}).get("pages", {}).get(str(self.cur)) or {}).get("boxes", []),
                                              notes=bool(notes),
                                              stored=_render.split_footnotes(translations[self.cur])[0])
            # paragraph starts as printed, where the scan has a text layer
            body_txt = _textlayer.apply(_render.english_quotes(body_txt), src_body, src_page, prev_page, ornaments)
            body_txt = _render.lesson_glue(_render.drop_margin_echoes(body_txt))   # a lesson's halves, one block
        if title:
            body_txt = title + "\n\n" + body_txt.strip()
        notes = [_render.english_quotes(n) for n in notes]
        def lay(t):
            ls = _render.layout(t, COL.width, FS, paras=PARAS, centre=CENTRE)
            return _render.centre_lines(ls, COL.width, FS) if centred else ls  # a title page: every line centred
        waiting = bool(self.held) or interject is not None
        head, sep, rest = body_txt.partition("\n\n") if (waiting and self.open) else ("", "", "")
        if interject is not None and after_para:
            # the picture's place is after a given paragraph (its caption
            # line's place in the text): split at the end of that paragraph
            words = re.findall(r"\w+", re.sub(r"<[^>]+>", "", after_para))[-8:]
            m = re.search(r"(?:\W|<[^>]+>)+".join(map(re.escape, words)), body_txt) if words else None
            if m:
                end = body_txt.find("\n\n", m.end())
                head, sep, rest = (body_txt[:end], "\n\n", body_txt[end + 2:]) if end >= 0 else (body_txt, "", "")
            elif not self.open:          # not found: the picture first, as before
                interject()
                interject, waiting = None, bool(self.held)
        if sep and rest.strip():         # the run-on sentence's paragraph first
            self._flow(lay(head), FS)
            if interject:
                interject()
            self.release()
            body_txt = rest
        elif waiting:                    # one paragraph: things that waited go after it
            self._flow(lay(body_txt), FS)
            if interject:
                interject()
            self.release()
            body_txt = ""
        if body_txt:
            self._flow(lay(body_txt), FS)
        if after:                        # pictures printed below the page's text
            after()
        tail = re.sub(r"</?i>|\s+$", "", body_txt or head or "")
        self.open = _render.page_left_open(tail, runs_on, centred)
        if notes:
            # end-of-section glossary block, smaller type; held while the
            # page's last sentence runs on to the next page
            if self.open:
                self.held += notes
            else:
                self._notes(notes)

    def _flow(self, lines, size, color=(0, 0, 0)):
        while True:
            lines = _render.strip_leading_gaps(lines)
            if not lines:
                return
            if self.page is None or COL.y1 - self.y < size * 1.4:
                self._newpage()
            before = len(lines)
            lines, self.y = _render.draw(
                self.page, (COL.x0, self.y, COL.x1, COL.y1), lines, size, color=color,
                fresh_top=COL.y0)
            self.on_sheet.add(self.cur)
            if lines and len(lines) == before:
                self._newpage()  # guard against a zero-progress loop

    @staticmethod
    def _clip_dims(src_page, band):
        r = src_page.rect
        if band:
            y0f, y1f, x0f, x1f = band
            return (x1f - x0f) * r.width, (y1f - y0f) * r.height
        return r.width, r.height

    def _fig_size(self, src_page, band, rotate=None, frac=1.0):
        rotate = self.rotate if rotate is None else rotate
        cw, ch = self._clip_dims(src_page, band)
        if rotate in (90, 270):
            cw, ch = ch, cw
        w = COL.width * min(1.0, frac)
        h = w * (ch / cw)
        if h > FIGURE_MAX_H:
            h, w = FIGURE_MAX_H, FIGURE_MAX_H * (cw / ch)
        return w, h

    def figure(self, src_page, band=None, caption="", rotate=None, centred=False, frac=None):
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
        w, h = self._fig_size(src_page, band, rotate, frac or 1.0)
        cl = []
        if caption.strip():
            # English quotation marks, as in the text: a caption can be a
            # paragraph of it (general rule 2; André pdf 166 "Weapons")
            cl = _render.layout(_render.english_quotes(caption), COL.width, FS - 1)
            labels = [q for q in caption.split("\n") if q.strip()]
            label_list = len(labels) >= 3 and all(len(re.sub(r"<[^>]+>", "", q).split()) <= 6 for q in labels)
            # a title page's lines, a picture placed from its detector box, or
            # a plate's list of labels (Masiello 1887 pdf 462)
            if centred or frac is not None or label_list:
                cl = _render.centre_lines(cl, COL.width, FS - 1)
        # room under the picture for its whole caption, not one line (a long
        # caption ran off the sheet's foot: Danet pdf 226, Vauresmont's cover)
        below = max(FS * 1.6, 4 + _render.measure(cl, FS - 1))
        # drawn smaller only beside its own page's text or pictures, not under
        # an earlier page's (Vauresmont's cover, alone on its page)
        fit = self.page is not None and (self.y + h + below <= COL.y1 or self.cur in self.on_sheet) \
            and _render.fit_on_sheet(self.y, w, h, below, COL.y1)
        if fit:
            w, h = fit
        else:
            self._newpage()
            # alone on a fresh sheet: smaller, so its whole caption fits under
            # it (Masiello 1887 pdf 194, 462: a full-page plate's twelve label
            # lines ran onto the next sheet)
            room = COL.y1 - self.y - below
            if h > room >= 0.5 * h:
                w, h = w * room / h, room
        pix = src_page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), clip=clip)
        x0 = COL.x0 + (COL.width - w) / 2
        self.page.insert_image(fitz.Rect(x0, self.y, x0 + w, self.y + h),
                               stream=pix.tobytes("jpeg", jpg_quality=72),
                               rotate=rotate)
        self.y += h + 4
        self.on_sheet.add(self.cur)
        if cl:
            for vl in cl:
                left, self.y = _render.draw(self.page,
                                            (COL.x0, self.y, COL.x1, COL.y1), [vl], FS - 1)
                if left:     # longer than a whole sheet holds: the rest on the next
                    self._newpage()
                    _, self.y = _render.draw(self.page, (COL.x0, self.y, COL.x1, COL.y1), [vl], FS - 1)
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
        translations = _render.apply_left_out([p.strip() for p in _pageio.split_pages(f.read())], book)
    try:
        PLACES = _overrides.picture_places(_overrides.load(book))
    except Exception:
        PLACES = {}
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
    _render.BOLD_SECTIONS = _render.book_bold_headings(book)
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
    LAYOUT = _layout.load(folder) is not None
    DECOR_OFF, DECOR_DROP = _layout.picture_rules(folder)
    DECOR_LOG.clear()
    _texty = [k for k, t in enumerate(translations)
              if len(re.sub(r"<[^>]+>|___FOOTNOTES___.*", "", t or "", flags=re.S).split()) >= 30]
    FIRST_TEXT, LAST_TEXT = (_texty[0], _texty[-1]) if _texty else (0, len(translations) - 1)
    for i, tr in enumerate(translations):
        flow.cur = i
        mid_after = None                 # the picture goes after this paragraph (its caption line's place)
        label_cuts = None                # [(paragraph index, picture)]: labels printed over their pictures
        st = parse_vlm_output(extractions[i]) if i < len(extractions) else {}
        src_page = src_doc[i] if (src_doc is not None and i < len(src_doc)) else None

        # A scoring grid / poule table: step 2 left a [[TABLE]] sentinel.
        # Embed the source page image for it and strip the sentinel from
        # the body so any surrounding prose still renders.
        # Translated tables (tables.json) are drawn in the text; only a
        # table without one still gets the scan image.
        tr, _all = _render.place_tables(tr or "", TABLES.get(str(i)))
        body_is_table = "[[TABLE]]" in (tr or "")
        # the paragraph just above the first table with no English: its
        # scan picture goes there (the order of bouts under its heading)
        table_after = [p for p in (tr or "").split("[[TABLE]]")[0].strip().split("\n\n") if p.strip()][-1:] if body_is_table else []
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

        if body_is_table and src_page is not None and LAYOUT:
            # a table with no English (the OCR could not read it: Kirchhoffer
            # pdf 75's order of bouts): the scan's own table, cropped to the
            # layout detector's box -- the tables above it that do have
            # English are drawn in the text, so not the whole page
            crops = _render.untranslated_table_crops(
                (((_layout._lay(folder) or {}).get("pages", {}).get(str(i)) or {}).get("boxes") or []),
                (TABLES.get(str(i)) or {}).get("en") or [])
            if crops:
                placements = [(c, "") for c in crops]
                if table_after:
                    mid_after = table_after[0]
        if body_is_table and src_page is not None and not placements:
            placements.append((None, "[table — reproduced from the original]"))

        if is_empty and not placements:
            continue  # genuinely blank sheet

        # The layout detector's picture boxes (2026-11-04): the VLM and the
        # density profiler miss line engravings and pages of several figures
        # (Barbasetti pdf 42, 63; Masiello's plates; Gomard's Planches), and
        # the profiler took covers, endpapers and stamps for pictures
        # (Jacob-André). With a layout.json, every detected picture is drawn
        # cropped to its box, a figure-record plate keeps the old whole-page
        # path, and a decoration (no caption, no record) is drawn unless the
        # book's .pictures file leaves it out; nothing outside the pages of
        # text (covers, endpapers) unless recorded.
        frac = [None] * len(placements)
        below = [False] * len(placements)
        turn = [True] * len(placements)
        _plate = any(f.get("kind") == "plate" for f in (figs_here if use_vlm_figs else []))
        if LAYOUT and src_page is not None and _plate and is_empty and (i < FIRST_TEXT or i > LAST_TEXT) \
                and not _layout.pictures(folder, i, 0.6, whole=False) and page_tone(src_page) in ("dark", "blank"):
            continue    # a "plate" before / after the text that is a cover, a marbled endpaper or a blank leaf (Gomard pdf 5)
        if LAYOUT and src_page is not None and not body_is_table and (
                not _plate or _layout.pictures(folder, i, 0.6 if (is_empty or caption_only_page) else 0.8, whole=False)):
            # a recorded plate is cropped to the detector's box when it has a
            # sure one, without the French running head and caption around it
            matter = i < FIRST_TEXT or i > LAST_TEXT
            recorded = bool(figs_here) if use_vlm_figs else False
            bare = is_empty or caption_only_page          # a page with no text of its own
            plate_head = bool(PLATE_HEAD.match(tr or "")) and len((tr or "").split()) <= 60
            # a plate the reviewer recorded ([pages.N] caption) may fill the whole
            # scan: its box is not a cover's (Masiello 1887 pdf 194: the foil
            # plate was cut to its left half, the next-best box)
            decided_plate = any(f.get("decided") and f.get("kind") == "plate" for f in (figs_here if use_vlm_figs else []))
            pics = [b for b in _layout.pictures(folder, i, 0.6 if (bare or recorded) else 0.8, whole=not bare or decided_plate,
                                                sideways=bool(_rot) or i in _rot_pages)
                    if box_ink(src_page, b) >= 0.01]
            keep = []
            for k, b in enumerate(pics, 1):
                # a figure: recorded, captioned on the scan, or a large picture on a
                # page whose only text is its caption (an unrecorded plate: Masiello
                # pdf 220, Gomard's Planches). The rest are decorations.
                big = (b[2] - b[0]) * (b[3] - b[1]) >= 0.15
                is_fig = (recorded and (big or not _plate)) or _layout.picture_captioned(folder, i, b) or (
                    (caption_only_page or plate_head) and big)
                dropped = (i, 0) in DECOR_DROP or (i, k) in DECOR_DROP or (not is_fig and DECOR_OFF)
                if not is_fig or dropped:
                    DECOR_LOG.append((i, k, tuple(b), is_fig, dropped))   # for the decoration review sheet
                if dropped:
                    continue                    # the reviewer's drop wins over any classification
                keep.append((b, is_fig))
            if bare and not pics and not recorded:
                # nothing the detector can crop: before / after the text a
                # cover, an endpaper or a blank leaf (left out); inside the
                # book the old whole-page plate (Saint-Martin's)
                dropped = DECOR_OFF or (i, 0) in DECOR_DROP or (i, 1) in DECOR_DROP
                if dropped and placements and not matter and page_contrast(src_page) >= 0.006:
                    DECOR_LOG.append((i, 1, (0.0, 0.0, 1.0, 1.0), False, True))
                if matter or page_contrast(src_page) < 0.006 or dropped:
                    placements = []      # blank leaves have no ink darker than the paper (Jacob-André pdf 13, 41)
                    if is_empty:
                        continue
                elif placements:
                    DECOR_LOG.append((i, 1, (0.0, 0.0, 1.0, 1.0), False, False))
                keep = None
            if keep is not None and (keep or not recorded or pics):
                fw = _layout.text_frame(folder, i)
                caps = ([_render.figure_caption(f) for f in figs_here] if (use_vlm_figs and figs_here) else [c for _, c in placements])
                # a bare "Fig." / "Figure" with no number is no caption (Prévost-Jollivet pdf 48)
                caps = [c for c in caps if c and not re.fullmatch(r"(?i)\s*fig(ure|\.)?\s*\.?\s*", c)]
                lab_re = _layout.label_lines(figs_here) if use_vlm_figs else FIG_LABEL
                labels = lab_re.findall(_render.split_footnotes(tr or "")[0]) if not caption_only_page else []
                _body = _render.split_footnotes(tr or "")[0]
                paras = [q.strip() for q in re.split(r"\n\s*\n", _body) if q.strip()]
                cap_paras = len(keep) >= 2 and len(paras) == len(keep) and (
                    all(re.match(r"(?i)(<i>)?fig(ure|\.)?\s*\d", q) for q in paras) or len(_body.split()) <= 80)
                placements, frac, below, turn = [], [], [], []
                for k, (b, is_fig) in enumerate(keep):
                    pad = 0.006
                    band = (max(0.0, b[1] - pad), min(1.0, b[3] + pad), max(0.0, b[0] - pad), min(1.0, b[2] + pad))
                    if cap_paras:
                        # one caption paragraph per picture (Kirchhoffer pdf 59, 66)
                        cap = paras[k]
                    elif caption_only_page or is_empty:
                        # a short page's text is the caption of its figure; under a
                        # decoration it stays the page's text (Jacob-André pdf 10, 11, 40)
                        cap = (caps[0] if caps else (tr if (caption_only_page and is_fig) else "")) if k == len(keep) - 1 else ""
                    elif len(labels) == len(keep):
                        cap = labels[k]
                    elif len(caps) == len(keep):
                        cap = caps[k]
                    else:
                        cap = " ".join(caps) if k == len(keep) - 1 else ""
                    placements.append((band, cap))
                    below.append(not is_empty and not caption_only_page and _layout.text_after(folder, i, b))
                    turn.append(i in _rot_pages or (is_fig and (b[2] - b[0]) * (b[3] - b[1]) >= 0.15))
                    small_bare = (caption_only_page or is_empty) and (b[2] - b[0]) * (b[3] - b[1]) < 0.15
                    frac.append(min(1.0, (b[2] - b[0]) / (fw if not (caption_only_page or is_empty) else 0.75))
                                if (small_bare or not (caption_only_page or is_empty)) else 1.0)
                if cap_paras:
                    _sep_notes = (tr or "").partition("___FOOTNOTES___")
                    tr = ("___FOOTNOTES___" + _sep_notes[2]) if _sep_notes[1] else ""
                    is_empty = not tr
                elif len(labels) == len(keep) and keep and not caption_only_page:
                    # the "Fig. N" lines go under their pictures, not in the text
                    body, sepn, notes_ = (tr or "").partition("___FOOTNOTES___")
                    if len(keep) == 1:
                        # and the picture where its label line was, not at the
                        # foot of the page (Barbasetti pdf 73: Fig. 29 was drawn
                        # after the paragraphs that follow it on the scan)
                        ep = _layout.paras(body)
                        j = next((j for j, q in enumerate(ep) if lab_re.fullmatch(q.strip())), None)
                        if j is not None and 0 < j < len(ep) - 1:
                            mid_after, below[0] = ep[j - 1], False
                    body = lab_re.sub("", body)
                    tr = re.sub(r"\n{3,}", "\n\n", body).strip() + (("\n\n" + sepn + notes_) if sepn else "")
                elif keep and not caption_only_page and not is_empty:
                    # a picture's printed caption the OCR also set in the text:
                    # drawn once, under its picture, and the picture where the
                    # line was (general rule 2; Kirchhoffer pdf 52 «Diagram A.»)
                    body, sepn, notes_ = (tr or "").partition("___FOOTNOTES___")
                    src_b = extractions[i].split("BODY_TEXT:", 1)[-1] if i < len(extractions) else ""
                    found = _layout.caption_paras(body, src_b, src_page, folder, i, [b for b, _ in keep])
                    boxes_k = [b for b, _ in keep]
                    cbs = {k: _layout.caption_box(folder, i, boxes_k[k]) for k in found}
                    if found and len(found) == len(keep) and all(
                            cb and cb[3] <= boxes_k[k][1] + 0.01 for k, cb in cbs.items()):
                        # every label printed OVER its picture (Prévost-Jollivet pdf
                        # 55, 58, 59: «Parade de sixte.», then the picture, then its
                        # text; Cordelois pdf 15: the notice over the signature): the
                        # label stays in the text, the picture goes right after it,
                        # nothing under it
                        label_cuts = sorted((js[-1], k) for k, js in found.items())
                        placements = [(band, "") for band, _ in placements]
                        found = {}
                    if found:
                        ep = _layout.paras(body)
                        for k, js in found.items():
                            placements[k] = (placements[k][0], "\n".join(ep[j] for j in js))
                        if len(found) == 1 and len(keep) == 1:
                            (k, js), = found.items()
                            below[k] = js[-1] == len(ep) - 1 and js[0] > 0
                            if 0 < js[0] and js[-1] < len(ep) - 1:
                                mid_after = ep[js[0] - 1]
                        gone = {j for js in found.values() for j in js}
                        # a caption line set mid-sentence («against the | horseman»,
                        # André pdf 387; Vauresmont pdf 62; «com- | bat», Prévost-
                        # Jollivet pdf 305): the sentence's two halves join, the
                        # picture after it
                        joined = {js[-1] + 1 for js in found.values()
                                  if 0 < js[0] and js[-1] + 1 < len(ep) and js[0] - 1 not in gone
                                  and _render.page_left_open(ep[js[0] - 1].strip())}
                        kept = []
                        for j, q in enumerate(ep):
                            if j in gone:
                                continue
                            if j in joined and kept:
                                if re.search(r"[a-zà-ÿ]-$", kept[-1].rstrip()) and re.match(r"[a-zà-ÿ]", q.lstrip()):
                                    kept[-1] = kept[-1].rstrip()[:-1] + q.lstrip()    # a word broken over the caption
                                else:
                                    kept[-1] = kept[-1].rstrip() + " " + q.lstrip()
                            else:
                                kept.append(q)
                        if mid_after and joined:
                            mid_after = next((q for q in kept if q.endswith(ep[max(joined)][-40:])), mid_after)
                        tr = "\n\n".join(kept) + (("\n\n" + sepn + notes_) if sepn else "")
                # a picture whose printed caption is a bare figure label («Fig. 5»):
                # that label, whatever the OCR recorded (a heading, a description
                # of the picture, nothing: Barbasetti pdf 44-164) -- not over a
                # caption the reviewer decided
                decided = any(f.get("decided") for f in (figs_here if use_vlm_figs else []))
                if placements and not decided and not caption_only_page:
                    printed = [_layout.printed_label(folder, i, b, src_page) for b, _ in keep]
                    body_, sepn_, notes_ = (tr or "").partition("___FOOTNOTES___")
                    lines_ = {re.sub(r"\s+", " ", m_.strip()).rstrip(".").lower() for m_ in FIG_LABEL.findall(body_)}
                    for k, lab in enumerate(printed):
                        if lab and re.fullmatch(r"(?i)\(?\s*fig(?:ura|ure)?\.?\s*\)?", lab):
                            # its number lost: one below the next picture's, when the
                            # page's text has that label line
                            nxt_ = re.match(r"(?i)\(?fig\S* (\d+)", printed[k + 1] or "") if k + 1 < len(printed) else None
                            lab = f"Fig. {int(nxt_.group(1)) - 1}" if nxt_ and f"fig. {int(nxt_.group(1)) - 1}" in lines_ else None
                        if lab and lab.lower() in {(l or "").lower() for l in printed[:k]}:
                            lab = None           # one label, one picture
                        num = re.search(r"\d+", lab or "")
                        before = [int(m_.group()) for m_ in (re.search(r"\d+", l or "") for l in printed[:k]) if m_]
                        if num and before and int(num.group()) <= max(before):
                            lab = None           # out of order: the layer misread it («Fig. 44» as 41, André pdf 371)
                        printed[k] = lab
                        if lab and k < len(placements):
                            placements[k] = (placements[k][0], lab)
                    used = {re.sub(r"\s+", " ", l.strip("() ")).rstrip(".").lower() for l in printed if l}   # «(Fig. 30)» is the text's «Fig. 30» line
                    if used:
                        body_ = re.sub(r"(?im)^[ \t]*(?:<i>)?(fig(?:ure|\.)?\s*\d+[a-z]?)\.?(?:</i>)?[ \t]*$",
                                       lambda m_: "" if re.sub(r"\s+", " ", m_.group(1)).lower() in used else m_.group(0), body_)
                        tr = re.sub(r"\n{3,}", "\n\n", body_).strip() + (("\n\n" + sepn_ + notes_) if sepn_ else "")
                if is_empty and not placements:
                    continue  # a blank leaf, a cover or an endpaper

        rot = _rot_pages.get(i)      # a listed page's own direction, else the book's
        first_h = flow._fig_size(src_page, placements[0][0], rot, (frac[0] if frac else None) or 1.0)[1] if placements else 0.0
        hdr = st.get("header", "")
        hdr_en = re.sub(r"</?i>", "", header_map.get(hdr, hdr))
        if not TWO_PART:                 # one printed line, split by step 1
            hdr_en = hdr_en.replace("\t", " ")
        # a title page, half-title, plate title (_render.display_page): its
        # lines centred, and a line the OCR took for a running head is the
        # page's title, set first (Kirchhoffer pdf 10)
        display = _render.display_page(_render.split_footnotes(tr or "")[0], st.get("page_num"))
        title_hdr = ""
        if display and hdr_en and "\t" not in hdr_en:
            title_hdr, hdr_en = hdr_en, ""
        flow.section_label(st.get("page_num", ""), hdr_en,
                           reserve=(first_h + FS * 1.6) if placements else 0.0,
                           compact=bool(is_empty and placements),
                           side=sides[i] if i < len(sides) else None)
        def draw_figures(part=None):
            nonlocal_title = [title_hdr]
            for k, (band, caption) in enumerate(placements):
                if part is not None and (k < len(below) and below[k]) != part:
                    continue
                if k == 0 and nonlocal_title[0] and caption_only_page:
                    caption, nonlocal_title[0] = nonlocal_title[0] + "\n\n" + caption, ""   # the title stays with the page's text
                # the book's plate rotation turns its plates, not a stamp or an
                # endpaper on the same kind of page (Gomard pdf 10, 11)
                r_k = rot if (k >= len(turn) or turn[k]) else 0
                flow.figure(src_page, band, caption=caption, rotate=r_k, centred=display,
                            frac=frac[k] if k < len(frac) else None)
            return nonlocal_title[0]

        # Skip the body when it's just the plate caption again (the OCR body
        # of a plate page is its caption line; figure() already drew it).
        def _norm(s):
            return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
        body_is_caption = placements and caption_only_page and any(
            _norm(tr) and _norm(c) and (_norm(tr) in _norm(c) or _norm(c) in _norm(tr))
            for _, c in placements)

        has_body = not is_empty and not body_is_caption
        if placements and below and len(placements) == 1 and (i + 1) in PLACES and not label_cuts:
            # the reviewer's place for the picture ([pages.N] picture_after)
            want_ = PLACES[i + 1]
            hit_ = next((q for q in _layout.paras(_render.split_footnotes(tr or "")[0]) if want_ in q), None)
            if hit_:
                mid_after, below[0] = hit_, False
        if label_cuts and has_body:
            # the page in pieces: its text to each label, then that label's picture
            body_, sepn_, notes_ = (tr or "").partition("___FOOTNOTES___")
            ep = _layout.paras(body_)
            src_body = extractions[i].split("BODY_TEXT:", 1)[-1] if i < len(extractions) else ""
            nxt = next((t for t in translations[i + 1:i + 3] if t and t not in SKIP_MARKERS), "")
            start, title_ = 0, title_hdr
            for cut, k in label_cuts + [(len(ep) - 1, None)]:
                piece = "\n\n".join(ep[start:cut + 1])
                last = k is None
                if last and sepn_:
                    piece += "\n\n" + sepn_ + notes_
                if piece.strip():
                    flow.body(piece, src_body if start == 0 else "", src_page if start == 0 else None, None,
                              (), title=title_, centred=display, runs_on=runs_on(src_body, nxt) if last else False)
                    title_ = ""
                if k is not None:
                    band = placements[k][0]
                    flow.figure(src_page, band, caption="", rotate=rot if (k >= len(turn) or turn[k]) else 0,
                                centred=display, frac=frac[k] if k < len(frac) else None)
                start = cut + 1
            rendered += 1
            figures += len(placements)
            continue
        late = has_body and placements and ((flow.open and not (caption_only_page or display)) or bool(mid_after))
        split = has_body and any(below[:len(placements)])
        if placements and not late:
            title_hdr = draw_figures(False if split else None)
        if has_body:
            src_body = extractions[i].split("BODY_TEXT:", 1)[-1] if i < len(extractions) else ""
            nxt = next((t for t in translations[i + 1:i + 3] if t and t not in SKIP_MARKERS), "")
            flow.body(tr, src_body, src_page, src_doc[i - 1] if (src_doc is not None and 0 < i <= len(src_doc)) else None,
                      _layout.ornaments(folder, i), title=title_hdr, centred=display,
                      interject=(lambda: draw_figures(False if split else None)) if late else None,
                      after=(lambda: draw_figures(True)) if split else None,
                      runs_on=runs_on(src_body, nxt), after_para=mid_after)
            rendered += 1
        figures += len(placements)

    flow.release()                       # notes still held at the end of the book

    if LAYOUT:
        json.dump({"decoration_off": DECOR_OFF,
                   "pictures": [{"scan": i + 1, "k": k, "box": [round(x, 4) for x in b],
                                 "figure": f, "dropped": d} for i, k, b, f, d in DECOR_LOG]},
                  open(f"{int_dir}/decorations.json", "w", encoding="utf-8"), indent=1)

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
