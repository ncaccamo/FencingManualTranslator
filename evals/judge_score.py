#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Score meaning_judge.py runs against the answer key (judge_key.py).

    python3 evals/judge_score.py [<model> ...]      (default: every result file)

A known finding counts as found when one of the judge's items quotes the same
words -- four words in a row in common, on the source side or the English
side. Reported per model: how many known meaning errors (and term errors)
it found, what it flagged on pages the agents found clean (noise -- or an
error they missed), its other items, unreadable answers, and seconds per
page. Writes evals/reports/judge/score.md with every unmatched item, for a
look by eye.
"""
import glob
import json
import os
import re
import sys

from meaning_judge import parse

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "judge")


def words(s):
    s = re.sub(r"<[^>]+>|[«»“”\"'’.,;:!?()\[\]—–-]", " ", (s or "").lower())
    return s.split()


def shares(a, b, n=4):
    wa, wb = words(a), words(b)
    if len(wa) < n or len(wb) < n:
        short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
        return bool(short) and " ".join(short) in " ".join(long_)
    grams = {tuple(wa[i:i + n]) for i in range(len(wa) - n + 1)}
    return any(tuple(wb[i:i + n]) in grams for i in range(len(wb) - n + 1))


def matches(item, finding):
    return (shares(item.get("source", ""), finding["source"]) or
            shares(item.get("english", ""), finding["english"]))


def main():
    key = {f"{p['stem']}:{p['scan']}": p for p in json.load(open(f"{OUT}/key.json", encoding="utf-8"))["pages"]}
    files = [f"{OUT}/{m}.json" for m in sys.argv[1:]] or \
        sorted(f for f in glob.glob(f"{OUT}/*.json") if not f.endswith("/key.json"))
    lines = ["# Meaning-review judge: scores", ""]
    table = ["| Model | Pages | Meaning errors found | Term errors found | Flags on clean pages "
             "| Other flags | Unreadable | Seconds / page |", "|---|---|---|---|---|---|---|---|"]
    detail = []
    for f in files:
        name = os.path.basename(f)[:-5]
        res = json.load(open(f, encoding="utf-8"))
        got = {"meaning": [0, 0], "term": [0, 0]}
        clean_flags = other = bad = 0
        secs = []
        unmatched = []
        for k, p in key.items():
            r = res.get(k)
            if not r:
                continue
            secs.append(r.get("seconds", 0))
            items = r.get("items")
            if items is None:
                items = parse(r.get("reply"))        # read again: the parser may have improved since
            if items is None:
                bad += 1
                continue
            items = [x for x in items if x.get("severity", "meaning") == "meaning"]
            used = set()
            for fd in p["findings"]:
                if fd["severity"] not in got:
                    continue
                got[fd["severity"]][1] += 1
                hit = [j for j, x in enumerate(items) if matches(x, fd)]
                if hit:
                    got[fd["severity"]][0] += 1
                    used |= set(hit)
            for j, x in enumerate(items):
                if j in used or any(matches(x, fd) for fd in p["findings"]):
                    continue
                if p["findings"]:
                    other += 1
                else:
                    clean_flags += 1
                unmatched.append((k, "clean page" if not p["findings"] else "page with findings", x))
        done = len(secs)
        pct = lambda a: f"{a[0]}/{a[1]} ({100 * a[0] // a[1] if a[1] else 0}%)"
        table.append(f"| {name} | {done} | {pct(got['meaning'])} | {pct(got['term'])} | {clean_flags} "
                     f"| {other} | {bad} | {sum(secs) // max(done, 1)} |")
        detail += ["", f"## {name}: flags not in the answer key", "",
                   "Each is noise -- or an error the agent reviews missed; worth a look either way.", ""]
        for k, where, x in unmatched:
            detail.append(f"- {k} ({where}): «{x.get('source', '')[:90]}» → \"{x.get('english', '')[:90]}\" "
                          f"-- {x.get('problem', '')[:160]}")
    lines += table + detail
    open(f"{OUT}/score.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(table))


if __name__ == "__main__":
    main()
