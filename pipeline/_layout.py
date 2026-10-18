# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Page-layout detection: which printed line is a running head.

olmOCR-2 transcribes well but is unreliable at telling a running head (the
small line in the top margin, usually beside the folio) from a title or the
first line of text -- the source of most header bugs in batch-2 review. A
layout-detection model answers exactly that question from the page image.

Model: docling-project/docling-layout-heron (IBM Docling; RT-DETR v2 trained
on DocLayNet; Apache-2.0), baked into the image at a pinned revision, CPU
only (~0.5 s/page). Classes include page_header, title, section_header,
text, picture, caption. Evaluation on all 1,673 pages of the 6-book corpus
(2026-09-25, review-notes/archive/experiments/TITLE_PROMPT_TEST.md): where it disagreed with the
text-heuristic headers, the scans sided with it on nearly every page checked.

Results are cached per book in intermediate/layout.json so the model never
runs twice for the same scan: {"model": <rev>, "pages": {idx: {"w", "h",
"boxes": [[label, score, [x0, y0, x1, y1] as fractions of the page]]}}}.
"""
import json
import re
import os
import statistics

MODEL_DIR = os.environ.get("LAYOUT_MODEL_DIR", "/opt/layout/docling-layout-heron")
MODEL_REV = "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"
RENDER_DPI = 150
BODY = ("text", "list_item", "table", "footnote", "formula")
HEADISH = ("page_header", "section_header", "title")

_model = None


def _load():
    global _model
    if _model is None:
        import torch
        from transformers import RTDetrV2ForObjectDetection, RTDetrImageProcessor
        torch.set_num_threads(max(1, (os.cpu_count() or 2) - 2))
        proc = RTDetrImageProcessor.from_pretrained(MODEL_DIR, local_files_only=True)
        model = RTDetrV2ForObjectDetection.from_pretrained(MODEL_DIR, local_files_only=True).eval()
        _model = (torch, proc, model)
    return _model


def detect_page(page) -> dict:
    """page: a fitz Page. Returns {"w", "h", "boxes"} (boxes >= 0.3 score)."""
    import fitz
    from PIL import Image
    torch, proc, model = _load()
    pix = page.get_pixmap(dpi=RENDER_DPI, colorspace=fitz.csRGB)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    with torch.no_grad():
        out = model(**proc(images=img, return_tensors="pt"))
    det = proc.post_process_object_detection(
        out, target_sizes=torch.tensor([img.size[::-1]]), threshold=0.3)[0]
    lab = model.config.id2label
    boxes = [[lab[int(l)], round(float(s), 3),
              [round(float(v) / d, 4) for v, d in zip(b, (img.width, img.height) * 2)]]
             for s, l, b in zip(det["scores"], det["labels"], det["boxes"])]
    return {"w": img.width, "h": img.height, "boxes": boxes}


def scan_book(folder: str, pdf_path: str, force: bool = False, progress=print) -> dict:
    """Detect every page of the book's scan PDF into intermediate/layout.json
    (resuming a partial file). Returns the loaded/updated data."""
    import fitz
    path = f"{folder}/intermediate/layout.json"
    data = {"model": MODEL_REV, "pages": {}}
    if os.path.exists(path) and not force:
        data = json.load(open(path, encoding="utf-8"))
        if data.get("model") != MODEL_REV:
            data = {"model": MODEL_REV, "pages": {}}
    doc = fitz.open(pdf_path)
    todo = [i for i in range(len(doc)) if str(i) not in data["pages"]]
    for k, i in enumerate(todo, 1):
        data["pages"][str(i)] = detect_page(doc[i])
        if k % 25 == 0 or k == len(todo):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            if progress:
                progress(f"  layout: {k}/{len(todo)} page(s)")
    if not todo and not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return data


def load(folder: str):
    path = f"{folder}/intermediate/layout.json"
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None


def no_running_heads(folder: str, books_dir: str = "/data/books") -> bool:
    """The reviewer's [book] running_heads = false (_overrides.py): the
    book prints none, and the detector's page_header box on its pages is
    the page number (Florio's "~ 3 ~", Bruno's "19", Lambertini's corner
    folio -- scan-checked 2026-10-05)."""
    import _overrides
    try:
        ov = _overrides.load(os.path.basename(folder.rstrip("/")).replace("_translated", ""),
                             books_dir)
    except _overrides.OverridesError:
        return False
    return ov["book"].get("running_heads") is False


def head_pages(folder: str, n: int):
    """The n pages' layout entries for the running-head decision, or None
    without a layout.json. In a book set to print no running heads the
    page_header boxes (its page numbers) are left out, so only the OCR's
    own HEADER says a page has a head."""
    lay = load(folder)
    if not lay:
        return None
    pages = [lay["pages"].get(str(i), {"boxes": []}) for i in range(n)]
    if no_running_heads(folder):
        pages = [dict(p, boxes=[b for b in p["boxes"] if b[0] != "page_header"]) for p in pages]
    return pages


# ---- decisions -------------------------------------------------------------

def _yc(b): return (b[2][1] + b[2][3]) / 2
def _hgt(b): return b[2][3] - b[2][1]
def _wid(b): return b[2][2] - b[2][0]
def _horiz(b): return _wid(b) > 2 * _hgt(b) and _yc(b) < 0.25


def header_band(pages: list):
    """Where this book prints its running head: (y_lo, y_hi, max_height) as
    page fractions, from the confident page_header boxes; None if too few."""
    heads = [b for p in pages for b in p["boxes"] if b[0] == "page_header" and b[1] >= 0.7]
    if len(heads) < 10:
        return None
    ys = [_yc(b) for b in heads]
    m = statistics.median(ys)
    mad = statistics.median([abs(y - m) for y in ys])
    w = max(0.012, 4 * mad)
    return (m - w, m + w, statistics.median([_hgt(b) for b in heads]) * 2.0)


def no_head_pages(folder: str, own_headers: list) -> set:
    """Page indices the detector is sure print NO running head, given the
    OCR model's own HEADER per page (before any promotion). The page parse
    must not add a running head to these -- by lifting a body line or by
    carrying one over from neighbouring pages (stage 2; all 32 such
    disagreements on Gomard / Kirchhoffer / Prévost were the pipeline's own
    additions: chapter titles, plates, title pages -- scan-checked). Empty
    when the book has no layout.json."""
    pages = head_pages(folder, len(own_headers))
    if pages is None:
        return set()
    band = header_band(pages)
    out = set()
    for i, own in enumerate(own_headers):
        own = (own or "").strip()
        # a plate's own label read into HEADER ("Pl. 2.", "Planche V.") is
        # not a running head (La Boëssière's plates)
        if re.match(r"(?i)(?:pl\.?|planche|fig\.?)\s*[\divxlc]", own):
            own = ""
        printed, unsure, _ = running_head(pages[i], band, bool(own) and own.lower() != "none")
        if not printed and not unsure:
            out.add(i)
    return out


def disputed_heads(folder: str, own_headers: list) -> set:
    """Page indices where the OCR reported a running head but the detector
    sees none on the page (its "unsure" case: only the OCR's word says
    printed). See settle_disputed_heads() in 1_ocr_and_extract_vlm.py."""
    pages = head_pages(folder, len(own_headers))
    if pages is None:
        return set()
    band = header_band(pages)
    out = set()
    for i, own in enumerate(own_headers):
        own = (own or "").strip()
        if not own or own.lower() in ("none", "[none]"):
            continue
        printed, unsure, _ = running_head(pages[i], band, True)
        if not printed:
            out.add(i)
    return out


def running_head(page: dict, band, vlm_header: bool) -> tuple:
    """Does this page print a running head? -> (decision, unsure, reason).

    - a page_header box (>= 0.5) that is wide, not tall, in the top quarter
    - or a heading-class box (>= 0.4) sitting in the book's own running-head band
    - or: the first text box starts in that band AND olmOCR reported a
      header -- the detector merged the running head into the paragraph box
    unsure = the detector and olmOCR disagree and geometry can't settle it."""
    bx = page["boxes"]
    strong = [b for b in bx if b[0] == "page_header" and b[1] >= 0.5 and _horiz(b)]
    banded = [b for b in bx if band and b[0] in HEADISH and b[1] >= 0.4 and _horiz(b)
              and band[0] <= _yc(b) <= band[1] and _hgt(b) <= band[2]]
    if strong:
        return True, False, "detector"
    if banded:
        return True, not vlm_header, "book's running-head position"
    first = min((b for b in bx if b[0] in BODY and b[1] >= 0.5), key=lambda b: b[2][1], default=None)
    merged = bool(band and first and band[0] - 0.01 <= first[2][1] <= band[1])
    if vlm_header and merged:
        return True, False, "text starts at the running-head position"
    return False, vlm_header, "no running head detected"


# ---- where the book prints its page number and running head ---------------

def _slot(b, page=None):
    """'L' / 'C' / 'R': where a box sits across the page. (Not measured
    against the text block: the detector's text boxes often miss part of
    its width -- Masiello's centred heads came out "R".)"""
    c = (b[2][0] + b[2][2]) / 2
    return "L" if c < 0.38 else "R" if c > 0.62 else "C"


def _page_numbers_setting(folder: str):
    import _overrides
    try:
        ov = _overrides.load(os.path.basename(folder.rstrip("/")).replace("_translated", ""))
    except _overrides.OverridesError:
        return None
    return ov["book"].get("page_numbers")


def folio_plan(folder: str, folios: list):
    """Where the book prints its page number and running head, learned from
    the scans (reviewer, 2026-10-18: the English mirrors the book's places).
    `folios` = each page's PAGE_NUMBER. Returns {"odd": side, "even": side},
    side = {"folio": ("top"|"bottom", "L"|"C"|"R"), "head": "L"|"C"|"R"},
    from the detector's boxes on pages with a read folio -- a narrow box in
    the top or bottom margin is the number, the widest other one at the top
    the head.
    The side is learned per odd / even folio, not assumed: Kirchhoffer has
    its odd numbers on the left. None when the scans don't say clearly
    (no layout.json, too few pages, or under 80 % agreement): then the PDFs
    keep their old fixed layout."""
    import collections
    set_by_hand = _page_numbers_setting(folder)
    if set_by_hand:
        where, _, kind = set_by_hand.rpartition("-")
        where = where or "top"
        odd, even = {"outer": ("R", "L"), "inner": ("L", "R"), "centre": ("C", "C")}[kind]
        return {"odd": {"folio": (where, odd), "head": "C"},
                "even": {"folio": (where, even), "head": "C"}}
    lay = load(folder)
    if not lay:
        return None
    # the raw boxes: in a book set to print no running heads, head_pages()
    # drops the top boxes -- which are exactly its page numbers
    pages = [lay["pages"].get(str(i), {"boxes": []}) for i in range(len(folios))]
    lone = 0.1 if no_running_heads(folder) else 0.05
    seen = {p: collections.Counter() for p in ("odd", "even")}
    heads = {p: collections.Counter() for p in ("odd", "even")}
    for page, num in zip(pages, folios):
        if not re.fullmatch(r"\d+", (num or "").strip()):
            continue
        par = "odd" if int(num) % 2 else "even"
        bx = [b for b in page["boxes"] if b[1] >= 0.5]
        # the number is the narrowest box in the margin -- a one-word head
        # ("ÉPÉE", Kirchhoffer) is nearly as narrow, so never just the first
        tops = sorted((b for b in bx if b[0] == "page_header" and _yc(b) < 0.15), key=_wid)
        bot = [b for b in bx if b[0] == "page_footer" and _yc(b) > 0.85 and _wid(b) < 0.1]
        top = tops[0] if tops and _wid(tops[0]) < 0.1 and (len(tops) > 1 or _wid(tops[0]) < lone) else None
        if top and not bot:
            seen[par][("top", _slot(top, page))] += 1
        elif bot and not top:
            seen[par][("bottom", _slot(min(bot, key=_wid), page))] += 1
        rest = [b for b in tops if b is not top]
        if rest:
            heads[par][_slot(max(rest, key=_wid), page)] += 1
    plan = {}
    for par in ("odd", "even"):
        total = sum(seen[par].values())
        if total < 10:
            return None
        where, n = seen[par].most_common(1)[0]
        if n < 0.8 * total:
            return None
        # the head: where most pages print it (a stretch of shifted scans
        # mustn't move it -- Masiello 1887 has 63 of 270 off to one side);
        # never on the number's side of a shared top line
        head = heads[par].most_common(1)[0][0] if heads[par] else "C"
        if where[0] == "top" and head == where[1]:
            head = "C"
        plan[par] = {"folio": where, "head": head}
    return plan


def page_side(plan, folios: list, i: int):
    """The plan's side for page i: by its own folio, else by the nearest
    page with a read one (pages alternate), else None."""
    if not plan:
        return None
    for d in range(0, 12):
        for k in ((i - d, i + d) if d else (i,)):
            if 0 <= k < len(folios) and re.fullmatch(r"\d+", (folios[k] or "").strip()):
                odd = (int(folios[k]) + (i - k)) % 2
                return plan["odd" if odd else "even"]
    return None


def two_part_heads(folder: str, headers: list) -> bool:
    """Does the book really print two-part running heads (a topic left, a
    subtopic right: Kirchhoffer's plates, "ÉPÉE ... LA GARDE")? Step 1 also
    splits a head the OCR read on two lines or with a wide gap -- one
    printed line in every other book checked (Jacob-André "CONSEILS AUX
    TÉMOINS.", Prévost-Jollivet, Gomard, Masiello; 2026-10-18). Yes only when
    the scans show two separate head boxes on 3+ such pages (Kirchhoffer 13,
    every other book 0); else the PDFs join the halves into one line."""
    lay = load(folder)
    if not lay:
        return False
    two = 0
    for k, h in enumerate(headers):
        if "\t" not in (h or ""):
            continue
        bx = sorted((b for b in lay["pages"].get(str(k), {"boxes": []})["boxes"]
                     if b[0] == "page_header" and b[1] >= 0.5 and _yc(b) < 0.15 and _wid(b) >= 0.06),
                    key=lambda b: b[2][0])
        apart = []
        for b in bx:
            if not apart or b[2][0] > apart[-1][2][2]:
                apart.append(b)
        two += len(apart) >= 2 and apart[1][2][0] - apart[0][2][2] > 0.1
    return two >= 3


def block_places(folder: str, i: int, n_paras: int):
    """For a page of a few short paragraphs -- picture captions, a title
    page, a dedication: where each text block sits on the scan, top to
    bottom, as [(how far down 0-1, centred?)]: the boxes the detector reads
    as text, caption or heading, outside the head and foot margins, merged
    where they touch. Only when there are exactly `n_paras` of them, so each
    English paragraph can be drawn level with its own and centred like it
    (Kirchhoffer pdf 66's captions, its title pages; 2026-10-18); else None."""
    lay = load(folder)
    if not lay:
        return None
    page = lay["pages"].get(str(i))
    if not page:
        return None
    bx = sorted((b for b in page["boxes"] if b[0] in ("caption", "text", "title", "section_header",
                                                      "list_item", "footnote") and b[1] >= 0.5
                 and 0.1 < _yc(b) < 0.92), key=lambda b: b[2][1])
    groups = []
    for b in bx:
        if groups and b[2][1] <= groups[-1][1] + 0.02:
            g = groups[-1]
            g[1], g[2], g[3] = max(g[1], b[2][3]), min(g[2], b[2][0]), max(g[3], b[2][2])
        else:
            groups.append([b[2][1], b[2][3], b[2][0], b[2][2]])
    if len(groups) != n_paras or not n_paras:
        return None
    body = [b for b in page["boxes"] if b[0] in BODY + ("caption", "picture") and b[1] >= 0.5]
    x0 = min((b[2][0] for b in body), default=0.1)
    x1 = max((b[2][2] for b in body), default=0.9)
    return [(g[0], abs((g[2] - x0) - (x1 - g[3])) < 0.06 * (x1 - x0) and (g[3] - g[2]) < 0.85 * (x1 - x0))
            for g in groups]


def caption_places(folder: str, i: int, n: int):
    """How far down the scan (0-1) the page's `n` picture captions sit --
    the detector's caption boxes, top to bottom, when there are exactly n
    (Kirchhoffer pdf 92: a plate page with only its caption, 78 % down);
    else None."""
    lay = load(folder)
    page = lay and lay["pages"].get(str(i))
    if not page:
        return None
    caps = sorted((b for b in page["boxes"] if b[0] == "caption" and b[1] >= 0.5), key=lambda b: b[2][1])
    merged = []
    for b in caps:
        if merged and b[2][1] <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], b[2][3])
        else:
            merged.append([b[2][1], b[2][3]])
    return [m[0] for m in merged] if len(merged) == n and n else None
