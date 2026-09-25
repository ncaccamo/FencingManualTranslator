# BookTranslator

Translates historical fencing / HEMA manuals into English while keeping the
scholarly conventions: technical terms left in the original and italicized,
original layout and figures preserved, English-only and side-by-side PDF
outputs, an AI-disclosure cover page. **Runs on your own hardware — no
cloud API calls; nothing leaves your machine (or, in remote-GPU mode,
your LAN).**

Tuned for an AMD RX 9070 XT (RDNA4, 16 GB) + Ryzen 7 5700X3D on Ubuntu
24.04, via a self-contained Docker image (ROCm 7.2.4 + Vulkan + llama.cpp).

## Pipeline

Each book runs through four scripts in `pipeline/`, writing to
`processed/<book>_translated/`. The two finished PDFs land in that folder's
root; everything else (raw OCR, the parsed scaffold, page numbers, the
translation) lives in an `intermediate/` subfolder so the root stays clean:

| Step | Script | Does | Output |
|---|---|---|---|
| 1 | `1_ocr_and_extract_vlm.py` | page image → structured text (olmOCR-2-7B on :11435) | `intermediate/raw_ocr.txt`, `intermediate/extracted.txt`, `intermediate/page_numbers.txt` |
| 2 | `2_translate.py` | extracted text → English (Qwen2.5-14B-Instruct on :11434) | `intermediate/translation.txt` |
| 3 | `3_english_only_pdf.py` | flowing English reading copy, figures inline | `… — English Only (Portrait).pdf` |
| 4 | `4_side_by_side_pdf.py` | scan left / translation right, paragraph-aligned | `… — Side by Side (Landscape).pdf` |

Every intermediate is kept, so you can re-run a later stage (e.g. tweak
`translation_prompt.txt`) without redoing OCR.

## Quick start

**Local GPU** (single box). Host needs only: Ubuntu 24.04 **or newer**,
kernel ≥ 6.12 (for the in-kernel `amdgpu` driver), the `render`/`video`
groups, and Docker. **No host ROCm, Vulkan SDK, or Mesa** — those are in
the image, which stays `FROM ubuntu:24.04` regardless of the host. Full
bare-metal steps: **`setup/MANUAL_SECTION_II_HOST_PREREQUISITES.md`**.

```bash
bash scripts/setup.sh     # verify host, write .env/backend.env, ai-net,
                          # build (~20-30 min), bring the container up idle
# first 'up' downloads ~13 GB of models (local mode only) -> docker compose logs -f translator

# put source PDFs in ./books/, then:
docker exec booktranslator-translator-1 python3 /app/1_ocr_and_extract_vlm.py
docker exec booktranslator-translator-1 python3 /app/2_translate.py
docker exec booktranslator-translator-1 python3 /app/3_english_only_pdf.py
docker exec booktranslator-translator-1 python3 /app/4_side_by_side_pdf.py
# (or: scripts/run_full_pipeline.sh)
```

Day-to-day operation, re-run rules, troubleshooting:
**`setup/MANUAL_SECTION_III_USAGE.md`**.

## Backends

`backend.env` selects where inference runs:

- `BACKEND_MODE=local` + `LLAMA_BACKEND=vulkan | rocm7 | rocm714` — runs
  `llama-server` in the container. Switching backends is
  `docker compose up -d`, no rebuild.
- `BACKEND_MODE=remote` — inference runs on a `llama-server` (or
  llama-swap) on another LAN machine, bearer-token auth. The
  BookTranslator host then needs **no GPU** (`scripts/setup.sh --remote`
  skips the GPU checks; a GPU-less host also needs
  `docker-compose.override.yml` — see below). With llama-swap on the GPU
  box the vision/text models swap automatically by request; with a bare
  `llama-server` you swap it by hand between step 1 and step 2.

Details, including the GPU-less-host setup: `docs/remote-gpu.md`.

## Per-book overrides

Optional files next to a source PDF in `books/`, matching its stem:

| File | Effect |
|---|---|
| `<stem>.crop` | `left,top,right,bottom` fractions trimmed from each page before OCR |
| `<stem>.lang` | Tesseract language codes — only used by the non-default Tesseract OCR path |

## Docs

| File | For |
|---|---|
| `README.md` | this — the front door |
| `docs/STATUS.md` | current state, open items, decisions worth not re-litigating |
| `setup/MANUAL_SECTION_I_OVERVIEW.md` | user manual — what it does, hardware, stack |
| `setup/MANUAL_SECTION_II_HOST_PREREQUISITES.md` | user manual — bare-metal host setup |
| `setup/MANUAL_SECTION_III_USAGE.md` | user manual — day-to-day operation, iteration loops, troubleshooting |
| `docs/model-quirks.md` | every way the OCR / translation models fail on this corpus + the prompt/code mitigation — read before tuning a prompt |
| `docs/pdf-layout.md` | how steps 3 & 4 build the PDFs — the `_render` engine, figure/alignment logic, the knobs |
| `docs/remote-gpu.md` | running inference on a remote GPU box / GPU-less BookTranslator host |
| `docs/evaluation-and-tuning.md` | **maintainer-only** — the regression harness (`evals/`), gold-reference scoring, swapping the translation model, the fine-tuning plan. Not needed to run the pipeline. |
| `CLAUDE.md` | orientation + hard invariants for AI coding agents |
| `docker/ROCM714_NOTES.md` | why the ROCm 7.14 backend is parked |
