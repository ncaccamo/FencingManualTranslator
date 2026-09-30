#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Extract + clean a gold book's text from a PDF that already has a usable
text layer (skips OCR). Writes  evals/gold/<stem>/<side>.txt  with:

  - running headers/footers stripped (detected by cross-page frequency)
  - end-of-line hyphenation joined
  - lines reflowed into paragraphs (the archive.org one-word-per-line
    text layers collapse cleanly since the tokens themselves are fine)
  - a  <!--p N-->  marker before each source page, for mapping back

    python3 /data/evals/gold_extract.py bazancourt_1862_secrets            # both sides
    python3 /data/evals/gold_extract.py bazancourt_1862_secrets src        # one side
    python3 /data/evals/gold_extract.py bazancourt_1862_secrets --pages 30-260
"""
import re
import sys
from collections import Counter
from pathlib import Path

import fitz

GOLD = Path("/data/evals/gold")


def _strip_running(pages: list[str], scan: int = 4) -> list[str]:
    """Drop running heads / folios from the top and bottom of each page.
    A physical line is junk if, with digits blanked, it recurs across many
    pages within the first/last `scan` lines -- or is a bare folio."""
    def key(line: str) -> str:
        return re.sub(r"\d+", "#", line).strip().lower()

    def folio(line: str) -> bool:
        return bool(re.fullmatch(r"[\dIVXLCDM]{1,5}\.?", line.strip(), re.I))

    top, bot = Counter(), Counter()
    for p in pages:
        ls = [l for l in p.split("\n") if l.strip()]
        for l in ls[:scan]:
            top[key(l)] += 1
        for l in ls[-scan:]:
            bot[key(l)] += 1
    th = max(5, len(pages) // 6)
    hdr = {k for k, c in top.items() if c >= th and 0 < len(k.split()) <= 6}
    ftr = {k for k, c in bot.items() if c >= th and 0 < len(k.split()) <= 6}

    def junk(line: str, pool: set) -> bool:
        return not line.strip() or folio(line) or key(line) in pool

    out = []
    for p in pages:
        lines = p.split("\n")
        for _ in range(scan):
            if lines and junk(lines[0], hdr):
                lines.pop(0)
            else:
                break
        for _ in range(scan):
            if lines and junk(lines[-1], ftr):
                lines.pop()
            else:
                break
        out.append("\n".join(lines))
    return out


def _reflow(text: str) -> str:
    text = re.sub(r"([A-Za-zÀ-ÿ])-\n([a-zà-ÿ])", r"\1\2", text)      # de-hyphenate
    # paragraph break: blank line, OR a short line that ends a sentence
    # followed by a capital / dash / quote (dialogue), OR a lone heading.
    paras, buf = [], []
    for raw in text.split("\n"):
        ln = raw.strip()
        if not ln:
            if buf:
                paras.append(" ".join(buf)); buf = []
            continue
        if re.fullmatch(r"[IVXLC]{1,6}\.?|[0-9]{1,3}\.?|[-–—*]{1,3}", ln):
            if buf:
                paras.append(" ".join(buf)); buf = []
            paras.append(ln)
            continue
        buf.append(ln)
        if re.search(r'[.!?»"”)]\s*$', ln) and len(ln) < 60:
            paras.append(" ".join(buf)); buf = []
    if buf:
        paras.append(" ".join(buf))
    txt = "\n\n".join(p for p in paras if p)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r" ([,;:.!?»])", r"\1", txt)
    txt = re.sub(r"« ", "« ", txt).replace(" »", " »")
    return txt.strip()


def extract(stem: str, side: str, pg_range: tuple[int, int] | None) -> None:
    d = GOLD / stem
    doc = fitz.open(d / f"{side}.pdf")
    lo, hi = (pg_range or (0, len(doc)))
    raw_pages = [doc[i].get_text() for i in range(lo, min(hi, len(doc)))]
    cleaned = _strip_running(raw_pages)
    chunks = []
    for i, p in zip(range(lo, hi), cleaned):
        body = _reflow(p)
        if body:
            chunks.append(f"<!--p {i}-->\n{body}")
    out = d / f"{side}.txt"
    out.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    words = sum(len(c.split()) for c in chunks)
    print(f"  {stem}/{side}: {len(chunks)} pages, {words} words -> {out}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    stem = args[0]
    sides = args[1:] or ["src", "ref"]
    rng = None
    for a in sys.argv:
        m = re.fullmatch(r"--pages=?(\d+)-(\d+)", a) or \
            (a.startswith("--pages") and None)
        if m:
            rng = (int(m.group(1)), int(m.group(2)))
    if "--pages" in sys.argv:
        i = sys.argv.index("--pages")
        a, b = sys.argv[i + 1].split("-")
        rng = (int(a), int(b))
    for side in sides:
        if (GOLD / stem / f"{side}.pdf").exists():
            extract(stem, side, rng)


if __name__ == "__main__":
    main()
