#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Second check of a meaning-judge run (meaning_judge.py): each flag goes back
to a model as one strict question -- would a reader of the English
understand something different from the author? -- and only the flags it
answers YES to are kept. Cuts the nitpicks the first pass labels "meaning".

    python3 evals/judge_verify.py <result> <model>        (in the image)

<result> is a result file name in evals/reports/judge (without .json), e.g.
fmt-judge-qwen35_nothink+v2; <model> as for meaning_judge.py (:nothink
allowed). Writes <result>+check.json (the same form, kept flags only; each
page also lists the dropped ones with the model's reason), saved after each
page, so a stopped run resumes. Score it with judge_score.py like any run.
"""
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meaning_judge as mj  # noqa: E402

QUESTION = """Below is one page of a {lang} fencing manual written in the 19th or early 20th century, its English translation, and one problem a reviewer reported in that translation. The translation deliberately stays close to the author's words: literal, old-fashioned or awkward English is correct as long as it says the same thing, and words in <i>italics</i> are technical terms kept on purpose.

Question: would a reader of the English understand something DIFFERENT from what the author wrote at that place (a different action, side, line, number, person, sense or negation, or something left out or added)?

Answer YES or NO as the first word, then one sentence of reason. Answer NO when the English is merely stiff, literal, wordy or a near-synonym, or when you are not sure.

{lang} ORIGINAL:
{source}

ENGLISH TRANSLATION:
{english}

REPORTED PROBLEM:
original: {item_source}
English: {item_english}
problem: {problem}
"""


def main():
    result, model = sys.argv[1], sys.argv[2]
    src = json.load(open(f"{mj.OUT}/{result}.json", encoding="utf-8"))
    key = {f"{p['stem']}:{p['scan']}": p for p in json.load(open(f"{mj.OUT}/key.json", encoding="utf-8"))["pages"]}
    path = f"{mj.OUT}/{result}+check.json"
    done = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    fails = 0
    for k, r in src.items():
        if k in done or k not in key:
            continue
        items = r.get("items")
        if items is None:
            items = mj.parse(r.get("reply"))
        if items is None:
            done[k] = dict(r, items=None)          # unreadable in the first pass: stays unreadable
            continue
        p, lang = key[k], mj.language(key[k]["stem"])
        kept, dropped, t = [], [], time.time()
        for x in items:
            if x.get("severity", "meaning") != "meaning":
                continue
            prompt = QUESTION.format(lang=lang, source=p["source"], english=p["english"],
                                     item_source=x.get("source", ""), item_english=x.get("english", ""),
                                     problem=x.get("problem", ""))
            try:
                try:
                    reply, _ = mj.ask(model, prompt)
                except requests.exceptions.RequestException as e:
                    if not mj.server_trouble(e):
                        raise
                    print(f"{k}: server trouble ({e}) -- waiting", flush=True)
                    time.sleep(60)
                    mj.wait_for_server()
                    reply, _ = mj.ask(model, prompt)
            except Exception as e:                   # noqa: BLE001
                print(f"{k}: ERROR {e}", flush=True)
                fails += 1
                if fails >= mj.FAILS:
                    raise SystemExit(f"STOPPED -- {fails} failed questions in a row")
                kept.append(x)                       # not checked: keep it, don't lose a flag
                continue
            fails = 0
            answer = re.sub(r"(?s)<think>.*?</think>", "", reply).strip()
            (kept if re.match(r"[\s*\"']*YES\b", answer, re.I) else dropped).append(
                dict(x, check=answer[:300]))
        done[k] = {"items": kept, "dropped": dropped, "seconds": r.get("seconds", 0) + round(time.time() - t)}
        json.dump(done, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"{k}: kept {len(kept)}, dropped {len(dropped)}", flush=True)


if __name__ == "__main__":
    main()
