#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Regression check: what would re-deriving these books with the current
code change? Run it after changing step 1's parsing / reconciliation code
(1_ocr_and_extract_vlm.py, _layout.py, _margins.py, ...) and before
reprocessing for real -- a fix for one book must not quietly change another.

    python3 scripts/dry_run.py <stem> [<stem> ...]       (on the host)
    python3 scripts/dry_run.py --all
    python3 scripts/dry_run.py --all --keep              keep the scratch copies

Copies each book's step-1 data (intermediate/, the scan linked, not copied)
to processed/_audit/dry_run/, runs reprocess_extracted.py on the copies in
the container, and compares with the book as it is now: pages changed,
page numbers and running heads changed, and the text change in words --
"moved" when the same words only crossed a page break (a sentence join),
otherwise the words added / removed, which is what needs a look. No GPU;
the books themselves are not touched. Writes the comparison to
processed/_audit/dry_run/report.md as well.
"""
import difflib
import glob
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import _pageio  # noqa: E402

PROC = os.path.join(ROOT, "processed")
DRY = os.path.join(PROC, "_audit", "dry_run")


def _field(page, name):
    m = re.search(rf"(?m)^{name}: (.*)$", page)
    return m.group(1) if m else ""


def _body(page):
    return page.split("BODY_TEXT:", 1)[-1]


def compare(stem: str) -> list:
    cur = _pageio.split_pages(open(f"{PROC}/{stem}_translated/intermediate/extracted.txt", encoding="utf-8").read())
    new = _pageio.split_pages(open(f"{DRY}/{stem}_translated/intermediate/extracted.txt", encoding="utf-8").read())
    out = []
    if len(cur) != len(new):
        return [f"page count {len(cur)} -> {len(new)}"]
    pages = [i + 1 for i, (a, b) in enumerate(zip(cur, new)) if a != b]
    if not pages:
        return []
    out.append(f"{len(pages)} page(s) change: {', '.join(map(str, pages[:30]))}{' ...' if len(pages) > 30 else ''}")
    pn = [(i + 1, _field(a, 'PAGE_NUMBER'), _field(b, 'PAGE_NUMBER')) for i, (a, b) in enumerate(zip(cur, new))
          if _field(a, 'PAGE_NUMBER') != _field(b, 'PAGE_NUMBER')]
    if pn:
        out.append(f"page numbers: " + "; ".join(f"scan {s} {a!r} -> {b!r}" for s, a, b in pn[:12]))
    hd = [(i + 1, _field(a, 'HEADER'), _field(b, 'HEADER')) for i, (a, b) in enumerate(zip(cur, new))
          if _field(a, 'HEADER') != _field(b, 'HEADER')]
    if hd:
        out.append(f"running heads ({len(hd)}): " + "; ".join(f"scan {s} {a[:30]!r} -> {b[:30]!r}" for s, a, b in hd[:8]))
    wa = " ".join(_body(p) for p in cur).split()
    wb = " ".join(_body(p) for p in new).split()
    ops = [o for o in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes() if o[0] != "equal"]
    removed = [" ".join(wa[o[1]:o[2]]) for o in ops if o[0] in ("delete", "replace")]
    added = [" ".join(wb[o[3]:o[4]]) for o in ops if o[0] in ("insert", "replace")]
    if sorted(" ".join(removed).split()) == sorted(" ".join(added).split()):
        if ops:
            out.append(f"text: {len(ops)} place(s), same words moved across page breaks")
    else:
        out.append(f"text: {len(ops)} change(s) -- look at these:")
        for o in ops[:10]:
            out.append(f"    {' '.join(wa[max(0, o[1] - 3):o[2] + 3])[:70]!r} -> {' '.join(wb[max(0, o[3] - 3):o[4] + 3])[:70]!r}")
    return out


def main(argv):
    names = [a for a in argv if not a.startswith("--")]
    if "--all" in argv:
        names = sorted(os.path.basename(d)[:-len("_translated")] for d in glob.glob(f"{PROC}/*_translated"))
    if not names:
        print(__doc__)
        return 2
    names = [n for n in names if os.path.exists(f"{PROC}/{n}_translated/intermediate/raw_ocr.txt")
             and os.path.exists(f"{PROC}/{n}_translated/intermediate/extracted.txt")]
    if os.path.exists(DRY):
        shutil.rmtree(DRY)
    for n in names:
        src, dst = f"{PROC}/{n}_translated/intermediate", f"{DRY}/{n}_translated/intermediate"
        os.makedirs(dst)
        for f in os.listdir(src):
            p = os.path.join(src, f)
            if f.endswith("_OCR.pdf"):
                os.symlink(os.path.relpath(p, dst), os.path.join(dst, f))
            elif os.path.isfile(p):
                shutil.copy2(p, dst)
    print(f"re-deriving {len(names)} book(s) from copies (no GPU) ...")
    r = subprocess.run(["docker", "exec", "-w", "/app", "fmt-translator", "python3", "reprocess_extracted.py", "--unlock"]
                       + [f"/data/processed/_audit/dry_run/{n}_translated" for n in names],
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-2000:])
        sys.exit("✗ reprocess failed on the copies")
    lines = []
    for n in names:
        c = compare(n)
        lines.append(f"- **{n}**: " + ("unchanged" if not c else c[0]))
        lines += [f"    - {x}" for x in c[1:]]
    report = "# Dry run: what re-deriving would change\n\n" + "\n".join(lines) + "\n"
    open(f"{DRY}/report.md", "w", encoding="utf-8").write(report)
    print(report)
    if "--keep" not in argv:
        for n in names:
            shutil.rmtree(f"{DRY}/{n}_translated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
