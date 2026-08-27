# Status & Roadmap

Current state of the pipeline, what's next, and the non-obvious decisions
worth not re-litigating. Session-by-session narrative lives in `git log`.

---

## Current state (2026-08-27)

- **Runs end-to-end.** Three full books through 1→2→3→4:
  `Joseph-Renaud_Lescrime` (109 pp), `Spinnewyn` (178 pp),
  `gomard_…1845` (386 pp — Google Books scan, 1845 text, printed
  marginalia). All **0 dead pages** (673 pp, 3 low-confidence, all kept).
  Local GPU only (`BACKEND_MODE=local`, Vulkan), no cloud calls.
- **OCR:** olmOCR-2-7B-1025 via llama-server on :11435 is the default and
  only path. Tesseract path (`1_ocr_and_extract.py`) is kept for reference
  / a no-VLM fallback, not used.
- **Translation:** Qwen2.5-7B on :11434. Never drops a page; flags
  low-confidence output inline.
- **PDFs:** English-only (flowing column, inline figures) and side-by-side
  (scan left / translation right, paragraph-aligned where the scan has a
  text layer). Both preserve `<i>` italics, indentation, drill/brace
  layout via `pipeline/_render.py`.
- **Backend selection:** `backend.env` switches local (`vulkan` | `rocm7` |
  `rocm714`) vs. remote (LAN llama-server + bearer token). See
  `docs/remote-gpu.md`.
- **Container:** comes up idle (no auto-started servers); the pipeline
  owns server lifecycle. Runs as the host UID.

## Open items (roughly prioritized)

1. **Translation quality** — a prompt pass landed (compound terms
   `coup droit`/`dérobement`/`doublement`, all-caps `PARADES DE X` heading
   few-shots, Markdown ban + `2_translate.py` sanitizer, accented-caps in
   headings). Still soft: braced command groups flatten ~half the time,
   occasional article/word-order slips, ToC / dotted-leader pages
   half-translate `coupé`/`battement`. The Larousse sower-motto garble is
   OCR-side (ignore the logo), not translation.
1b. **Multi-model serving compose** (a general-chat + a coding model,
   reusing the `llama-server-{rocm,vulkan}` binaries rather than adding
   Ollama). Was tracked in the retired MANIFEST; not started.
2. **Footnotes** — DONE (mechanism). `pipeline/footnote_glossary.txt` (16
   curated terms) → `2_translate.py::pull_footnotes` appends a
   `___FOOTNOTES___` block, keyed off the SOURCE text (the 7B model won't
   keep terms in French to anchor an inline marker), each term glossed
   ONCE per book on first occurrence. `_render.py` splits the block off;
   `3_` renders it end-of-section (small grey type + rule), `4_` reserves
   a bottom strip in the right column. Model `<fn>` path also supported
   but rarely used. TODO: expand the glossary as more books surface terms;
   the notes have no inline reference mark (glossary path).
3. ~~Second / third full book.~~ DONE — Spinnewyn (178 pp) surfaced + fixed
   the `\n---\n` separator collision and the VLM server-death detection gap
   (see git log); 118/178 corroborated folios, 127/178 para-aligned.
   Gomard 1845 (386 pp) validated a harder class: Google Books scan, 1845
   type, printed marginal side-notes (olmOCR drops the marginalia, keeps
   the main text clean), ~20 line-drawing plates. Repetition-loop fix held
   (10 runaway ToC pages → 0). Weak spots on this class: figure detector
   caught 4 of ~20 plates (tuned for framed photo plates, not line
   drawings), para-alignment only 47% (Google's OCR text layer is patchier
   than Gallica's).
4. **Remote mode against a real Windows llama-server** — only stub-tested
   so far (correct URL, bearer header, no local process). Windows-side
   steps are in `docs/remote-gpu.md`.
5. **User Manual** Sections I (refresh — see Known staleness) and III
   (write — day-to-day usage). Section II is current.
6. **English-only PDF** wastes vertical space over a run of consecutive
   figure pages (one figure per sheet) — could pack 2/sheet.
7. `rocm714` backend — wired everywhere but not compiled; build it in
   `BackendBuildstage` and sync in.
8. Finish the backend-selection port to `LocalAIChat` (it already has
   `backend.env` + both services on `restart: "no"`; confirm its
   model-calling code reads URLs from a resolver, not hardcoded).
9. Open WebUI standalone script exists but was never connected.

## Not yet tested

- A physically photographed (not flatbed) book — all sources so far are
  clean library scans.
- Figure detection on line-drawing illustrations (margin *or* full-page
  framed): `find_figure_band()` is tuned for framed photo plates by row
  ink-density and misses faint half-tones and line art. Gomard: 4 of ~20
  plates caught.
- Paragraph alignment against a sparse/patchy embedded text layer —
  Gomard's Google OCR gave 47% aligned vs. Spinnewyn's 71% on a cleaner
  Gallica layer. Below some threshold it should probably fall back to
  plain top-to-bottom flow rather than anchor on unreliable block boxes.
- Books with printed marginalia — olmOCR-2 silently drops the side-notes.
  Acceptable for now (main text stays clean); flagged in case a source
  carries substantive content in the margins.

## Non-obvious decisions / don't-retry-this

- **The `\n---\n` page separator is content-fragile.** Every stage joins /
  splits pages on it. The translation model emits Markdown, and a lone
  `---` thematic-break line inside one page silently split 178 pages into
  179 (Spinnewyn run), misaligning every page after it with its source.
  `2_translate.py` now rewrites `^---+$` → `* * *` and strips `![](...)`
  before joining, and `translation_prompt.txt` forbids Markdown. `1_`'s
  `extracted.txt` has the same theoretical exposure (OCR is told to use
  `* * *`, lower risk). A real fix would be a separator that can't occur
  in content (form feed, or a long sentinel) — deferred.

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

## Doc set

- `README.md` — index / quick start.
- `docs/STATUS.md` (this) — current state, open items, decisions.
- `CLAUDE.md` — AI-agent orientation + hard invariants.
- `setup/MANUAL_SECTION_I/II` — operator manual. **Section III (day-to-day
  usage) not written; Section I's model names were refreshed but a fuller
  pass is due when III is done.**
- `docs/remote-gpu.md` — remote-GPU mode + `rocm714` (both experimental).
- `docker/ROCM714_NOTES.md` — why ROCm 7.14 is parked.
- `setup/DEV_ENVIRONMENT_SETUP.md` — maintainer dev-box setup (VS Code,
  git, Claude Code); not for end users.
- `setup/archive/translation_pipeline_manual.md` — old ROCm 6.x manual,
  reference only, environment details stale.
- Retired 2026-08-27: `HANDOFF.md` (→ git log), `MANIFEST.md` (job done;
  `setup/setup_inference_backend.sh` + Manual II superseded it),
  `docs/design-backend-selection.md` (→ `docs/remote-gpu.md`).
