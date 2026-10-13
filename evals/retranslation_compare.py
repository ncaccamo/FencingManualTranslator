#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""After a full retranslation: did any page get worse? Compares a book's
translation now with a saved copy of the one before, page by page, and lists
the pages that need a look. Runs on the host, no model.

    python3 evals/retranslation_compare.py <stem> [<stem> ...] [--before <tag>]

<tag> names the saved copy in the book's intermediate/ folder:
translation.<tag>.txt and quality_report.<tag>.json (default
"before-literal-2026-10-11", saved before the literal-prompt retranslation).

Per page, the warning signs of a regression:
  - shorter by 15%+ in words (something dropped) or longer by 30%+ (added)
  - a kept term lost its italics, or a footnote marker (†) disappeared
  - newly low-confidence / untranslated / empty
  - a review-report check that wasn't there before
  - French or Italian left in the English (a source word run of 4+)
and the closeness to the author's words (content words with a source
look-alike, as evals/gold_passages.py) before and after -- expected to rise.
A [[fix]] whose text is no longer in the new page is listed too (it no
longer applies: re-check it).

Writes evals/reports/retranslation/<stem>.md (summary + the pages to look
at, worst first, each with source, before and after) and .html (the same,
side by side). evals/reports/ is git-ignored (book text).
"""
import difflib
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, os.path.join(ROOT, "evals"))
import _pageio  # noqa: E402
from gold_passages import closeness  # noqa: E402



def _drawn_italics():
    """_render.line_name_italics, without importing _render (it needs fitz,
    which the host lacks): the PDF italicises the French line names when it
    is drawn, so a line name the model left roman is not a lost italic."""
    src = open(os.path.join(ROOT, "pipeline", "_render.py"), encoding="utf-8").read()
    start = src.index("_LINE_NAMES = re.compile")
    end = src.index("\ndef markdown_italics")
    ns = {"re": re}
    exec(src[start:end], ns)
    return ns["line_name_italics"]


line_name_italics = _drawn_italics()
OUT = os.path.join(ROOT, "evals", "reports", "retranslation")
FLAGS = ("[low-confidence", "[translation unavailable", "[Empty page]")


def _body(page):
    m = re.search(r"BODY_TEXT:\s*(.*)", page, re.S)
    return (m.group(1) if m else page).strip()


def _words(t):
    return len(re.sub(r"<[^>]+>|___FOOTNOTES___", " ", t).split())


def _italics(t):
    return sorted(w.lower() for w in re.findall(r"<i>([^<]+)</i>", t))


def _checks(path):
    if not os.path.exists(path):
        return {}
    out = {}
    for it in json.load(open(path, encoding="utf-8")):
        if it.get("level") == "CHECK" and it.get("scan"):
            out.setdefault(it["scan"], set()).add(f"{it.get('kind')}: {it.get('detail', '')[:90]}")
    return out


def _notes(pages):
    """Term -> first scan with its "†N French: term" note. The note goes on a
    term's first occurrence, so a retranslation can move it to an earlier
    page; that is not a lost note."""
    first = {}
    for i, p in enumerate(pages):
        for t in re.findall(r"†\d+ (?:French|Italian): ([^\n]*)", p):
            first.setdefault(t.strip().lower(), i + 1)
    return first


def _fixes(stem):
    p = os.path.join(ROOT, "books", f"{stem}.overrides.toml")
    if not os.path.exists(p):
        return []
    try:
        import tomllib
    except ImportError:
        return []
    ov = tomllib.load(open(p, "rb"))
    return [f for f in ov.get("fix", []) if f.get("find")]


def compare(stem, tag):
    I = os.path.join(ROOT, "processed", f"{stem}_translated", "intermediate")
    ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
    old = _pageio.split_pages(open(f"{I}/translation.{tag}.txt", encoding="utf-8").read())
    new = _pageio.split_pages(open(f"{I}/translation.txt", encoding="utf-8").read())
    first_note = _notes(new)
    qa, qb = _checks(f"{I}/quality_report.{tag}.json"), _checks(f"{I}/quality_report.json")
    rows, tot = [], {"changed": 0, "cl_old": [0, 0], "cl_new": [0, 0]}
    for i in range(min(len(old), len(new), len(ex))):
        s, a, b, src = i + 1, old[i], new[i], _body(ex[i])
        if a.strip() == b.strip():
            continue
        tot["changed"] += 1
        ca, cb = closeness(a, src), closeness(b, src)
        tot["cl_old"][0] += ca[0]; tot["cl_old"][1] += ca[1]
        tot["cl_new"][0] += cb[0]; tot["cl_new"][1] += cb[1]
        why, score = [], 0
        wa, wb = _words(a), _words(b)
        if wa >= 20 and wb < 0.85 * wa:
            why.append(f"shorter: {wa} → {wb} words"); score += 3
        if wa >= 20 and wb > 1.30 * wa:
            why.append(f"longer: {wa} → {wb} words"); score += 2
        # a kept term counts as lost only below its count in the source (the
        # old page may have italicised a verb form, croiser -> "cross")
        ia, ib = _italics(line_name_italics(a)), _italics(line_name_italics(b))
        lost = [w for w in set(ia) if ia.count(w) > ib.count(w)
                and ib.count(w) < len(re.findall(rf"(?<!\w){re.escape(w)}(?!\w)", src, re.I))]
        if lost:
            why.append("italics lost: " + ", ".join(sorted(lost)[:6])); score += 2
        gone = [t for t in re.findall(r"†\d+ (?:French|Italian): ([^\n]*)", a)
                if first_note.get(t.strip().lower(), 10**9) > s]
        if gone:
            why.append("term notes lost: " + ", ".join(gone[:6])); score += 2
        for f in FLAGS:
            if f in b and f not in a:
                why.append(f"new: {f.strip('[')}"); score += 4
        for c in sorted(qb.get(s, set()) - qa.get(s, set())):
            why.append(f"new check — {c}"); score += 2
        if re.search(r"\b(?:[a-zàâçéèêëîïôûùüÿœ]+\s+){3}(?:de|du|des|la|le|les|di|del|della|che|il|lo)\b",
                     re.sub(r"<i>.*?</i>", "", b)) and not re.search(
                     r"\b(?:[a-zàâçéèêëîïôûùüÿœ]+\s+){3}(?:de|du|des|la|le|les|di|del|della|che|il|lo)\b",
                     re.sub(r"<i>.*?</i>", "", a)):
            why.append("possible source-language run"); score += 2
        if why:
            rows.append((score, s, why, src, a, b, ca, cb))
    # step 2 applies the fixes at the end of a run, so a working fix leaves its
    # replacement in the page; one whose find and replacement are both missing
    # did not apply. Not only fixes that worked on the old page: Barbasetti's 5
    # stale fixes were already stale before this retranslation and went unreported.
    fixes = [f for f in _fixes(stem) if 1 <= f.get("scan", 0) <= len(new)
             and f.get("find") and f.get("replace")
             and f["find"] not in new[f["scan"] - 1]
             and f["replace"] not in new[f["scan"] - 1]]
    rows.sort(key=lambda r: (-r[0], r[1]))
    pct = lambda c: 100 * c[0] / max(1, c[1])
    head = [f"# {stem} — retranslation check", "",
            f"Pages changed: {tot['changed']} of {len(new)}. Pages to look at: {len(rows)}. "
            f"Closeness to the author's words on the changed pages: {pct(tot['cl_old']):.2f}% → "
            f"{pct(tot['cl_new']):.2f}% (content words with a source look-alike).", ""]
    if fixes:
        head += ["**`[[fix]]` entries whose text is gone from the new page** (no longer applied — re-check):", ""]
        head += [f"- pdf {f['scan'] + 1}: find \"{f['find'][:70]}\"" for f in fixes] + [""]
    md, h = list(head), []
    e = lambda t: html.escape(t).replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>").replace("\n", "<br>")
    for score, s, why, src, a, b, ca, cb in rows:
        md += [f"## pdf {s + 1} — " + "; ".join(why), "",
               "**Before:**", "", a.replace("\n", "  \n"), "", "**After:**", "", b.replace("\n", "  \n"), ""]
        h.append(f"<h2>pdf {s + 1} — {html.escape('; '.join(why))}</h2><div class=g><div><h3>Source</h3>{e(src)}</div>"
                 f"<div><h3>Before</h3>{e(a)}</div><div><h3>After</h3>{e(b)}</div></div>")
    os.makedirs(OUT, exist_ok=True)
    open(f"{OUT}/{stem}.md", "w", encoding="utf-8").write("\n".join(md) + "\n")
    css = ("body{font:14px/1.45 Georgia,serif;margin:16px}h2{border-top:1px solid #999;padding-top:.5em;font-size:16px}"
           ".g{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}@media(max-width:900px){.g{grid-template-columns:1fr}}")
    open(f"{OUT}/{stem}.html", "w", encoding="utf-8").write(
        f"<!doctype html>\n<meta charset=utf-8>\n<title>{html.escape(stem)} retranslation</title>\n<style>{css}</style>\n"
        + "".join(f"<p>{e(l)}</p>\n" for l in head if l) + "\n".join(h) + "\n")
    print(f"{stem}: {tot['changed']} pages changed, {len(rows)} to look at, closeness "
          f"{pct(tot['cl_old']):.2f}% → {pct(tot['cl_new']):.2f}%, {len(fixes)} [[fix]] no longer applied "
          f"-> {os.path.relpath(OUT, ROOT)}/{stem}.md")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tag = sys.argv[sys.argv.index("--before") + 1] if "--before" in sys.argv else "before-literal-2026-10-11"
    if tag in args:
        args.remove(tag)
    if not args:
        sys.exit(__doc__)
    for st in args:
        compare(st, tag)
