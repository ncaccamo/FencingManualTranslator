# Status & Roadmap

Current state of the pipeline, what's next, and the non-obvious decisions
worth not re-litigating. Session-by-session narrative lives in `git log`.

---

## Current state (2026-08-27)

- **Runs end-to-end.** Five full books through 1→2→3→4:
  `Joseph-Renaud_Lescrime` (109 pp), `Spinnewyn` (178 pp),
  `gomard_…1845` (386 pp — Google Books scan, 1845 text, printed
  marginalia), `LaMarche_TraitDeLEpee` (337 pp — clean scan, literary
  prose), `…Jacob_Jules_bd6t53440660` (331 pp — Gallica 68 ppi flat
  JPEG). All **0 dead pages** (1341 pp). Local GPU only
  (`BACKEND_MODE=local`, Vulkan), no cloud calls.
- **OCR:** olmOCR-2-7B-1025 via llama-server on :11435 is the default and
  only path. Tesseract path (`1_ocr_and_extract.py`) is kept for reference
  / a no-VLM fallback, not used.
- **Translation:** Qwen2.5-7B on :11434. Never drops a page; flags
  low-confidence output inline.
- **PDFs:** English-only (flowing column, figures in reading order) and
  side-by-side (scan left / translation right, paragraph-aligned where the
  scan has a text layer). Both preserve `<i>` italics, indentation,
  drill/brace layout via `pipeline/_render.py`.
- **Figures (English-only):** the VLM flags each page as `plate` / `inset`
  and reads its caption (`figures.json` from step 1, caption translated by
  step 2 with the full prompt). Step 3 embeds a plate whole (cropped to
  the profiler's ink band when it's portrait), an inset from the profiler
  band, with the English caption beneath. Catches line-drawing plates the
  old density-only path missed. Prototyped on Joseph-Renaud (31 plates)
  and Gomard 1845 (8) — the other 3 books still use the profiler-only
  path until re-OCR'd. Open: sideways fold-out engravings stay sideways
  (can't tell from page geometry); pure-plate pages waste ~½ a sheet
  (see #6); adjacent captions can run together.
- **Backend selection:** `backend.env` switches local (`vulkan` | `rocm7` |
  `rocm714`) vs. remote (LAN llama-server + bearer token). See
  `docs/remote-gpu.md`.
- **Container:** comes up idle (no auto-started servers); the pipeline
  owns server lifecycle. Runs as the host UID.

## Open items (roughly prioritized)

1. **Translation quality** — a prompt pass landed (compound terms
   `coup droit`/`dérobement`/`doublement`, all-caps `PARADES DE X` heading
   few-shots, Markdown ban + `2_translate.py` sanitizer, accented-caps in
   headings). The LaMarche/Jacob-Jules run surfaced + fixed **model
   meta-responses leaking in as page content** — near-blank pages got
   "Please provide the text you would like to translate", and "Here is the
   translation:" / "This text does not appear to be fencing…" preambles
   slipped past rule 6. `2_translate.py` now: skips near-blank source
   (`is_near_blank`), rejects whole-output refusals (`looks_like_refusal`),
   strips leading preambles (`strip_model_preamble` — regexes kept narrow
   so real page-initial prose like "In this case, … Example:" survives).
   Verified 0 leaks across all 5 books after re-run. Full catalogue of
   this and every other model quirk: `docs/model-quirks.md`. Still soft: braced
   command groups flatten ~half the time, occasional article/word-order
   slips, ToC / dotted-leader pages half-translate `coupé`/`battement`,
   and literary prose (LaMarche) leaves a few `[low-confidence]` pages
   substantially in French. The Larousse sower-motto garble is OCR-side.
1b. ~~Multi-model serving compose~~ — CLOSED, superseded by `LocalAIChat`.
   That's a separate product now (llama-swap on :11500 serving a chat +
   a coding model, one resident, Open WebUI dropdown drives the swap).
   This item predates the project split; BookTranslator serves only its
   two pipeline models (olmOCR-2, Qwen2.5-7B), sequentially, by design.
2. **Footnotes** — DONE (mechanism). `pipeline/footnote_glossary.txt` (16
   curated terms) → `2_translate.py::pull_footnotes` appends a
   `___FOOTNOTES___` block, keyed off the SOURCE text (the 7B model won't
   keep terms in French to anchor an inline marker), each term glossed
   ONCE per book on first occurrence. `_render.py` splits the block off;
   `3_` renders it end-of-section (small grey type + rule), `4_` reserves
   a bottom strip in the right column. Model `<fn>` path also supported
   but rarely used. TODO: expand the glossary as more books surface terms;
   the notes have no inline reference mark (glossary path).
3. ~~More full books.~~ DONE — five now (see Current state). Each surfaced
   something: Spinnewyn → `\n---\n` separator collision + VLM server-death
   detection gap; Gomard 1845 → Google Books scan / printed marginalia
   (dropped) / line-drawing plates (figure detector caught 4 of ~20) /
   para-alignment 47%; repetition-loop fix held (10 runaway ToC pages → 0).
   LaMarche (clean scan, literary prose) → para-alignment 81% (best), but
   several `[low-confidence]` pages left mostly in French; model
   meta-response leak found + fixed. Jacob Jules (Gallica 68 ppi) → only
   27% corroborated folios (olmOCR can't read the low-res page numbers;
   bracketed sheet ordinals by design), para-alignment 34%.
4. **Remote mode against a real Windows llama-server** — only stub-tested
   so far (correct URL, bearer header, no local process). Windows-side
   steps are in `docs/remote-gpu.md`.
5. ~~**User Manual** Sections I and III.~~ DONE — Section I rewritten
   (current models, flowing PDF, footnotes-done, backend selection,
   limitations reconciled with this file); Section III written
   (`setup/MANUAL_SECTION_III_USAGE.md` — quick start, per-step re-run
   rules, iteration loops, `reprocess_extracted.py`, helper-script table,
   troubleshooting). Keep in sync as behaviour changes. Section II is
   current.
6. **English-only PDF** wastes vertical space over a run of consecutive
   figure pages (one figure per sheet) — could pack 2/sheet.
7. `rocm714` backend — wired everywhere but not compiled; build it in
   `BackendBuildstage` and sync in.
8. ~~Backend-selection port to `LocalAIChat`.~~ DONE + verified
   (2026-08-27). Grepped the repo: the one endpoint that matters,
   Open WebUI's `OWUI_OPENAI_BASE_URL`, is set by `scripts/start.sh` from
   `backend.env` (local default `http://model-server:11500/v1`, remote →
   `http://$REMOTE_HOST:$REMOTE_PORT/v1`); `LISTEN_PORT` / `REMOTE_PORT`
   are parameterised; the `127.0.0.1` binds are llama-swap's internal
   child servers (correct); the `:11500` setup probe is `local`-mode
   guarded. LocalAIChat's own STATUS "Verified" table also covers
   local/vulkan, local/rocm7, and remote against a real LAN stand-in.

## Not yet tested

- A physically photographed (not flatbed) book — all sources so far are
  clean library scans.
- Figure detection: the VLM path (`figures.json`) now handles full-page
  plates including line art; `find_figure_band()` still does the *geometry*
  for an inset crop and is the fallback for un-re-OCR'd books (misses
  faint half-tones / line art). Still unproven: how many mid-book plates
  the VLM under-reports (Gomard flagged only 8 — physical-book check
  pending), inset figures embedded in busy text pages, orientation of
  sideways fold-out engravings.
- Paragraph alignment against a sparse/patchy embedded text layer —
  Gomard's Google OCR gave 47% aligned vs. Spinnewyn's 71% on a cleaner
  Gallica layer. Below some threshold it should probably fall back to
  plain top-to-bottom flow rather than anchor on unreliable block boxes.
- Books with printed marginalia — olmOCR-2 silently drops the side-notes.
  Acceptable for now (main text stays clean); flagged in case a source
  carries substantive content in the margins.

## Non-obvious decisions / don't-retry-this

- **Page separator: now U+001E, was `\n---\n`.** All stages join / split
  the multi-page files (`raw_ocr.txt`, `extracted.txt`, `translation.txt`)
  on it. The translation model emits Markdown, and a lone `---`
  thematic-break line inside one page silently split 178 pages into 179
  (Spinnewyn run), misaligning every later page with its source.
  `pipeline/_pageio.py` now owns it: `PAGE_SEP = "\n\x1e\n"` (a
  non-printing control char that cannot occur in OCR text or model
  output). `split_pages()` still reads a legacy `\n---\n` file so existing
  `processed/` folders keep working without a re-run; a file is one format
  or the other, never mixed. The `2_translate.py` `^---+$` → `* * *`
  sanitiser and the `translation_prompt.txt` Markdown ban stay as
  defence-in-depth.

- **olmOCR-2, not Tesseract.** 8-page three-way eval (Gallica text layer /
  forced Tesseract / olmOCR-2). Forced Tesseract is unusable on any page
  with an in-text figure (dozens–hundreds of garbage tokens the cleanup
  can't remove) and misreads stains (`matière`→`2 latiére`). No
  Tesseract-first / per-page-fallback scheme.
- **The pipeline owns the llama-servers, not the container entrypoint.**
  An earlier entrypoint started both servers under `wait -n`; a `pkill`
  from any pipeline step then dropped PID 1 and bounced the whole
  container (hidden by `restart: unless-stopped`). Entrypoint now just
  checks models and `exec tail -f /dev/null`.
- **`docker exec` shells don't inherit the entrypoint's
  `LD_LIBRARY_PATH`.** Any new `Popen` starting a llama-server needs the
  `_llama_server_env()` handling already in `1_`/`2_` (restores
  `/usr/local/lib/llama-<backend>/`).
- **Raw intermediates are always kept** (`raw_ocr.txt`, `extracted.txt`,
  `translation.txt`) so you can iterate on a prompt without re-running the
  previous stage. `pipeline/reprocess_extracted.py` re-applies the
  parse/reconcile logic to a saved `raw_ocr.txt` without re-OCR.
- **olmOCR-2 fabricates page numbers** from stray marks (engraving plate
  numbers, stamps) and normalizes roman numerals.
  `reconcile_page_numbers()` only trusts a folio corroborated by ≥2
  consecutive pages; everything else is a bracketed sheet ordinal `[n]`.
  Never emits ≤ 0.
- **olmOCR-2 falls into repetition loops** on some table-of-contents
  (dot-leaders) and ornament (`* * *`) pages — thousands of tokens of one
  pattern, which then overflows the translation model's context. Mitigated
  three ways: `repeat_penalty: 1.12` on the VLM request, `collapse_runs()`
  clips pathological runs before parsing (also in `reprocess_extracted.py`),
  and `2_translate.py` caps oversized input at ~2500 words.
- **Switching between local backends is `docker compose up -d`, never a
  rebuild** — both binaries are baked into the image. Only `rocm714`
  (not built) or a Dockerfile change needs a rebuild.
- **`backend.env` must have clean `KEY=value` lines** — no trailing inline
  comments (compose's `env_file` parser is inconsistent about them).
- **`restart: "no"` everywhere** — a crash must stay visible, not be
  papered over by an auto-restart. `unless-stopped` previously hid a
  PID-1 container-bounce bug. (Set 2026-08-27; matches the CLAUDE.md
  invariant.)
- **The host installs no ROCm / Vulkan / Mesa** — only the in-kernel
  `amdgpu` driver (kernel ≥ 6.12) + Docker. All GPU userspace is compiled
  into the image; `docker exec` inference never reads host `/opt/rocm`.
  Same model across BookTranslator / LocalAIChat / BackendBuildstage.
  `scripts/monitor_gpu.sh` reads `rocm-smi` from inside the container so a
  ROCm-free host still has GPU monitoring (or use host `nvtop`).
  (Decided 2026-08-27; `setup_inference_backend.sh`, which did a host
  ROCm install, was retired for `scripts/setup.sh`.)

## Doc set

- `README.md` — index / quick start.
- `docs/STATUS.md` (this) — current state, open items, decisions.
- `CLAUDE.md` — AI-agent orientation + hard invariants.
- `setup/MANUAL_SECTION_I/II/III` — operator manual: overview / host
  setup / day-to-day operation. All three current as of 2026-08-27.
- `docs/model-quirks.md` — field guide to every OCR / translation model
  failure mode + the prompt/code mitigation. The "why" behind
  `vlm_ocr_prompt.txt` / `translation_prompt.txt` and the `2_translate.py`
  guards.
- `docs/pdf-layout.md` — how steps 3 & 4 render the PDFs: the `_render`
  text engine, figure placement (`figures.json` + profiler), paragraph
  alignment, and every layout knob.
- `docs/remote-gpu.md` — remote-GPU mode + `rocm714` (both experimental).
- `docker/ROCM714_NOTES.md` — why ROCm 7.14 is parked.
- `setup/DEV_ENVIRONMENT_SETUP.md` — maintainer dev-box setup (VS Code,
  git, Claude Code); not for end users.
- Retired 2026-08-27: `HANDOFF.md` (→ git log), `MANIFEST.md` (job done),
  `docs/design-backend-selection.md` (→ `docs/remote-gpu.md`),
  `setup/setup_inference_backend.sh` (installed host ROCm + inline-generated
  a stale Dockerfile; → `scripts/setup.sh`, verify-and-build only, host
  needs no ROCm/Vulkan). `setup/archive/` (10 `.bak` Dockerfiles + 12
  superseded numbered scripts + the ROCm 6.x
  `translation_pipeline_manual.md` — all in git history; Manual I/II/III
  supersede the old manual), and `setup/03_setup_open_webui.sh`
  (Open WebUI belongs to `LocalAIChat`, never connected here).
