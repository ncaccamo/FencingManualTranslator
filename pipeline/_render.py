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


# ---- figure captions ---------------------------------------------------------
# Step 1 records a caption per picture; sometimes the OCR model wrote a
# description of the picture instead of the printed caption, or left a piece
# of its own prompt on the end. Checked on all 455 recorded captions,
# 2026-10-07 (review-notes/archive/experiments/CAPTION_CHECK_SAMPLE.md).
_CAPTION_JUNK = [
    # "Fig. 11 -- the picture is essentially the whole page (little or no
    # running text)": the OCR prompt's own wording (Edom x4, Masiello 1887 x6)
    re.compile(r"\s*-{1,2}\s*the picture (?:is essentially|sits on)\b.*$", re.I | re.S),
    # an image tag: "— ![Notre garde ...](../images/page_127.png)" (Dubois
    # 82), mid-caption with text after it (Rossi 213), or cut off at the
    # end: "... Rossi. — ![Vel" (Rossi 285)
    re.compile(r"\s*[—–-]*\s*!\[[^\]]*\]\([^)]*\)"),
    re.compile(r"\s*[—–-]*\s*!\[[^\]\n]*$"),
]
_PRINTED_SHAPE = re.compile(r"(?i)^(?:fig(?:ura|ure)?\.?|tav(?:ola)?\.?|pl(?:anche)?\.?)\s*[\divxlc]+\b")
_DESCRIBES = re.compile(
    r"(?i)^(?:immagine|figura che|disegno (?:di|che)|due figure mostranti|"
    r"diagram (?:of|showing)|photograph of|illustration of|image of|picture of|drawing of)\b"
    r"|\b(?:che mostra|mostranti|raffigurante|con etichette|labell?ed|drawing of|depicting|demonstrating)\b")
_ENGLISH = re.compile(r"(?i)\b(?:the|of a|with (?:his|her|a|the)|and the|in a|is|are|showing|holding|"
                      r"wearing|person|man|fencers?)\b")


def clean_caption(text):
    """A recorded caption without the OCR prompt's fragments / image tags."""
    t = text or ""
    for rx in _CAPTION_JUNK:
        t = rx.sub("", t)
    return t.strip()


def describes_picture(text):
    """True when a recorded caption is the OCR model's description of the
    picture, not a caption the book prints: "Two fencers demonstrating a
    parry and counterattack", "Immagine di una mano che impugna la spada",
    "Diagram showing a triangle with labeled points". A caption shaped like
    a printed one ("Fig. 55. — Figure théorique. — Montrant que ...") is
    kept unless it says it is a drawing / image of something."""
    t = clean_caption(text)
    if not t:
        return False
    if _PRINTED_SHAPE.match(t) and not re.search(r"(?i)\b(?:drawing|image|photograph|picture) of\b", t):
        return False
    if _DESCRIBES.search(t):
        return True
    return len({m.group(0).lower() for m in _ENGLISH.finditer(t)}) >= 2


def figure_caption(fig):
    """The caption to print for a figures.json record ("" = none): the
    reviewer's always; otherwise the recorded one, cleaned, unless it is a
    description of the picture rather than a printed caption."""
    en = (fig.get("caption_en") or "").strip()
    src = (fig.get("caption") or "").strip()
    if fig.get("decided"):
        return en or src
    if describes_picture(src) or describes_picture(en if not src else ""):
        return ""
    return clean_caption(en or src)


def header_map(int_dir):
    """intermediate/headers.json (running head -> English), plus each
    two-part head the other way round: a book that prints the halves on
    alternate sides ("LA MISE EN GARDE / FLEURET" on a left-hand page,
    "FLEURET / LA MISE EN GARDE" on a right-hand one -- Kirchhoffer) uses
    the same English, halves swapped, without translating it twice."""
    import json
    p = os.path.join(int_dir, "headers.json")
    hmap = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    for k, v in list(hmap.items()):
        if "\t" in k and "\t" in v:
            a, b = k.split("\t", 1)
            ea, eb = v.split("\t", 1)
            hmap.setdefault(f"{b}\t{a}", f"{eb}\t{ea}")
    return hmap


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


# The OCR model writes printed italics as markdown -- *Inside line*, *The
# Adventuress* -- and the translation keeps the asterisks (21 places, 12 in
# Vauresmont's glossary). Rendered as italics. Only a pair within one line
# that opens on a letter/digit/quote and closes on a non-space, so a note
# marker "(*)", a bullet "* In the bout", "* * *" or a lone "*" is untouched.
_MD_ITALIC = re.compile(r"(?<![\w*])\*(?=[A-Za-zÀ-ÿ0-9<«\"“])([^*\n]{1,80}?)(?<=[^\s*(])\*(?![\w*])")


# The French lines / parries are kept in French and italicised, like riposte
# (translation_prompt.txt rule 2) -- but only the model applied that, and it
# mostly didn't: across the French books ~95% came out roman ("parry of quarte"),
# while the Italian books italicise terza / quarta 99% of the time. Applied here,
# at drawing time, every book gets it on a PDF rebuild, no retranslation
# (reviewer's decision, 2026-10-11). Words that are never English are always
# italicised; "prime" only where it is the fencing term -- not "past his prime",
# "prime importance"; "octave" not beside verse or music. Text already inside <i>..</i> (a whole italic running head,
# a kept phrase) is left alone; a plural keeps a roman "s", as <i>volte</i>s; in
# a compound only the line name is italic: counter-<i>quarte</i>, <i>tierce</i>-<i>prime</i>.
_LINE_NAMES = re.compile(r"(?<![\w>/])(seconde|tierce|quarte|quinte|sixte|septime|octave|prime)(s?)(?![\w<])",
                         re.IGNORECASE)
_PRIME_ENGLISH_BEFORE = re.compile(r"\b(?:his|her|their|its|my|your|one's|of\s+(?:the\s+)?first)\s+$", re.IGNORECASE)
_PRIME_ENGLISH_AFTER = re.compile(
    r"^\s+(?:importance|minister|example|reason|cause|object|objective|necessity|condition|"
    r"quality|mover|number|numbers|motive|consideration|concern|purpose|target\s+of\s+criticism)\b", re.IGNORECASE)
# "octave" as verse or music ("sung in such beautiful octaves by Poliziano")
_OCTAVE_ENGLISH = re.compile(r"\b(?:sung|sing|song|verse|stanza|rhym|poet|poem|music|note|chord|tone)", re.IGNORECASE)


def line_name_italics(s):
    if not s or not _LINE_NAMES.search(s):
        return s
    out, depth = [], 0
    for part in re.split(r"(</?i>)", s):
        low = part.lower()
        if low == "<i>":
            depth += 1
        elif low == "</i>":
            depth = max(0, depth - 1)
        elif depth == 0 and part:
            def sub(m, part=part):
                w = m.group(1).lower()
                if w == "prime" and (_PRIME_ENGLISH_BEFORE.search(part[:m.start()])
                                     or _PRIME_ENGLISH_AFTER.match(part[m.end():])):
                    return m.group(0)
                if w == "octave" and _OCTAVE_ENGLISH.search(part[max(0, m.start() - 60):m.end() + 60]):
                    return m.group(0)
                return f"<i>{m.group(1)}</i>{m.group(2)}"
            part = _LINE_NAMES.sub(sub, part)
        out.append(part)
    return "".join(out)


# Italian hand positions / lines written as numerals ("4ª", "4a", "3<sup>a</sup>-in-
# 4<sup>a</sup>", "2nd-in-3rd") -> the line name, drawn when the PDF is made
# (prompt rule: never "4th"; Barbasetti 1902, Bruno 1891). Only these Italian
# shapes: a French book's "5th position" (Cordelois, Baudry) is a numbered drill
# position and is left alone, as are "1.a"-style equation / day references.
_IT_N = {"1": "prima", "2": "seconda", "3": "terza", "4": "quarta",
         "5": "quinta", "6": "sesta", "7": "settima", "8": "ottava"}
_NUM_A = r"([1-8])(?:ª|<sup>a</sup>|\^a|a)"
_NUM_ORD = r"([1-8])(?:st|nd|rd|th)"
_IT_LINE_NUMERAL = re.compile(
    r"(?<![\w.§/(])(?<!fig\. )(?<!plate )(?<!table )(?<!no\. )(?<!p\. )(?<!part )"
    r"(?:" + _NUM_A + r"|" + _NUM_ORD + r"(?=-in-))"
    r"(?:-in-(?:" + _NUM_A + r"|" + _NUM_ORD + r"))?"
    r"(?!\w)", re.IGNORECASE)


def italian_line_numerals(s):
    def sub(m):
        a, b = m.group(1) or m.group(2), m.group(3) or m.group(4)
        return f"<i>{_IT_N[a]}</i>" + (f"-in-<i>{_IT_N[b]}</i>" if b else "")
    return _IT_LINE_NUMERAL.sub(sub, s) if s else s


# Italian "quarta bassa" / "terza alta" -> "low quarta" / "high terza": English
# says low / high, not bassa / alta (reviewer, 2026-10-12). The model wrote it
# three ways (Masiello 1887: "<i>quarta bassa</i>", "<i>quarta</i> bassa",
# "low <i>quarta</i>"); applied when the PDF is drawn, so no retranslation.
# Feminine singular only: the French books' "quarte basse" is a different word.
_IT_LINE_LOW_HIGH = re.compile(
    r"<i>\s*(prima|seconda|terza|quarta|quinta|sesta|settima|ottava)\s+(bassa|alta)\s*</i>"
    r"|<i>\s*(prima|seconda|terza|quarta|quinta|sesta|settima|ottava)\s*</i>\s+(bassa|alta)\b",
    re.IGNORECASE)


def italian_low_high(s):
    def sub(m):
        line, pos = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        word = "low" if pos.lower() == "bassa" else "high"
        if line.isupper() and pos.isupper():
            return f"{word.upper()} <i>{line}</i>"
        if line[:1].isupper():
            return f"{word.capitalize()} <i>{line.lower()}</i>"
        return f"{word} <i>{line}</i>"
    return _IT_LINE_LOW_HIGH.sub(sub, s) if s else s


def markdown_italics(s):
    return _MD_ITALIC.sub(lambda m: "<i>" + re.sub(r"</?i>", "", m.group(1)) + "</i>", s)


def _spans(s):
    """'a <i>b</i> c' -> [('a ', 0), ('b', 1), (' c', 0)]"""
    out, ital = [], 0
    for part in _TAG.split(markdown_italics(s)):
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


def _wrap(spans, width, size, first_indent=0.0):
    """Greedy word-wrap (text, ital) spans into visual lines; the first
    line is `first_indent` pt narrower (a paragraph's first-line indent)."""
    lines, cur, cur_w = [], [], first_indent
    for text, ital in spans:
        for tok in re.findall(r"\S+\s*|\s+", text):
            w = _w(tok, ital, size)
            if cur and tok.strip() and cur_w + w > width:
                lines.append(cur)
                cur, cur_w = [], 0.0          # later lines: full width
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


# Leading spaces are kept as indentation for structure -- drills, braced
# command groups, lists (LaMarche pp. 82-83). The OCR also indents ordinary
# paragraphs, but only some of them, so running prose came out randomly
# indented (LaMarche pp. 14-16): a long prose line with no list marker and
# no brace is set flush left.
_LIST_START = re.compile(r"(?:[—–\-•*]|\d+\s*[°.)]|[a-zA-Z]\)|[IVXLC]+\.\s)")


# A paragraph that follows another with no blank line between them -- the
# OCR often separates paragraphs with a single line break (every book; most
# in LaMarche, Joseph-Renaud, Masiello 1887) -- gets a first-line indent, as
# printed books do; without it the page reads as one dense block
# (Joseph-Renaud pdf 50, 54; 2026-10-06 review). Only prose after a line
# that ends a sentence: list items, drills and verse keep their own layout.
PARA_INDENT_EM = 1.5
_SENT_END = re.compile(r"[.!?…»”\")](?:</i>)?\s*$")   # not ":" -- a list follows it (Masiello 1887 scan 120)
# a section divider of stars on its own line (Joseph-Renaud's "★★") is not
# drawn: the OCR caught it on some pages only (2026-10-06 review); the blank
# lines around it still leave the gap
_STARS = re.compile(r"^\s*[★☆⁂✶✱]+(?:\s*[★☆⁂✶✱]+)*\s*$")


def _para_line(s):
    """A line of running text (any length): starts with a word or a quote,
    isn't a list item, part of a braced group, or a heading in capitals."""
    letters = [c for c in re.sub(r"<[^>]+>", "", s) if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) > 0.7 * len(letters):
        return False
    return ("{" not in s and "}" not in s and not _LIST_START.match(s)
            and bool(re.match(r"[\w“\"«(<]", s)))


def _prose_indent(s):
    return (len(s) >= 60 and "{" not in s and "}" not in s
            and not _LIST_START.match(s) and bool(re.match(r"[\w“\"«(<]", s)))


def layout(text, width, size):
    """text -> list[Line], pre-wrapped to `width` pt at `size`."""
    out = []
    para_end = False            # the line before ended a prose sentence
    text = italian_low_high(italian_line_numerals(line_name_italics(text)))
    for raw in (text or "").split("\n"):
        if _STARS.match(raw):
            continue
        if not raw.strip():
            out.append(Line(gap=True))
            para_end = False
            continue
        m = _MARGIN_RE.match(raw)
        if m:
            para_end = False
            # the note is italic throughout; an <i> term inside it stays italic
            note = re.sub(r"</?i>", "", m.group(1))
            for vline in _wrap([(note, 1)], max(width - NOTE_INDENT, _MIN_WRAP), size):
                out.append(Line(NOTE_INDENT, vline, note=True))
            continue
        n = len(raw) - len(raw.lstrip(" "))
        prose = _prose_indent(raw[n:])
        if n and prose:
            raw, n = raw[n:], 0
        indent = n * _SPACE_PT
        avail = max(width - indent, _MIN_WRAP)
        text_line = n == 0 and _para_line(raw)
        first = size * PARA_INDENT_EM if text_line and para_end else 0.0
        for k, vline in enumerate(_wrap(_spans(raw[n:]), avail, size, first)):
            out.append(Line(indent + (first if k == 0 else 0.0), vline))
        para_end = text_line and bool(_SENT_END.search(raw))
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
