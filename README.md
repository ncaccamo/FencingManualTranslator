# BookTranslator

Translates historical fencing / HEMA manuals into English while keeping the
scholarly conventions: technical terms left in the original and italicized,
original layout and figures preserved, English-only and side-by-side PDF
outputs, an AI-disclosure cover page. **Runs entirely on local hardware —
no cloud API calls, nothing leaves the machine.**

Tuned for an AMD RX 9070 XT (RDNA4, 16 GB) + Ryzen 7 5700X3D on Ubuntu
24.04, via a self-contained Docker image (ROCm 7.2.4 + Vulkan + llama.cpp).

## Pipeline

Each book runs through four scripts in `pipeline/`, writing to
`processed/<book>_translated/`:

| Step | Script | Does | Output |
|---|---|---|---|
| 1 | `1_ocr_and_extract_vlm.py` | page image → structured text (olmOCR-2-7B on :11435) | `raw_ocr.txt`, `extracted.txt`, `page_numbers.txt` |
| 2 | `2_translate.py` | extracted text → English (Qwen2.5-7B on :11434) | `translation.txt` |
| 3 | `3_english_only_pdf.py` | flowing English reading copy, figures inline | `… — English Only (Portrait).pdf` |
| 4 | `4_side_by_side_pdf.py` | scan left / translation right, paragraph-aligned | `… — Side by Side (Landscape).pdf` |

Every intermediate is kept, so you can re-run a later stage (e.g. tweak
`translation_prompt.txt`) without redoing OCR.

## Quick start

Prereqs: Ubuntu 24.04, kernel ≥ 6.12, Docker, an `ai-net` docker network,
AMD GPU set up — see **`setup/MANUAL_SECTION_II_HOST_PREREQUISITES.md`**
for the full bare-metal setup.

```bash
cp backend.env.example backend.env        # defaults: local / vulkan
docker compose build                       # first time only (~20-30 min)
docker compose up -d                        # container comes up idle

# put source PDFs in ./books/, then:
docker exec booktranslator-translator-1 python3 /app/1_ocr_and_extract_vlm.py
docker exec booktranslator-translator-1 python3 /app/2_translate.py
docker exec booktranslator-translator-1 python3 /app/3_english_only_pdf.py
docker exec booktranslator-translator-1 python3 /app/4_side_by_side_pdf.py
# (or: scripts/run_full_pipeline.sh)
```

One-time on a fresh checkout: `sudo chown -R "$(id -u):$(id -g)" processed/`.

## Backends

`backend.env` selects where inference runs:

- `BACKEND_MODE=local` + `LLAMA_BACKEND=vulkan | rocm7 | rocm714` — runs
  `llama-server` in the container. Switching backends is
  `docker compose up -d`, no rebuild.
- `BACKEND_MODE=remote` — points the pipeline at a `llama-server` on
  another machine over the LAN (bearer-token auth). Load the vision model
  there before step 1, the text model before step 2.

Details: `docs/design-backend-selection.md`.

## Docs

| File | For |
|---|---|
| `README.md` | this — the front door |
| `docs/STATUS.md` | current state, open items, decisions worth not re-litigating |
| `docs/design-backend-selection.md` | backend-mode design + status |
| `setup/MANUAL_SECTION_I_OVERVIEW.md` | user manual — what it does, hardware, stack (needs a refresh) |
| `setup/MANUAL_SECTION_II_HOST_PREREQUISITES.md` | user manual — bare-metal host setup |
| `setup/MANIFEST.md` | setup-script tracking + pipeline file inventory |
| `CLAUDE.md` | orientation + hard invariants for AI coding agents |
| `docker/ROCM714_NOTES.md` | why the ROCm 7.14 backend is parked |
