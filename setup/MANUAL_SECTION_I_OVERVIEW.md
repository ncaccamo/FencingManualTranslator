# User Manual — Section I: System Overview

This section explains *what BookTranslator is and how it works* at a
conceptual level. Section II covers preparing a host machine to run it;
Section III covers day-to-day operation.

---

## I.1 — What this system does

BookTranslator converts a scanned historical fencing or martial-arts
manual (a PDF) into two English PDFs: a plain reading copy, and a
side-by-side copy with the original scan next to the translation.

It is built for the conventions this kind of scholarly translation
expects:

- **Technical terms are kept in the original language and italicized**
  rather than forced into a lossy English equivalent. *Prise de fer*,
  *froissement*, *liement*, *coulé* and the named lines stay as they are;
  *fleuret* becomes "foil", *parade* becomes "parry", and headings are
  translated in full.
- **A bare source footnote** — `Fr. liement` — is emitted the first time
  each hard term (`bind`, `disengagement`, `taking the blade`, …) appears
  in a book, so a reader can see the original word. Not an explanation.
- **Original page structure is preserved** as far as each output format
  allows — page numbers, running headers, drill and command layout,
  brace-grouped alternatives, and figures pulled from the scan.
- **Every output opens with a disclosure cover page**: that the
  translation is AI-generated, the date it was produced, and which models
  did the OCR and the translation.

Everything runs on your own hardware. There are no cloud API calls and no
per-page or per-book cost. Two deployments:

- **Single box with an AMD GPU** — the default. Nothing leaves the
  machine.
- **Split** — a GPU-less host runs the pipeline (OCR parsing, PDF
  rendering) and sends inference to a `llama-server` on another machine
  *on your own LAN*, bearer-token authed. Still nothing leaves your
  network. See `docs/remote-gpu.md`.

## I.2 — Hardware requirements

### Single box (local GPU)

| Component | Notes |
|---|---|
| GPU | AMD, 16 GB VRAM. Developed and run on an RX 9070 XT (RDNA4, gfx1201). Needs either a working ROCm 7.x stack or a Vulkan driver — the container ships both llama.cpp builds and `backend.env` picks one. |
| CPU | 8 cores / 16 threads recommended (developed on a Ryzen 7 5700X3D). |
| RAM | 32 GB. The compose file caps the container at 28 GB. |
| Disk | ~50 GB: ~13 GB for the two GGUF models (in `~/llm_models`), ~20–30 GB for the Docker image, the rest for processed books. 240 GB+ if you later add general-purpose chat/coding models alongside. |
| OS | Ubuntu 24.04 LTS, kernel ≥ 6.12. See Section II. |

### Split (remote GPU)

| Where | Needs |
|---|---|
| Pipeline host | **No GPU.** x86-64 Ubuntu 24.04+, any kernel, Docker. ~35 GB for the image + room for `books/` / `processed/`. RAM: a few GB (PDF rendering only). |
| GPU box | The 16 GB AMD card, a Vulkan (or ROCm/HIP) `llama.cpp` build, and the GGUF model files. No project checkout, no Docker, no Python. A Windows gaming PC works — Vulkan uses the gaming driver. `docs/remote-gpu.md`. |

The two models load **one at a time**, not together, to fit in 16 GB:

- **Vision / OCR** — `olmOCR-2-7B-1025` (Q6_K, ~5.8 GB; a Qwen2.5-VL
  fine-tune specialised for document OCR). Reads each page image and
  returns structured text.
- **Translation** — `Qwen2.5-14B-Instruct` (Q4_K_M, ~8.6 GB). Translates the
  extracted text.

The OCR step (step 1) loads the vision model on port 11435 and unloads it
when done; the translation step (step 2) then loads the text model on port
11434. This is why the two steps are separate scripts and why nothing runs
between books.

## I.3 — Technology stack

| Layer | What | Why |
|---|---|---|
| Inference engine | [llama.cpp](https://github.com/ggerganov/llama.cpp) `llama-server` | Runs both GGUF models behind an OpenAI-compatible HTTP API. |
| GPU backend | ROCm 7.2.4 **and** Vulkan (LunarG SDK), both compiled into the image | Two interchangeable ways to run llama.cpp on the AMD GPU. Vulkan is currently the faster of the two on this hardware and is the default; ROCm is kept as a fallback. Switching is a config edit, not a rebuild. |
| Containerisation | Docker + Docker Compose | Isolates the ROCm/Vulkan libraries, Python environment and tooling from the host. The container runs as your host UID so files in `processed/` are yours, not root's. |
| OCR / extraction | `olmOCR-2-7B-1025` via `llama-server` | A vision-language model reads the page image directly. Unlike plain OCR it also parses *structure* — it separates the page number, the running header and the body, and it transcribes drill layout and marginalia sensibly. A legacy Tesseract path (`1_ocr_and_extract.py`) is kept in the repo for reference but is not used. |
| Translation | `Qwen2.5-14B-Instruct` via `llama-server` | Text-only translation, steered entirely by `translation_prompt.txt`. |
| PDF generation | PyMuPDF (`fitz`), with a custom text layouter (`pipeline/_render.py`) | Assembles the two output PDFs, pulls figure crops from the scan, and lays out rich text (line breaks, indentation, `<i>` italics, footnote blocks) with overflow-safe flow. |
| Prompts | Plain-text files (`translation_prompt.txt`, `vlm_ocr_prompt.txt`) | Kept editable outside the code, per the original design requirement — change translation behaviour without touching Python. |

## I.4 — The pipeline

Each book goes through four scripts in `pipeline/`, in order. All outputs
land in `processed/<book>_translated/`.

### Step 1 — `1_ocr_and_extract_vlm.py`  (vision model, port 11435)

Renders every page of the source PDF at 300 DPI, downscales to a fixed
1288 px on the long edge, and sends each image to the vision model. Writes
three files:

- **`raw_ocr.txt`** — the model's untouched responses, one per page,
  separated by `\n---\n`. Written *before* any parsing or cleanup, always.
  This is the recovery point: `reprocess_extracted.py` can rebuild the
  next two files from it without paying for OCR again.
- **`extracted.txt`** — a canonical `PAGE_NUMBER: / HEADER: / BODY_TEXT:`
  scaffold per page, after cleanup (runaway repetition loops on
  dot-leader / ornament pages are clipped here).
- **`page_numbers.txt`** — one reconciled page number per line.
  `olmOCR-2` invents page numbers from stray marks (plate numbers, stamps)
  and normalises roman numerals, so a folio is trusted only when ≥ 2
  consecutive pages agree on the same page-index→folio offset; otherwise
  the line is a bracketed sheet ordinal like `[12]`. It never emits a
  number ≤ 0.

It also copies the source PDF into the folder as `<book>_OCR.pdf` for the
side-by-side step to draw from.

### Step 2 — `2_translate.py`  (translation model, port 11434)

Reads `extracted.txt`, strips the structural labels, and translates each
page's body with `translation_prompt.txt`. Writes **`translation.txt`**
(pages separated by `\n---\n`, so it stays aligned with `extracted.txt`
page-for-page).

- Technical terms are preserved and wrapped in `<i>…</i>`; headings and
  captions are translated in full.
- Each Category-3 term (see `translation_prompt.txt` rule 3b) is
  translated and gets a bare `Fr. <term>` footnote on its first
  occurrence per book, under a `___FOOTNOTES___` marker on that page.
- Output is validated: wrong-script output (CJK / Cyrillic / etc.),
  more than ~35 % non-English words, output under half the source length,
  or a bare model preamble / refusal ("Here is the translation:", "Please
  provide the text…") triggers up to 3 retries.
- A near-blank source page (a stray mark, a bare page number) is written
  as `[Empty page]` without a model call.
- **A page is never dropped.** If every attempt fails it is written as
  `[low-confidence translation — review]` followed by the best attempt,
  or `[translation unavailable — original text follows]` with the source
  text, so the page count never changes.
- Pathologically oversized pages (> 2500 words, almost always an OCR
  repetition artefact) get their first 2500 words translated with a note,
  rather than stalling the run.

### Step 3 — `3_english_only_pdf.py`

Builds the **English-only reading copy** — portrait, US Letter. Text flows
continuously across as many pages as it needs (it is not one source page
per sheet). Figures detected in the scan are placed inline with an
`[illustration from the original]` caption. Footnote blocks are rendered
at the end of their section in small grey type under a rule. Opens with
the disclosure cover page.

### Step 4 — `4_side_by_side_pdf.py`

Builds the **side-by-side copy** — landscape, US Letter, one source page
per sheet. Original scan on the left, translation on the right, with a
divider. Where the scan carries an embedded text layer, translated
paragraphs are top-anchored to the vertical position of the matching
source paragraph so the two columns track each other. Footnotes for the
page sit in a reserved strip at the bottom of the right column. Same cover
page.

Every intermediate is kept, so any later stage can be re-run on its own —
tweak `translation_prompt.txt` and re-run step 2 without redoing OCR;
tweak a PDF script and re-run just it. Section III covers this.

## I.5 — Backend selection

`backend.env` (copied from `backend.env.example`) decides where inference
runs:

- `BACKEND_MODE=local` with `LLAMA_BACKEND=vulkan | rocm7 | rocm714` —
  `llama-server` runs inside the container. `vulkan` (default) and `rocm7`
  are compiled into the image; switching between them is
  `docker compose up -d`, no rebuild. `rocm714` is wired but not yet
  compiled.
- `BACKEND_MODE=remote` — no local server; both steps call a
  `llama-server` on another machine on your LAN, with a bearer token. If
  that box runs **llama-swap** the vision/text models swap automatically
  by request name (the pipeline sends `VLM_MODEL_NAME` for step 1,
  `TRANSLATION_MODEL_NAME` for step 2) — no manual step, `run_full_pipeline.sh`
  works. With a bare `llama-server` you restart it with the other model
  between step 1 and step 2. `scripts/setup.sh --remote` prepares a
  GPU-less pipeline host. Details in `docs/remote-gpu.md`.

## I.6 — Known limitations

Authoritative, current list: `docs/STATUS.md`. Every model failure mode
and the prompt/code guard for it: `docs/model-quirks.md`. As of this
writing:

- **Drill / command tables translate inconsistently** — brace-grouped
  alternative lines flatten into a sentence roughly half the time.
- **Table-of-contents pages half-translate** — a few kept terms
  (`coupé`, `battement`) slip through untranslated on dotted-leader
  pages.
- **Florid literary prose defeats the translation model** — technical drill text
  translates well; long rhetorical passages (e.g. LaMarche's
  `Le Fleuret et l'Épée`) can come back partly in French under a
  `[low-confidence]` flag.
- **Figure detection is conservative and tuned for framed photo plates.**
  Full-page line-drawing plates and faint half-tones are often missed
  (on one 386-page book, 4 of ~20 plates were caught). It will not
  produce a false figure from dense text.
- **Paragraph alignment in the side-by-side PDF depends on the scan's
  text layer.** A clean library scan aligns ~70 % of pages; a patchier
  one (e.g. Google Books OCR) drops to ~45 %. Unaligned pages fall back
  to plain top-to-bottom flow.
- **Printed marginalia are dropped** by the OCR step. Fine when the
  margin notes are cross-references; a problem if a source carries real
  content there.
- **Only clean flatbed/library scans have been run** — not a
  phone-photographed book.
- These source footnotes carry a `†N` inline mark and a matching line in
  the page's "Original terms:" block.

These are output-quality gaps in pipeline code. The infrastructure
(Sections II / III) is stable.
