#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Score translation settings against published translations, passage by
passage: is one setting less accurate than another? (MTP vs normal,
temperature 0 vs 0.3 -- 2026-10-10.) Runs in the container.

    python3 /data/evals/gold_passages.py sample            CPU: pick the passages
    python3 /data/evals/gold_passages.py run <label>       GPU: translate them
        (the setting comes from the environment, as for a book run:
         TRANSLATION_MODEL_NAME, EVAL_DETERMINISTIC=1 for temperature 0)
    python3 /data/evals/gold_passages.py score <label> [<label> ...]

A passage is a run of consecutive aligned sentence pairs (gold/<stem>/
aligned.jsonl) of about a page (120-260 source words), translated with the
real step-2 translate_page() and the book language's prompt, and scored
with gold_report.chrf (chrF++) against the human translation of the same
sentences. Whole-book averages hide small differences -- these references
are mostly free translations, so a passage's score swings widely -- hence
`score` compares settings passage by passage (paired), and two runs of the
same setting at temperature 0.3 give the noise floor: a difference smaller
than theirs means nothing. Writes reports/gold-passages/<label>.json and
score.md + score.html (each passage side by side, largest differences first).
"""
import html
import json
import os
import random
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/data/evals")
GOLD = "/data/evals/gold"
OUT = "/data/evals/reports/gold-passages"
# book: passages wanted. Bazancourt is the one close translation; the others
# are free (restructured) -- still enough to catch a real loss of accuracy.
# La Boessiere left out: its alignment pairs unrelated passages (checked
# 2026-10-10), which would only add noise.
BOOKS = {"bazancourt_1862_secrets": 25, "rosaroll-grisetti_1803_scienza": 15,
         "pecoraro-pessina_1912_schermasciabola": 20}
LO, HI = 120, 260


def sample():
    rnd = random.Random(20261010)
    out = []
    for stem, want in BOOKS.items():
        pairs = [json.loads(l) for l in open(f"{GOLD}/{stem}/aligned.jsonl", encoding="utf-8")]
        lang = json.load(open(f"{GOLD}/{stem}/meta.json", encoding="utf-8"))["src"].get("lang", "fr")
        # candidate passages: greedy runs of pairs that all have both sides
        # (a 1-0 / 0-1 block is an alignment gap -- it would score the gap)
        cands, i = [], 0
        while i < len(pairs):
            j, words = i, 0
            while j < len(pairs) and words < LO and pairs[j]["src"].strip() and pairs[j]["ref"].strip():
                words += len(pairs[j]["src"].split())
                j += 1
            if LO <= words <= HI:
                cands.append((i, j))
                i = j
            else:
                i = max(j, i + 1)
        pick = sorted(rnd.sample(cands, min(want, len(cands))))
        for a, b in pick:
            out.append({"id": f"{stem.split('_')[0]}:{a}", "stem": stem, "lang": lang,
                        "src": "\n".join(p["src"] for p in pairs[a:b]),
                        "ref": "\n".join(p["ref"] for p in pairs[a:b])})
        print(f"{stem}: {len(pick)} passages (from {len(cands)} candidates)")
    os.makedirs(OUT, exist_ok=True)
    json.dump(out, open(f"{OUT}/passages.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{len(out)} passages, {sum(len(p['src'].split()) for p in out)} source words -> {OUT}/passages.json")


def run(label):
    import _progress
    sys.argv = [sys.argv[0]]
    src = open("/app/2_translate.py", encoding="utf-8").read()
    ns = {"__name__": "_gold_passages"}
    exec(compile(src.split('\nprint("\\n" + "="*60)\nprint("STEP 2')[0], "2_translate.py", "exec"), ns)
    passages = json.load(open(f"{OUT}/passages.json", encoding="utf-8"))
    res_p = f"{OUT}/{label}.json"
    res = json.load(open(res_p, encoding="utf-8")) if os.path.exists(res_p) else {
        "label": label, "model": os.environ.get("TRANSLATION_MODEL_NAME", ""),
        "deterministic": os.environ.get("EVAL_DETERMINISTIC", ""), "passages": {}}
    todo = [p for p in passages if p["id"] not in res["passages"]]
    print(f"{label}: {len(todo)} passage(s) to translate ({len(passages) - len(todo)} done)")
    _progress.start(f"translation quality test: {label}", "translation", len(todo))
    for k, p in enumerate(todo, 1):
        ns["BOOK_LANG"] = {"it": "Italian", "fr": "French"}.get(p["lang"], "French")
        ns["PROMPT_TEMPLATE"] = ns["prompt_for_language"](ns["BOOK_LANG"])
        st = {"fn_seen": set(), "footnoted_seen": set(), "fn_state": {}}
        t0 = time.time()
        out = ns["translate_page"](f"PAGE_NUMBER: none\nHEADER: none\nBODY_TEXT:\n{p['src']}", k, len(todo), st)
        res["passages"][p["id"]] = {"text": out, "secs": round(time.time() - t0, 1)}
        json.dump(res, open(res_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        _progress.page(k)
    _progress.finish("stopped")
    print(f"✓ {label}: {len(res['passages'])} passages -> {res_p}")


def score(labels):
    from gold_report import chrf
    passages = json.load(open(f"{OUT}/passages.json", encoding="utf-8"))
    runs = {l: json.load(open(f"{OUT}/{l}.json", encoding="utf-8"))["passages"] for l in labels}
    ids = [p["id"] for p in passages if all(p["id"] in runs[l] for l in labels)]
    ref = {p["id"]: p for p in passages}
    sc = {l: {i: chrf(runs[l][i]["text"], ref[i]["ref"]) for i in ids} for l in labels}
    lines = [f"# Translation quality test — {len(ids)} passages vs the published translations", "",
             "chrF++ against the human translation (0-100; higher = closer). Paired: each passage",
             "is scored for every setting against the same reference.", "",
             "| Setting | Mean | Median | Worst passage | s/passage |", "|---|---|---|---|---|"]
    for l in labels:
        v = sorted(sc[l].values())
        secs = [runs[l][i]["secs"] for i in ids]
        lines.append(f"| {l} | {sum(v)/len(v):.2f} | {v[len(v)//2]:.1f} | {v[0]:.1f} | {sum(secs)/len(secs):.0f} |")
    lines += ["", "Paired differences (row minus column): mean, and passages better / worse by more than 2 points", "",
              "| | " + " | ".join(labels) + " |", "|---" * (len(labels) + 1) + "|"]
    for a in labels:
        row = [a]
        for b in labels:
            if a == b:
                row.append("—")
                continue
            d = [sc[a][i] - sc[b][i] for i in ids]
            row.append(f"{sum(d)/len(d):+.2f} ({sum(x > 2 for x in d)}↑ {sum(x < -2 for x in d)}↓)")
        lines.append("| " + " | ".join(row) + " |")
    by_book = {}
    for i in ids:
        by_book.setdefault(ref[i]["stem"], []).append(i)
    lines += ["", "| Book | passages | " + " | ".join(labels) + " |", "|---" * (len(labels) + 2) + "|"]
    for stem, b in by_book.items():
        lines.append(f"| {stem} | {len(b)} | " + " | ".join(f"{sum(sc[l][i] for i in b)/len(b):.1f}" for l in labels) + " |")
    md = "\n".join(lines) + "\n"
    open(f"{OUT}/score.md", "w", encoding="utf-8").write(md)
    print(md)
    # reading page: passages where the settings disagree most first
    spread = sorted(ids, key=lambda i: -(max(sc[l][i] for l in labels) - min(sc[l][i] for l in labels)))
    e = html.escape
    rows = []
    for i in spread:
        cells = "".join(f"<td><b>{e(l)} — {sc[l][i]:.1f}</b><br>{e(runs[l][i]['text']).replace(chr(10), '<br>')}</td>"
                        for l in labels)
        rows.append(f"<tr><td><b>{e(i)}</b><br><i>source</i><br>{e(ref[i]['src']).replace(chr(10), '<br>')}"
                    f"<br><br><i>published translation</i><br>{e(ref[i]['ref']).replace(chr(10), '<br>')}</td>{cells}</tr>")
    open(f"{OUT}/score.html", "w", encoding="utf-8").write(
        "<!doctype html><meta charset=utf-8><title>Translation quality test</title>"
        "<style>body{font:14px/1.45 Georgia,serif;margin:16px}table{border-collapse:collapse}"
        "td{vertical-align:top;border:1px solid #bbb;padding:8px;min-width:260px}</style>"
        f"<h1>Translation quality test</h1><p>Largest disagreement first. Scores: chrF++ vs the published translation.</p>"
        f"<table>{''.join(rows)}</table>")
    print(f"-> {OUT}/score.md, score.html")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "sample":
        sample()
    elif cmd == "run" and len(sys.argv) > 2:
        run(sys.argv[2])
    elif cmd == "score" and len(sys.argv) > 2:
        score(sys.argv[2:])
    else:
        sys.exit(__doc__)
