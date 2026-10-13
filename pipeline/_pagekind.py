# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Pages that need a different translation: the book's own glossary of
terms, and a bibliography.

Why: prompt rule 7b (a glossary keeps its headword in the original) and
rule 0's book-title line were followed on some pages and not others
(2026-10-13 test: Gomard pdf 110-111 right, Kirchhoffer pdf 39 and Gomard
pdf 113 not at all; Gomard's bibliography without its original titles).
A rule the model reads on every page is easy for it to miss on the few
pages it applies to. So the pages are found here, the model is told what
kind of page it has, and code completes what it still leaves out.

  book_page_kinds(pages)  -> {index: "glossary" | "bibliography"}
  complete_glossary(en, src)      headword kept: "<i>Haut</i> (high). — …"
  complete_bibliography(en, src)  title followed by "[original title]"

A glossary is a run of pages whose entries ("Haut. — Expression …",
"Caver; c'est …", "Sauter, se dit …") have their headwords in alphabetical
order. Run-in section headings have the same shape ("Battement. — …",
Kirchhoffer pdf 24) but not the order, and an alphabetical list of names
("Engel Leone maestro d'armi — Torino", Lambertini) has no definitions.
A bibliography page is one most of whose lines end in a year.
"""
import bisect
import re
import unicodedata

# headword, optional "(…)", then the separator that opens its definition
_ENTRY = re.compile(r"""^[ \t]*[-–]?[ \t]*(?P<head>[A-ZÀ-Ý«][^\n.;:—]{0,45}?)[ \t]*(?:\([^)\n]{0,30}\)[ \t]*)?
 (?P<sep> \.[ \t]*[—–-]
   | [ \t][—–][ \t]
   | [;,][ \t]*(?=c['’]est|se\ dit|on\ dit|exprime|si\ dice|è\ )
   | [ \t]?;[ \t](?=[a-zà-ÿ])
   | \.[ \t]+(?=C['’]est|Se\ dit|On\ (?:dit|appelle)|Expression|Mouvement|Action|Ce\ mot|Si\ dice|È\ )
 )(?P<rest>[^\n]*)""", re.M | re.X)

_YEAR_END = re.compile(r"\b1[4-9]\d\d\s*[.)]?\s*(?:\([^)\n]*\)\s*)?$")


def _lines(text: str) -> list:
    """Non-empty lines of a page body, margin notes left out."""
    return [l for l in text.split("\n") if l.strip() and not l.strip().startswith("⟦")]


def glossary_entries(text: str) -> list:
    """(line index, headword, separator) of each glossary-shaped line, line
    indices into _lines(text). A headword of more than five words, one in
    capitals (a heading), or a definition under four words (a name list's
    "— Torino") does not count."""
    out = []
    for n, line in enumerate(_lines(text)):
        m = _ENTRY.match(line)
        if not m:
            continue
        head = m.group("head").strip().rstrip(",").rstrip()
        if len(head.split()) > 5 or head.isupper() or len(m.group("rest").split()) < 4:
            continue
        out.append((n, head, m.group("sep")))
    return out


def _key(head: str, reflexive: bool = False) -> str:
    h = unicodedata.normalize("NFKD", head).encode("ascii", "ignore").decode().lower()
    h = re.sub(r"^[^a-z]+", "", h)
    if reflexive:               # "Se couvrir" filed under C (Kirchhoffer) or under S (Gomard)
        h = re.sub(r"^(?:se\s+|s')", "", h)
    return re.sub(r"[^a-z]", "", h)


def _longest_rising(keys: list) -> int:
    tails = []
    for k in keys:
        i = bisect.bisect_left(tails, k)
        if i == len(tails):
            tails.append(k)
        else:
            tails[i] = k
    return len(tails)


def _alphabetical(heads: list) -> bool:
    """At least three headwords, three quarters of them in alphabetical order."""
    if len(heads) < 3:
        return False
    return max(_longest_rising([_key(h, r) for h in heads])
               for r in (False, True)) >= 0.75 * len(heads)


def book_page_kinds(pages: list) -> dict:
    """{0-based page index: "glossary" | "bibliography"} for a book's page
    bodies (cleaned text, no field labels). A single alphabetical page is
    not enough -- two in a row are; one weaker page between two such runs
    (a second list starting again at A) and the page right after a run
    whose first entries carry the order on (the list's last page) join it."""
    heads = [[h for _, h, _ in glossary_entries(p or "")] for p in pages]
    strong = [_alphabetical(h) for h in heads]
    kinds, k, n = {}, 0, len(pages)
    while k < n:
        if not strong[k]:
            k += 1
            continue
        j = k
        while True:
            while j + 1 < n and strong[j + 1]:
                j += 1
            if j + 2 < n and strong[j + 2] and len(heads[j + 1]) >= 3:
                j += 2
                continue
            break
        if j > k:
            for i in range(k, j + 1):
                kinds[i] = "glossary"
            nxt = heads[j + 1] if j + 1 < n else []
            if len(nxt) >= 2 and max(_key(nxt[0], r) >= _key(heads[j][-1], r)
                                     for r in (False, True)):
                kinds[j + 1] = "glossary"
        k = j + 1
    for i, p in enumerate(pages):
        lines = _lines(p or "")
        years = sum(1 for l in lines if _YEAR_END.search(l))
        if i not in kinds and years >= 4 and years >= 0.4 * len(lines):
            kinds[i] = "bibliography"
    return kinds


# what the model is told, right before the page text
INSTRUCTION = {
    "glossary": (
        "THIS PAGE IS THE BOOK'S OWN GLOSSARY OF TERMS (rule 7b). Every entry starts "
        "with its headword: keep that headword in the original language, in <i>…</i>, "
        "then its English in parentheses, then the translated definition — "
        "\"<i>Haut</i> (high). — Expression used …\", \"<i>Caver</i> (to angle); this is …\". "
        "A kept term (rule 2) is not repeated: \"<i>Remise</i>. — …\"."),
    "bibliography": (
        "THIS PAGE IS A BIBLIOGRAPHY (rule 0). Translate each book title, then give the "
        "original title in square brackets, then the rest of the entry translated: "
        "\"The Art of Arms Simplified [L'art des armes simplifié], by J. Olivier. Paris, 1771.\""),
}


def _plain(s: str) -> str:
    s = re.sub(r"<fn>.*?</fn>|</?i>|†\d+", "", s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


# where the English definition starts, by the source's separator
def _en_sep(sep: str) -> str:
    s = sep.strip()
    if s.startswith("."):
        return r"\.[ \t]*[—–-]" if len(s) > 1 else r"\.[ \t]"
    if s in ("—", "–"):
        return r"[ \t][—–][ \t]"
    return re.escape(s[:1])


def complete_glossary(en: str, src: str) -> str:
    """Put back each glossary headword the translation turned into English:
    "High. — Expression …" -> "<i>Haut</i> (high). — Expression …". Lines
    are paired one for one; if the page has a different number of lines in
    English the page is left as it is (the instruction did what it could)."""
    s_lines, e_all = _lines(src), en.split("\n")
    e_idx = [i for i, l in enumerate(e_all) if l.strip() and not l.strip().startswith("⟦")
             and l.strip() != "___FOOTNOTES___"]
    if "___FOOTNOTES___" in en:     # the page's own notes are not entries
        cut = e_all.index("___FOOTNOTES___")
        e_idx = [i for i in e_idx if i < cut]
    if len(e_idx) != len(s_lines):
        return en
    for n, head, sep in glossary_entries(src):
        i = e_idx[n]
        line = e_all[i]
        lead = re.match(r"[ \t]*[-–]?[ \t]*", line).group(0)
        body = line[len(lead):]
        if re.match(r"<i>\s*" + re.escape(head) + r"\s*</i>", body, re.I):
            continue                                    # already kept
        # a headword with its own commas ("Etre croisé, se croiser, se dit")
        # has as many in English before the definition
        seps = list(re.finditer(_en_sep(sep), body[:len(head) * 3 + 40]))
        skip = head.count(sep.strip()[:1]) if sep.strip()[:1] in ",;" else 0
        if len(seps) <= skip or seps[skip].start() == 0:
            continue
        en_head = body[:seps[skip].start()].rstrip().rstrip(",").rstrip()
        gloss = re.sub(r"<fn>.*?</fn>|†\d+", "", en_head).strip()
        if gloss.endswith(")") and "(" in gloss:        # "Ligne (to be in)"
            continue
        if _plain(gloss) == _plain(head):               # same word, or left in the original
            new = f"<i>{head}</i>"
        else:
            gloss = re.sub(r"^((?:<i>)?)([A-Z])(?=[a-z])", lambda g: g.group(1) + g.group(2).lower(), gloss)
            new = f"<i>{head}</i> ({gloss})"
        e_all[i] = lead + new + body[len(en_head):]
    return "\n".join(e_all)


# the author, after the title: ", par …", ", di …", "; by …"
_BY_SRC = re.compile(r"[,;]\s+(?:(?:par|di|da|composta\s+da|scritt[oa]\s+da|del|dal)\s|d['’](?=[A-ZÀ-Ý]))")
_BY_EN = re.compile(r"[,;]\s+(?:by|composed\s+by|written\s+by)\s")


def complete_bibliography(en: str, src: str) -> str:
    """Give each bibliography title its original in square brackets when the
    translation left it out. Entries are paired by the year that ends them
    (same years, same order, or the page is left as it is); the title is
    what comes before the author (", par …" / ", by …")."""
    s_entries = [l for l in _lines(src) if _YEAR_END.search(l)]
    e_all = en.split("\n")
    e_idx = [i for i, l in enumerate(e_all) if _YEAR_END.search(l)]
    def year(l):
        return re.findall(r"1[4-9]\d\d", l)[-1]
    if [year(l) for l in s_entries] != [year(e_all[i]) for i in e_idx]:
        return en
    for s, i in zip(s_entries, e_idx):
        line = e_all[i]
        if "[" in line:
            continue
        ms, me = _BY_SRC.search(s), _BY_EN.search(line)
        if not ms or not me:
            continue
        title = s[:ms.start()].strip()
        if len(title.split()) < 2 or _plain(title) == _plain(line[:me.start()]):
            continue
        e_all[i] = line[:me.start()] + f" [{title}]" + line[me.start():]
    return "\n".join(e_all)
