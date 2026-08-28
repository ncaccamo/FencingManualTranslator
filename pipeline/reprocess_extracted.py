#!/usr/bin/env python3
"""Rebuild extracted.txt + page_numbers.txt from a saved raw_ocr.txt.

`raw_ocr.txt` holds olmOCR-2's untouched responses. The PAGE_NUMBER/HEADER/
BODY_TEXT parsing and the page-number reconciliation in
1_ocr_and_extract_vlm.py run on those. This lets you re-apply an improved
parse/reconcile to an already-OCR'd book without paying the OCR cost again.

Usage:
    python3 reprocess_extracted.py                 # every *_translated with raw_ocr.txt
    python3 reprocess_extracted.py <folder> ...    # only these
"""
import glob
import json
import os
import sys

import _pageio

# reuse the real logic, without running that module's main block
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "_vlm_prefix", os.path.join(os.path.dirname(__file__), "1_ocr_and_extract_vlm.py"))
_src = open(_spec.origin).read()
_ns = {"__name__": "_vlm_prefix"}
exec(compile(_src.split("def preprocess_for_ocr")[0], _spec.origin, "exec"), _ns)
parse_vlm_response = _ns["parse_vlm_response"]
reconcile_page_numbers = _ns["reconcile_page_numbers"]
collapse_runs = _ns["collapse_runs"]
strip_leaked_headmatter = _ns["strip_leaked_headmatter"]
_norm_head = _ns["_norm_head"]

targets = sys.argv[1:] or [
    d for d in glob.glob("/data/processed/*_translated")
    if os.path.isfile(os.path.join(d, "raw_ocr.txt"))
]

for folder in targets:
    raw_path = os.path.join(folder, "raw_ocr.txt")
    if not os.path.isfile(raw_path):
        print(f"skip (no raw_ocr.txt): {folder}")
        continue

    raw_pages = [collapse_runs(p) for p in
                 _pageio.split_pages(open(raw_path, encoding="utf-8").read())]
    parsed = [parse_vlm_response(p) for p in raw_pages]
    _kh = {_norm_head(p["header"]) for p in parsed if p["header"]}
    for p in parsed:
        p["body"] = strip_leaked_headmatter(p["body"], _kh)
    nums = reconcile_page_numbers([p["page_raw"] for p in parsed])

    canonical = []
    for raw, f, n in zip(raw_pages, parsed, nums):
        if raw.lstrip().startswith("[VLM Error"):
            canonical.append(raw)
            continue
        canonical.append(
            f"PAGE_NUMBER: {n}\nHEADER: {f['header'] or 'none'}\nBODY_TEXT:\n{f['body']}")

    figures_map = {str(i): p["figures"]
                   for i, p in enumerate(parsed) if p.get("figures")}

    with open(os.path.join(folder, "extracted.txt"), "w", encoding="utf-8") as fh:
        fh.write(_pageio.join_pages(canonical))
    with open(os.path.join(folder, "page_numbers.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(nums))
    with open(os.path.join(folder, "figures.json"), "w", encoding="utf-8") as fh:
        json.dump(figures_map, fh, ensure_ascii=False, indent=1)

    real = sum(1 for n in nums if not n.startswith("["))
    n_fig = sum(len(v) for v in figures_map.values())
    print(f"✓ {os.path.basename(folder)}: {len(raw_pages)} pages, "
          f"{real} corroborated folios, {len(nums) - real} inferred, "
          f"{n_fig} figure(s) on {len(figures_map)} page(s)")
