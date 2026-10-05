#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""A second look at page numbers: ask the OCR model what number, if any, is
printed in a page's top and bottom margins -- only for pages whose number
is currently hidden (a bracketed "[n]" in page_numbers.txt: worked out from
the neighbours, but not confirmed by the OCR's own reading) or missing.

    python3 /app/read_folios.py <stem> [<stem> ...]
    python3 /app/read_folios.py <stem> --pages 13,25-26
    python3 /app/read_folios.py <stem> [<stem> ...] --apply
        apply the stored readings to page_numbers.txt and extracted.txt
        directly (page numbers only, no re-derive; pages whose number is set
        in the overrides file are left alone); rebuild the PDFs after

Answers go to intermediate/folio_strips.json ({scan: "117" | "xiii" |
"NONE" | ...}); pages already read are skipped. Nothing else changes until
the book is re-derived (reprocess_extracted.py / step 1): the page-number
step then shows a hidden number only where the strip read names exactly
that number (apply_folio_witness). It never hides or changes a number.

Why only as a witness: on 16 pages with known answers the strip read was
right 11 times -- it missed printed numbers, repeated an OCR misread, took
a printer's gathering mark for a folio (2026-10-01). Agreement with the
number the sequence predicts is what makes a reading trustworthy.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _overrides  # noqa: E402

PROMPT = ("This image shows only the top and bottom margins of one page of an old printed "
          "book, stacked one above the other. If a page number is printed there (arabic like "
          "117, or roman like xiv or viij), answer with that page number exactly as printed. "
          "If no page number is printed, answer NONE. Answer with the page number or NONE only.")
BAND = 0.13          # top / bottom share of the page height


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    spec = sys.argv[sys.argv.index("--pages") + 1] if "--pages" in sys.argv else None
    if spec:
        args.remove(spec)
    import _books
    args = [_books.stem_of(n) for n in _books.select(
        args + [a for a in sys.argv[1:] if a.startswith("--")], __doc__)]
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "1_ocr_and_extract_vlm.py"),
               encoding="utf-8").read()
    ns = {"__name__": "_folios"}
    exec(compile(src.split('\nprint("\\n" + "="*60)\nprint("STEP 1')[0], "step1", "exec"), ns)
    if "--apply" in sys.argv:
        for stem in args:
            apply(stem, ns)
        return
    fitz, Image, requests, bc = ns["fitz"], ns["Image"], ns["requests"], ns["bc"]
    if ns["start_vlm_server"]() is None:
        sys.exit("✗ cannot reach / start the OCR server")
    for stem in args:
        I = f"/data/processed/{stem}_translated/intermediate"
        nums = open(f"{I}/page_numbers.txt", encoding="utf-8").read().split("\n")
        pages = (_overrides.parse_page_spec(spec) if spec
                 else [i + 1 for i, n in enumerate(nums) if n.startswith("[") or not n.strip()])
        out_p = f"{I}/folio_strips.json"
        out = json.load(open(out_p, encoding="utf-8")) if os.path.exists(out_p) else {}
        todo = [s for s in pages if str(s) not in out]
        print(f"{stem}: {len(todo)} page(s) to read ({len(pages) - len(todo)} already read)")
        doc = fitz.open(f"{I}/{stem}_OCR.pdf")
        for s in todo:
            pix = doc[s - 1].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            h, b = img.height, int(img.height * BAND)
            strip = Image.new("RGB", (img.width, 2 * b + 40), "white")
            strip.paste(img.crop((0, 0, img.width, b)), (0, 0))
            strip.paste(img.crop((0, h - b, img.width, h)), (0, b + 40))
            b64, _ = ns["prepare_image_for_vlm"](strip)
            try:
                r = requests.post(f"{bc.ocr_base_url()}/v1/chat/completions",
                                  headers={"Content-Type": "application/json", **bc.auth_headers()},
                                  json={"model": ns["VLM_MODEL_NAME"], "temperature": 0, "max_tokens": 12,
                                        "messages": [{"role": "user", "content": [
                                            {"type": "text", "text": PROMPT},
                                            {"type": "image_url",
                                             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]},
                                  timeout=120)
                ans = r.json()["choices"][0]["message"]["content"].strip() if r.status_code == 200 else None
            except Exception:
                ans = None
            if ans is None:
                print(f"  scan {s}: no answer (kept unread)")
                continue
            out[str(s)] = ans
            print(f"  scan {s}: {ans!r}  (now {nums[s - 1]!r})", flush=True)
            with open(out_p, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=1)


def apply(stem, ns):
    """Show the hidden page numbers the stored readings confirm, in
    page_numbers.txt and each page's PAGE_NUMBER line -- nothing else."""
    import re
    import _pageio
    I = f"/data/processed/{stem}_translated/intermediate"
    fs = f"{I}/folio_strips.json"
    if not os.path.exists(fs):
        print(f"{stem}: no folio_strips.json -- read first")
        return
    nums = open(f"{I}/page_numbers.txt", encoding="utf-8").read().split("\n")
    new = ns["apply_folio_witness"](nums, json.load(open(fs, encoding="utf-8")))
    decided = {s for s, v in _overrides.load(stem)["pages"].items() if "page_number" in v}
    ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
    changed = []
    for i, (a, b) in enumerate(zip(nums, new)):
        if a != b and i + 1 not in decided and i < len(ex):
            nums[i] = b
            ex[i] = re.sub(r"(?m)^(\s*PAGE_NUMBER:).*$", lambda m: f"{m.group(1)} {b}", ex[i], count=1)
            changed.append((i + 1, a, b))
    if changed:
        with open(f"{I}/page_numbers.txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(nums))
        with open(f"{I}/extracted.txt", "w", encoding="utf-8") as fh:
            fh.write(_pageio.join_pages(ex))
    print(f"{stem}: {len(changed)} page number(s) shown: "
          + ", ".join(f"scan {s} {a}->{b}" for s, a, b in changed[:12])
          + (" ..." if len(changed) > 12 else ""))


if __name__ == "__main__":
    main()
