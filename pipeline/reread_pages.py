#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Re-read selected pages of an already-OCR'd book with the OCR model, and
re-derive the book's extracted text from the result.

    python3 /app/reread_pages.py <stem> <pages>      e.g.  27   or   12,40-42
    python3 /app/reread_pages.py <stem> <pages> --dry-run   (render only)

Pages are SCAN pages (1-based). For each one: render it exactly as step 1
does (300 dpi, preprocessing, the book's .crop), send it to the OCR model,
and replace that page in intermediate/raw_ocr.txt (the previous file is kept
as raw_ocr.before-reread-<date>.txt). Then reprocess_extracted.py
re-derives extracted.txt / page_numbers.txt / figures.json the normal way
-- shortening repetition runs, keeping step 2's English captions for
figures whose caption didn't change. Finally it lists the pages whose
extracted text changed; a normal step 2 run retranslates just those.

Use it for a page the OCR timed out on, misread, or cut short (Spinnewyn's
contents page, 2026-10-01: only its first line had been read). Resuming
step 1 with a page marked as failed does the re-read too, but that path
neither shortens repetition runs nor keeps the English captions.
"""
import datetime
import os
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _overrides  # noqa: E402
import _pageio  # noqa: E402

if len(sys.argv) < 3:
    sys.exit(__doc__)
stem, spec = sys.argv[1], sys.argv[2]
dry = "--dry-run" in sys.argv
import _books  # noqa: E402
if not dry:
    _books.check_unlocked(stem, sys.argv)

# step 1's definitions only (rendering, OCR call, server start) -- its main
# code runs at module level, below the STEP 1 banner
_src = open(os.path.join(HERE, "1_ocr_and_extract_vlm.py"), encoding="utf-8").read()
_ns = {"__name__": "_reread"}
exec(compile(_src.split('\nprint("\\n" + "="*60)\nprint("STEP 1')[0],
             "1_ocr_and_extract_vlm.py", "exec"), _ns)

folder = f"/data/processed/{stem}_translated"
I = f"{folder}/intermediate"
pdf = f"{I}/{stem}_OCR.pdf"
if not os.path.exists(f"{I}/raw_ocr.txt") or not os.path.exists(pdf):
    sys.exit(f"✗ {stem}: no raw_ocr.txt / OCR PDF -- run step 1 on the book first")
raw = _pageio.split_pages(open(f"{I}/raw_ocr.txt", encoding="utf-8").read())
targets = _overrides.parse_page_spec(spec)
bad = [s for s in targets if not 1 <= s <= len(raw)]
if bad:
    sys.exit(f"✗ {stem}: page(s) {bad} out of range (1..{len(raw)})")

fitz, Image = _ns["fitz"], _ns["Image"]
src_pdf = f"/data/books/{stem}.pdf"
crop = _ns["resolve_crop"](src_pdf if os.path.exists(src_pdf) else pdf)
doc = fitz.open(pdf)
proc = None if dry else _ns["start_vlm_server"]()
if not dry and proc is None:
    sys.exit("✗ cannot reach / start the OCR server")
done, failed = [], []
try:
    for s in targets:
        page = doc[s - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img = _ns["preprocess_for_ocr"](img)
        if any(crop):
            img = _ns["apply_crop_to_image"](img, crop)
        b64, kb = _ns["prepare_image_for_vlm"](img)
        if dry:
            print(f"  page {s}: rendered ({kb:.0f} KB) -- dry run, not sent")
            continue
        r = _ns["extract_page_with_vlm"](b64, kb, s - 1, len(raw))
        if r.get("success"):
            raw[s - 1] = r["content"]
            done.append(s)
        else:
            failed.append(s)
finally:
    if proc and proc is not _ns["REMOTE_SERVER"]:
        proc.terminate()

if dry:
    sys.exit(0)
if failed:
    print(f"⚠ not re-read (kept as before): {failed}")
if not done:
    sys.exit(1)

stamp = datetime.date.today().isoformat()
os.replace(f"{I}/raw_ocr.txt", f"{I}/raw_ocr.before-reread-{stamp}.txt")
with open(f"{I}/raw_ocr.txt", "w", encoding="utf-8") as fh:
    fh.write(_pageio.join_pages(raw))
print(f"✓ re-read {done}; previous raw OCR kept as raw_ocr.before-reread-{stamp}.txt")

old_ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
subprocess.run([sys.executable, os.path.join(HERE, "reprocess_extracted.py"), folder, "--unlock"], check=True)  # lock checked above
new_ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
changed = [k + 1 for k, (a, b) in enumerate(zip(old_ex, new_ex)) if a != b]
print(f"extracted text changed on scan page(s): {changed or 'none'}")
if changed and os.path.exists(f"{I}/translation.txt"):
    print(f"  retranslate them: python3 /app/2_translate.py {stem} "
          f"(picks up changed pages only)")
