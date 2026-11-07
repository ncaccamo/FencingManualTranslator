#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""A book's footnote that runs over more than one paragraph, or on to the
next page, set in the English as body text (general rule 1, 2026-11-06).

The OCR reads a page top to bottom: the note's first paragraph starts "(1)"
and step 2 moves it into the page's notes (_tx_notes.extract_source_
footnotes), but a second paragraph of the same note, or the part of a note
carried over to the next page, has no mark of its own and stays in the text
-- often in the middle of a sentence that runs on to the next page
(Masiello pdf 210: «Questa è fra le guardie la migliore» between "almost
without the cooperation" and "active of muscles"; pdf 212: eight paragraphs
of note under three lines of text; Barbasetti pdf 24).

What tells them apart is the type: the book sets its notes smaller. The
scan's text layer gives each printed line's height, so the note area is the
run of lines at the foot of the page that are all smaller than every line
of text above them (the running head and folio left out). The source
paragraphs set there are the note; the English paragraphs at the same
places (when the source and the English have as many) move into the
page's notes: added to the "(N)" note before them on the page, or, before
any, as the continuation of the last page's note ("… "), only when that
note breaks off mid-sentence.
"""
import re

FN_HEADER = "___FOOTNOTES___"
_NOTE_MARK = re.compile(r"^\((\d{1,2})\)[ .]")
_IMAGE = re.compile(r"\s*!\[.*\]\(.*\)\s*$")
_OPEN = re.compile(r"[^.!?»”\")\]…]\s*$")
# the text resumes: a titled section heading («§ 341. Differenza tra la lezione
# e l'assalto.», Florio pdf 199) -- a bare «§ 387.» over a quotation inside a
# note (Masiello pdf 278) is not one
_HEADING = re.compile(r"^§\s*\d+\s*[.—-]\s*\S.{2,}")
# the printer's signature line at the foot («MASIELLO, La scherma di sciabola.»)
_SIGNATURE = re.compile(r"^[A-ZÀ-Ý][A-ZÀ-Ý'’]{3,}\s*,")


def _norm(s):
    import _textlayer
    return _textlayer.norm(s)


def _lines(page):
    """The text layer's lines: (top, height, text), top to bottom, the
    running head / folio strip (top 9 %) and scraps of under six letters
    (scan noise like «OO > fc>», Masiello pdf 210) left out."""
    import fitz
    H = page.rect.height
    out = []
    for b in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
        for ln in b.get("lines", []):
            t = "".join(s["text"] for s in ln["spans"]).strip()
            x0, y0, x1, y1 = ln["bbox"]
            if y0 / H >= 0.09 and len(_norm(t)) >= 6:
                out.append((y0 / H, y1 - y0, t))
    return sorted(out)


def _body_height(lines):
    """The text's type: the tallest line height that at least three lines
    share (a page of mostly note still has its few lines of text)."""
    from collections import Counter
    c = Counter(round(h) for _, h, _ in lines)
    common = [h for h, n in c.items() if n >= 3]
    return max(common) if common else None


def _paras(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def _locate(sp, lines):
    """For each source paragraph, the index of the text-layer line it starts
    on (in order down the page), or None."""
    from difflib import SequenceMatcher
    keys = [_norm(t) for _, _, t in lines]
    out, last = [], -1
    for q in sp:
        k = _norm(_NOTE_MARK.sub("", q))[:12]
        n = next((n for n in range(last + 1, len(lines)) if len(k) >= 6 and k in keys[n]), None)
        if n is None and len(k) >= 8:
            # a paragraph starts a printed line: its first letters against each
            # line's, for an OCR slip («mentre» for the printed «mente», the end
            # of a word broken over the page: Masiello pdf 280)
            n = next((n for n in range(last + 1, len(lines))
                      if SequenceMatcher(None, k, keys[n][:len(k)]).ratio() >= 0.75), None)
        if n is not None:
            last = n
        out.append(n)
    return out


def note_paragraphs(src_body, page):
    """Indices of the source paragraphs (the OCR's image links left out) that
    are set as a note: after a "(N)" note whose printed lines, from there to
    the foot of the page, are all smaller than the text (that note's further
    paragraphs); or, before any "(N)", the run at the foot of a page whose
    lines are all smaller than every line above (a smaller-type paragraph
    of text, Masiello's «AVVERTENZA», included) and at most 0.8 of the
    text's size (a note carried over from the last page). [] when the type does not
    tell them apart (Barbasetti's text layer: 8-10 pt text, 6-8 pt notes)."""
    lines = _lines(page)
    body_h = _body_height(lines)
    if not body_h or len(lines) < 4:
        return []
    sp = [q for q in _paras(src_body) if not _IMAGE.match(q)]
    at = _locate(sp, lines)
    small = [h < 0.9 * body_h for _, h, _ in lines]
    out = set()
    # a note's further paragraphs, after its "(N)"
    for j, q in enumerate(sp):
        if not _NOTE_MARK.match(q) or at[j] is None or not all(small[at[j]:]):
            continue
        for k in range(j + 1, len(sp)):
            if _NOTE_MARK.match(sp[k]) or _HEADING.match(sp[k]):
                break
            if at[k] is not None and at[k] < at[j]:
                break                     # set above the note: the OCR's order, not the page's
            if not _signature(sp, k):
                out.add(k)
    # a note carried over from the last page: the foot of a clean two-size page
    first_mark = next((j for j, q in enumerate(sp) if _NOTE_MARK.match(q)), len(sp))
    top = next((n for n in range(len(lines)) if all(small[n:])), None)
    if top and max(h for _, h, _ in lines[top:]) < min(h for _, h, _ in lines[:top]) - 0.3 \
            and max(h for _, h, _ in lines[top:]) <= 0.8 * body_h:
        start = next((j for j in range(first_mark) if at[j] is not None and at[j] >= top), None)
        if start is not None:
            for k in range(start, first_mark):
                if _HEADING.match(sp[k]):
                    break
                if not _signature(sp, k):
                    out.add(k)
    return sorted(out)


def _signature(sp, k):
    """The page's last paragraph is the printer's signature line: a few
    words opening on the author's name in capitals and a comma."""
    return k == len(sp) - 1 and len(sp[k].split()) <= 8 and bool(_SIGNATURE.match(sp[k]))


def move_into_notes(tr_page, src_body, page, prev_tr_page=""):
    """The page's English with its note paragraphs moved from the text into
    the notes, or None when nothing moves (no note area, the paragraphs do
    not line up, or a carried-over part whose last-page note did not break
    off)."""
    if page is None or not tr_page:
        return None
    idx = note_paragraphs(src_body, page)
    if not idx:
        return None
    sp = [q for q in _paras(src_body) if not _IMAGE.match(q)]
    body, sep, notes = tr_page.partition(FN_HEADER)
    ep = _paras(body)
    plain = [j for j, q in enumerate(sp) if not _NOTE_MARK.match(q)]     # the source's text paragraphs
    if len(plain) != len(ep):
        return None
    note_lines = [l for l in notes.split("\n") if l.strip()]
    carried, added, moved = [], {}, set()
    for j in idx:
        mark = next((_NOTE_MARK.match(sp[k]).group(1) for k in range(j - 1, -1, -1)
                     if _NOTE_MARK.match(sp[k])), None)     # the "(N)" note the paragraph follows
        e = plain.index(j)
        text = re.sub(r"\s+", " ", ep[e]).strip()
        if mark is None:
            carried.append(text)
        else:
            added.setdefault(mark, []).append(text)
        moved.add(e)
    if carried:
        prev_notes = [l for l in (prev_tr_page or "").partition(FN_HEADER)[2].split("\n") if l.strip()]
        if not prev_notes or not _OPEN.search(prev_notes[-1]):
            return None                  # the last page's note did not break off
    for n, texts in added.items():
        k = next((k for k, l in enumerate(note_lines) if l.startswith(f"({n})")), None)
        if k is None:
            return None
        note_lines[k] = note_lines[k].rstrip() + " " + " ".join(texts)
    if carried:
        note_lines.insert(0, "… " + " ".join(carried))
    new_body = "\n\n".join(q for e, q in enumerate(ep) if e not in moved)
    lead = "\n" if tr_page.startswith("\n") else ""
    return lead + new_body + "\n\n" + FN_HEADER + "\n" + "\n".join(note_lines) + "\n"
