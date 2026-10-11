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
    python3 /data/evals/gold_passages.py diff <a> <b>      the passages where two
        settings differ, side by side, changed words marked: <a>_vs_<b>.html
        (open in a browser) and .md (readable in an editor's preview)

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
    import _prompt_variants
    variant = _prompt_variants.apply(ns)
    passages = json.load(open(f"{OUT}/passages.json", encoding="utf-8"))
    res_p = f"{OUT}/{label}.json"
    res = json.load(open(res_p, encoding="utf-8")) if os.path.exists(res_p) else {
        "label": label, "model": os.environ.get("TRANSLATION_MODEL_NAME", ""),
        "deterministic": os.environ.get("EVAL_DETERMINISTIC", ""), "prompt_variant": variant, "passages": {}}
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


_STOP = set("""the and that this with from have been which their there they them then than what when where whose
would could should shall will your yours ours into upon also only very more most such some same other others each
every being were does done make made like over under after before about without while because though through those
these it's its his her him who whom one ones must may might can not nor but for are was had has our out all any""".split())


def _words(t):
    import re
    import unicodedata
    t = re.sub(r"</?i>|†\d+|___FOOTNOTES___.*", "", t, flags=re.S)
    return [unicodedata.normalize("NFKD", w.lower()).encode("ascii", "ignore").decode()
            for w in re.findall(r"[A-Za-zÀ-ÿ']+", t)]


def closeness(text, src):
    """Closeness to the author's words (2026-10-10): the share of the
    translation's content words (4+ letters, not function words) that have
    a look-alike in the source -- évident/evident, réciproque/reciprocal --
    plus English words per source word. Paraphrase ("mutual" for
    réciproque) and additions ("the task of") lower the first and raise the
    second. A rough stand-in, comparable between settings on the same
    passages; not a score of its own."""
    import difflib
    sw = [w for w in _words(src) if len(w) >= 4]
    ss, sp = set(sw), {w[:4] for w in sw}
    ew = [w for w in _words(text) if len(w) >= 4 and w not in _STOP]
    hit = sum(1 for w in ew if w[:4] in sp or any(
        difflib.SequenceMatcher(None, w, x).ratio() >= 0.75 for x in ss if abs(len(x) - len(w)) <= 3 and x[0] == w[0]))
    return hit, len(ew), len(_words(text)) / max(1, len(_words(src)))


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
             "Close to source = share of content words with a look-alike in the source (higher = more",
             "literal); EN/src = English words per source word (paraphrase and additions raise it).", "",
             "| Setting | Mean | Median | Worst passage | Close to source | EN/src | s/passage |",
             "|---|---|---|---|---|---|---|"]
    for l in labels:
        v = sorted(sc[l].values())
        secs = [runs[l][i]["secs"] for i in ids]
        cl = [closeness(runs[l][i]["text"], ref[i]["src"]) for i in ids]
        lines.append(f"| {l} | {sum(v)/len(v):.2f} | {v[len(v)//2]:.1f} | {v[0]:.1f} | "
                     f"{100 * sum(c[0] for c in cl) / max(1, sum(c[1] for c in cl)):.2f}% | "
                     f"{sum(c[2] for c in cl) / len(cl):.3f} | {sum(secs)/len(secs):.0f} |")
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

    def e(t):
        # escaped, but the translation's own <i>..</i> stays italics
        return html.escape(t).replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>")
    rows = []
    for i in spread:
        cells = "".join(f"<td><b>{e(l)} — {sc[l][i]:.1f}</b><br>{e(runs[l][i]['text']).replace(chr(10), '<br>')}</td>"
                        for l in labels)
        rows.append(f"\n<tr><td><b>{e(i)}</b><br><i>source</i><br>{e(ref[i]['src']).replace(chr(10), '<br>')}"
                    f"<br><br><i>published translation</i><br>{e(ref[i]['ref']).replace(chr(10), '<br>')}</td>{cells}</tr>")
    open(f"{OUT}/score.html", "w", encoding="utf-8").write(
        "<!doctype html><meta charset=utf-8><title>Translation quality test</title>"
        "<style>body{font:14px/1.45 Georgia,serif;margin:16px}table{border-collapse:collapse}"
        "td{vertical-align:top;border:1px solid #bbb;padding:8px;min-width:260px}</style>"
        f"<h1>Translation quality test</h1><p>Largest disagreement first. Scores: chrF++ vs the published translation.</p>"
        f"<table>{''.join(rows)}\n</table>\n")
    print(f"-> {OUT}/score.md, score.html")


def diff(a, b):
    import difflib
    from gold_report import chrf
    ref = {p["id"]: p for p in json.load(open(f"{OUT}/passages.json", encoding="utf-8"))}
    A = json.load(open(f"{OUT}/{a}.json", encoding="utf-8"))["passages"]
    B = json.load(open(f"{OUT}/{b}.json", encoding="utf-8"))["passages"]
    ids = [i for i in A if i in B and A[i]["text"] != B[i]["text"]]
    sc = {i: (chrf(A[i]["text"], ref[i]["ref"]), chrf(B[i]["text"], ref[i]["ref"])) for i in ids}
    ids.sort(key=lambda i: sc[i][1] - sc[i][0])          # b's worst first

    def marks(x, y, esc, d0, d1, i0, i1):
        wx, wy = x.split(" "), y.split(" ")
        X, Y = [], []
        for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, wx, wy).get_opcodes():
            u, v = esc(" ".join(wx[a1:a2])), esc(" ".join(wy[b1:b2]))
            if op == "equal":
                X.append(u); Y.append(v)
            else:
                if u: X.append(f"{d0}{u}{d1}")
                if v: Y.append(f"{i0}{v}{i1}")
        return " ".join(X), " ".join(Y)

    def e(t):
        return html.escape(t).replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>")

    def mdesc(t):
        return t.replace("<i>", "*").replace("</i>", "*")

    head = (f"The {len(ids)} of {len(A)} passages where **{a}** and **{b}** differ. "
            f"Sorted with {b}'s lowest score (chrF++ against the published translation) first; "
            "scores move about ±2 points on randomness alone.")
    H, M = [], [f"# {a} vs {b}", "", head, "",
                f"Marked: ~~struck~~ = only in {a}, **bold** = only in {b}.", ""]
    for i in ids:
        x, y = marks(A[i]["text"], B[i]["text"], e, "<del>", "</del>", "<ins>", "</ins>")
        H.append(f"\n<h2>{e(i)} <small>{e(a)} {sc[i][0]:.1f} · {e(b)} {sc[i][1]:.1f} "
                 f"({sc[i][1] - sc[i][0]:+.1f})</small></h2>\n<div class=g>"
                 f"<div><h3>{e(a)}</h3>{x.replace(chr(10), '<br>')}</div>"
                 f"<div><h3>{e(b)}</h3>{y.replace(chr(10), '<br>')}</div></div>\n"
                 f"<details><summary>Source and published translation</summary><div class=g>"
                 f"<div>{e(ref[i]['src']).replace(chr(10), '<br>')}</div>"
                 f"<div>{e(ref[i]['ref']).replace(chr(10), '<br>')}</div></div></details>")
        mx, my = marks(A[i]["text"], B[i]["text"], mdesc, "~~", "~~", "**", "**")
        M += [f"## {i} — {a} {sc[i][0]:.1f} · {b} {sc[i][1]:.1f} ({sc[i][1] - sc[i][0]:+.1f})", "",
              f"**{a}:**", "", mx.replace(chr(10), "  \n"), "", f"**{b}:**", "", my.replace(chr(10), "  \n"), ""]
    css = ("body{font:15px/1.5 Georgia,serif;margin:16px;max-width:1400px}"
           "h2{margin-top:2em;border-top:1px solid #999;padding-top:.6em}small{font-weight:normal;color:#555}"
           ".g{display:grid;grid-template-columns:1fr 1fr;gap:16px}"
           "del{background:#fbd5d5;text-decoration:none}ins{background:#c9efc9;text-decoration:none}"
           "@media(max-width:800px){.g{grid-template-columns:1fr}}")
    stem = f"{OUT}/{a}_vs_{b}"
    open(f"{stem}.html", "w", encoding="utf-8").write(
        f"<!doctype html>\n<meta charset=utf-8>\n<meta name=viewport content='width=device-width'>\n"
        f"<title>{e(a)} vs {e(b)}</title>\n<style>{css}</style>\n<h1>{e(a)} vs {e(b)}</h1>\n"
        f"<p>{head.replace('**', '')} Red = only in {e(a)}, green = only in {e(b)}.</p>{''.join(H)}\n")
    open(f"{stem}.md", "w", encoding="utf-8").write("\n".join(M) + "\n")
    print(f"{len(ids)} passages -> {stem}.html, {stem}.md")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "sample":
        sample()
    elif cmd == "run" and len(sys.argv) > 2:
        run(sys.argv[2])
    elif cmd == "diff" and len(sys.argv) == 4:
        diff(sys.argv[2], sys.argv[3])
    elif cmd == "score" and len(sys.argv) > 2:
        score(sys.argv[2:])
    else:
        sys.exit(__doc__)
