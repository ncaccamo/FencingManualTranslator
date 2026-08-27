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
32GB RAM, Ubuntu 24.04 + kernel ≥6.12.

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

## Where to look
- `docs/STATUS.md` — current state, prioritized open items, and the
  non-obvious decisions worth not re-litigating. **Start here.**
- `README.md` — front door: pipeline overview, quick start, doc index.
- `setup/MANUAL_SECTION_I_OVERVIEW.md`,
  `setup/MANUAL_SECTION_II_HOST_PREREQUISITES.md`,
  `setup/MANUAL_SECTION_III_USAGE.md` — user manual (overview / host
  setup / day-to-day operation).
- `docs/remote-gpu.md` — `backend.env`, remote-GPU mode, `rocm714`.
- `docker/ROCM714_NOTES.md` — why the ROCm 7.14 backend is parked.
- Session history: `git log` (HANDOFF.md / MANIFEST.md retired 2026-08-27).
