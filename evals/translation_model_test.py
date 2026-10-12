#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Translation model selection test: candidate models translate a fixed set
of hard + control pages, and the results are scored and compared.

    # translate the set with a model (a llama-swap key in remote mode):
    docker exec -e TRANSLATION_MODEL_NAME=fmt-translate-qwen38 fmt-translator \\
        python3 /data/evals/translation_model_test.py run qwen38
    # the current translations as a no-GPU baseline:
    docker exec fmt-translator python3 /data/evals/translation_model_test.py current
    # score + side-by-side reading page:
    docker exec fmt-translator python3 /data/evals/translation_model_test.py compare current qwen38 gemma4

Each page goes through the real translate_page() (same prompt, retries and
post-processing as a book run, incl. the book's [terms]); nothing under
processed/ is written. Results: evals/reports/translation-model-test/<label>.json.
`compare` scores every page with the review report's checks
(quality_report.translation_issues), adds seconds per page, and writes
evals/reports/translation-model-test/compare.md + compare.html (French | each model).
evals/reports/ is git-ignored (it holds book text).
"""
import html
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, "/app")
import _pageio     # noqa: E402

OUT = "/data/evals/reports/translation-model-test"

# (book stem prefix, scan page, why it's in the set). Hard cases are pages
# this corpus proved difficult in batch-2 review; controls are ordinary prose.
PAGES = [
    ("gomard", 156, "coup vs botte definitions"),
    ("gomard", 172, "coup/botte terms throughout"),
    ("gomard", 239, "produced THROUGHS"),
    ("gomard", 249, "rompre la mesure -> 'romp'"),
    ("gomard", 333, "table of contents; 'TIMED THROWS'"),
    ("gomard", 235, "author footnote + margin note"),
    ("gomard", 349, "plate description page"),
    ("gomard", 39, "INTRODUCTION: long historical prose"),
    ("gomard", 180, "dense technical prose"),
    ("prevost", 29, "flagged low-confidence"),
    ("prevost", 45, "title + subtitle, kept terms"),
    ("prevost", 57, "parry descriptions with image placeholders"),
    ("prevost", 153, "counter-riposte section"),
    ("prevost", 310, "medical vocabulary ('fil de soie')"),
    ("kirchhoffer", 45, "RASSEMBLANT caption / poitrine"),
    ("kirchhoffer", 57, "numbered figure captions"),
    ("kirchhoffer", 50, "idiomatic prose"),
    ("lamarche", 30, "literary prose, low-confidence"),
    ("lamarche", 45, "left partly in French"),
    ("lamarche", 65, "low-confidence, 'romp'"),
    ("lamarche", 128, "left partly in French"),
    ("lamarche", 314, "no usable translation"),
    ("jacob", 148, "low-confidence, drill commands"),
    ("jacob", 169, "low-confidence"),
    ("jacob", 204, "no usable translation"),
    ("jacob", 209, "low-confidence"),
    ("jacob", 308, "no usable translation"),
    ("spinnewyn", 18, "left partly in French"),
    ("spinnewyn", 116, "left partly in French"),
    ("spinnewyn", 148, "English much shorter than French"),
    ("spinnewyn", 173, "glossary entries"),
    # controls
    ("gomard", 120, "control"),
    ("prevost", 100, "control"),
    ("kirchhoffer", 30, "control"),
    ("lamarche", 200, "control"),
    ("jacob", 100, "control"),
    ("spinnewyn", 80, "control"),
]

# PAGE_SET=retranslation: the 2026-10-11 retranslation check -- pages where the
# literal prompt swapped a line name, dropped a kept term for an English
# look-alike, or left a quotation in French; plus controls.
RETRANSLATION_PAGES = [
    ("kirchhoffer", 52, "quarte -> tierce"), ("kirchhoffer", 23, "reprise -> re-attack"),
    ("kirchhoffer", 76, "reprise -> bout"),
    ("josephrenaud", 49, "sixte -> septime"), ("josephrenaud", 142, "seconde -> second"),
    ("josephrenaud", 169, "barré -> stop-hit"), ("josephrenaud", 178, "sixte -> septime"),
    ("josephrenaud", 192, "seconde -> second"), ("josephrenaud", 228, "épée -> foil"),
    ("josephrenaud", 263, "flèche -> run"),
    ("barbasetti", 35, "French quotation left"), ("barbasetti", 36, "French quotation left"),
    ("barbasetti", 69, "botta drilla"), ("barbasetti", 184, "rimessa -> remise"),
    ("masiello_1887", 11, "maestro -> master"), ("masiello_1887", 72, "manrovesci -> reversals"),
    ("masiello_1887", 128, "French quotation left"), ("masiello_1887", 133, "French quotation, l'fencing"),
    ("masiello_1887", 349, "tocchi -> touches"), ("masiello_1887", 434, "maestro -> master"),
    ("prevost-jollivet", 57, "sixte -> septime, all tries"),
    ("gomard", 190, "sixte -> septime (0.3 run)"), ("gomard", 265, "sixte -> septime (0.3 run)"),
    # from the meaning reviews (glossary terms, lost negations)
    ("kirchhoffer", 19, "jarrets tendus -> knees bent"), ("kirchhoffer", 50, "negation lost"),
    ("kirchhoffer", 54, "battement -> straight thrust"), ("kirchhoffer", 61, "pointe d'arrêt; contre de quarte"),
    ("kirchhoffer", 64, "negation lost; plastronnez"), ("kirchhoffer", 86, "negation lost; contretemps"),
    ("jacob", 17, "coup fourré -> feint"), ("jacob", 25, "hésiterais lost"),
    ("jacob", 117, "battements -> cuts"), ("jacob", 132, "coup fourré -> astray"),
    ("jacob", 144, "double contre de quarte"), ("jacob", 155, "à fond -> to the bottom"),
    ("jacob", 237, "pistol tirer -> fence"), ("jacob", 277, "entraînés -> trained"),
    ("josephrenaud", 30, "en cavant aux avancés"), ("josephrenaud", 39, "ne ... qu'aux reversed"),
    ("josephrenaud", 86, "poule -> chicken"), ("josephrenaud", 145, "coquille -> button; caver"),
    ("josephrenaud", 176, "battement -> stamp"), ("josephrenaud", 183, "contre de sixte -> against"),
    ("josephrenaud", 197, "corps-à-corps -> croisé"), ("josephrenaud", 264, "ignorait -> knew"),
    ("gomard", 60, "développement -> glide"), ("gomard", 69, "défend -> defends"),
    ("gomard", 123, "effacé -> squared"), ("gomard", 139, "foible/forte reversed"),
    ("gomard", 251, "attaque à l'épée"), ("gomard", 263, "ne plus que -> no longer"),
    ("gomard", 297, "parer au simple"), ("gomard", 304, "défende -> defend"),
    # round 3 (Italian books, Prévost-Jollivet, LaMarche pattern reviews)
    ("masiello_1887", 83, "page left in Italian"), ("masiello_1887", 164, "coccia -> pommel"),
    ("masiello_1887", 168, "coccia -> back of the head"), ("masiello_1887", 189, "forza nemica capitale"),
    ("masiello_1887", 278, "coccia -> skull"), ("masiello_1887", 302, "parata di appoggio"),
    ("masiello_1887", 397, "petto sopra -> outside"), ("masiello_1887", 439, "risposta -> answer"),
    ("masiello_1887", 553, "destramente -> to the right"), ("masiello_1887", 731, "quegli/questi swapped"),
    ("barbasetti", 33, "fleuret -> épée"), ("barbasetti", 41, "coccia, gavigliano -> ricasso"),
    ("barbasetti", 43, "prona -> supine"), ("barbasetti", 116, "parate di tasto"),
    ("barbasetti", 134, "sopra/sotto -> outside/inside"),
    ("prevost-jollivet", 21, "rapière -> riposte"), ("prevost-jollivet", 49, "the four lines"),
    ("prevost-jollivet", 88, "ligne du dehors"), ("prevost-jollivet", 106, "contre-dégagement"),
    ("prevost-jollivet", 130, "contres-opposés et septime"), ("prevost-jollivet", 156, "tendre la broche"),
    ("prevost-jollivet", 162, "temps -> tempo"), ("prevost-jollivet", 316, "jour -> day; ferrailler"),
    ("lamarche", 56, "ongles en-dessus -> below"), ("lamarche", 99, "dégagez dessus"),
    ("lamarche", 120, "parade trompée"), ("lamarche", 125, "contre de sixte et quarte"),
    ("lamarche", 133, "tendre la perche; à la retraite"), ("lamarche", 172, "dessus/dedans vs haut/bas"),
    ("lamarche", 289, "rapière -> riposte"), ("lamarche", 313, "fusée -> spring"),
    ("lamarche", 161, "corps-à-corps replaced; en deçà"),
    # controls
    ("kirchhoffer", 30, "control"), ("josephrenaud", 100, "control"), ("josephrenaud", 200, "control"),
    ("jacob", 100, "control"), ("jacob", 250, "control"), ("barbasetti", 100, "control"),
    ("masiello_1887", 300, "control"), ("masiello_1887", 500, "control"), ("gomard", 120, "control"),
    ("prevost-jollivet", 150, "control"), ("lamarche", 200, "control"), ("masiello_1887", 600, "control"),
]
# PAGE_SET=followup: pages whose rules changed after a test run
FOLLOWUP_PAGES = [("lamarche", 133, "tendre la perche"), ("prevost-jollivet", 156, "tendre la broche"),
                  ("prevost-jollivet", 79, "d'un coup sec"), ("barbasetti", 116, "parate di tasto"),
                  ("lamarche", 56, "ongles en-dessus"), ("prevost-jollivet", 21, "rapière"),
                  ("prevost-jollivet", 248, "rapière")]
if os.environ.get("PAGE_SET") == "followup2":
    PAGES = [("lamarche", 56, "ongles en-dessus; own-language note")]
    OUT = OUT + "/retranslation"
if os.environ.get("PAGE_SET") == "followup":
    PAGES = FOLLOWUP_PAGES
    OUT = OUT + "/retranslation"
if os.environ.get("PAGE_SET") == "retranslation":
    PAGES = RETRANSLATION_PAGES
    OUT = OUT + "/retranslation"


def _folder(prefix):
    import glob
    return glob.glob(f"/data/processed/{prefix}*_translated")[0]


def _load_tx():
    """step 2's functions, without starting its server."""
    src = open("/app/2_translate.py", encoding="utf-8").read()
    ns = {"__name__": "_tx_prefix"}
    exec(compile(src.split('\nprint("\\n" + "="*60)\nprint("STEP 2')[0], "2_translate.py", "exec"), ns)
    return ns


def _pages():
    for prefix, scan, why in PAGES:
        folder = _folder(prefix)
        ex = _pageio.split_pages(open(f"{folder}/intermediate/extracted.txt", encoding="utf-8").read())
        if scan <= len(ex):
            yield prefix, folder, scan, why, ex[scan - 1].strip()


def run(label, current=False):
    os.makedirs(OUT, exist_ok=True)
    ns = None if current else _load_tx()
    variant = ""
    if ns:
        sys.path.insert(0, "/data/evals")
        import _prompt_variants
        variant = _prompt_variants.apply(ns)
    first_temp = os.environ.get("FIRST_TEMP")
    if ns and first_temp:
        # test another first-try temperature (step 2's FIRST_TRY_TEMPERATURE);
        # the hotter retries (0.55, 0.8) stay as they are
        ns["FIRST_TRY_TEMPERATURE"] = float(first_temp)
        ns["translate_with_llama"].__defaults__ = (float(first_temp),)
    res = {"label": label, "model": os.environ.get("TRANSLATION_MODEL_NAME", ""),
           "first_temp": first_temp or "step 2 default", "prompt_variant": variant, "pages": {}}
    for prefix, folder, scan, why, page in _pages():
        key = f"{prefix}:{scan}"
        if current:
            tr = _pageio.split_pages(open(f"{folder}/intermediate/translation.txt", encoding="utf-8").read())
            res["pages"][key] = {"text": tr[scan - 1].strip(), "secs": None}
            continue
        stem = os.path.basename(folder)[:-len("_translated")]
        ns["PROMPT_TEMPLATE"] = ns["prompt_for_book"](stem)   # language prompt + book terms, as step 2
        st = {"fn_seen": set(), "footnoted_seen": set(), "fn_state": {}}
        t0 = time.time()
        out = ns["translate_page"](page, scan, 0, st)
        res["pages"][key] = {"text": out, "secs": round(time.time() - t0, 1)}
        json.dump(res, open(f"{OUT}/{label}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(res, open(f"{OUT}/{label}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✓ {label}: {len(res['pages'])} page(s) -> {OUT}/{label}.json")


def compare(labels):
    import importlib.util
    spec = importlib.util.spec_from_file_location("qr", "/app/quality_report.py")
    qr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qr)
    runs = {l: json.load(open(f"{OUT}/{l}.json", encoding="utf-8")) for l in labels}
    rows, table = [], {}
    for prefix, folder, scan, why, page in _pages():
        key = f"{prefix}:{scan}"
        src = qr.clean_vlm_extraction(page) if page else ""
        cells = {}
        for l in labels:
            p = runs[l]["pages"].get(key)
            if not p:
                continue
            issues = qr.translation_issues(src, p["text"] or "")
            cells[l] = (p, issues)
        rows.append((key, why, src, cells))
    L = ["# Translation model selection test", "",
         f"{len(rows)} pages ({sum(1 for r in rows if r[1] != 'control')} hard cases, "
         f"{sum(1 for r in rows if r[1] == 'control')} controls). Checks = the review "
         "report's translation checks.", "",
         "| Run | Model | Pages with a CHECK | Checks by kind | Low-conf / unavailable | s/page (mean) |",
         "|---|---|---|---|---|---|"]
    for l in labels:
        cs = [c[l] for _, _, _, c in rows if l in c]
        flagged = sum(1 for p, iss in cs if any(x[0] == qr.CHECK for x in iss))
        kinds = Counter(x[1] for p, iss in cs for x in iss if x[0] == qr.CHECK)
        lowc = sum(1 for p, iss in cs if (p["text"] or "").lstrip().startswith(
            ("[low-confidence", "[translation unavailable")))
        secs = [p["secs"] for p, _ in cs if p.get("secs")]
        L.append(f"| {l} | {runs[l].get('model') or '(current output)'} | {flagged}/{len(cs)} | "
                 f"{', '.join(f'{k} {n}' for k, n in kinds.most_common()) or '—'} | {lowc} | "
                 f"{(sum(secs) / len(secs)) if secs else 0:.1f} |")
    L += ["", "## Per page", ""]
    for key, why, src, cells in rows:
        L.append(f"- **{key}** ({why}): " + "; ".join(
            f"{l}: " + (", ".join(x[1] for x in iss if x[0] == qr.CHECK) or "ok")
            for l, (p, iss) in cells.items()))
    open(f"{OUT}/compare.md", "w", encoding="utf-8").write("\n".join(L) + "\n")
    # side-by-side reading page
    H = ["<!doctype html><meta charset=utf-8><title>Translation model test</title><style>",
         "body{font:14px/1.45 Georgia,serif;margin:16px;background:#fff;color:#111}"
         "table{border-collapse:collapse;width:100%;table-layout:fixed}"
         "td,th{border:1px solid #ccc;padding:8px;vertical-align:top;white-space:pre-wrap}"
         "th{background:#f3f3f3;font-family:sans-serif}.iss{color:#a00;font:12px sans-serif}"
         "h2{font-family:sans-serif;margin-top:28px}"
         "@media(prefers-color-scheme:dark){body{background:#161616;color:#ddd}"
         "th{background:#262626}td,th{border-color:#444}.iss{color:#f77}}</style>",
         "<h1>Translation model selection test</h1>"]
    for key, why, src, cells in rows:
        H.append(f"<h2>{html.escape(key)} — {html.escape(why)}</h2><table><tr><th>French</th>"
                 + "".join(f"<th>{html.escape(l)}</th>" for l in cells) + "</tr><tr>"
                 + f"<td>{html.escape(src)}</td>"
                 + "".join("<td>" + html.escape(p["text"] or "") + "".join(
                     f"<div class=iss>{html.escape(x[0] + ' · ' + x[1] + ': ' + x[2])}</div>"
                     for x in iss) + (f"<div class=iss>{p['secs']} s</div>" if p.get("secs") else "")
                           + "</td>" for l, (p, iss) in cells.items())
                 + "</tr></table>")
    open(f"{OUT}/compare.html", "w", encoding="utf-8").write("\n".join(H))
    print(open(f"{OUT}/compare.md", encoding="utf-8").read().split("## Per page")[0])
    print(f"→ {OUT}/compare.md, compare.html")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "run":
        run(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "current":
        run("current", current=True)
    elif len(sys.argv) >= 3 and sys.argv[1] == "compare":
        compare(sys.argv[2:])
    else:
        sys.exit(__doc__)
