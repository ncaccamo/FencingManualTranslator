# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Checks on one translation try: left in the source language, wrong script,
a refusal or preamble, a lost negation, footnote, quotation or line name.
translate_page() retries a try that fails one; the review report reuses
some of them."""

import re

from _tx_common import FN_HEADER


FRENCH_WORDS = {
    "le", "la", "les", "un", "une", "des", "du", "de", "d", "et", "est", "sont", "était", "étaient",
    "dans", "sur", "sous", "par", "pour", "avec", "sans", "vers", "chez", "entre", "depuis",
    "au", "aux", "en", "il", "ils", "elle", "elles", "on", "je", "tu", "nous", "vous",
    "ne", "n", "pas", "plus", "point", "rien", "jamais", "mais", "ou", "où", "si", "donc",
    "que", "qu", "qui", "quoi", "dont", "quel", "quelle", "quels", "quelles",
    "ce", "cet", "cette", "ces", "cela", "ceci", "celui", "celle", "ceux",
    "son", "sa", "ses", "leur", "leurs", "mon", "ma", "mes", "ton", "ta", "tes", "notre", "votre",
    "à", "se", "s", "lui", "moi", "toi", "soi", "l", "c", "j", "m", "t",
    "être", "avoir", "fait", "faire", "faites", "était", "être", "sera", "ont", "avait", "avaient",
    "tout", "tous", "toute", "toutes", "bien", "très", "aussi", "ainsi", "alors", "comme",
    "quand", "lorsque", "puisque", "parce", "afin", "selon", "malgré", "cependant", "toutefois",
    "encore", "déjà", "toujours", "peut", "doit", "peu", "trop", "assez",
}
ITALIAN_WORDS = {"il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "del", "della", "di", "e", "è", "sono", "in", "su", "per", "con", "da", "a", "che", "questo", "questa", "quello", "quella", "mio", "tuo", "suo"}
GERMAN_WORDS = {"der", "die", "das", "ein", "eine", "und", "ist", "sind", "in", "im", "auf", "zu", "von", "mit", "den", "dem", "des", "als", "wie", "dass", "aus", "nach", "bei", "an", "vor", "hinter", "über"}
SPANISH_WORDS = {"el", "la", "los", "las", "un", "una", "y", "es", "son", "en", "de", "del", "por", "para", "con", "al", "que", "este", "esta", "ese", "esa", "mi", "tu", "su", "nuestro", "vuestro"}
LATIN_WORDS = {"et", "in", "ad", "ab", "cum", "ex", "per", "pro", "sub", "super", "non", "est", "sunt", "ut", "si", "aut", "vel", "sed", "atque", "neque", "quod", "qui", "quo", "quam", "ubi", "unde"}

NON_ENGLISH_INDICATORS = FRENCH_WORDS | ITALIAN_WORDS | GERMAN_WORDS | SPANISH_WORDS | LATIN_WORDS


def _french_token_count(line: str) -> int:
    toks = re.findall(r"\b[\wàâäéèêëïîôùûüç]+\b", line.lower())
    n = sum(1 for w in toks if w in FRENCH_WORDS)
    n += len(re.findall(r"\b[cdjlmnqst]['’][aeiouyhàâäéèêëïîôùûü]", line, re.I))
    return n


def has_wrong_script(text: str) -> bool:
    """Sources are all Latin-script (French/Italian/German/Spanish/Latin).
    CJK / Cyrillic / Hebrew / Arabic characters in the output mean the model
    glitched -- e.g. it wrote a note to itself in Chinese mid-sentence.
    (Greek is deliberately allowed: classical treatises do quote it.)
    """
    return bool(re.search(
        r"[Ѐ-ӿ֐-׿؀-ۿ　-ヿ㐀-鿿가-힯]",
        text))


# The numbered lines / parries. At temperature 0 the model can copy another
# line name from the same page in place of the right one -- "la riposte
# droite après quarte" -> "after tierce", three times on one page (Kirchhoffer
# scan 52), sixte -> septime eight times (Joseph-Renaud scans 49, 178), each
# time a name that also appears on the page; the 0.3 translations had it too
# (Joseph-Renaud scans 81, 140, 243; Gomard 190, 265; Prévost 96). A wrong
# line is a wrong action, so such a try is rejected like a too-short one.
_LINE_NAMES_FR = ("prime", "seconde", "tierce", "quarte", "quinte", "sixte", "septime", "octave")
_LINE_NAMES_IT = ("prima", "seconda", "terza", "quarta", "quinta", "sesta", "settima", "ottava")
# the English ordinal a source word may rightly become ("la seconde fois" ->
# "the second time", "prima" -> "before"): counted with the line name, so it
# isn't taken for a line that went missing
_LINE_ORDINALS = {"prime": "first", "seconde": "second", "tierce": "third", "quarte": "fourth",
                  "quinte": "fifth", "sixte": "sixth", "septime": "seventh", "octave": "eighth",
                  "prima": "first|before|earlier", "seconda": "second|according", "terza": "third",
                  "quarta": "fourth", "quinta": "fifth", "sesta": "sixth", "settima": "seventh",
                  "ottava": "eighth"}
# source spellings of the same lines: old French "quart" / "carte" / OCR
# "quarté", "septième" (Gomard scans 82, 87; Danet 300); Italian "3.a" for
# terza, and "diterza" with the space lost (Enrichetti 81, Masiello 381)
_LINE_SPELLINGS_FR = [(r"(?<!\w)(?:quart|quarté|carte)(s?)(?!\w)", r"quarte\1"),
                      (r"(?<!\w)septième(s?)(?!\w)", r"septime\1")]
_LINE_SPELLINGS_IT = [(rf"(?<![\w.]){n}\s?(?:\.?\s?[aª])(?!\w)", w)
                      for n, w in zip(range(1, 9), _LINE_NAMES_IT)] + [
                     (r"(?<!\w)di(?=(?:prima|seconda|terza|quarta|quinta|sesta|settima|ottava)(?!\w))", "di ")]


def _line_name_counts(text: str, source: bool = False, ordinals: bool = False, lang: str = "French") -> dict:
    italian = lang == "Italian"
    names = _LINE_NAMES_IT if italian else _LINE_NAMES_FR
    text = re.sub(r"</?i>", "", text).lower()
    if source:
        for pat, rep in (_LINE_SPELLINGS_IT if italian else _LINE_SPELLINGS_FR):
            text = re.sub(pat, rep, text)
    out = {}
    for w in names:
        alt = f"{w}|{_LINE_ORDINALS[w]}" if ordinals else w
        out[w] = len(re.findall(rf"(?<!\w)(?:{alt})s?(?!\w)", text))
    return out


def line_names_swapped(text: str, src: str, lang: str = "French") -> str:
    """'' if the English names the same lines as the source; otherwise what
    went wrong: a line the source never names (twice or more, so one stray
    word can't trip it), or a line the source names that is gone -- not even
    as an English ordinal -- while another line's count went up: the swap.
    "prime" / "prima" never count as extra ("prime importance")."""
    s = _line_name_counts(src, source=True, lang=lang)
    t = _line_name_counts(text, lang=lang)
    extra = [w for w in t if t[w] >= 2 and s[w] == 0 and w not in ("prime", "prima")]
    if extra:
        return "names " + ", ".join(extra) + " (not in the source)"
    t_ord = _line_name_counts(text, ordinals=True, lang=lang)
    gone = [w for w in s if s[w] >= 1 and t_ord[w] == 0]
    grew = [w for w in t if t[w] > s[w]]
    if gone and grew:
        return f"{', '.join(gone)} -> {', '.join(grew)}"
    return ""


def contains_too_much_non_english(text: str) -> bool:
    # Technical terms the prompt instructs the model to KEEP in the source
    # language are wrapped in <i>...</i> -- those are correct output, not a
    # translation failure, so drop them before measuring.
    text = re.sub(r"<i>.*?</i>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    words_cased = re.findall(r"\b\w+\b", text)
    if not words_cased:
        return True
    words = [w.lower() for w in words_cased]
    # A French article/preposition sitting directly in front of a
    # Capitalized word is very likely a correctly-untranslated proper name
    # ("Cte J. de Reverseaux", "Parc des Princes"), not failed prose --
    # a page that's mostly a list of duel participants' names legitimately
    # stays almost entirely unchanged and shouldn't count against it
    # (Kirchhoffer src 38: "DUEL ANDRÉ GAUCHER... TOURNOI DE MONTE-CARLO").
    non_english_count = sum(
        1 for i, w in enumerate(words)
        if w in NON_ENGLISH_INDICATORS
        and not (i + 1 < len(words_cased) and words_cased[i + 1][:1].isupper()))
    if non_english_count / len(words) > 0.35:
        return True
    # Backstop for a page that stayed mostly French but is short on the
    # stop words above: elision (consonant + apostrophe + vowel, "d'une" /
    # "qu'il" -- English contractions put a consonant after the apostrophe)
    # and endings that need French-specific letters. An elision straight
    # into a Capitalized letter ("d'Hurcourt") is the same proper-name
    # case as above, not genuine untranslated prose.
    fr = sum(1 for m in re.finditer(
                r"\b[cdjlmnqst]['’]([aeiouyhàâäéèêëïîôùûü])", text, re.I)
             if not m.group(1).isupper()) \
        + len(re.findall(r"\w(?:ité|eaux|aient|âtre|euse|eux|oué)\b", text, re.I))
    return fr / len(words) > 0.045


# An optional leading politeness token the model likes to prepend to both
# preambles and refusals ("Certainly. Here is...", "Sure, please provide...").
_LEAD_POLITE = r"(?:(?:sure|certainly|of course|okay|ok|yes|alright|understood)[,.!]?\s+)?"

# Prose preambles the model emits despite the prompt's "output ONLY the
# translation" rule. Matched only at the very start of the output, one per
# line, case-insensitively. The Markdown sanitizers already handle '#'/'**'.
#
# Every alternative must reference the ACT of translating or be a known
# fixed filler phrase -- loose patterns like "any line ending in a colon"
# strip real page-initial sentences ("In this case, the appreciation of
# the thrust ... Example:" is genuine fencing prose).
_PREAMBLE_RE = re.compile(
    r"^\s*" + _LEAD_POLITE + r"(?:"
    r"here(?:'s| is)\b[^\n]*\b(?:translat\w+|rendering|english version)\b[^\n]*|"
    r"[^\n]*\bhere(?:'s| is) the translation\b[^\n]*|"      # "... Here is the translation:"
    r"below is\b[^\n]*\btranslat\w+[^\n]*|"
    r"the (?:following is|translation (?:is|follows))\b[^\n]*|"
    r"i(?:'ll| will| can)\b[^\n]*\btranslat\w+[^\n]*|"
    r"(?:sure|certainly|of course|okay|ok)[,.!]?|"          # bare filler token
    r"note:\s*[^\n]*|"
    r"following the (?:critical )?rules[^\n]*:|"
    r"this (?:one )?may take a step back[^\n]*"
    r")\s*$",
    re.IGNORECASE)

# Whole-output shapes that mean "the model had nothing to translate" or
# refused -- never a usable page, not even as a low-confidence fallback.
_REFUSAL_RE = re.compile(
    r"^\s*" + _LEAD_POLITE + r"(?:"
    r"please provide (?:the|some) (?:source )?(?:text|content)\b|"
    # "I am ready to translate the text. Please provide the source text"
    # (Rossi gold run, 2026-10-07: a page with nothing to translate)
    r"i(?: am|'m) ready to translate\b|"
    r"(?:the\s+)?(?:text|content)\s+(?:you\s+)?provided\s+(?:does\s+not|doesn't|is\s+not)\b|"
    r"there (?:is|was) no (?:text|content)\b|"
    r"it (?:seems|appears) (?:that )?(?:there is|no) \b|"
    r"this text does not appear to (?:be|contain)\b|"
    r"no (?:text|content) (?:was )?provided\b"
    r")",
    re.IGNORECASE)


_ENGLISH_FUNCTION_WORDS = {"the", "of", "and", "to", "is", "an", "that", "with",   # not "a", "in", "on": French / Italian too
                           "it", "for", "by", "his", "be", "not", "as", "which", "on", "or",
                           "he", "one", "without", "from", "at", "are", "was", "this"}


# function words, for telling which language a paragraph is in (the review
# report's lists, quality_report.py, plus Latin)
_PARA_EN = set("the and of to is are in that with for on it by as be this from his their which "
               "was were one an at or not but have has its".split())
_PARA_LANG = {
    "French": set("le la les des du de et est sont qui que dans pour avec une un sur pas ne se il "
                  "elle au aux ce cette son sa ses leur leurs nous vous on en par plus".split()),
    "Italian": set("il lo la i gli le di da del della dei delle degli che non è sono per con una "
                   "un uno nel nella nei si ma come più anche questo questa quello essere al alla".split()),
    "Latin": set("et est sunt qui quae quod non ad cum ex ab sive nam enim ut aut vel sed atque neque "
                 "quam ubi esse eius ipse ipsis idest omnis".split()),
}


def untranslated_paragraph(text: str):
    """(paragraph start, language) of a paragraph of eight words or more
    still in French / Italian / Latin, or None. Kept terms (<i>), notes,
    margin notes and a bibliography's [original titles] don't count. The
    review report flagged such paragraphs after the fact: Latin footnotes
    (Marchionni pdf 34-35, Danet pdf 338), French quoted in Italian books
    (Florio pdf 55, Masiello 1887 pdf 129), verse left as printed (Blengini
    pdf 82, Florio pdf 38)."""
    body = text.split(FN_HEADER)[0]
    for para in re.split(r"\n\s*\n", body):
        bare = re.sub(r"<i>.*?</i>|<fn>.*?</fn>|⟦[^⟧]*⟧|\[[^\]\n]*\]", " ", para, flags=re.S)
        w = [x.lower() for x in re.findall(r"[^\W\d_]+", bare)]
        if len(w) < 8:
            continue
        en = sum(x in _PARA_EN for x in w)
        lang, n = max(((l, sum(x in ws for x in w)) for l, ws in _PARA_LANG.items()), key=lambda t: t[1])
        if n >= 5 and n > 2 * en:
            return " ".join(para.split()[:6]), lang
    return None


# the author's own footnotes: "(1) …", "(*) …" -- a marker, then the note

# Negations: a page whose source has clearly more negations than its English
# -- a "not" lost turns the meaning round (Kirchhoffer pdf 87 «nous ne
# cesserons» -> "we shall cease"), or a passage was left out. French counts
# only a real negation (ne ... pas / jamais / plus / rien ...; not the
# expletive ne, not ne ... que "only"); Italian "non". Whole pages, notes
# included on both sides.
_NEG_FR = re.compile(r"\b(?:ne|n['’])\s*(?:\S+\s+){0,4}?(?:pas|point|jamais|plus|rien|guère|personne|"
                     r"aucun|aucune|nullement)\b"
                     # literary: no "pas" after cesser, oser, pouvoir, savoir ("nous ne cesserons")
                     r"|\bne\s+(?:l[ae]s?\s+|[mts]e\s+)?(?:cess|os[ae]|osi|pu[ie]?s|peu[xt]|pourr|sau|sai[ts]?\b|sav)\w*(?!\s+(?:\S+\s+){0,3}?(?:pas|point|jamais|plus|rien|guère)\b)"
                     r"(?![^.;:!?]{0,60}\bqu)",          # ne peut ... que = "can only" (Gomard scan 27)
                     re.I)
# Italian: not "non ... che" ("only", like ne ... que) nor "se non" ("except")
_NEG_IT = re.compile(r"(?<![Ss]e )\bnon\b(?!\s+(?:\S+\s+){0,4}?che\b)", re.I)
_NEG_EN = re.compile(r"\b(?:not|never|no|nor|none|nothing|nobody|neither|cannot|without|nowhere|"
                     r"hardly|scarcely|seldom|rarely)\b|n't\b", re.I)


def lost_negations(src: str, en: str, italian: bool) -> int:
    """How many more negations the source page has than its English, notes
    included on both sides: a page that opens with the end of the previous
    page's footnote has that text in the English notes block (Marchionni
    scan 107, Danet scan 69), so body against body would count it as lost."""
    src = re.sub(r"<table\b.*?(?:</table\s*>|\Z)", " ", src or "", flags=re.S | re.I)
    if len((en or "").split()) < 40:
        return 0
    return len((_NEG_IT if italian else _NEG_FR).findall(src)) - len(_NEG_EN.findall(en or ""))


_NOTE_MARK = r"\((?:\d{1,2}|\*{1,3})\)"


def note_bodies(text: str) -> int:
    """How many of the author's footnotes a page holds: a marker at the start
    of a line, then the note's first word (a call in the running text,
    "(1), ma siccome …", is not a note). In a finished page the notes are
    gathered in the footnote block, where they can follow one another on a
    line: there any marker followed by a word counts."""
    body, _, block = (text or "").partition(FN_HEADER)
    word = r"[ \t.]*(?:<i>|[«“\"])?[^\W\d_]"
    return (len(re.findall(r"(?m)^[ \t]*" + _NOTE_MARK + word, body))
            + len(re.findall(_NOTE_MARK + word, block)))


def quotation_left_untranslated(text: str) -> bool:
    """A "<fn>French in the original</fn>" (or Latin, …) note after a
    quotation still in that language: the text before the note, back to
    the opening quotation mark or the start of the line, has no English
    function word and some foreign one (Masiello 1887 pdf 73: "Sans le
    bouter d'estocq ou hachier.<fn>French in the original</fn>")."""
    for m in re.finditer(r"<fn>\s*\w+ in the original\.?\s*</fn>", text, re.I):
        seg = text[max(0, m.start() - 400):m.start()].split("\n")[-1]
        opened = max(seg.rfind(q) for q in "«“\"")
        if opened > 0:
            seg = seg[opened:]
        seg = re.sub(r"<fn>.*?</fn>|<i>.*?</i>", " ", seg)
        words = [w.lower() for w in re.findall(r"\b\w+\b", seg)]
        if len(words) < 3 or any(w in _ENGLISH_FUNCTION_WORDS for w in words):
            continue
        if any(w in NON_ENGLISH_INDICATORS for w in words) \
                or re.search(r"\b[cdjlmnqst]['’][aeiouyh]", seg, re.I):
            return True
    return False


def strip_model_preamble(text: str) -> str:
    """Drop leading 'Here is the translation:' / 'Sure:' / 'Note: ...' lines
    the model prepends despite the prompt. Stops at the first real line."""
    lines = text.split("\n")
    while lines and (not lines[0].strip() or _PREAMBLE_RE.match(lines[0])):
        lines.pop(0)
    return "\n".join(lines).strip()


def looks_like_refusal(text: str) -> bool:
    """True when the whole output is a 'give me something to translate' /
    'this isn't fencing text' message rather than a translation."""
    return bool(_REFUSAL_RE.match(text.strip()))


def is_near_blank(text: str) -> bool:
    """A page whose OCR body is just a stray mark / page number / one word --
    not worth a model call (the model answers 'Please provide the text...').
    Kept conservative so real terse content (a plate caption like
    'Planche IV') still goes through."""
    letters = re.findall(r"[^\W\d_]", text, flags=re.UNICODE)
    return len(text.split()) < 2 or len(letters) < 8


_TOC_LINE_RE = re.compile(r"^.{1,70}?[.·•…\s]{4,}\s*\d{1,4}\s*$")


def is_toc_page(text: str) -> bool:
    """A table-of-contents / index page: most lines are
    `entry text … dot-leader … page number`. Such a page is neither prose
    nor a clean heading list -- the model applies the term policy
    unevenly and (worse) staples a once-per-book `<fn>` footnote onto a
    contents line. Detected pages translate with footnotes suppressed."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return False
    hits = sum(1 for l in lines if _TOC_LINE_RE.match(l))
    return hits >= 0.5 * len(lines)
