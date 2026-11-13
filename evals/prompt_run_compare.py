#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Two or more runs of translation_model_test.py set against each other, on
what the reviewer already corrected. Host, no model:

    python3 evals/prompt_run_compare.py s2-full s2-perpage              (PAGE_SET=retranslation runs)
    python3 evals/prompt_run_compare.py --set "" qwen38 gemma4          (the default page set)

For each run:
    tokens / seconds   the server's own counts for every model call
    tries, cut off     calls per page; replies that ran out of room
    fixes              each [[fix]] of the books' overrides files on a test page:
                       "wrong again"  the run wrote the fix's "find" wording
                       "right"        it wrote the "replace" wording unaided
                       "other"        neither (the sentence came out differently)
    term checks        pipeline/_glossary_table.py: forbidden forms, kept terms missing

A fix counts only when its "find" and "replace" differ in words (not case,
tags or punctuation alone), and its wording is compared without tags, case
or spacing. Pages where the runs differ on a fix are listed, to read.
"""
from collections import Counter
from pathlib import Path
import glob
import json
import re
import statistics
import sys
import tomllib

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "pipeline"))
sys.path.insert(0, str(REPO / "scripts"))

import _glossary_table as G  # noqa: E402
import _pageio  # noqa: E402
from repeated_fixes import changes  # noqa: E402


def flat(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>|†\d+", "", s or "")).strip().lower()


def book(prefix: str) -> tuple:
    folder = glob.glob(str(REPO / "processed" / f"{prefix}*_translated"))[0]
    stem = Path(folder).name[:-len("_translated")]
    lang = "French"
    mp = REPO / "books" / f"{stem}.meta.json"
    if mp.exists():
        lang = json.load(open(mp, encoding="utf-8")).get("language") or "French"
    ov = REPO / "books" / f"{stem}.overrides.toml"
    data = tomllib.load(open(ov, "rb")) if ov.exists() else {}
    fixes, book_terms = data.get("fix", []), data.get("terms") or {}
    src = _pageio.split_pages((Path(folder) / "intermediate" / "extracted.txt").read_text(encoding="utf-8"))
    return stem, lang, fixes, src, book_terms


def main(argv: list) -> int:
    sub = "retranslation"
    if "--set" in argv:
        i = argv.index("--set")
        sub = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    out_dir = REPO / "evals" / "reports" / "translation-model-test" / sub
    runs = {l: json.load(open(out_dir / f"{l}.json", encoding="utf-8")) for l in argv}
    keys = [k for k in next(iter(runs.values()))["pages"] if all(k in r["pages"] for r in runs.values())]
    tables = {lang: [t for t in G.load(lang) if t["kind"] != "note"] for lang in ("French", "Italian")}
    books = {}
    stat = {l: Counter() for l in runs}
    differ, lines = [], []
    for key in keys:
        prefix, scan = key.rsplit(":", 1)
        if prefix not in books:
            books[prefix] = book(prefix)
        stem, lang, fixes, src, book_terms = books[prefix]
        page_src = G.running_text(src[int(scan) - 1].split("BODY_TEXT:", 1)[-1])
        mine = [f for f in fixes if str(f.get("scan")) == scan and "find" in f and "replace" in f
                and changes(f["find"], f["replace"])]
        terms = [t for t in tables[lang] if G.check_applies(t, page_src)]
        verdicts = {}
        for l, r in runs.items():
            p = r["pages"][key]
            text, en = p["text"] or "", flat(p["text"])
            calls = p.get("calls") or []
            st = stat[l]
            st["pages"] += 1
            st["calls"] += len(calls)
            st["retried pages"] += len(calls) > 1
            st["cut off"] += sum(c.get("finish") == "length" for c in calls)
            st["low confidence"] += text.lstrip().startswith(("[low-confidence", "[translation unavailable"))
            st["secs"] += p.get("secs") or 0
            for c in calls:
                st["prompt tokens"] += c.get("prompt_tokens") or 0
                st["prompt ms"] += c.get("prompt_ms") or 0
                st["reply tokens"] += c.get("completion_tokens") or 0
                st["max prompt+reply"] = max(st["max prompt+reply"], (c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0))
            for f in mine:
                a, b = flat(f["find"]), flat(f["replace"])
                v = "right" if b and b in en else "wrong again" if a and a in en else "other"
                st[f"fix {v}"] += 1
                verdicts.setdefault(id(f), {})[l] = v
            hits = [(t["id"], x) for t in terms
                    for x in (G.forbidden_found(t, G.without_book_terms(t, text, book_terms))
                              + (["(missing)"] if G.kept_missing(t, text) and not G.book_translates(t, book_terms) else []))]
            st["term checks fired"] += len(hits)
            st["pages with a term check"] += bool(hits)
        for f in mine:
            v = verdicts[id(f)]
            if len(set(v.values())) > 1:
                differ.append(f"- {key}: {f['find'][:70]!r} → {f['replace'][:70]!r}: " + ", ".join(f"{l} {x}" for l, x in v.items()))
    labels = list(runs)
    lines += [f"# Prompt runs compared: {', '.join(labels)}", "", f"{len(keys)} pages translated by every run.", "",
              "| | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]

    def row(name, fn):
        lines.append(f"| {name} | " + " | ".join(str(fn(stat[l])) for l in labels) + " |")
    row("model calls", lambda s: s["calls"])
    row("pages retried", lambda s: s["retried pages"])
    row("replies cut off (out of room)", lambda s: s["cut off"])
    row("pages kept as low confidence", lambda s: s["low confidence"])
    row("prompt tokens per call (mean)", lambda s: round(s["prompt tokens"] / max(1, s["calls"])))
    row("largest prompt + reply (tokens)", lambda s: s["max prompt+reply"])
    row("seconds reading the prompt, per call", lambda s: round(s["prompt ms"] / 1000 / max(1, s["calls"]), 1))
    row("seconds per page (mean)", lambda s: round(s["secs"] / max(1, s["pages"]), 1))
    row("total minutes", lambda s: round(s["secs"] / 60))
    row("fixes: right unaided", lambda s: s["fix right"])
    row("fixes: wrong again", lambda s: s["fix wrong again"])
    row("fixes: other wording", lambda s: s["fix other"])
    row("term checks fired", lambda s: s["term checks fired"])
    row("pages with a term check", lambda s: s["pages with a term check"])
    lines += ["", f"## Fixes on which the runs differ ({len(differ)})", ""] + differ
    path = out_dir / f"compare-{'-vs-'.join(labels)}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:22]))
    print(f"\n→ {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
