# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Step 1: text across page breaks -- a word hyphenated over the break is
rejoined, and a sentence split by it is joined onto one page so step 2
translates it whole."""

import re


def _book_compounds(parsed: list) -> set:
    """Hyphenated words the book prints whole somewhere ("contre-dégagement",
    "procès-verbal"), lowercased: a word broken across a page break at that
    hyphen keeps it. Measured 2026-10-05: the four compounds the joins below
    had glued (La Boëssière contre-dégagement 37x elsewhere, Jacob
    procès-verbal 32x, Masiello Sciabola scapolo-omerale 3x) all appear
    hyphenated in their book; the line-break splits (pronation 90x,
    avversario 266x) never do. The hyphenated form must also outnumber
    the joined one: a line-break hyphen the OCR left inside a page makes a
    stray "scia-bola" (Bruno) or "del-l'" (Barbasetti) beside hundreds of
    "sciabola"."""
    from collections import Counter
    text = "\n".join(p.get("body") or "" for p in parsed)
    hy = Counter(m.lower() for m in re.findall(r"\b\w+-\w+\b", text))
    whole = Counter(w.lower() for w in re.findall(r"\w+", text))
    return {k for k, n in hy.items() if n > whole[k.replace("-", "")]}


def _glue_broken(left: str, right: str, compounds: set) -> str:
    """`left` ends "<word>-" at a page break, `right` continues the word:
    one word, without the hyphen unless the book prints it as a compound."""
    lw = re.search(r"(\w+)-\Z", left)
    rw = re.match(r"\w+", right)
    if lw and rw and f"{lw.group(1)}-{rw.group(0)}".lower() in compounds:
        return left + right
    return left[:-1] + right


def _book_words(parsed: list) -> set:
    """Every word of four letters or more in the book's text, lower case."""
    return {w.lower() for p in parsed for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", p.get("body") or "")}


def dehyphenate_across_pages(parsed: list) -> None:
    """A word hyphenated across a page break in the original ("...déjà pu-"
    | "bliés, on ...") is otherwise translated as two fragments. When page
    i's text ends lowercase+hyphen and page i+1's starts lowercase, splice
    the completion fragment (plus any punctuation glued to it) up to close
    the word on page i. Capitalised continuations -- real hyphenated names
    like Sainct-Didier / La Boëssière -- are left alone.
    The author's footnotes at the foot of page i ("se mé-" then "(1) Cette
    appellation...", Kirchhoffer scan 12) and margin notes at the top of
    page i+1 (Gomard scan 82) sit between the two halves; they are stepped
    over, not joined into. A compound the book prints elsewhere keeps its
    hyphen (_book_compounds)."""
    compounds = _book_compounds(parsed)
    vocab = None
    for i in range(len(parsed) - 1):
        a = (parsed[i].get("body") or "").rstrip()
        main, tail = a, ""
        if not re.search(r"[a-zà-ÿ]-\Z", main):
            # only then look past a footnote block closing the page
            fn = _FOOTNOTE_TAIL.search(a)
            main, tail = (a[:fn.start()].rstrip(), a[fn.start():]) if fn else (a, "")
            if not re.search(r"[a-zà-ÿ]-\Z", main):
                continue
        b = parsed[i + 1].get("body") or ""
        lead = re.match(r"(?:\s*⟦MARGIN:[^⟧]*⟧)*\s*", b).group(0)
        m = re.match(r"([a-zà-ÿ]+[^\s\w]*)([\s\S]*)", b[len(lead):])
        if not m:
            # the second half a few lines down: a two-column lesson page the
            # OCR read master's column first («Parerà seconda.» then «tendo
            # simultaneamente…», Masiello 1887 pdf 582). Only a line whose
            # first word completes a word the book uses elsewhere.
            vocab = vocab if vocab is not None else _book_words(parsed)
            frag = re.search(r"([a-zà-ÿ]+)-\Z", main).group(1)
            lines = b[len(lead):].split("\n")
            for k in range(1, min(6, len(lines))):
                mm = re.match(r"([a-zà-ÿ]+)([^\s\w]*)([\s\S]*)", lines[k])
                # not a one-letter word ("e", "a") that happens to make one;
                # two letters can be the word's end («la-» | «ma», pdf 601)
                if mm and len(mm.group(1)) >= 2 and (frag + mm.group(1)).lower() in vocab:
                    lines[k] = mm.group(3).lstrip()
                    if not lines[k].strip():
                        del lines[k]
                    parsed[i]["body"] = _glue_broken(main, mm.group(1) + mm.group(2), compounds) + tail
                    parsed[i + 1]["body"] = (lead.rstrip() + "\n\n" if lead.strip() else "") + "\n".join(lines).lstrip()
                    break
            continue
        parsed[i]["body"] = _glue_broken(main, m.group(1), compounds) + tail
        parsed[i + 1]["body"] = (lead.rstrip() + "\n\n" if lead.strip() else "") + m.group(2).lstrip()


# a page whose text ends on a title before a name ("M.", "MM.", "Mme.") ends
# mid-sentence: the name is on the next page
_HONORIFIC_END = r"(?:^|[\s(«\u201c])(?:M|MM|Mme|Mmes|Mlle|Mlles|Me|Mgr|Dr|St|Ste|Sig|Sigg|Sig\.ra)\.\Z"


def _not_after_title(text, m):
    """A sentence-end match that isn't the full stop of a title ("MM. Anatole")."""
    return not (m.group(0).startswith(".") and re.search(_HONORIFIC_END[:-2] + r"\Z", text[:m.start() + 1]))


# words a sentence can't end on: articles, prepositions, conjunctions,
# possessives (French, Italian)
_RUNS_ON = set("""le la les un une des de du à au aux en et ou que qui dont par pour sur sous
dans avec sans vers chez entre comme ce cet cette ces son sa ses leur leurs mon ma mes notre nos
votre vos se ne il lo gli i uno di del dello della dei degli delle da dal dallo dalla dai dagli
dalle a al allo alla ai agli alle in nel nello nella nei negli nelle con col coi su sul sullo
sulla sui sugli sulle per tra fra e ed o od che chi cui suo sua suoi sue loro""".split())


def _capital_runs_on(main: str, rest: str) -> bool:
    """A sentence that runs on into a capital on the next page -- a term the
    book capitalises («… sur l'Arrêt de pied ferme, la Marche & la» |
    «Retraite : …», Danet). A page that ends on a name («… quand Leon» |
    «Battista Alberti …», Blengini) is left: a name can end a sentence. Only
    when the page stops on a word a sentence can't end on, and the next page
    opens with running text, not a heading, caption or label line: on 162
    such page breaks in 30 books (2026-11-09) this joined 23, none wrongly;
    a page that ends on any other word is left as it is (a lost full stop
    looks the same as a run-on)."""
    last = main.rsplit("\n", 1)[-1].strip()
    if any(c in last for c in "\u27e6\u27e7|\u00bb") or len(last.split()) < 4:
        return False
    if last.split()[-1].lower() not in _RUNS_ON:
        return False
    first = re.sub(r"<[^>]+>", "", rest.split("\n", 1)[0]).strip()
    if re.match(r"(?:Fig|FIG|§|\d|\(|ART|Art\.|Articolo|ARTICOLO|Parerà|Maestro|Allievo|Question|Réponse|Observation)", first):
        return False
    words = first.split()
    if first.isupper() or len(words) < 5:
        return False
    if re.search(r"[.:)]$", first) and len(words) <= 12:
        return False                       # a heading or caption line
    if first.endswith("-") and len(words) <= 10:
        return False                       # a two-line heading («Osservazioni sul … di- | sarmo …»)
    return True


def join_split_sentences(parsed: list) -> int:
    """A sentence split across a page break is otherwise translated as two
    unrelated fragments, and a short fragment often comes out wrong: "Bien"
    / "fermer alors la ligne" -> "Well" / "then close" (Spinnewyn 101/102),
    "il deviendrait incomparable" -> "it" for the fencer (Jacob 26/27). Every
    split sentence is joined on one page so it is translated whole (operator
    decision 2026-10-01: no broken sentences, at any length).

    A split: page i's running text (before its footnote block) ends without
    sentence-final punctuation, and the next page with text (one blank or
    plate page may sit between) starts lowercase, after any margin notes.
    The SHORTER fragment moves -- the sentence's start forward onto the next
    page, or its end back onto this one -- so the side-by-side drifts as
    little as possible (median 8 words on the French books). A move that
    would leave a page with no text goes the other way, or not at all (an
    emptied page changes how its page number is worked out). Contents lines,
    tables and fragments holding a margin note are left alone. Run after
    margin notes are placed. Returns how many splits were joined."""
    def text(p):
        b = p.get("body") or ""
        return "" if b.strip().lower() in ("", "none") else b

    joined = 0
    compounds = _book_compounds(parsed)
    for i in range(len(parsed) - 1):
        a = text(parsed[i]).rstrip()
        if not a:
            continue
        fn = _FOOTNOTE_BLOCK.search(a)
        main, foot = (a[:fn.start()].rstrip(), a[fn.start():]) if fn else (a, "")
        # only . ! ? (or an ellipsis), optionally closed by a quote or bracket,
        # end a sentence -- not ; or : ("ce petit prévôt, lui dis-je;" |
        # "entre vos mains, il deviendrait..." is one sentence, Jacob 26/27).
        # Nor a title before a name: "... le poids, MM." | "Anatole de La Forge
        # et Ranc." (Jacob 25/26; 13 such breaks in 11 books, 2026-10-09)
        honorific = re.search(_HONORIFIC_END, main) if main else None
        if not main or (re.search(r"[.!?\u2026][\u00bb\u201d\")\]\s]*\Z", main) and not honorific):
            continue
        # a plate leaf's caption line -- no page number, the page's only line,
        # short, no punctuation -- is not a sentence's start: Masiello 1887 pdf 448
        # ("Destro e mancino cavazione al fianco") took the opening of pdf
        # 450, whose sentence starts on pdf 443, six plate pages back. Nor is
        # a plate's list of labels, every line short and unpunctuated
        # (Masiello 1887 pdf 462, the saber's parts: «… Ghiera | Coccia» took
        # the opening of pdf 464, «taglio verso la punta …»)
        lines_a = [ln for ln in a.strip().split("\n") if ln.strip()]
        if (parsed[i].get("page_raw") or "").strip().lower() in ("", "none") \
                and not re.search(r"[,;:.]", a) \
                and (len(lines_a) == 1 and len(a.split()) <= 8
                     or len(lines_a) >= 3 and all(len(ln.split()) <= 6 for ln in lines_a)):
            continue
        last_line = main.rsplit("\n", 1)[-1]
        # a contents line (dotted leader to a page number) or a table row --
        # not an ellipsis in prose ("abbattimenti.... in voler essere")
        if re.search(r"(?:\.\s*){3,}\s*[0-9ivxlcIVXLC]+\s*\Z|\u2026\s*\d+\s*\Z|<t[dhr]|</table", last_line):
            continue
        j = i + 1
        if not text(parsed[j]).strip() and j + 1 < len(parsed):
            j += 1                                   # across one blank / plate page
        b = text(parsed[j])
        lead = re.match(r"(?:\s*\u27e6MARGIN:[^\u27e7]*\u27e7)*\s*", b).group(0)
        rest = b[len(lead):]
        # a quotation running on: French books open each of its lines with
        # "«" (Gomard, Baudry, Masiello; 23 pages, 2026-10-02), which the OCR
        # sometimes reads as a straight '"' (Gomard 76/77) -- that mark is
        # dropped where the two parts join, not a new sentence
        cont = re.match(r"[\u00ab\"][ \u00a0]*(?=[a-z\u00e0-\u00ff])", rest)
        if cont:
            rest = rest[cont.end():]
        if not re.match(r"[a-z\u00e0-\u00ff]", rest) and not (honorific and re.match(r"[A-Z\u00c0-\u00de]", rest)) \
                and not _capital_runs_on(main, rest):
            continue
        # the sentence's start on page i: after the last sentence end or paragraph break
        cut = max([m.end() for m in re.finditer(r"[.!?\u2026][\u00bb\u201d\")\]]*\s+|\n\s*\n", main)
                   if _not_after_title(main, m)] or [0])
        tail = main[cut:].strip()
        # its end on page j: up to the first sentence end or paragraph break
        m = next((x for x in re.finditer(r"[.!?\u2026][\u00bb\u201d\")\]]*(?=\s|\Z)|\n\s*\n", rest)
                  if _not_after_title(rest, x)), None)
        head = rest[:m.end()].strip() if m else rest.strip()
        if not tail or not head or "\u27e6" in tail + head or "<table" in tail + head:
            continue
        empties_i = not main[:cut].strip()
        empties_j = not rest[len(head):].strip() if m else True
        forward = len(tail.split()) <= len(head.split())
        if forward and empties_i:
            forward = False
        if not forward and empties_j:
            if empties_i:
                continue
            forward = True
        # a word broken across the break ("av-" | "versario"): rejoin it,
        # without the hyphen -- unless it is a compound the book prints
        # hyphenated elsewhere ("contre-" | "dégagement", La Boëssière)
        broken = re.search(r"[a-z\u00e0-\u00ff]-\Z", tail)
        if forward:                                  # sentence start -> next page
            main = main[:cut].rstrip()
            rest = _glue_broken(tail, rest, compounds) if broken else tail + " " + rest
        else:                                        # sentence end -> this page
            main = _glue_broken(main.rstrip(), head, compounds) if broken else main.rstrip() + " " + head
            rest = rest[m.end():].lstrip() if m else ""
        parsed[i]["body"] = main + ("\n\n" + foot.lstrip("\n") if foot else "")
        parsed[j]["body"] = (lead.rstrip() + "\n\n" if lead.strip() else "") + rest
        joined += 1
    return joined


# For joining split sentences: the author's footnotes from "(1)" / "*" to the
# end of the page, however many paragraphs (Florio's run to quotations and
# comments). Broader than _FOOTNOTE_TAIL, which the hyphen join keeps.
# A "* * *" section break is not a footnote marker: taking it for one hid
# the real split at the foot of the page (LaMarche, Spinnewyn, Tiboldi;
# 15 pages, 2026-10-02).
_FOOTNOTE_BLOCK = re.compile(r"\n[ \t]*\n[ \t]*(?:\(1\)|\*(?![ \t]*\*))[ \t][\s\S]*\Z")

# The author's footnotes closing a page's text: a blank line, then "(1) " or
# "* " -- everything from there to the end of the page. Not "1." -- that is
# as often a numbered paragraph (LaMarche, Spinnewyn).
_FOOTNOTE_TAIL = re.compile(r"\n[ \t]*\n[ \t]*(?:\(\d+\)|\*)[ \t](?:(?!\n[ \t]*\n[ \t]*[^(*\s])[\s\S])*\Z")
