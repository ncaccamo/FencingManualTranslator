# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""The term tables (glossary_fr.toml, glossary_it.toml): one entry per term
decision, and the part of the prompt built from them for one page.

Not used by step 2 yet (2026-11-11): the prompt files are still what the
model is sent. This module holds the same decisions as data, so that a
page's prompt can carry only the entries whose source word is on the page.

    load("French")                      the entries, checked
    matched(entries, page_text)         those whose source is on the page
    findings(entries, source, english)  what the entries set to check = "retry" find on a page
    check_applies(entry, source)        should the entry's check run on this page
    forbidden_found(entry, english)     its "never" forms present in the English
    without_book_terms(entry, en, t)    the English less the book's own [terms] renderings
    book_translates(entry, terms)       a kept term the book's [terms] translate
    kept_missing(entry, english)        a kept term the English does not have
    glossary_block(prompt, entries)     rules 3 and 3b written from entries
    page_prompt(prompt, lang, text)     the prompt with rules 3 / 3b cut
                                        down to the page's entries
    code_lists("French")                the kept / footnoted lists step 2's code works from
    prompt_entries(prompt)              rules 3 / 3b of a prompt file, split
                                        into entries (for the test that the
                                        tables and the prompt say the same)
"""
import os
import re
import tomllib
import unicodedata

_DIR = os.path.dirname(os.path.abspath(__file__))
_FILES = {"French": "glossary_fr.toml", "Italian": "glossary_it.toml"}
_KINDS = ("keep", "translate", "footnote", "note")
_KEYS = {"id", "kind", "with", "group", "source", "source_re", "english", "never", "never_re", "check_source_re", "check", "forms", "refs", "model_notes", "not_before", "enforce", "line"}
_RULE3, _RULE3B, _RULE4 = "3. TRANSLATE", "3b. TRANSLATE", "4. Headings"


class GlossaryError(ValueError):
    pass


def load(lang: str, path: str = "") -> list:
    """The entries of a language's table, in file order. A mistake in the
    file (unknown field, repeated id, entry with nothing to match on) stops
    the load: a term silently left out is worse than a failed run."""
    p = path or os.path.join(_DIR, _FILES[lang])
    with open(p, "rb") as fh:
        terms = tomllib.load(fh).get("term", [])
    seen = set()
    for t in terms:
        i = t.get("id")
        if not i or i in seen:
            raise GlossaryError(f"{p}: id missing or repeated: {i!r}")
        seen.add(i)
        bad = set(t) - _KEYS
        if bad:
            raise GlossaryError(f"{p}: [{i}] unknown field(s) {sorted(bad)}")
        if t.get("kind") not in _KINDS:
            raise GlossaryError(f"{p}: [{i}] kind must be one of {_KINDS}")
        if t.get("check", "off") not in ("off", "retry"):
            raise GlossaryError(f"{p}: [{i}] check must be \"off\" or \"retry\"")
        if (t.get("forms") or t.get("model_notes") or t.get("not_before")) and t.get("kind") != "footnote":
            raise GlossaryError(f"{p}: [{i}] forms / model_notes / not_before belong to a footnoted term")
        if t.get("enforce") and t.get("kind") != "keep":
            raise GlossaryError(f"{p}: [{i}] enforce belongs to a kept term")
        for f in t.get("forms", []):
            if not (isinstance(f, list) and len(f) in (2, 3) and all(isinstance(x, str) and x for x in f)):
                raise GlossaryError(f"{p}: [{i}] forms: each is [source form, English] or [form, English, noted under]")
        try:
            for _, pattern in t.get("not_before", []):
                re.compile(pattern)
            for _, wrong in t.get("enforce", []):
                for w in wrong:
                    re.compile(w)
        except (re.error, ValueError, TypeError) as e:
            raise GlossaryError(f"{p}: [{i}] not_before / enforce: {e}")
        if t["kind"] == "note":
            if t.get("with") not in ("translate", "footnote") or not t.get("line"):
                raise GlossaryError(f"{p}: [{i}] a note needs 'with' and 'line'")
            continue
        if not t.get("source") and not t.get("source_re"):
            raise GlossaryError(f"{p}: [{i}] needs 'source' or 'source_re'")
        if t["kind"] != "keep" and not t.get("line"):
            raise GlossaryError(f"{p}: [{i}] needs 'line'")
        if t["kind"] == "keep" and not t.get("english"):
            raise GlossaryError(f"{p}: [{i}] a kept term needs 'english' (how it is written in the English)")
        t["_en_re"] = _english_re(t["english"]) if t.get("english") else None
        t["_never_re"] = _english_re(t["never"], endings=True) if t.get("never") else None
        try:
            t["_never_raw"] = re.compile(t["never_re"]) if t.get("never_re") else None
            t["_check_src"] = re.compile(t["check_source_re"]) if t.get("check_source_re") else None
        except re.error as e:
            raise GlossaryError(f"{p}: [{i}] never_re / check_source_re: {e}")
        t["_re"] = _source_re(t.get("source", []), lang)
        t["_strict_re"] = _source_re(t.get("source", []), lang, strict=True)
        try:
            t["_raw_re"] = re.compile(t["source_re"]) if t.get("source_re") else None
        except re.error as e:
            raise GlossaryError(f"{p}: [{i}] source_re: {e}")
    return terms


def code_lists(lang: str, terms: list = None) -> tuple:
    """The lists step 2's code corrections work from, built from the table
    (fields forms / refs / model_notes / not_before / enforce), in table
    order:

        footnoted    [(source form, English)]   translated when the model left
                     the word in the source language, with a first-use note
        note_under   {form: the word its note is written under}
        not_before   {form: pattern}            not the fencing term before this
        kept         [(term, [wrong English renderings])]
        model_notes  {terms whose note the model writes itself}

    pipeline/_glossary_fr.py and _glossary_it.py give them their old names."""
    terms = load(lang) if terms is None else terms
    footnoted, note_under, not_before, kept, model_notes = [], {}, {}, [], set()
    for t in terms:
        for f in t.get("forms", []):
            footnoted.append((f[0], f[1]))
            if len(f) > 2:
                note_under[f[0]] = f[2]
        for form, under in t.get("refs", []):
            note_under[form] = under
        for form, pattern in t.get("not_before", []):
            not_before[form] = pattern
        for term, wrong in t.get("enforce", []):
            kept.append((term, list(wrong)))
        model_notes.update(t.get("model_notes", []))
    return footnoted, note_under, not_before, kept, model_notes


def norm(text: str, accents: bool = False) -> str:
    """Page text as the source forms are matched against it: small letters,
    no accents (unless `accents`), one kind of apostrophe, a word the
    printer or the OCR broke at a line end ("Trat- tato", "dégage-\\nment")
    put back together."""
    if accents:
        t = unicodedata.normalize("NFC", text or "").lower()
        t = t.replace("\u2019", "'").replace("\u02bc", "'")
        return re.sub(r"(?<=\w)-\s*\n\s*(?=\w)|(?<=\w)- (?=\w)", "", t)
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = t.replace("’", "'").replace("ʼ", "'")
    return re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])|(?<=[a-z])- (?=[a-z])", "", t)


_IT_PLURAL = {"a": "[ae]", "o": "(?:o|i|hi)", "e": "[ei]", "i": "i"}


def _word(tok: str, lang: str, strict: bool = False) -> str:
    """One word of a source form as a pattern. "tir*" is any word starting
    "tir". Otherwise the common endings: French -s -x -e -es; an Italian
    word's last vowel may change (colpo / colpi, attacco / attacchi).
    `strict`, for the checks: an Italian word only takes its own plural
    (terza / terze, not "terzo")."""
    if tok.endswith("*"):
        return re.escape(tok[:-1]) + r"\w*"
    if lang == "Italian" and strict:
        if len(tok) > 3 and tok[-1] in _IT_PLURAL:
            return re.escape(tok[:-1]) + _IT_PLURAL[tok[-1]]
        return re.escape(tok)
    if lang == "Italian":
        if len(tok) > 3 and tok[-1] in "aeio":
            return re.escape(tok[:-1]) + r"(?:[aeio]|h[ei])"
        return re.escape(tok)
    return re.escape(tok) + r"(?:s|x|e|es)?"


def _source_re(forms: list, lang: str, strict: bool = False):
    if not forms:
        return None
    alts = [r"[\s\-]+".join(_word(w, lang, strict) for w in norm(f, accents=strict).split()) for f in forms]
    return re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)")


def _english_re(forms: list, endings: bool = False):
    """English forms as one pattern: whole words, any case, accents as
    written ("doublé" is not "double"). A kept term's form is a word start
    ("ripost" finds riposte, riposted, riposting); a forbidden form takes
    the English endings (blow, blows; romp, romped)."""
    tail = r"(?:s|es|ed|ing)?(?!\w)" if endings else r"\w*"
    alts = [r"[\s\-]+".join(re.escape(w) for w in f.lower().replace("\u2019", "'").split()) for f in forms]
    return re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")" + tail, re.I)


def english_plain(en: str) -> str:
    """The English as the checks read it: no tags, one kind of apostrophe."""
    return re.sub(r"<[^>]+>", "", en or "").replace("\u2019", "'")


def forbidden_found(term: dict, en: str) -> list:
    """The entry's forbidden forms present in the English (the caller has
    already found the entry's source on the page)."""
    plain = english_plain(en)
    # a pattern, where it is given, replaces the word list: the words alone
    # ("time") are right elsewhere on the page, the pattern ("two times") is not
    rx = term.get("_never_raw") or term.get("_never_re")
    return sorted({re.sub(r"\s+", " ", m.group(0).lower()) for m in rx.finditer(plain)}) if rx else []


def _book_rendering(value: str) -> str:
    """The English a [terms] line gives, without its remark:
    "thrust -- Gomard's defined term, …" -> "thrust"."""
    return re.split(r"\s+--\s+|\s+\(|;", english_plain(value), maxsplit=1)[0].strip()


def without_book_terms(term: dict, en: str, book_terms: dict) -> str:
    """The English with the book's own renderings of this entry's word
    taken out, so a check does not flag what the book's [terms] decided:
    Jacob-André's «le jeu de l'épée» → "the game of the épée" stays, while
    "game" for «jeu» anywhere else in that book is still found."""
    plain = english_plain(en)
    for key, value in (book_terms or {}).items():
        r = _book_rendering(value)
        if r and on_page(term, key, strict=True):
            plain = re.sub(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in r.split()) + r"(?!\w)", " ", plain, flags=re.I)
    return plain


def book_translates(term: dict, book_terms: dict) -> bool:
    """A kept term the book has chosen to translate: a [terms] key is one of
    the entry's own source forms and its English has no kept form."""
    for key, value in (book_terms or {}).items():
        if any(norm(key, accents=True) == norm(f, accents=True).rstrip("*") for f in term.get("source", [])) \
                and term.get("_en_re") and not term["_en_re"].search(_book_rendering(value)):
            return True
    return False


def source_hit(term: dict, src: str) -> str:
    """The source words that make the entry's check apply, as printed
    (small letters), or "" -- for a message or a retry hint."""
    for rx, text in ((term.get("_check_src"), src or ""), (term.get("_raw_re"), src or ""),
                     (term.get("_strict_re"), norm(src, accents=True))):
        m = rx.search(text) if rx else None
        if m:
            return re.sub(r"\s+", " ", m.group(0)).strip().lower()
    return ""


def findings(terms: list, src: str, en: str, book_terms: dict = None, level: str = "retry") -> list:
    """What the entries set to `level` find on one page: a list of
    (entry, what) -- what is a forbidden form found in the English, or
    "(missing)" for a kept term. `src` is the page's source text, `en` its
    English. A book's own [terms] are respected."""
    src = running_text(src)
    out = []
    for t in terms:
        if t.get("check", "off") != level or not check_applies(t, src):
            continue
        for f in forbidden_found(t, without_book_terms(t, en, book_terms)):
            out.append((t, f))
        if t["kind"] == "keep" and kept_missing(t, en) and not book_translates(t, book_terms):
            out.append((t, "(missing)"))
    return out


def check_applies(term: dict, src: str) -> bool:
    """Should the entry's check run on this page? Its source must be there,
    read strictly; an entry with `check_source_re` is checked only where
    that pattern is (the count «due tempi», not every «tempo»)."""
    if term["kind"] == "note":
        return False
    if term.get("_check_src"):
        return bool(term["_check_src"].search(src or ""))
    return on_page(term, src, strict=True)


def kept_missing(term: dict, en: str) -> bool:
    """A kept term whose source is on the page and which the English does
    not have in any form."""
    return term["kind"] == "keep" and not term["_en_re"].search(english_plain(en))


_TABLE = re.compile(r"<table.*?</table>", re.S | re.I)


def running_text(src: str) -> str:
    """A source page without its tables: their English is kept apart
    (tables.json), so a check of the page's English must not expect it."""
    return _TABLE.sub(" ", src or "")


def on_page(term: dict, text: str, normed: str = "", strict: bool = False) -> bool:
    """Is the entry's source on the page? `text` as written (for source_re),
    `normed` = norm(text) when the caller already has it. For choosing a
    page's prompt entries the match is loose (accents optional, so a heading
    in capitals without them still counts: better an entry too many). For a
    check, `strict`: accents as printed («doublé» is not «double») and an
    Italian word only in its own plural."""
    if term["kind"] == "note":
        return False
    if term.get("_raw_re") and term["_raw_re"].search(text or ""):
        return True
    if strict:
        return bool(term.get("_strict_re") and term["_strict_re"].search(norm(text, accents=True)))
    return bool(term.get("_re") and term["_re"].search(normed or norm(text)))


def matched(terms: list, text: str) -> list:
    """The entries for a page, in table order (the same page always gets
    the same list), with each note whose entries are among them."""
    n = norm(text)
    hit = [t for t in terms if on_page(t, text, n)]
    ids = {t["id"] for t in hit}
    kinds = {(t["kind"], t.get("group")) for t in hit}
    out = []
    for t in terms:
        if t["kind"] == "note":
            g = t.get("group")
            if (g and (t["with"], g) in kinds) or (not g and any(k == t["with"] for k, _ in kinds)):
                out.append(t)
        elif t["id"] in ids:
            out.append(t)
    return out


def _section(prompt: str, start: str, end: str) -> tuple:
    a = prompt.index(start)
    b = prompt.index(end, a)
    return a, b


def glossary_block(prompt: str, terms: list) -> str:
    """Rules 3 and 3b of `prompt` written again from `terms`: each rule's
    own first line (kept word for word from the prompt), then one line per
    entry. A rule none of whose entries is given says so."""
    out = []
    for kind, start, end in (("translate", _RULE3, _RULE3B), ("footnote", _RULE3B, _RULE4)):
        a, b = _section(prompt, start, end)
        out.append(prompt[a:b].split("\n", 1)[0])
        mine = [t for t in terms if t["kind"] == kind or (t["kind"] == "note" and t["with"] == kind)]
        if not any(t["kind"] == kind for t in mine):
            out.append("   (none of these terms is on this page)")
        for t in mine:
            first, *rest = t["line"].split("\n")
            lead = "   " if t["kind"] == "note" and t["with"] == "footnote" else "   - "
            out.append(lead + first)
            out.extend("     " + r for r in rest)
        out.append("")
    return "\n".join(out) + "\n"


_STUB = ("3 and 3b. The fencing terms to TRANSLATE are listed after the examples, under "
         "TERMS ON THIS PAGE: rule 3 (plain text) and rule 3b (with a note at first use).\n\n")
_END_MARK = "Text to translate:"


def page_prompt(prompt: str, lang: str, text: str, terms: list = None, at_end: bool = False) -> str:
    """`prompt` (a language's whole prompt, as prompt_for_language() gives
    it) with rules 3 and 3b cut down to the entries on the page. Everything
    else -- rules 0-2, 4-10, the examples -- is the prompt's own text.

    `at_end`: the page's entries go after the examples instead of between
    rules 2 and 4 (one line there says where they are). Everything before
    them is then the same for every page of a language, and the model
    server reads that part once instead of once per page."""
    terms = load(lang) if terms is None else terms
    a, _ = _section(prompt, _RULE3, _RULE3B)
    _, b = _section(prompt, _RULE3B, _RULE4)
    block = glossary_block(prompt, matched(terms, text))
    if not at_end:
        return prompt[:a] + block + prompt[b:]
    rest = prompt[b:]
    k = rest.rindex(_END_MARK) if _END_MARK in rest else len(rest)
    return prompt[:a] + _STUB + rest[:k] + "TERMS ON THIS PAGE\n\n" + block + rest[k:]


def prompt_entries(prompt: str) -> list:
    """Rules 3 and 3b of a prompt file as (kind, text) in order, split the
    way the tables are: one per line and per " | " part; the coup / botte
    passage, which runs over several lines, is one entry."""
    out = []
    for kind, start, end in (("translate", _RULE3, _RULE3B), ("footnote", _RULE3B, _RULE4)):
        a, b = _section(prompt, start, end)
        lines = prompt[a:b].split("\n")[1:]
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            i += 1
            if not s:
                continue
            if s.startswith("- coup vs botte"):
                blk = [s[2:]]
                while i < len(lines) and not lines[i].startswith("   - "):
                    blk.append(lines[i].strip())
                    i += 1
                out.append((kind, "\n".join(blk)))
                continue
            body = s[2:] if s.startswith("- ") else s
            if "→" not in body:
                out.append(("note", body))
                continue
            out.extend((kind, e.strip()) for e in re.split(r"\s+\|\s+", body))
    return out
