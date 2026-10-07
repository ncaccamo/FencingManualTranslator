#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""OCR a gold book (no usable PDF text layer) with olmOCR-2, plain-text.

Runs INSIDE the container (needs fitz + the VLM server). Reuses step 1's
server lifecycle and per-page call, but with a bare "transcribe verbatim"
prompt instead of the corpus PAGE_NUMBER/HEADER/FIGURES scaffold.

    docker exec fmt-translator \
      python3 /data/evals/ocr_gold.py angelo_1763_ecolearmes            # both sides
    ... angelo_1763_ecolearmes src --force
    ... angelo_1763_ecolearmes ref --pages 8-160

Writes evals/gold/<stem>/<side>.txt as  <!--p N-->\\n<page text>  chunks.
"""
import base64
import io
import re
import sys
import time
from pathlib import Path

import fitz
from PIL import Image

sys.path.insert(0, "/app")   # backend_config, _pageio live here

# --- reuse step 1's helpers without running its corpus loop ------------
_STEP1 = Path("/app/1_ocr_and_extract_vlm.py")
_src = _STEP1.read_text()
_ns = {"__name__": "_ocr_gold"}
exec(compile(_src.split('\nprint("\\n" + "="*60)')[0], str(_STEP1), "exec"), _ns)

start_vlm_server = _ns["start_vlm_server"]
preprocess_for_ocr = _ns["preprocess_for_ocr"]
prepare_image_for_vlm = _ns["prepare_image_for_vlm"]
bc = _ns["bc"]
requests = _ns["requests"]

GOLD = Path("/data/evals/gold")
PROMPT = ("Transcribe every word of printed text on this page, exactly as "
          "printed, in natural reading order. Keep original spelling, accents "
          "and punctuation. Do not translate, summarise, correct, or add "
          "commentary. Do not describe images. Output only the transcription; "
          "if the page has no text, output nothing.")


def _ocr_page(img_b64: str) -> str:
    for attempt in range(3):
        try:
            r = requests.post(
                f"{bc.ocr_base_url()}/v1/chat/completions",
                headers={"Content-Type": "application/json", **bc.auth_headers()},
                # the same model name step 1 sends (VLM_MODEL_NAME, e.g. the
                # llama-swap key "fmt-ocr"); a hard-coded "olmocr2" 404s there
                json={"model": _ns["VLM_MODEL_NAME"], "temperature": 0.1, "max_tokens": 4096,
                      "repeat_penalty": 1.12,
                      "messages": [{"role": "user", "content": [
                          {"type": "text", "text": PROMPT},
                          {"type": "image_url", "image_url": {
                              "url": f"data:image/jpeg;base64,{img_b64}"}}]}]},
                timeout=300)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            print(f"    HTTP {r.status_code}, retry"); time.sleep(4)
        except Exception as e:  # noqa: BLE001
            print(f"    {type(e).__name__}, retry"); time.sleep(4)
    return "[OCR FAILED]"


def ocr_side(stem: str, side: str, rng, force: bool) -> None:
    d = GOLD / stem
    pdf, out = d / f"{side}.pdf", d / f"{side}.txt"
    if not pdf.exists():
        return
    if out.exists() and not force:
        print(f"  {stem}/{side}: exists, skip (--force to redo)")
        return
    doc = fitz.open(pdf)
    lo, hi = rng or (0, len(doc))
    hi = min(hi, len(doc))
    chunks, t0 = [], time.time()
    import _progress                     # the live view / Homepage tile (pipeline/_progress.py)
    _progress.start(f"{stem} ({side}, gold)", "OCR", hi - lo)
    for i in range(lo, hi):
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img = preprocess_for_ocr(img)
        b64, _kb = prepare_image_for_vlm(img)
        txt = _ocr_page(b64)
        if txt:
            chunks.append(f"<!--p {i}-->\n{txt}")
        done = i - lo + 1
        if done % 10 == 0 or i == hi - 1:
            rate = (time.time() - t0) / done
            print(f"  {stem}/{side}: {done}/{hi - lo}  ({rate:.1f}s/pg, "
                  f"~{rate * (hi - i - 1) / 60:.0f} min left)")
        out.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")  # incremental
        _progress.page(i + 1)
    _progress.finish()
    words = sum(len(c.split()) for c in chunks)
    print(f"  {stem}/{side}: DONE {len(chunks)} pages, {words} words -> {out}")


def main() -> None:
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not pos:
        sys.exit(__doc__)
    stem, sides = pos[0], (pos[1:] or ["src", "ref"])
    force = "--force" in sys.argv
    rng = None
    if "--pages" in sys.argv:
        a, b = sys.argv[sys.argv.index("--pages") + 1].split("-")
        rng = (int(a), int(b))

    if start_vlm_server() is None:
        sys.exit("could not start VLM server")
    try:
        for s in sides:
            ocr_side(stem, s, rng, force)
    finally:
        _ns["subprocess"].run(["pkill", "-f", "llama-server.*11435"])


if __name__ == "__main__":
    main()
