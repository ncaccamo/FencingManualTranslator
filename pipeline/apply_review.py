#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Write the review page's Fix answers (books/<stem>.review, review_page.py)
into the book's overrides file as [[fix]] entries. Run on the HOST, from the
repo (books/ is read-only in the container):

    python3 pipeline/apply_review.py <stem> [<stem> ...]
    python3 pipeline/apply_review.py --answers <file.json> <stem> [...]

then, in the container, apply_overrides.py <stem> and rebuild the PDFs.

--answers takes the answers from another review tool (one that shows the
cards of "<stem> — Review cards.json"): a JSON list of
{"stem", "id", "answer": "fix"|"fine"|"later", "find", "replace", "note",
"scan", "pdf", "kind", "issue"}. They are merged into books/<stem>.review
(same card id: the new answer wins) before the fixes are written.

Only answers with a find / replace are written; each [[fix]] carries a
comment naming its card. A find that isn't on the page's English now is
skipped and listed -- a fix whose text isn't there never takes effect,
which is how approved answers went missing before (2026-11-05). An answer
already in the overrides file is not written twice. A Fix answer with only
a note is work for you (or a code change): the review report keeps listing
the item until it's resolved.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _overrides  # noqa: E402
import _pageio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOOKS = os.path.join(ROOT, "books")
PROCESSED = os.path.join(ROOT, "processed")


def toml_str(s: str) -> str:
    """A TOML basic string (JSON's escapes are TOML's, bar DEL)."""
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007f")


REVIEW_FIELDS = ("id", "scan", "pdf", "kind", "issue", "answer", "note", "find", "replace")


def merge_answers(stem: str, incoming: list, books_dir: str = BOOKS) -> int:
    """Merge answers (dicts with REVIEW_FIELDS) into books/<stem>.review,
    keeping the file's other cards; -> how many were added or changed."""
    have = _overrides.load_review(stem, books_dir)
    n = 0
    for a in incoming:
        if a.get("answer") not in ("fix", "fine", "later") or not isinstance(a.get("id"), str):
            raise _overrides.OverridesError(f"answer for card {a.get('id')!r} needs id and "
                                            "answer = fix, fine or later")
        new = {k: a[k] for k in REVIEW_FIELDS if a.get(k) not in (None, "")}
        if have.get(a["id"]) != new:
            have[a["id"]] = new
            n += 1
    L = [f"# Review answers (books/{stem}.review), from the review page (review_page.py).",
         "# answer: fix / fine (as is) / later. find + replace on a fix: apply_review.py writes it as a [[fix]].", ""]
    for c in have.values():
        L.append("[[card]]")
        for k in REVIEW_FIELDS:
            v = c.get(k)
            if v in (None, ""):
                continue
            if k == "issue":
                v = str(v)[:240]
            L.append(f"{k} = {v}" if isinstance(v, int) and not isinstance(v, bool) else f"{k} = {toml_str(str(v))}")
        L.append("")
    p = os.path.join(books_dir, f"{stem}.review")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    _overrides.load_review(stem, books_dir)       # still a valid file
    return n


def plan(stem: str, books_dir: str = BOOKS, processed: str = PROCESSED) -> tuple:
    """-> (entries to write [(card id, answer)], skipped [(card id, scan, why)])."""
    answers = _overrides.load_review(stem, books_dir)
    ov = _overrides.load(stem, books_dir)
    tp = f"{processed}/{stem}_translated/intermediate/translation.txt"
    tr = _pageio.split_pages(open(tp, encoding="utf-8").read()) if os.path.exists(tp) else []
    todo, skipped = [], []
    missing = {k for s, k, f in _overrides.review_fixes_missing(answers, ov)}
    for k, a in answers.items():
        if k not in missing:
            continue
        s = a["scan"]
        page = tr[s - 1] if 0 < s <= len(tr) else ""
        if a["find"] not in page:          # copied from the page's English on the review page
            skipped.append((k, s, f"{a['find'][:50]!r} is not on the page's English"))
        else:
            todo.append((k, a))
    return todo, skipped


def write(stem: str, todo: list, books_dir: str = BOOKS) -> None:
    from datetime import date
    p = _overrides.path_for(stem, books_dir)
    old = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
    L = ["", f"# review page answers (books/{stem}.review), applied {date.today().isoformat()}"]
    for k, a in sorted(todo, key=lambda t: t[1]["scan"]):
        where = f"pdf {a['pdf']}" if a.get("pdf") else f"scan {a['scan']}"
        L += ["[[fix]]", f"# {where} -- review card {k}: {' '.join(str(a.get('issue', '')).split())[:90]}",
              f"scan = {a['scan']}", f"find = {toml_str(a['find'])}",
              f"replace = {toml_str(a.get('replace', ''))}"]
    text = (old.rstrip("\n") + "\n" + "\n".join(L)) if old.strip() else "\n".join(L[1:])
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    _overrides.load(stem, books_dir)            # still a valid file


def main(argv):
    argv = list(argv)
    incoming = []
    if "--answers" in argv:
        i = argv.index("--answers")
        if i + 1 >= len(argv):
            sys.exit("✗ --answers needs a JSON file")
        incoming = json.load(open(argv[i + 1], encoding="utf-8"))
        del argv[i:i + 2]
    stems = [a for a in argv if not a.startswith("--")]
    if not stems:
        print(__doc__)
        return 2
    if HERE == "/app" or not os.access(BOOKS, os.W_OK):
        sys.exit(f"✗ {BOOKS} is not writable here -- run this on the host, from the repo")
    rc = 0
    for stem in stems:
        try:
            if _overrides.load(stem, BOOKS)["book"].get("final") is True:
                print(f"⊘ finished, left alone: {stem} ([book] final = true)")
                continue
        except _overrides.OverridesError:
            pass
        mine = [a for a in incoming if a.get("stem") == stem]
        if mine:
            try:
                n = merge_answers(stem, mine)
            except _overrides.OverridesError as e:
                print(f"✗ {stem}: {e}")
                rc = 1
                continue
            print(f"  {n} answer(s) added or changed in books/{stem}.review")
        try:
            todo, skipped = plan(stem)
        except _overrides.OverridesError as e:
            print(f"✗ {stem}: {e}")
            rc = 1
            continue
        if todo:
            write(stem, todo)
        for k, s, why in skipped:
            print(f"  ⚠ card {k} (scan {s}): {why} -- skipped; correct the find on the review page")
        print(f"✓ {stem}: {len(todo)} fix(es) written to {_overrides.path_for(stem, BOOKS)}"
              + (f"; next: apply_overrides.py {stem}, then rebuild the PDFs" if todo else ""))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
