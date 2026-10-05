#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Patch a book's scan with pages from another copy, keeping the work
already done on every other page.

For a scan with a defect -- a missing page, a page scanned twice, a folded
plate -- that another digitised copy has right. A plan file lists the new
scan page by page:

    {"note": "Printed pp. 152-153 supplied from HathiTrust uc1.b5079424 ...",
     "pages": [1, 2, ..., 199,
               {"file": "_alt/jacob_p152.pdf", "page": 1},
               {"image": "_alt/plates/f339.jpg"},
               200, 201, 203, ...]}

A number is a page (1-based) of the ORIGINAL scan -- the untouched copy kept
at books/_alt/<stem>_original.pdf (--apply prints the host command that
keeps it), so a plan can be re-applied or changed later. "file" takes a page of another PDF, "image"
a picture file (paths relative to books/). "note" goes on the cover page
("Changes to this scan: ...", via books/<stem>.meta.json source_changes).

    python3 /app/patch_scan.py <stem> <plan.json>            # dry run: show the changes
    python3 /app/patch_scan.py <stem> <plan.json> --apply    # stage the scan, move the data
    (then the printed host commands: books/ is read-only in the container)

--apply then leaves the book ready for the usual steps, which redo only the
new pages:
    PDF_ONLY=<stem> python3 /app/1_ocr_and_extract_vlm.py   # OCR + layout + margin notes of the new pages
    python3 /app/patch_scan.py <stem> <plan.json> --finish  # re-parse, keeping translated captions
    python3 /app/2_translate.py <stem>                      # translates the new pages only
    PDF_ONLY=<stem> ... 3_english_only_pdf.py / 4_side_by_side_pdf.py ; quality_report.py <stem>

To change a plan after applying it, put the book's data back first: the
first --apply backed it up as intermediate.before_patch-<time>, and the
plan's page numbers are those of the original scan, so the data must be too
(mv intermediate aside, copy the backup to intermediate, then --apply).

Per-page data (raw OCR, layout, margin notes, translation, translation
fingerprints, figures) is keyed by scan page, so it is moved to the new
numbering; a new page starts empty and is read and translated fresh. The
whole intermediate folder is backed up first.
"""
import datetime
import json
import os
import shutil
import sys

import fitz

import _pageio

BOOKS = os.environ.get("BOOKS_DIR", "/data/books")
PROCESSED = os.environ.get("PROCESSED_DIR", "/data/processed")
NEW = "[VLM Error: new page from patch_scan.py -- to be read]"


def load_plan(path: str) -> dict:
    plan = json.load(open(path, encoding="utf-8"))
    for e in plan["pages"]:
        if not (isinstance(e, int) or (isinstance(e, dict) and ("file" in e or "image" in e))):
            sys.exit(f"bad plan entry: {e!r}")
    return plan


def original_pdf(stem: str) -> str:
    keep = f"{BOOKS}/_alt/{stem}_original.pdf"
    return keep if os.path.exists(keep) else f"{BOOKS}/{stem}.pdf"


def mapping(plan: dict) -> list:
    """new page index (0-based) -> old page index, or None for a new page."""
    return [e - 1 if isinstance(e, int) else None for e in plan["pages"]]


def describe(stem: str, plan: dict, n_old: int) -> None:
    m = mapping(plan)
    used = {o for o in m if o is not None}
    bad = [o + 1 for o in used if not 0 <= o < n_old]
    if bad:
        sys.exit(f"plan names page(s) {bad} not in the {n_old}-page original")
    dropped = [i + 1 for i in range(n_old) if i not in used]
    print(f"{stem}: {n_old} pages -> {len(m)} pages")
    print(f"  original pages not carried over (replaced or removed): {dropped or 'none'}")
    for j, e in enumerate(plan["pages"]):
        if not isinstance(e, int):
            src = e.get("file") or e.get("image")
            print(f"  new page {j + 1}: {src}" + (f" p.{e['page']}" if "page" in e else ""))
    moved = sum(1 for j, o in enumerate(m) if o is not None and o != j)
    print(f"  {moved} kept page(s) renumbered; their OCR and translation are kept")
    ov = f"{BOOKS}/{stem}.overrides.toml"
    if os.path.exists(ov) and ("scan" in open(ov).read() or "[pages." in open(ov).read()):
        print(f"  ⚠ {ov} names scan pages -- renumber them by hand (see the mapping above)")


def build_pdf(stem: str, plan: dict, out: str) -> None:
    src = fitz.open(original_pdf(stem))
    new = fitz.open()
    for e in plan["pages"]:
        if isinstance(e, int):
            new.insert_pdf(src, from_page=e - 1, to_page=e - 1)
        elif "file" in e:
            other = fitz.open(os.path.join(BOOKS, e["file"]))
            p = e.get("page", 1) - 1
            new.insert_pdf(other, from_page=p, to_page=p)
        else:
            px = fitz.Pixmap(os.path.join(BOOKS, e["image"]))   # pixel size
            dpi = e.get("dpi", 300)
            w, h = px.width * 72 / dpi, px.height * 72 / dpi
            page = new.new_page(width=w, height=h)
            page.insert_image(page.rect, filename=os.path.join(BOOKS, e["image"]))
    new.save(out, garbage=3, deflate=True)


def remap_list(old: list, m: list, fill):
    return [old[o] if o is not None and o < len(old) else fill for o in m]


def remap_keys(old: dict, m: list) -> dict:
    return {str(j): old[str(o)] for j, o in enumerate(m) if o is not None and str(o) in old}


def apply(stem: str, plan: dict) -> None:
    folder = f"{PROCESSED}/{stem}_translated"
    I = f"{folder}/intermediate"
    m = mapping(plan)
    # 1. books/ is read-only in the container (the pipeline never writes
    #    there): the new scan and meta file are staged under processed/_patch/
    #    and moved into books/ from the host (commands printed at the end)
    stage = f"{PROCESSED}/_patch/{stem}"
    os.makedirs(stage, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    bak = f"{folder}/intermediate.before_patch-{stamp}"
    shutil.copytree(I, bak)
    print(f"  data backed up: {bak}")
    # 2. the new scan
    build_pdf(stem, plan, f"{stage}/{stem}.pdf")
    n = len(m)
    # 3. raw OCR -> a partial run with the new pages still to read; step 1
    #    picks the book up because raw_ocr.txt is moved aside
    raw = _pageio.split_pages(open(f"{I}/raw_ocr.txt", encoding="utf-8").read())
    crop = [0.0, 0.0, 0.0, 0.0]
    cf = f"{BOOKS}/{stem}.crop"
    if os.path.exists(cf):
        parts = [float(x) for x in open(cf).read().strip().split(",")]
        crop = parts if len(parts) == 4 else crop
    with open(f"{I}/raw_ocr.partial.json", "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "total": n, "crop": crop,
                   "pages": remap_list(raw, m, NEW)}, fh, ensure_ascii=False)
    os.replace(f"{I}/raw_ocr.txt", f"{I}/raw_ocr.before_patch.txt")
    # 4. layout + margin notes (both resume per page: new pages are detected/read)
    for name in ("layout.json", "margins.json"):
        p = f"{I}/{name}"
        if os.path.exists(p):
            d = json.load(open(p, encoding="utf-8"))
            if name == "layout.json":
                d["pages"] = remap_keys(d["pages"], m)
            else:
                d = remap_keys(d, m)
            json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 5. translation + fingerprints: a new page has none, so step 2 translates it
    tp = f"{I}/translation.txt"
    if os.path.exists(tp):
        tr = _pageio.split_pages(open(tp, encoding="utf-8").read())
        open(tp, "w", encoding="utf-8").write(_pageio.join_pages(remap_list(tr, m, "")))
    fp = f"{I}/translated_from.json"
    if os.path.exists(fp):
        d = json.load(open(fp, encoding="utf-8"))
        d["pages"] = remap_list(d["pages"], m, "")
        d["models"] = remap_list(d.get("models") or [], m, "")
        d["stamps"] = remap_list(d.get("stamps") or [], m, None)
        json.dump(d, open(fp, "w", encoding="utf-8"), indent=0)
    # 6. figures (with their translated captions): step 1 rewrites
    #    figures.json, so keep the moved copy for --finish
    gp = f"{I}/figures.json"
    if os.path.exists(gp):
        json.dump(remap_keys(json.load(open(gp, encoding="utf-8")), m),
                  open(f"{I}/figures.patched.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    # the old extracted text, moved: --finish compares against it to list
    # the pages that need translating
    ep = f"{I}/extracted.txt"
    if os.path.exists(ep):
        ex = _pageio.split_pages(open(ep, encoding="utf-8").read())
        open(f"{I}/extracted.patched_old.txt", "w", encoding="utf-8").write(
            _pageio.join_pages(remap_list(ex, m, "")))
    json.dump([j + 1 for j, o in enumerate(m) if o is None],
              open(f"{I}/patch_new_pages.json", "w"))
    for stale in ("translation.partial.json", "retranslate.partial.json"):
        if os.path.exists(f"{I}/{stale}"):
            os.remove(f"{I}/{stale}")
    # 7. the cover page note
    mp = f"{BOOKS}/{stem}.meta.json"
    moves = [f"mv processed/_patch/{stem}/{stem}.pdf books/"]
    if plan.get("note") and os.path.exists(mp):
        meta = json.load(open(mp, encoding="utf-8"))
        meta["source_changes"] = plan["note"]
        open(f"{stage}/{stem}.meta.json", "w", encoding="utf-8").write(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        moves.append(f"mv processed/_patch/{stem}/{stem}.meta.json books/")
    keep = [] if os.path.exists(f"{BOOKS}/_alt/{stem}_original.pdf") else \
        [f"cp -p books/{stem}.pdf books/_alt/{stem}_original.pdf"]
    print(f"  new scan staged: {stage}/{stem}.pdf ({n} pages)")
    print("  now, on the host, from the repo folder (keeps the original first):")
    for c in keep + moves:
        print(f"    {c}")
    print(f"  then: PDF_ONLY={stem} python3 /app/1_ocr_and_extract_vlm.py")


def finish(stem: str) -> None:
    """After step 1 read the new pages: put back the moved figures (with
    their translated captions) and re-parse, so an unchanged caption keeps
    its translation (reprocess_extracted.py merges them)."""
    I = f"{PROCESSED}/{stem}_translated/intermediate"
    if not os.path.exists(f"{I}/raw_ocr.txt"):
        sys.exit("raw_ocr.txt missing -- run step 1 first")
    if os.path.exists(f"{I}/figures.patched.json"):
        os.replace(f"{I}/figures.patched.json", f"{I}/figures.json")
    import subprocess
    subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "reprocess_extracted.py"),
                    f"{PROCESSED}/{stem}_translated", "--unlock"], check=True)  # lock checked in main()
    # pages to translate: the new ones, and any kept page whose text the
    # re-parse changed (e.g. a running head settled differently next to a
    # new page)
    body = lambda p: " ".join(p.split("BODY_TEXT:", 1)[-1].split())
    new = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
    oldp = f"{I}/extracted.patched_old.txt"
    old = _pageio.split_pages(open(oldp, encoding="utf-8").read()) if os.path.exists(oldp) else []
    old += [""] * (len(new) - len(old))
    todo = {k + 1 for k, (a, b) in enumerate(zip(old, new)) if body(a) != body(b)}
    np_ = f"{I}/patch_new_pages.json"
    if os.path.exists(np_):
        todo |= set(json.load(open(np_)))      # new pages: text and captions
    todo = sorted(todo)
    print(f"  {len(todo)} page(s) to translate: {todo}")
    print(f"  next: python3 /app/2_translate.py --pages {stem} {','.join(map(str, todo))}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    stem, plan_path = args
    if "--apply" in sys.argv or "--finish" in sys.argv:
        import _books
        _books.check_unlocked(stem, sys.argv)
    if "--finish" in sys.argv:
        finish(stem)
        return
    plan = load_plan(plan_path)
    describe(stem, plan, len(fitz.open(original_pdf(stem))))
    if "--apply" in sys.argv:
        apply(stem, plan)
    else:
        print("  (dry run -- add --apply to build the scan and move the data)")


if __name__ == "__main__":
    main()
