# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Rich-text layout shared by 3_english_only_pdf.py and 4_side_by_side_pdf.py.

Replaces the old `translation_text.replace('\\n', ' ')` + `re.sub(r'\\s+', ' ')`
collapse, which threw away every line break, indent and <i> span -- turning
fencing drills / braced command tables (see LaMarche pp. 82-83) into a wall of
prose. This keeps:

  * line breaks and blank-line paragraph gaps
  * leading-whitespace indentation (drill blocks, braced command groups)
  * <i>...</i> rendered as real italics
  * greedy word wrap inside the indent
  * overflow reported as a return value (leftover lines) so callers flow to
    the next slot / page instead of silently dropping text

Text is placed with fitz.TextWriter so glyph advances (accents, italics) are
computed by the font, not by summing get_text_length() -- an earlier version
did the latter and accented words like "DEUXIÈME" / "Coupé" collided with the
following word.
"""
import os
import re
from pathlib import Path

import fitz


_DEG = {"cw": 270, "ccw": 90, "90": 270, "-90": 90, "180": 180}


def _rotate_file(folder):
    stem = Path(folder).name.replace("_translated", "")
    for base in (f"/data/books/{stem}.rotate", f"{folder}/{stem}.rotate"):
        if os.path.exists(base):
            return base
    return None


def rotation_plan(folder):
    """books/<stem>.rotate -> (book, pages). Steps 3 & 4 turn sideways-
    printed plates upright when embedding the source page.

    One rule per line ('#' starts a comment):
      cw | ccw | 180            every figure page of the book ('plates:' prefix
                                optional), e.g. Gomard
      all:cw                    every page
      cw: 35, 39, 55-229        these scan pages (1-based), whatever they hold --
      ccw: 241                  for books whose plates face different ways
    book = (scope, degrees) or None; pages = {0-based index: degrees}. A
    page rule wins over the book rule."""
    path = _rotate_file(folder)
    book, pages = None, {}
    if not path:
        return book, pages
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip().lower()
        if not line:
            continue
        m = re.match(r"^(cw|ccw|180|-?90)\s*:\s*([\d,\s-]+)$", line)
        if m:
            deg = _DEG[m.group(1)]
            for part in re.split(r"[,\s]+", m.group(2).strip()):
                if not part:
                    continue
                a, _, b = part.partition("-")
                for n in range(int(a), int(b or a) + 1):
                    pages[n - 1] = deg
            continue
        scope = "all" if line.startswith("all:") else "plates"
        deg = _DEG.get(line.split(":")[-1].strip())
        if deg:
            book = (scope, deg)
    return book, pages


def load_rotate(folder):
    """The book-wide rule only: (scope, degrees) or None (see rotation_plan)."""
    return rotation_plan(folder)[0]


# reserved names for the built-in PDF base-14 fonts
ROMAN, ITALIC, BOLD = "times-roman", "times-italic", "times-bold"
_TAG = re.compile(r"(</?i>)", re.I)
_SPACE_PT = 3.0      # width credited to one leading space when deriving indent
_MIN_WRAP = 48.0     # never wrap into a column narrower than this (pt)

_FONTS = {}


def _font(name):
    if name not in _FONTS:
        _FONTS[name] = fitz.Font(name)
    return _FONTS[name]


def _spans(s):
    """'a <i>b</i> c' -> [('a ', 0), ('b', 1), (' c', 0)]"""
    out, ital = [], 0
    for part in _TAG.split(s):
        if not part:
            continue
        low = part.lower()
        if low == "<i>":
            ital = 1
        elif low == "</i>":
            ital = 0
        else:
            out.append((part, ital))
    return out or [("", 0)]


def _w(txt, ital, size):
    return _font(ITALIC if ital else ROMAN).text_length(txt, fontsize=size)


def _wrap(spans, width, size):
    """Greedy word-wrap (text, ital) spans into visual lines."""
    lines, cur, cur_w = [], [], 0.0
    for text, ital in spans:
        for tok in re.findall(r"\S+\s*|\s+", text):
            w = _w(tok, ital, size)
            if cur and tok.strip() and cur_w + w > width:
                lines.append(cur)
                cur, cur_w = [], 0.0
                tok = tok.lstrip()
                w = _w(tok, ital, size)
            if not tok:
                continue
            cur.append((tok, ital))
            cur_w += w
    if cur:
        lines.append(cur)
    return lines or [[("", 0)]]


class Line:
    __slots__ = ("indent", "spans", "gap", "note")

    def __init__(self, indent=0.0, spans=None, gap=False, note=False):
        self.indent = indent
        self.spans = spans or []
        self.gap = gap
        self.note = note        # an author's margin note (see layout())


# A margin note arrives from step 2 as its own line, ⟦MARGIN: …⟧, placed
# right before the paragraph it annotates (_margins.py). Drawn as a distinct
# grey italic line with a thin bar at its left edge -- in the text flow, next
# to its paragraph, never stacked elsewhere and never as raw brackets.
_MARGIN_RE = re.compile(r"^\s*⟦MARGIN:\s*(.*?)\s*⟧\s*$")
NOTE_INDENT = 10.0
NOTE_COLOR = (0.38, 0.38, 0.38)


def layout(text, width, size):
    """text -> list[Line], pre-wrapped to `width` pt at `size`."""
    out = []
    for raw in (text or "").split("\n"):
        if not raw.strip():
            out.append(Line(gap=True))
            continue
        m = _MARGIN_RE.match(raw)
        if m:
            # the note is italic throughout; an <i> term inside it stays italic
            note = re.sub(r"</?i>", "", m.group(1))
            for vline in _wrap([(note, 1)], max(width - NOTE_INDENT, _MIN_WRAP), size):
                out.append(Line(NOTE_INDENT, vline, note=True))
            continue
        n = len(raw) - len(raw.lstrip(" "))
        indent = n * _SPACE_PT
        avail = max(width - indent, _MIN_WRAP)
        for vline in _wrap(_spans(raw[n:]), avail, size):
            out.append(Line(indent, vline))
    return out


def draw(page, rect, lines, size, *, leading=1.28, gap_frac=0.55,
         color=(0, 0, 0), dry=False):
    """Draw as many `lines` as fit in `rect` (x0, y0, x1, y1).

    Returns (leftover_lines, y_end). `leftover_lines` is empty when everything
    fit. With dry=True nothing is drawn -- use it to test whether a given
    size fits before committing.
    """
    x0, y0, x1, y1 = rect
    lh = size * leading
    y = y0
    tw = None if dry else fitz.TextWriter(page.rect)
    tw_note = None if dry else fitz.TextWriter(page.rect)
    wrote = wrote_note = False
    leftover = []
    for idx, ln in enumerate(lines):
        if ln.gap:
            y += lh * gap_frac
            continue
        if ln.indent < 0:            # rule sentinel (footnote separator)
            if y + lh * 0.8 > y1:
                leftover = lines[idx:]
                break
            y += lh * 0.35
            if not dry:
                page.draw_line((x0, y), (x0 + min(120, x1 - x0), y),
                               width=0.4, color=color)
            y += lh * 0.35
            continue
        if y + lh > y1:
            leftover = lines[idx:]
            break
        if tw is not None:
            writer = tw_note if ln.note else tw
            pt = fitz.Point(x0 + ln.indent, y + size)
            for txt, ital in ln.spans:
                if not txt:
                    continue
                res = writer.append(pt, txt, font=_font(ITALIC if ital else ROMAN),
                                    fontsize=size)
                # TextWriter.append returns (text_rect, last_point) on this
                # PyMuPDF; older builds return just the point.
                pt = res[1] if isinstance(res, (tuple, list)) else res
                if ln.note:
                    wrote_note = True
                else:
                    wrote = True
            if ln.note:
                bx = x0 + ln.indent - 5
                page.draw_line((bx, y + size * 0.15), (bx, y + lh * 0.95),
                               width=0.8, color=NOTE_COLOR)
        y += lh
    if wrote:
        tw.write_text(page, color=color)
    if wrote_note:
        tw_note.write_text(page, color=NOTE_COLOR)
    return leftover, y


def strip_leading_gaps(lines):
    i = 0
    while i < len(lines) and lines[i].gap:
        i += 1
    return lines[i:]


FN_HEADER = "___FOOTNOTES___"


def split_footnotes(text):
    """(body, [note_str, ...]) -- peel off the trailing ___FOOTNOTES___
    block that 2_translate.py appends. Empty list if there is none."""
    body, sep, rest = text.partition(FN_HEADER)
    if not sep:
        return text.rstrip(), []
    return body.rstrip(), [l for l in rest.strip().splitlines() if l.strip()]


def footnote_lines(notes, width, size):
    """Lay a list of note strings for drawing: a thin rule, then each note
    wrapped with a hanging indent. Each note already self-identifies -- a
    "(1)" from the book, "[margin note] ...", or a "French: <term>" source
    reference -- so no block label (it just wasted a line). Returns
    list[Line] (first is a rule sentinel: indent<0)."""
    out = [Line(indent=-1.0)]  # rule marker for draw()
    for n in notes:
        wrapped = _wrap(_spans(n), width, size)
        for j, vl in enumerate(wrapped):
            out.append(Line(0.0 if j == 0 else size * 0.9, vl))
    return out


def measure(lines, size, *, leading=1.28, gap_frac=0.55):
    """Total height `lines` will occupy."""
    h = 0.0
    for ln in lines:
        h += size * leading * (gap_frac if ln.gap else 1.0)
    return h
