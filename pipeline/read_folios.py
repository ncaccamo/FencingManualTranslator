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

Answers go to intermediate/folio_strips.json ({scan: [top, bottom]}, each
"117" | "xiii" | "NONE" | ...); pages already read this way are skipped (an
older single-string reading, from the full-width strip, is read again). Nothing else changes until
the book is re-derived (reprocess_extracted.py / step 1): the page-number
step then shows a hidden number only where the strip read names exactly
that number (apply_folio_witness). It never hides or changes a number.

Why only as a witness: on 16 pages with known answers the strip read was
right 11 times -- it missed printed numbers, repeated an OCR misread, took
a printer's gathering mark for a folio (2026-10-01). Agreement with the
number the sequence predicts is what makes a reading trustworthy.

The crop (2026-10-09, evals/folio_choice_test.py): each margin is cut from
the printed area the layout detector found (intermediate/layout.json), not
a fixed share of the image -- on a photographed page that share can stop
above the head line (Jacob-André scan 14: the old strip missed "II", the
new crop reads it) -- then cut in thirds, stacked, so the model sees it
larger. On 137 labelled pages: 40 of 47 printed numbers confirmed, none
shown wrong, no number on any of 90 pages that print none. A choice
question ("II, III or IV?") was tested and rejected: it repeats the values
offered.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _overrides  # noqa: E402

PROMPT = ("This image shows one margin of a page of an old printed book, cut into three pieces "
          "stacked one above the other. If a page number is printed in it (arabic like 117, or "
          "roman like xiv or viij), answer with that page number exactly as printed. If no page "
          "number is printed, answer NONE. Answer with the page number or NONE only.")


def text_box(layout_page):
    """The printed area (x0, y0, x1, y1, fractions) from one layout.json page:
    union of its boxes except pictures (a stamp or vignette above a heading
    would push the top margin up into blank paper); None if no text."""
    boxes = [b[2] for b in (layout_page or {}).get("boxes", []) if b[0] != "picture"]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def margins(img, box):
    """Top and bottom margin crops: from well outside the printed area to just
    inside its first / last line, a little wider than it. No printed area
    (blank page, plate): the top and bottom fifth of the image."""
    W, H = img.size
    if not box:
        b = int(H * 0.2)
        return img.crop((0, 0, W, b)), img.crop((0, H - b, W, H))
    x0, y0, x1, y1 = box
    xa, xb = int(max(0, x0 - 0.08) * W), int(min(1, x1 + 0.08) * W)
    return (img.crop((xa, int(max(0, y0 - 0.09) * H), xb, int(min(1, y0 + 0.035) * H))),
            img.crop((xa, int(max(0, y1 - 0.035) * H), xb, int(min(1, y1 + 0.09) * H))))


def thirds(Image, band):
    """A margin cut in thirds, stacked: the same content ~1.8x larger after
    the model's resize."""
    w3 = band.width // 3
    out = Image.new("RGB", (w3, 3 * band.height + 40), "white")
    for k in range(3):
        out.paste(band.crop((k * w3, 0, (k + 1) * w3, band.height)), (0, k * (band.height + 20)))
    return out


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
    fitz, Image = ns["fitz"], ns["Image"]
    if ns["start_vlm_server"]() is None:
        sys.exit("✗ cannot reach / start the OCR server")
    for stem in args:
        I = f"/data/processed/{stem}_translated/intermediate"
        nums = open(f"{I}/page_numbers.txt", encoding="utf-8").read().split("\n")
        pages = (_overrides.parse_page_spec(spec) if spec
                 else [i + 1 for i, n in enumerate(nums) if n.startswith("[") or not n.strip()])
        out_p = f"{I}/folio_strips.json"
        out = json.load(open(out_p, encoding="utf-8")) if os.path.exists(out_p) else {}
        todo = [s for s in pages if not isinstance(out.get(str(s)), list)]
        print(f"{stem}: {len(todo)} page(s) to read ({len(pages) - len(todo)} already read)")
        doc = fitz.open(f"{I}/{stem}_OCR.pdf")
        lay_p = f"{I}/layout.json"
        lay = json.load(open(lay_p, encoding="utf-8"))["pages"] if os.path.exists(lay_p) else {}
        for s in todo:
            pix = doc[s - 1].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            ans = [ask(ns, thirds(Image, m)) for m in margins(img, text_box(lay.get(str(s - 1))))]
            if None in ans:
                print(f"  scan {s}: no answer (kept unread)")
                continue
            out[str(s)] = ans
            print(f"  scan {s}: top {ans[0]!r}, bottom {ans[1]!r}  (now {nums[s - 1]!r})", flush=True)
            with open(out_p, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=1)


def ask(ns, img):
    """The OCR model's answer for one margin, or None (no answer)."""
    requests, bc = ns["requests"], ns["bc"]
    b64, _ = ns["prepare_image_for_vlm"](img)
    try:
        r = requests.post(f"{bc.ocr_base_url()}/v1/chat/completions",
                          headers={"Content-Type": "application/json", **bc.auth_headers()},
                          json={"model": ns["VLM_MODEL_NAME"], "temperature": 0, "max_tokens": 12,
                                "messages": [{"role": "user", "content": [
                                    {"type": "text", "text": PROMPT},
                                    {"type": "image_url",
                                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]},
                          timeout=120)
        return r.json()["choices"][0]["message"]["content"].strip() if r.status_code == 200 else None
    except Exception:
        return None

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
