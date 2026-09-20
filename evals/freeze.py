#!/usr/bin/env python3
"""Snapshot a book's accepted pipeline output as a regression baseline.

    python3 evals/freeze.py Joseph-Renaud            # substring-matches a *_translated dir
    python3 evals/freeze.py Joseph-Renaud --force    # overwrite an existing baseline

Freezes the TEXT artifacts only (translation.txt, extracted.txt,
figures.json, headers.json, page_numbers.txt) -- never the PDFs, which are
large, binary, and fully regenerable from these. Writes a MANIFEST.json
recording the git commit and time, so a later `check.py` says what it is
comparing against.

Baselines live in  evals/baselines/<book>/  and ARE committed -- that is
how "why did the baseline change" stays in git history. ~1-2 MB per book.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent
PROCESSED = REPO / "processed"
BASELINES = Path(__file__).resolve().parent / "baselines"
ARTIFACTS = ("translation.txt", "extracted.txt", "figures.json",
             "headers.json", "page_numbers.txt")


def _resolve(pattern: str) -> Path:
    hits = sorted(d for d in PROCESSED.glob("*_translated")
                  if pattern.lower() in d.name.lower())
    if not hits:
        sys.exit(f"no processed/*_translated dir matches {pattern!r}")
    if len(hits) > 1 and "sample" not in pattern.lower():
        non_sample = [h for h in hits if "sample" not in h.name.lower()]
        if len(non_sample) == 1:
            hits = non_sample
    if len(hits) > 1:
        sys.exit("ambiguous: " + ", ".join(h.name for h in hits))
    return hits[0]


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(REPO), *args],
                                       text=True).strip()
    except Exception:
        return "?"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.is_file() else "-"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = "--force" in sys.argv
    if not args:
        sys.exit(__doc__)
    src = _resolve(args[0])
    book = src.name.removesuffix("_translated")
    dst = BASELINES / book

    if dst.exists() and not force:
        sys.exit(f"baseline {dst.relative_to(REPO)} exists -- pass --force to replace")
    dst.mkdir(parents=True, exist_ok=True)

    frozen = {}
    for name in ARTIFACTS:
        f = src / "intermediate" / name
        if f.is_file():
            shutil.copy2(f, dst / name)
            frozen[name] = _sha(f)
        else:
            frozen[name] = None

    (dst / "MANIFEST.json").write_text(json.dumps({
        "book": book,
        "source": str(src.relative_to(REPO)),
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "artifacts": frozen,
    }, indent=2) + "\n")

    print(f"froze {book}  ->  {dst.relative_to(REPO)}")
    for k, v in frozen.items():
        print(f"  {k:20} {v or '(absent)'}")
    print("\nremember to: git add evals/baselines/" + book)


if __name__ == "__main__":
    main()
