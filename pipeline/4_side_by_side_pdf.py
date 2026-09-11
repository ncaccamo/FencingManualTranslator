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
import json
from pathlib import Path
import glob
import re

import _render
import _pageio
import _cover

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
    paragraph's height. Returns True on success, False when the caller
    should fall back to plain flow.

    Anchoring can only ADD whitespace (push a paragraph down to its source
    fraction), never remove it. So:
      * if any single anchor gap is huge, or the total added whitespace is
        more than a third of the column, the anchors are untrustworthy
        (patchy Google Books text layer) -> plain flow;
      * if the anchored layout overflows only a little, compress every gap
        proportionally so a bigger font still fits (better than shrinking
        to 7 pt with full gaps);
      * otherwise render as-is."""
    paras = [p for p in re.split(r"\n\s*\n", tr.strip()) if p.strip()]
    span0, span1 = displayed_scan_span(src_page)
    span_h = span1 - span0
    laid = [_render.layout(p, RIGHT.width, size) for p in paras]
    line_h = [sum(size * LEADING if not ln.gap else size * LEADING * 0.55
                  for ln in L) for L in laid]

    cur = y_top
    gaps = []
    for k, h in enumerate(line_h):
        anchor = (span0 + fracs[k] * span_h) if k < len(fracs) else None
        want = max(cur, anchor) if anchor is not None else cur
        gaps.append(want - cur)
        cur = want + h + PARA_GAP
    W = sum(gaps)
    Gmax = max(gaps) if gaps else 0.0
    X = cur - bottom                                    # overflow (<=0 fits)
    NORMAL_GAP = size * LEADING

    if Gmax > max(55.0, 5 * NORMAL_GAP) or W > 0.35 * (bottom - y_top):
        return False
    scale = 1.0
    if X > 0:
        if W <= 0 or X > 0.8 * W:
            return False
        scale = max(0.0, 1.0 - X / W)

    cur = y_top
    for k, L in enumerate(laid):
        cur = min(cur + gaps[k] * scale, bottom - size)
        _, cur = _render.draw(page, (RIGHT.x0, cur, RIGHT.x1, bottom), L, size)
        cur += PARA_GAP
    return True


def parse_vlm_output(text: str) -> dict:
    result = {"page_num": "", "header": ""}
    # [ \t]*, not \s* -- \s also matches the newline, so on a genuinely
    # blank value ("PAGE_NUMBER: \nHEADER: ...", common since blank folios
    # stopped getting a bracketed [n]) it ate straight through into the
    # next field's label and captured THAT instead -- rendered onto the
    # page as a bogus "Page HEADER: <header text>" tag.
    m = re.search(r'PAGE_NUMBER:[ \t]*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if m:
        result["page_num"] = m.group(1).strip()
    m = re.search(r'HEADER:[ \t]*(.+?)(?:\n|BODY_TEXT:)', text, re.IGNORECASE | re.DOTALL)
    if m and m.group(1).strip().lower() != "none":
        result["header"] = m.group(1).strip()
    return result


_load_rotate = _render.load_rotate


def new_spread(doc, src, src_idx, struct, *, cont=False, rotate=0):
    """One landscape page: original scan left, labels + divider right.
    Returns (page, right_rect_top_y)."""
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    try:
        p.show_pdf_page(LEFT, src, src_idx, rotate=rotate)
    except Exception as e:
        p.insert_text((LEFT.x0 + 10, LEFT.y0 + 20), f"[original page unavailable: {e}]",
                      fontsize=9, fontname=_render.ROMAN)
    p.draw_line((DIVIDER_X, 10), (DIVIDER_X, PAGE_H - 10), width=1)

    y = RIGHT.y0
    pn = struct.get("page_num") or ""
    # a bracketed number is reconcile's inference, not a read folio -- don't
    # present it at all (front matter, unnumbered plate leaves). A tight
    # single-page gap between two read folios was already promoted to a
    # real number upstream.
    trusted = pn and not str(pn).startswith("[")
    tag = (f"Page {pn}{'  (cont.)' if cont else ''}" if trusted
           else "(cont.)" if cont else None)
    if tag:
        p.insert_text((RIGHT.x0, y), tag, fontsize=9, fontname=_render.BOLD)
        y += 14
    hdr = (struct.get("header") or "").replace("\n", " ").strip()
    if "\t" in hdr:
        # a two-part running head: topic left, sub-topic right (F13).
        left, right = [s.strip() for s in hdr.split("\t", 1)]
        p.insert_text((RIGHT.x0, y), left, fontsize=8, fontname=_render.ITALIC)
        p.insert_text((RIGHT.x1 - _render._w(right, 1, 8), y), right,
                      fontsize=8, fontname=_render.ITALIC)
        y += 12
    elif hdr and len(hdr.split()) > 12:
        # not a running head -- a title-page title etc. Wrap it as a block
        # instead of a single clipped line.
        _, y = _render.draw(p, (RIGHT.x0, y, RIGHT.x1, RIGHT.y1),
                            _render.layout(f"<i>{hdr}</i>", RIGHT.width, 8), 8)
        y += 4
    elif hdr:
        p.insert_text((RIGHT.x0, y), hdr, fontsize=8, fontname=_render.ITALIC)
        y += 12
    p.draw_line((RIGHT.x0, y), (RIGHT.x1, y), width=0.5)
    y += 8
    return p, y


created_count = 0

# PDF_ONLY=stem[,stem...] limits the rebuild to those books (comma or
# space separated). Handy after a cover / layout tweak.
_only = set(os.environ.get("PDF_ONLY", "").replace(",", " ").split())

for folder in [f for f in glob.glob("/data/processed/*_translated") if os.path.isdir(f)]:
    book = Path(folder).name.replace("_translated", "")
    if _only and book not in _only:
        continue

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
    fig_map = {}
    if os.path.exists(f"{folder}/figures.json"):
        with open(f"{folder}/figures.json", encoding="utf-8") as f:
            fig_map = json.load(f)
    header_map = {}
    if os.path.exists(f"{folder}/headers.json"):
        with open(f"{folder}/headers.json", encoding="utf-8") as f:
            header_map = json.load(f)

    src = fitz.open(ocr_pdf)
    n = min(len(src), len(translations))
    _rot = _load_rotate(folder)
    print(f"Creating: {book}  ({n} pages)"
          + (f"  [rotate {_rot[1]}° {_rot[0]}]" if _rot else ""))

    doc = fitz.open()
    _cover.render(doc, book, page_w=PAGE_W, page_h=PAGE_H, margin=48)

    # Book-level alignment gate: paragraph alignment needs a usable scan
    # text layer. On a book where most pages don't have one (Google-Books
    # scans), the per-page bail still fires but the result is a jitter of
    # aligned / bailed / near-miss pages. If fewer than ALIGN_MIN_RATE of
    # the text pages can align, don't attempt it anywhere -- a consistent
    # plain flow reads better.
    _ALIGN_MIN_RATE = float(os.environ.get("ALIGN_MIN_RATE", "0.65"))
    _txt = _ok = 0
    for i in range(n):
        t = translations[i]
        if not t or t in SKIP_MARKERS or fig_map.get(str(i)):
            continue
        _txt += 1
        bt, _ = _render.split_footnotes(t)
        npar = len([p for p in re.split(r"\n\s*\n", bt.strip()) if p.strip()])
        fr = reconcile_fracs(paragraph_fracs(src[i]), npar)
        if fr and len(fr) >= 2:
            _ok += 1
    book_align = _txt >= 8 and _ok >= _ALIGN_MIN_RATE * _txt
    if _txt:
        print(f"  text layer usable on {_ok}/{_txt} pages"
              + ("" if book_align else "  → book-level plain flow"))

    cont_pages = aligned = 0
    for i in range(n):
        struct = parse_vlm_output(extractions[i]) if i < len(extractions) else {}
        if struct.get("header"):
            struct["header"] = re.sub(r"</?i>", "", header_map.get(struct["header"], struct["header"]))
        tr = translations[i]

        # a scoring/poule table: step 2 left a [[TABLE]] sentinel where the
        # markup was. Render it as a short pointer among the prose (the
        # real grid is on the facing scan); a pure-table page becomes just
        # the pointer.
        tr = re.sub(r"(?m)^\s*\[\[TABLE\]\]\s*$",
                    "[table — see facing page]", tr or "")

        rot = _rot[1] if (_rot and (_rot[0] == "all" or str(i) in fig_map)) else 0
        page, y = new_spread(doc, src, i, struct, rotate=rot)

        if not tr or tr in SKIP_MARKERS:
            # No body text. If figures.json has a caption for the page,
            # render just that -- the plate is on the facing scan, so a
            # "see facing page" pointer would only be noise. No caption ->
            # say whether the original page is blank.
            caps = [f.get("caption_en") or f.get("caption")
                    for f in fig_map.get(str(i), [])
                    if f.get("caption_en") or f.get("caption")]
            if caps:
                yy = y + 4
                for c in caps:
                    for vl in _render.layout(c, RIGHT.width, 9):
                        _, yy = _render.draw(page, (RIGHT.x0, yy, RIGHT.x1, RIGHT.y1),
                                             [vl], 9)
                    yy += 6
            else:
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
        # Skip it on a page with a figure -- the scan's text blocks span
        # the gap where the picture sits, so the anchors jump and the
        # translation ends up misaligned.
        n_paras = len([p for p in re.split(r"\n\s*\n", body_txt.strip()) if p.strip()])
        has_fig = bool(fig_map.get(str(i)))
        fracs = (None if (has_fig or not book_align)
                 else reconcile_fracs(paragraph_fracs(src[i]), n_paras))
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
            page, y = new_spread(doc, src, i, struct, cont=True, rotate=rot)
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
