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


HEAD_PROMPT = ("This image is the running head printed at the top of one page of an old "
               "fencing treatise (it may include the page number). Transcribe its "
               "printed text exactly, on one line. Output only the text -- no labels, "
               "no commentary.")


def _read_crop(page, box, vlm_url, headers, model, prompt=None):
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
                {"type": "text", "text": prompt or READ_PROMPT},
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


def anchor(page_pdf, page_layout: dict, box: list, paras: list, after: int = 0):
    """(paragraph index, char position in it or None) for this note, or None.

    1. The main-column words printed at the note's top line (scan text
       layer): the paragraph sharing most of them. On a tie, the first one
       at or after paragraph `after` (the previous note's) -- notes and
       paragraphs both run down the page (Gomard scan 37: "maître d'armes.
       Paris" is in two bibliography entries; the note is by the second).
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
        top = max(scores)
        tied = [i for i in range(len(paras)) if scores[i] == top]
        best = next((i for i in tied if i >= after), tied[0])
        if top >= max(2, len(probe) // 2):
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
    # longest first, then alphabetical: a set's order changes from run to
    # run, so ties must not depend on it
    for w in sorted(set(line_words), key=lambda w: (-len(w), w)):
        hits = [m.start() for m in re.finditer(r"\b" + re.escape(w) + r"\b", low)]
        if len(hits) == 1:
            cut = max((m.end() for m in re.finditer(r"[.!?;:](?:[»”)\]]*)\s+", para[:hits[0]])),
                      default=0)
            return cut if cut > 0.15 * len(para) else None
    return None


def _is_copy(para: str, note: str) -> bool:
    """A body paragraph that is just the OCR model's pasted copy of a note.
    The overlap must cover most of the words on BOTH sides: a short
    chapter title shares all its few words with a longer note ("DE LA
    RIPOSTE." / "Valeur de la riposte.", Gomard scan 236) without being a
    copy of it -- one-sided overlap deleted ~20 of Gomard's chapter titles."""
    p, n = set(_words(para)), set(_words(note))
    if not p or not n or len(para.split()) > max(25, 2 * len(note.split())):
        return False
    return len(p & n) >= 0.7 * max(len(p), len(n))


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _dedupe(notes: list) -> list:
    """The detector sometimes also boxes a note's first line on its own
    (Gomard's bibliography: "1778." as well as "1778. Nicolas Demeuse.").
    Drop a note that lies mostly inside a bigger note's box, or whose whole
    text starts a longer note printed at about the same height."""
    def covered(a, b):
        ix = min(a[2], b[2]) - max(a[0], b[0])
        iy = min(a[3], b[3]) - max(a[1], b[1])
        return ix > 0 and iy > 0 and ix * iy >= 0.6 * _area(a)

    def key(t):
        return re.sub(r"[\W_]+", " ", t).strip().lower()

    out = []
    for a in notes:
        ka = key(a["text"])
        if not any(b is not a and (
                (_area(a["box"]) < _area(b["box"]) and covered(a["box"], b["box"]))
                or (len(ka) < len(key(b["text"])) and key(b["text"]).startswith(ka)
                    and abs(a["box"][1] - b["box"][1]) < 0.02))
                for b in notes):
            out.append(a)
    return out


_REF_ONLY = re.compile(r"\W*(?:[\d.,\s]+|ibid|id|pag|p)\W*", re.I)


def _drop_false_notes(notes: list, body: str) -> list:
    """Things in the margin that are not the author's notes (2026-09-28
    review): a contents page's number column ("309", "ibid.", "Pag." -- La
    Boëssière scan 338), a shelfmark ("36.141 6"), a library stamp or a
    title-page brace read as a word ("L'ÉCONOMIE", "Chez"). Keep a lone year
    ("1843.") -- Gomard's bibliography dates its entries that way."""
    few = len(re.findall(r"[^\W\d_]{2,}", MARK_RE.sub("", body or ""))) < 60
    out = []
    for n in notes:
        t = n["text"].strip()
        if _REF_ONLY.fullmatch(t) and not re.fullmatch(r"\W*1[5-9]\d\d\W*", t):
            continue
        if few and len(re.findall(r"[^\W\d_]{2,}", t)) < 3:
            continue
        out.append(n)
    return out


def _trim_overlapping_year(notes: list) -> list:
    """A note box that runs into the next note picks up its year: Gomard scan
    38 "1828. Encyclopédie moderne 1831" -- the "1831" is the next note's
    "1834." misread. Drop a trailing year when the next note's box overlaps
    this one and starts with a year."""
    out = [dict(n) for n in notes]
    for a, b in zip(out, out[1:]):
        if b["box"][1] < a["box"][3] and re.match(r"\W*1[5-9]\d\d\b", b["text"]):
            a["text"] = re.sub(r"\s+1[5-9]\d\d\.?\s*$", "", a["text"])
    return out


def insert_markers(body: str, notes: list, page_pdf, page_layout: dict) -> tuple:
    """Body text with ⟦MARGIN: …⟧ lines inserted before each note's paragraph
    (and pasted copies removed). Returns (body, unanchored_count)."""
    # a bare short number is a stray mark (Jacob scan 322: "68"), not a note;
    # a year label ("1818.", Gomard's bibliography) is kept
    notes = [n for n in notes if (n.get("text") or "").strip()
             and not re.fullmatch(r"\W*\d{1,3}\W*", n["text"])]
    notes = _dedupe(sorted(notes, key=lambda n: n["box"][1]))
    notes = _drop_false_notes(notes, body)
    notes = _trim_overlapping_year(notes)
    if not notes:
        return body, 0
    paras = [p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    paras = [p for p in paras if not any(_is_copy(p, n["text"]) for n in notes)]
    at = {}                                   # (para, char pos) -> [markers]
    lost = prev = 0
    for n in notes:
        hit = anchor(page_pdf, page_layout, n["box"], paras, prev) if paras else None
        if hit is None:
            hit, lost = (0, None), lost + 1
        prev = hit[0]
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


def head_boxes(page: dict) -> list:
    """A page's confident running-head box: page_header class, >= 0.8, in
    the top quarter, wider than tall. [] if none."""
    return [b[2] for b in page.get("boxes", [])
            if b[0] == "page_header" and b[1] >= 0.8 and b[2][1] < 0.25
            and (b[2][2] - b[2][0]) > (b[2][3] - b[2][1])][:1]


def read_heads(folder: str, pdf_path: str, progress=print) -> dict:
    """Read the running head straight from the detector's box on pages
    whose OCR reply has no HEADER field at all (a malformed reply: 491
    pages across 9 books, 2026-09-29) -> intermediate/heads.json
    {page_idx: {"box": [...], "text": "..."}}. Those pages otherwise get a
    head only by copying a neighbour's, which can be wrong (LaMarche pdf
    318). Measurement first: nothing reads heads.json yet. Resumes."""
    import fitz
    import backend_config as bc
    import _layout
    import _pageio
    lay = _layout.load(folder)
    if not lay:
        raise RuntimeError("no intermediate/layout.json -- run layout_scan.py first")
    raw = _pageio.split_pages(open(f"{folder}/intermediate/raw_ocr.txt", encoding="utf-8").read())
    path = f"{folder}/intermediate/heads.json"
    data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    doc = fitz.open(pdf_path)
    url = f"{bc.ocr_base_url()}/v1/chat/completions"
    headers = {"Content-Type": "application/json", **bc.auth_headers()}
    model = os.environ.get("VLM_MODEL_NAME", "")
    todo = []
    for i, r in enumerate(raw):
        if any(l.startswith("HEADER") for l in r.split("\n")) or str(i) in data:
            continue
        boxes = head_boxes(lay["pages"].get(str(i), {}))
        if boxes:
            todo.append((i, boxes[0]))
    for n, (i, box) in enumerate(todo, 1):
        text = _read_crop(doc[i], box, url, headers, model, prompt=HEAD_PROMPT)
        data[str(i)] = {"box": box, "text": text}
        if n % 20 == 0 or n == len(todo):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            if progress:
                progress(f"  heads: {n}/{len(todo)}")
    return data
