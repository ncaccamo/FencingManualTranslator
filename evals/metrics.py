"""Crude per-book health metrics computed straight from the pipeline
artifacts in a `*_translated/` folder. No model calls. Cheap enough to run
on every book on every change.

A metric moving the wrong way after a prompt/rule edit is the early signal
to look at the diff -- see check.py.
"""
from __future__ import annotations
from pathlib import Path
import json
import re

import _pageio
from _rules import (contains_too_much_non_english, _shout_ratio,
                    has_wrong_script)

SKIP_MARKERS = ("[Empty page]", "[translation unavailable", "[low-confidence")


def _pages(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return _pageio.split_pages(path.read_text(encoding="utf-8"))


def book_metrics(folder: str | Path) -> dict:
    folder = Path(folder)
    tr = _pages(folder / "translation.txt")
    ex = _pages(folder / "extracted.txt")
    pn = [l.strip() for l in
          (folder / "page_numbers.txt").read_text(encoding="utf-8").splitlines()] \
        if (folder / "page_numbers.txt").is_file() else []
    figs = json.loads((folder / "figures.json").read_text(encoding="utf-8")) \
        if (folder / "figures.json").is_file() else {}
    heads = json.loads((folder / "headers.json").read_text(encoding="utf-8")) \
        if (folder / "headers.json").is_file() else {}

    bodies = [re.sub(r"^\[[^\]]+\]\n", "", p).strip() for p in tr]
    return {
        "pages": len(tr),
        "unavailable": sum(1 for p in tr if p.lstrip().startswith("[translation unavailable")),
        "low_confidence": sum(1 for p in tr if p.lstrip().startswith("[low-confidence")),
        "empty_page": sum(1 for p in tr if p.strip() == "[Empty page]"),
        "french_residue": sum(1 for b in bodies if b and contains_too_much_non_english(b)),
        "shouting": sum(1 for b in bodies if _shout_ratio(b) > 0.85),
        "wrong_script": sum(1 for b in bodies if b and has_wrong_script(b)),
        "figure_rows": sum(len(v) for v in figs.values()),
        "figure_pages": len(figs),
        "italic_spans": sum(len(re.findall(r"<i>.*?</i>", p)) for p in tr),
        "untrusted_folios": sum(1 for n in pn if n.startswith("[")),
        "trusted_folios": sum(1 for n in pn if n and not n.startswith("[")),
        "headers": len(heads),
        "fn_markers": sum(p.count("†") for p in tr),
    }


if __name__ == "__main__":
    import sys
    for f in sys.argv[1:] or []:
        print(f)
        for k, v in book_metrics(f).items():
            print(f"  {k:18} {v}")
