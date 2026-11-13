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

A scan with no text layer (Masiello's foil, a photographed copy): the
layout boxes alone, one source line per box (box_positions). The table
keeps the columns' widths as printed (data-split, from the column rule on
the scan: column_rule).

Left as it is when anything is uncertain: the line counts differ, a line
inside the passage could not be found, fewer than two lines a column, or
the columns are a list of names or titles (Blengini pdf 134, Joseph-Renaud
pdf 20)."""
import html
import math
import re
from difflib import SequenceMatcher

SKIP = [""]                     # why the last page was left as it is (for dry runs)
EDGES = [None]                  # box_positions: the column boxes' (left x0, left x1, right x0, right x1)


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
    """[(y, side, text, x0, x1)]: 'L' / 'R' for a column's line, 'F' for a line
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
            out.append((y, "F", " ".join(w[4] for w in ws), x0, x1)); continue
        gaps = [(ws[k + 1][0] - ws[k][2], k) for k in range(len(ws) - 1)
                if lx1 - 0.06 <= ws[k][2] and ws[k + 1][0] <= rx0 + 0.06]
        gap, k = max(gaps, default=(0, None))
        # two labels each centred over its column on one baseline («Maestro.»
        # … «Allievo.», Masiello pdf 599): a line of up to three words, one
        # starting well inside the left column and the next well inside the
        # right one (by where words start: the text layer's word boxes can
        # run far past the printed word)
        wide_gap = [(ws[j + 1][0] - ws[j][0], j) for j in range(len(ws) - 1)
                    if len(ws) <= 3 and ws[j][0] <= gx - 0.1 and ws[j + 1][0] >= rx0 + 0.05]
        if wide_gap:
            gap, k = max(wide_gap)
            out.append((y, "L", " ".join(w[4] for w in ws[:k + 1]), x0, ws[k][2]))
            out.append((y, "R", " ".join(w[4] for w in ws[k + 1:]), ws[k + 1][0], x1))
            continue
        # two columns' lines on one baseline -- the left one starts at the
        # left column's edge (a centred heading «IV. ESEMPIO» does not)
        if k is not None and gap >= 0.02 and x0 <= lx0 + 0.08:
            out.append((y, "L", " ".join(w[4] for w in ws[:k + 1]), x0, ws[k][2]))
            out.append((y, "R", " ".join(w[4] for w in ws[k + 1:]), ws[k + 1][0], x1))
        elif x0 >= rx0 - 0.04:
            out.append((y, "R", " ".join(w[4] for w in ws), x0, x1))
        elif x1 <= gx + 0.01:
            out.append((y, "L", " ".join(w[4] for w in ws), x0, x1))
        else:                      # across the gutter: a heading, a full-width line
            out.append((y, "F", " ".join(w[4] for w in ws), x0, x1))
    return sorted(out)


def place(src_lines, vis, y0, y1):
    """Each source line's (y, side) on the scan, or None. An exact start
    wins over a near one («I. TEMPO» is not «II. TEMPO»); a label the OCR
    gave twice (as a margin note and as a line) takes the same place."""
    starts = []
    for k, (y, s, t, *_) in enumerate(vis):
        nxt = [u for (yy, ss, u, *_) in vis[k + 1:] if ss == s][:2]
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
        if bi is None and len(key) >= 12:
            # one line spaced out across both columns («Si eseguisce … in due
            # tempi:», Masiello pdf 599): a left piece and a right piece on
            # the same baseline, read together
            for k in free:
                y, s, t = starts[k][0], starts[k][1], starts[k][2]
                if s != "L":
                    continue
                rk = next((j for j in free if j != k and starts[j][1] == "R" and abs(starts[j][0] - y) < 0.003), None)
                if rk is not None and (t + " " + starts[rk][2]).startswith(key):
                    bi = k
                    used[rk] = True
                    break
        if bi is None and len(key) < 12 and key in keys:
            out.append(keys[key] + ("dup",)); continue      # the same label again
        if bi is None:
            out.append(None); continue
        used[bi] = True
        keys.setdefault(key, starts[bi][:2])
        out.append(starts[bi][:2])
    return out


def build(en_lines, pos, g, y0, y1, one_side_short=False, split=None):
    """The band of `en_lines` the book sets in two columns, rebuilt in the
    scan's order: each heading across the page once, the column lines
    between headings as a table, rows as printed (same baseline = same
    row), the left column `split` of the width as printed. Returns
    (first, last, block) -- the band's first and last line
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
    nl, nr = sum(pos[k][1] == "L" for k in cols), sum(pos[k][1] == "R" for k in cols)
    # a table run on from the page before opens the page: its last row may
    # have one line on a side (Masiello pdf 600: «Parerà quinta.» beside the
    # pupil's two lines)
    cont = bool(cols) and cols[0] == 0 and min(nl, nr) >= 1 and max(nl, nr) >= 2
    if (nl < 2 or nr < 2) and not cont:
        SKIP[0] = "under 2 lines a side"; return None
    # both columns hold sentences (actions, hits and parries), not a list of
    # names or titles (Blengini pdf 134's masters, Joseph-Renaud's novels)
    cells = [en_lines[k] for k in cols if len(_norm(en_lines[k]).split()) >= 3]
    titled = lambda t: all(w[:1].isupper() or not w[:1].isalpha() for w in re.sub(r"<[^>]+>", "", t).split() if len(w) > 3)
    if not cells or sum(map(titled, cells)) >= 0.5 * len(cells):
        SKIP[0] = "a list of titles"; return None
    longest = [max((len(_norm(en_lines[k]).split()) for k in cols if pos[k][1] == side), default=0) for side in "LR"]
    # (with no text layer only one side need have them: the foil's master's
    # column is short commands, «Parerà terza.»)
    if (max(longest) if one_side_short else min(longest)) < 4:
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
    # an intro ending ":" with nothing beside it («Si eseguisce in due
    # tempi:», which the scan spaces across the columns) is set above the
    # table, so a label row («MAESTRO.» | «ALLIEVO.») after it heads the
    # table (Masiello pdf 599)
    for n, (y, o, side, t) in enumerate(items):
        if side in "LR" and re.sub(r"<[^>]+>", "", t).rstrip().endswith(":") and not any(
                m != n and s2 in "LR" and abs(y2 - y) < 0.012 for m, (y2, _, s2, _) in enumerate(items)):
            items[n] = (y, 0, "F", t)
    block, rows = [], []
    def flush():
        if rows:
            head = rows[0]["cells"]
            th = all(len(_norm(v)) <= 12 for v in head.values()) and len(head) == 2 and len(rows) > 1
            body = "".join(("<tr>" + "".join(f"<th>{r['cells'].get(c, '')}</th>" for c in "LR") + "</tr>") if (th and n == 0)
                           else ("<tr>" + "".join(f"<td>{r['cells'].get(c, '')}</td>" for c in "LR") + "</tr>")
                           for n, r in enumerate(rows))
            tag = f'<table data-split="{split:.2f}">' if split else "<table>"
            block.append("\x00TABLE" + tag + body + "</table>")
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


def _dedup(bs):
    """The layout boxes once each: the detector gives most boxes two or three
    times (text and section_header, a second pass), and pieces of a heading
    inside the heading's box."""
    bs = sorted(bs, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    out = []
    for b in bs:
        inside = lambda o: (min(b[2], o[2]) - max(b[0], o[0])) * (min(b[3], o[3]) - max(b[1], o[1])) >= 0.8 * (b[2] - b[0]) * (b[3] - b[1])
        if not any(min(b[2], o[2]) > max(b[0], o[0]) and min(b[3], o[3]) > max(b[1], o[1]) and inside(o) for o in out):
            out.append(b)
    return out

PITCH = 0.0215                  # a printed line's height on the page (fraction; the foil's)


def _rows(b):
    return max(1, round((b[3] - b[1]) / PITCH))


def box_positions(sl, boxes, g):
    """Each source line's (y, side) from the layout boxes alone, or None --
    for a scan with no text layer (Masiello's foil, a photographed copy).
    One source line = one box. The OCR does not read such a page in one
    order: one column then the other, row by row, or the left half of the
    page first and then the right column (foil pdf 117). What is fixed: the
    left half (lines across the page and the left column) top to bottom; a
    right box after the left line level with it and before the next
    section's heading or left column. Every such order whose lines fit
    their boxes (length against the box's width x lines) is found; when
    more than one fits, the best (lines closest to their boxes' length) is
    kept only if clearly better on the lines where they differ -- else the
    page is left as it is."""
    gx, y0, y1, lx1, rx0, lx0 = g
    bs = _dedup([b[2] for b in boxes if b[0] in ("text", "section_header", "title", "list_item", "caption") and b[1] >= 0.3])
    F, Ls, Rs, cur = [], [], [], {"L": [], "R": []}
    def flush():
        l, r = sorted(cur["L"], key=lambda b: b[1]), sorted(cur["R"], key=lambda b: b[1])
        # a short line across the page that ends before the gutter (foil pdf 135's
        # «Si eseguisce in due tempi:») sits above the other column's first box
        while l and r and (l[0][3] < r[0][1] - 0.005 or r[0][3] < l[0][1] - 0.005):
            F.append(l.pop(0) if l[0][3] < r[0][1] - 0.005 else r.pop(0))
        if l and r:
            ry = {id(b): a[1] for a, b in zip(l, r) if len(l) == len(r) and abs(a[1] - b[1]) < 0.03}
            Ls.extend((b, len(F), b[1]) for b in l)
            Rs.extend((b, len(F), ry.get(id(b), b[1])) for b in r)
        else:
            F.extend(l + r)
        cur["L"], cur["R"] = [], []
    for b in sorted(bs, key=lambda b: b[1]):
        inband = b[3] >= y0 - 0.01 and b[1] <= y1 + 0.01
        if inband and b[2] <= rx0 + 0.01 and b[0] < gx:
            cur["L"].append(b)
        elif inband and b[0] >= lx1 - 0.01 and b[2] > gx:
            cur["R"].append(b)
        else:
            flush(); F.append(b)
    flush()
    F.sort(key=lambda b: b[1])
    n = len(F) + len(Ls) + len(Rs)
    if n != len(sl):
        SKIP[0] = f"boxes {n} != lines {len(sl)}"; return None
    if not Ls or not Rs:
        SKIP[0] = "no column boxes"; return None
    allb = F + [x[0] for x in Ls + Rs]
    D = sum(len(t) for t in sl) / sum((b[2] - b[0]) * _rows(b) for b in allb)     # characters per unit width, this page
    def fits(t, b):
        r, cap = _rows(b), D * (b[2] - b[0]) * _rows(b)
        n = len(t)          # 4 characters' play: a one-word label in small capitals (foil pdf 135's «ALLIEVO.»)
        return n <= 1.35 * cap + 4 and (n + 4 >= 0.35 * cap if r == 1 else n + 4 >= 0.75 * cap * (r - 1) / r)
    left = sorted([(b[1], "F", k, b) for k, b in enumerate(F)] + [(x[0][1], "L", k, x[0]) for k, x in enumerate(Ls)],
                  key=lambda z: z[0])
    seg = lambda x: x[1]                      # a section: the number of F lines above it
    need = [sum(1 for z in left if z[0] <= x[0][1] + 0.01) for x in Rs]
    before = [min([j for j, z in enumerate(left) if z[1] == "L" and seg(Ls[z[2]]) > seg(x)] + [len(left)]) for x in Rs]
    heads = [b[2] for b in boxes if b[0] in ("section_header", "title") and b[1] >= 0.3]
    sols, path = [], []
    def dfs(i, a, r):
        if len(sols) > 50:
            return
        if i == n:
            if tuple(path) not in sols:
                sols.append(tuple(path))
            return
        t = sl[i]
        # a section heading is not read while the right column above it is unfinished (foil pdf 81)
        head = a < len(left) and left[a][1] == "F" and bool(re.match(r"\s*§", t) or any(
            abs(h[1] - left[a][3][1]) < 0.005 and abs(h[0] - left[a][3][0]) < 0.02 for h in heads))
        if a < len(left) and fits(t, left[a][3]) and all(before[q] > a for q in range(r, len(Rs))) \
                and not (head and any(Rs[q][0][1] < left[a][3][1] for q in range(r, len(Rs)))):
            path.append((left[a][1], left[a][2])); dfs(i + 1, a + 1, r); path.pop()
        if r < len(Rs) and a >= need[r] and a <= before[r] and fits(t, Rs[r][0]):
            path.append(("R", r)); dfs(i + 1, a, r + 1); path.pop()
    dfs(0, 0, 0)
    if not sols:
        SKIP[0] = "no reading order fits"; return None
    # several orders fit: the one whose lines best match their boxes' length
    # (a box's last line is about half full), when it is clearly the best
    def box(side, k):
        return (left[[j for j, z in enumerate(left) if z[1] == side and z[2] == k][0]][3] if side != "R" else Rs[k][0])
    def err(sol, only=None):
        e = 0.0
        for j, (t, (side, k)) in enumerate(zip(sl, sol)):
            if only is not None and j not in only:
                continue
            b = box(side, k); r = _rows(b)
            e += math.log((len(t) + 4) / (D * (b[2] - b[0]) * (r - 0.4 if r > 1 else 0.85) + 4)) ** 2
        return e
    sols.sort(key=err)
    if len(sols) > 50:
        SKIP[0] = "too many reading orders fit"; return None
    for other in sols[1:]:          # on the lines where they differ
        diff = {j for j in range(n) if sols[0][j] != other[j]}
        if err(sols[0], diff) > err(other, diff) / 3:
            SKIP[0] = "two reading orders fit"; return None
    EDGES[0] = (min(x[0][0] for x in Ls), max(x[0][2] for x in Ls), min(x[0][0] for x in Rs), max(x[0][2] for x in Rs))
    out = []
    for side, k in sols[0]:
        if side == "F":
            out.append((F[k][1], "F"))
        else:
            x = (Ls if side == "L" else Rs)[k]
            out.append((x[2], side))
    return out


def column_rule(page, x0, x1, y0, y1):
    """x of the vertical rule the book prints between two columns, between
    the left column's longest line (x0) and the right column's start (x1),
    over the passage's height y0..y1 (fractions), or None. Centred columns
    start well after their rule (Barbasetti pdf 133); a master's short
    commands end well before it (Masiello sabre pdf 134)."""
    import pymupdf as fitz
    W, H = page.rect.width, page.rect.height
    if x1 - x0 < 0.005 or y1 - y0 < 0.03:
        return None
    try:
        pm = page.get_pixmap(dpi=150, colorspace=fitz.csGRAY,
                             clip=fitz.Rect(x0 * W, y0 * H, x1 * W, min(y1, 1.0) * H))
    except Exception:
        return None
    w, h, buf = pm.width, pm.height, pm.samples
    if w < 1 or h < 10:
        return None
    # dark against the page's own paper: a thin rule prints lighter than type
    # on a photographed copy (Masiello foil, sabre)
    paper = sorted(buf[r * pm.stride + c] for r in range(0, h, 3) for c in range(w))
    thr = paper[len(paper) // 2] - 35
    # band by band: a photographed page's rule leans (foil pdf 76), and it
    # breaks between two passages and prints lighter in places (Masiello
    # pdf 295); its x = the median of the bands' darkest columns, when a
    # third of the bands or more have one dark over 55% of them
    n, xs = 12, []
    for k in range(n):
        r0, r1 = k * h // n, (k + 1) * h // n
        if r1 - r0 < 3:
            continue
        dark = [sum(1 for r in range(r0, r1) if buf[r * pm.stride + c] < thr) / (r1 - r0) for c in range(w)]
        c = max(range(w), key=lambda c: dark[c])
        if dark[c] >= 0.55:
            xs.append(c)
    if len(xs) < n / 3:
        return None
    xs.sort()
    return x0 + (xs[len(xs) // 2] + 0.5) / w * (x1 - x0)


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
    sl = [l for l in (src_body or "").split("\n") if l.strip()]
    raw = body.split("\n")
    nz = [k for k, l in enumerate(raw) if l.strip()]
    el = [raw[k] for k in nz]
    ref = [l for l in (stored if stored is not None else body).split("\n") if l.strip()]
    if len(sl) != len(ref) and notes:       # the source's notes, which the English moved to its notes
        sl = [l for l in sl if not re.match(r"\s*\(\d+\)\s", l)]
    if len(sl) != len(ref):
        SKIP[0] = "line counts differ"; return body
    if has_words:
        vis = visual_lines(page, g, wide_boxes(boxes, g[0]))
        rpos = place(sl, vis, g[1], g[2])
    else:                                   # no text layer: the layout boxes alone (Masiello's foil)
        rpos = box_positions(sl, boxes, g)
        if rpos is None:
            return body
    if stored is None:
        pos = rpos
    else:                                   # each line of body: the place of the same stored line
        pos, used = [], set()
        for line in el:
            k = next((k for k, r in enumerate(ref) if k not in used and r.strip() == line.strip()), None)
            if k is not None:
                used.add(k)
            pos.append(rpos[k] if k is not None else None)
    # the columns' widths as printed (from the scan's lines in the passage, or
    # with no text layer its column boxes): the left column from where its
    # lines start to the column rule, the right one from the rule to where
    # its lines end
    ys = [p[0] for p in pos if p is not None and len(p) == 2 and p[1] in "LR"]
    edges = None
    if not has_words:
        edges = EDGES[0]
    elif ys:
        # lines of three words or more: a centred heading or label («I. ESERCIZIO.», «B.») is not an edge
        col = [v for v in vis if v[1] in "LR" and min(ys) - 0.002 <= v[0] <= max(ys) + 0.002 and len(v[2].split()) >= 3]
        if any(v[1] == "L" for v in col) and any(v[1] == "R" for v in col):
            edges = (min(v[3] for v in col if v[1] == "L"), max(v[4] for v in col if v[1] == "L"),
                     min(v[3] for v in col if v[1] == "R"), max(v[4] for v in col if v[1] == "R"))
    split = None
    if edges and ys and edges[3] > edges[0] and edges[2] > edges[1]:
        rule = column_rule(page, edges[1], edges[2], min(ys), max(ys) + 0.02)
        # no rule found (a skewed photograph's columns shift between two
        # passages: foil pdf 106): the rule is set just before the right column
        split = ((rule if rule is not None else edges[2] - 0.005) - edges[0]) / (edges[3] - edges[0])
    res = build(el, pos, *g[:3], one_side_short=not has_words, split=split if split and 0.15 <= split <= 0.85 else None)
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
