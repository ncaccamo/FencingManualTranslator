#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Local meaning review: a model reads each source page beside its English
and lists the places where the English says something different. Model
selection first: run every candidate on the answer key (judge_key.py), then
score them (judge_score.py).

    python3 evals/meaning_judge.py <model> [<model> ...]      (in the image)

<model> is a llama-swap model key (llama-swap.yaml). Results go to
evals/reports/judge/<model>.json, one entry per page, saved after each page,
so a stopped run resumes. Needs the server to give the model a 16k context:
reasoning models write long before they answer.
"""
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, "/app")
import backend_config as bc  # noqa: E402

OUT = "/data/evals/reports/judge"
URL = f"{bc.translate_base_url()}/v1/chat/completions"

INSTRUCTIONS = """You check the English translation of one page of a historical {lang} fencing manual against the {lang} original. Find the places where the English SAYS SOMETHING DIFFERENT from the original: a wrong sense of a word (false friends: ignorer = not know, sensible = perceptible), a lost or added negation, a wrong subject or object (who does what to whom), a wrong fencing action, side, line or number, a phrase or sentence left out or added, an idiom rendered word for word so it means something else, or a passage left untranslated.

Do NOT report: style, word order, literal but correct renderings (the translation is meant to stay close to the author's words), British/American spelling, punctuation, or capitalisation. Words in <i>italics</i> are technical terms deliberately kept in the original language (riposte, quarte, terza, cavazione...) -- not an error. Notes marked †1 under ___FOOTNOTES___ are the translator's; (1), (2) are the author's own. A page may begin or end mid-sentence.

Answer with a JSON list only, no other text. One object per problem:
{{"source": "the exact words of the original", "english": "the exact words of the English", "problem": "what is wrong, in one sentence", "fix": "corrected English", "severity": "meaning" or "minor"}}
"meaning" = a reader of the English would understand something different from the author. "minor" = awkward or imprecise but the sense survives. If there is nothing wrong, answer [].

{lang} ORIGINAL:
{source}

ENGLISH TRANSLATION:
{english}
"""


def language(stem):
    p = f"/data/books/{stem}.meta.json"
    try:
        return json.load(open(p, encoding="utf-8")).get("language", "French")
    except (OSError, ValueError):
        return "French"


def ask(model, prompt):
    r = requests.post(URL, headers={"Content-Type": "application/json", **bc.auth_headers()},
                      json={"model": model, "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.0, "max_tokens": 8000}, timeout=3600)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    return msg.get("content") or "", msg.get("reasoning_content") or ""


def parse(reply):
    reply = re.sub(r"(?s)<think>.*?</think>", "", reply or "")
    m = re.search(r"\[.*\]", reply, re.S)
    if not m:
        return None
    try:
        items = json.loads(m.group(0))
    except ValueError:
        return None
    return [x for x in items if isinstance(x, dict)]


def main():
    key = json.load(open(f"{OUT}/key.json", encoding="utf-8"))["pages"]
    if os.environ.get("JUDGE_LIMIT"):                    # a quick trial: the first n pages
        key = key[:int(os.environ["JUDGE_LIMIT"])]
    for model in sys.argv[1:]:
        name = re.sub(r"[^\w.-]", "_", model)
        path = f"{OUT}/{name}.json"
        done = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        for p in key:
            k = f"{p['stem']}:{p['scan']}"
            if k in done and done[k].get("items") is not None:
                continue
            lang = language(p["stem"])
            prompt = INSTRUCTIONS.format(lang=lang, source=p["source"], english=p["english"])
            t = time.time()
            try:
                reply, thought = ask(model, prompt)
            except Exception as e:                       # noqa: BLE001
                print(f"{model} {k}: ERROR {e}", flush=True)
                continue
            items = parse(reply)
            done[k] = {"items": items, "reply": reply, "thought_chars": len(thought),
                       "seconds": round(time.time() - t)}
            json.dump(done, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"{model} {k}: {'unparsed' if items is None else len(items)} item(s), "
                  f"{done[k]['seconds']}s", flush=True)


if __name__ == "__main__":
    main()
