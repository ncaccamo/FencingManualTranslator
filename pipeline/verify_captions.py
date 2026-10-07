#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""A second look at figure captions: is the caption step 1 recorded for a
picture really printed in the book, or did the OCR model describe the
picture instead? Asks the OCR model, page by page, for the captions printed
with the page's pictures -- and nothing else -- and compares.

    python3 /app/verify_captions.py <stem> [<stem> ...]       (or --all)
    python3 /app/verify_captions.py <stem> --pages 32,75
    python3 /app/verify_captions.py <stem> [...] --summary    what was found

Why: step 1's prompt asks for "that picture's own caption, transcribed
exactly as printed", or "no caption"; sometimes the model writes a
description instead ("Two fencers demonstrating a half-circle movement",
"Immagine di una mano che impugna la spada" -- Barbasetti, Masiello;
2026-10-07). The English-only PDF printed those as captions.

Answers go to intermediate/caption_check.json:
    {scan: {"read": [caption lines as printed] | [] for none,
            "figures": [{"recorded": ..., "verdict": ...}, ...]}}
verdict: "printed"     the second reading agrees with the recorded caption
         "not printed" the second reading found no caption on the page
         "different"   it found a caption, but not the recorded one
Pages already checked are skipped. This only records; nothing in the book
changes. (A second reading is a witness, not proof: read_folios.py's was
right 11 times in 16. Agreement is what makes a caption trustworthy.)
Captions decided in the overrides file are not checked -- a person set them.
"""
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _overrides  # noqa: E402

PROMPT = ("This is one page of an old printed book. It has one or more pictures on it "
          "(engravings, photographs, diagrams). For each picture, transcribe the caption that "
          "is PRINTED with it -- the line or lines directly below or above the picture that "
          "name or explain it, often starting with Fig., Pl., Planche, Schéma, Figura or Tav. "
          "-- exactly as printed, in the book's own language. Write one caption per line, top "
          "to bottom. Do not describe the pictures and do not copy the page's running text. "
          "If no caption is printed with any picture, answer exactly: NONE")


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
    out = []
    for r in recorded:
        if not read:
            out.append("not printed")
        elif any(same_caption(r, x) for x in read):
            out.append("printed")
        else:
            out.append("different")
    return out


def _ask(ns, img):
    b64, _ = ns["prepare_image_for_vlm"](img)
    bc, requests = ns["bc"], ns["requests"]
    try:
        r = requests.post(f"{bc.ocr_base_url()}/v1/chat/completions",
                          headers={"Content-Type": "application/json", **bc.auth_headers()},
                          json={"model": ns["VLM_MODEL_NAME"], "temperature": 0, "max_tokens": 300,
                                "messages": [{"role": "user", "content": [
                                    {"type": "text", "text": PROMPT},
                                    {"type": "image_url",
                                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]},
                          timeout=180)
        return r.json()["choices"][0]["message"]["content"].strip() if r.status_code == 200 else None
    except Exception:
        return None


def _lines(answer: str) -> list:
    if re.fullmatch(r"\W*none\W*", answer, re.I):
        return []
    return [l.strip(" -*•") for l in answer.split("\n") if l.strip(" -*•") and not re.fullmatch(r"none\.?", l.strip(), re.I)]


def summary(stem: str) -> None:
    I = f"/data/processed/{stem}_translated/intermediate"
    p = f"{I}/caption_check.json"
    if not os.path.exists(p):
        print(f"{stem}: not checked yet")
        return
    from collections import Counter
    data = json.load(open(p, encoding="utf-8"))
    c = Counter(f["verdict"] for v in data.values() for f in v["figures"])
    print(f"{stem}: {sum(c.values())} caption(s) on {len(data)} page(s): " +
          ", ".join(f"{n} {k}" for k, n in c.most_common()))
    for s in sorted(data, key=int):
        for f in data[s]["figures"]:
            if f["verdict"] != "printed":
                print(f"   scan {s}: {f['verdict']}: recorded {f['recorded'][:60]!r}"
                      f" | read {' / '.join(data[s]['read'])[:60]!r}")


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
    for stem in stems:
        I = f"/data/processed/{stem}_translated/intermediate"
        fp = f"{I}/figures.json"
        if not os.path.exists(fp):
            print(f"{stem}: no figures.json")
            continue
        figs = json.load(open(fp, encoding="utf-8"))
        decided = {s for s, v in _overrides.load(stem)["pages"].items() if "caption" in v}
        pages = {int(k) + 1: [f.get("caption") for f in v if f.get("caption")]
                 for k, v in figs.items() if any(f.get("caption") for f in v)}
        pages = {s: c for s, c in pages.items() if s not in decided}
        if spec:
            pages = {s: c for s, c in pages.items() if s in set(_overrides.parse_page_spec(spec))}
        out_p = f"{I}/caption_check.json"
        out = json.load(open(out_p, encoding="utf-8")) if os.path.exists(out_p) else {}
        todo = [s for s in sorted(pages) if str(s) not in out]
        print(f"{stem}: {len(todo)} page(s) with captions to check ({len(pages) - len(todo)} already)")
        if not todo:
            continue
        doc = fitz.open(f"{I}/{stem}_OCR.pdf")
        _progress.start(f"{stem} (caption check)", "OCR", len(todo))
        for s in todo:
            pix = doc[s - 1].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            ans = _ask(ns, ns["preprocess_for_ocr"](img))
            if ans is None:
                print(f"  scan {s}: no answer (kept unchecked)")
                continue
            read = _lines(ans)
            v = verdicts(pages[s], read)
            out[str(s)] = {"read": read, "figures": [{"recorded": r, "verdict": x} for r, x in zip(pages[s], v)]}
            print(f"  scan {s}: {', '.join(v)}", flush=True)
            with open(out_p, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=1)
            _progress.page(s)
        _progress.finish()
        summary(stem)


if __name__ == "__main__":
    main()
