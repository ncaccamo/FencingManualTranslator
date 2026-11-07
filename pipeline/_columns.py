"""A passage the book sets in two parallel columns -- the master's actions
and the pupil's (Masiello), fencer A and fencer B (Barbasetti's «Esempi»),
hits and parries (Enrichetti) -- the OCR reads one column after the other,
so the English gives each fencer's lines in turn and nothing says who does
what. Here such a passage is drawn as a two-column table, in both PDFs,
its rows as printed: a line level with a line in the other column shares
its row, a line set lower starts a new one.

Where the lines are: the layout detector's text boxes give the two columns
and the gutter; the scan's text layer gives each printed line's words and
height; each source line (extracted.txt) is found on the scan by its first
words, and the English line with the same index takes its place (the
translation keeps the source's lines one for one -- checked first). Only
the drawing changes: translation.txt stays as it is.

Left as it is when anything is uncertain: no text layer, the line counts
differ, a line inside the passage could not be found, fewer than two lines
a column, or the columns are a list of names or titles (Blengini pdf 134,
Joseph-Renaud pdf 20). Dry run over the corpus 2026-11-07: Barbasetti 25,
Masiello 1887 54, Masiello 1902 (sciabola) 26, Enrichetti 1 pages."""
import html
import re
from difflib import SequenceMatcher

SKIP = [""]                     # why the last page was left as it is (for dry runs)


def _norm(s):
    s = html.unescape(re.sub(r"<[^>]+>", "", s or "")).lower()
    s = re.sub(r"[’`]", "'", s)
    return re.sub(r"[^\w']+", " ", s).strip()


def gutter(boxes):
    """(gutter x, y0, y1) of the page's two-column band, from the layout
    boxes: text boxes on each side of the middle, side by side."""
    tb = [b[2] for b in boxes if b[0] in ("text", "list_item") and b[1] >= 0.5 and b[2][2] - b[2][0] < 0.45]
    left = [b for b in tb if b[2] <= 0.62 and (b[0] + b[2]) / 2 < 0.5]
    right = [b for b in tb if b[0] >= 0.42 and (b[0] + b[2]) / 2 > 0.5]
    pairs = [(a, c) for a in left for c in right if min(a[3], c[3]) - max(a[1], c[1]) > 0.005 and c[0] > a[2] - 0.01]
    if len(pairs) < 2:
        return None
    lx1 = max(a[2] for a, _ in pairs)
    rx0 = min(c[0] for _, c in pairs)
    if rx0 <= lx1 - 0.005:
        return None
    ys = [v for a, c in pairs for v in (a[1], a[3], c[1], c[3])]
    y0, y1 = min(ys), max(ys)
    # the columns' inner edges from all their boxes in the band, not only the
    # paired ones (Barbasetti pdf 166: the pairs are the short «I. TEMPO» labels)
    inb = lambda b: b[3] >= y0 - 0.05 and b[1] <= y1 + 0.15
    # a left box never reaches the paired right column, a right box starts
    # after the left column (Barbasetti pdf 159: a box under the picture)
    lx1 = max([b[2] for b in left if inb(b) and b[2] <= rx0] + [lx1])
    rx0 = min([b[0] for b in right if inb(b) and b[0] >= lx1] + [rx0])
    lx0 = min([b[0] for b in left if inb(b)] + [min(a[0] for a, _ in pairs)])
    return ((lx1 + rx0) / 2, y0, y1, lx1, rx0, lx0)


def wide_boxes(boxes, gx=0.5):
    """Boxes across both columns: a sentence set full width, a heading over
    the gutter (Masiello's letter-spaced «§ 45. — Finta …»), a picture's
    caption under it (Barbasetti pdf 159's «Fig. 48»)."""
    return [b[2] for b in boxes if b[1] >= 0.5 and (
        (b[0] in ("text", "section_header", "title", "list_item") and b[2][2] - b[2][0] >= 0.55)
        or (b[0] in ("section_header", "title", "caption") and b[2][0] < gx < b[2][2]))]


def visual_lines(page, g, wide=(), band=None):
    """[(y, side, text)]: 'L' / 'R' for a column's line, 'F' for a line
    across the page (a heading, a full-width sentence), from the scan's
    words. `g` = gutter(): its lx1 / rx0 are the columns' inner edges. A
    line with a wide gap between the columns is two; else a line starting
    at the right column is 'R', one ending before the gutter 'L', the rest
    'F' -- and a line in a full-width layout box is 'F' whole (justified
    text has gaps as wide as the gutter: Masiello)."""
    W, H = page.rect.width, page.rect.height
    gx, lx1, rx0, lx0 = g[0], g[3], g[4], g[5]
    lines = {}
    for x0, y0, x1, y1, w, b, l, n in page.get_text("words"):
        lines.setdefault((b, l), []).append((x0 / W, y0 / H, x1 / W, y1 / H, w))
    out = []
    for ws in lines.values():
        ws.sort()
        y = min(w[1] for w in ws)
        yc = (min(w[1] for w in ws) + max(w[3] for w in ws)) / 2
        x0, x1 = ws[0][0], max(w[2] for w in ws)
        inwide = [b for b in wide if b[1] - 0.004 <= yc <= b[3] + 0.004]
        if any(b[0] - 0.01 <= x0 and x1 <= b[2] + 0.01 for b in inwide) and (x0 < gx < x1 or x0 > lx0 + 0.08):
            out.append((y, "F", " ".join(w[4] for w in ws))); continue
        gaps = [(ws[k + 1][0] - ws[k][2], k) for k in range(len(ws) - 1)
                if lx1 - 0.06 <= ws[k][2] and ws[k + 1][0] <= rx0 + 0.06]
        gap, k = max(gaps, default=(0, None))
        # two columns' lines on one baseline -- the left one starts at the
        # left column's edge (a centred heading «IV. ESEMPIO» does not)
        if k is not None and gap >= 0.02 and x0 <= lx0 + 0.08:
            out.append((y, "L", " ".join(w[4] for w in ws[:k + 1])))
            out.append((y, "R", " ".join(w[4] for w in ws[k + 1:])))
        elif x0 >= rx0 - 0.04:
            out.append((y, "R", " ".join(w[4] for w in ws)))
        elif x1 <= gx + 0.01:
            out.append((y, "L", " ".join(w[4] for w in ws)))
        else:                      # across the gutter: a heading, a full-width line
            out.append((y, "F", " ".join(w[4] for w in ws)))
    return sorted(out)


def place(src_lines, vis, y0, y1):
    """Each source line's (y, side) on the scan, or None. An exact start
    wins over a near one («I. TEMPO» is not «II. TEMPO»); a label the OCR
    gave twice (as a margin note and as a line) takes the same place."""
    starts = []
    for k, (y, s, t) in enumerate(vis):
        nxt = [u for (yy, ss, u) in vis[k + 1:] if ss == s][:2]
        starts.append((y, s, _norm(t), _norm(" ".join([t] + nxt))))
    used, out, keys = {}, [], {}
    for line in src_lines:
        key = _norm(re.sub(r"⟦MARGIN:\s*(.*?)⟧", r"\1", line))[:28]
        if not key:
            out.append(None); continue
        free = [k for k in range(len(starts)) if k not in used]
        exact = [k for k in free if (starts[k][2] == key if len(key) < 12 else starts[k][3].startswith(key))]
        if exact:
            # a short label («A.») can occur twice on a page: the one nearest
            # the line placed before it (Barbasetti pdf 129)
            last_y = next((p[0] for p in reversed(out) if p is not None), None)
            bi = min(exact, key=lambda k: abs(starts[k][0] - last_y)) if len(key) < 12 and last_y is not None else exact[0]
        else:
            bi, best = None, 0
            for k in free:
                t = starts[k][3]
                if len(key) < 12 or len(t) < len(key) * 0.6:
                    continue
                r = SequenceMatcher(None, key, t[:len(key)]).ratio()
                if r > best:
                    best, bi = r, k
            if best < 0.8:
                bi = None
        if bi is None and len(key) < 12 and key in keys:
            out.append(keys[key] + ("dup",)); continue      # the same label again
        if bi is None:
            out.append(None); continue
        used[bi] = True
        keys.setdefault(key, starts[bi][:2])
        out.append(starts[bi][:2])
    return out


def build(en_lines, pos, g, y0, y1):
    """The band of `en_lines` the book sets in two columns, rebuilt in the
    scan's order: each heading across the page once, the column lines
    between headings as a table, rows as printed (same baseline = same
    row). Returns (first, last, block) -- the band's first and last line
    and what replaces them -- or None when a line inside the band could not
    be placed, or the columns are a list (names, titles), not actions."""
    ys = [p[0] for p in pos if p is not None and p[1] in "LR" and len(p) == 2]
    if not ys:
        SKIP[0] = "no column lines"; return None
    y0, y1 = min(ys), max(ys)
    # a heading across the page just above the first column line is the band's own
    inband = lambda p: p is not None and (y0 - (0.04 if p[1] == "F" else 0.005)) <= p[0] <= y1 + 0.005
    idx = [k for k, p in enumerate(pos) if inband(p)]
    cols = [k for k in idx if pos[k][1] in "LR" and len(pos[k]) == 2]
    if sum(pos[k][1] == "L" for k in cols) < 2 or sum(pos[k][1] == "R" for k in cols) < 2:
        SKIP[0] = "under 2 lines a side"; return None
    # both columns hold sentences (actions, hits and parries), not a list of
    # names or titles (Blengini pdf 134's masters, Joseph-Renaud's novels)
    cells = [en_lines[k] for k in cols if len(_norm(en_lines[k]).split()) >= 3]
    titled = lambda t: all(w[:1].isupper() or not w[:1].isalpha() for w in re.sub(r"<[^>]+>", "", t).split() if len(w) > 3)
    if not cells or sum(map(titled, cells)) >= 0.5 * len(cells):
        SKIP[0] = "a list of titles"; return None
    if any(max((len(_norm(en_lines[k]).split()) for k in cols if pos[k][1] == side), default=0) < 4 for side in "LR"):
        SKIP[0] = "a side with only short lines"; return None              # a list with short notes beside it (Joseph-Renaud pdf 20's novels and publishers)
    first, last = idx[0], idx[-1]
    if any(pos[k] is None for k in range(last + 1, len(pos))):
        # a line after the passage not found on the scan: most likely the
        # rest of a column (Florio pdf 37's SABER column) -- not half a table
        SKIP[0] = "unplaced line after"; return None
    if any(not inband(pos[k]) for k in range(first, last + 1)):
        SKIP[0] = "unplaced line inside: " + str([en_lines[k][:30] for k in range(first, last + 1) if not inband(pos[k])][:2]); return None
    items, seen = [], set()
    for k in idx:
        if len(pos[k]) > 2:          # a label the OCR gave twice: once
            continue
        y, side = pos[k][:2]
        if side == "F":
            if (round(y, 3), _norm(en_lines[k])) in seen:
                continue                   # a heading the OCR gave once per column
            seen.add((round(y, 3), _norm(en_lines[k])))
        items.append((y, 0 if side == "F" else 1, side, re.sub(r"⟦MARGIN:\s*(.*?)⟧", r"\1", en_lines[k])))
    items.sort()
    block, rows = [], []
    def flush():
        if rows:
            head = rows[0]["cells"]
            th = all(len(_norm(v)) <= 12 for v in head.values()) and len(head) == 2 and len(rows) > 1
            body = "".join(("<tr>" + "".join(f"<th>{r['cells'].get(c, '')}</th>" for c in "LR") + "</tr>") if (th and n == 0)
                           else ("<tr>" + "".join(f"<td>{r['cells'].get(c, '')}</td>" for c in "LR") + "</tr>")
                           for n, r in enumerate(rows))
            block.append("\x00TABLE<table>" + body + "</table>")
            rows.clear()
    for y, _, side, t in items:
        if side == "F":
            flush(); block.append(t); continue
        if rows and side not in rows[-1]["cells"] and abs(y - rows[-1]["y"]) < 0.012:
            rows[-1]["cells"][side] = t
        else:
            rows.append({"y": y, "cells": {side: t}})
    flush()
    return first, last, block


def column_tables(body, src_body, page, boxes, notes=False, stored=None):
    """The page's English body (notes split off) with its two-column passage
    as tables, or `body` unchanged. `src_body`: the OCR BODY_TEXT; `page`:
    the scan page (PyMuPDF); `boxes`: its layout boxes; `notes`: the
    English has notes (the source may keep them in its body); `stored`: the
    page's English as translation.txt has it, when step 3 has changed
    `body` since (a caption line taken out, general rule 2: Barbasetti pdf
    159) -- its lines are the ones matched one for one with the source,
    and each line of `body` takes the place of the same line there."""
    SKIP[0] = ""
    if page is None or not boxes or "<table" in body or "\x00TABLE" in body or "[[TABLE]]" in body:
        return body
    g = gutter(boxes)
    if not g:
        SKIP[0] = "no band"; return body
    try:
        has_words = bool(page.get_text("words"))
    except Exception:
        has_words = False
    if not has_words:
        SKIP[0] = "no text layer"; return body
    sl = [l for l in (src_body or "").split("\n") if l.strip()]
    raw = body.split("\n")
    nz = [k for k, l in enumerate(raw) if l.strip()]
    el = [raw[k] for k in nz]
    ref = [l for l in (stored if stored is not None else body).split("\n") if l.strip()]
    if len(sl) != len(ref) and notes:       # the source's notes, which the English moved to its notes
        sl = [l for l in sl if not re.match(r"\s*\(\d+\)\s", l)]
    if len(sl) != len(ref):
        SKIP[0] = "line counts differ"; return body
    rpos = place(sl, visual_lines(page, g, wide_boxes(boxes, g[0])), g[1], g[2])
    if stored is None:
        pos = rpos
    else:                                   # each line of body: the place of the same stored line
        pos, used = [], set()
        for line in el:
            k = next((k for k, r in enumerate(ref) if k not in used and r.strip() == line.strip()), None)
            if k is not None:
                used.add(k)
            pos.append(rpos[k] if k is not None else None)
    res = build(el, pos, *g[:3])
    if res is None:
        return body
    first, last, block = res
    out = "\n".join(raw[:nz[first]] + [""] + ["\n\n".join(block)] + [""] + raw[nz[last] + 1:])
    # every line of the English is still there, once (a heading the OCR
    # repeated may go): otherwise the page is left as it is
    flat = _norm(out)
    keep = lambda l: _norm(re.sub(r"⟦MARGIN:\s*(.*?)⟧", r"\1", l))
    if any(keep(l) and keep(l) not in flat for l in el):
        SKIP[0] = "a line would be lost"; return body
    return out
