# CLAUDE.md — BookTranslator

## What this is
Translates historical fencing/HEMA manuals into English while preserving
scholarly conventions: technical terms left untranslated and italicized,
footnotes for hard-to-translate passages, original layout/images
preserved, English-only and side-by-side PDF outputs, AI-disclosure cover
page. Runs fully locally, no cloud API calls.

## Sibling projects — read this before assuming a dependency exists
This repo is **fully self-contained**. It does not depend on
`LocalAIChat` or `BackendBuildstage` to build or run — `docker/Dockerfile`
already compiles its own ROCm 7.2.4 + Vulkan dual llama.cpp build stage.

- `LocalAIChat` — separate product (Open WebUI + general chat/coding
  models). No relationship at build or runtime. Not run at the same time
  as this project (shared GPU).
- `BackendBuildstage` — offline sandbox only, for testing a new
  ROCm/Vulkan/llama.cpp version before it's manually copied into this
  repo's Dockerfile. Never a build or runtime dependency. This repo is
  the *source* that sandbox was seeded from, not the other way around.

## Hardware this is tuned for
AMD RX 9070 XT (RDNA4, gfx1201, 16GB VRAM), Ryzen 7 5700X3D (8c/16t),
32GB RAM. Host: Ubuntu 24.04+ (tested on 24.04), kernel ≥6.12 for the
in-kernel amdgpu driver. The `FROM ubuntu:24.04` pin is the image's, not
the host's — a newer host runs the 24.04 container unchanged.

`BACKEND_MODE=remote` (`backend.env`) makes the pipeline host
GPU-agnostic: inference runs on a `llama-server` / llama-swap on another
LAN system, so the host needs only x86-64 + Docker (no GPU, no kernel 6.12,
no ROCm/Vulkan). `scripts/setup.sh --remote` + `docker-compose.override.yml`
(strips the compose GPU device mounts). See `docs/remote-gpu.md`.

## Key invariants — do not violate these without a deliberate decision
- `restart: "no"` everywhere. On-demand system, not a long-running
  service. Don't reintroduce `unless-stopped` — it previously masked a
  real container-bounce bug.
- The pipeline owns server lifecycle, not the container entrypoint. The
  entrypoint does not auto-start llama-servers; start/stop logic lives in
  the pipeline scripts (`1_ocr_and_extract_vlm.py`, `2_translate.py`,
  `pipeline/start_servers.sh`).
- VLM/olmOCR-2-7B-1025 is the default and only OCR path (vision on port
  11435, translation on 11434) — decided after an 8-page comparison
  against forced-Tesseract and Gallica's text layer. No Tesseract
  fallback by design.
- Raw OCR output (`raw_ocr.txt`) must always be written before any
  cleanup runs, unconditionally — this was a real gap once, don't
  reintroduce it.
- `docker exec` shells do not inherit the entrypoint's `LD_LIBRARY_PATH`
  for the backend lib dir — any new `Popen` call starting a llama-server
  needs the same `_llama_server_env()` handling already used in the
  existing scripts.
- The host needs **no ROCm / Vulkan / Mesa** — just the in-kernel
  `amdgpu` driver (kernel ≥6.12) + Docker (in `local` mode; `remote` mode
  drops the amdgpu/kernel requirement too). All GPU userspace is in the
  image; nothing reads host `/opt/rocm`. Setup is `scripts/setup.sh`
  (`--remote` for a GPU-less host; verify + build, no sudo). Don't
  reintroduce a host ROCm install step.

## Where to look
- `docs/STATUS.md` — current state, prioritized open items, and the
  non-obvious decisions worth not re-litigating. **Start here.**
- `README.md` — front door: pipeline overview, quick start, doc index.
- `setup/MANUAL_SECTION_I_OVERVIEW.md`,
  `setup/MANUAL_SECTION_II_HOST_PREREQUISITES.md`,
  `setup/MANUAL_SECTION_III_USAGE.md` — user manual (overview / host
  setup / day-to-day operation).
- `docs/model-quirks.md` — catalogue of every OCR / translation model
  failure mode on this corpus + its prompt/code fix. Read before touching
  `vlm_ocr_prompt.txt` or `translation_prompt.txt`.
- `docs/pdf-layout.md` — how steps 3 & 4 render the PDFs (`_render`
  layout engine, figure placement, paragraph alignment, the knobs).
- `docs/remote-gpu.md` — `backend.env`, remote-GPU mode (llama-swap or
  bare server), GPU-less host setup, `rocm714`.
- `docs/evaluation-and-tuning.md` — maintainer-only: the `evals/`
  regression harness, gold-reference scoring, model swaps, fine-tuning.
  The operator's books/corpus/baselines are git-ignored (`books/`,
  `processed/`, `evals/gold|baselines|reports/`) — the repo is a general
  tool, not tied to a specific corpus.
- `docker/ROCM714_NOTES.md` — why the ROCm 7.14 backend is parked.
- Session history: `git log` (HANDOFF.md / MANIFEST.md retired 2026-08-27).

## Workflow notes
- Ask for my input on decisions you think need it.
- Make changes freely based on feedback.
- Do not commit or push anything to remote until I explicitly say to.
- Give time estimates where possible for reprocessing steps.
- Run processes such that you can track completion of steps and move to the next or notify automatically. run_in_background: true or similar.
- Given the nature of this project and the multiple different formats of source books, be very careful with changes that could introduce regression. We don't want to be playing whack-a-mole by fixing an issue in the current book that breaks good behavior in one we worked on earlier.
- Diagnose every flagged-page bug from that page's real data, never
  from a similar-looking earlier bug: dump its `raw_ocr.txt` text,
  `extracted.txt` fields, and rendered PDF page (scratchpad script in
  the container) before changing code, and re-check the same page after.
  Run `python3 evals/test_rules.py` after every change — one frozen test
  per past fix; a failure means a different book regressed. With only
  one confirmed instance, prefer a narrow fix or a direct data patch
  over widening a shared function (e.g. `reconcile_headers()`,
  `reconcile_page_numbers()`); a second real example is the bar for
  generalizing. Broad unverified fixes caused every regression in the
  batch-2 review cycle.
