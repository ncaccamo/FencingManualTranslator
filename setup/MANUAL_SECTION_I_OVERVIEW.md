# User Manual — Section I: System Overview

## I.1 — What this system does (Function)

BookTranslator translates historical fencing and martial arts manuals
(HEMA texts) from their original language into English, while preserving
the scholarly conventions expected of this kind of translation:

- Technical fencing/martial-arts terms are left untranslated and
  italicized, rather than forced into an English equivalent that loses
  precision (e.g. terms of art that don't map cleanly to modern English).
- Where a passage is genuinely difficult to translate, a footnote
  preserves the original term/phrase so a reader can cross-reference it.
- Original page layout, images, and figures are preserved where the
  output format allows it.
- Output is produced as two separate PDFs: an English-only reading copy,
  and a side-by-side original/translation copy for direct comparison.
- Every output includes a cover page disclosing that the translation is
  AI-generated, the date, and which models performed the work.

Everything runs locally on your own hardware — no cloud API calls, no
per-page or per-book cost, and no source material ever leaves your machine.

## I.2 — Hardware requirements

| Component | Minimum (confirmed working on) |
|---|---|
| GPU | AMD GPU with ROCm or Vulkan support, 16GB+ VRAM recommended (confirmed on AMD RX 9070 XT / RDNA4, gfx1201) |
| CPU | 8 cores / 16 threads recommended (confirmed on AMD Ryzen 7 5700X3D) |
| RAM | 32GB |
| Disk | ~50GB minimum: ~13GB for the two GGUF models, ~20-30GB for the Docker image, remainder for processed books and headroom. 240GB+ recommended if you plan to add general-purpose chat/coding models later. |
| OS | Ubuntu 24.04 LTS with a kernel ≥6.12 (see Section II) |

Two models are loaded for the core pipeline (sequentially, not
simultaneously, to fit in VRAM):
- **Vision/OCR model**: olmOCR-2-7B-1025 (Q6_K quantization, ~5.8GB; a Qwen2.5-VL fine-tune specialized for document OCR) — reads each scanned page image and extracts structured text
- **Translation model**: Qwen2.5-7B-Instruct (Q6_K quantization, ~5.8GB) — translates the extracted text

## I.3 — Technologies used

| Layer | Technology | Purpose |
|---|---|---|
| Inference engine | [llama.cpp](https://github.com/ggerganov/llama.cpp) | Runs both GGUF-quantized models |
| GPU backend | ROCm 7.2.4 (stable) or Vulkan (LunarG SDK) | Two interchangeable ways to run llama.cpp on the AMD GPU — Vulkan currently the faster of the two on this hardware, ROCm kept as a fallback |
| Containerization | Docker + Docker Compose | Isolates the whole toolchain (ROCm/Vulkan libraries, Python environment, models) from the host OS, and makes the setup reproducible |
| OCR/text extraction | olmOCR-2-7B-1025 via llama-server (Tesseract path kept for reference, not the default) | The vision-language model reads each page image directly, rather than relying on traditional OCR alone, since a VLM can also parse structure (headers, page numbers, body text) contextually |
| Translation | Qwen2.5-7B-Instruct via llama-server | Text-only translation model, prompted with instructions on term preservation and italicization |
| PDF generation | PyMuPDF (`fitz`), Ghostscript, `ocrmypdf`, `pdf2image` | Assembles the final English-only and side-by-side PDFs, handles source PDF manipulation |
| Prompts | Plain text files (`translation_prompt.txt`, `vlm_ocr_prompt.txt`) | Kept as easily-editable files rather than baked into code, per the original requirement — edit these to change translation behavior without touching Python |

## I.4 — General processing steps (the pipeline)

Each book goes through four stages, run as separate scripts in
`pipeline/`, in order:

1. **`1_ocr_and_extract_vlm.py`** — Renders each page of the source PDF as
   an image, sends it to the vision model server (port 11435), and saves
   the structured extraction (page number, header, body text) to
   `extracted.txt` in the book's processed folder. This is the step that
   was validated end-to-end on a full 109-page book.

2. **`2_translate.py`** — Reads `extracted.txt`, sends each page's text to
   the translation server (port 11434) with the translation prompt
   (technical terms preserved/italicized), saves results to
   `translation.txt`.

3. **`3_english_only_pdf.py`** — Builds a portrait, US Letter-size PDF
   containing only the English translation, with a cover page (date,
   models used, disclaimer) and up to two source pages packed per output
   page.

4. **`4_side_by_side_pdf.py`** — Builds a landscape, US Letter-size PDF
   with the original page image on the left and the English translation
   on the right, one source page per output page, for direct comparison.

All intermediate outputs (raw VLM extraction, raw translation text) are
kept alongside the final PDFs in `processed/<book>_translated/`, per the
original requirement to preserve every step for iteration — you don't
need to re-run OCR to experiment with a different translation prompt, for
example.

## I.5 — Known limitations (as of this writing)

- Footnotes for difficult-to-translate terms are not yet implemented.
- Translation of drill / command tables is inconsistent — braced
  alternative-line groups sometimes flatten into a sentence.
- The English-only PDF's figure detection is deliberately conservative:
  faint half-tone illustrations and small decorative vignettes can be
  missed.
- Only run against clean library scans so far, not a phone-photographed
  book.

For the current, authoritative list of open items see `docs/STATUS.md` in
the repo. These are pipeline-code gaps, not infrastructure gaps — the
underlying system (Sections II/III) is functional; output-formatting work
is ongoing.
