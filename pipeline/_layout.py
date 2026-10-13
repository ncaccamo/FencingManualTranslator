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
