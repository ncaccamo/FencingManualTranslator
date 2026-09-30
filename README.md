# BookTranslator

Turns a scanned historical fencing / HEMA manual into an English study
edition: the original page on the left, a scholarly translation on the
right, page for page. It runs fully on your own hardware — **no cloud API
calls; nothing leaves your machine (or, in remote-GPU mode, your LAN).**

It is built for reading and research, not for publishing unreviewed: every
translation is machine-made (a cover page says so), and the output flags
what it isn't sure of so you can check it against the facing scan.

## What it does to a book

- **Reads the scan** with a vision model (olmOCR-2) — body text, the running
  head, the printed page number, plates and their captions — rather than
  trusting the PDF's own text layer.
- **Translates** with a local LLM (Qwen3.8-27B) under a fencing-
  specific term policy:
  - terms English fencing literature keeps (*riposte*, *remise*, *quarte*,
    *épée* …) stay in the original, italicized;
  - ordinary terms get their standard English names (*parade* → parry,
    *fente* → lunge, *coup* → thrust, *botte* → hit);
  - hard-to-translate terms (*coupé*, *dérobement*, *appel* …) are translated
    and footnoted with the French on first use.
- **Keeps the book's apparatus**: running heads (translated), page numbers
  (shown when printed, or when the surrounding numbers pin them down
  exactly; unnumbered plates show none — never a guess), chapter titles,
  the author's own footnotes, plate captions.
- **Builds two PDFs**, with an AI-disclosure cover page:
  - **Side by Side (Landscape)** — scan left, translation right. This is the
    primary output and the one under active review.
  - **English Only (Portrait)** — a flowing reading copy with figures
    inline. Currently deferred: it has known layout issues
    (`docs/STATUS.md`).

**Source language:** tested on French manuals (six so far, 1845–1911). The
prompt also carries Italian and German term lists, but no Italian or German
book has been run.

**Review report:** each run also writes `<book> — Review Report.md` next to
the PDFs, a short list of the pages worth checking (by pdf page number) and
why, plus book-wide terminology choices to settle. Your decisions go in a
per-book overrides file that every later run re-applies (manual III.5a–b).

**What the output flags:** `[low-confidence translation — review]` or
`[translation unavailable — original text follows]` on a page the model
struggled with; `†` footnotes for kept French terms; `[margin note]` for
the author's shoulder notes; `[table — see facing page]` where a table is
left to the scan.

## Pipeline

![Pipeline overview](docs/img/pipeline.drawio.svg)

Step by step — what happens, the tools, and the files each step reads and
writes: [detailed pipeline diagram](docs/img/pipeline-detail.drawio.svg).

Each book runs through four scripts in `pipeline/`, writing to
`processed/<book>_translated/`. The two finished PDFs land in that folder's
root; everything else (raw OCR, the parsed scaffold, page numbers, the
translation) lives in an `intermediate/` subfolder:

| Step | Script | Does | Output |
|---|---|---|---|
| 1 | `1_ocr_and_extract_vlm.py` | page image → structured text (olmOCR-2-7B on :11435) | `intermediate/raw_ocr.txt`, `intermediate/extracted.txt`, `intermediate/page_numbers.txt` |
| 2 | `2_translate.py` | extracted text → English (Qwen3.8-27B on :11434) | `intermediate/translation.txt` |
| 3 | `3_english_only_pdf.py` | flowing English reading copy, figures inline | `… — English Only (Portrait).pdf` |
| 4 | `4_side_by_side_pdf.py` | scan left / translation right, paragraph-aligned | `… — Side by Side (Landscape).pdf` |

Every intermediate is kept, so a later stage can be re-run (e.g. after a
`translation_prompt.txt` change) without redoing OCR. A 350-page book takes
a few hours, most of it in step 1; steps 3 and 4 take a minute or two.

## Where it runs

Two modes, set in `backend.env`:

- **Local** — the GPU is in the same system. Tuned for an AMD RX 9070 XT
  (RDNA4, 16 GB) + Ryzen 7 5700X3D. Host needs only Ubuntu 24.04 **or
  newer**, kernel ≥ 6.12 (in-kernel `amdgpu`), the `render`/`video` groups,
  and Docker — **no host ROCm, Vulkan SDK, or Mesa**; those are in the
  image (ROCm 7.2.4 + Vulkan + llama.cpp), which stays `FROM ubuntu:24.04`
  regardless of the host. `LLAMA_BACKEND=vulkan | rocm7 | rocm714`;
  switching is `docker compose up -d`, no rebuild.
- **Remote** — inference runs on a `llama-server` (or llama-swap) on
  another system on your LAN, bearer-token auth. The BookTranslator host
  then needs **no GPU** — just x86-64 + Docker (`scripts/setup.sh --remote`
  plus `docker-compose.override.yml`). With llama-swap the vision and text
  models swap automatically by request; with a bare `llama-server` you
  swap them by hand between step 1 and step 2.

Details: `docs/remote-gpu.md`. Bare-metal host setup:
`setup/MANUAL_SECTION_II_HOST_PREREQUISITES.md`.

## Quick start

```bash
bash scripts/setup.sh     # verify host, write .env/backend.env, ai-net,
                          # build (~20-30 min), bring the container up idle
                          # (--remote for a GPU-less host)
# local mode: first 'up' downloads ~13 GB of models -> docker compose logs -f translator

# put source PDFs in ./books/ (name them <author>_<year>_<short-title>.pdf), then:
docker exec booktranslator-translator-1 python3 /app/1_ocr_and_extract_vlm.py
docker exec booktranslator-translator-1 python3 /app/2_translate.py
docker exec booktranslator-translator-1 python3 /app/3_english_only_pdf.py
docker exec booktranslator-translator-1 python3 /app/4_side_by_side_pdf.py
# (or: scripts/run_full_pipeline.sh)
```

Day-to-day operation, re-run rules, troubleshooting:
**`setup/MANUAL_SECTION_III_USAGE.md`**.

## Your books stay yours

The repository is a general tool, not tied to any corpus. `books/`,
`processed/` and the evaluation data under `evals/` are git-ignored: you
bring your own scans (public-domain sources such as Gallica, Google Books
and archive.org work well) and nothing about them is committed.

## Per-book overrides

Optional files next to a source PDF in `books/`, matching its stem:

| File | Effect |
|---|---|
| `<stem>.overrides.toml` | your review decisions — page numbers, headers, header wording, book terms, fixes to the English; re-applied on every run (manual III.5b) |
| `<stem>.meta.json` | title / author / year / publisher / rights for the cover page (generate with `scripts/make_book_meta.py`) |
| `<stem>.crop` | `left,top,right,bottom` fractions trimmed from each page before OCR |
| `<stem>.rotate` | rotate sideways-scanned plates in the PDFs: `cw`, `ccw` or `180`, optionally prefixed `plates:` (default) or `all:` |
| `<stem>.lang` | Tesseract language codes — only used by the non-default Tesseract OCR path |

Rename a book everywhere it lives (source, outputs, baselines) with
`scripts/rename_book.py`.

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
| `docs/remote-gpu.md` | running inference on a remote GPU system / GPU-less BookTranslator host |
| `docs/evaluation-and-tuning.md` | **maintainer-only** — the regression harness (`evals/`), gold-reference scoring, swapping the translation model, the fine-tuning plan. Not needed to run the pipeline. |
| `CLAUDE.md` | orientation + hard invariants for AI coding agents |
| `docker/ROCM714_NOTES.md` | why the ROCm 7.14 backend is parked |
