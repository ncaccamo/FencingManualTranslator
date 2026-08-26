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
      downloads, starts both translation (11434) and VLM (11435) servers

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

[ ] Italic support for technical terms in PDF output (currently disabled)

[ ] Full-length book test (currently validated on 7-page sample only)

## Once everything above is [x]:

-> Consolidate into 00_install_from_scratch.sh (idempotent, single entry point)
-> Becomes User Manual Section II content
-> User Manual Section III ("how to use") written against the consolidated
   script's actual final state, not these intermediate patches
