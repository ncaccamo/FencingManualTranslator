#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""How well would the term tables' checks have done on the books already
reviewed? No model, no GPU: it reads each book's source pages, its English
and its overrides file.

    python3 evals/glossary_score.py                 every book with a meta file
    python3 evals/glossary_score.py --min-fixes 50  what counts as a reviewed book

Two checks, for each entry whose source word is on a page
(pipeline/_glossary_table.py):

    forbidden   the English has a form the entry's line forbids ("never …")
    kept        a kept term's English form is missing

and two measures for each entry:

    caught       [[fix]] entries the check would have flagged: it fires on
                 the fix's "find" and not on its "replace"
    still fires  pages of a REVIEWED book (--min-fixes or more fixes) where
                 it fires on the English as it stands now. A reviewer has
                 passed those pages, so these are false alarms -- or errors
                 the review missed: read a few before trusting either way.

The report (evals/reports/glossary-score/<date>.md) proposes a check level
for each entry from the two: see LEVELS below. On unreviewed books the
checks' hits are listed as pages to look at.
"""
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import glob
import json
import os
import sys
import tomllib

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "pipeline"))

import _glossary_table as G  # noqa: E402
import _pageio  # noqa: E402

LEVELS = """retry  caught 3 or more fixes, and still fires on under 2% of the reviewed pages that have the term
card   caught at least 1 fix, or still fires on under 10% of them
off    the rest: fires too often on pages a reviewer passed"""


def level(caught: int, fires: int, pages: int) -> str:
    rate = fires / pages if pages else 0.0
    if caught >= 3 and rate < 0.02:
        return "retry"
    if (caught >= 1 and rate < 0.10) or (fires and rate < 0.10) or (caught and not pages):
        return "card"
    return "off" if (fires or caught) else "-"


def checks(term: dict, en: str, book_terms: dict = None) -> list:
    """What fires for an entry whose source is on the page: forbidden forms,
    or "missing" -- less what the book's own [terms] decided."""
    out = G.forbidden_found(term, G.without_book_terms(term, en, book_terms))
    if term["kind"] == "keep" and G.kept_missing(term, en) and not G.book_translates(term, book_terms):
        out.append("(missing)")
    return out


def main(argv: list) -> int:
    min_fixes = int(argv[argv.index("--min-fixes") + 1]) if "--min-fixes" in argv else 100
    tables = {lang: [t for t in G.load(lang) if t["kind"] != "note"] for lang in ("French", "Italian")}
    stat = {lang: defaultdict(lambda: Counter()) for lang in tables}       # id -> caught / fires / pages / fixes_seen
    examples = {lang: defaultdict(list) for lang in tables}
    totals = Counter()
    todo = []                                                              # unreviewed books: pages to look at
    for mp in sorted(glob.glob(str(REPO / "books" / "*.meta.json"))):
        stem = os.path.basename(mp)[:-len(".meta.json")]
        lang = json.load(open(mp, encoding="utf-8")).get("language")
        inter = REPO / "processed" / f"{stem}_translated" / "intermediate"
        if lang not in tables or not (inter / "translation.txt").exists():
            continue
        src = [p.split("BODY_TEXT:", 1)[-1] for p in _pageio.split_pages((inter / "extracted.txt").read_text(encoding="utf-8"))]
        en = _pageio.split_pages((inter / "translation.txt").read_text(encoding="utf-8"))
        ov = REPO / "books" / f"{stem}.overrides.toml"
        data = tomllib.load(open(ov, "rb")) if ov.exists() else {}
        fixes = [f for f in data.get("fix", []) if "find" in f and "replace" in f]
        book_terms = data.get("terms") or {}
        reviewed = len(fixes) >= min_fixes
        totals["books"] += 1
        totals["reviewed books"] += reviewed
        src = [G.running_text(s) for s in src]            # table cells' English is in tables.json, not on the page
        on = [[t for t in tables[lang] if G.check_applies(t, s)] for s in src]
        # (1) the fixes
        for f in fixes:
            k = int(f["scan"]) - 1
            if not 0 <= k < len(src):
                continue
            totals["fixes"] += 1
            hit = False
            for t in on[k]:
                a, b = checks(t, f["find"], book_terms), checks(t, f["replace"], book_terms)
                # "missing" on a snippet means little unless the replace supplies the term
                if t["kind"] == "keep":
                    fired = "(missing)" in a and "(missing)" not in b
                    a = [x for x in a if x != "(missing)"]
                    b = [x for x in b if x != "(missing)"]
                else:
                    fired = False
                fired = fired or bool(set(a) - set(b))
                if fired:
                    hit = True
                    stat[lang][t["id"]]["caught"] += 1
                    if len(examples[lang][t["id"]]) < 3:
                        examples[lang][t["id"]].append(f"{stem} scan {f['scan']}: {f['find'][:60]!r} → {f['replace'][:60]!r}")
            totals["fixes caught"] += hit
        # (2) the English as it stands
        for k, (s, e) in enumerate(zip(src, en)):
            for t in on[k]:
                got = checks(t, e, book_terms)
                if reviewed:
                    stat[lang][t["id"]]["pages"] += 1
                    stat[lang][t["id"]]["fires"] += bool(got)
                elif got:
                    todo.append((stem, k + 1, t["id"], ", ".join(got)))
    out = [f"# Term-table checks against the reviewed books ({date.today()})", "",
           f"{totals['books']} books, {totals['reviewed books']} reviewed ({min_fixes}+ fixes). "
           f"{totals['fixes caught']} of {totals['fixes']} [[fix]] entries would have been flagged "
           f"({100 * totals['fixes caught'] / max(1, totals['fixes']):.0f}%).", "", "Proposed levels:", "", "```", LEVELS, "```", ""]
    for lang in tables:
        rows = []
        for t in tables[lang]:
            c = stat[lang][t["id"]]
            if not (t.get("never") or t.get("never_re") or t["kind"] == "keep"):
                continue
            rows.append((level(c["caught"], c["fires"], c["pages"]), -c["caught"], t["id"], t["kind"], c))
        out += [f"## {lang}", "", "| entry | kind | caught | still fires | of pages | proposed |", "|---|---|---|---|---|---|"]
        order = {"retry": 0, "card": 1, "off": 2, "-": 3}
        for lv, _, i, kind, c in sorted(rows, key=lambda r: (order[r[0]], r[1], r[2])):
            out.append(f"| {i} | {kind} | {c['caught']} | {c['fires']} | {c['pages']} | {lv} |")
        out += ["", "Fixes caught, examples:", ""]
        for i, ex in examples[lang].items():
            out += [f"- **{i}**"] + [f"    - {e}" for e in ex]
        out.append("")
    out += ["## Unreviewed books: pages where a check fires", ""]
    by = Counter((s, i) for s, _, i, _ in todo)
    out += [f"- {s}: {i} on {n} page(s)" for (s, i), n in sorted(by.items(), key=lambda kv: (kv[0][0], -kv[1]))]
    rep = REPO / "evals" / "reports" / "glossary-score"
    rep.mkdir(parents=True, exist_ok=True)
    path = rep / f"{date.today()}.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(out[2])
    for lang in tables:
        lv = Counter(level(stat[lang][t["id"]]["caught"], stat[lang][t["id"]]["fires"], stat[lang][t["id"]]["pages"])
                     for t in tables[lang] if t.get("never") or t.get("never_re") or t["kind"] == "keep")
        print(f"{lang}: {sum(lv.values())} checkable entries -> retry {lv['retry']}, card {lv['card']}, off {lv['off']}, never fired {lv['-']}")
    print(f"report: {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
