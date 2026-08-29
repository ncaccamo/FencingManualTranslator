#!/usr/bin/env python3
"""Rename a book's stem across EVERY place it lives, in lockstep.

    scripts/rename_book.py <old_stem> <new_stem> [--dry-run]
    scripts/rename_book.py --map renames.tsv [--dry-run]     # one "old<TAB>new" per line

Moves, for each pair:
  books/<stem>.pdf  and  books/<stem>.{crop,lang,meta.json}
  processed/<stem>_translated/            (+ the 3 output PDFs inside, whose
  processed/<stem>_translated.bak-*/       names embed the old stem)
  evals/baselines/<stem>/                 (+ rewrites book/source in MANIFEST.json)

No pipeline re-run -- pure filesystem plus one JSON tweak. Missing paths
are skipped. Refuses if a destination already exists.
"""
from __future__ import annotations
from pathlib import Path
import json
import sys

REPO = Path(__file__).resolve().parent.parent
BOOKS = REPO / "books"
PROCESSED = REPO / "processed"
BASELINES = REPO / "evals" / "baselines"

DRY = "--dry-run" in sys.argv
_plan: list[tuple[Path, Path]] = []


def _mv(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if dst.exists():
        sys.exit(f"! destination exists, aborting: {dst.relative_to(REPO)}")
    _plan.append((src, dst))


def _sub_stem(name: str, old: str, new: str) -> str:
    # only rewrite a leading occurrence (the stem), keep suffixes like
    # " — English Only (Portrait).pdf" / "_OCR.pdf" / "_translated.bak-*"
    return new + name[len(old):] if name.startswith(old) else name


def plan_pair(old: str, new: str) -> None:
    # books/ pdf + sidecars
    for ext in (".pdf", ".crop", ".lang", ".meta.json"):
        _mv(BOOKS / f"{old}{ext}", BOOKS / f"{new}{ext}")

    # processed dirs: the live one and any dated backups. NB: don't glob --
    # a stem can contain '[' ']' (Gallica junk names) which glob eats.
    live = PROCESSED / f"{old}_translated"
    baks = [p for p in PROCESSED.iterdir()
            if p.name.startswith(f"{old}_translated.bak-")] \
        if PROCESSED.is_dir() else []
    for d in ([live] if live.is_dir() else []) + sorted(baks):
        new_dir = PROCESSED / _sub_stem(d.name, old, new)
        # PDFs inside embed the book name (stem minus _translated)
        for f in d.iterdir():
            if f.name.startswith(old) and f.suffix == ".pdf":
                _mv(f, d / _sub_stem(f.name, old, new))
        _mv(d, new_dir)

    # eval baseline
    b = BASELINES / old
    if b.is_dir():
        _mv(b, BASELINES / new)


def apply_manifest_fix(new: str) -> None:
    mf = BASELINES / new / "MANIFEST.json"
    if not mf.is_file():
        return
    m = json.loads(mf.read_text())
    m["book"] = new
    if isinstance(m.get("source"), str):
        m["source"] = m["source"].replace(
            m["source"].split("/")[-1], f"{new}_translated")
    if DRY:
        print(f"  [manifest] {mf.relative_to(REPO)}  book -> {new}")
    else:
        mf.write_text(json.dumps(m, indent=2) + "\n")


def main() -> None:
    if "--map" in sys.argv:
        f = Path(sys.argv[sys.argv.index("--map") + 1])
        pairs = [tuple(l.split("\t")) for l in f.read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
    else:
        pos = [a for a in sys.argv[1:] if not a.startswith("-")]
        if len(pos) != 2:
            sys.exit(__doc__)
        pairs = [(pos[0], pos[1])]

    for old, new in pairs:
        plan_pair(old.strip(), new.strip())

    if not _plan:
        sys.exit("nothing to move (no matching paths)")

    for src, dst in _plan:
        print(f"  {src.relative_to(REPO)}\n    -> {dst.relative_to(REPO)}")
    if DRY:
        for _, new in pairs:
            apply_manifest_fix(new.strip())
        print("\n(dry run -- nothing changed)")
        return

    # deepest paths first so a dir move doesn't invalidate a queued file move
    for src, dst in sorted(_plan, key=lambda p: len(str(p[0])), reverse=True):
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        print(f"moved {dst.relative_to(REPO)}")
    for _, new in pairs:
        apply_manifest_fix(new.strip())
    print("\ndone. re-freeze baselines if their content is stale:  "
          "evals/freeze.py <new_stem> --force")


if __name__ == "__main__":
    main()
