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


def folio_sides(folder, folios):
    """Per page, where the book prints its number and running head (see
    _layout.folio_plan; reviewer, 2026-10-18), or None for every page when
    the scans don't say clearly -- the old fixed layout then."""
    import _layout
    plan = _layout.folio_plan(folder, folios)
    return [_layout.page_side(plan, folios, i) for i in range(len(folios))]


def slot_x(slot, txt, font, size, x0, x1):
    """Left x for `txt` set at `slot` ('L' / 'C' / 'R') between x0 and x1."""
    w = _font(font).text_length(txt, fontsize=size)
    return x0 if slot == "L" else x1 - w if slot == "R" else (x0 + x1 - w) / 2


def place_row(items, x0, x1):
    """[(text, slot, font, size)] -> [(text, x, font, size, line)]: one line
    when they don't collide, else each later item on the next line."""
    out, spans, line = [], [], 0
    for txt, slot, font, size in items:
        x = slot_x(slot, txt, font, size, x0, x1)
        w = _font(font).text_length(txt, fontsize=size)
        if any(not (x + w + 8 <= a or b + 8 <= x) for a, b in spans):
            line += 1
            spans = []
        spans.append((x, x + w))
        out.append((txt, x, font, size, line))
    return out


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


_SUP = re.compile(r"<sup>\s*([^<]{1,4}?)\s*</sup>", re.I)
_SUP_DIGITS = {"1": "¹", "2": "²", "3": "³"}          # the ones the PDF's base fonts have


def _sup(m):
    """A superscript the translation marked up (a note number in a heading
    set in capitals came out as a literal "<SUP>2</SUP>", Prévost-Jollivet
    pdf 210): drawn as ¹ ² ³ where the font has it, else as plain text."""
    t = m.group(1)
    return "".join(_SUP_DIGITS.get(c, c) for c in t) if all(c in _SUP_DIGITS for c in t) else t


def _spans(s):
    """'a <i>b</i> c' -> [('a ', 0), ('b', 1), (' c', 0)]"""
    out, ital = [], 0
    s = _SUP.sub(_sup, s or "")
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
                # punctuation glued to the word before it (after an italic
                # term: "<i>quarte</i>; diseng.") goes down with that word,
                # never alone at the start of a line (Gomard scan 229)
                carry = []
                if (re.match(r"[;:,.!?)\]»”’]", tok) and len(cur) > 1
                        and not cur[-1][0][-1:].isspace()):
                    carry = [cur.pop()]
                lines.append(cur)
                cur = carry                   # later lines: full width
                cur_w = sum(_w(t, i, size) for t, i in carry)
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
    __slots__ = ("indent", "spans", "gap", "note", "tail", "table")

    def __init__(self, indent=0.0, spans=None, gap=False, note=False, tail="", table=""):
        self.indent = indent
        self.spans = spans or []
        self.gap = gap
        self.note = note        # an author's margin note (see layout())
        self.tail = tail        # a contents line's page number: set flush right, dot leaders before it
        self.table = table      # an HTML table drawn as a grid in the flow (draw_table)


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
DIVIDER = "[[DIVIDER]]"     # a section divider kept from the book (2_translate.restore_dividers)


def _divider(width, size):
    """The book's section divider: centred stars with space around them
    (reviewer, 2026-10-18; whatever the book's own ornament)."""
    mark = "*      *      *"
    return [Line(gap=True), Line(max(0.0, (width - _w(mark, 0, size)) / 2), [(mark, 0)]), Line(gap=True)]


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


# a line that ends a paragraph in the book: a sentence end, or the ";" / ":"
# that closes each item of a run-in list (Kirchhoffer pdf 13: "La lame, ...;"
# / "La garde, ...;" each its own indented paragraph)
_PARA_END = re.compile(r"[.!?…»”\")\];:](?:</i>)?\s*$")


def centres_headings(folder):
    """Does the book centre its headings? From the layout detector: most of
    its heading boxes sit centred over the text (70-98 % in most books;
    Bruno, Danet, Saint-Martin under half -- those stay flush left)."""
    import _layout
    lay = _layout.load(folder)
    if not lay:
        return False
    centred = total = 0
    for p in lay["pages"].values():
        body = [b for b in p["boxes"] if b[0] == "text" and b[1] >= 0.5]
        if not body:
            continue
        x0, x1 = min(b[2][0] for b in body), max(b[2][2] for b in body)
        for b in p["boxes"]:
            if b[0] in ("section_header", "title") and b[1] >= 0.5 and (b[2][1] + b[2][3]) / 2 > 0.12:
                left, right = b[2][0] - x0, x1 - b[2][2]
                total += 1
                centred += abs(left - right) < 0.06 * (x1 - x0) and left > 0.04
    return total >= 20 and centred >= 0.5 * total


def book_paragraphs(stem, books_dir="/data/books"):
    """The book's paragraph style ([book] paragraphs; "indented" unless set)."""
    import _overrides
    try:
        return _overrides.load(stem, books_dir)["book"].get("paragraphs", "indented")
    except _overrides.OverridesError:
        return "indented"


def layout(text, width, size, paras=None, centre=False, toc=None):
    """text -> list[Line], pre-wrapped to `width` pt at `size`.

    `paras` -- body text only: the book's paragraph style (reviewer,
    2026-10-18). The OCR marks paragraphs at random -- a blank line or a
    bare line break, 0, 2 or 4 leading spaces (Kirchhoffer pdf 13) -- so the
    page came out partly indented, partly spaced. "indented": every
    paragraph of running text opens with a first-line indent and a blank
    line between two of them is dropped; "spaced": a blank line between
    paragraphs, no indent. Either way headings, lists, drills, verse and
    tables keep their own layout, and a page that opens mid-sentence (a
    small letter) gets no indent. None: the old rules (captions, notes).
    `centre` -- the book centres its headings (_layout.centres_headings):
    a heading line in capitals is centred too. `toc` -- the page is a
    contents / index page (None: decided from this text; a caller drawing
    a page paragraph by paragraph decides once for the page). Text starting
    _textlayer.CONT opens mid-paragraph (the scan says so): no indent."""
    cont = (text or "").startswith("\x02")
    text = (text or "").lstrip("\x02")
    if not paras or (toc if toc is not None else False):
        text = re.sub("[\x03-\x05]", "", text)    # _textlayer's row marks: body text only
    if paras:
        return _layout_paras(text, width, size, paras, centre, toc, cont)
    out = []
    para_end = False            # the line before ended a prose sentence
    text = italian_low_high(italian_line_numerals(line_name_italics(text)))
    for raw in (text or "").split("\n"):
        if _STARS.match(raw) or raw.strip() == DIVIDER:
            out += _divider(width, size)
            para_end = False
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


_LEADERS = re.compile(r"\.{3,}|…{2,}|\s\d+\s*$")   # a contents line: dot leaders, a page number


_RUN_IN = re.compile(r"[^\W\d][^.!?—]{0,40}?[.:]\s*[—–]\s")   # "L'épée. — ", up to ~6 words


def _heading_line(plain):
    """A heading set in capitals: mostly capital letters, short, not a
    contents line."""
    letters = [c for c in plain if c.isalpha()]
    return (len(letters) >= 3 and sum(c.isupper() for c in letters) > 0.7 * len(letters)
            and len(plain.split()) <= 15 and not _LEADERS.search(plain))


def _rejoin(text):
    """The text's lines, with a sentence the OCR broke at a printed line end
    put back together ("Saber bouts, in the fencing hall," / "are less
    subject ...", Jacob-André pdf 220): the line runs on (a comma or a word,
    25+ letters) and the next opens with a small letter. Never inside a
    brace group or a heading."""
    out = []
    for line in (text or "").split("\n"):
        last = out[-1] if out else ""
        if (last.strip() and len(re.sub(r"<[^>]+>", "", last).strip()) >= 25
                and re.search(r"[,\w](?:</i>)?$", last.rstrip())
                and re.match(r" {0,4}(?:<i>)?[a-zà-ÿ]", line)
                and "{" not in last and "{" not in line and "}" not in line
                and not _heading_line(re.sub(r"<[^>]+>", "", last).strip())
                and not _MARGIN_RE.match(last) and not _MARGIN_RE.match(line)):
            out[-1] = last.rstrip() + " " + line.strip()
        else:
            out.append(line)
    return out


def _title_line(plain):
    """A heading in ordinary case standing alone between blank lines: short,
    one phrase, not a sentence of running text -- "52. Double Circling and
    Double Controcavazione" (Barbasetti), "Synoptic charts." (Masiello).
    Not a quotation, a colon line, or a contents line."""
    words = plain.split()
    return (0 < len(words) <= 10 and not re.match(r"[«“\"(]", plain)
            and not re.search(r"[:;,]$", plain) and not _LEADERS.search(plain)
            and not re.search(r"[.!?…]\s+\S", re.sub(r"^(?:§\s*)?[\dIVXLC]+[.°]\s*(?:—\s*)?", "", plain))
            and plain[:1] not in "-—–•*" and bool(re.match(r"(?:§\s*)?[\w(]", plain))
            and not plain[:1].islower())


# a contents / index line: its page number at the end, after dot leaders, or
# "Page 61" / "Pag. 61" / "» 263" / "ibid." (Barbasetti, Masiello, Kirchhoffer)
_TOC_END = re.compile(r"^(?P<text>.*?\S)(?:\s*(?:\.\s*){2,}|\s*…+\s*|\s+)"
                      r"(?P<num>(?:(?:Page|Pag\.|p\.)\s*)?(?:[»\"]\s*)?(?:\d{1,4}\*?|[IVXLC]{1,7}|ibid\.?))\s*$")
_TOC_STRONG = re.compile(r"(?:\.\s*){2,}|…|\b(?:Page|Pag\.)\s*\d|»\s*(?:\d|ibid)")


def toc_page(text):
    return _toc_page((text or "").split("\n"))


def _toc_page(rows):
    """A contents or index page: 3+ lines ending in a page number with
    dot leaders, "Page N" or "» N"."""
    return sum(1 for r in rows if _TOC_END.match(r.strip()) and _TOC_STRONG.search(r)) >= 3


def _layout_toc(rows, width, size, centre):
    """Contents lines: the text, dot leaders, the page number flush right
    (as the book sets them); a two-column row ("10. Parry ... Page 61 | 22.
    Parries ...", Barbasetti) is split in two. No paragraph indent; the
    OCR's own indents and blank lines stay; headings centred."""
    out = []
    lines = []
    # a two-column contents ("49. … Pag. 119 | 53. … Pag. 124", Barbasetti):
    # a run of such rows is read column by column -- the left column down,
    # then the right -- as the book is (format review, 2026-10-22, pdf 9)
    run = []
    def flush():
        lines.extend([l for l, _ in run] + [r for _, r in run if r])
        run.clear()
    for r in rows:
        if " | " in r or r.rstrip().endswith(" |"):
            parts = re.split(r"\s+\|\s*", r.rstrip(), maxsplit=1)
            run.append((parts[0], parts[1].strip() if len(parts) > 1 else ""))
        else:
            flush()
            lines.append(r)
    flush()
    # the OCR doesn't always keep a two-column contents in pairs ("11. Lunge"
    # alone on a row, Barbasetti pdf 7): its entries are numbered, so each run
    # of numbered entries goes in number order
    if any(" | " in r or r.rstrip().endswith(" |") for r in rows):
        num = lambda l: re.match(r"\s*(\d+)\.\s", l)
        k = 0
        while k < len(lines):
            j = k
            while j < len(lines) and num(lines[j]):
                j += 1
            if j - k > 1:
                lines[k:j] = sorted(lines[k:j], key=lambda l: int(num(l).group(1)))
            k = max(j, k + 1)
    for raw in lines:
        # OCR debris in the leaders: "parry...... /...... 78" (Prévost-Jollivet)
        raw = re.sub(r"(\.{2,})\s*[/|\\]\s*(\.{2,})", r"\1\2", raw)
        if not raw.strip():
            out.append(Line(gap=True))
            continue
        if _STARS.match(raw) or raw.strip() == DIVIDER:
            out += _divider(width, size)
            continue
        n = min(len(raw) - len(raw.lstrip(" ")), 8)
        body = raw.strip()
        plain = re.sub(r"<[^>]+>", "", body)
        m = _TOC_END.match(body)
        if m and not _heading_line(plain):
            tail = m.group("num")
            reserve = _w(tail, 0, size) + 24
            entry = re.sub(r"\s+\.$", "", m.group("text"))      # "Beat Parries . » 67"
            vl = _wrap(_spans(entry), max(width - n * _SPACE_PT - reserve, _MIN_WRAP), size)
            for k, v in enumerate(vl):
                out.append(Line(n * _SPACE_PT if k == 0 else n * _SPACE_PT + 12, v,
                                tail=tail if k == len(vl) - 1 else ""))
            continue
        if centre and (_heading_line(plain) or re.fullmatch(r"(?:CHAPTER|CAPITOLO|CHAPITRE|PART|PARTE)\b.*", plain, re.I)):
            for v in _wrap(_spans(body), width, size):
                out.append(Line(max(0.0, (width - sum(_w(t, i, size) for t, i in v)) / 2), v))
            continue
        for k, v in enumerate(_wrap(_spans(body), max(width - n * _SPACE_PT, _MIN_WRAP), size)):
            out.append(Line(n * _SPACE_PT if k == 0 else n * _SPACE_PT + 12, v))
    return out


_STEP = re.compile(r"^(?:[IVX]+|\d+)\.?\s+(?:TIME|TEMPO|TEMPS)\b", re.I)
_SPEAKER = re.compile(r"^(?:<i>)?(Maestro|Master|Allievo|Student|Pupil|Professeur|Élève)(?:</i>)?\s*[:.]?\s*$", re.I)


def lesson_glue(text):
    """A lesson with numbered steps -- the master's actions, then the
    pupil's "I. TEMPO ..." steps -- as one block, its blank lines gone, so
    _pair_lesson sees both halves (the side-by-side lays a page out block by
    block; the OCR sometimes leaves a blank line between every line of a
    lesson, Barbasetti pdf 141). The lesson runs back from its first step
    over speaker heads and short lines to a heading ("IV. EXAMPLE") or a
    long paragraph, and on to the line after its last step's text."""
    rows = (text or "").split("\n")
    plain = [_unmarked(r) for r in rows]
    steps = [k for k, r in enumerate(plain) if _STEP.match(r)]
    if not steps:
        return text
    drop = set()
    k = 0
    while k < len(steps):
        first = steps[k]
        # the steps: each with the lines straight under it; after a blank
        # line only another step continues the lesson
        j, last = first, first
        while j < len(rows) and _STEP.match(plain[j]):
            j += 1
            while j < len(rows) and plain[j] and not _STEP.match(plain[j]):
                j += 1
            last = j - 1
            while j < len(rows) and not plain[j]:
                j += 1
        start = first
        n = first - 1
        while n >= 0:
            r = plain[n]
            if not r:
                n -= 1
                continue
            heading = r.isupper() or re.match(r"^[IVX]+\.\s+\w+$", r) or re.match(r"^[a-z]\)\s", r)
            if heading or len(r.split()) > 40:
                break
            start = n
            n -= 1
        for m in range(start, last + 1):
            if not rows[m].strip():
                drop.add(m)
        while k < len(steps) and steps[k] <= last:
            k += 1
    return "\n".join(r for m, r in enumerate(rows) if m not in drop)


def _unmarked(row):
    """A row without _textlayer's start marks (INDENTED / FLUSH), stripped."""
    return row.strip().lstrip("\x03\x04\x05").strip()


def _pair_lesson(rows):
    """A lesson set in two columns in the book -- the master's actions left,
    the pupil's numbered steps right (Barbasetti: "Maestro: | Allievo: / I.
    TEMPO ...") -- arrives as two blocks one after the other. When the
    master has as many lines as the pupil has steps, pair them back into a
    two-column table, so the actions alternate as printed (reviewer,
    2026-10-18). Returns the rows with each such run replaced by one
    "\x00TABLE" row."""
    import html as _html
    out, k = [], 0
    while k < len(rows):
        j = k
        heads = []
        if _SPEAKER.match(_unmarked(rows[j])):
            heads.append(_unmarked(rows[j])); j += 1
        left = []
        while j < len(rows) and _unmarked(rows[j]) and not _STEP.match(_unmarked(rows[j])) \
                and not _SPEAKER.match(_unmarked(rows[j])):
            left.append(_unmarked(rows[j])); j += 1
        while j < len(rows) and not _unmarked(rows[j]):
            j += 1
        if j < len(rows) and _SPEAKER.match(_unmarked(rows[j])):
            heads.append(_unmarked(rows[j])); j += 1
        steps = []
        while j < len(rows) and _unmarked(rows[j]):
            line = _unmarked(rows[j])
            if _STEP.match(line):
                steps.append([line])
            elif steps:
                steps[-1].append(line)
            else:
                break
            j += 1
        if left and len(steps) >= 2 and len(left) == len(steps) and all(len(x) >= 2 for x in steps) \
                and all(len(re.sub(r"<[^>]+>", "", l).split()) <= 25 for l in left):    # (Barbasetti pdf 141: 19 words)
            cell = lambda t: re.sub(r"&lt;(/?i)&gt;", r"<\1>", _html.escape(t))
            body = ""
            if len(heads) == 2:
                body += "<tr>" + "".join(f"<th>{cell(re.sub(r'[:.]$', '', h))}</th>" for h in heads) + "</tr>"
            for l, st in zip(left, steps):
                body += f"<tr><td>{cell(l)}</td><td>{cell(st[0])}<br>{cell(' '.join(st[1:]))}</td></tr>"
            out.append("\x00TABLE" + "<table>" + body + "</table>")
            k = j
            continue
        out.append(rows[k]); k += 1
    return out


def _layout_paras(text, width, size, style, centre=False, toc=None, cont=False):
    """layout() with a paragraph style. A line is a paragraph of running
    text only when it reads as one: long (60+ letters), or ending a sentence
    or list item right after another paragraph (Kirchhoffer's "The thumb
    guard, ...;"). Short lines without that ending, and the lines of a brace
    group, are structure -- drill steps, two-column dialogue tables (Barbasetti
    scan 134), Gomard's braced feints (scan 232) -- and keep the OCR's own
    lines and blank lines. A blank line is dropped only between two
    paragraphs, at least one of them long (two short ones -- lines of
    dialogue -- keep it). `cont`: the page opens mid-paragraph (the scan's
    text layer, _textlayer): its first paragraph gets no indent."""
    out = []
    prev, prev_long = "start", False    # start | prose | prose-open | other
    gap = brace = prev_colon = False
    text = italian_low_high(italian_line_numerals(line_name_italics(text)))
    if toc if toc is not None else _toc_page((text or "").split("\n")):
        return _layout_toc(re.sub("[\x03-\x05]", "", text or "").split("\n"), width, size, centre)
    rows = _pair_lesson(_rejoin(text))
    for at, raw in enumerate(rows):
        if raw.startswith("\x00TABLE"):
            # a full blank line before and after a table, never tight against
            # the text (reviewer, 2026-10-18)
            if out:
                out += [Line(gap=True), Line(gap=True)]
            out.append(Line(table=raw[len("\x00TABLE"):]))
            out += [Line(gap=True)]
            gap, prev, prev_long = False, "other", False
            continue
        if _STARS.match(raw) or raw.strip() == DIVIDER:
            out += _divider(width, size)
            gap, prev, prev_long = False, "other", False
            continue
        if not raw.strip():
            gap = prev != "start"
            brace = False
            continue
        # where the book starts this row (_textlayer): at the paragraph indent,
        # or flush -- what the print shows wins over the guesses below
        head = raw.lstrip(" ")[:1]
        forced = {"\x03": "indent", "\x04": "flush", "\x05": "hanging"}.get(head)
        raw = raw.replace("\x03", "").replace("\x04", "").replace("\x05", "")
        m = _MARGIN_RE.match(raw)
        if m:
            if gap:
                out.append(Line(gap=True))
            gap, prev = False, "other"
            note = re.sub(r"</?i>", "", m.group(1))
            for vline in _wrap([(note, 1)], max(width - NOTE_INDENT, _MIN_WRAP), size):
                out.append(Line(NOTE_INDENT, vline, note=True))
            continue
        brace = brace or "{" in raw
        n = len(raw) - len(raw.lstrip(" "))
        body = raw[n:]
        plain = re.sub(r"<[^>]+>", "", body).strip()
        long_ = len(plain) >= 60
        ends = bool(_PARA_END.search(body))
        # a long numbered paragraph ("4° Le bras droit ...", Joseph-Renaud pdf
        # 8) is a paragraph like any other; a short numbered drill step is not
        numbered = bool(_LIST_START.match(body)) and long_ and not body.lstrip().startswith(("—", "-", "•", "*"))
        prose = (not brace and (n <= 4 or long_) and (_para_line(body) or numbered)
                 and (long_ or (ends and prev == "prose")))
        if forced and not brace:
            prose = True                  # the print says it's a paragraph of its own
        if prose:
            raw, n = body, 0
        new_para = prose and prev in ("start", "prose", "other") and not (
            prev == "start" and (cont or not long_ or re.match(r"(?:<i>)?[«“\"(]?[a-zà-ÿ]", raw)))
        if forced == "indent" and not brace:
            new_para = True
        elif forced in ("flush", "hanging"):
            new_para = False
        # ... but not before a run-in section title ("L'épée. — Tous les
        # assauts", "Tactique générale. — "): the book leaves space there
        run_in = bool(_RUN_IN.match(plain))
        drop_gap = (style == "indented" and prose and prev == "prose" and (long_ or prev_long)
                    and not run_in and not prev_colon)
        alone = ((at == 0 or not rows[at - 1].strip()) and (at + 1 == len(rows) or not rows[at + 1].strip()))
        # a title ending "." ("Synoptic charts.") only after a heading or at
        # the top -- after running text it is a one-line paragraph ("He returns.")
        # the page's last line breaking off mid-sentence ("... taking a pace
        # forward, in", Prévost-Jollivet pdf 210) runs on to the next page
        cut = (not any(r.strip() for r in rows[at + 1:]) and len(plain.split()) >= 6
               and bool(re.search(r"[a-zà-ÿ,]$", plain)))
        # a row the text layer placed (indent, flush, hanging) is text, not a
        # heading (Prévost-Jollivet pdf 315: a glossary's last headword)
        title = alone and not cut and not forced and _title_line(plain) and (
            not re.search(r"\.(?:</i>)?$", body) or prev in ("start", "other")
            or bool(re.match(r"(?:§\s*)?[\dIVXLC]+[.°]", plain)))
        if centre and not brace and not forced and (title or (not prose and _heading_line(plain))):
            # a heading: centred, its space before kept
            if gap:
                out.append(Line(gap=True))
            gap = prev_colon = False
            for vline in _wrap(_spans(body), width, size):
                w = sum(_w(t, i, size) for t, i in vline)
                out.append(Line(max(0.0, (width - w) / 2), vline))
            prev, prev_long = "other", False
            continue
        if gap and not drop_gap:
            out.append(Line(gap=True))
        elif style == "spaced" and new_para and prev == "prose" and not gap:
            out.append(Line(gap=True))
        gap = False
        first = size * PARA_INDENT_EM if (style == "indented" and new_para) else 0.0
        indent = n * _SPACE_PT
        avail = max(width - indent, _MIN_WRAP)
        if forced == "hanging" and not brace:
            # a glossary entry as the book sets it: first line flush, the rest in
            hang = size * PARA_INDENT_EM
            for k, vline in enumerate(_wrap(_spans(raw[n:]), max(avail - hang, _MIN_WRAP), size, -hang)):
                out.append(Line(indent + (0.0 if k == 0 else hang), vline))
        else:
            for k, vline in enumerate(_wrap(_spans(raw[n:]), avail, size, first)):
                out.append(Line(indent + (first if k == 0 else 0.0), vline))
        prev = ("prose" if ends else "prose-open") if prose else "other"
        prev_long = long_
        # a line ending ":" introduces a list or table: the gap after it stays
        prev_colon = bool(re.search(r":(?:</i>)?\s*$", body))
    return out


def display_page(text, page_num):
    """A display page -- title page, half-title, a plate's title leaf, a
    publisher's notice: no printed page number (none read, or reconcile's
    "[n]") and only a few short blocks (8 at most, 30 words each). The book
    centres such a page's lines, and a line the OCR filed as its running
    head is its title (Kirchhoffer pdf 10: «L'ESCRIME»), not a head."""
    pn = str(page_num or "").strip()
    if pn and not pn.startswith("[") and pn.lower() != "none":
        return False
    text = (text or "").strip()
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return (bool(paras) and not text.startswith("[") and len(paras) <= 8
            and all(len(p.split()) <= 30 for p in paras)
            and not re.search(r"\[\[TABLE|\[table|<table", text) and not toc_page(text))


def centre_lines(lines, width, size):
    """The same lines, each centred in `width` (a title page's lines)."""
    for ln in lines:
        if not ln.gap and ln.indent >= 0 and not ln.tail:
            w = sum(_w(t, i, size) for t, i in ln.spans if t)
            ln.indent = max(0.0, (width - w) / 2)
    return lines


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
        if ln.table:
            h = draw_table(page, (x0, y, x1, y1), ln.table, size=min(size, 8.5), dry=True)
            if h is None:
                if idx == 0:            # can't fit even on a fresh page: skip it
                    continue
                leftover = lines[idx:]
                break
            if not dry:
                draw_table(page, (x0, y, x1, y1), ln.table, size=min(size, 8.5))
            y = h + lh * 0.4
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
            if ln.tail:
                tail_w = _font(ROMAN).text_length(ln.tail, fontsize=size)
                dot_w = _font(ROMAN).text_length(". ", fontsize=size)
                gap_w = x1 - tail_w - 4 - (pt.x + 4)
                if gap_w > dot_w:
                    writer.append(fitz.Point(pt.x + 4, y + size), ". " * int(gap_w / dot_w),
                                  font=_font(ROMAN), fontsize=size)
                writer.append(fitz.Point(x1 - tail_w, y + size), ln.tail,
                              font=_font(ROMAN), fontsize=size)
                wrote = wrote or not ln.note
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
    return body.rstrip(), [n for l in rest.strip().splitlines() if l.strip() for n in split_note_run(l)]


def drop_margin_echoes(text):
    """A margin note the OCR also read into the running text (Gomard pdf
    310: "The gliding or glide." as a body line next to ⟦MARGIN: The
    gliding or glide.⟧): the body copy goes. A body line is an echo when
    it is short (12 words or fewer) and its words are a margin note's
    within eight lines of it."""
    lines = (text or "").split("\n")
    plain = lambda t: re.sub(r"[^\w]+", " ", re.sub(r"<[^>]+>", "", t)).strip().lower()
    notes = [(k, plain(m.group(1))) for k, l in enumerate(lines) for m in [_MARGIN_RE.match(l)] if m]
    if not notes:
        return text
    out = []
    for k, l in enumerate(lines):
        p = plain(l)
        if p and not _MARGIN_RE.match(l) and len(p.split()) <= 12 and (
                # further than 3 lines only a phrase of 4+ words (Gomard pdf 65, 6 apart):
                # a short lesson cell the margin step mistook for a note is text (Masiello pdf 581)
                any((abs(k - j) <= 3 or (abs(k - j) <= 8 and len(p.split()) >= 4))
                    and n and (p == n or (len(p) > 8 and p in n)) for j, n in notes)):
            continue
        out.append(l)
    return "\n".join(out)


_LINE_QUOTE = re.compile(r"^(\s*)(?:«|•)\s*")


def english_quotes(text):
    """The book's quotation marks in English form (reviewer, 2026-10-22):
    «…» -> “…”, straight "…" -> “…”. Old books open every printed line of a
    quotation with « (Gomard, Barbasetti) -- the OCR sometimes reads it as
    • (Prévost-Jollivet pdf 237, which came out as a bulleted list): a run
    of 3+ such lines, one of them running on (a small letter), is one
    quotation -- its first mark opens it, the rest go. A » standing alone
    between spaces is a ditto mark ("4th  »  wide", contents "...... » 268")
    and stays. Table rows are left alone; contents pages are not passed in."""
    lines = (text or "").split("\n")
    # 1. per-line marks: a run of lines opening with « or •
    k = 0
    while k < len(lines):
        j = k
        while j < len(lines) and _LINE_QUOTE.match(lines[j]) and not lines[j].startswith("\x00TABLE"):
            j += 1
        if j - k >= 3 and any(re.match(r"\s*(?:«|•)\s*[a-zà-ÿ]", lines[n]) for n in range(k + 1, j)):
            lines[k] = _LINE_QUOTE.sub(lambda m: m.group(1) + "«", lines[k], count=1)
            for n in range(k + 1, j):
                lines[n] = _LINE_QUOTE.sub(lambda m: m.group(1), lines[n], count=1)
        k = max(j, k + 1)
    # 2. pair « with », dropping a « that reopens an open quotation, or that
    # opens a line (or follows a split word) before a small letter -- the
    # per-line mark of a quotation opened earlier: "after the examination /
    # « approved", "out- « side"
    out, open_ = [], False
    for line in lines:
        if line.startswith("\x00TABLE"):
            out.append(line)
            continue
        res, i = [], 0
        while i < len(line):
            c = line[i]
            if c == "«":
                rest = line[i + 1:].lstrip(" \u00a0")
                cont = bool(re.match(r"[a-zà-ÿ]", rest)) and (not line[:i].strip() or line[:i].rstrip().endswith("-"))
                if open_ or cont:
                    i += 1                                   # a continuation mark: gone
                    while i < len(line) and line[i] == " ":
                        i += 1
                    continue
                open_ = True
                res.append("“")
                i += 1
                while i < len(line) and line[i] in " \u00a0":
                    i += 1
                continue
            if c == "»":
                before = line[:i].rstrip()
                after = line[i + 1:i + 2]
                # alone between spaces, not after the end of a sentence: a ditto mark
                ditto = (not open_ and (not before or line[i - 1] in " \u00a0\t.") and (not after or after in " \u00a0\t")
                         and not (re.search(r"[.!?…]$", before) and not re.search(r"(?:\.\s?){4,}$", before)))
                if ditto:
                    res.append(c)
                else:
                    while res and res[-1] in " \u00a0":
                        res.pop()
                    res.append("”")
                    open_ = False
                i += 1
                continue
            res.append(c)
            i += 1
        out.append("".join(res))
    # 3. straight double quotes, by position: opening after a space or start
    # (never inside a table row: its HTML has colspan="13" -- Kirchhoffer pdf 75)
    def straight(line):
        if line.startswith("\x00TABLE"):
            return line
        line = re.sub(r'(^|[\s(\[—–-])"(?=\S)', "\\1“", line)
        return line.replace('"', "”")
    return "\n".join(straight(l) for l in out)


def split_note_run(note):
    """One line holding several of the author's notes -- "(1) ... (2) ...",
    "1. ... 2. ..." (the translation runs them together; Kirchhoffer pdf 26,
    Prévost-Jollivet pdf 210) -> one string per note. Split only at the
    next number in sequence, after the end of a sentence or a closing
    mark, so a number inside a note never splits it."""
    m = re.match(r"\s*(\()?(\d{1,3})(\))?[.)]?\s", note)
    if not m or (m.group(1) and not m.group(3)) or (not m.group(1) and not re.match(r"\s*\d{1,3}\.\s", note)):
        return [note]
    paren, k = bool(m.group(1)), int(m.group(2))
    out, rest = [], note
    while True:
        nxt = (rf"\({k + 1}\)\s" if paren else rf"{k + 1}\.\s+(?=[A-ZÀ-Ý«“\"(<])")
        hit = None
        for h in re.finditer(rf"(?<=[.!?»”)\]…:;])\s+(?={nxt})", rest):
            # not after "p." / "fig." / "art." ...: "see p. 3. Then" is no note 3
            # ("Op. cit." and "ibid." end a note, so they may come before one)
            if not re.search(r"(?i)\b(?:p|pp|fig|figs|art|n|no|nos|vol|ch|chap|cf)\.$", rest[:h.start()]):
                hit = h
                break
        if not hit:
            break
        out.append(rest[:hit.start()].rstrip())
        rest, k = rest[hit.end():], k + 1
    return out + [rest] if out else [note]


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


# ---- tables ----------------------------------------------------------------

def parse_table(html_text):
    """An OCR'd HTML table -> (rows, n_cols): rows = [[cell, ...], ...],
    cell = {"text", "row", "col", "rowspan", "colspan", "head"} placed on a
    grid (rowspan / colspan honoured)."""
    import html as _html
    rows, taken, out = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html_text, re.S | re.I), {}, []
    n_cols = 0
    for r, row in enumerate(rows):
        c, line = 0, []
        for m in re.finditer(r"<(t[hd])\b([^>]*)>(.*?)</t[hd]>", row, re.S | re.I):
            while taken.get((r, c)):
                c += 1
            rs = int((re.search(r'rowspan="?(\d+)', m.group(2)) or [0, 1])[1])
            cs = int((re.search(r'colspan="?(\d+)', m.group(2)) or [0, 1])[1])
            text = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<(?!/?i>|br\s*/?>)[^>]+>", " ", m.group(3)))).strip()
            text = re.sub(r"\s*<br\s*/?>\s*", "\n", text)
            line.append({"text": text, "row": r, "col": c, "rowspan": rs, "colspan": cs,
                         "head": m.group(1).lower() == "th"})
            for dr in range(rs):
                for dc in range(cs):
                    taken[(r + dr, c + dc)] = True
            c += cs
        n_cols = max(n_cols, c)
        out.append(line)
    return out, n_cols


def draw_table(page, rect, html_text, size=7.5, dry=False, min_size=5.5):
    """Draw an HTML table as a ruled grid in rect = (x0, y0, x1, y1); the
    largest size from `size` down to `min_size` that fits. Returns the y
    under the table (or None when it can't fit at all)."""
    rows, n_cols = parse_table(html_text)
    if not rows or not n_cols:
        return None
    x0, y0, x1, y1 = rect
    W = x1 - x0
    pad = 2.0
    while size >= min_size:
        # column widths: by the longest word / content of single-column cells
        need = [8.0] * n_cols
        floor = [8.0] * n_cols          # the longest word: a column never narrower
        for line in rows:
            for c in line:
                if c["colspan"] == 1 and c["text"]:
                    # a heading cell is set in bold: measure it in bold (Bruno's
                    # "Paragraph" ran into "Order number")
                    wd = (lambda t: _font(BOLD).text_length(t, fontsize=size)) if c["head"] \
                        else (lambda t: _w(t, 0, size))
                    words = re.sub(r"</?i>", "", c["text"]).split()
                    longest = max((wd(w) for w in words), default=0)
                    full = wd(re.sub(r"</?i>", "", c["text"]))
                    need[c["col"]] = max(need[c["col"]], longest + 2 * pad, min(full, 140) * 0.6)
                    floor[c["col"]] = max(floor[c["col"]], longest + 2 * pad)
        if sum(floor) > W and size - 0.5 >= min_size:   # longest words don't fit: smaller type
            size -= 0.5
            continue
        if sum(floor) > W:              # not even at the smallest size: squeeze all
            widths = [f_ * W / sum(floor) for f_ in floor]
        else:
            # share out what is left above the floors (Bruno's "Paragraph" was
            # squeezed under its own width and ran into "Order number")
            extra = [n_ - f_ for n_, f_ in zip(need, floor)]
            k = (W - sum(floor)) / sum(extra) if sum(extra) else 0.0
            widths = [f_ + e_ * min(k, 10.0) for f_, e_ in zip(floor, extra)]
            widths = [w * W / sum(widths) for w in widths]
        xs = [x0]
        for w in widths:
            xs.append(xs[-1] + w)
        # row heights from wrapped text
        n_rows = len(rows)
        lh = size * 1.2
        heights = [lh + 2 * pad] * n_rows
        laid = []
        for line in rows:
            for c in line:
                cw = xs[c["col"] + c["colspan"]] - xs[c["col"]] - 2 * pad
                lines = [v for part in c["text"].split("\n") if part.strip()
                         for v in _wrap(_spans(part), max(cw, 4), size)]
                laid.append((c, lines))
                if c["rowspan"] == 1:
                    heights[c["row"]] = max(heights[c["row"]], len(lines) * lh + 2 * pad)
        if sum(heights) <= y1 - y0:
            break
        size -= 0.5
    else:
        return None
    ys = [y0]
    for h in heights:
        ys.append(ys[-1] + h)
    if dry:
        return ys[-1]
    tw = fitz.TextWriter(page.rect)
    for c, lines in laid:
        cx0, cx1 = xs[c["col"]], xs[c["col"] + c["colspan"]]
        cy0, cy1 = ys[c["row"]], ys[min(c["row"] + c["rowspan"], n_rows)]
        page.draw_rect(fitz.Rect(cx0, cy0, cx1, cy1), width=0.4, color=(0.45, 0.45, 0.45))
        ty = cy0 + pad
        for vl in lines:
            w = sum(_w(t, i, size) for t, i in vl)
            tx = (cx0 + cx1 - w) / 2 if c["head"] else cx0 + pad
            pt = fitz.Point(tx, ty + size)
            for t, ital in vl:
                if t:
                    res = tw.append(pt, t, font=_font(BOLD if c["head"] and not ital else ITALIC if ital else ROMAN),
                                    fontsize=size)
                    pt = res[1] if isinstance(res, (tuple, list)) else res
            ty += lh
    tw.write_text(page)
    return ys[-1]


def load_tables(int_dir):
    """intermediate/tables.json (2_translate.translate_tables), or {}."""
    import json
    p = os.path.join(int_dir, "tables.json")
    try:
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    except ValueError:
        return {}


def place_tables(text, entry):
    """Put a page's translated tables where its [[TABLE]] markers are, each
    as one layout row ("\x00TABLE<html>"); more tables than markers -> the
    rest follow the last marker. Returns (text, all_placed): a marker with
    no translated table (none yet, or one the OCR read as headings only)
    stays as the marker, for the caller's pointer to the scan."""
    marks = len(re.findall(r"(?m)^[ \t]*\[\[TABLE\]\][ \t]*$", text or ""))
    tables = (entry or {}).get("en") or []
    if not marks or not tables:
        return text, not marks
    groups = [[t] for t in tables[:marks - 1]] + [tables[marks - 1:]]
    it = iter(groups)
    left = [False]

    def sub(m):
        group = next(it, [])
        if not group or None in group:
            left[0] = True
        # a table without a translation keeps its marker, in its place
        rows = [("\x00TABLE" + t) if t else "[[TABLE]]" for t in group]
        rows = [r for k, r in enumerate(rows) if r != "[[TABLE]]" or k == 0 or rows[k - 1] != r]
        return "\n".join(rows) or m.group(0)
    text = re.sub(r"(?m)^[ \t]*\[\[TABLE\]\][ \t]*$", sub, text)
    return text, not left[0]
