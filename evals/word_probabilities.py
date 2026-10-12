#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Why did the model write that word? Translates one page at temperature 0
and prints, wherever one of the given words is a likely next word, the
model's top choices and their probabilities. Runs on the host against the
remote server (backend.env); one request.

    python3 evals/word_probabilities.py <stem> <scan> [--words tierce,quarte,...]
        [--entry fmt-translate-plain] [--tokens 400]

--words: the words to watch (default: the French or Italian line names).
--entry: the llama-swap key. Use the plain entry: with speculative decoding
(MTP) on, every drafted token comes back with no alternatives, so most of
the page shows nothing. Asking for it makes llama-swap swap models (about a
minute each way), which delays a running job.

The prompt is the book's language prompt without its [terms] and without
the neighbouring-page context step 2 adds, so the output can differ from
the book's; check that the word you are asking about comes out the same.
Example (2026-10-11, Kirchhoffer scan 52): "the straight riposte after ___"
-- tierce 34.3%, quarte 32.6%; the source says quarte.
"""
import json
import math
import os
import re
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "evals"))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import _pageio  # noqa: E402
from _rules import TX  # noqa: E402

LINES_FR = "prime seconde tierce quarte quinte sixte septime octave"
LINES_IT = "prima seconda terza quarta quinta sesta settima ottava"


def env():
    vals = {}
    for line in open(os.path.join(ROOT, "backend.env"), encoding="utf-8"):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return vals


def language(stem):
    meta = os.path.join(ROOT, "books", f"{stem}.meta.json")
    if os.path.exists(meta):
        return json.load(open(meta, encoding="utf-8")).get("language") or "French"
    return "French"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    if len(args) < 2:
        sys.exit(__doc__)
    stem, scan = args[0], int(args[1])
    lang = language(stem)
    entry, tokens = opt("--entry", "fmt-translate-plain"), int(opt("--tokens", "400"))
    words = opt("--words", LINES_IT if lang == "Italian" else LINES_FR).replace(",", " ").split()
    page = _pageio.split_pages(open(os.path.join(
        ROOT, "processed", f"{stem}_translated", "intermediate", "extracted.txt"), encoding="utf-8").read())[scan - 1]
    m = re.search(r"BODY_TEXT:\s*(.*)", page, re.S)
    body = (m.group(1) if m else page).strip()
    e = env()
    url = f"http://{e['REMOTE_HOST']}:{e['REMOTE_PORT']}/v1/chat/completions"
    r = requests.post(url, headers={"Authorization": f"Bearer {e.get('REMOTE_API_KEY', '')}"}, json={
        "model": entry, "temperature": 0.0, "max_tokens": tokens, "logprobs": True, "top_logprobs": 5,
        "messages": [{"role": "system", "content": "You are an expert translator of historical fencing texts."},
                     {"role": "user", "content": f"{TX['prompt_for_language'](lang)}\n\nText to translate:\n{body}"}],
    }, timeout=1800).json()
    choice = r["choices"][0]
    print(choice["message"]["content"][:1500], "\n" + "-" * 60)
    starts = {w[:3].lower() for w in words}
    out, shown = "", 0
    for t in (choice.get("logprobs") or {}).get("content") or []:
        tops = [(x["token"], 100 * math.exp(x["logprob"])) for x in t.get("top_logprobs", [])]
        if any(w.strip().lower()[:3] in starts and len(w.strip()) >= 2 for w, _ in tops[:3]):
            print(f"…{out[-60:]!r}\n    " + ", ".join(f"{w.strip()!r} {p:.1f}%" for w, p in tops))
            shown += 1
        out += t["token"]
    if not shown:
        print("no watched word among the likely next words"
              + (" (no alternatives returned: is speculative decoding on for this entry?)"
                 if not any(t.get("top_logprobs") for t in (choice.get("logprobs") or {}).get("content") or []) else ""))


if __name__ == "__main__":
    main()
