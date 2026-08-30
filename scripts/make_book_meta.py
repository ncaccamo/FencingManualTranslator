#!/usr/bin/env python3
"""Generate books/<stem>.meta.json from the `status: have` rows of
books/library.md, for the PDF cover page (see pipeline/_cover.py).

    python3 scripts/make_book_meta.py            # create missing sidecars
    python3 scripts/make_book_meta.py --force    # overwrite existing too
    python3 scripts/make_book_meta.py <stem> ... # only these stems

books/ is git-ignored (private corpus); so are the sidecars this writes.
The generator only fills what the registry table carries reliably --
`publisher` is almost always left "" for you to hand-add where it matters.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "books" / "library.md"
OUT = ROOT / "books"

_ID_RE = re.compile(r"`([a-z0-9][a-z0-9_-]+)`")
_BACKTICK = re.compile(r"`([^`]+)`")


def _clean(cell: str) -> str:
    s = cell.strip().strip("*").strip()
    s = s.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", s).strip(" ~")


def _source_url(source: str, sid: str) -> str:
    if not sid:
        return ""
    s = source.lower()
    if "gallica" in s:
        return f"https://gallica.bnf.fr/ark:/12148/{sid}"
    if "archive.org" in s:
        return f"https://archive.org/details/{sid}"
    if "google" in s:
        return f"https://books.google.com/books?id={sid}"
    return ""


def parse_rows():
    lang = ""
    cols = []
    for raw in LIB.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            head = line[3:].strip().lower()
            lang = ("French" if head.startswith("french")
                    else "Italian" if head.startswith("italian") else "")
            cols = []
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):          # header underline
            continue
        if cells and cells[0].lower() in ("stem", "| stem"):
            cols = [c.lower() for c in cells]
            continue
        if not cols or not _ID_RE.fullmatch(cells[0]):
            continue
        row = dict(zip(cols, cells))
        if "have" not in row.get("status", "").lower():
            continue
        stem = _clean(cells[0])
        src_cell = row.get("source id / url", "")
        m = _BACKTICK.search(src_cell)
        sid = m.group(1).strip() if m else ""
        source = _clean(_BACKTICK.sub("", src_cell)).rstrip(" /-—")
        meta = {
            "stem": stem,
            "title": _clean(row.get("title", "")),
            "author": _clean(row.get("author(s)", row.get("author", ""))),
            "year": _clean(row.get("year", "")),
            "publisher": "",
            "language": lang,
            "source": source if source not in ("", "-", "—") else "",
            "source_id": sid,
            "source_url": _source_url(source, sid),
            "source_terms": ("Gallica digitisation — free for non-commercial reuse"
                             if "gallica" in source.lower() else ""),
        }
        ym = re.match(r"\d{4}", meta["year"])
        if ym and int(ym.group()) >= 1930:
            meta["rights"] = ("The original may still be under copyright — "
                              "verify before distributing.")
        yield stem, meta


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = "--force" in sys.argv[1:]
    wrote = skipped = 0
    for stem, meta in parse_rows():
        if args and stem not in args:
            continue
        dest = OUT / f"{stem}.meta.json"
        if dest.exists() and not force:
            skipped += 1
            continue
        dest.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"  wrote {dest.relative_to(ROOT)}")
        wrote += 1
    print(f"\n{wrote} written, {skipped} left in place "
          f"(--force to overwrite).")


if __name__ == "__main__":
    main()
