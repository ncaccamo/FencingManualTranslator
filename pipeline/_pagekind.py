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
        # kept, but italic only in part, before the model's own English:
        # "<i>Coup</i> jugé (judged <i>coup</i>)", "<i>Etre croisé</i>, <i>se croiser</i> (to be crossed)"
        paren = body.find("(", 0, len(head) * 2 + 20)
        if paren > 0 and _plain(body[:paren]) == _plain(head):
            e_all[i] = lead + f"<i>{head}</i> " + body[paren:]
            continue
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


_HEADWORD = re.compile(r"(?m)^([ \t]*[-–]?[ \t]*)(<i>[^<>\n]{1,80}</i>)")


def mask_headwords(text: str) -> tuple:
    """Hide each line-opening "<i>headword</i>" of a glossary page from the
    term passes that follow, which otherwise rewrite the French inside it
    ("<i>Écraser le fer</i>" -> "<i>Écraser the blade</i>", Prévost-Jollivet
    pdf 316). unmask_headwords() puts them back."""
    saved = []

    def hide(m):
        saved.append(m.group(2))
        return f"{m.group(1)}\x02{len(saved) - 1}\x02"
    return _HEADWORD.sub(hide, text), saved


def unmask_headwords(text: str, saved: list) -> str:
    return re.sub(r"\x02(\d+)\x02", lambda m: saved[int(m.group(1))], text)


# the author, after the title: ", par …", ", di …", "; by …"
# ... followed by a name ("par Danet", "par le sieur", "d'Alessandro"), never
# "by way of dialogue"
_NAME = r"(?=[A-ZÀ-Ý]|M\.|Mr|le\s|the\s+[Ss]ieur)"
_BY_SRC = re.compile(r"(?:(?:[,;]\s+|\s|^)(?:par|di|da|composta\s+da|scritt[oa]\s+da|del|dal"
                     r"|von|dargestellt\s+von|herausgegeben\s+von)\s|[,;]\s+d['’])" + _NAME)
_BY_EN = re.compile(r"(?:[,;]\s+|\s|^)(?:by|composed\s+by|written\s+by|presented\s+by|edited\s+by)\s" + _NAME)


def _entries(lines: list) -> list:
    """Bibliography entries: the lines up to and including each one ending
    in a year (an entry may run over two or three lines)."""
    out, cur = [], []
    for n, l in lines:
        cur.append((n, l))
        if _YEAR_END.search(l):
            out.append(cur)
            cur = []
    return out


def complete_bibliography(en: str, src: str) -> str:
    """Give each bibliography title its original in square brackets when the
    translation left it out. Entries are paired by the year that ends them
    (same years, same order, or the page is left as it is); the title is
    what comes before the author (", par …" / ", by …")."""
    s_entries = _entries(list(enumerate(_lines(src))))
    e_all = en.split("\n")
    e_entries = _entries([(i, l) for i, l in enumerate(e_all)
                          if l.strip() and not l.strip().startswith("⟦")])

    def year(entry):
        return re.findall(r"1[4-9]\d\d", entry[-1][1])[-1]
    if [year(e) for e in s_entries] != [year(e) for e in e_entries]:
        return en
    for s_ent, e_ent in zip(s_entries, e_entries):
        if any("[" in l for _, l in e_ent):
            continue
        # the title starts on the line where the author is named (the line
        # before too, when that one holds only its last word or two): an
        # entry without a year above it is not part of it
        s_lines = [l.strip() for _, l in s_ent]
        ms = next(((n, m) for n, l in enumerate(s_lines) for m in [_BY_SRC.search(l)] if m), None)
        hit = next(((k, m) for k, (_, l) in enumerate(e_ent)
                    for m in [_BY_EN.search(l)] if m), None)
        if not ms or not hit:
            continue
        n, m = ms
        title = s_lines[n][:m.start()]
        if len(title.split()) < 2 and n > 0:
            title = s_lines[n - 1] + " " + title
        title = title.strip().rstrip(",;").strip()
        k, me = hit
        if me.start() == 0:                     # "by …" opens the line: after the line before
            if k == 0:
                continue
            k -= 1
            at = len(e_ent[k][1].rstrip())
        else:
            at = me.start()
        i, line = e_ent[k]
        # the title, and the English before the author, must look like the
        # same thing: not a carry-over from the page before (a year, a
        # "(…)"), not "in which" against a long title
        before = line[:at] if len(line[:at].split()) >= 2 or k == 0 else e_ent[k - 1][1] + " " + line[:at]
        nt, nb = len(title.split()), len(before.split())
        if nt < 2 or re.search(r"1[4-9]\d\d|\(", title) or not 0.5 * nt <= nb <= 2.2 * nt \
                or _plain(title) == _plain(before):
            continue
        e_all[i] = line[:at].rstrip().rstrip(",;") + f" [{title}]" + line[at:]
    return "\n".join(e_all)
