# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Paragraph starts read off the scan's own text layer (the digitiser's OCR
under the page image: Gallica, Internet Archive, Google, a scanning app).

Its words are too poor to translate from, but its line positions are the
printed page's: a line that starts further in than the lines around it opens
a paragraph. The OCR model's line breaks don't say that -- it breaks where
the print wraps round a drop capital (Kirchhoffer pdf 12, «Jusqu'à l'âge»
beside the big L) and a word hyphenated across two pages carries its whole
line over (pdf 14, «Il faudra se mé-|fier»), so both came out as new,
indented paragraphs. Steps 3 and 4 call apply() on each page's English:

  * a row the text layer shows as running on (beside a drop capital, or not
    indented) is joined to the row above;
  * a page whose first printed line is not indented opens mid-paragraph --
    the English gets CONT in front, and layout() gives it no indent;
  * a run-in section title ("Le vêtement de l'escrimeur. — ") loses the
    OCR's blank line before it where the book leaves no space (pdf 13).

No text layer, or rows that can't be matched to it: the English is returned
unchanged (the old rules). Nothing here changes a word of the text.
"""
import difflib
import re
import unicodedata

CONT = "\x02"            # in front of a page's English: it opens mid-paragraph
DIVIDER = "[[DIVIDER]]"  # _render.DIVIDER: a section divider
INDENTED = "\x03"       # in front of a row: the book starts it at the paragraph indent
FLUSH = "\x04"          # in front of a row: the book starts it at the margin, not indented

INDENT = 0.012           # a paragraph indent: this much of the page width in from the margin
FULL = 0.02              # a line ending this close to the right margin runs on to the next
_FOOTNOTE = re.compile(r"^\s*(?:\(\d+\)|\d+\)|\*|___FOOTNOTES___)")


def norm(s):
    """Letters only, lower case, no accents: the text layer runs words
    together and garbles letters, so compare like this."""
    s = unicodedata.normalize("NFKD", re.sub(r"<[^>]+>", "", s or "").lower())
    return re.sub(r"[^a-z]", "", s)


def page_lines(page):
    """The text layer's printed lines, top to bottom: [(x0, y, text, x1)].
    Pieces on one baseline ("L'arme" + "Pour pratiquer ...") are one line."""
    import fitz
    out = []
    # text only: the default also decodes the page image -- a full-page
    # scan on every page, and seconds a page
    for b in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
        for ln in b.get("lines", []):
            t = "".join(s["text"] for s in ln["spans"]).strip()
            if t:
                x0, y0, x1, y1 = ln["bbox"]
                out.append([x0, y0, y1, t, x1])
    out.sort(key=lambda r: (r[1], r[0]))
    merged = []
    for x0, y0, y1, t, x1 in out:
        # one printed line: the centres agree to within half the smaller
        # height -- a taller bold run-in title ("Lignes. —", Kirchhoffer
        # pdf 22) must not swallow the line above, and a descender («: par
        # conséquent» beside «fer», Joseph-Renaud pdf 281) must not split one
        if merged and abs((y0 + y1) / 2 - (merged[-1][1] + merged[-1][2]) / 2) < min(
                y1 - y0, merged[-1][2] - merged[-1][1]) / 2:
            m = merged[-1]
            parts = sorted([(m[0], m[3]), (x0, t)])
            merged[-1] = [parts[0][0], m[1], max(m[2], y1), " ".join(p[1] for p in parts), max(m[4], x1)]
        else:
            merged.append([x0, y0, y1, t, x1])
    return [(x0, y, t, x1) for x0, y, _, t, x1 in merged]


def _body(lines, rows=None):
    """The printed lines of the text: from the first line that shares a run
    of letters with the OCR's first rows (`rows`), so a running head, a page
    number or a stamp above it is left out (Prévost-Jollivet's «THÉORIE
    PRATIQUE DE L'ESCRIME.» is long enough to pass for text). Without rows,
    or no such line among the first few: from the first line of 20+
    letters. Lines with no letters (a number in the margin, «11» beside
    Kirchhoffer pdf 22) are never text."""
    lines = [l for l in lines if len(norm(l[2])) >= 2]
    # the text column: scraps of the facing page in the gutter («x», «ulle-»,
    # Jacob-André) and notes in the margin lie wholly outside it
    long_ = sorted(l[0] for l in lines if len(norm(l[2])) >= 20)
    if long_:
        left = long_[len(long_) // 4]
        right = sorted(l[3] for l in lines if len(norm(l[2])) >= 20)[-1]
        lines = [l for l in lines if l[3] > left + 0.02 * (right - left) and l[0] < right - 0.02 * (right - left)]
    keys = [norm(r) for r in (rows or [])[:2] if len(norm(r)) >= 12]
    for k, l in enumerate(lines[:20]):     # lines above the first row went to the page before
        t = norm(l[2])
        letters = [c for c in l[2] if c.isalpha()]
        if sum(c.isupper() for c in letters) > 0.7 * len(letters):
            continue                        # a running head in capitals, never the text («CONTRE LE BRAS TENDU»)
        if len(t) >= 12 and any(difflib.SequenceMatcher(None, t, key, autojunk=False)
                                .find_longest_match(0, len(t), 0, len(key)).size >= 12 for key in keys):
            return lines[k:]
    k = 0
    while k < len(lines) and len(norm(lines[k][2])) < 20:
        k += 1
    return lines[k:]


def _indent(off, same):
    """The page's paragraph indent (a page fraction): the commonest offset
    among the indented lines, the smaller on a tie; a drop capital's lines
    count once. None when no line is indented."""
    ins = [o for o, sm in zip(off, same) if INDENT < o <= 0.08 and not sm]
    if not ins:
        return None
    return min(ins, key=lambda o: (-sum(abs(o - p) <= 0.006 for p in ins), o))


def kinds(lines, width):
    return profile(lines, width)[0]


def profile(lines, width):
    """(kinds, places). kinds -- each line: 'start' (opens a paragraph) or 'run' (the paragraph above
    runs on into it); None for the whole page when its text layer is too
    noisy to trust (Internet Archive layers put some line starts well off
    the printed ones -- Masiello 1887 pdf 128).

    A line runs on only when the line above reaches the right margin, and
    then only from the left margin -- or, round a drop capital (set in much
    further than an indent), from the same start as the line above. A line
    at the page's indent always starts one: two in a row are two one-line
    paragraphs (Joseph-Renaud's dialogue, Masiello's «La cosa appare ...»).
    places -- each line 'margin', 'indent' (the page's paragraph indent) or
    'far' (a drop capital, a centred line); for evals/format_check.py."""
    xs = [l[0] for l in lines]
    ends = [l[3] for l in lines]
    margin = [sorted(xs[max(0, k - 5):k + 6])[min(1, len(xs[max(0, k - 5):k + 6]) - 1)] for k in range(len(xs))]
    off = [(x - m) / width for x, m in zip(xs, margin)]
    right = [max(ends[max(0, k - 8):k + 9]) for k in range(len(ends))]
    full = [e >= r - FULL * width for e, r in zip(ends, right)]
    def place(o):
        if o <= INDENT:
            return "margin"
        if ind is not None and abs(o - ind) <= 0.008:
            return "indent"
        return "far"
    same = [k > 0 and abs(xs[k] - xs[k - 1]) <= INDENT * width for k in range(len(xs))]
    ind = _indent(off, same)
    where = [place(o) for o in off]
    # far lines that aren't a drop capital's group or the page's first line:
    # misplaced starts (or a centred title) -- too many, and the layer is noise
    stray = sum(1 for k in range(1, len(xs)) if where[k] == "far" and not same[k]
                and not (k + 1 < len(xs) and same[k + 1]))
    if stray > 0.1 * len(xs):
        return None, where
    out = []
    for k in range(len(xs)):
        if k == 0:
            out.append("run" if where[0] == "margin" else "start")   # the page above is out of sight
        elif not full[k - 1]:
            out.append("start")
        elif where[k] == "far" and where[k - 1] == "far" and same[k]:
            out.append("run")                                # round a drop capital
        elif where[k] == "margin" and ind is not None:
            # from the margin: only on a page that indents its paragraphs --
            # a flush glossary's entries (Gomard pdf 110) say nothing
            out.append("run")
        else:
            out.append("start")
    return out, where


def _find(key, lines, start):
    """Index of the printed line that begins with `key` (normalized), from
    `start` on; None if no line does."""
    best, at = 0.0, None
    for k in range(start, len(lines)):
        head = norm(lines[k][2])[:len(key)]
        r = difflib.SequenceMatcher(None, key, head).ratio()
        if r > best:
            best, at = r, k
    return at if best >= 0.8 else None


def hints(rows, lines, width, prev_lines=None):
    """rows: the page's OCR body rows (the source, non-blank); lines: the text
    layer (page_lines). Returns (opens_mid_paragraph, joins): the first a
    bool (None: no text layer), `joins` one bool per row -- True: run this row on to the
    row above. Pure, for the tests."""
    body = _body(lines, rows)
    if len(body) < 2:
        return None, [False] * len(rows)
    kind = kinds(body, width)
    if kind is None:
        return None, [False] * len(rows)
    opens = kind[0] == "run"
    if prev_lines and rows:
        # the first row may begin at the foot of the page before, moved here
        # whole by the cross-page joining: if a line there starts with it,
        # that line says whether it opened a paragraph (Prévost-Jollivet pdf
        # 178: «Après la parade de tierce» is indented there). A fragment
        # carried over mid-line («Il faudra se mé-», Kirchhoffer pdf 14)
        # starts no line, and the page's own first line decides.
        pb = _body(prev_lines)
        pk = kinds(pb, width) if len(pb) >= 2 else None
        key = norm(rows[0])[:25]
        if pk and len(key) >= 15:
            for k in range(max(0, len(pb) - 8), len(pb)):
                head = norm(pb[k][2])[:len(key)]
                if difflib.SequenceMatcher(None, key, head).ratio() >= 0.9:
                    opens = pk[k] == "run"
                    break
    joins, at = [False] * len(rows), 0
    for r, row in enumerate(rows):
        key = norm(row)[:15]
        if len(key) < 8:
            continue
        k = _find(key, body, at)
        if k is None:
            continue
        at = k + 1
        if r > 0 and kind[k] == "run":
            joins[r] = True
    return opens, joins


def spacing(rows, lines):
    """Per row: True when the book leaves extra space above its line (the
    gap from the line above is well over the page's usual line pitch),
    False when it doesn't, None when the row's line isn't found or has no
    line above on the page. Pure, for the tests."""
    body = _body(lines, rows)
    ys = [l[1] for l in body]
    pitch = [None] + [ys[k] - ys[k - 1] for k in range(1, len(ys))]
    out, at = [None] * len(rows), 0
    for r, row in enumerate(rows):
        key = norm(row)[:15]
        if len(key) < 8:
            continue
        k = _find(key, body, at)
        if k is None:
            continue
        at = k + 1
        near = sorted(p for p in pitch[max(1, k - 6):k + 7] if p and p > 0)
        if k == 0 or len(near) < 3:
            continue
        normal = near[len(near) // 2]
        if pitch[k] > 1.3 * normal:
            out[r] = True
        elif pitch[k] < 1.12 * normal:
            out[r] = False
    return out


def row_places(rows, lines, width):
    """Per row: 'indent' when the book starts it at the page's paragraph
    indent, 'margin' when it starts a paragraph flush at the margin, None
    otherwise (runs on, centred, not found, no usable layer). Pure."""
    body = _body(lines, rows)
    out = [None] * len(rows)
    if len(body) < 2:
        return out
    kind, where = profile(body, width)
    if kind is None:
        return out
    at = 0
    for r, row in enumerate(rows):
        key = norm(row)[:15]
        if len(key) < 8:
            continue
        k = _find(key, body, at)
        if k is None:
            continue
        at = k + 1
        if kind[k] == "start" and where[k] in ("indent", "margin") and (k or where[k] == "indent"):
            out[r] = where[k]
    return out


def _pitch(body):
    """The page's usual line pitch."""
    p = sorted(body[k][1] - body[k - 1][1] for k in range(1, len(body)) if body[k][1] > body[k - 1][1])
    return p[len(p) // 2] if p else 20


def _rows(text):
    return [r for r in (text or "").split("\n") if r.strip()]


def _source_rows(src_body):
    rows = _rows(src_body)
    for k, r in enumerate(rows):                   # the author's notes at the foot aren't body
        if k and _FOOTNOTE.match(r):
            return rows[:k]
    return rows


# a row starting like this is a new item even when the book prints it flush
# with the margin: dialogue dashes and quotes (Jacob-André pdf 144), numbered
# and lettered items (Gomard «9°», «1°»), a short run-in title -- with a dash
# («Pression. —») or a glossary head («Rompre la mesure. C'est ...»,
# Prévost-Jollivet pdf 318) -- and margin notes
_ITEM = re.compile(r"\s*(?:<[^>]+>)*\s*(?:[—–\-•*«“\"]|\d+\s*[°.)]|[a-zA-Z]\)|[IVXLC]+\.\s"
                   r"|[^\W\d][^.!?—]{0,40}?[.:]\s*[—–]\s|(?:\S+\s){0,4}\S+\.\s+[A-ZÀ-Ý]|⟦)")


# a run-in section title opening the row ("L'épée. — ", "The clothing of the
# fencer. — "): _render._RUN_IN, which keeps a blank line before it
_RUN_IN = re.compile(r"[^\W\d][^.!?—]{0,40}?[.:]\s*[—–]\s")


def _prose(row, least=30):
    """Running text, not a heading: a flush-left title the text layer can't
    tell from a running-on line must never swallow the paragraph below."""
    plain = re.sub(r"<[^>]+>", "", row).strip()
    return len(plain) >= least and not plain.isupper() and not plain.endswith(":")


def apply(english, src_body, page, prev_page=None, ornaments=()):
    """The page's English body with the text layer's paragraph starts
    applied (see the module note); unchanged when there is nothing to go
    on. `src_body`: the OCR BODY_TEXT of the same page; `page`: its scan
    page (PyMuPDF), or None; `prev_page`: the scan page before it, if any;
    `ornaments`: heights (0-1) of the small marks the layout detector found
    between paragraphs (_layout.ornaments) -- a divider the OCR missed goes
    back in before the paragraph that follows it."""
    if page is None or not (english or "").strip():
        return english
    if "<table" in (src_body or "") or "\x00TABLE" in english or "[[TABLE]]" in english:
        return english                               # a table's rows aren't paragraphs
    try:
        lines = page_lines(page)
    except Exception:                                # noqa: BLE001 -- no usable layer
        return english
    if len(lines) < 4:
        return english
    src = _source_rows(src_body)
    try:
        prev_lines = page_lines(prev_page) if prev_page is not None else None
    except Exception:                                # noqa: BLE001
        prev_lines = None
    opens, joins = hints(src, lines, page.rect.width, prev_lines)
    rows = english.split("\n")
    filled = [k for k, r in enumerate(rows) if r.strip()]
    if len(filled) == len(src):                      # row k of the English is row k of the source
        for r in range(len(src) - 1, 0, -1):
            a, b = filled[r - 1], filled[r]
            if (joins[r] and b == a + 1 and _prose(rows[a], 60) and _prose(rows[b])
                    and not _ITEM.match(rows[b]) and not _ITEM.match(src[r]) and "⟦" not in rows[a]
                    and not rows[b].lstrip().startswith("<i>")       # an italic head: a glossary entry (Gomard pdf 110)
                    and not rows[a].rstrip().endswith(("-", "‐"))):  # a word split at the line end: left as it was
                rows[a] = rows[a].rstrip() + " " + rows[b].strip()
                rows[b] = None
        # no space before a run-in title where the print has none: the OCR's
        # blank lines there come and go (Kirchhoffer pdf 13). Never added: a
        # line missing from the layer looks like a gap (pdf 38-40)
        space = spacing(src, lines)
        for r in range(1, len(src)):
            a, b = filled[r - 1], filled[r]
            if (rows[b] is not None and space[r] is False
                    and _RUN_IN.match(re.sub(r"<[^>]+>", "", rows[b]).strip())):
                for k in range(a + 1, b):
                    rows[k] = None                       # the blank lines in between
        # a divider the OCR never read, where the detector saw one between
        # two paragraphs and no line of text runs through it
        body = _body(lines, src) if ornaments else []
        for orn in ornaments or ():
            y = orn * page.rect.height
            pitch = _pitch(body)
            # text, not an ornament, if any line of the layer runs through it
            # («ET», «ou», «IV.°», a lettered label -- format review 2026-10-22)
            if any(l[1] - 2 <= y <= l[1] + 0.8 * pitch and re.search(r"\w", l[2]) for l in lines):
                continue
            below = [k for k, l in enumerate(body) if l[1] > y]
            if not below or below[0] == 0:
                continue
            if body[below[0]][1] - body[below[0] - 1][1] < 1.6 * pitch:
                continue                                   # no gap in the text: not between paragraphs
            places = profile(body, page.rect.width)[1]
            if places[below[0]] == "far":
                continue                                   # a centred title follows: a chapter number («IV.°», Masiello)
            k0, at, target = below[0], 0, None
            for r, row in enumerate(src):
                key = norm(row)[:15]
                if len(key) < 8:
                    continue
                k = _find(key, body, at)
                if k is None:
                    continue
                at = k + 1
                if k == k0:
                    target = r
                    break
            if target is None or rows[filled[target]] is None:
                continue
            prev = re.sub(r"<[^>]+>", "", rows[filled[target - 1]] or "").strip() if target else ""
            if prev and len(prev.split()) <= 8 and not re.search(r"[.!?:;»”…)]$", prev):
                continue                                   # right after a title: a chapter number, not a divider
            near = [rows[n] for n in range(max(0, filled[target] - 3), min(len(rows), filled[target] + 1)) if rows[n]]
            if any(DIVIDER in x or re.match(r"\s*[★☆⁂✶✱*]", x) for x in near):
                continue
            rows[filled[target]] = "\n" + DIVIDER + "\n\n" + rows[filled[target]]
        # where each row starts in print: short lines the layout would set
        # flush (a drill's commands, the first of a numbered run, a dialogue
        # dash -- Jacob-André pdf 112) get the indent the book gives them,
        # and an OCR indent the book doesn't have goes (Joseph-Renaud «2°»)
        place = row_places(src, lines, page.rect.width)
        for r in range(len(src)):
            b = filled[r]
            if rows[b] is None or not place[r] or re.search(r"[{}|⟦]|\x00TABLE", rows[b]):
                continue
            mark = INDENTED if place[r] == "indent" else FLUSH
            rows[b] = re.sub(r"^(\n*)", lambda m: m.group(1) + mark, rows[b], count=1)
        english = "\n".join(r for r in rows if r is not None)
    if opens:
        english = CONT + english.lstrip("\n")
    return english
