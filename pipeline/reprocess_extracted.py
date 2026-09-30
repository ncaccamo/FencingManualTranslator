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
import re
import os
import sys

import _pageio
import _layout
import _margins
import _overrides

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
tail_catchwords = _ns["tail_catchwords"]
text_layer_folio_bands = _ns["text_layer_folio_bands"]
settle_disputed_heads = _ns["settle_disputed_heads"]
has_page_number_field = _ns["has_page_number_field"]
apply_read_heads = _ns["apply_read_heads"]
leaked_top_header = _ns["leaked_top_header"]
is_plate_leaf = _ns["is_plate_leaf"]
_norm_head = _ns["_norm_head"]
_fold_caption_only_body = _ns["_fold_caption_only_body"]
postprocess_parsed_pages = _ns["postprocess_parsed_pages"]
fold_caption_header_into_figure = _ns["fold_caption_header_into_figure"]
reconcile_headers = _ns["reconcile_headers"]
known_headmatter = _ns["known_headmatter"]

targets = sys.argv[1:] or [
    d for d in glob.glob("/data/processed/*_translated")
    if os.path.isfile(os.path.join(d, "intermediate", "raw_ocr.txt"))
]

for folder in targets:
    int_dir = os.path.join(folder, "intermediate")
    raw_path = os.path.join(int_dir, "raw_ocr.txt")
    if not os.path.isfile(raw_path):
        print(f"skip (no raw_ocr.txt): {folder}")
        continue

    raw_pages = [collapse_runs(p) for p in
                 _pageio.split_pages(open(raw_path, encoding="utf-8").read())]
    parsed = [parse_vlm_response(p) for p in raw_pages]
    postprocess_parsed_pages(parsed)
    # running heads read from the page where the OCR reply left the field out
    _hp = os.path.join(int_dir, "heads.json")
    if os.path.isfile(_hp):
        apply_read_heads(parsed, raw_pages, json.load(open(_hp, encoding="utf-8")))
    _kh = known_headmatter(parsed)
    # pages the layout detector is sure print no running head (_layout.py)
    _no_head = _layout.no_head_pages(folder, [p.get("header") for p in parsed])
    _disputed = _layout.disputed_heads(folder, [p.get("header") for p in parsed])
    _catch = tail_catchwords(parsed)
    for i, p in enumerate(parsed):
        if i in _no_head:
            continue
        own = (p.get("header") or "").strip()   # the VLM's own, pre-promotion
        if not (p.get("header") or "").strip():
            promoted = leaked_top_header(p["body"], _kh)
            if promoted:
                p["header"] = promoted
        p["body"] = strip_leaked_headmatter(p["body"], _kh, own, _catch)
    _fold_caption_only_body(parsed)
    fold_caption_header_into_figure(parsed)
    _no_head |= settle_disputed_heads(parsed, _disputed)
    reconcile_headers(parsed, _no_head)
    # margin notes as ⟦MARGIN: …⟧ lines, each before its paragraph (_margins.py)
    _m_placed, _m_lost = _margins.apply_to_book(folder, parsed)
    _is_plate = [any(f.get("kind") == "plate" for f in (p.get("figures") or []))
                 or is_plate_leaf(p.get("body"))
                 for p in parsed]
    _pdfs = glob.glob(f"{folder}/intermediate/*_OCR.pdf")
    nums = reconcile_page_numbers([p["page_raw"] for p in parsed], _is_plate,
                                  [p.get("body") for p in parsed],
                                  text_layer_folio_bands(_pdfs[0]) if _pdfs else None,
                                  [has_page_number_field(r) for r in raw_pages])
    # the reviewer's per-book decisions always win (see _overrides.py)
    nums = _overrides.apply_extraction(
        parsed, nums, _overrides.load(os.path.basename(folder).replace("_translated", "")))

    canonical = []
    for raw, f, n in zip(raw_pages, parsed, nums):
        if raw.lstrip().startswith("[VLM Error"):
            canonical.append(raw)
            continue
        canonical.append(
            f"PAGE_NUMBER: {n}\nHEADER: {f['header'] or 'none'}\nBODY_TEXT:\n{f['body']}")

    figures_map = {str(i): p["figures"]
                   for i, p in enumerate(parsed) if p.get("figures")}

    with open(os.path.join(int_dir, "extracted.txt"), "w", encoding="utf-8") as fh:
        fh.write(_pageio.join_pages(canonical))
    with open(os.path.join(int_dir, "page_numbers.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(nums))
    # Keep step 2's work: it cleans each caption and adds its translation
    # (caption_en) in place. A figure whose caption is unchanged by this
    # reprocess keeps both; without this every translated caption was lost.
    _fp = os.path.join(int_dir, "figures.json")
    _old = json.load(open(_fp, encoding="utf-8")) if os.path.exists(_fp) else {}
    _words = lambda c: set(re.findall(r"\w+", (c or "").lower()))
    for k, figs in figures_map.items():
        olds = _old.get(k) or []
        for j, f in enumerate(figs):
            o = olds[j] if j < len(olds) else None
            # step 2 only ever REMOVES from a caption (a description in
            # parentheses, a library stamp) -- so the cleaned caption's words
            # are a subset of the raw one's when it's the same caption
            if o and o.get("kind") == f.get("kind") and _words(o.get("caption")) <= _words(f.get("caption")):
                f["caption"] = o.get("caption", "")
                if o.get("caption_en"):
                    f["caption_en"] = o["caption_en"]
    with open(os.path.join(int_dir, "figures.json"), "w", encoding="utf-8") as fh:
        json.dump(figures_map, fh, ensure_ascii=False, indent=1)

    real = sum(1 for n in nums if n and not n.startswith("["))
    n_fig = sum(len(v) for v in figures_map.values())
    print(f"✓ {os.path.basename(folder)}: {len(raw_pages)} pages, "
          f"{real} corroborated folios, {len(nums) - real} inferred, "
          f"{n_fig} figure(s) on {len(figures_map)} page(s)"
          + (f", {_m_placed} margin note(s)" + (f" ({_m_lost} unanchored)" if _m_lost else "")
             if _m_placed else ""))
