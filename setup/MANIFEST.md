# BookTranslator Rebuild — Script Manifest
# Running log of everything built during the ROCm 7.2.4 / Vulkan / Open WebUI
# upgrade. This is the source list for the eventual consolidated
# 00_install_from_scratch.sh + User Manual Section II, once everything below
# is confirmed working end-to-end.
#
# STATUS KEY: [ ] not yet verified   [x] confirmed working on real hardware

## Host-level (run once, outside Docker)

[x] 01_host_rocm72_upgrade.sh — CONFIRMED 2026-08-25
    - Removes ROCm 6.4.3, installs ROCm 7.2.4 (production stream, gfx1201 native)
    - Requires reboot at the end
    - Verified: /opt/rocm/.info/version = 7.2.4, rocminfo shows gfx1201 (RX 9070 XT),
      groups shows video+render already present

## BookTranslator container (run from ~/BookTranslator)

[x] 02_update_docker_vulkan_benchmark.sh — CONFIRMED 2026-08-25 (rocm stage)
    - Rewrites Dockerfile: ROCm 7.2.4 + dual llama.cpp build (HIP + Vulkan)
    - Adds linux_scripts/benchmark_backends.sh
    - rocm (7.2.4 stable) backend confirmed compiling successfully
    - vulkan backend: build not yet confirmed, expected to be straightforward

[ ] 04_dockerfile_cleanup.sh
    - Same Dockerfile, adds build-artifact cleanup (smaller image)
    - Run AFTER 02, BEFORE first real build if possible (saves a rebuild)

[ ] 05_fix_compose_and_entrypoint.sh
    - Fixes docker-compose.yml: ai-net network, port 11435, group_add instead
      of privileged:true, explicit LLAMA_BACKEND env
    - Fixes entrypoint.sh: respects MODEL_PATH/VLM_MODEL_PATH env vars instead
      of hardcoding filenames, adds fuzzy-match fallback to avoid duplicate
      downloads
    - SUPERSEDED (session 2): entrypoint no longer starts any llama-server
      (was bouncing the container via `wait -n`); it only checks/downloads
      models then `exec tail -f /dev/null`. The pipeline scripts own server
      lifecycle; `pipeline/start_servers.sh` covers interactive use.
      docker-compose.yml also gained `user: ${HOST_UID}:${HOST_GID}` so
      ./processed output isn't root-owned.

## Open WebUI (standalone, run anywhere)

[ ] 03_setup_open_webui.sh
    - Creates ~/OpenWebUI, docker-compose.yml pinned to v0.11.0
    - Joins ai-net network for future connectivity to BookTranslator
    - Zero models connected out of the box — connect later via Admin Settings

## Not yet built (waiting on decisions / backend confirmation)

[~] ROCm 7.14 preview backend — PARKED 2026-08-25 (see docker/ROCM714_NOTES.md)
    - Confirmed root cause: hip-lang-config.cmake not shipped anywhere in
      rocm[libraries,devel,device-gfx1201]==7.14.0 - preview wheels appear
      runtime-only, not a build SDK for compiling new HIP source
    - Build stage disabled (skip message) so it doesn't block rocm/vulkan
    - Entrypoint falls back to rocm if rocm714 binary is ever missing
    - Breadcrumbs left in docker/ROCM714_NOTES.md for revisiting later

[ ] Multi-model serving compose (gpt-oss-20b general chat, qwen2.5-coder-14b
    coding) — reusing the same llama-server-{rocm,vulkan} binaries rather
    than adding Ollama as a second runtime. Blocked on: backend choice
    confirmed, one-at-a-time vs simultaneous decision confirmed.

[ ] Open WebUI <-> BookTranslator connection (register 11434/11435 as
    OpenAI-compatible connections once backend confirmed stable)

[x] Italic support for technical terms in PDF output — DONE session 2
    (pipeline/_render.py renders <i>…</i> as real italics in both PDFs)

[x] Full-length book test — DONE session 2 (Joseph-Renaud_Lescrime,
    109 pp, end-to-end 1→2→3→4). Second book still pending.

## Pipeline components (in ./pipeline/, mounted into the container at /app)

  1_ocr_and_extract.py       Tesseract OCR path (NOT the default; kept for
                             reference / no-VLM fallback)
  1_ocr_and_extract_vlm.py   DEFAULT OCR — olmOCR-2 via llama-server :11435.
                             Writes raw_ocr.txt, extracted.txt (canonical
                             PAGE_NUMBER/HEADER/BODY_TEXT), page_numbers.txt
  2_translate.py             Qwen2.5-7B via llama-server :11434. Writes
                             translation.txt. Never drops a page.
  3_english_only_pdf.py      Flowing single-column English PDF + inline
                             figures (row-density band detection)
  4_side_by_side_pdf.py      Scan-left / translation-right; paragraph
                             alignment when the scan has a text layer
  _render.py                 Shared rich-text layout (fitz.TextWriter):
                             line breaks, indentation, <i> italics,
                             overflow-safe flow. Used by 3_ and 4_.
  reprocess_extracted.py     Rebuild extracted.txt + page_numbers.txt from a
                             saved raw_ocr.txt (re-apply parse/reconcile
                             fixes without re-running OCR)
  start_servers.sh           Manually start both llama-servers (interactive /
                             Open WebUI use; the pipeline doesn't need it)
  translation_prompt.txt     Term policy (foil/saber/parry; épée kept) +
                             few-shot examples
  vlm_ocr_prompt.txt         Verbatim-transcription rules incl. command-list
                             / drill layout preservation
  test_vlm_single_page.py    One-page VLM OCR check against :11435
  check_mmproj.py            mmproj auto-match sanity check

Per-book sidecar files (optional, next to the source PDF in ./books/):
  <stem>.lang   Tesseract language codes (Tesseract path only)
  <stem>.crop   left,top,right,bottom fractions to trim before OCR

## Once everything above is [x]:

-> Consolidate into 00_install_from_scratch.sh (idempotent, single entry point)
-> Becomes User Manual Section II content
-> User Manual Section III ("how to use") written against the consolidated
   script's actual final state, not these intermediate patches
