# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Letter case in step 2's English: a heading, caption or kept term takes
the book's case (reviewer rule: case matches the book), and an all-capitals
reply is brought back to the source's case."""

import re

from _tx_common import FN_HEADER


def match_word_case(term: str, sample: str) -> str:
    """`term` in the case of the text it replaces: all capitals in a capitals
    line (a kept term in Prévost's "DE LA RIPOSTE" title), a capital first
    letter where the replaced text starts with one, else as given.
    capitalize() lowers the rest of `term`."""
    return term.upper() if sample.isupper() \
        else term.capitalize() if sample[:1].isupper() else term


def caps_kept_terms(text: str, src: str = "") -> str:
    """A kept term (<i>épée</i>) inside a line set in capitals takes capitals
    too: "OF THE <i>épée</i>." -> "OF THE <i>ÉPÉE</i>." (20 cases over 8
    books). Rule: its neighbouring words are capitals (missing neighbour =
    line edge), at least one exists; or it stands alone on its line and the
    French has it in capitals (Kirchhoffer's "ÉPÉE" section title). A
    sentence-case caption keeps it lowercase ("(An <i>épée</i> bout.)")."""
    def _is_caps(w):
        letters = [c for c in w if c.isalpha()]
        return len(letters) >= 2 and all(c.isupper() for c in letters)

    def _line(line):
        parts = re.split(r"(<i>[^<]*</i>)", line)
        for k, part in enumerate(parts):
            m = re.fullmatch(r"<i>([^<]*)</i>", part)
            if not m or m.group(1).isupper() or not any(c.isalpha() for c in m.group(1)):
                continue
            before = re.findall(r"[^\W\d_][\w'’-]*", re.sub(r"<[^>]+>", " ", "".join(parts[:k])))
            after = re.findall(r"[^\W\d_][\w'’-]*", re.sub(r"<[^>]+>", " ", "".join(parts[k + 1:])))
            prev, nxt = (before[-1] if before else None), (after[0] if after else None)
            alone = prev is None and nxt is None
            if alone:
                # a title line of its own: copy the case of the same word
                # standing alone in the French ("Épée", Kirchhoffer scan 41)
                same = [l.strip(" .") for l in (src or "").split("\n")
                        if l.strip(" .").lower() == m.group(1).lower()]
                if same:
                    parts[k] = f"<i>{match_word_case(m.group(1), same[0])}</i>"
                continue
            if all(_is_caps(w) for w in (prev, nxt) if w is not None):
                parts[k] = f"<i>{m.group(1).upper()}</i>"
        return "".join(parts)
    return "\n".join(_line(l) for l in text.split("\n"))


# Before a word: the start of a sentence (or of the line), so its capital is
# grammar, not the book's choice -- opening quote, bracket or dash allowed.
_SENT_START = re.compile(r"(?:^|[.!?…])[\s«“\"(\[—–-]*$")


def _case_class(w: str):
    letters = [c for c in w if c.isalpha()]
    if not letters:
        return None
    if all(c.islower() for c in letters):
        return "lower"
    if len(letters) >= 2 and all(c.isupper() for c in letters):
        return "upper"
    if letters[0].isupper() and all(c.islower() for c in letters[1:2]):
        return "cap"
    return None


def _apply_case(w: str, cls: str) -> str:
    if cls == "upper":
        return w.upper()
    if cls == "lower":
        return w[:1].lower() + w[1:]
    return w[:1].upper() + w[1:]


def source_case_kept_terms(text: str, src: str = "") -> str:
    """A kept term takes the letter case the book gives it (reviewer rule,
    2026-10-18: case matches the book, no exceptions). Kirchhoffer scan 74
    prints «(Fleuret — Épée — Sabre.)»; the model wrote "(Foil — <i>épée</i>
    — Saber.)". Only the book's mid-sentence uses count -- a capital at a
    sentence start is grammar -- and a term at the start of an English
    sentence is left as it is, as is one in capitals (caps_kept_terms).
    Paragraph by paragraph when the English has the book's paragraphs,
    else the whole page; the book's uses must agree on one case, or there
    must be as many in the English paragraph as in the French one (then
    in order). A word in a capitals heading takes the line's case, so it
    doesn't count."""
    if not src or "<i>" not in text:
        return text
    plain_src = re.sub(r"<[^>]+>", "", src)
    lines = text.split("\n")
    en_paras = [n for n, l in enumerate(lines) if l.strip() and l.strip() != FN_HEADER]
    src_paras = [l for l in plain_src.split("\n") if l.strip()]
    aligned = len(en_paras) == len(src_paras)
    if aligned:
        groups = [([n], [s]) for n, s in zip(en_paras, src_paras)]
    else:
        groups = [(en_paras, src_paras)]

    def _caps_line(line):
        letters = [c for c in line if c.isalpha()]
        return len(letters) >= 4 and sum(c.isupper() for c in letters) > 0.7 * len(letters)

    def _mids(term, src_lines):
        out = []
        for line in src_lines:
            if _caps_line(line):           # a heading in capitals: the line's case, not the word's
                continue
            for m in re.finditer(r"(?<![\w’'-])" + re.escape(term) + r"(?![\w-])", line, re.I):
                if not _SENT_START.search(line[:m.start()]):
                    out.append(_case_class(m.group(0)))
        return out

    edits = {}
    for en_lines, src_lines in groups:
        uses = {}
        for n in en_lines:
            line = lines[n]
            for m in re.finditer(r"<i>([^<]+)</i>", line):
                term = m.group(1)
                if _case_class(term) in (None, "upper"):
                    continue
                before = re.sub(r"<[^>]+>", "", line[:m.start()])
                if not _SENT_START.search(before):
                    uses.setdefault(term.lower(), []).append((n, m.start(), term))
                elif (aligned and _case_class(term) == "lower" and not before.strip(" «“\"(")
                      and re.match(r"[\s«“\"(]*" + re.escape(term) + r"(?![\w-])",
                                   src_lines[0], re.I)
                      and _case_class(re.match(r"[\s«“\"(]*(\S+)", src_lines[0]).group(1)) != "lower"):
                    # opening its line, as the book's line opens with it capitalised
                    # (Gomard scan 113's word list: "<i>épée</i>." for «Épée.»)
                    edits[(n, m.start())] = (term, _apply_case(term, "cap"))
        for key, found in uses.items():
            mids = _mids(found[0][2], src_lines)
            if not mids or None in mids:
                continue
            if len(set(mids)) == 1:
                want = mids * len(found)
            elif len(mids) == len(found) and aligned:
                want = mids
            else:
                continue
            for (n, at, term), cls in zip(found, want):
                if _case_class(term) != cls:
                    edits[(n, at)] = (term, _apply_case(term, cls))
    for (n, at), (old, new) in sorted(edits.items(), reverse=True):
        line = lines[n]
        lines[n] = line[:at] + f"<i>{new}</i>" + line[at + len(old) + 7:]
    return _heading_case_kept_terms("\n".join(lines), plain_src)


_PICTURE_TAG = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _short_line(line: str, most: int = 9) -> bool:
    words = re.sub(r"<[^>]+>", "", line).split()
    return 0 < len(words) <= most


def _heading_case_kept_terms(text: str, src: str) -> str:
    """A kept term in a short line -- a heading, a title, a contents line --
    takes its case from the book's own line, line by line: the k-th short
    English line using the term goes with the k-th short line of the book
    using it, when they come in equal numbers (Barbasetti pdf 74: «Parata di
    Terza» -> "Parry of <i>terza</i>", left as it was by the page-wide count,
    which the picture tag's «![Parata di Terza](…)» and a lower-case «terza»
    in the text made ambiguous). Not the term opening either line or
    following a sentence start (a capital there is grammar or the heading's
    own: Masiello «§ 151. — Rimessa semplice.»), not a line in capitals, and
    the two lines of like length (Edom scan 254: a sentence is no heading)."""
    body = text.split(FN_HEADER)[0]
    lines = text.split("\n")
    head_n = len(body.split("\n"))
    src_lines = [l for l in (_PICTURE_TAG.sub("", l) for l in src.split("\n"))
                 if _short_line(l) and not re.match(r"\s*(PAGE_NUMBER|HEADER|BODY_TEXT)\s*:", l)]

    def _caps(line):
        letters = [c for c in line if c.isalpha()]
        return len(letters) >= 4 and sum(c.isupper() for c in letters) > 0.7 * len(letters)

    en_lines = [n for n in range(head_n) if "<i>" in lines[n] and _short_line(lines[n])]
    edits = {}
    for n in en_lines:
        for m in re.finditer(r"<i>([^<]+)</i>", lines[n]):
            term = m.group(1)
            if _case_class(term) not in ("lower", "cap"):
                continue
            before = re.sub(r"<[^>]+>", "", lines[n][:m.start()])
            if _SENT_START.search(before):
                continue
            pat = r"(?<![\w-])" + re.escape(term) + r"(?![\w-])"     # after an elision too: «l’Épée»
            mine = [k for k in en_lines if re.search(r"<i>" + re.escape(term) + r"</i>", lines[k], re.I)]
            theirs = [l for l in src_lines if re.search(pat, l, re.I)]
            if len(mine) != len(theirs):
                continue
            s = theirs[mine.index(n)]
            words_en, words_src = len(re.sub(r"<[^>]+>", "", lines[n]).split()), len(s.split())
            if _caps(s) or not 0.5 <= words_en / words_src <= 2:
                continue
            here = [x for x in re.finditer(r"<i>([^<]+)</i>", lines[n]) if x.group(1).lower() == term.lower()]
            hits = list(re.finditer(pat, s, re.I))
            if len(here) != len(hits):
                continue
            hit = hits[[x.start() for x in here].index(m.start())]      # the k-th use with the k-th
            if _SENT_START.search(s[:hit.start()]):
                continue
            want = _case_class(hit.group(0))
            if want in ("lower", "cap") and want != _case_class(term):
                edits[(n, m.start())] = (term, _apply_case(term, want))
    for (n, at), (old, new) in sorted(edits.items(), reverse=True):
        line = lines[n]
        lines[n] = line[:at] + f"<i>{new}</i>" + line[at + len(old) + 7:]
    return "\n".join(lines)


_MARKUP = re.compile(r"(</?(?:i|b|sup|fn)>)", re.I)   # not a stray "<" (Gomard scan 245)


def _mostly_caps(line: str, share: float = 0.85) -> bool:
    letters = [c for c in _MARKUP.sub("", line) if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) > share * len(letters)


def source_caps_lines(text: str, src: str = "") -> str:
    """A short line the book sets in capitals is in capitals in the English
    too (reviewer rule, 2026-10-18: case matches the book). The model often
    sets them in title or sentence case: "PREMIÈRE PARTIE." -> "First Part.",
    "ALLIEVO." -> "Student.", "RIPOSTES A TEMPS PERDU PAR COUPS SIMPLES." ->
    lower case (Gomard scan 245). Only when the English has the book's
    paragraphs one for one and the two lines are of a like length (Florio
    scan 81 has as many paragraphs, shifted). A word the book prints in
    small letters on that line keeps its case ("PAR LE Dr. W. HAMSTRONG")."""
    if not src:
        return text
    lines = text.split("\n")
    body_end = next((n for n, l in enumerate(lines) if l.strip() == FN_HEADER), len(lines))
    en_paras = [n for n in range(body_end) if lines[n].strip()]
    src_paras = [l for l in re.sub(r"<[^>]+>", "", src).split("\n")
                 if l.strip() and not re.match(r"\s*\(\d+\)", l)]
    if len(en_paras) != len(src_paras):
        return text
    for n, s_line in zip(en_paras, src_paras):
        line = lines[n]
        sw, ew = len(s_line.split()), len(_MARKUP.sub("", line).split())
        if (not _mostly_caps(s_line) or sw > 15 or ew > 2 * sw + 3 or "<fn>" in line
                or re.fullmatch(r"\s*\[[^\]]*\]\s*", line) or _mostly_caps(line, 0.5)):
            continue
        keep = {w for w in re.findall(r"[^\W\d_][\w.'’-]*", s_line) if not w.isupper()}
        lines[n] = "".join(
            part if _MARKUP.fullmatch(part) else
            re.sub(r"[^\W\d_][\w.'’-]*", lambda m: m.group(0) if m.group(0) in keep
                   else m.group(0).upper(), part)
            for part in _MARKUP.split(line))
    return "\n".join(lines)


_TITLE_STOPWORDS = {
    "a", "an", "the", "and", "or", "nor", "but", "of", "to", "in", "on", "at",
    "by", "for", "with", "from", "as", "per", "via",
    # French particles, so "Parade de Sixte." still reads as Title Case
    "de", "du", "des", "la", "le", "les", "à", "au", "aux", "et", "ou",
    "d", "l", "en", "sur", "par", "dans",
}


def match_case(src: str, dst: str) -> str:
    """Make a translated heading / caption follow the CASE STYLE of its
    source line, faithfully -- a book that sets a running head in capitals
    gets an all-caps English head, a sentence-case caption gets a
    sentence-case English caption (even if the model shouted it).
      source ALL CAPS   -> dst.upper()  (incl. kept <i> terms)
      source Title Case -> Title Case (particles stay lower)
      anything else     -> sentence case: first letter up, the rest down
                           (acronyms present in the source are preserved)
    Words the model kept in the source language (<i>..</i>) keep their own
    case, EXCEPT under ALL CAPS where the whole line goes up together."""
    if not dst:
        return dst

    # A caption can combine a main title with a trailing parenthetical
    # annotation set in a DIFFERENT case register (Kirchhoffer: "LE
    # CROISÉ AU FLANC." all-caps, then "(La main de l'attaquant reste
    # haute.)" in ordinary sentence case -- confirmed against the real
    # scan). Measuring case over the whole combined string dilutes an
    # all-caps main title's upper_ratio below the 0.8 threshold, so the
    # real all-caps title fell through to a broken middle case instead.
    # Split off a trailing "(...)" and case-match each side separately,
    # only when BOTH src and dst end in one -- otherwise fall through to
    # the single-mode behaviour below unchanged.
    _PAREN_TAIL = re.compile(r"^(.*?)([ \t]*\([^()]*\))\s*$", re.S)
    sm = _PAREN_TAIL.match(src)
    dm = _PAREN_TAIL.match(dst)
    if sm and dm and sm.group(1).strip() and dm.group(1).strip():
        main = match_case(sm.group(1), dm.group(1))
        paren = match_case(sm.group(2).strip(), dm.group(2).strip())
        return f"{main.rstrip()} {paren}"

    # Same combined-case-register problem, different separator: a main
    # title followed by a trailing "-- <clause>" annotation ALSO set in
    # ordinary lower case in print (Kirchhoffer "PRISE DE FER SUR LE
    # BRAS TENDU. -- 1er TEMPS : feinte de liement de seconde." -- the
    # title is genuinely all-caps, the "1er TEMPS : ..." clause genuinely
    # lower case, confirmed by the same pattern repeating identically
    # across 3 plates in one run). Only split at the LAST em-dash when
    # the trailing segment actually STARTS lower-case (letter or digit,
    # for ordinals like "1er"/"3rd") -- a caption that only uses "--" to
    # separate two SAME-case segments ("FIG. 4. -- LE CROISÉ AU FLANC.",
    # both all-caps) must stay whole. Recurses, so a caption with more
    # than one such trailing clause peels them off one at a time.
    #
    # *2026-09-20:* "1er" doesn't have as natural a digit-form as "2e"/
    # "3e" (no bare "1st" the model reaches for as readily), so on this
    # exact caption's FIRST plate the model spelled it "FIRST TEMPO"
    # instead of "1st Tempo" -- still visually part of the caps run, so
    # the tail no longer starts lower-case/digit and the split silently
    # stopped firing (Fig. 6/7 in the SAME plate run use "2nd"/"3rd" and
    # split fine; only Fig. 5's "1er" hit this). Also accept a spelled-
    # out ordinal word as a tail-start, case-insensitively -- it's the
    # same "this is really the lower-case-register clause" signal as a
    # bare digit, just spelled differently.
    _ORDINAL_WORD = r"(?i:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b"
    _DASH_TAIL = re.compile(
        rf"^(.*\S)[ \t]*[—-][ \t]+([a-zà-ÿ0-9].*|{_ORDINAL_WORD}.*)$", re.S)
    sm2 = _DASH_TAIL.match(src)
    dm2 = _DASH_TAIL.match(dst)
    if sm2 and dm2:
        main = match_case(sm2.group(1), dm2.group(1))
        tail = match_case(sm2.group(2), dm2.group(2))
        return f"{main.rstrip()} — {tail}"

    letters = [c for c in src if c.isalpha()]
    if not letters:
        return dst
    words = [w for w in re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", src)
             if any(c.isalpha() for c in w)]
    upper_ratio = sum(c.isupper() for c in letters) / len(letters)
    src_acronyms = {w.upper() for w in words if w.isupper() and len(w) >= 2}

    if upper_ratio >= 0.8 and len(letters) >= 2:
        # uppercase everything, then restore the lowercase markup tags
        return re.sub(r"</?(?:i|fn)>", lambda m: m.group(0).lower(),
                      dst.upper(), flags=re.I)

    sig = [w for w in words if w.lower() not in _TITLE_STOPWORDS]
    if sig and all(w[0].isupper() for w in sig):
        def _title(m, first=[True]):
            w = m.group(0)
            lead = first[0]
            first[0] = False
            if len(w) == 1:
                return w.upper()                       # figure label ("Schéma A")
            if w.upper() in src_acronyms:
                return w.upper()
            if not lead and w.lower() in _TITLE_STOPWORDS:
                return w.lower()
            return w[:1].upper() + w[1:].lower()
        return _recase(dst, lambda s, first: re.sub(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", _title, s))

    # sentence case: lower the model's over-capitalisation, keep source
    # acronyms, then upper the very first letter.
    def _sent(s, first=True):
        s = re.sub(r"[^\W\d_]+",
                   lambda m: m.group(0) if (m.group(0).upper() in src_acronyms
                                            and len(m.group(0)) >= 2)
                   else m.group(0).lower(), s)
        # Capitalise the first GENUINE word-initial letter, not just the
        # first letter found -- a clause starting with an ordinal ("3rd
        # tempo...", "3e TEMPS...") has its first letter mid-token ("r"
        # in "3rd"), and force-capitalising that produced a broken "3Rd".
        if not first:
            return s
        return re.sub(r"(?<![\dA-Za-zÀ-ÿ])[A-Za-zÀ-ÿ]",
                      lambda m: m.group(0).upper(), s, count=1)
    return _recase(dst, _sent)


def _recase(text: str, fn) -> str:
    """Apply fn to the parts of `text` that are NOT inside <i>...</i>
    (kept-in-source terms keep their own casing). fn(part, first): `first`
    only for the part at the very start -- sentence case capitalises that
    one alone, not the word after every italic term ("the <i>riposte</i>
    Is hit", Baudry Fig. 7)."""
    out, i, k = [], 0, 0
    for m in re.finditer(r"<i>.*?</i>", text, re.DOTALL | re.IGNORECASE):
        out.append(fn(text[i:m.start()], k == 0 and i == 0))
        out.append(m.group(0))
        i = m.end()
        k += 1
    out.append(fn(text[i:], i == 0))
    return "".join(out)


def match_dash_caps(fr: str, en: str) -> str:
    """A caption's segments after a dash start with a capital in the French
    ("Fig. 2. — Garde classique vue de face.") but the model lowercases them
    ("Fig. 2. — classical guard ..."; Baudry, Gomard plates). Capitalise the
    first letter after each dash in the English when the French segment at
    the same position starts with a capital."""
    split = r"\s*[—–]\s*"
    fr_parts = re.split(split, fr or "")
    en_parts = re.split(r"(\s*[—–]\s*)", en or "")
    if len(fr_parts) < 2 or len(en_parts) != 2 * len(fr_parts) - 1:
        return en
    for k in range(1, len(fr_parts)):
        f = fr_parts[k].lstrip()
        if not (f[:1].isalpha() and f[:1].isupper()):
            continue
        seg = en_parts[2 * k]
        m = re.match(r"(\s*(?:<i>)?)([a-zà-ÿ])", seg)
        if m:
            en_parts[2 * k] = m.group(1) + m.group(2).upper() + seg[m.end():]
    return "".join(en_parts)


def _shout_ratio(text: str) -> float:
    """Fraction of letters that are uppercase. Only meaningful with enough
    letters to average over -- returns 0 for short strings (a plate caption
    or a heading is legitimately all-caps)."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 25:
        return 0.0
    return sum(c.isupper() for c in letters) / len(letters)


def deshout(text: str, source: str) -> str:
    """Qwen sometimes latches onto a source paragraph that opens with a
    small-caps word ('L'ESCRIME au fleuret ...') and renders the WHOLE page
    in capitals. Last-resort repair: lowercase a shouting line, re-capitalise
    sentence starts, and keep any token that is genuinely all-caps in the
    source line (acronyms, a section title)."""
    keep = {w for w in re.findall(r"\b[A-ZÀ-Þ]{2,}\b", source)}
    out = []
    for line in text.split("\n"):
        if _shout_ratio(line) <= 0.85:
            out.append(line)
            continue
        lowered = re.sub(r"[A-Za-zÀ-ÿ]+",
                         lambda m: m.group(0) if m.group(0) in keep
                         else m.group(0).capitalize() if m.group(0).istitle()
                         else m.group(0).lower(),
                         line)
        lowered = re.sub(r"(^\s*[\"«(]?\s*[a-zà-ÿ])",
                         lambda m: m.group(1).upper(), lowered)
        lowered = re.sub(r"([.!?]\s+[\"«(]?)([a-zà-ÿ])",
                         lambda m: m.group(1) + m.group(2).upper(), lowered)
        lowered = re.sub(r"\bi\b", "I", lowered)
        out.append(lowered)
    return "\n".join(out)
