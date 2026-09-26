"""Margin (shoulder) notes: find them from the page layout, read them, and
anchor each one in the text flow next to the paragraph it annotates.

Why: olmOCR-2 transcribes the main text block and mostly drops printed
margin notes (Gomard: ~25 of 384 kept, often pasted into the prose), and
every text-shape heuristic that tried to recognise them afterwards either
misfiled headings / signatures as notes or deleted real text. The layout
detector (_layout.py) sees them directly: narrow text boxes outside the
page's own main column.

Flow:
  1. detect_notes(page): margin boxes from intermediate/layout.json, using
     each page's own column edges (books alternate left/right pages).
  2. read_book(): crop every note from the scan and have the OCR model read
     just that crop -> intermediate/margins.json (resumable; GPU, a few s
     per note). The scan's text layer is too noisy to use for note text.
  3. anchor(): which paragraph of the OCR'd page body each note belongs to:
     the main-column line printed at the note's height (scan text layer,
     matched by word overlap), else the detector's paragraph boxes in order.
  4. insert_markers(): at reprocess time, put each note into the page body as
     its own line, ``⟦MARGIN: note⟧``, right before its paragraph, and drop
     any copy the OCR model pasted into the prose. The translator keeps the
     marker (prompt rule), so the English note lands in the same place; the
     PDF renders it as a distinct line. A note that can't be anchored goes
     at the top of the page, and the review report says so.
"""
import base64
import io
import json
import os
import re

MARK_RE = re.compile(r"⟦MARGIN:\s*(.*?)\s*⟧", re.S)
READ_PROMPT = ("This image is one printed margin note from a page of a 19th-century "
               "French fencing treatise. Transcribe its printed text exactly, joining "
               "its lines into one line (rejoin words hyphenated across lines). Output "
               "only the text -- no labels, no commentary.")


def detect_notes(page: dict) -> list:
    """Margin boxes on one page: narrow text boxes outside the page's own main
    column (its wide text boxes), below the running head, above the footer."""
    bx = page.get("boxes", [])
    wide = [b for b in bx if b[0] in ("text", "list_item", "footnote")
            and b[1] >= 0.5 and b[2][2] - b[2][0] >= 0.35]
    if not wide:
        return []
    c0 = min(b[2][0] for b in wide)
    c1 = max(b[2][2] for b in wide)
    out = []
    for b in sorted(bx, key=lambda b: b[2][1]):
        x0, y0, x1, y1 = b[2]
        if (b[0] in ("text", "caption", "footnote", "list_item") and b[1] >= 0.5
                and x1 - x0 < 0.25 and (x0 >= c1 - 0.015 or x1 <= c0 + 0.015)
                and y0 > 0.11 and y1 < 0.93
                and not any(abs(o[0] - x0) < 0.02 and abs(o[1] - y0) < 0.02 for o in out)):
            out.append([x0, y0, x1, y1])
    return out


def _read_crop(page, box, vlm_url, headers, model):
    import fitz
    import requests
    from PIL import Image, ImageOps
    r = page.rect
    pad = 0.006
    clip = fitz.Rect(max(0, box[0] - pad) * r.width, max(0, box[1] - pad) * r.height,
                     min(1, box[2] + pad) * r.width, min(1, box[3] + pad) * r.height)
    pix = page.get_pixmap(dpi=300, clip=clip, colorspace=fitz.csRGB)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    img = ImageOps.autocontrast(img.convert("L"), cutoff=1).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    body = {"model": model, "temperature": 0.1, "max_tokens": 300,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": READ_PROMPT},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}}]}]}
    resp = requests.post(vlm_url, json=body, headers=headers, timeout=300)
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"].get("content") or ""
    # olmOCR sometimes still answers in its page scaffold
    text = re.sub(r"(?im)^\s*(PAGE_NUMBER|HEADER|FIGURES)\s*:.*$", "", text)
    text = re.sub(r"(?im)^\s*BODY_TEXT\s*:\s*", "", text)
    return re.sub(r"\s+", " ", text.replace("-\n", "")).strip()


def read_book(folder: str, pdf_path: str, progress=print) -> dict:
    """Detect and read every margin note -> intermediate/margins.json
    {page_idx: [{"box": [...], "text": "..."}]}. Resumes: a note already read
    (same box) is not read again."""
    import fitz
    import backend_config as bc
    import _layout
    lay = _layout.load(folder)
    if not lay:
        raise RuntimeError("no intermediate/layout.json -- run layout_scan.py first")
    path = f"{folder}/intermediate/margins.json"
    data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    doc = fitz.open(pdf_path)
    url = f"{bc.ocr_base_url()}/v1/chat/completions"
    headers = {"Content-Type": "application/json", **bc.auth_headers()}
    model = os.environ.get("VLM_MODEL_NAME", "")
    todo = []
    for k, page in lay["pages"].items():
        for box in detect_notes(page):
            have = data.get(k, [])
            if not any(n["box"] == box and n.get("text") for n in have):
                todo.append((k, box))
    for n, (k, box) in enumerate(todo, 1):
        text = _read_crop(doc[int(k)], box, url, headers, model)
        data.setdefault(k, [])
        data[k] = [x for x in data[k] if x["box"] != box] + [{"box": box, "text": text}]
        data[k].sort(key=lambda x: x["box"][1])
        if n % 10 == 0 or n == len(todo):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            if progress:
                progress(f"  margin notes: {n}/{len(todo)}")
    if not todo and not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return data


# ---- anchoring ----------------------------------------------------------------

_LIG = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ſ": "s"})


def _words(t: str) -> list:
    return [w for w in re.findall(r"[^\W\d_]{3,}", (t or "").translate(_LIG).lower())]


def anchor(page_pdf, page_layout: dict, box: list, paras: list):
    """(paragraph index, char position in it or None) for this note, or None.

    1. The main-column words printed at the note's top line (scan text
       layer): the paragraph sharing most of them.
    2. Fallback: the detector's main-column text boxes, top to bottom; the
       box level with the note maps proportionally onto the paragraphs."""
    if not paras:
        return None
    r = page_pdf.rect
    ytop = box[1] * r.height
    words = page_pdf.get_text("words")
    main = [w for w in words if abs(w[1] - ytop) < 0.012 * r.height
            and not (box[0] * r.width - 2 <= w[0] <= box[2] * r.width + 2)]
    line_words = _words(" ".join(w[4] for w in sorted(main, key=lambda w: w[0])))
    probe = set(line_words)
    if len(probe) >= 2:
        scores = [len(probe & set(_words(p))) for p in paras]
        best = max(range(len(paras)), key=lambda i: scores[i])
        if scores[best] >= max(2, len(probe) // 2):
            return best, _position(paras[best], line_words)
    col = [b[2] for b in page_layout.get("boxes", [])
           if b[0] in ("text", "list_item") and b[1] >= 0.5 and b[2][2] - b[2][0] >= 0.35]
    col = sorted({(round(c[1], 2), round(c[3], 2)) for c in col})
    if not col:
        return None
    k = next((i for i, (y0, y1) in enumerate(col) if y0 - 0.005 <= box[1] <= y1 + 0.005), None)
    if k is None:
        k = max((i for i, (y0, _) in enumerate(col) if y0 <= box[1]), default=0)
    return min(len(paras) - 1, round(k * len(paras) / len(col))), None


def _position(para: str, line_words: list):
    """Where in the paragraph the note's line is printed: the start of the
    sentence containing that line (a word of it that occurs once in the
    paragraph), or None if it's the paragraph's first sentence anyway."""
    low = para.translate(_LIG).lower()
    for w in sorted(set(line_words), key=len, reverse=True):
        hits = [m.start() for m in re.finditer(r"\b" + re.escape(w) + r"\b", low)]
        if len(hits) == 1:
            cut = max((m.end() for m in re.finditer(r"[.!?;:](?:[»”)\]]*)\s+", para[:hits[0]])),
                      default=0)
            return cut if cut > 0.15 * len(para) else None
    return None


def _is_copy(para: str, note: str) -> bool:
    """A body paragraph that is just the OCR model's pasted copy of a note."""
    p, n = set(_words(para)), set(_words(note))
    if not p or not n or len(para.split()) > max(25, 2 * len(note.split())):
        return False
    return len(p & n) >= 0.7 * len(p)


def insert_markers(body: str, notes: list, page_pdf, page_layout: dict) -> tuple:
    """Body text with ⟦MARGIN: …⟧ lines inserted before each note's paragraph
    (and pasted copies removed). Returns (body, unanchored_count)."""
    # a bare short number is a stray mark (Jacob scan 322: "68"), not a note;
    # a year label ("1818.", Gomard's bibliography) is kept
    notes = [n for n in notes if (n.get("text") or "").strip()
             and not re.fullmatch(r"\W*\d{1,3}\W*", n["text"])]
    if not notes:
        return body, 0
    paras = [p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    paras = [p for p in paras if not any(_is_copy(p, n["text"]) for n in notes)]
    at = {}                                   # (para, char pos) -> [markers]
    lost = 0
    for n in notes:
        hit = anchor(page_pdf, page_layout, n["box"], paras) if paras else None
        if hit is None:
            hit, lost = (0, None), lost + 1
        at.setdefault(hit, []).append(f"⟦MARGIN: {n['text']}⟧")
    out = []
    for i, p in enumerate(paras or [""]):
        cuts = sorted({pos for (k, pos) in at if k == i and pos})
        out += at.get((i, None), [])
        start = 0
        for c in cuts:
            out.append(p[start:c].strip())
            out += at[(i, c)]
            start = c
        if p[start:].strip():
            out.append(p[start:].strip())
    return "\n\n".join(out), lost


def apply_to_book(folder: str, parsed: list) -> tuple:
    """Insert margin-note markers into every parsed page's body (in place)
    from intermediate/margins.json. Returns (notes placed, unanchored).
    No margins.json -> nothing to do (books without margin notes, or not
    read yet)."""
    import glob
    import fitz
    import _layout
    path = f"{folder}/intermediate/margins.json"
    lay = _layout.load(folder)
    pdfs = glob.glob(f"{folder}/intermediate/*_OCR.pdf")
    if not (os.path.exists(path) and lay and pdfs):
        return 0, 0
    data = json.load(open(path, encoding="utf-8"))
    doc = fitz.open(pdfs[0])
    placed = lost = 0
    for k, notes in data.items():
        i = int(k)
        if i >= len(parsed) or not notes:
            continue
        body, l = insert_markers(parsed[i].get("body") or "", notes, doc[i],
                                 lay["pages"].get(k, {"boxes": []}))
        parsed[i]["body"] = body
        placed += len(MARK_RE.findall(body))
        lost += l
    return placed, lost
