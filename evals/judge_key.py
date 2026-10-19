#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Answer key for the meaning-review judge (meaning_judge.py): the findings
of the agent meaning reviews (evals/reports/meaning-review/<stem>.final.*.md)
placed on their scan pages, with the English those reviews read
(translation.before-round4.txt -- the text has been fixed since).

A finding goes in only when its quoted English is really on that page of
that text. Pages with no finding in any review of the book (and enough text)
are the "clean" pages: what the judge flags there is noise -- or an error the
agents missed, which the scorer lists for a look.

    python3 evals/judge_key.py [--pages 50] > evals/reports/judge/key.json   (in the image)

Writes evals/reports/judge/key.json: {"pages": [{"stem", "scan", "source",
"english", "findings": [{"severity", "title", "source", "english", "fix"}]}]}.
Deterministic: the same pages every time.
"""
import glob
import json
import os
import random
import re
import sys

sys.path.insert(0, "/app")
import _pageio  # noqa: E402

ROOT = "/data"
BOOKS = ["kirchhoffer-etal_1911_escrime", "josephrenaud_1928_traitemoderne", "jacob-andre_1887_jeuepee",
         "prevost-jollivet_1891_escrimeduel", "barbasetti_1902_schermaspada", "masiello_1887_schermaitaliana"]
REVIEWS = f"{ROOT}/evals/reports/meaning-review"


def norm(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = s.replace("’", "'").replace("“", '"').replace("”", '"').replace("«", "").replace("»", "")
    return re.sub(r"\s+", " ", s).strip().lower()


def parse(md):
    """'## pdf N — title [sev] ...' blocks -> findings."""
    out = []
    for block in re.split(r"(?m)^## ", md)[1:]:
        head = block.split("\n", 1)[0]
        sev = re.findall(r"\[(meaning|term|minor|unsure)", head)
        get = lambda k: (re.search(rf"(?m)^- {k}:\s*(.*)$", block) or [None, ""])[1].strip()
        src, en, fix = get("Source"), get("English now"), get("Should be")
        if not src or not en:
            continue
        unq = lambda s: re.sub(r'^[«"“]+|[»"”]+$', "", s.split("»")[0] if s.startswith("«") else s).strip()
        out.append({"severity": sev[0] if sev else "minor", "title": head.split("—", 1)[-1].split("[")[0].strip(),
                     "source": unq(src), "english": unq(en.strip('"')), "fix": fix.strip('"')})
    return out


def main():
    n_pages = int(sys.argv[sys.argv.index("--pages") + 1]) if "--pages" in sys.argv else 50
    rnd = random.Random(20261019)
    with_findings, clean = [], []
    for stem in BOOKS:
        I = f"{ROOT}/processed/{stem}_translated/intermediate"
        ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
        tr = _pageio.split_pages(open(f"{I}/translation.before-round4.txt", encoding="utf-8").read())
        src_n = [norm(e.split("BODY_TEXT:", 1)[-1]) for e in ex]
        en_n = [norm(t) for t in tr]
        found = {}
        for md in glob.glob(f"{REVIEWS}/{stem}.final.*.md"):
            for f in parse(open(md, encoding="utf-8").read()):
                key = norm(f["source"])[:45]
                hits = [i for i, s in enumerate(src_n) if key and key in s]
                if len(hits) != 1:
                    continue
                i = hits[0]
                if norm(f["english"])[:40] not in en_n[i]:
                    continue                    # not in the text the review read: leave it out
                found.setdefault(i, []).append(f)
        mentioned = set(found)
        for md in glob.glob(f"{REVIEWS}/{stem}.*.md"):        # any review's pages count as not clean
            for m in re.finditer(r"(?m)^## pdf (\d+)", open(md, encoding="utf-8").read()):
                mentioned.add(int(m.group(1)) - 2)                   # pdf ~ scan + 1 (0-based: -2)
        for i, fs in found.items():
            if any(f["severity"] in ("meaning", "term") for f in fs):
                with_findings.append((stem, i, fs))
        for i, (e, t) in enumerate(zip(ex, tr)):
            body = e.split("BODY_TEXT:", 1)[-1]
            if (i not in mentioned and i - 1 not in mentioned and i + 1 not in mentioned
                    and len(body.split()) >= 120 and "<table" not in body and not t.strip().startswith("[")):
                clean.append((stem, i, []))
    rnd.shuffle(with_findings), rnd.shuffle(clean)
    k = round(n_pages * 0.7)
    picked = sorted(with_findings[:k] + clean[:n_pages - k])
    pages = []
    for stem, i, fs in picked:
        I = f"{ROOT}/processed/{stem}_translated/intermediate"
        ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
        tr = _pageio.split_pages(open(f"{I}/translation.before-round4.txt", encoding="utf-8").read())
        pages.append({"stem": stem, "scan": i + 1, "source": ex[i].split("BODY_TEXT:", 1)[-1].strip(),
                      "english": tr[i].strip(), "findings": fs})
    os.makedirs(f"{ROOT}/evals/reports/judge", exist_ok=True)
    json.dump({"pages": pages}, open(f"{ROOT}/evals/reports/judge/key.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    sev = {}
    for p in pages:
        for f in p["findings"]:
            sev[f["severity"]] = sev.get(f["severity"], 0) + 1
    print(f"{len(pages)} pages ({sum(1 for p in pages if p['findings'])} with findings, "
          f"{sum(1 for p in pages if not p['findings'])} clean); findings by severity {sev}; "
          f"pool: {len(with_findings)} pages with meaning/term findings, {len(clean)} clean")


if __name__ == "__main__":
    main()
