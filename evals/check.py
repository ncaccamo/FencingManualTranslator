#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Diff the current pipeline output against the frozen baselines and write
a report. Run it after any prompt/rule change + pipeline re-run.

    python3 evals/check.py                 # every book that has a baseline
    python3 evals/check.py Joseph-Renaud   # just this one
    python3 evals/check.py --quiet         # summary only, no per-page diffs

Exit code is non-zero if ANY artifact changed -- so it can gate a commit.
A change is not automatically a regression; it is a "read these N pages"
list. Deliberately accept a change by re-running freeze.py --force.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import difflib
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import _pageio
from metrics import book_metrics

REPO = Path(__file__).resolve().parent.parent
PROCESSED = REPO / "processed"
BASELINES = Path(__file__).resolve().parent / "baselines"
REPORTS = Path(__file__).resolve().parent / "reports"


def _pages(text: str) -> list[str]:
    return _pageio.split_pages(text) if text else []


def _cmp_pages(old: str, new: str) -> tuple[list[int], list[str]]:
    o, n = _pages(old), _pages(new)
    changed, snippets = [], []
    for i in range(max(len(o), len(n))):
        a = o[i] if i < len(o) else "<missing>"
        b = n[i] if i < len(n) else "<missing>"
        if a != b:
            changed.append(i)
            if len(snippets) < 12:
                d = difflib.unified_diff(a.splitlines(), b.splitlines(),
                                         f"baseline p{i}", f"current p{i}",
                                         lineterm="", n=1)
                snippets.append("\n".join(list(d)[:24]))
    return changed, snippets


def _cmp_lines(old: str, new: str) -> list[tuple[int, str, str]]:
    o, n = old.splitlines(), new.splitlines()
    return [(i, o[i] if i < len(o) else "", n[i] if i < len(n) else "")
            for i in range(max(len(o), len(n)))
            if (o[i] if i < len(o) else None) != (n[i] if i < len(n) else None)]


def _cmp_figures(old: str, new: str) -> list[str]:
    o = json.loads(old or "{}")
    n = json.loads(new or "{}")
    out = []
    for k in sorted(set(o) | set(n), key=lambda x: int(x)):
        a, b = o.get(k, []), n.get(k, [])
        if a != b:
            out.append(f"    idx {k}: {len(a)} row(s) -> {len(b)} row(s)"
                       + (f"  {json.dumps(b, ensure_ascii=False)[:120]}" if b else ""))
    return out


def _cmp_json_map(old: str, new: str) -> list[str]:
    o = json.loads(old or "{}")
    n = json.loads(new or "{}")
    out = []
    for k in sorted(set(o) | set(n)):
        if o.get(k) != n.get(k):
            out.append(f"    {k!r}: {o.get(k)!r} -> {n.get(k)!r}")
    return out


def check_book(book: str, quiet: bool) -> tuple[bool, str]:
    base = BASELINES / book
    cur = PROCESSED / f"{book}_translated"
    lines = [f"## {book}"]
    manifest = json.loads((base / "MANIFEST.json").read_text())
    lines.append(f"baseline frozen {manifest['frozen_at']} @ {manifest['git_commit'][:10]}"
                 + ("  (dirty tree)" if manifest.get("git_dirty") else ""))

    changed_any = False
    for name in ("translation.txt", "extracted.txt", "page_numbers.txt",
                 "figures.json", "headers.json"):
        bf, cf = base / name, cur / "intermediate" / name
        if not bf.is_file():
            continue
        if not cf.is_file():
            lines.append(f"\n### {name}: MISSING in current output")
            changed_any = True
            continue
        old, new = bf.read_text(encoding="utf-8"), cf.read_text(encoding="utf-8")
        if old == new:
            lines.append(f"\n### {name}: unchanged")
            continue
        changed_any = True
        if name in ("translation.txt", "extracted.txt"):
            idx, snips = _cmp_pages(old, new)
            lines.append(f"\n### {name}: {len(idx)} page(s) changed -> {idx}")
            if not quiet:
                for s in snips:
                    lines.append("```diff\n" + s + "\n```")
        elif name == "page_numbers.txt":
            d = _cmp_lines(old, new)
            lines.append(f"\n### {name}: {len(d)} folio label(s) changed")
            if not quiet:
                for i, a, b in d[:40]:
                    lines.append(f"    line {i}: {a!r} -> {b!r}")
        elif name == "figures.json":
            d = _cmp_figures(old, new)
            lines.append(f"\n### {name}: {len(d)} page(s) changed")
            lines += d if not quiet else d[:0]
        elif name == "headers.json":
            d = _cmp_json_map(old, new)
            lines.append(f"\n### {name}: {len(d)} header(s) changed")
            lines += d if not quiet else d[:0]

    # metrics table: baseline vs current
    try:
        m_cur = book_metrics(cur)
        m_base = _baseline_metrics(base)
        lines.append("\n### metrics (baseline -> current)")
        for k in m_cur:
            a, b = m_base.get(k, "-"), m_cur[k]
            flag = "" if a == b else "   <--"
            lines.append(f"    {k:18} {a} -> {b}{flag}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"\n(metrics skipped: {e})")

    return changed_any, "\n".join(lines)


def _baseline_metrics(base: Path) -> dict:
    """book_metrics() wants a folder; the baseline dir already has the right
    file names, so point it there."""
    return book_metrics(base)


def main() -> None:
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    quiet = "--quiet" in sys.argv
    books = [d.name for d in sorted(BASELINES.iterdir())
             if d.is_dir() and (d / "MANIFEST.json").is_file()]
    if argv:
        books = [b for b in books if any(a.lower() in b.lower() for a in argv)]
    if not books:
        sys.exit("no matching baselines -- run evals/freeze.py first")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = [f"# eval check {stamp}", ""]
    any_changed = False
    for b in books:
        changed, text = check_book(b, quiet)
        any_changed |= changed
        report.append(text)
        report.append("")

    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"{stamp}.md"
    out.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    print(f"\nreport written to {out.relative_to(REPO)}")
    sys.exit(1 if any_changed else 0)


if __name__ == "__main__":
    main()
