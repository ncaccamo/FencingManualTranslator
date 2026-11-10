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
    if not src:
        return text
    plain_src = re.sub(r"<[^>]+>", "", src)
    if "<i>" not in text:
        return _heading_case_kept_terms(text, plain_src)
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

# words that stay lower case in a Title Case line, English and the book's
# own particles ("Palais des Arts", "Lumières dans la Nuit")
_TITLE_LOWER = {
    "a", "an", "the", "and", "or", "nor", "but", "of", "to", "in", "on", "at", "by",
    "for", "with", "from", "as", "per", "via", "into", "onto", "upon", "within",
    "over", "under", "about", "against", "between", "than", "after", "before",
    "de", "du", "des", "la", "le", "les", "à", "au", "aux", "et", "ou", "en", "sur",
    "par", "dans", "di", "da", "del", "della", "dei", "degli", "delle", "il", "lo",
    "gli", "e", "o", "con", "per", "nel", "nella", "sul", "sulla", "al", "alla",
}


def _fold(s: str) -> str:
    """Letters without their accents, one for one (positions kept):
    «Epée» and "épée" are the same word."""
    import unicodedata
    return "".join((unicodedata.normalize("NFD", c)[0] if c.isalpha() else c) for c in s)


def _short_line(line: str, most: int = 9) -> bool:
    words = re.sub(r"<[^>]+>", "", line).split()
    return 0 < len(words) <= most


def _caps_ln(line: str) -> bool:
    letters = [c for c in re.sub(r"<[^>]+>", "", line) if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) > 0.7 * len(letters)


def _short_lines(text: str, src: str):
    """The English body's short lines (line numbers) and the book's short
    lines: picture tags, field names, margin notes, bracketed notes and
    lines in capitals left out."""
    lines = text.split("\n")
    head_n = len(text.split(FN_HEADER)[0].split("\n"))
    src_lines = [l for l in (_PICTURE_TAG.sub("", l) for l in src.split("\n"))
                 if _short_line(l) and not _caps_ln(l) and "⟦" not in l
                 and not re.match(r"\s*(PAGE_NUMBER|HEADER|BODY_TEXT)\s*:", l)]
    en_n = [n for n in range(head_n) if _short_line(lines[n]) and not _caps_ln(lines[n])
            and "⟦" not in lines[n] and not lines[n].strip().startswith("[")]
    return lines, en_n, src_lines


def _heading_case_kept_terms(text: str, src: str) -> str:
    """A word of a short line -- a heading, a title, a contents line -- that
    the English prints as the book does (a kept term, «quarte», «Duel»)
    takes the book's case, line by line: the k-th short English line using
    it goes with the k-th short line of the book using it, when they come
    in equal numbers, and its k-th use there with the k-th (Barbasetti
    pdf 74: «Parata di Terza» -> "Parry of <i>terza</i>", left as it was by
    the page-wide count, which the picture tag's «![Parata di Terza](…)»
    and a lower-case «terza» in the text made ambiguous; Lamarche «Contre
    de Quarte» -> "Counter in quarte"). Accents aside («Epée» = "épée"),
    after an elision too («l’Épée»). Not the word opening either line or
    following a sentence start (a capital there is grammar or the
    heading's own: Masiello «§ 151. — Rimessa semplice.»), not a line in
    capitals, and the two lines of like length (Edom scan 254: a sentence
    is no heading)."""
    lines, en_n, src_lines = _short_lines(text, src)
    tok = re.compile(r"<i>([^<]+)</i>|<[^>]+>|(?<![\w’'])([^\W\d_][\w-]*)")
    plain = lambda l: re.sub(r"<[^>]+>", "", l)
    edits = {}
    for n in en_n:
        for m in tok.finditer(lines[n]):
            term = m.group(1) or m.group(2)
            if not term or _case_class(term) not in ("lower", "cap"):
                continue
            if m.group(2) and (term.lower() in _TITLE_LOWER or term.lower() in _FALSE_FRIENDS or len(term) < 3):
                continue
            at = m.start(1) if m.group(1) else m.start(2)
            if _SENT_START.search(plain(lines[n][:at])):
                continue
            pat = re.compile(r"(?<![\w-])" + re.escape(_fold(term)) + r"(?![\w-])", re.I)
            mine = [k for k in en_n if pat.search(_fold(plain(lines[k])))]
            theirs = [l for l in src_lines if pat.search(_fold(l))]
            if len(mine) != len(theirs) or n not in mine:
                continue
            s = theirs[mine.index(n)]
            words_en, words_src = len(plain(lines[n]).split()), len(s.split())
            if not 0.5 <= words_en / words_src <= 2:
                continue
            here = [x for x in tok.finditer(lines[n]) if (x.group(1) or x.group(2) or "").lower() == term.lower()]
            hits = list(pat.finditer(_fold(s)))
            if len(here) != len(hits):
                continue
            hit = hits[[x.start() for x in here].index(m.start())]      # the k-th use with the k-th
            if _SENT_START.search(s[:hit.start()]):
                continue
            want = _case_class(s[hit.start():hit.end()])
            if m.group(2) and want != "cap":
                continue               # a plain word is only raised: English capitals («provençal» -> "Provençal") stay
            if want in ("lower", "cap") and want != _case_class(term):
                edits[(n, at)] = (term, _apply_case(term, want))
    for (n, at), (old, new) in sorted(edits.items(), reverse=True):
        lines[n] = lines[n][:at] + new + lines[n][at + len(old):]
    return _raise_plain_words(_heading_title_case("\n".join(lines), src), src)


# the book's word and the English one spelt alike but not the same word
# (Italian «Come» = how, «Sole» = only, French «Fine» = thin ...)
_FALSE_FRIENDS = {"come", "sole", "all", "alle", "pure", "note", "fine", "case", "mode", "base",
                  "dice", "male", "more", "once", "son", "tire", "sale", "dove", "data", "per"}


def _raise_plain_words(text: str, src: str) -> str:
    """A word the English prints as the book does («Quarte», «Art», «Duel»)
    takes the book's capital where every mid-sentence use of it on the
    book's page has one (reviewer rule, 2026-11-10: case matches the print;
    Danet and Saint-Martin set the guards «Quarte», «Tierce» ... always so).
    Only raised -- English capitals («Provençal», «Rue») stay; not a word
    at a sentence start, an elided one («All'entrare»), a false friend, or
    in a line in capitals or a margin note."""
    src_lines = [_PICTURE_TAG.sub("", l) for l in src.split("\n")
                 if not _caps_ln(l) and "⟦" not in l
                 and not re.match(r"\s*(PAGE_NUMBER|HEADER|BODY_TEXT)\s*:", l)]
    folded = [_fold(l) for l in src_lines]
    memo = {}

    def book_case(w):
        k = _fold(w).lower()
        if k not in memo:
            seen = set()
            for l in folded:
                for m in re.finditer(r"(?<![\w-])" + re.escape(k) + r"(?![\w’'-])", l, re.I):
                    if not _SENT_START.search(l[:m.start()]):
                        seen.add(_case_class(l[m.start():m.end()]))
            memo[k] = seen.pop() if len(seen) == 1 else None
        return memo[k]

    tok = re.compile(r"<i>[^<]+</i>|<[^>]+>|(?<![\w’'])([^\W\d_][\w-]*)")
    out = []
    for line in text.split("\n"):
        if "⟦" in line or _caps_ln(line) or line.strip() == FN_HEADER:
            out.append(line)
            continue

        def up(m):
            w = m.group(1)
            if (not w or _case_class(w) != "lower" or len(w) < 3 or w in _TITLE_LOWER
                    or w in _FALSE_FRIENDS):
                return m.group(0)
            if _SENT_START.search(re.sub(r"<[^>]+>", "", line[:m.start()])):
                return w
            return w[:1].upper() + w[1:] if book_case(w) == "cap" else w
        out.append(tok.sub(up, line))
    return "\n".join(out)


def _heading_title_case(text: str, src: str) -> str:
    """A short line the book sets in Title Case is in Title Case in the
    English (Barbasetti «Quarta di Cambiamento», Danet «Reprise sur le
    Dégagement.»; reviewer rule: case matches the book). Only when the
    book's short lines and the English ones come in equal numbers (paired
    in order), the line is one phrase (no second sentence: Lamarche
    «Fatiguez-le. Énervez-le.»), has no word in capitals (Masiello «PMN e
    PBB»), its page number is the English one's (a contents line), every
    word but the particles is capitalised, and one of those capitals is on
    a word the English translates -- a capital the English could only
    copy (a verse's first word, a name: Blengini «Lacera Emanuel.») is no
    sign of Title Case. Prepositions and the book's particles stay lower."""
    lines, en_n, src_lines = _short_lines(text, src)
    if not en_n or len(en_n) != len(src_lines):
        return text
    word = re.compile(r"(?<![\w’'])[^\W\d_][\w’'-]*")     # not "st" of "1st"
    plain = lambda l: re.sub(r"<[^>]+>", "", l)

    def content(l):
        out = []
        for m in word.finditer(l):
            w = re.split(r"['’]", m.group(0))[-1] or m.group(0)
            if w.lower() not in _TITLE_LOWER and len(w) >= 3:
                out.append(w)
        return out

    for n, s in zip(en_n, src_lines):
        en = plain(lines[n])
        if (re.search(r"[.!?;:]\s+\S", s.strip().rstrip(".")) and not re.search(r"\.{3,}", s)) \
                or re.search(r"\b[A-Z]{2,}\b", s):
            continue
        if re.findall(r"\d+", s) != re.findall(r"\d+", en):
            continue
        if not 0.5 <= len(en.split()) / len(s.split()) <= 2:
            continue
        cw = content(s)
        if len(cw) < 2 or not all(w[0].isupper() for w in cw):
            continue
        en_words = {_fold(w).lower() for w in word.findall(en)}
        if not any(_fold(w).lower() not in en_words for w in cw[1:]):
            continue                       # every capital after the first is a word the English copies

        def up(m):
            w = m.group(0)
            if (w.lower() in _TITLE_LOWER or not w[:1].islower() or len(w) < 2
                    or re.match(r"(?i)(d|l|dall|dell|nell|sull|all|qu)['’]", w)):
                return w              # a particle, a list letter «b)», an elision «d'Oro»
            return w[:1].upper() + w[1:]
        lines[n] = "".join(p if p.startswith("<") else word.sub(up, p)
                           for p in re.split(r"(<[^>]+>)", lines[n]))
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
