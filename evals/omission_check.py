#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Where the English may have dropped (or doubled) text: per paragraph, not
per page -- one dropped sentence in a long page is invisible to the review
report's whole-page length check. Changes nothing; a list of pages for a
reader to check against the scan.

    python3 evals/omission_check.py <stem> [<stem> ...] | --all      (in the image)

Per page, when the source and the English have the same number of
paragraphs, each pair is compared:
  - length: the English paragraph is far shorter (or longer) than the
    book's usual English/source ratio (its median over all paragraphs);
  - sentences: the English has fewer sentences than the source (2+ fewer,
    or 1 fewer in a paragraph of 3 or fewer);
and on every page:
  - numbers: a number in the source (a date, a count, a figure number) that
    the English doesn't have, in figures or words.
A page whose paragraph counts differ is listed too (a paragraph lost or
merged), with the counts.

Writes evals/reports/omission/<stem>.md, worst first, by side-by-side pdf
page; one summary line per book on stdout.
"""
import os
import re
import sys

sys.path.insert(0, "/app")
import _books  # noqa: E402
import _pageio  # noqa: E402
import _render  # noqa: E402
import quality_report as q  # noqa: E402

OUT = "/data/evals/reports/omission"
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
         "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100, "thousand": 1000,
         "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
         "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}


def _clean(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"⟦MARGIN:[^⟧]*⟧", " ", s)
    return s


# a picture link, a figure's own label line ("Fig. 31", "Tav. II"): drawn
# as the picture's caption, not part of the English body
_FIGLINE = re.compile(r"!\[[^\]]*\]\([^)]*\)|\(?(?:fig|tav|pl|planche|plate|figure)\.?\s*[\dIVXLC]+\.?\)?", re.I)


def _paras(text):
    return [p for p in (x.strip() for x in re.split(r"\n\s*\n", text))
            if p and not re.fullmatch(r"\[[^\]]*\]", p) and not _FIGLINE.fullmatch(p)]


def _words(s):
    return len(re.findall(r"[^\W\d_]+", _clean(s)))


def _sentences(s):
    s = _clean(s)
    s = re.sub(r"\b(?:M|MM|Mme|Mr|Mrs|Dr|St|fig|Fig|p|pl|Pl|etc|No|n°|cf|vol|art|Art|ch|chap)\.", "X", s)
    s = re.sub(r"\.{2,}|…", " ", s)                      # ellipses and leaders
    # a stop, any closing quotes or brackets after it ("in line.” But"), then
    # a capital or the end; ";" not counted (translations move it)
    return len(re.findall(r"[.!?][”\"»’)\]]*(?=\s+[«“\"(‘]?\s?[A-ZÀ-Ý]|\s*$)", s))


def _numbers(s):
    s = _FIGLINE.sub(" ", _clean(s))
    return set(int(n) for n in re.findall(r"(?<![\w.,])(\d{1,4})(?![\w])", s) if int(n) > 1)


def check(stem):
    F = f"/data/processed/{stem}_translated"
    I = f"{F}/intermediate"
    if not os.path.exists(f"{I}/translation.txt"):
        return None
    tr = _pageio.split_pages(open(f"{I}/translation.txt", encoding="utf-8").read())
    ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
    pmap = q.page_map(F, len(tr))
    pages = []
    for i, t in enumerate(tr):
        if i >= len(ex) or not t.strip() or t.lstrip().startswith("["):
            continue
        src = ex[i].split("BODY_TEXT:", 1)[-1]
        if "<table" in src or "[[TABLE]]" in t or _render.toc_page(t):
            continue
        en_body, en_notes = _render.split_footnotes(t.strip())
        en_all = en_body + "\n\n" + "\n\n".join(en_notes)
        pages.append((i, src, en_body, en_all))
    # the book's usual ratio, over paragraph pairs of 25+ source words
    ratios = []
    for i, src, en, _ in pages:
        sp, ep = _paras(src), _paras(en)
        if len(sp) == len(ep):
            ratios += [_words(e) / _words(s) for s, e in zip(sp, ep) if _words(s) >= 25]
    if not ratios:
        return None
    med = sorted(ratios)[len(ratios) // 2]
    rows = []
    for i, src, en, en_all in pages:
        why, score = [], 0
        sp, ep = _paras(src), _paras(en)
        if len(sp) == len(ep):
            for k, (s, e) in enumerate(zip(sp, ep)):
                sw, ew = _words(s), _words(e)
                if sw >= 25:
                    r = ew / sw / med
                    if r < 0.72:
                        why.append(f"para {k + 1}: English {r:.0%} of the usual length ({ew} vs {sw} words)")
                        score += 3
                    elif r > 1.45:
                        why.append(f"para {k + 1}: English {r:.0%} of the usual length ({ew} vs {sw} words) -- doubled?")
                        score += 1
                ss, es = _sentences(s), _sentences(e)
                if ss - es >= 2 or (ss - es == 1 and ss <= 3 and sw >= 15):
                    why.append(f"para {k + 1}: {es} sentence(s) in English, {ss} in the source")
                    score += 2
        elif abs(len(sp) - len(ep)) >= 1 and _words(src) >= 40:
            why.append(f"{len(ep)} paragraph(s) in English, {len(sp)} in the source")
            score += 1
        en_text = _clean(en_all).lower()
        en_nums = _numbers(en_all) | {v for w, v in WORDS.items() if re.search(rf"\b{w}\b", en_text)}
        lost = sorted(_numbers(src) - en_nums)
        if lost:
            why.append("number(s) not in the English: " + ", ".join(map(str, lost[:6])))
            score += 2 * len(lost)
        if why:
            rows.append((score, pmap.get(i + 1, f"scan {i + 1}"), i + 1, why))
    rows.sort(key=lambda r: -r[0])
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/{stem}.md", "w", encoding="utf-8") as f:
        f.write(f"# Possible omissions: {stem}\n\n{len(pages)} text pages; usual English/source "
                f"word ratio {med:.2f}; {len(rows)} pages listed, worst first.\n\n")
        for score, pdfn, scan, why in rows:
            f.write(f"- pdf {pdfn} (scan {scan}, score {score}): " + "; ".join(why) + "\n")
    return len(pages), len(rows), sum(1 for r in rows if r[0] >= 3), med


def main():
    for stem in _books.select(sys.argv[1:], __doc__, writes=False):
        r = check(stem)
        if r:
            n, listed, strong, med = r
            print(f"{stem:42s} pages {n:4d}  listed {listed:4d}  score>=3 {strong:4d}  ratio {med:.2f}", flush=True)


if __name__ == "__main__":
    main()
