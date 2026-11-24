# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Long-s misreads in a book printed before 1800: proposed [[ocr_fix]] lines.

Old type prints the long s (ſ) much like an f, and the OCR reads «ſemelle»
as «femelle», «ſerrés» as «ferrés», «de la ſorte» as «de la forte». The
translation then follows the wrong word ("two femurs and a half").

This lists the words of the book's OCR text that are not French as read but
are with an s for an f: the s-form is common in the 19th/20th-century books
of the corpus (their extracted text is the vocabulary), and the f-form is
not, or is much rarer than the s-form in this book. It changes nothing: it
prints each candidate with its pages and context, and the [[ocr_fix]] lines
to paste into books/<stem>.overrides.toml after checking the scan. Then
reprocess the book (dry_run.py first) and retranslate the pages it changes.

    python3 /app/long_s_check.py <stem> [--min 3]     (in the container)

Words that are French either way («femelle» / «semelle», «font» / «sont») are
left out unless the s-form is at least 5 times as common, in this book and in
the vocabulary; those that are listed get their lines commented out ("# check
the scan:"): uncomment the ones the scan confirms.
"""
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

import _pageio
import _reading

DATA = os.environ.get("DATA_DIR", "/data")
WORD = re.compile(r"[A-Za-zÀ-ÿœŒæÆ]+(?:['’-][A-Za-zÀ-ÿœŒæÆ]+)*")


def _words(text: str):
    return [w for w in WORD.findall(text)]


def vocabulary(exclude: str) -> Counter:
    """Word counts (lower case) of the French books printed from 1800 on."""
    voc = Counter()
    for p in glob.glob(f"{DATA}/processed/*_translated/intermediate/extracted.txt"):
        stem = p.split("/")[-3][:-len("_translated")]
        y = _reading.book_year(stem, f"{DATA}/books")
        if stem == exclude or y is None or y < _reading.EARLY_BEFORE:
            continue
        txt = open(p, encoding="utf-8").read()
        if len(re.findall(r"\b(?:le|les|des|que|dans)\b", txt[:20000])) < 50:   # not French
            continue
        voc.update(w.lower() for w in _words(txt))
    return voc


def s_forms(word: str):
    """The word with one or more of its f read as s (lower case)."""
    w = word.lower()
    idx = [i for i, c in enumerate(w) if c == "f"]
    out = set()
    for mask in range(1, 1 << min(len(idx), 4)):
        chars = list(w)
        for b, i in enumerate(idx[:4]):
            if mask >> b & 1:
                chars[i] = "s"
        out.add("".join(chars))
    return out


def candidates(pages: list, voc: Counter, min_voc: int = 3) -> list:
    """[(f-form, s-form, [scan pages], example context)], most frequent first."""
    book = Counter(w.lower() for pg in pages for w in _words(pg))
    where = defaultdict(list)
    ctx = {}
    for i, pg in enumerate(pages, 1):
        for m in WORD.finditer(pg):
            w = m.group(0)
            if "f" not in w.lower() or len(w) < 3:
                continue
            where[w.lower()].append(i)
            ctx.setdefault(w.lower(), " ".join(pg[max(0, m.start() - 40):m.end() + 30].split()))
    out = []
    for w, scans in where.items():
        best = max(s_forms(w), key=lambda s: voc[s] + book[s])
        if voc[best] < min_voc:
            continue
        real_f = voc[w] > 0
        if real_f and not (book[best] >= 5 * book[w] and voc[best] >= 5 * max(voc[w], 1)):
            continue                     # French as read too, and not clearly rarer
        out.append((w, best, sorted(set(scans)), ctx[w], real_f))
    out.sort(key=lambda x: -len(x[2]))
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    stem = args[0]
    min_voc = int(sys.argv[sys.argv.index("--min") + 1]) if "--min" in sys.argv else 3
    raw = f"{DATA}/processed/{stem}_translated/intermediate/raw_ocr.txt"
    if not os.path.exists(raw):
        print(f"no OCR text for {stem} ({raw})")
        return 1
    y = _reading.book_year(stem, f"{DATA}/books")
    if y is not None and y >= _reading.EARLY_BEFORE:
        print(f"note: {stem} is dated {y}; the long s was out of use by then -- expect few true hits")
    pages = _pageio.split_pages(open(raw, encoding="utf-8").read())
    voc = vocabulary(stem)
    found = candidates(pages, voc, min_voc)
    if not found:
        print(f"{stem}: no long-s candidates")
        return 0
    print(f"{stem}: {len(found)} candidate word(s) -- check each on the scan before using its lines\n")
    for w, s, scans, c, real_f in found:
        flag = "  (French as read too: check)" if real_f else ""
        print(f"«{w}» → «{s}»  scans {', '.join(map(str, scans[:12]))}{' …' if len(scans) > 12 else ''}{flag}")
        print(f"    … {c} …")
    print("\n# proposed lines (case kept as printed; one per page and word):")
    for w, s, scans, c, real_f in found:
        mark = "# check the scan: " if real_f else ""
        for sc in scans:
            # the word with the one before it: a bare word would also match
            # inside longer ones («forte» in «fortement»)
            for m in sorted(set(re.findall(r"\S+[ \t]+" + re.escape(w) + r"\b|^" + re.escape(w) + r"\b",
                                           pages[sc - 1], re.IGNORECASE | re.MULTILINE))):
                word = m[-len(w):]
                # the printed letter where only the f changes, so a capital stays
                fixed = "".join(a if a.lower() == b else (b.upper() if a.isupper() else b)
                                for a, b in zip(word, s))
                print(f"{mark}[[ocr_fix]]\n{mark}scan = {sc}\n{mark}find = {json.dumps(m, ensure_ascii=False)}\n"
                      f"{mark}replace = {json.dumps(m[:-len(w)] + fixed, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
