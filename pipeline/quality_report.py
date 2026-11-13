#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Per-book review report: every page the pipeline itself has reason to
doubt, with the reason, by side-by-side pdf page number. No model, no GPU.

    python3 /app/quality_report.py <stem> [<stem> ...]   (or --all)

Writes `processed/<stem>_translated/<stem> — Review Report.md` (for you) and
`intermediate/quality_report.json` (for tools). Review the listed pages,
record decisions in books/<stem>.overrides.toml (manual III.5b), and use
`2_translate.py --pages` for pages that need retranslating.

Two levels: CHECK = likely wrong; NOTE = worth a glance. A check earns its
place only if it catches real problems without burying them -- each one
here was measured against pages found wrong by hand in batch-2 review.
"""
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

import _layout
import _overrides
import _pageio
import _translate as _tx
from _translate import clean_vlm_extraction
# negations: the rule is step 2's (_tx_checks.py: a try that loses one is retried);
# the report lists the pages where every try lost it
from _tx_checks import _NEG_EN, _NEG_FR, _NEG_IT, contains_too_much_non_english, lost_negations
from _tx_common import FN_HEADER, _strip_accents
from _tx_terms import house_italics
from _glossary_fr import TERM_RULES
from _glossary_it import ITALIC_RULES_IT, TERM_RULES_IT

CHECK, NOTE = "CHECK", "NOTE"
ACCEPTED = "ACCEPTED"      # marked "fine as is" on the review page (books/<stem>.review)

# "Still French?" for the report: French function words must clearly
# outnumber English ones. (step 2's contains_too_much_non_english() is a
# quick gate tuned to reject whole outputs; on single paragraphs it also
# fires on English with quoted French, names, and short headings.)
_FR = set("le la les des du de et est sont qui que dans pour avec une un sur pas ne se il "
          "elle au aux ce cette son sa ses leur leurs nous vous on en par plus".split())
_EN = set("the and of to is are in that with for on it by as be this from his their which "
          "was were one an at or not but have has its".split())
# Italian books: the same test against Italian function words (counted
# separately -- the two lists share "la", "le", "un" -- and the larger wins)
_IT = set("il lo la i gli le di da del della dei delle degli che non è sono per con una "
          "un uno nel nella nei si ma come più anche questo questa quello essere al alla".split())


def still_french(text: str, min_fr: int = 5) -> bool:
    w = [x.lower() for x in re.findall(r"[^\W\d_]+", re.sub(r"<i>.*?</i>|«[^»]*»|\"[^\"]*\"", " ", text))]
    fr = max(sum(x in _FR for x in w), sum(x in _IT for x in w))
    en = sum(x in _EN for x in w)
    return fr >= min_fr and fr > 2 * en

# Misspellings the model has produced repeatedly (fix_known_garbles() repairs
# new output; this catches output translated before the repair existed).
# Book-wide consistency: a French phrase rendered several ways across the
# book. Not an error on any one page -- a choice to make once ([terms] in
# the overrides file) and apply with `--pages` to the minority pages.
CONSISTENCY = [
    ("coups simples / composés", r"\bcoups?\s+(?:simples?|composés?)\b",
     r"\b(?:simple|compound)\s+(thrusts?|blows?|throws?|strikes?|attacks?|hits?)\b"),
    ("botte (de quarte …)", r"\bbottes?\s+de\s+\w+",
     r"\b(hits?|thrusts?|lunges?|finish(?:es)?)\s+(?:of|in|to)\s+(?:prime|seconde|tierce|quarte|quinte|sixte|septime|octave)\b"),
]
GARBLES = r"\b(?:thusts?|throughs|rapises?|riponses?|rompers?|rippostes?|(?-i:[A-Z]{2,}[a-z]{2,}[A-Z]*))\b"
META = re.compile(r"(?i)\b(?:here is the translation|translated text:|as an ai\b|"
                  r"i cannot translate|please provide the text|i'm sorry)")


def _body(tr: str) -> str:
    """English page without the footnote block and pipeline markers."""
    t = tr.split("\n" + FN_HEADER + "\n", 1)[0]
    return re.sub(r"(?m)^\[[^\]\n]*\]\s*$", "", t)


def _norm_title(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", re.sub(r"</?i>", "", s or ""))
    return re.sub(r"[^a-z]+", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def lead_title(body: str) -> str:
    """The first line of a page body if it stands alone as a title: short,
    followed by a blank line (or nothing), and either mostly capitals or a
    short sentence-case heading ("Du dégagement."). Else ""."""
    lines = [l.strip() for l in (body or "").strip("\n").split("\n")]
    lines = [l for l in lines if not l.startswith("⟦MARGIN")]
    if not lines or not lines[0]:
        return ""
    first = lines[0]
    if len(lines) > 1 and lines[1]:
        return ""                                     # runs on: a paragraph, not a title
    plain = re.sub(r"</?i>", "", first)
    letters = [c for c in plain if c.isalpha()]
    words = plain.split()
    if len(letters) < 4 or len(words) > 10 or plain.lower() == "none" \
            or re.search(r"[,;]$", plain) or plain.startswith("FIGURES"):
        return ""
    caps = sum(c.isupper() for c in letters) >= 0.6 * len(letters)
    heading = len(words) <= 6 and plain[:1].isupper() and plain.rstrip().endswith(".")
    return first if caps or heading else ""


def title_issues(raw_body: str, header: str, ex_body: str, tr: str,
                 own_header: str = "", captions: str = "") -> list:
    """A body title the OCR read that the page lost: dropped by the page
    parse, or dropped in translation. [(level, kind, detail)]. Added after
    14 Gomard chapter titles went missing (a margin-note copy check) and
    only a hand review caught it."""
    out = []
    t = lead_title(raw_body)
    if t:
        # printed twice (running head AND title below it, Gomard's chapter
        # openers): the OCR reported it as its own HEADER too -- then the
        # body copy must survive, the header alone doesn't count
        twice = _norm_title(own_header) == _norm_title(t)
        # a whole line near the top of the body (not a margin note, which
        # can repeat a title's words: "Valeur de la riposte."), the header
        # unless printed twice, or a figure caption (plate labels move there)
        lines = [l for l in (ex_body or "").strip("\n").split("\n")
                 if l.strip() and not l.lstrip().startswith("⟦MARGIN")][:6]
        have = {_norm_title(l) for l in lines} | {_norm_title(captions)} \
            | (set() if twice else {_norm_title(header)} | {_norm_title(x) for x in header.split("\t")})
        nt = _norm_title(t)
        if nt and nt not in have and not any(nt in c for c in (_norm_title(captions),) if c):
            out.append((CHECK, "title", f"the OCR read the title {t!r} at the top of the "
                        "page, but the page text lost it"))
    et = lead_title(ex_body)
    if et:
        tl = [l for l in _body(tr).strip("\n").split("\n")
              if l.strip() and not l.startswith("⟦MARGIN")]
        first = re.sub(r"</?i>", "", tl[0]) if tl else ""
        letters = [c for c in first if c.isalpha()]
        is_head = bool(letters) and (sum(c.isupper() for c in letters) >= 0.6 * len(letters)
                                     or (len(first.split()) <= 8 and first.rstrip().endswith("."))
                                     # a plate label joined to its caption on one
                                     # line, as decided ("Plate V. — Attack ...")
                                     or bool(re.match(r"(?i)(?:plate|pl\.)\s+[ivxlcdm\d]+\.?\s+—", first)))
        if not is_head:
            out.append((CHECK, "title", f"body title {et!r} is not a heading in the "
                        "translation (missing, or lowercased into the text)"))
    return out


def _words(s: str) -> int:
    return len(re.findall(r"[^\W\d_]+", re.sub(r"</?i>", "", s)))


# Latin quotations the model left as printed (Masiello pdf 39, 49; a dozen in
# Florio): a sentence with two or more words found only in Latin, and at
# least as many of them as common English words
_LATIN = re.compile(r"\b(?:et|est|quod|atque|enim|sed|cum|vel|nunc|quem|quae|quam|sunt|esse|nec|ab|"
                    r"ad|qui|ex|tamen|autem|ergo|igitur|ubi|ita|apud|mihi|tibi|nobis|vobis|eius|"
                    r"eorum|omnia|omnes)\b|\b[^\W\d_]{3,}(?:orum|arum|ibus|untur|antur|entur)\b", re.I)
_ENGLISH = re.compile(r"\b(?:the|and|of|to|is|was|that|with|which|his|it|be|by|for|this|he|they|"
                      r"from)\b", re.I)


def latin_lines(body: str) -> list:
    out = []
    for ln in re.split(r"\n|(?<=[.;:])\s+", body or ""):
        plain = re.sub(r"<[^>]+>", "", ln)
        if len(re.findall(r"[^\W\d_]+", plain)) < 4:
            continue
        lat = len(_LATIN.findall(plain))
        if lat >= 2 and lat >= len(_ENGLISH.findall(plain)):
            out.append(plain.strip()[:70])
    return out


def caption_misreads(figs: dict, ex: list) -> list:
    """Plate captions with a word that appears nowhere else in the book but
    is one or two letters away from a word the book uses often: an OCR
    misread of an engraved or sideways caption (Masiello 1887: "Segamento"
    for Legamento, "Pavata" for Parata, "Bolla" for Botta -- 37 of its 39
    plates were misread and only a scan-by-scan check found them).
    [(scan, caption, word, likely word)]. Inflections of a book word, line
    names and short capitals (FIG, TAV) are not flagged."""
    vocab = defaultdict(int)
    for p in ex:
        for w in re.findall(r"[^\W\d_]{4,}", p.split("BODY_TEXT:", 1)[-1]):
            vocab[w.lower()] += 1
    common = [w for w, c in vocab.items() if c >= 3]
    lines = set("prime seconde tierce quarte quinte sixte septime octave prima seconda terza "
                "quarta quinta sesta settima ottava prof chap figs".split())
    out = []
    for k, fl in figs.items():
        for f in fl:
            if f.get("kind") != "plate" or f.get("decided"):
                continue
            cap = re.sub(r"\s+--\s.*", "", f.get("caption") or "")
            for w in re.findall(r"[^\W\d_]{4,}", cap):
                lw = w.lower()
                if vocab[lw] > 2 or lw in lines or (w.isupper() and len(w) <= 5):
                    continue
                def kin(c):
                    n = len(os.path.commonprefix([c, lw]))
                    return n >= 4 and n >= len(c) - 2 and n >= len(lw) - 2
                if any(kin(c) for c in common if c[:3] == lw[:3]):
                    continue
                near = [c for c in common if abs(len(c) - len(lw)) <= 2 and c[0] == lw[0]
                        and _edits(c, lw) <= 2] or \
                       [c for c in common if abs(len(c) - len(lw)) <= 1 and _edits(c, lw) <= 1]
                if near:
                    out.append((int(k) + 1, cap, w, max(near, key=lambda c: vocab[c])))
                    break
    return out


def translation_issues(src: str, t: str) -> list:
    """The per-page checks on a translation: [(level, kind, detail)].
    `src` is the page's French body, `t` its translation.txt entry. Shared
    by the review report and the translation model selection test
    (evals/translation_model_test.py)."""
    out = []
    body = _body(t)
    if t.lstrip().startswith("[low-confidence translation"):
        out.append((CHECK, "translation", "flagged low-confidence by step 2's quality gate"))
    if t.lstrip().startswith("[translation unavailable"):
        out.append((CHECK, "translation", "no usable translation -- the French is shown instead"))
    if t.lstrip().startswith("[oversized OCR page"):
        out.append((CHECK, "ocr", "OCR ran away (repetition); only the start was translated"))
    if META.search(body):
        out.append((CHECK, "translation", f"model chatter in the text: "
            f"{META.search(body).group(0)!r}"))
    m = re.search(GARBLES, body, re.I)
    if m:
        out.append((CHECK, "wording", f"misspelling the model is known for: {m.group(0)!r}"))
    for fr, wrong, rule in _rules:
        if re.search(fr, src, re.I):
            w = re.search(wrong, re.sub(r"<i>.*?</i>", "", body), re.I)
            if w:
                out.append((CHECK, "wording", f"{w.group(0)!r} against the glossary ({rule})"))
    if _rules is TERM_RULES_IT:
        for fr, wrong, rule in ITALIC_RULES_IT:
            w = re.search(fr, src, re.I) and re.search(wrong, body, re.I)
            if w:
                out.append((CHECK, "wording", f"{w.group(0)!r} kept in Italian, against the "
                            f"glossary ({rule})"))
    for line in latin_lines(body):
        out.append((CHECK, "translation", f"a line left untranslated, in Latin or another "
                    f"language (rule 0: translate it, with an \"… in the original\" note): {line!r}"))
        break
    # one of the author's footnotes missing from the English (the count is
    # step 2's note_bodies: a marker opening a line, or any marker in the
    # footnote block, followed by the note's first word)
    def notes(x):
        body, _, block = (x or "").partition("___FOOTNOTES___")
        mark, word = r"\((?:\d{1,2}|\*{1,3})\)", r"[ \t.]*(?:<i>|[«“\"])?[^\W\d_]"
        return (len(re.findall(r"(?m)^[ \t]*" + mark + word, body))
                + len(re.findall(mark + word, block)))
    if notes(t) < notes(src):
        out.append((CHECK, "translation", f"a footnote of the source is missing "
                    f"({notes(src)} in the source, {notes(t)} in the English)"))
    # leftover French: a whole paragraph the model didn't translate
    for para in re.split(r"\n\s*\n", body):
        if _words(para) >= 8 and still_french(para):
            out.append((CHECK, "translation", "paragraph left (mostly) untranslated: "
                f"{para.strip()[:60]!r}"))
            break
    # length: an omission or a runaway, on pages with enough text to judge
    # whole page incl. the footnote block: the author's own footnotes
    # move there, so body-only would read as an omission
    # tables are left to the scan, images are placeholders: not text
    sw = _words(re.sub(r"(?is)<table\b.*?(?:</table\s*>|\Z)|!\[[^\]]*\]\([^)]*\)", " ", src))
    tw = _words(re.sub(r"(?m)^\[[^\]\n]*\]\s*$|^___FOOTNOTES___$|\[margin note\]", "", t))
    if sw >= 40 and not t.lstrip().startswith("["):
        r = tw / sw
        if r < 0.7:
            out.append((CHECK, "translation", f"English is {r:.0%} the length of the "
                f"French ({tw} vs {sw} words) -- something may be missing"))
        elif r > 1.8:
            out.append((NOTE, "translation", f"English is {r:.0%} the length of the "
                f"French ({tw} vs {sw} words) -- repetition or additions?"))
    lab = re.match(r"\s*(?:planche|pl\.|fig\.?|figure)\s+([IVXLC\d]+)\b", src, re.I)
    if lab and not t.lstrip().startswith("[") and not re.match(
            r"\s*(?:<i>)?(?:plate|pl\.|fig\.?|figure)\s+" + lab.group(1) + r"\b", t, re.I):
        out.append((CHECK, "translation", f"the page's label {lab.group(0).strip()!r} "
            "is missing from the English"))
    n_src, n_en = src.count("⟦MARGIN:"), t.count("⟦MARGIN:")
    if n_en < n_src:
        out.append((CHECK, "margin note", f"{n_src - n_en} of the page's {n_src} margin "
                    "note(s) missing from the English"))
    if "[margin note]" in t:
        out.append((NOTE, "layout", "a paragraph was moved to the notes as a margin note "
            "(heuristic) -- confirm it is one"))
    return out


def _plainp(p: str) -> str:
    return re.sub(r"\W+", " ", re.sub(r"<[^>]+>", "", p)).strip().lower()


def text_marks(src: str, t: str) -> list:
    """Marks left in the English text: [(level, kind, detail)]. Each was
    found in a book's English by the reviewer and missed by every other
    check (decision-page review, 2026-11-05). Report only -- not part of
    translation_issues(), so the translation model test's scores stay put.
    `src` is the page's source body, `t` its translation.txt entry."""
    out = []
    body, _, block = t.partition(FN_HEADER)
    # a paragraph printed twice in a row that the source prints once: a
    # [[fix]] that added text again on every run (Kirchhoffer scan 80,
    # LaMarche scan 4) or a model loop. The source can repeat a row on
    # purpose (Masiello 1887's saber tables), so only the excess counts.
    def twice(x):
        ps = [_plainp(p) for p in re.split(r"\n\s*\n", x) if p.strip()]
        return [a for a, b in zip(ps, ps[1:]) if len(a) >= 40 and a == b]
    rep = twice(body)
    if len(rep) > len(twice(src)):
        out.append((CHECK, "repeated", f"a paragraph printed twice in a row, once in the "
                    f"source: {rep[-1][:60]!r}"))
    # a translator's note (†N) whose mark is nowhere on the page, and the
    # reverse
    notes = re.findall(r"(?m)^†(\d+)\b", block)
    marks = set(re.findall(r"†(\d+)(?!\d)", body)) | set(re.findall(r"(?m)(?<=\S)†(\d+)(?!\d)", block))
    for n in sorted(set(notes) - marks, key=int):
        out.append((CHECK, "note", f"translator's note †{n} has no †{n} mark in the text"))
    for n in sorted(marks - set(notes), key=int):
        out.append((CHECK, "note", f"mark †{n} in the text, but no note †{n}"))
    for line in t.split("\n"):
        s = line.strip()
        if "|" in s or "}" in s:                   # a table or brace row
            continue
        if re.fullmatch(r"[«»“”\"„]+", s):
            # a lone ditto mark is printed in lists (layout rule); here it
            # stands on a line of its own -- usually a stray from the OCR
            out.append((NOTE, "punctuation", f"a line that is only {s!r} -- a ditto mark "
                        "the book prints, or a stray? Check the scan"))
        elif re.match(r"[:;,]\s|\.\s(?!\.)", s):
            # a page opening "; he has the choice" (Edom scan 217), a stray
            # ". " from the OCR (Prévost-Jollivet scan 140): a sentence not
            # joined across the break, or a mark the book doesn't print
            out.append((CHECK, "punctuation", f"a line starts with {s[0]!r}: {s[:50]!r} -- "
                        "a stray mark, or a sentence split from the page before"))
    # the OCR's reading of a note mark (1): "(!)", "(i)", "(l)" -- only
    # where the page has note (1) and no (1) in the text, since an author's
    # "(!)" is real (Edom scan 273)
    if re.search(r"(?m)^\(1\)", block) and "(1)" not in body:
        m = re.search(r"\((?:!|i|l|I)\)", body)
        if m:
            out.append((CHECK, "note", f"{m.group(0)!r} in the text where note (1) has no mark "
                        "-- the OCR's reading of (1)?"))
    # guillemets read as bullets: "formula alone: • At your service, sir. •"
    # (Prévost-Jollivet scans 26, 217, 292); a bullet opening a line is a list
    m = re.search(r"[^\s•][ \t]*•", t)
    if m:
        out.append((CHECK, "punctuation", f"'•' inside a line: "
                    f"{t[max(0, m.start() - 25):m.end() + 15]!r} -- guillemets read as bullets?"))
    # a word still split at a line end of the scan: "supple- ness", "per- form"
    m = re.search(r"\b([A-Za-z]{2,})- (?!(?:and|or|nor|to|as|through)\b)([a-z]{2,})\b",
                  re.sub(r"<[^>]+>", "", body))
    if m:
        out.append((CHECK, "wording", f"a word split by a hyphen: {m.group(0)!r} -- join it"))
    return out


def _edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 9
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def name_variants(meta: dict, tr: list) -> list:
    """The author's name misspelt by the OCR: [(scan, level, detail)]. The
    names come from meta.json (author, title_full); a word one or two
    letters off one of them, or the same letters in odd case, is listed
    (Kirchhoffer "LeCuyer", Masiello 1887 "Mosiello", Prévost-Jollivet
    "JOLIVET", Barbasetti "BARBASETTE" -- all found by eye, 2026-11-05).
    Accents don't count (meta.json's "Lécuyer" is printed "Lecuyer"). Kept
    quiet: only a capitalised word, at most twice in the book, never
    written in lower case there (March, Parts, prevent), and rarer than
    the name itself (meta.json misspelt: Vauresmont)."""
    import unicodedata
    deacc = lambda s: "".join(c for c in unicodedata.normalize("NFKD", s)
                              if not unicodedata.combining(c)).lower()
    names = {w for w in re.findall(r"[^\W\d_]{5,}", f"{meta.get('author') or ''} "
                                   f"{meta.get('title_full') or ''}") if w[0].isupper()}
    seen = defaultdict(list)
    for i, t in enumerate(tr):
        for w in re.findall(r"[^\W\d_]+", re.sub(r"<[^>]+>", "", t or "")):
            seen[w].append(i + 1)
    lower = {deacc(w) for w in seen if w.islower()}
    out = []
    for n in sorted(names):
        nl = deacc(n)
        count = sum(len(v) for w, v in seen.items() if deacc(w) == nl)
        for w, v in seen.items():
            wl = deacc(w)
            if len(w) < 5 or not w[0].isupper() or wl[0] != nl[0] or len(v) > 2 \
                    or len(v) >= count or wl in lower:
                continue
            d = _edit_distance(wl, nl)
            odd_case = d == 0 and not (w.isupper() or w[1:] == w[1:].lower())
            if odd_case or 0 < d <= (2 if len(nl) >= 7 else 1):
                out.append((v[0], CHECK if d <= 1 else NOTE,
                            f"{w!r} -- the name {n!r} misread? ({count} time(s) as {n!r} in the "
                            "book) Check the scan"))
    return out


def english_only_issues(tr: list, figs: dict, tables: dict) -> list:
    """What the English-only PDF shows that the side-by-side doesn't:
    [(scan, level, kind, detail)]. 2026-11-05 decision-page review: a
    caption printed again as a line of text (Kirchhoffer pdf 52 "Diagram
    A"), a figure number misread ("Fig. 41" between 10 and 12, André scan
    53), a table with no English (step 3 shows the scan's table instead)."""
    import _render
    out, seq = [], []
    for k in sorted(figs, key=int):
        i = int(k)
        body = _body(tr[i]) if i < len(tr) else ""
        # a plate page whose text is only its caption is printed once: step
        # 3 uses the text as the caption (caption_only_page, <= 20 words)
        lines = {_plainp(l) for l in body.split("\n") if l.strip()} \
            if len(re.sub(r"<[^>]+>", "", body).split()) > 20 else set()
        for f in figs[k]:
            c = (f.get("caption_en") or "").strip()
            if not c or _render.describes_picture(f.get("caption") or ""):
                continue
            if len(_plainp(c)) >= 6 and _plainp(c) in lines:
                # the book can print a heading in the same words as the
                # plate's caption (Masiello 1887 "Sword in line"): a note
                out.append((i + 1, NOTE, "caption", f"caption {c[:50]!r} is also a line of the "
                            "text: the English-only PDF may print it twice -- check that page, and the scan"))
            m = re.match(r"\s*(?:<i>)?fig(?:ure|\.)?\s*(\d+)\b(?!\s*(?:bis|ter)\b)", c, re.I)
            if m:
                seq.append((i + 1, int(m.group(1)), c))
    for (s0, a, _), (s1, b, c), (s2, d, _) in zip(seq, seq[1:], seq[2:]):
        if a < d and d - a <= 4 and not a < b < d:
            out.append((s1, CHECK, "caption", f"{c[:40]!r}: figure {b} between {a} and {d} -- "
                        "a misread number? Check the scan; correct it with [pages.N] caption"))
    for i, t in enumerate(tr):
        n = (t or "").count("[[TABLE]]")
        if n:
            good = sum(1 for h in ((tables.get(str(i)) or {}).get("en") or []) if h)
            if good < n:
                out.append((i + 1, CHECK, "table", f"{n - good} of the page's {n} table(s) have no "
                            "English: the English-only PDF shows the scan's table, untranslated"))
    return out


def fix_status(tr: list, ov: dict, french: bool, edited=()) -> list:
    """Each [[fix]] / [[table_fix]] against the text now: [(scan, what,
    status)]. "pending" = apply_overrides.py has not run since it was added;
    "not found" = neither its find nor its result is on the page (the page
    was retranslated, or the fix was written wrong) -- the decision is not
    in the PDFs. 21 approved answers were found missing this way only by
    hand (2026-11-05); the warning step 2 prints was ignored for weeks."""
    out = []
    what = lambda f: (f"prepend {f['prepend'][:50]!r}" if "prepend" in f
                      else f"{f['find'][:50]!r} → {f['replace'][:50]!r}")
    # in file order, on one copy, as step 2 applies them: a later fix may
    # take back an earlier one (Jacob-André scan 141: "halfway" -> "with a
    # half-lunge", then the decision changed back)
    pages, touched = list(tr), set()
    # the pages' fixes read in the book-wide edits' wording, on the pages
    # with those edits made, as step 2 applies them ([[book_fix]],
    # _overrides.apply_fixes); a book fix not made yet is pending, not a
    # page fix gone missing
    bf = ov.get("book_fix", [])
    if bf:
        _overrides.apply_fixes(pages, dict(ov, fix=[], pages={}, table_fix=[]))
    for f in _overrides.book_worded(_tx._house_fixes(ov, french)["fix"], bf):
        s = f["scan"]
        if s > len(tr) or s in edited:            # a hand edit replaces the page
            continue
        applied, missing = _overrides.apply_fixes(pages, dict(ov, fix=[f], pages={}, table_fix=[], book_fix=[]))
        if missing:
            out.append((s, what(f), "not found"))
        touched.add(s)
    hi = house_italics
    for s in sorted(touched):
        if hi(pages[s - 1], french) != tr[s - 1]:
            out.append((s, "on this page", "pending"))
    for f in bf:                                  # a book-wide edit that matches nowhere
        if not any(f["find"] in t or f["replace"] in t for t in tr):
            out.append((0, f"{f['find'][:50]!r} → {f['replace'][:50]!r}", "not found"))
        elif any(f["find"] in t for t in tr):
            out.append((0, f"{f['find'][:50]!r} → {f['replace'][:50]!r}", "pending"))
    # a fix undone by a later one: the earlier one is clutter
    fx = [f for f in ov["fix"] if "find" in f]
    for k, a in enumerate(fx):
        for b in fx[k + 1:]:
            if a["scan"] == b["scan"] and a["replace"] == b["find"] and b["replace"] == a["find"]:
                out.append((a["scan"], what(a), "taken back"))
    return out


def card_id(x: dict) -> str:
    """A report item's id on the review page (review_page.py) and in
    books/<stem>.review: the same item keeps its id from run to run."""
    import hashlib
    d = x["detail"] if isinstance(x["detail"], str) else json.dumps(x["detail"], sort_keys=True)
    return hashlib.sha1(f"{x['scan']}|{x['kind']}|{d}".encode()).hexdigest()[:10]


def page_map(folder: str, n: int) -> dict:
    """scan page -> side-by-side pdf page, read off the built PDF (a page
    that overflows adds a '(cont.)' sheet, shifting everything after it)."""
    pdfs = glob.glob(f"{folder}/*Side by Side*.pdf")
    if not pdfs:
        return {}
    try:
        import fitz
    except ImportError:
        return {}
    d = fitz.open(pdfs[0])
    out, scan = {}, 0
    for p in range(1, len(d)):                          # sheet 1 is the cover
        r = d[p].rect
        head = d[p].get_text(clip=fitz.Rect(r.width / 2, 0, r.width, 60)).lstrip()
        # anywhere in the top strip: the running head can sit before the
        # page number on that line (the book's places, 2026-10-18)
        if not re.search(r"\(cont\.\)", head):
            scan += 1
            out[scan] = p + 1
    return out if scan == n else {}


_rules = TERM_RULES      # the book's language's list, set by check_book()


def _edits(a: str, b: str) -> int:
    """Letters to change, add or drop to turn a into b."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# numbers in words: heads that differ in one ("QUARTO" / "QUINTO", "FIFTH" /
# "SIXTH") are different heads, not a misreading
_ORDINALS = set("""PRIMO PRIMA SECONDO SECONDA TERZO TERZA QUARTO QUARTA QUINTO QUINTA
SESTO SESTA SETTIMO SETTIMA OTTAVO OTTAVA NONO NONA DECIMO DECIMA UNDICESIMO DODICESIMO
PREMIER PREMIÈRE PREMIERE DEUXIÈME DEUXIEME SECOND SECONDE TROISIÈME TROISIEME QUATRIÈME
QUATRIEME CINQUIÈME CINQUIEME SIXIÈME SIXIEME SEPTIÈME SEPTIEME HUITIÈME HUITIEME NEUVIÈME
NEUVIEME DIXIÈME DIXIEME FIRST THIRD FOURTH FIFTH SIXTH SEVENTH EIGHTH NINTH TENTH
ELEVENTH TWELFTH ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN UNO DUE TRE QUATTRO
CINQUE SEI SETTE OTTO NOVE DIECI UN DEUX TROIS QUATRE CINQ SEPT HUIT NEUF DIX""".split())


def head_variants(where: dict, hmap: dict) -> list:
    """Running heads shown one or two letters apart are usually one head the
    OCR misread: "FERDINANDO MASIEMLO" / "MASIELLO" / "MASIello". Compared
    as shown in the English (headers.json), so a misreading the translation
    already smoothed over is no issue, and punctuation-only differences
    ("INTRODUCTION" / "INTRODUCTION.") are ignored; capitals are not (they
    show). Which spelling is right
    is the reviewer's call -- the commoner one can be the misreading
    (Barbasetti: "BARBASSETTI" on 63 pages, right on 11) -- so this only
    reports, once per group; the decision is a [headers] line in the
    overrides file. Heads differing in a number are different heads."""
    def norm(h):
        h = h.replace("\u2019", "'").replace("\t", " ")
        return re.sub(r"\s+", " ", re.sub(r"[.,:;!?]", "", h)).strip()
    shown = defaultdict(list)                     # English as shown -> scans
    for h, scans in where.items():
        shown[(hmap.get(h) or h).strip()] += scans
    by_norm = defaultdict(list)
    for en, scans in shown.items():
        by_norm[norm(en)] += scans
    keys = sorted(by_norm, key=lambda k: -len(by_norm[k]))
    def nums(h):                                  # numbers, in figures or words
        return re.findall(r"\d+|\b[IVXLC]+\b", h) + \
            [w for w in re.findall(r"[A-ZÀ-Ý]+", h.upper()) if w in _ORDINALS]
    group = {k: k for k in keys}                  # each variant -> commonest
    for x, k in enumerate(keys):
        for c in keys[:x]:
            if group[c] != c or len(min(k, c, key=len)) < 6 or nums(k) != nums(c):
                continue
            if k.upper() == c.upper() and not any(
                    re.search(r"[A-Z]{2,}[a-z]|[a-z][A-Z]", x) for x in (k, c)):
                continue                          # "THE ÉPÉE" / "The Épée": style, not a misreading
            if abs(len(k) - len(c)) <= 2 and _edits(k.upper(), c.upper()) <= 2:
                group[k] = c
                break
    out = []
    for c in keys:
        members = [k for k in keys if group[k] == c]
        if len(members) < 2:
            continue
        variants = "; ".join(f"{k!r} ({len(by_norm[k])})" for k in members)
        first = min(s for k in members[1:] for s in by_norm[k])
        out.append((CHECK, first, f"running head shown in {len(members)} spellings: "
                    f"{variants} -- likely one head, misread or translated two ways; "
                    "set one English for every spelling under [headers]"))
    return out


def _ocr_head(raw_page: str) -> bool:
    m = re.search(r"(?im)^[ \t]*HEADER[ \t]*:[ \t]*(.*)$", raw_page or "")
    v = (m.group(1).strip().strip("[]") if m else "").lower()
    return bool(v) and v not in ("none", "n/a", "-")


def folio_shaped_heads(pages: list, raw: list) -> tuple:
    """(pages, of) when the book looks like one that prints no running heads
    and the detector's page_header box is its page number; () otherwise.
    Counted over pages whose confident head boxes are all at most ~3
    characters wide (pixel width <= 5x height): the OCR reads no head on
    at least 80% of them. Measured 2026-10-05 over 19 books: Bruno 94%,
    Florio 90%, Lambertini 86% (all print only page numbers, scan-checked);
    every book with real running heads 66% or less (LaMarche)."""
    small = none = 0
    for i, p in enumerate(pages):
        st = [b for b in p["boxes"] if b[0] == "page_header" and b[1] >= 0.5 and _layout._horiz(b)]
        if not st or i >= len(raw) or not p.get("w"):
            continue
        if all(_layout._wid(b) * p["w"] <= 5 * _layout._hgt(b) * p["h"] for b in st):
            small += 1
            none += not _ocr_head(raw[i])
    return (none, small) if small >= 20 and none >= 0.8 * small else ()


# False friends: a source word rendered by its English look-alike, which
# means something else (Kirchhoffer pdf 63 «ignoraient» -> "ignored" for
# "did not know"; pdf 60 «défiant» -> "defying" for "wary"). Flagged when the
# page's source has the word and its English the look-alike. (source, English
# look-alike, usual sense) -- French and Italian. Not demander -> demand
# ("demander réparation" is "demand satisfaction") nor attendre -> attend:
# on a dry run (2026-10-19) those were right or unrelated.
FALSE_FRIENDS = [
    (r"\bignor(?:er|e|es|ait|aient|ant|é|ée|ent|ons|ez|ava|avano|are|ano|ando)\b",
     r"\bignor(?:e|ed|es|ing)\b", "not know, be unaware of"),
    (r"\b(?:dé|mé)fiant", r"\bdef(?:ying|iant)\b", "wary, distrustful"),
    (r"\b(?:actuellement|attualmente)\b", r"\bactually\b", "at present, now"),
    (r"\b(?:éventuellement|eventualmente)\b", r"\beventually\b", "possibly, if need be"),
    (r"\bassist(?:er|e|ait|aient|ent|ant|é)\s+à|\bassist(?:ere|eva|ono|ito)\s+a", r"\bassist(?:s|ed|ing)?\b",
     "attend, be present at"),
    (r"\bsensib(?:le|les|ile|ili)\b", r"\bsensible\b", "perceptible, noticeable, sensitive"),
    (r"\bprét(?:end|endre|endent|endait)|\bpretend(?:e|ere|ono)\b", r"\bpretend(?:s|ed|ing)?\b", "claim"),
    (r"\bachev(?:er|ez|é|ée|ait|ant)\b", r"\bachiev(?:e|es|ed|ing)\b", "finish, complete"),
    (r"\bparenti\b", r"\bparents\b", "relatives"),
    # Italian, from the meaning checks (2026-11-02): Masiello 1887 pdf 98,
    # 113, 116, 117, 119, 360, 725, 727, 737 and Barbasetti pdf 191; the same
    # words turned up in Descalzi, Florio, Bruno, Masiello 1902. Not valore
    # -> value: "artistic value", "a debatable value" were right.
    (r"\bsoggezion", r"\bsubjection\b", "awe, self-consciousness; tenere in soggezione = keep in check"),
    (r"\balternativ[oi]\b", r"\balternative\b", "alternating"),
    (r"\bpreventiv[oaie]\b", r"\bpreventive\b", "prior, made in advance"),
    (r"\bgentilizi", r"\bgentile\b", "of a (noble) family"),
    # of a target or body part only: "discover whether he parries" is right
    (r"\bscopr(?:ire|e|ono|endo|irsi|endosi)\b",
     r"\bdiscover(?:s|ing|ed)?\s+(?:a|the|his|some|one's|its|their|the same)?\s*"
     r"(?:part|target|line|chest|body|flank|himself|arm|breast|side)", "uncover, expose"),
    (r"\bpietos[oaie]\b", r"\bpitiful\b", "merciful, compassionate"),
    (r"\binconsult[oaie]\b", r"\bunconsulted\b", "rash, ill-considered"),
    (r"\bsi conviene\b|\bconviene di\b", r"\bagrees? (?:to|on)\b", "one has to, it is fitting"),
    (r"\bfanal[ei]\b", r"\blanterns?\b", "street lamps"),
    (r"\bstatut[oi]\b", r"\bstates\b", "statutes"),
    (r"\barringo\b", r"\btrade\b", "arena, lists"),
    # arme blanche / arma bianca (André, Blengini, Cordelois, Grisier, Lambertini)
    (r"\barmes? blanches?\b|\barm[ai] bianc[ah]e?\b", r"\bwhite (?:weapons?|arms?)\b",
     "edged / bladed weapon, or \"cold steel\" in a set phrase -- never white"),
]


def false_friends(src: str, en: str) -> list:
    out = []
    for fr, look, sense in FALSE_FRIENDS:
        f = re.search(fr, src or "", re.I)
        e = re.search(look, re.sub(r"<[^>]+>", "", en or ""), re.I)
        if f and e:
            out.append(f"«{f.group(0)}» rendered as its look-alike {e.group(0)!r} -- usually means "
                       f"\"{sense}\"")
    return out


def repeated_notes(tr: list) -> list:
    """A footnoted term's note ("†1 Italian: legamento") is given once per
    book. Returns (scan, note number, label, term, scan of the first one)
    for each later page that repeats it -- retranslated pages of an Italian
    book did, before notes_already_given() read the book's label (Masiello
    1902 fioretto / sciabola, 2026-11-06)."""
    first, out = {}, []
    for i, t in enumerate(tr):
        for m in re.finditer(r"(?m)^†(\d+) (French|Italian): (.+?)\s*$", t or ""):
            key = _strip_accents(m.group(3).lower())
            if key in first:
                out.append((i + 1, m.group(1), m.group(2), m.group(3), first[key]))
            else:
                first[key] = i + 1
    return out


def check_book(folder: str) -> list:
    global _rules
    _rules = TERM_RULES_IT if _tx.book_language(
        os.path.basename(folder)[:-len("_translated")]) == "Italian" else TERM_RULES
    I = f"{folder}/intermediate"
    ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
    tr = _pageio.split_pages(open(f"{I}/translation.txt", encoding="utf-8").read())
    pn = open(f"{I}/page_numbers.txt", encoding="utf-8").read().split("\n")
    import _render
    # a page the reviewer left out (library / digitiser text) is not in the PDFs
    tr = ["[Empty page]" if t == _render.LEFT_OUT else t
          for t in _render.apply_left_out(tr, os.path.basename(folder)[:-len("_translated")])]
    hmap = _render.header_map(I)          # with two-part heads either way round
    figs = json.load(open(f"{I}/figures.json", encoding="utf-8")) \
        if os.path.exists(f"{I}/figures.json") else {}
    issues = []
    raw = _pageio.split_pages(open(f"{I}/raw_ocr.txt", encoding="utf-8").read()) \
        if os.path.exists(f"{I}/raw_ocr.txt") else []

    def add(scan, level, kind, detail):
        issues.append({"scan": scan, "level": level, "kind": kind, "detail": detail})

    if len(ex) != len(tr):
        add(0, CHECK, "alignment", f"extracted.txt has {len(ex)} pages, translation.txt "
            f"{len(tr)} -- translations are attached to the wrong scans")
        return issues

    for i, (e, t) in enumerate(zip(ex, tr)):
        s = i + 1
        src = clean_vlm_extraction(e) if e.strip() else ""
        body = _body(t)
        if "VLM Error" in e:
            add(s, CHECK, "ocr", "the OCR model failed on this page")
        for detail in false_friends(src, body):
            add(s, CHECK, "false friend", detail)
        lost = lost_negations(src, t, _rules is TERM_RULES_IT)
        if lost >= 2:
            add(s, CHECK, "negation", f"the source has {lost} more negations than the English "
                "-- a \"not\" lost, or a passage left out")
        for level, kind, detail in translation_issues(src, t) + text_marks(src, t):
            add(s, level, kind, detail)
        if raw and i < len(raw):
            rb = raw[i].split("BODY_TEXT:", 1)[1] if "BODY_TEXT:" in raw[i] else ""
            h = (re.search(r"(?m)^HEADER: (.*)$", e) or [None, ""])[1].strip()
            eb = e.split("BODY_TEXT:", 1)[1] if "BODY_TEXT:" in e else ""
            oh = re.search(r"(?im)^[ \t]*HEADER[ \t]*:[ \t]*(.*)$", raw[i])
            caps = " ".join(f.get("caption") or "" for f in figs.get(str(i), []))
            for level, kind, detail in title_issues(rb, h, eb, t, oh.group(1) if oh else "", caps):
                add(s, level, kind, detail)
        if i and src.strip() and len(src) > 80:
            prev = clean_vlm_extraction(ex[i - 1]) if ex[i - 1].strip() else ""
            norm = lambda x: re.sub(r"\W+", "", x.lower())[:300]
            if norm(src) == norm(prev):
                add(s, NOTE, "scan", f"same text as scan {s - 1} -- the source scanned "
                    "this leaf twice")
        if src.count("[?]") >= 3:
            add(s, NOTE, "ocr", f"{src.count('[?]')} illegible spots marked by the OCR")
        for f in figs.get(str(i), []):
            cap = (f.get("caption") or "").strip()
            if cap and not f.get("decided") and _render.describes_picture(cap):
                # not printed: the OCR model described the picture. The PDFs
                # leave it out; if the book does print a caption here, set it
                add(s, NOTE, "caption", f"recorded caption left out -- it describes the "
                    f"picture ({cap[:50]!r}); if a caption is printed, set [pages.{s}] caption")
            elif cap and not (f.get("caption_en") or "").strip():
                add(s, CHECK, "caption", f"plate caption not translated: {cap[:50]!r}")

    # the second, caption-only reading of the page (verify_captions.py):
    # a printed caption step 1 didn't record, or recorded differently
    _cr = f"{I}/caption_read.json"
    if os.path.exists(_cr):
        import verify_captions as _vc
        try:
            _dec = {p for p, v in _overrides.load(os.path.basename(folder)[:-len("_translated")])
                    ["pages"].items() if "caption" in v or "caption_en" in v}   # either is a decision
        except _overrides.OverridesError:
            _dec = set()
        for s_, d in json.load(open(_cr, encoding="utf-8")).items():
            s_ = int(s_)
            if s_ in _dec:
                continue
            kept = [r for r in d.get("recorded", []) if not _render.describes_picture(r)]
            head = (re.search(r"(?m)^HEADER: (.*)$", ex[s_ - 1]) or [None, ""])[1] if s_ <= len(ex) else ""
            found = [" ".join(f_.split()) for f_ in d.get("found", []) if not _vc.is_noise(f_, head)]
            if not found:
                continue
            # one item per page: a plate with several captioned figures
            # (Enrichetti scan 143) is one thing to fix, not eight
            shown = " | ".join(found)
            shown = shown if len(shown) <= 110 else shown[:107] + "..."
            if kept:
                # usually the recorded one is wrong (Masiello 1887 scan 487:
                # "Ensuite de lever" for the sideways "Invito di terza")
                add(s_, CHECK, "caption", f"recorded caption {kept[0][:45]!r} differs from what "
                    f"is printed: {shown!r} -- set [pages.{s_}] caption to the printed one")
            else:
                add(s_, CHECK, "caption", f"caption printed here but not recorded: {shown!r} "
                    f"-- set [pages.{s_}] caption if it belongs to the picture")

    # terminology consistency (book level, reported separately)
    for name, fr, en in CONSISTENCY:
        seen = defaultdict(list)
        for i, (e, t) in enumerate(zip(ex, tr)):
            if re.search(fr, clean_vlm_extraction(e) if e.strip() else "", re.I):
                for m in re.finditer(en, re.sub(r"</?i>", "", _body(t)), re.I):
                    seen[m.group(1).lower().rstrip("s").replace("finishe", "finish")].append(i + 1)
        if len(seen) > 1:
            issues.append({"scan": 0, "level": "TERMS", "kind": name,
                           "detail": {k: sorted(set(v)) for k, v in seen.items()}})

    # formula notation the OCR wrote in LaTeX and plain_math() (step 1) could
    # not turn into plain text
    for i, t in enumerate(tr):
        m = re.search(r"\\[(\[].{0,40}", _body(t or ""))
        if m:
            add(i + 1, CHECK, "ocr", f"formula left in LaTeX notation: {m.group(0)!r}")

    for s_, num, label, term, first in repeated_notes(tr):
        add(s_, CHECK, "note", f"note †{num} \"{label}: {term}\" repeats the one on scan "
            f"{first} -- remove it and its mark")

    # a sentence still split across a page break in the English: the page
    # ends mid-sentence and the next page with text continues in lowercase.
    # Step 1 joins every split sentence onto one page (join_split_sentences);
    # what's left -- a table across the break, a margin note inside the
    # fragment -- is listed so nothing is translated as two halves unseen.
    def _english(t):
        b = re.sub(r"\u27e6MARGIN:[^\u27e7]*\u27e7", "", _body(t or ""))
        b = re.sub(r"\[\[(?:TABLE|DIVIDER)\]\]", "", b).strip()
        return "" if b in ("", "[Empty page]") else b
    eng = [_english(t) for t in tr]
    for i in range(len(eng) - 1):
        a = eng[i]
        if not a or re.search(r"[.!?;:\u00bb\u201d\")\]\u2026*]\s*\Z", a):
            continue
        j = i + 1 if eng[i + 1] or i + 2 >= len(eng) else i + 2
        if re.match(r"[a-z]", eng[j] or ""):
            add(i + 1, CHECK, "split", f"sentence split across the page break: "
                f"\u2026{a[-40:]!r} | {eng[j][:40]!r}\u2026")

    # running heads vs. the layout detector (intermediate/layout.json)
    # (not in a book set to print none -- [book] running_heads = false: the
    # detector's head there is the page number, so it has no say)
    lay = _layout.load(folder)
    if lay and os.path.exists(f"{I}/raw_ocr.txt") and not _layout.no_running_heads(folder):
        raw = _pageio.split_pages(open(f"{I}/raw_ocr.txt", encoding="utf-8").read())
        pages = [lay["pages"].get(str(i), {"boxes": []}) for i in range(len(ex))]
        band = _layout.header_band(pages)
        folio_heads = folio_shaped_heads(pages, raw)
        if folio_heads:
            add(0, "BOOK", "running heads", f"on {folio_heads[0]} of {folio_heads[1]} pages "
                "the detector's running head is only a few characters wide and the OCR "
                "reads no head there: it is probably the page number. If the book prints "
                "no running heads, add `[book]` `running_heads = false` to the overrides "
                "file and rerun step 1 (`reprocess_extracted.py`); until then those pages "
                "are not listed below.")
        # a page whose running head the reviewer set in the overrides file
        # ([pages.N] header, "" = none) is decided -- not re-flagged (Jacob
        # scan 49, La Boëssière scan 10: titles, not running heads)
        try:
            decided = {s for s, v in _overrides.load(os.path.basename(folder)[:-len("_translated")])
                       ["pages"].items() if "header" in v}
        except _overrides.OverridesError:
            decided = set()
        for i, e in enumerate(ex):
            if i + 1 in decided:
                continue
            m = re.search(r"(?im)^[ \t]*HEADER[ \t]*:[ \t]*(.*)$", raw[i]) if i < len(raw) else None
            v = (m.group(1).strip().strip("[]") if m else "").lower()
            vlm_h = bool(v) and v not in ("none", "n/a", "-")
            printed, unsure, why = _layout.running_head(pages[i], band, vlm_h)
            h = (re.search(r"(?m)^HEADER: (.*)$", e) or [None, ""])[1].strip()
            shown = bool(h) and h.lower() != "none"
            if unsure:
                add(i + 1, NOTE, "layout", "the layout detector and the OCR disagree on "
                    "whether this page has a running head")
            elif printed and not shown:
                # a plate's printed plate number ("Pl. 6.") reads as a page
                # head to the detector; a plate page shows no running head
                # (La Boëssière's 18 plates)
                if not any(f.get("kind") == "plate" for f in figs.get(str(i), [])) \
                        and not folio_heads:
                    add(i + 1, CHECK, "layout", "the page prints a running head, but none is shown")
            elif shown and not printed:
                add(i + 1, CHECK, "layout", f"running head {h.replace(chr(9), ' / ')!r} is "
                    "shown, but the page prints none (title page, plate, or opener?)")

    # page numbers: a shown folio must rise, and by no more than the sheets
    last = None
    for i, v in enumerate(pn[:len(ex)]):
        if not v or v.startswith("[") or not v.isdigit():
            continue
        if last is not None:
            li, lv = last
            if int(v) <= lv:
                add(i + 1, CHECK, "page number", f"shows p. {v} after p. {lv} "
                    f"(scan {li + 1}) -- goes backwards; one of the two is wrong")
                add(li + 1, CHECK, "page number", f"shows p. {lv}, but scan {i + 1} "
                    f"after it shows p. {v} -- one of the two is wrong")
            elif int(v) - lv > i - li:
                add(i + 1, CHECK, "page number", f"shows p. {v}, only {i - li} scan "
                    f"page(s) after p. {lv} -- skips ahead")
        last = (i, int(v))

    # --- 2026-10-18: checks for what the reviewer found by eye -------------
    # (a) the same short source line (a heading, a lesson label) translated
    # two ways in the book: Barbasetti "I. TEMPO" -> "I. TIME" / "I. TEMPO"
    # (38 of 86), "Azioni composte" -> compound / composite actions
    plain = lambda x: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", x)).strip()
    seen_as = defaultdict(lambda: defaultdict(list))
    for i, (e, t) in enumerate(zip(ex, tr)):
        sl = [plain(l) for l in clean_vlm_extraction(e).split("\n") if l.strip()] if e.strip() else []
        el = [plain(l) for l in _body(t).split("\n") if l.strip() and not l.startswith("[[")]
        if len(sl) != len(el):
            continue
        for a, b in zip(sl, el):
            if 1 <= len(a.split()) <= 6 and len(re.findall(r"[^\W\d_]", a)) >= 3:
                seen_as[a.upper().rstrip(".:")][b.upper().rstrip(".:")].append(i + 1)
    for a, ens in seen_as.items():
        # a variant sharing no word with the usual one is a page whose lines
        # are shifted against the source ("I. TEMPO" -> "STUDENT"), not a
        # second translation
        ens = {re.sub(r"†\d+", "", k).strip(): v for k, v in ens.items()}
        top = max(ens, key=lambda k: len(ens[k])) if ens else ""
        tw = {w for w in re.findall(r"[^\W\d_]{3,}", top)}
        ens = {k: v for k, v in ens.items() if k == top or tw & set(re.findall(r"[^\W\d_]{3,}", k))}
        if len(ens) > 1 and sum(len(v) for v in ens.values()) >= 3:
            ways = "; ".join(f"{k!r} ({len(v)})" for k, v in sorted(ens.items(), key=lambda kv: -len(kv[1])))
            first = min(min(v) for v in ens.values())
            add(first, CHECK, "consistency", f"{a!r} is translated {len(ens)} ways: {ways} -- "
                "pick one (a code rule or [[fix]])")

    # (b) a source word left in italics that isn't a kept term: italic in the
    # English, the same word in the source, rare in the book, not in the
    # prompt's kept terms -- Jacob-André *baleine*, Kirchhoffer "has *remisé*"
    kept = set()
    for pf in glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "translation_prompt*.txt")):
        ptxt = open(pf, encoding="utf-8").read()
        keep_rule = re.search(r"2\. KEEP.*?(?=\n3\. )", ptxt, re.S)   # the kept terms, listed plain
        for chunk in [m.group(1) for m in re.finditer(r"<i>([^<]+)</i>", ptxt)] + \
                ([keep_rule.group(0)] if keep_rule else []):
            kept |= {w.lower() for w in re.findall(r"[^\W\d_][\w'’-]*", chunk)}

    def is_kept(w):                       # plurals and inflections: croisés, épées, appuntate
        stems = {w, w.rstrip("s"), w[:-1] + "a", w[:-1] + "o", w[:-1] + "e", w[:-2] + "a", w[:-2] + "o"}
        return bool(stems & kept)
    import _pagekind
    glossary = {k for k, v in _pagekind.book_page_kinds(
        [clean_vlm_extraction(e) if e.strip() else "" for e in ex]).items() if v == "glossary"}
    ital = defaultdict(list)
    for i, (e, t) in enumerate(zip(ex, tr)):
        if i in glossary:                 # headwords kept in italics there (rule 7b)
            continue
        src_words = {w.lower() for w in re.findall(r"[^\W\d_][\w'’-]*", e)}
        for m in re.finditer(r"<i>([^<\s]+)</i>", _body(t)):
            w = m.group(1).strip(".,;:!?").lower()
            if len(w) >= 4 and w in src_words and not is_kept(w):
                ital[w].append(i + 1)
    odd = {w: v for w, v in ital.items() if len(v) <= 2}
    if len(odd) > 12:                     # one entry for a long list (Marchionni: archaic arms)
        add(0, "BOOK", "kept term", f"{len(odd)} words left in the original in italics that are "
            "not kept terms -- translate them, or add them to the kept terms: " +
            ", ".join(f"{w} (scan {v[0]})" for w, v in sorted(odd.items(), key=lambda kv: kv[1][0])))
    else:
        for w, v in sorted(odd.items(), key=lambda kv: kv[1][0]):
            add(v[0], CHECK, "kept term", f"<i>{w}</i> left in the original, but it is not one of the "
                f"kept terms{' (also scan ' + str(v[1]) + ')' if len(v) > 1 else ''} -- translate it, "
                "or add it to the kept terms")

    for scan, cap, w, like in caption_misreads(figs, ex):
        add(scan, NOTE, "caption", f"plate caption {cap[:50]!r}: {w!r} appears nowhere else in the "
            f"book -- a misread of {like!r}? Check the scan; correct it with [pages.{scan}] caption")

    # (c) table cells still in the source language (tables.json)
    tables = json.load(open(f"{I}/tables.json", encoding="utf-8")) if os.path.exists(f"{I}/tables.json") else {}
    for k, v in tables.items():
        for en in v.get("en") or []:
            if not en:
                continue
            left = [plain(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", en, re.S)
                    if still_french(plain(c), min_fr=2)]
            if left:
                add(int(k) + 1, CHECK, "table", f"{len(left)} table cell(s) left untranslated, "
                    f"e.g. {left[0][:50]!r}")

    # running heads left in French: one entry per distinct head, on its
    # first page, listing where else it appears
    where = defaultdict(list)
    for i, e in enumerate(ex):
        m = re.search(r"(?m)^HEADER: (.*)$", e)
        h = m.group(1).strip() if m else ""
        if h and h.lower() != "none":
            where[h].append(i + 1)
    for level, scan, detail in head_variants(where, hmap):
        add(scan, level, "header", detail)
    for h, scans in where.items():
        en = hmap.get(h, "")
        g = re.search(GARBLES, en, re.I)
        if g:
            add(scans[0], CHECK, "header", f"running head {en!r}: misspelling "
                f"{g.group(0)!r} (on {len(scans)} page(s))")
        for fr, wrong, rule in _rules:
            w = re.search(fr, h, re.I) and re.search(wrong, en, re.I)
            if w:
                add(scans[0], CHECK, "header", f"running head {en!r}: {w.group(0)!r} "
                    f"against the glossary ({rule}; on {len(scans)} page(s))")
        if not en or (en.strip() == h.strip() and still_french(h.replace("\t", " "), min_fr=2)):
            more = f" (also on {len(scans) - 1} more page(s), through scan {scans[-1]})" \
                if len(scans) > 1 else ""
            add(scans[0], CHECK, "header",
                (f"running head {h!r} left untranslated: {en!r}" if en
                 else f"running head {h!r} was never translated") + more
                + " -- set its English under [headers] in the overrides file")

    # hand-edited pages (books/<stem>.edits/, edit_page.py): a person has
    # read them, so the machine checks are dropped -- unless the page's
    # source text changed after the edit was made
    try:
        edits = _overrides.load_edits(os.path.basename(folder)[:-len("_translated")])
    except _overrides.OverridesError as e:
        add(0, "BOOK", "hand edits", str(e))
        edits = {}

    for s, level, kind, detail in english_only_issues(tr, figs, tables):
        add(s, level, kind, detail)

    # pages step 2 kept from a reply that ran out of room (cut_off.json,
    # _translate.record_cut_off): the English stops before the page does,
    # whatever became of its banner. A hand edit of the page clears it (below).
    cut_p = os.path.join(folder, "intermediate", "cut_off.json")
    if os.path.exists(cut_p):
        for s in sorted(int(k) for k in json.load(open(cut_p, encoding="utf-8"))):
            if 1 <= s <= len(tr):
                add(s, CHECK, "translation", "cut off: the model's reply ran out of room, so the English "
                    "stops before the page does -- retranslate the page, or complete it with edit_page.py")

    # a footnoted term the book uses and translates, with its first-use note
    # nowhere in the book: the model translated the word and left the note
    # off, and the code adds a note only where the word was left in the
    # source language (2026-11-12: about one book-and-term pair in six).
    # One item per term for the whole book; a note taken out on purpose is
    # answered "leave it" once.
    import _glossary_table as _gt
    import _tx_terms as _tt
    _lang = "Italian" if _rules is TERM_RULES_IT else "French"
    _refs = defaultdict(list)
    for _fr, _en in _tt._footnoted(_lang):
        _refs[_tt._footnote_ref(_lang).get(_fr, _fr)].append(_fr)
    try:
        _book_terms = {k.lower() for k in _overrides.load(os.path.basename(folder)[:-len("_translated")])["terms"]}
    except _overrides.OverridesError:
        _book_terms = set()
    _src = [_gt.norm(_gt.running_text(clean_vlm_extraction(e)) if e.strip() else "", accents=True) for e in ex]
    _whole = "\n".join(tr)
    for _ref, _forms in _refs.items():
        if f"{_lang}: {_ref}" in _whole or _ref in _book_terms or _book_terms & set(_forms):
            continue
        _rx = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(f) for f in _forms) + r")(?![\w-])")
        _on = [i + 1 for i, t in enumerate(_src) if _rx.search(t)]
        if len(_on) >= 3:
            add(0, "BOOK", "first-use note", f"«{_ref}» is on {len(_on)} pages (first: scan {_on[0]}) and "
                f"translated, but the note \"{_lang}: {_ref}\" is nowhere in the book -- add it at the first "
                "use (a [[fix]]), or leave it if the note was taken out on purpose")

    # a checked term rule (glossary_*.toml, check = "retry") still broken
    # after step 2's retry -- term_checks.json, _translate.record_term_left
    term_p = os.path.join(folder, "intermediate", "term_checks.json")
    if os.path.exists(term_p):
        for k, left in sorted(json.load(open(term_p, encoding="utf-8")).items(), key=lambda kv: int(kv[0])):
            if 1 <= int(k) <= len(tr):
                for what in left:
                    add(int(k), CHECK, "term", f"{what} -- against the term rule, and a retry naming the rule "
                        "did not mend it: fix the wording, or set the book's own rendering under [terms]")

    import _cover
    for s, level, detail in name_variants(_cover._meta(os.path.basename(folder)[:-len("_translated")]), tr):
        add(s, level, "name", detail)

    # the reviewer's [[fix]] decisions: each one in the text, or listed
    try:
        _ov = _overrides.load(os.path.basename(folder)[:-len("_translated")])
    except _overrides.OverridesError:
        _ov = None
    if _ov:
        for s, what, status in fix_status(tr, _ov, _rules is not TERM_RULES_IT, edits):
            if s == 0:                            # a [[book_fix]]: no page of its own
                add(1, CHECK, "override", f"[[book_fix]] {what}: " + (
                    "not in the text yet -- run apply_overrides.py" if status == "pending" else
                    "matches no page of the book -- rewrite it or remove it"))
                continue
            if status == "taken back":
                add(s, NOTE, "override", f"[[fix]] {what} is taken back by a later fix on the "
                    "same page -- remove both, or the earlier one")
                continue
            add(s, CHECK, "override", f"[[fix]] {what}: " + (
                "not in the text yet -- run apply_overrides.py" if status == "pending" else
                "its text is not on the page, so the decision is not in the PDFs -- the page was "
                "retranslated, or the find is wrong: rewrite the fix or remove it"))
        for f in _ov["table_fix"]:
            en = (tables.get(str(f["scan"] - 1)) or {}).get("en") or []
            if not any(h and (f["find"] in h or f["replace"] in h) for h in en):
                add(f["scan"], CHECK, "override", f"[[table_fix]] {f['find'][:50]!r} → "
                    f"{f['replace'][:50]!r}: its text is in none of the page's tables -- "
                    "rewrite the fix or remove it")
            elif any(h and f["find"] in h and f["find"] not in f["replace"] for h in en):
                add(f["scan"], CHECK, "override", f"[[table_fix]] {f['find'][:50]!r}: not in "
                    "the table yet -- run apply_overrides.py")
        _raw = f"{folder}/intermediate/raw_ocr.txt"
        if _ov["ocr_fix"] and os.path.exists(_raw):
            raw = _pageio.split_pages(open(_raw, encoding="utf-8").read())
            for f in _ov["ocr_fix"]:
                page = raw[f["scan"] - 1] if f["scan"] <= len(raw) else ""
                if f["find"] not in page and f["replace"] not in page:
                    add(f["scan"], CHECK, "override", f"[[ocr_fix]] {f['find'][:50]!r} → "
                        f"{f['replace'][:50]!r}: its text is not in the page's OCR -- the page was "
                        "read again: rewrite the correction or remove it")
    if edits:
        stale = {s for s, e in edits.items()
                 if s <= len(ex) and e["source"] and not _tx.fingerprint_matches(e["source"], ex[s - 1])}
        issues[:] = [x for x in issues if x["scan"] not in edits or x["level"] in ("TERMS", "BOOK")]
        for s in sorted(stale):
            add(s, CHECK, "hand edit", f"the page's source text changed after it was edited "
                f"by hand -- compare books/{os.path.basename(folder)[:-len('_translated')]}"
                f".edits/scan-{s:04d}.txt with the page")

    # the review page's answers (books/<stem>.review, review_page.py): an
    # item marked "fine as is" is decided -- kept in the JSON as ACCEPTED,
    # left out of the pages to check; a "fix" not yet in the overrides
    # file is listed until apply_review.py has written it
    stem = os.path.basename(folder)[:-len("_translated")]
    try:
        answers = _overrides.load_review(stem)
    except _overrides.OverridesError as e:
        add(0, "BOOK", "review answers", str(e))
        answers = {}
    for x in issues:
        if answers.get(card_id(x), {}).get("answer") == "fine" and x["level"] in (CHECK, NOTE):
            x["level"] = ACCEPTED
    if answers and _ov:
        for s, k, find in _overrides.review_fixes_missing(answers, _ov):
            add(s, CHECK, "review", f"answered Fix on the review page (card {k}), not in the "
                f"overrides file yet: {find[:50]!r} -- run pipeline/apply_review.py on the host")
    return issues


def write_report(folder: str, stem: str, issues: list) -> str:
    I = f"{folder}/intermediate"
    ex_n = len(_pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read()))
    pn = open(f"{I}/page_numbers.txt", encoding="utf-8").read().split("\n")
    pmap = page_map(folder, ex_n)
    ov = _overrides.load(stem)
    by_page = defaultdict(list)
    terms = [x for x in issues if x["level"] == "TERMS"]
    book = [x for x in issues if x["level"] == "BOOK"]
    issues = [x for x in issues if x["level"] not in ("TERMS", "BOOK")]
    for x in issues:
        if x["level"] != ACCEPTED:
            by_page[x["scan"]].append(x)
    n_ok = sum(1 for x in issues if x["level"] == ACCEPTED)
    n_check = sum(1 for x in issues if x["level"] == CHECK)
    pages_check = sorted({x["scan"] for x in issues if x["level"] == CHECK})
    kinds = Counter(x["kind"] for x in issues if x["level"] == CHECK)

    def where(s):
        folio = pn[s - 1] if 0 < s <= len(pn) else ""
        bits = [f"pdf {pmap[s]}" if s in pmap else None, f"scan {s}",
                f"p. {folio}" if folio and not folio.startswith("[") else None]
        return " · ".join(b for b in bits if b)

    L = [f"# Review report — {stem}", "",
         f"{ex_n} pages. **{len(pages_check)} page(s) to check** "
         f"({n_check} issue(s)); {sum(1 for x in issues if x['level'] == NOTE)} note(s)."
         + (f" {n_ok} more marked fine as is on the review page (`books/{stem}.review`)." if n_ok else ""),
         "", "Record decisions in `books/" + stem + ".overrides.toml` (manual III.5b); "
         "retranslate with `2_translate.py --pages " + stem + " <scan pages>`.", ""]
    if not pmap:
        L += ["*The side-by-side PDF is missing or out of date, so pdf page numbers "
              "are not shown — rebuild it and rerun this report.*", ""]
    if book:
        L += ["## About this book", ""] + [f"- **{x['kind']}**: {x['detail']}" for x in book] + [""]
    if kinds:
        L += ["| Kind | Checks |", "|---|---|"] + [f"| {k} | {n} |" for k, n in kinds.most_common()] + [""]
    if terms:
        L += ["## Terminology to settle", "",
              "One French phrase, several English renderings across the book. Pick one, "
              "add it under `[terms]` in the overrides file, and retranslate the other "
              "pages with `--pages`.", ""]
        for x in terms:
            parts = sorted(x["detail"].items(), key=lambda kv: -len(kv[1]))
            L.append(f"- **{x['kind']}**: " + "; ".join(
                f"*{k}* on {len(v)} page(s) (scan {', '.join(map(str, v))})"
                for k, v in parts))
        L.append("")
    L += ["## Pages to check", ""]
    for s in sorted(by_page):
        items = sorted(by_page[s], key=lambda x: x["level"] != CHECK)
        if items[0]["level"] != CHECK:
            continue
        L.append(f"- **{where(s)}**" + ("  *(has overrides)*" if s in ov["pages"] else ""))
        L += [f"    - {x['level']} · {x['kind']}: {x['detail']}" for x in items]
    notes = [s for s in sorted(by_page) if all(x["level"] == NOTE for x in by_page[s])]
    if notes:
        L += ["", "## Notes", ""]
        for s in notes:
            L.append(f"- {where(s)}: " + "; ".join(f"{x['kind']} — {x['detail']}" for x in by_page[s]))
    out = f"{folder}/{stem} — Review Report.md"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    with open(f"{I}/quality_report.json", "w", encoding="utf-8") as fh:
        json.dump(issues, fh, ensure_ascii=False, indent=1)
    return out


def main(stems):
    for stem in stems:
        folder = f"/data/processed/{stem}_translated"
        if not os.path.exists(f"{folder}/intermediate/translation.txt"):
            print(f"✗ {stem}: not translated yet")
            continue
        issues = check_book(folder)
        out = write_report(folder, stem, issues)
        pages = len({x["scan"] for x in issues if x["level"] == CHECK})
        print(f"✓ {stem}: {pages} page(s) to check -> {out}")


if __name__ == "__main__":
    import _books
    main([_books.stem_of(n) for n in _books.select(sys.argv[1:], __doc__, writes=False)])
