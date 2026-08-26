# BookTranslator — Handoff / Offramp Document
Generated at the close of the infrastructure upgrade phase. Paste this into
a new conversation to continue.

---

## 1. CURRENT STATUS

**Infrastructure: DONE and confirmed working.**
- Host: Ubuntu 24.04, ROCm 7.2.4 (upgraded from 6.4.3), AMD RX 9070 XT (gfx1201)
- Docker image builds two working llama.cpp backends:
  - `rocm` — ROCm 7.2.4, stable, confirmed: 85.69 t/s (translation model)
  - `vulkan` — LunarG Vulkan SDK 1.4.357.1 (tarball, not apt), confirmed: 91.96 t/s — **current default in `.env`, winner**
  - `rocm714` — ROCm 7.14 preview, **parked** (see docker/ROCM714_NOTES.md) — pip wheels don't ship `hip-lang-config.cmake`, can't compile new HIP source against it currently
- Both servers (translation on :11434, VLM/OCR on :11435) confirmed loading and serving on both working backends
- `setup_inference_backend.sh` is the single consolidated script recreating all of this from scratch (Docker image + compose file + host ROCm driver)
- **`MANUAL_SECTION_II_HOST_PREREQUISITES.md`** covers what has to be true about the machine BEFORE that script runs: Ubuntu 24.04, a kernel new enough for RDNA4 (6.12+ — the default 24.04 kernel is 6.8, too old; needs the HWE kernel stack), BIOS settings, Docker Engine + Compose plugin installation, and group membership. `setup_inference_backend.sh` does NOT install Docker itself or upgrade the kernel — that's this document's job.

**Pipeline: NOT done — this is the next phase.** The infra work was a detour;
these are the original, still-open items:
1. **Italics disabled** — confirmed by direct code inspection: both
   `3_english_only_pdf.py` and `4_side_by_side_pdf.py` contain
   `plain_text = clean_text.replace('<i>', '').replace('</i>', '')` —
   the `<i>` markers from the translation prompt are stripped before
   rendering, not rendered as actual italic text.
2. **Footnotes for hard-to-translate terms** — not implemented at all.
   Requirement: footnote with original term/phrase when translation is
   difficult, so a reader can cross-reference the source term.
3. **Images/figures in english-only PDF** — side-by-side PDF preserves
   them (embeds the original page via `show_pdf_page`). English-only PDF
   is pure text reconstruction — no images at all currently.
4. **Cover page inconsistency** — english-only PDF's cover includes
   translation/vision model names + AI-disclaimer; side-by-side PDF's
   cover has date only, no model names. Should be consistent.
5. **Full-length book never tested** — only validated on a 7-page sample.
6. **Text overflow risk (found during this session, not in original list)**
   — neither PDF generator checks `insert_textbox`'s return value for
   overflow. PyMuPDF silently truncates text that doesn't fit its
   allocated box. Untested at scale; a full book with longer paragraphs
   than the sample could hit this and silently drop text.

**Not started:** Open WebUI setup (script written, never connected to the
now-working llama-server endpoints), and the two planned chatbot models
(`gpt-oss-20b` general/research, `qwen2.5-coder-14b` coding).

---

## 2. PROJECT STRUCTURE

```
~/BookTranslator/
├── docker-compose.yml       # references docker/Dockerfile, reads LLAMA_BACKEND from .env
├── .env                     # RENDER_GID, VIDEO_GID, LLAMA_BACKEND=vulkan
├── docker/
│   ├── Dockerfile           # ROCm 7.2.4 + Vulkan (LunarG SDK) dual build
│   └── ROCM714_NOTES.md     # why rocm714 is parked, how to revisit
├── pipeline/                # OCR/translation/PDF Python scripts + prompts
│   ├── 1_ocr_and_extract_vlm.py
│   ├── 2_translate.py
│   ├── 3_english_only_pdf.py
│   ├── 4_side_by_side_pdf.py
│   ├── translation_prompt.txt
│   └── vlm_ocr_prompt.txt
├── scripts/
│   └── benchmark_backends.sh
├── setup/
│   ├── setup_inference_backend.sh   # THE consolidated script - run this on a fresh machine
│   └── archive/              # old Dockerfile .bak files from the debugging session
├── books/                    # source PDFs (read-only mount)
└── processed/                # pipeline output
```

`~/llm_models` (outside the project folder) holds the 3 GGUFs (~13GB):
Qwen2.5-7B-Instruct-Q6_K, Qwen2-VL-7B-Instruct-Q6_K, mmproj-Qwen2-VL-7B-Instruct-f16.

`~/OpenWebUI` has a standalone `docker-compose.yml` (v0.11.0, joins `ai-net`
network) but was never brought up / connected.

---

## 3. LESSONS LEARNED (technical gotchas worth remembering)

1. **`amdgpu` apt repo doesn't belong in the container.** It's kernel-driver
   packaging (host-only); adding it 404'd since its version numbering
   doesn't follow ROCm release numbers. Only `rocm/apt` is needed in-container.

2. **Vulkan's build toolchain is fragmented across independently-versioned
   Ubuntu packages** (`glslc`, `glslang-tools`, `spirv-tools`,
   `spirv-headers` are all separate, non-obviously-named packages from
   different upstream projects). Switched to LunarG's official SDK
   **tarball** (not apt — LunarG discontinued apt package updates after
   May 2025) for a coherent, tested-together bundle. Still needed
   `libvulkan-dev` from apt separately for the linkable loader library —
   the SDK tarball provides the shader compiler toolchain, not the
   platform-specific Vulkan loader.

3. **llama.cpp links its CLI tools against shared libraries**
   (`libllama-server-impl.so` etc.), not fully static binaries. Must copy
   these out of the build tree into backend-specific lib directories
   BEFORE deleting the build tree, or the binaries fail at runtime with
   "cannot open shared object file."

4. **`docker exec` does not inherit environment variables `export`ed
   inside another already-running process** (e.g., entrypoint.sh's own
   `LD_LIBRARY_PATH` export only covers processes IT spawns). Any
   diagnostic/benchmark script using `docker exec` must pass needed env
   vars explicitly via `docker exec -e VAR=val`.

5. **ROCm 7.14 preview (TheRock) installs via pip wheels, not apt** — a
   fundamentally different mechanism from the stable line. Confirmed
   working through the compiler-discovery layer (had to find the real ELF
   `amdclang++`, not the Python wrapper `hipcc` shim) but ultimately
   blocked by a missing `hip-lang-config.cmake` in the wheel distribution
   — the preview wheels appear to target running pre-built workloads, not
   compiling new HIP source. Parked, documented, not a dead end forever.

6. **`docker buildx`'s dedicated builder containers can accumulate a large
   stuck cache volume** that resists normal `docker system prune`. Fix:
   `docker buildx rm <builder-name>` (not the node name shown with a
   trailing container ID) tears down the builder and its state volume
   together properly.

7. **Complex multi-line terminal pastes (nested heredocs, quotes,
   backticks) get corrupted in transit.** Downloadable script files that
   get run with `bash script.sh` are far more reliable than "paste this
   into your terminal" for anything beyond a few lines.

8. **CMake's `FindVulkan.cmake` reads the `VULKAN_SDK` environment
   variable as a first-class search hint** — this is documented CMake
   behavior, not a guess, and is why the tarball approach (which sets
   exactly this variable) is more reliable than piecing together
   individually-guessed apt package names.

---

## 4. TO-DO MANIFEST FOR NEXT PHASE

### Pipeline fixes (the original, still-primary goal)
- [ ] Re-implement italic rendering for `<i>...</i>` spans in both PDF
      generators (requires parsing tagged spans and alternating
      times-roman/times-italic fonts within `insert_textbox`, or using
      PyMuPDF's `insert_htmlbox` if version supports it)
- [ ] Implement footnotes: when translation is difficult, add a footnote
      with the original untranslated term/phrase
- [ ] Add image/figure embedding to `3_english_only_pdf.py` (currently
      side-by-side does this via `show_pdf_page`, english-only doesn't) side-by-side doesn't actually need it because the image can just be viewed on the untranslated half. But if there are captions, those should be translated so need to consider how to present that on the page.
- [ ] Make cover pages consistent between english-only and side-by-side
      (side-by-side is missing model names)
- [ ] Check `insert_textbox` return value for overflow in both generators;
      decide on a fallback (auto-shrink font, continuation page, or at
      minimum a warning log) before it silently drops text
- [ ] Full-length book test (currently only 7-page sample validated)

### Infrastructure (smaller, can interleave with pipeline work)
- [ ] **Recreate the operational helper scripts** — a prior manual
      (`translation_pipeline_manual.md`, uploaded mid-session) reveals a
      `linux_scripts/` folder that existed before but was never carried
      into the new `docker/`/`pipeline/`/`scripts/` structure:
      `run_full_pipeline.sh`, `check_gpu_usage.sh`, `monitor_gpu.sh`,
      `diagnose_pages.sh`, `shutdown_docker.sh`, `startup_docker.sh`,
      `clean_rebuild.sh`, `compact_docker_storage.sh`, `full_cleanup.sh`.
      This is a real functional gap, not just missing docs - e.g. the
      disk-cleanup logic we hand-derived over several turns this session
      (buildx builder teardown, image pruning) was apparently already
      solved once before in `compact_docker_storage.sh`/`full_cleanup.sh`.
      Worth rewriting these for the new folder structure and confirmed
      LLAMA_BACKEND-switching workflow rather than starting from scratch.
- [ ] Bring up Open WebUI (`~/OpenWebUI`, already scripted) and connect it
      to the now-working llama-server endpoints (11434/11435) as
      OpenAI-compatible connections
- [ ] Decide and build serving setup for `gpt-oss-20b` (general chat +
      research, via Open WebUI's built-in web search) and
      `qwen2.5-coder-14b` (coding) — reusing the same llama-server
      binaries already built, not adding Ollama as a second runtime
- [ ] Revisit ROCm 7.14 periodically per docker/ROCM714_NOTES.md
- [ ] Consider pinning exact apt package versions in the Dockerfile for
      long-term reproducibility (discussed, not yet done — low priority
      since rebuilds are infrequent)

### Manual accuracy (found via the old translation_pipeline_manual.md upload)
- [ ] Update MANUAL_SECTION_I_OVERVIEW.md to reflect the pipeline's actual
      documented sophistication: adaptive image compression for VLM OCR
      (2200px down to 1400px escalating on failure), pre-screening pages
      >2MB, automatic VLM server restart after repeated failures,
      English-output validation on translations (checks for stray foreign
      words, retries up to 3x)
- [ ] The old manual is a good source for Section III ("how to use") once
      written — it already documents the day-to-day workflow in detail
      (just needs updating for the new folder structure and confirmed
      backend-switching mechanism via .env)
- [ ] Old manual pins ROCm 6.4.3 and explicitly warns against upgrading -
      that guidance is now superseded; make sure whoever reads both
      documents knows MANUAL_SECTION_II reflects current reality

---

## 5. HARDWARE / ENVIRONMENT REFERENCE

- AMD RX 9070 XT, 16GB VRAM (gfx1201, RDNA4)
- AMD Ryzen 7 5700X3D, 8 core / 16 thread
- 32GB system RAM
- 240GB drive (was down to 53GB free at one point due to accumulated Docker
  build cache from repeated rebuilds during debugging — resolved by tearing
  down a stuck `docker buildx` builder; keep an eye on this if doing many
  rebuilds in the pipeline-fix phase too)
