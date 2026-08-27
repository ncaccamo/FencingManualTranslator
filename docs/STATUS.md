# Status & Roadmap

Current state of the pipeline, what's next, and the non-obvious decisions
worth not re-litigating. Session-by-session narrative lives in `git log`.

---

## Current state (2026-08-27)

- **Runs end-to-end.** `Joseph-Renaud_Lescrime` (109 pp) has gone
  1→2→3→4 producing both PDFs, twice. Local GPU only (`BACKEND_MODE=local`,
  Vulkan), no cloud calls.
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
  `docs/design-backend-selection.md`.
- **Container:** comes up idle (no auto-started servers); the pipeline
  owns server lifecycle. Runs as the host UID.

## Open items (roughly prioritized)

1. **Translation quality** — braced command groups still flatten ~half the
   time; "PARRY OF TIERCE HIGH" word order; the Larousse sower-motto
   garble (really an OCR-side "ignore the logo" problem). Needs another
   `translation_prompt.txt` pass, possibly more few-shot.
2. **Footnotes for hard-to-translate terms** — not implemented. Needs a
   `translation_prompt.txt` flag for difficult terms + footnote layout in
   both PDF generators.
3. **Second full book** (Spinnewyn or LaMarche full) to shake out the
   figure/alignment code on different layouts.
4. **Remote mode against a real Windows llama-server** — only stub-tested
   so far (correct URL, bearer header, no local process). Windows-side
   steps are in `docs/design-backend-selection.md` §5.
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
- Figure detection where illustrations are margin line-drawings rather
  than framed plates.

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
- **Switching between local backends is `docker compose up -d`, never a
  rebuild** — both binaries are baked into the image. Only `rocm714`
  (not built) or a Dockerfile change needs a rebuild.
- **`backend.env` must have clean `KEY=value` lines** — no trailing inline
  comments (compose's `env_file` parser is inconsistent about them).
- **`docker-compose.yml` still has `restart: unless-stopped`**, which
  contradicts the `restart: "no"` invariant in `CLAUDE.md` and the design
  doc. It's now harmless (the entrypoint no longer starts servers, so
  nothing to bounce) but should be reconciled — either flip compose to
  `"no"` or update the invariant.

## Known doc staleness

- `setup/MANUAL_SECTION_I_OVERVIEW.md` predates this session: still says
  "Qwen2-VL-7B" (now olmOCR-2), "validated on a 7-page sample" (now a full
  book), "italics not implemented / English-only doesn't embed images"
  (both now done). Refresh when writing Section III.
- `setup/archive/translation_pipeline_manual.md` — old ROCm 6.x-era
  manual; reusable for structure/wording, environment details all stale.
- `setup/DEV_ENVIRONMENT_SETUP.md` — the maintainer's personal dev-box
  recreation notes, not part of the shipped manual.
