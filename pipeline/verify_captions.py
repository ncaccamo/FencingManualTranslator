#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""A second reading of figure captions, for the review report: what caption
is actually printed with each picture?

    python3 /app/verify_captions.py <stem> [<stem> ...]       (or --all)
    python3 /app/verify_captions.py <stem> --pages 32,75
    python3 /app/verify_captions.py <stem> [...] --summary    what was found

Step 1 records a caption per picture, but sometimes misses a printed one
(read as running text, then lost -- Kirchhoffer scan 75), garbles it, or
writes a description of the picture instead. This reads only the small
areas where a caption can be -- each caption box the layout detector
found next to a picture, and the space around a recorded picture with no
caption box -- turned upright when the box is tall and narrow (a sideways
caption on a sideways plate, Masiello 1887 scan 487). Reading the whole
page let the model drift into the running text (first version,
2026-10-07: right in 3 of 12 sample cases).

Answers go to intermediate/caption_read.json:
    {scan: {"crops": [{"kind": "caption" | "around picture", "read": text}],
            "recorded": [...], "verdicts": ["printed" | "unconfirmed", ...],
            "found": [printed caption text no recorded caption matches]}}
Pages already read are skipped. It only records; the review report lists
what to fix by hand ("caption printed here but not recorded", "recorded
caption differs from the printed one"). Captions set in the overrides file
are not read -- a person set them.
"""
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _overrides  # noqa: E402

PROMPT = ("This image is a small part of a printed book page. If it contains printed caption "
          "text -- the line or lines that name or explain a picture, for example \"Fig. 3. — "
          "...\", \"Pl. 2\", \"Le gant.\" -- transcribe that text exactly as printed, in the "
          "book's own language, one line per caption. Do not describe any picture. If there is "
          "no printed text, answer exactly: NONE")
PIC_MIN, CAP_MIN = 0.6, 0.5          # layout detector scores
AROUND_ABOVE, AROUND_BELOW = 0.06, 0.10   # page-height share read around a picture


def _norm(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "").lower()
    return re.sub(r"[\W_]+", " ", s).strip()


def same_caption(recorded: str, read: str) -> bool:
    """The two readings name the same printed caption: close as strings,
    or one contains the other's first words (a caption read in part)."""
    a, b = _norm(recorded), _norm(read)
    if not a or not b:
        return False
    if difflib.SequenceMatcher(None, a, b).ratio() >= 0.6:
        return True
    head = lambda t: " ".join(t.split()[:4])
    return len(a.split()) >= 2 and len(b.split()) >= 2 and (head(a) in b or head(b) in a)


def verdicts(recorded: list, read: list) -> list:
    """Per recorded caption: "printed" when a reading of the page matches it
    (a whole reading, or the lines of one joined -- Edom 39's labels),
    else "unconfirmed"."""
    pool = list(read) + [" ".join(read)] if read else []
    return ["printed" if any(same_caption(r, x) for x in pool) else "unconfirmed" for r in recorded]


def _lines(answer: str) -> list:
    if re.fullmatch(r"\W*none\W*", answer or "", re.I):
        return []
    return [l.strip(" -*•") for l in answer.split("\n")
            if l.strip(" -*•") and not re.fullmatch(r"none\.?", l.strip(), re.I)]


# text a caption area can hold that is not a caption: the scanner's
# watermark, library stamps and bookplates, a bare number (2026-10-08 run:
# most of Edom's / Prévost's / Gomard's "found" were these)
_NOISE = re.compile(r"(?i)digiti[sz]ed by|google|biblioth[eèé]que nationale|\bbnf\b|library|"
                    r"universit[yàé]|ex[ -]?libris|librairie|\bstamp\b")


def is_noise(text: str, header: str = "") -> bool:
    t = " ".join((text or "").split())
    letters, digits = len(re.findall(r"[^\W\d_]", t)), len(re.findall(r"\d", t))
    if letters < 4 or _NOISE.search(t) or digits > letters:   # "V. 8 39004 FOEG": a shelfmark
        return True
    h = _norm(header)
    return bool(h) and h != "none" and (_norm(t) in h or h in _norm(t))


def _dedupe(boxes: list) -> list:
    out = []
    for b in boxes:
        x0, y0, x1, y1 = b[2]
        if not any(abs(x0 - o[2][0]) < .02 and abs(y0 - o[2][1]) < .02 and
                   abs(x1 - o[2][2]) < .02 and abs(y1 - o[2][3]) < .02 for o in out):
            out.append(b)
    return out


def regions(page_layout: dict, has_record: bool) -> list:
    """[(kind, (x0, y0, x1, y1) fractions)] to read on one page."""
    bx = page_layout.get("boxes", [])
    pics = _dedupe([b for b in bx if b[0] == "picture" and b[1] >= PIC_MIN])
    caps = _dedupe([b for b in bx if b[0] == "caption" and b[1] >= CAP_MIN])
    if not pics and not has_record:
        return []
    out = [("caption", tuple(b[2])) for b in caps]
    if not caps and has_record:
        for b in pics:
            x0, y0, x1, y1 = b[2]
            out.append(("around picture", (max(0, x0 - .05), max(0, y0 - AROUND_ABOVE),
                                           min(1, x1 + .05), min(1, y1 + AROUND_BELOW))))
    return out


def _ask(ns, img):
    b64, _ = ns["prepare_image_for_vlm"](img)
    bc, requests = ns["bc"], ns["requests"]
    try:
        r = requests.post(f"{bc.ocr_base_url()}/v1/chat/completions",
                          headers={"Content-Type": "application/json", **bc.auth_headers()},
                          json={"model": ns["VLM_MODEL_NAME"], "temperature": 0, "max_tokens": 200,
                                "messages": [{"role": "user", "content": [
                                    {"type": "text", "text": PROMPT},
                                    {"type": "image_url",
                                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]},
                          timeout=180)
        return r.json()["choices"][0]["message"]["content"].strip() if r.status_code == 200 else None
    except Exception:
        return None


def read_region(ns, page_img, box):
    """Read one area; a tall, narrow one is read turned both ways and the
    answer with more letters kept (sideways caption)."""
    W, H = page_img.size
    x0, y0, x1, y1 = box
    pad = 0.012
    crop = page_img.crop((int(max(0, x0 - pad) * W), int(max(0, y0 - pad) * H),
                          int(min(1, x1 + pad) * W), int(min(1, y1 + pad) * H)))
    if crop.height > 1.5 * crop.width:
        answers = [_ask(ns, crop.rotate(a, expand=True)) for a in (90, 270)]
        answers = [a for a in answers if a is not None]
        if not answers:
            return None
        return max(answers, key=lambda a: len(re.findall(r"[^\W\d_]", "\n".join(_lines(a)))))
    return _ask(ns, crop)


def summary(stem: str) -> None:
    p = f"/data/processed/{stem}_translated/intermediate/caption_read.json"
    if not os.path.exists(p):
        print(f"{stem}: not read yet")
        return
    from collections import Counter
    data = json.load(open(p, encoding="utf-8"))
    c = Counter(v for d in data.values() for v in d["verdicts"])
    found = sum(1 for d in data.values() if d["found"])
    print(f"{stem}: {len(data)} page(s) read; recorded captions: " +
          (", ".join(f"{n} {k}" for k, n in c.most_common()) or "none") +
          f"; pages with a printed caption not recorded: {found}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    spec = sys.argv[sys.argv.index("--pages") + 1] if "--pages" in sys.argv else None
    if spec:
        args.remove(spec)
    import _books
    stems = [_books.stem_of(n) for n in _books.select(
        args + [a for a in sys.argv[1:] if a.startswith("--")], __doc__, writes=False)]
    if "--summary" in sys.argv:
        for stem in stems:
            summary(stem)
        return
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "1_ocr_and_extract_vlm.py"),
               encoding="utf-8").read()
    ns = {"__name__": "_captions"}
    exec(compile(src.split('\nprint("\\n" + "="*60)\nprint("STEP 1')[0], "step1", "exec"), ns)
    fitz, Image = ns["fitz"], ns["Image"]
    if ns["start_vlm_server"]() is None:
        sys.exit("✗ cannot reach / start the OCR server")
    import _progress
    import _pageio
    for stem in stems:
        I = f"/data/processed/{stem}_translated/intermediate"
        if not os.path.exists(f"{I}/layout.json") or not os.path.exists(f"{I}/extracted.txt"):
            print(f"{stem}: no layout.json / extracted.txt -- run step 1 first")
            continue
        lay = json.load(open(f"{I}/layout.json", encoding="utf-8"))["pages"]
        figs = json.load(open(f"{I}/figures.json", encoding="utf-8")) if os.path.exists(f"{I}/figures.json") else {}
        ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
        decided = {s for s, v in _overrides.load(stem)["pages"].items() if "caption" in v}
        todo_all = {}
        for k, pl in lay.items():
            s = int(k) + 1
            if s in decided:
                continue
            reg = regions(pl, bool(figs.get(k)))
            if reg:
                todo_all[s] = reg
        if spec:
            want = set(_overrides.parse_page_spec(spec))
            todo_all = {s: r for s, r in todo_all.items() if s in want}
        out_p = f"{I}/caption_read.json"
        out = json.load(open(out_p, encoding="utf-8")) if os.path.exists(out_p) else {}
        todo = [s for s in sorted(todo_all) if str(s) not in out]
        print(f"{stem}: {len(todo)} page(s) to read ({len(todo_all) - len(todo)} already)")
        if not todo:
            continue
        doc = fitz.open(f"{I}/{stem}_OCR.pdf")
        _progress.start(f"{stem} (caption reading)", "OCR", len(todo))
        for s in todo:
            pix = doc[s - 1].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
            img = ns["preprocess_for_ocr"](Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
            crops, failed = [], False
            for kind, box in todo_all[s]:
                ans = read_region(ns, img, box)
                if ans is None:
                    failed = True
                    break
                crops.append({"kind": kind, "box": [round(x, 4) for x in box], "read": "\n".join(_lines(ans))})
            if failed:
                print(f"  scan {s}: no answer (kept unread)")
                continue
            read = [c["read"] for c in crops if c["read"]]
            recorded = [f.get("caption") for f in figs.get(str(s - 1), []) if f.get("caption")]
            body = _norm(ex[s - 1].split("BODY_TEXT:", 1)[-1]) if s <= len(ex) else ""
            # printed text no recorded caption matches -- and not already in the
            # page's own text (a caption box can also be running text)
            head = (re.search(r"(?m)^HEADER: (.*)$", ex[s - 1]) or [None, ""])[1] if s <= len(ex) else ""
            found = [r for r in read if not any(same_caption(c, r) for c in recorded)
                     and " ".join(_norm(r).split()[:5]) not in body and not is_noise(r, head)]
            out[str(s)] = {"crops": crops, "recorded": recorded,
                           "verdicts": verdicts(recorded, read), "found": found}
            with open(out_p, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=1)
            _progress.page(s)
        _progress.finish()
        summary(stem)


if __name__ == "__main__":
    main()
