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
HANGING = "\x05"        # in front of a row: first line at the margin, the rest indented (a glossary)

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


def page_pieces(page):
    """The text layer's lines as it stores them, not merged along a
    baseline: on a page set in columns, each column's piece of a printed
    line apart (a lesson table, Masiello pdf 373). [(x0, y, text, x1)]."""
    import fitz
    out = []
    for b in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
        for ln in b.get("lines", []):
            t = "".join(s["text"] for s in ln["spans"]).strip()
            if t:
                out.append((ln["bbox"][0], ln["bbox"][1], t, ln["bbox"][2]))
    return sorted(out, key=lambda l: (l[1], l[0]))


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
    # a page set with hanging indents (Prévost-Jollivet's glossary, pdf
    # 315-317): an entry's first line at the margin, the lines after it
    # at the indent -- the other way round from a paragraph
    cont = [where[k] for k in range(1, len(xs)) if full[k - 1]]
    hanging = cont.count("indent") >= 3 and cont.count("indent") > 2 * cont.count("margin")
    # far lines that aren't a drop capital's group or the page's first line:
    # misplaced starts (or a centred title) -- too many, and the layer is noise
    stray = sum(1 for k in range(1, len(xs)) if where[k] == "far" and not same[k]
                and not (k + 1 < len(xs) and same[k + 1]))
    if stray > 0.1 * len(xs):
        return None, where
    out = []
    if hanging:
        for k in range(len(xs)):
            if where[k] == "indent" and (k == 0 or full[k - 1]):
                out.append("run")
            else:
                out.append("start")
        return out, ["hanging" if (w == "margin" and o == "start") else w for w, o in zip(where, out)]
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
        if kind[k] == "start" and where[k] in ("indent", "margin", "hanging") and (k or where[k] != "margin"):
            out[r] = where[k]
    return out


_SPEAKER = re.compile(r"^(?:<i>)?(Maestro|Master|Maître|Allievo|Student|Pupil|Élève|Professeur)(?:</i>)?\s*[:.]?\s*$", re.I)
_STEP = re.compile(r"^(?:[IVX]+|\d+)\.?\s+(?:TIME|TEMPO|TEMPS)\b", re.I)


def lesson_columns(rows, src, lines, width):
    """Lessons the book sets as tables of paragraphs -- Maestro | Allievo
    (| Allievo) side by side, each row level across (Masiello pdf 373 and
    its sword and sabre parts, where only a page's first lesson carries the
    MAESTRO / ALLIEVO heads) -- which the OCR reads one column after
    another. `rows`: the English rows, row k the translation of source row
    `src[k]`; `lines`: the text layer's pieces (page_pieces). A run of 3+
    rows whose printed pieces are narrow and fall in 2+ columns beside each
    other (overlapping in height) is a table: its columns by where the
    pieces start, its rows by their height, a speaker head as the column's
    title. Returns [(first_row, end_row, html)], possibly empty. A lesson
    with numbered steps ("I. TEMPO") is _render._pair_lesson's."""
    plain = lambda k: re.sub(r"[\x02-\x05]", "", rows[k]).strip()
    if any(_STEP.match(plain(k)) for k in range(len(rows))):
        return []
    full_w = max((l[3] - l[0] for l in lines), default=0)
    if not full_w:
        return []
    used, info = set(), {}
    for k in range(len(rows)):
        if not plain(k):
            continue
        key = norm(src[k])[:15]
        if len(key) < 5:
            continue
        best, at = 0.0, None
        for n, l in enumerate(lines):
            if n in used:
                continue
            r = difflib.SequenceMatcher(None, key, norm(l[2])[:len(key)]).ratio()
            if r > best:
                best, at = r, n
        if at is not None and best >= 0.8:
            used.add(at)
            info[k] = lines[at]
    head = lambda k: bool(_SPEAKER.match(plain(k)))
    # an intro ("It is executed in two tempi:"), a § heading or a row of a
    # table the OCR wrote with | is never a cell: it ends a run
    breaker = lambda k: bool(re.search(r":$", plain(k)) or re.match(r"§|\d+\.\s*[—–-]", plain(k)) or " | " in rows[k])
    narrow = lambda k: not breaker(k) and (head(k) or (k in info and info[k][3] - info[k][0] < 0.55 * full_w))
    found, k, n = [], 0, len(rows)
    while k < n:
        if not plain(k) or not narrow(k):
            k += 1
            continue
        run, j = [], k
        while j < n and (not plain(j) or narrow(j)):
            if plain(j):
                run.append(j)
            j += 1
        k = j
        if len(run) < 3:
            continue
        heads_run = [r for r in run if head(r)]
        run_cells = [r for r in run if not head(r)]
        if len(run_cells) < 3:
            continue
        xs = sorted({round(info[r][0]) for r in run_cells})
        bounds = [xs[0]] + [b for a_, b in zip(xs, xs[1:]) if b - a_ > 0.12 * width]
        col = lambda r: max(c for c, x in enumerate(bounds) if info[r][0] >= x - 0.03 * width)
        cols = {}
        for r in run_cells:
            cols.setdefault(col(r), []).append(r)
        if len(cols) < 2:
            continue
        ys = {c: [info[r][1] for r in rs] for c, rs in cols.items()}
        order = sorted(cols)
        if not all(min(ys[order[i + 1]]) <= max(ys[order[i]]) + 6 for i in range(len(order) - 1)):
            continue                                     # one after the other on the page, not beside
        # speaker heads title the columns, in the order they come
        heads = {c: [heads_run[i]] if i < len(heads_run) else [] for i, c in enumerate(order)}
        cells = [(order.index(c), info[r][1], r) for c, rs in cols.items() for r in rs]
        gaps = [abs(p[1] - q[1]) for p in cells for q in cells if p[0] == q[0] and p[2] != q[2]]
        tol = max(4.0, 0.5 * min(gaps)) if gaps else 12.0
        table = []
        for c, y, r in sorted(cells, key=lambda t: t[1]):
            for row in table:
                if abs(row[0] - y) <= tol and c not in row[1]:
                    row[1][c] = [r]
                    break
            else:
                table.append([y, {c: [r]}])
        # a table's rows have cells side by side -- a title page's centred
        # lines, each at its own height, never do (Masiello pdf 8)
        if sum(len(row[1]) >= 2 for row in table) * 2 < len(table):
            continue
        # a lesson's rows pair a short action with the reply in full: at least
        # two rows with both sides filled and 8+ words on the right -- not a
        # title page's lines (Masiello pdf 8), not a ditto list (Barbasetti pdf 163)
        last = len(order) - 1
        if sum(1 for row in table if 0 in row[1] and last in row[1]
               and len(" ".join(plain(r) for r in row[1][last]).split()) >= 8) < 2:
            continue
        title = ("<tr>" + "".join(f"<th>{plain(heads[c][0]) if heads[c] else ''}</th>" for c in order) + "</tr>"
                 if any(heads.values()) else "")
        body = "".join("<tr>" + "".join(f"<td>{' '.join(plain(r) for r in row[1].get(i, []))}</td>"
                                         for i in range(len(order))) + "</tr>" for row in table)
        found.append((run[0], run[-1] + 1, f"<table>{title}{body}</table>"))
        # (a head standing apart before the run's first cell is part of it)
    return found


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
        # no space where the print has none -- before a run-in title or any
        # row: the OCR's blank lines come and go (Kirchhoffer pdf 13). Never added: a
        # line missing from the layer looks like a gap (pdf 38-40)
        space = spacing(src, lines)
        for r in range(1, len(src)):
            a, b = filled[r - 1], filled[r]
            # any row, not only a run-in title (Barbasetti pdf 84, 27, 32;
            # Masiello pdf 115: blank lines the book doesn't have)
            if (rows[b] is not None and space[r] is False and DIVIDER not in rows[b]
                    and not rows[b].startswith("\x00TABLE")):
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
            if not below and len(body) >= 6 and y > body[-1][1] + 1.6 * pitch:     # (not a title page's rule, pdf 18)
                # under the page's last line: a chapter's end ornament (Masiello pdf 373)
                lastrow = max((b for b in filled if rows[b]), default=None)
                if lastrow is not None and not any(DIVIDER in (rows[n] or "") for n in range(lastrow, len(rows))):
                    rows[lastrow] = rows[lastrow] + "\n\n" + DIVIDER
                continue
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
        # a lesson set as a table of paragraphs, read column after column by
        # the OCR (Masiello pdf 373): back into its rows, level across
        cols = lesson_columns([rows[b] or "" for b in filled], src, page_pieces(page), page.rect.width)
        if cols:
            for a, z, html_ in cols:
                for k in range(filled[a], filled[z - 1] + 1):
                    rows[k] = None                       # the lesson's rows and the blank lines between them
                rows[filled[a]] = "\x00TABLE" + html_
            return CONT * bool(opens) + "\n".join(r for r in rows if r is not None)
        # where each row starts in print: short lines the layout would set
        # flush (a drill's commands, the first of a numbered run, a dialogue
        # dash -- Jacob-André pdf 112) get the indent the book gives them,
        # and an OCR indent the book doesn't have goes (Joseph-Renaud «2°»)
        place = row_places(src, lines, page.rect.width)
        for r in range(len(src)):
            b = filled[r]
            if rows[b] is None or not place[r] or re.search(r"[{}|⟦]|\x00TABLE", rows[b]):
                continue
            mark = {"indent": INDENTED, "margin": FLUSH, "hanging": HANGING}[place[r]]
            # on the row's own text -- a divider put back in front of it is
            # its own line (Joseph-Renaud pdf 141 printed "[[DIVIDER]]")
            head, _, own = rows[b].rpartition("\n")
            rows[b] = (head + "\n" if _ else "") + mark + own
        english = "\n".join(r for r in rows if r is not None)
    if opens:
        english = CONT + english.lstrip("\n")
    return english
