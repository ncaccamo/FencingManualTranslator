# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Step 1: page numbers across the book -- roman numerals, the scan's text
layer, the second margin-only read (apply_folio_witness) and the book-wide
reconciliation (reconcile_page_numbers): a number is shown only when the
sequence corroborates it."""

import re
import fitz

from _ocr_parse import has_page_number_field


def _roman_to_int(s: str):
    s = s.strip().upper()
    if len(s) > 1 and s.endswith("J"):          # old style "xij", "viiij" (La Boëssière)
        s = s[:-1] + "I"
    if not s or not re.fullmatch(r'[IVXLCDM]+', s):
        return None
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = prev = 0
    for ch in reversed(s):
        v = vals[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def _int_to_roman(n: int) -> str:
    out = ""
    for v, r in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                 (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + r, n - v
    return out


def _romanize_runs(out: list) -> list:
    """Front matter printed in roman numerals comes back from the OCR half
    normalised to arabic ("1, iv, 5, VI, 7, VIII" -- Jacob scans 13-48,
    Spinnewyn 14-29). The values are right; show the whole numbering run
    in roman. A run ends where the numbering restarts (xxxvi -> 2); it is
    roman when >= 2 of its folios, and >= 30% of them, were read as roman.
    Case follows the majority of those readings."""
    vals = [None if (not x or x.startswith("[")) else _page_to_int(x) for x in out]
    runs, cur, last = [], [], None
    for i, v in enumerate(vals):
        if v is None:
            continue
        if last is not None and v <= last:
            runs.append(cur)
            cur = []
        cur.append(i)
        last = v
    runs.append(cur)
    out = list(out)
    for run in runs:
        rom = [i for i in run if re.fullmatch(r"[ivxlcdmIVXLCDM]+[jJ]?", out[i])]
        if len(rom) >= 2 and len(rom) >= 0.3 * len(run):
            upper = sum(out[i].isupper() for i in rom) * 2 >= len(rom)
            for i in run:
                r = _int_to_roman(vals[i])
                out[i] = r if upper else r.lower()
    return out


def apply_folio_witness(nums: list, strips: dict) -> list:
    """Show a hidden page number ("[117]": worked out from the neighbours,
    not confirmed by the OCR) when a second, separate reading -- the OCR
    model on the page's margins alone (read_folios.py, stored in
    intermediate/folio_strips.json) -- names exactly that number. Never
    hides or changes a number: a reading that disagrees, or "NONE", leaves
    the page as it was (the strip read alone is wrong too often to decide:
    11 of 16 right on the test pages, 2026-10-01; with the margin crops of
    2026-10-09 it still names chapter numbers, dates and shelf marks --
    evals/folio_choice_test.py). Roman readings are shown
    in roman, in the case of the neighbouring page numbers."""
    out = list(nums)

    def confirmed(k):
        x = nums[k] if 0 <= k < len(nums) else ""
        return None if not x or x.startswith("[") else _page_to_int(x)

    for i, n in enumerate(nums):
        if n.startswith("[") and n.endswith("]"):
            want = _page_to_int(n.strip("[]"))
        elif not n.strip():
            # no number at all (Vauresmont scan 129, printed 117): the
            # confirmed numbers on BOTH sides, within 3 pages, must form one
            # consistent run -- that run predicts this page's number
            prev = next(((k, confirmed(k)) for k in range(i - 1, i - 4, -1) if confirmed(k)), None)
            nxt = next(((k, confirmed(k)) for k in range(i + 1, i + 4) if confirmed(k)), None)
            if not (prev and nxt and nxt[1] - prev[1] == nxt[0] - prev[0]):
                continue
            want = prev[1] + (i - prev[0])
        else:
            continue
        # a reading is one string (the old full-width strip) or a list of
        # the top and bottom margins read separately (read_folios.py since
        # 2026-10-09): either margin naming exactly the number confirms it
        got = strips.get(str(i + 1), "")
        reads = [re.sub(r"[^0-9A-Za-z]", "", str(g)) for g in (got if isinstance(got, list) else [got])]
        reads = [r for r in reads if r and r.upper() != "NONE"]
        if not want or not reads:
            continue
        if any(r.isdigit() and int(r) == want for r in reads):
            out[i] = str(want)
            continue
        if any(not r.isdigit() and _roman_to_int(r) == want for r in reads):
            near = next((x for k in range(1, 6) for x in (nums[i - k] if i - k >= 0 else "",
                                                          nums[i + k] if i + k < len(nums) else "")
                         if re.fullmatch(r"[ivxlcdmIVXLCDM]+", x or "")), "")
            r = _int_to_roman(want)
            out[i] = r if near.isupper() else r.lower()
    return out


def _page_to_int(s: str):
    """Best-effort numeric value of a reported page-number string, accepting
    plain digits ('83'), digits embedded in noise, or a roman numeral
    ('VIII')."""
    if not s:
        return None
    m = re.search(r'\d+', s)
    if m:
        return int(m.group())
    return _roman_to_int(s)


def text_layer_folio_bands(pdf_path: str) -> list:
    """Per page, the scan's own text layer in the top 12% and bottom 10% --
    where a printed page number sits. [] when unreadable. Used only to
    confirm a number the OCR skipped (reconcile_page_numbers `printed`)."""
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return []
    out = []
    for pg in doc:
        r = pg.rect
        out.append(pg.get_text(clip=fitz.Rect(0, 0, r.width, 0.12 * r.height)) + " "
                   + pg.get_text(clip=fitz.Rect(0, 0.9 * r.height, r.width, r.height)))
    return out


def _printed_here(band: str, value: int, shown: str) -> bool:
    """The text layer shows the number -- exactly, or damaged print read one
    digit off (Gomard scan 276 prints "260": OCR "209", text layer "200")."""
    if not band or value is None:
        return False
    band = re.sub(r"(?<=\d)[.,](?=\d)", "", band)         # "2.38" (Prévost scan 302)
    forms = {str(value), shown.strip(".").lower()}
    if any(re.search(r"(?<![\w])" + re.escape(f) + r"(?![\w])", band, re.I) for f in forms if f):
        return True
    return any(_plausible_misread(int(m), value) for m in re.findall(r"(?<![\w])\d{2,4}(?![\w])", band))


def _plausible_misread(read: int, value: int) -> bool:
    """The OCR misread a printed folio: same number of digits (>= 2), one
    digit wrong ("108" for 198). Small readings are what the OCR invents on
    unnumbered pages (1, 10, 11, 12 ...), so a reading under 20 never counts."""
    if read is None or value is None or read < 20:
        return False
    a, b = str(read), str(value)
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


def strip_fieldless_folio(body: str, raw: str) -> str:
    """A page the detector is sure prints no running head keeps its body
    as the OCR gave it -- except the page number, when the reply had no
    fields at all and the folio came out as the body's first line (Bruno
    scans 30, 196, 236; Lambertini 34, 90, 118: always the page's own
    number, 2026-10-05). Arabic only: a roman "III." there is a section
    number (Marchionni scan 9, Florio 217); and not a year -- "1898" heads
    LaMarche's dedication (scan 14)."""
    if has_page_number_field(raw):
        return body
    lines = body.lstrip("\n").split("\n")
    top = lines[0].strip() if lines else ""
    if re.fullmatch(r"\d{1,4}", top) and not re.fullmatch(r"1[5-9]\d\d", top):
        return "\n".join(lines[1:]).lstrip("\n")
    return body


def reconcile_page_numbers(page_raws: list, is_plate: list = None,
                           bodies: list = None, printed: list = None,
                           answered: list = None) -> list:
    """olmOCR-2 reliably reads a real printed folio, but it will also invent
    a plausible-looking number from a stray mark -- an engraver's plate
    number inside an illustration, a library accession stamp -- or silently
    convert 'VIII' to '8'.

    A reported folio is TRUSTED when:
      * a neighbouring sheet's folio continues the printed sequence
        (i-1 -> v-1, or i+1 -> v+1), directly or across a short unread gap;
      * the VLM's own read equals what interpolation from the nearest anchor
        predicts (fixed-point, so a whole run rides off one anchor); or
      * MONOTONIC-CHAIN: two folio reads separated only by unnumbered leaves
        (blanks / unflagged plates) where the folio rises by 1..(sheet gap)
        -- this handles "text p.N, plate leaves, text p.N+1" books where
        sheet != folio so the strict-sequence check can't fire.

    Everything else is interpolated from the nearest trusted anchor and
    bracketed '[n]'; with no anchor at all, the plain sheet ordinal.

    `is_plate[i]` (a whole-page illustration -- no printed folio) lets the
    tight-gap fill skip plate leaves when checking the arithmetic. Never
    emits <= 0.

    SHOWN only if printed (operator rule, 2026-09-28: "if the page does not
    say a page, I don't want one"): a number is left unbracketed only when
    the OCR read it, or misread it plausibly (_plausible_misread), or the
    scan's text layer shows it (`printed`, text_layer_folio_bands), or the
    OCR never answered the question for that page (`answered[i]` False: a
    malformed reply with no PAGE_NUMBER field -- unknown, so the sequence
    value is shown as before). Any other page keeps its value bracketed --
    used for sequencing, not shown.
    The OCR invents small numbers on unnumbered pages (chapter openers,
    plates), and those used to be "corrected" into shown folios.
    """
    ints = [_page_to_int(p) for p in page_raws]
    n = len(ints)
    plate = [bool(is_plate[i]) if is_plate and i < len(is_plate) else False
             for i in range(n)]
    # A page with no text and no figure has no printed folio to read: the
    # OCR invents one ("PAGE_NUMBER: 10" / "11" on Spinnewyn's blank scans
    # 8-9, which then agreed with each other and were trusted). Drop the
    # reading; the page can still be numbered from its neighbours. (On the
    # reviewed books every such page already had no reading.)
    if bodies:
        for i in range(n):
            b = (bodies[i] or "").strip() if i < len(bodies) else ""
            if not plate[i] and (not b or b.lower() == "none"):
                ints[i] = None

    trusted = [False] * n
    for i, v in enumerate(ints):
        # A plate leaf's own "read" is its engraver's plate number
        # (Gomard: "Planche XVII" -> 17), not a printed folio -- never
        # trust it for itself, even when it happens to land where the
        # surrounding sequence's arithmetic would predict a real folio.
        # `plate[i]` already keeps it from breaking OTHER pages' gap
        # checks; this closes the matching gap where the plate leaf's own
        # bogus number could get trusted directly.
        if v is None or v <= 0 or plate[i]:
            continue
        for d in (1, 2, 3):
            lo_ok = (i - d >= 0 and ints[i - d] == v - d
                     and all(ints[k] is None for k in range(i - d + 1, i)))
            hi_ok = (i + d < n and ints[i + d] == v + d
                     and all(ints[k] is None for k in range(i + 1, i + d)))
            if lo_ok or hi_ok:
                trusted[i] = True
                break

    # Monotonic-chain: consecutive folio reads with only unnumbered leaves
    # between them, folio rising by at least 1 and at most the sheet gap.
    # Neither endpoint may itself be a plate leaf, for the same reason.
    reads = [i for i in range(n) if ints[i] and ints[i] > 0 and not plate[i]]

    def _chain_ok(a, b):
        return (all(ints[k] is None or plate[k] or k in reads for k in range(a + 1, b))
                and 1 <= ints[b] - ints[a] <= (b - a))

    # A read that the reads on EITHER side of it bracket consistently
    # without it -- but which itself falls outside that bracket -- is an
    # invented folio, not a chain link (Kirchhoffer src idx 16: "8*" plate,
    # unflagged plate, an unnumbered inset plate the VLM read as "10",
    # plate, then a real "9" -- 8 -> 10 across one leaf looked like a
    # normal rise, but 8 -> 9 around it says the 10 is bogus).
    outlier = set()
    for p_, x, q in zip(reads, reads[1:], reads[2:]):
        if _chain_ok(p_, q) and (ints[x] > ints[q] or ints[x] < ints[p_]):
            outlier.add(x)
    for a, b in zip(reads, reads[1:]):
        if a in outlier or b in outlier:
            continue
        if (all(ints[k] is None or plate[k] for k in range(a + 1, b))
                and 1 <= ints[b] - ints[a] <= (b - a)):
            trusted[a] = trusted[b] = True

    anchors = [i for i in range(n) if trusted[i]]
    if not anchors:
        return [f"[{i + 1}]" for i in range(n)]

    changed = True
    while changed:
        changed = False
        for i in range(n):
            if trusted[i] or ints[i] is None or ints[i] <= 0 or plate[i]:
                continue
            j = min(anchors, key=lambda a: abs(a - i))
            if ints[i] == ints[j] + (i - j):
                trusted[i] = True
                anchors = sorted(anchors + [i])
                changed = True

    # Mutually-corroborating misreads: two adjacent pages that misread the
    # same digit the same way (Gomard 298/299 -> "208/209", Prévost 198/199
    # in old-style figures -> "108/109") differ by exactly 1, so each
    # vouches for the other above. The pages OUTSIDE the run expose them:
    # the nearest trusted pages on either side agree with each other across
    # the gap exactly (allowing for plate leaves), and the run doesn't fit
    # that line. Untrust such a run (<= 3 pages); the tight-gap promotion
    # below then fills in the forced folios. A genuine change of numbering
    # (roman front matter -> arabic 1) can't trigger this: its outer pages
    # don't agree across the gap.
    # A folio can't repeat: when two pages that are trusted in sequence
    # carry the SAME number, the later one is a leaf the VLM gave its
    # neighbour's number (Kirchhoffer scan 65: an unflagged plate read "38"
    # right after the real p. 38 -- the monotonic chain had paired it with
    # the p. 39 two sheets on). Untrusted, it falls to the unnumbered-leaf
    # handling below.
    # Unless the two pages carry the same text: a digitisation that scanned
    # one leaf twice (Jacob scans 201-202, both p. 155) is faithfully two
    # sheets showing the same folio.
    def _same_text(a, b):
        if not bodies:
            return False
        norm = lambda t: re.sub(r"\W+", "", (t or "").lower())[:300]
        return bool(norm(bodies[a])) and norm(bodies[a]) == norm(bodies[b])

    _prev = None
    for i in range(n):
        if trusted[i]:
            if _prev is not None and ints[i] == ints[_prev] and not _same_text(i, _prev):
                trusted[i] = False
                continue
            _prev = i

    _MAX_RUN = 3
    tr_idx = [i for i in range(n) if trusted[i]]
    k = 0
    while k < len(tr_idx):
        # a maximal run of consecutive trusted pages continuing one sequence
        e = k
        while (e + 1 < len(tr_idx) and tr_idx[e + 1] == tr_idx[e] + 1
               and ints[tr_idx[e + 1]] == ints[tr_idx[e]] + 1):
            e += 1
        if e - k + 1 <= _MAX_RUN and k > 0 and e + 1 < len(tr_idx):
            p, q = tr_idx[k - 1], tr_idx[e + 1]
            between = [j for j in range(p + 1, q) if not plate[j]]
            if ints[q] - ints[p] == len(between) + 1:
                expect = {j: ints[p] + 1 + between.index(j) for j in between}
                run = tr_idx[k:e + 1]
                if any(ints[j] != expect.get(j) for j in run):
                    for j in run:
                        trusted[j] = False
        k = e + 1

    # Tight-gap promotion: a run of non-trusted pages bracketed by two
    # trusted folios P .. Q, where Q - P == (non-plate slots in the run) + 1,
    # is filled in exactly -- the values are forced, so present them as real
    # ("303"), not "[303]", even where the read was a misread ("903"). A run
    # with an extra UNNUMBERED plate leaf the VLM didn't flag breaks the
    # arithmetic (Q-P too small) and stays bracketed -> suppressed.
    folio = [ints[i] if trusted[i] else None for i in range(n)]
    blank = [False] * n
    i = 0
    while i < n:
        if trusted[i]:
            i += 1
            continue
        lo = i
        while i < n and not trusted[i]:
            i += 1
        hi = i - 1                                          # run is [lo..hi]
        if lo - 1 >= 0 and hi + 1 < n and trusted[lo - 1] and trusted[hi + 1]:
            P, Q = folio[lo - 1], folio[hi + 1]
            nonplate = [k for k in range(lo, hi + 1) if not plate[k]]
            if P and Q and Q - P == len(nonplate) + 1:
                cur = P
                for k in range(lo, hi + 1):
                    if plate[k]:
                        continue
                    cur += 1
                    folio[k] = cur
                    trusted[k] = True
            elif P and Q and len(nonplate) > Q - P - 1:
                # more non-plate leaves in the run than the folio gap can
                # hold -> it contains unnumbered leaves (unflagged plates,
                # blanks) whose folio can't be pinned. Interpolating from
                # the nearest anchor seesaws a fabricated, self-
                # contradicting folio onto each ([17][18][19][20][13][14]
                # [15][16]); emit blank instead.
                for k in range(lo, hi + 1):
                    blank[k] = True

    anchors = sorted(k for k in range(n) if trusted[k])
    out = []
    for i in range(n):
        if trusted[i]:
            out.append(page_raws[i]
                       if folio[i] is None or _page_to_int(page_raws[i]) == folio[i]
                       else str(folio[i]))                  # printed / forced folio
            continue
        if blank[i] or plate[i]:            # inferred plate leaf -> no folio
            out.append("")
            continue
        j = min(anchors, key=lambda a: abs(a - i))          # nearest trusted sheet
        guess = (folio[j] or 0) + (i - j)                   # 1 sheet == 1 folio
        out.append(f"[{guess}]" if guess >= 1 else f"[{i + 1}]")
    for i in range(n):
        if not out[i] or out[i].startswith("["):
            continue
        value = _page_to_int(out[i])
        read = _page_to_int(page_raws[i]) if page_raws[i] else None
        if read == value or _plausible_misread(read, value) or (
                printed and i < len(printed) and _printed_here(printed[i], value, out[i])) or (
                answered is not None and i < len(answered) and not answered[i] and read is None):
            continue
        out[i] = f"[{value}]"
    return _romanize_runs(out)
