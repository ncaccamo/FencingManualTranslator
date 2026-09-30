# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Category 3 ("explained") terms: the original word stays in the text, in
italics, and its first use in each book gets a short explanatory footnote.

Runs on a book's translated pages after translation -- deterministic, no
model. The first italic use of each term in the running text (headings are
skipped) gets a `†N` mark, numbered after the page's other footnotes, and
the note is added to that page's footnote block, in the same format as the
footnoted-term (category 2) references. Running it again adds nothing: a
note already in the book is not placed twice.

The notes are data, one file per source language:
    pipeline/explained_terms_<lang>.txt
        cavazione, cavazioni = A spiral movement of the point ...
Blank lines and lines starting with # are ignored. The forms before "=" are
the spellings to look for; the first is the one the note names.
"""
import os
import re

FN_MARK = "†"
FN_HEADER = "___FOOTNOTES___"
FN_MAX_PER_PAGE = 4


def load(lang: str, folder: str = os.path.dirname(os.path.abspath(__file__))) -> list:
    """[(forms, note)] for a language ("Italian" / "French"); [] if no file."""
    p = os.path.join(folder, f"explained_terms_{lang.lower()[:2]}.txt")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        forms, note = line.split("=", 1)
        forms = [f.strip() for f in forms.split(",") if f.strip()]
        if forms and note.strip():
            out.append((forms, note.strip()))
    return out


def _skip_line(line: str) -> bool:
    """Not running text: a heading (mostly capitals), a margin note
    (⟦MARGIN: …⟧), or a contents entry (dotted leader to a page number)."""
    if "⟦MARGIN" in line or re.search(r"(?:\.\s*){3,}\s*\d+\s*$|…+\s*\d+\s*$", line):
        return True
    letters = [c for c in re.sub(r"<[^>]+>", "", line) if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) > 0.7 * len(letters)


def _note_line(forms: list, note: str) -> str:
    return f"<i>{forms[0]}</i>: {note}"


def add_notes(pages: list, terms: list) -> int:
    """Place each term's note at its first italic use in `pages` (the
    book's translation.txt pages, edited in place). Returns how many notes
    were added."""
    added = 0
    book = "\n".join(pages)
    for forms, note in terms:
        line_txt = _note_line(forms, note)
        if line_txt in book:                        # placed on an earlier run
            continue
        pat = re.compile(r"<i>\s*(?:" + "|".join(re.escape(f) for f in forms)
                         + r")\s*</i>\w*", re.I)       # "<i>volte</i>s": mark after the s
        for i, page in enumerate(pages):
            body, sep, notes = page.partition("\n" + FN_HEADER + "\n")
            nums = [int(n) for n in re.findall(re.escape(FN_MARK) + r"(\d+)", body)]
            if len(nums) >= FN_MAX_PER_PAGE:
                continue
            lines, hit = body.split("\n"), None
            # a contents page: most of its lines end in a page number
            filled = [l for l in lines if l.strip()]
            if len(filled) >= 4 and sum(bool(re.search(r"\d+\s*$", l)) for l in filled) >= 0.5 * len(filled):
                continue
            for j, ln in enumerate(lines):
                if _skip_line(ln):
                    continue
                m = pat.search(ln)
                if m:
                    hit = (j, m)
                    break
            if not hit:
                continue
            j, m = hit
            n = max(nums, default=0) + 1
            lines[j] = lines[j][:m.end()] + f"{FN_MARK}{n}" + lines[j][m.end():]
            body = "\n".join(lines)
            entry = f"{FN_MARK}{n} {line_txt}"
            trail = "\n" if page.endswith("\n") else ""
            if sep:
                notes = notes.rstrip("\n") + "\n" + entry + "\n"
                pages[i] = body + sep + notes
            else:
                pages[i] = body.rstrip("\n") + "\n\n" + FN_HEADER + "\n" + entry + trail
            added += 1
            break
    return added
