# BookTranslator — Handoff / Offramp Document

## SESSION 2 (2026-08-26, Claude Code) — read this first
Big session. In rough order (details in the dated sections below):
1. OCR-quality bugs fixed in the Tesseract path (7-page sample).
2. VLM OCR path swapped Qwen2-VL-7B → olmOCR-2-7B-1025; `1_ocr_and_extract_vlm.py`
   output contract fixed (canonical scaffold, `parse_vlm_response()`,
   `reconcile_page_numbers()`, `raw_ocr.txt`).
3. Hard-page eval (8 pages, 3 books) → **olmOCR-2 is the default OCR path**,
   no Tesseract fallback.
4. **Server lifecycle re-architected** — entrypoint no longer starts the
   llama-servers (was bouncing the container); the pipeline owns them.
   New `pipeline/start_servers.sh` for interactive use. **Image rebuild.**
5. **Container runs as the host user** (`user:` in compose) — `./processed`
   files are no longer root-owned. **Image rebuild.**
6. `2_translate.py` hardened — never drops a page, `<i>`-aware English
   check, short-page length exemption, CJK-garble guard.
7. **Steps 3 & 4 rewritten** around `pipeline/_render.py` (fitz.TextWriter
   layout: line breaks, indentation, `<i>` italics, overflow-safe flow).
   English-only: flowing column + inline figures. Side-by-side:
   paragraph alignment.
8. `translation_prompt.txt` rewritten — term policy (foil/saber/parry,
   épée kept) + few-shot.
9. **Full book run**: `Joseph-Renaud_Lescrime` (109 pp) end-to-end,
   twice (before and after the prompt rewrite). Deliverables in
   `processed/Joseph-Renaud_Lescrime_translated/`.
10. New helper: `pipeline/reprocess_extracted.py` (re-run parse/reconcile
    from a saved `raw_ocr.txt`, no re-OCR).

Prior handoff content (session 1 infra: ROCm 7.2.4 + Vulkan dual build,
git/GitHub setup, move to Claude Code) is in git history.

---

## 1. CURRENT STATUS

**Infrastructure:** ROCm 7.2.4 + Vulkan dual backend on the RX 9070 XT
(gfx1201). Container comes up **idle** — no auto-started servers; the
pipeline (or `pipeline/start_servers.sh`) starts `llama-server` on demand.
Container runs as UID 1000. `LLAMA_BACKEND=vulkan` (in `.env`); switching
to `rocm` at runtime needs root or a rebuild (symlinks baked at build).
The Dockerfile was rebuilt twice this session — a fresh checkout needs
`docker compose build` before first run, then
`sudo chown -R "$(id -u):$(id -g)" processed/` once.

**Pipeline: substantially improved this session — see dated sections.**

### `1_ocr_and_extract.py` (Tesseract path) — rewritten, validated on a real 7-page sample
Confirmed via direct testing (not just code review):
- Raw OCR is now preserved permanently as `raw_ocr.txt` before any
  cleanup runs (previously NOT saved at all — a real gap against the
  project's own "keep raw OCR" requirement).
- Per-book OCR language via optional `<stem>.lang` sidecar file next to
  the source PDF (default `fra+lat+eng`) instead of always OCRing
  against 6 languages at once, which measurably hurts accuracy.
- Per-book crop via optional `<stem>.crop` sidecar (`left,top,right,bottom`
  fractions) — confirmed this fixes garbage tokens from facing-page/
  binding bleed in photographed scans.
- Forces Tesseract `--tesseract-pagesegmode 3` — confirmed dramatically
  cleaner than leaving PSM unset or forcing PSM 6.
- Removed `--oversample` by default — confirmed it was blowing up images
  5-6x for zero quality gain on scans where the PDF's page size is set
  from pixel count instead of true DPI (common in library digitizations).
- **Found and fixed a real corruption bug**: two "aggressive cleanup"
  regexes were silently destroying correctly-OCR'd French text —
  `"vous ébranler"` → `"vousébranler"`, systematically, on any word
  starting with é/è/ê/à following another word. Deleted outright, not
  just gated. `AGGRESSIVE_CLEANUP` now defaults off.
- Real ligature-replacement bug fixed (old code's fi/fl/ff replacements
  were accidental no-ops — source and target strings were identical).
- Validated end-to-end on a real page (from "Le jeu de l'épée") against
  visually-confirmed ground truth: near-perfect output.

### VLM OCR path — model swapped Qwen2-VL-7B-Instruct → olmOCR-2-7B-1025
- olmOCR-2 (Allen AI, fine-tuned from Qwen2.5-VL-7B specifically for
  document OCR, not general vision chat) is a strict upgrade for this
  task at the same VRAM class. GGUF via `bartowski/allenai_olmOCR-2-7B-1025-GGUF`.
- `1_ocr_and_extract_vlm.py` rewritten: fixed 1288px resize (documented
  olmOCR-2 requirement, replaces the old two-tier compression logic),
  `_find_matching_mmproj()` picks the correct mmproj by filename-token
  overlap rather than naive alphabetical-first (confirmed alphabetical
  sort was silently wrong: `mmproj-Qwen2-VL...` sorts before
  `mmproj-allenai_olmOCR...` in plain ASCII, nothing to do with which
  model is actually loaded).
- `vlm_ocr_prompt.txt` rewritten with explicit anti-hallucination
  instructions (no paraphrase/modernize/silent-skip) and an instruction
  to ignore facing-page/binding/margin clutter directly, rather than
  relying only on a physical crop.
- `test_vlm_single_page.py` fixed — was hardcoded to port 11434 (the
  TRANSLATION model's port, not the VLM's 11435) and used a different
  ad-hoc prompt than production. Now matches the real pipeline's image
  prep and prompt exactly.
- **`docker-compose.yml` fixed**: `VLM_MODEL_PATH`/`VLM_MMPROJ_PATH` were
  hardcoded to the old Qwen2-VL paths in the `environment:` block, which
  `entrypoint.sh` reads to auto-start the VLM server on container boot.
  This is now pointed at the olmOCR-2 files, so a normal
  `docker compose up -d` brings up the correct model with no manual
  intervention.
- **Validated on a real page**: ran against an actual library page (page
  124 of a different book than the 7-page sample), compared byte-for-byte
  against the visible source image — perfect match, 118/118 words
  identical, including correctly reassembling hyphenated line-wrapped
  words (`troi-\nsième` → `troisième`) and correctly ignoring bleed-through
  text from the reverse side of the thin paper without being told about
  this specific artifact.
- **Output contract fixed this session** (`1_ocr_and_extract_vlm.py`):
  - The raw `PAGE_NUMBER:/HEADER:/BODY_TEXT:` scaffold used to be written
    verbatim to `extracted.txt` and passed straight downstream (so a
    hallucinated `PAGE_NUMBER: 57` became a line handed to the translator
    and printed on the side-by-side PDF). Now `parse_vlm_response()` splits
    the fields and `extracted.txt` is a *rebuilt canonical* scaffold —
    downstream regexes in `2_translate.py` / `3_` / `4_` are unchanged and
    still match.
  - `reconcile_page_numbers()` sequence-checks the reported folio against
    the book's dominant page-index→folio offset; keeps it only if ≥2 pages
    corroborate, else emits inferred `[n]`. Kills the plate-number /
    half-title / blank-page fabrications. Roman numerals preserved.
  - `raw_ocr.txt` now written (untouched model responses) + `page_numbers.txt`
    — parity with the Tesseract path's permanent-raw-OCR requirement.
  - `vlm_ocr_prompt.txt`: added "don't add accents to bare display
    capitals" and "ignore publisher's-device motto lettering"; tightened
    the `PAGE_NUMBER` instruction (no numbers from plates/stamps; keep
    roman numerals as-is).

### Hard-page eval — DONE, and default decided
8-page three-way comparison (Gallica embedded text layer vs. forced
Tesseract PSM 3 vs. olmOCR-2), across 3 new sample books, covering:
in-text engravings, mid-page woodcut + caption, brace-delimited command
lists, a stratified title page, a foxing-damaged page, a footnote +
`1°/2°/3°` list, a small-type observation block, and a blank verso.
Raw outputs: `processed/_hardpage_eval/<label>/{gallica,tesseract,olmocr}.txt`
+ `page.png` (dir is gitignored under `processed/`).

- **olmOCR-2 won or tied all 8**, decisively on every hard-layout page.
  Forced Tesseract is *unusable* on any page with an in-text figure
  (dozens–hundreds of garbage tokens the cleanup stage can't remove) and
  also misreads stains (`matière`→`2 latiére`, `SALLES`→`SAELES`) and
  terminal italic `z` (`Rompez`→`Rompex`). Gallica's layer drops 30–45%
  of non-body content (display titles, captions, imprint blocks).
- **DECISION: the VLM/olmOCR-2 path is the default OCR path** (it already
  is what `scripts/run_full_pipeline.sh` calls). No Tesseract-first /
  per-page-fallback scheme — the failure modes above make that a net
  negative for these sources.
- olmOCR-2's one consistent weak spot: it fabricates a `PAGE_NUMBER` on
  pages with no printed folio (read `57` off an engraving plate, `1` on a
  half-title, `10` on a blank page). Mitigated in `1_ocr_and_extract_vlm.py`
  (see below).

### Server lifecycle — RE-ARCHITECTED (pipeline now owns the llama-servers)
Found running steps 1→2 for real: the container entrypoint auto-started
both llama-servers under `wait -n`, so step 2's `pkill llama-server.*11434`
dropped PID 1 and **bounced the whole container** mid-run (`restart:
unless-stopped` hid it). Also: a server spawned by a pipeline script via
`docker exec` died instantly with `libllama-server-impl.so: cannot open
shared object file` — the entrypoint puts `/usr/local/lib/llama-<backend>/`
on `LD_LIBRARY_PATH` for its own servers but a `docker exec` shell never
gets that.

Decision (chosen): **the pipeline owns server lifecycle.**
- `docker/Dockerfile` entrypoint: no longer starts llama-servers, no
  `wait -n`; keeps the model-download checks, then `exec tail -f /dev/null`.
  Container comes up idle. **Image rebuild required.**
- `1_ocr_and_extract_vlm.py` / `2_translate.py`: new `_llama_server_env()`
  restores the backend lib dir on `LD_LIBRARY_PATH` for the `Popen`; step 1
  now `pkill`s port 11435 before starting (it had been silently
  piggybacking on the entrypoint's server) and logs to `/tmp/vlm_server.log`
  instead of `DEVNULL`.
- `pipeline/start_servers.sh` (NEW): brings both servers up manually for
  interactive / Open WebUI use —
  `docker exec booktranslator-translator-1 /host_scripts/start_servers.sh`.
- Verified: full step 1 → step 2 on the LaMarche sample, models load/unload
  per step, no container bounce.

### Container now runs as the host user (fixes root-owned `processed/` files)
The container had no `user:` — everything it wrote into `./processed` came
out `root:root`, so the host user couldn't edit/delete outputs.
- `docker-compose.yml`: `user: "${HOST_UID:-1000}:${HOST_GID:-1000}"`;
  `.env` gained `HOST_UID` / `HOST_GID`. GPU still works — the host user is
  already in `video`(44) / `render`(992), carried in via `group_add`, and
  `/dev/kfd` + `/dev/dri/renderD128` are group `render`.
- `docker/Dockerfile`: bakes the default `llama-*` → `*-vulkan` symlinks at
  build (a non-root entrypoint can't write `/usr/local/bin`); entrypoint's
  `ln -sf` re-point lines are now `|| true` (were fatal under `set -e`).
  Switching `LLAMA_BACKEND` to `rocm` at runtime now needs root or a
  rebuild. **Image rebuild required.**
- One-time fix for already-root-owned files (done here via the still-root
  container; on a fresh checkout use):
  `sudo chown -R "$(id -u):$(id -g)" processed/`
- `scripts/run_full_pipeline.sh` still has a `sudo chown processed` guard at
  the top — now a harmless no-op, left as a safety net.
- Verified: container runs as uid 1000, step 2 (GPU) still ~38 s/15 pages,
  new files land as the host user.

### `2_translate.py` — hardened (validated on the 15-page LaMarche sample)
Real run discarded 2 of 15 pages outright (`[Translation failed on page N]`):
- Page 8 (a plate caption: `PLANCHE I` + dimension numbers + `LA BONNE
  ÉPÉE`) — killed by the `< 0.6× source words` length check.
- Page 14 (a fencing-command drill) — the model kept the imperatives in
  French; `contains_too_much_non_english` (>20%) rejected it 3×. The prompt
  *tells* the model to keep technical terms in French, so the check and the
  prompt were in direct conflict.

Fixes:
- `contains_too_much_non_english`: strip `<i>…</i>` spans (preserved terms
  are correct output) before the ratio; threshold 0.2 → 0.35.
- Length check: skipped for sources < 25 words; ratio 0.6 → 0.5.
- `has_wrong_script()` (NEW): reject a candidate containing CJK / Cyrillic /
  Hebrew / Arabic — the model was occasionally emitting a Chinese note to
  itself mid-sentence and it passed every other check. Greek allowed.
- **Never drop a page**: on final failure keep the best non-garbled
  attempt as `[low-confidence translation — review]\n…`, else fall back to
  `[translation unavailable — original text follows]\n<source>`.
- Empty / no-body pages now logged (`— no body text, skipped`) instead of
  vanishing from the console.
- Re-run: 15/15 pages produced, no drops, ~38s (was ~93s with retry loops).

Still open at the translation-quality (model/prompt) level, not plumbing:
inconsistent handling of imperative commands (`Rompez` → "Break" on one
page, left as-is on another), occasional over-`<i>`-tagging of ordinary
words, numbers dropped on sparse plate pages, run-to-run nondeterminism
(temp 0.3). Needs a `translation_prompt.txt` pass, possibly few-shot.

### Steps 3 & 4 (PDF generation) — REWRITTEN around a shared layout engine
`pipeline/_render.py` (NEW): lays translated text into a rect with
`fitz.TextWriter`, preserving line breaks, blank-line paragraph gaps,
leading-space indentation and `<i>…</i>` italics, wrapping inside the
indent, and returning leftover lines so callers flow instead of truncating.
(First cut summed `get_text_length()` per span for the x-cursor and accented
words collided — `DEUXIÈMEREPRISE`, `Coupémust`; TextWriter fixed it.)

- `3_english_only_pdf.py`: dropped the fixed 356 pt / 2-slots-per-sheet
  grid. Now one flowing column; consecutive source pages pack onto a sheet
  (13 sample pages → 6 sheets). Each source page gets a bold `— <n> —
  <header>` label + rule, then its text flows, breaking to a new sheet when
  full. Overflow is now impossible. `[Empty page]` markers skipped.
- `4_side_by_side_pdf.py`: right column flows through `_render`; picks the
  largest of 9/8/7 pt that fits; if even 7 pt overflows it adds "(cont.)"
  landscape pages (original re-shown left) — text is never dropped. `<i>`
  now renders italic. Blank/empty pages render the scan + a note.
- Both: shared cover style, correct model names (was a stale
  `Vision Model: Qwen2-VL-7B-Instruct` line in `3_`).
- Validated: LaMarche sample → both PDFs; drill pages (folios 82-83) now
  show one command per line with block separation instead of a wall of
  prose; side-by-side folio 83 fits with no continuation.

### Structured drill / command tables — partly addressed
Prompt rules added: `vlm_ocr_prompt.txt` "COMMAND LISTS AND DRILLS" (one
command per line, keep indentation, normalize a `{` brace to aligned
indented lines, ornament separators → `* * *`); `translation_prompt.txt`
rule 6 (keep that line structure, do not merge braced alternatives into a
sentence). Render side (above) now respects whatever structure arrives.
Model compliance is partial: on LaMarche folio 82 olmOCR still flattened
the block indentation (blank-line separation survived); the translator
kept `Doublez { dessus/dedans/dessous` stacked on folio 84 but flattened
it to a comma list on folio 83. Needs few-shot or a post-process to be
reliable.

### Full-length book — RUN end-to-end (Joseph-Renaud_Lescrime, 109 pp)
First real whole-book run, steps 1→2→3→4 chained. ~25 min wall
(16 OCR / 9 translate / seconds PDF). **Stable**: no crashes, no container
bounce, container ran as the non-root host user, all 7 artifacts written,
**zero pages dropped**. 5 pages skipped as empty (blank/plate), 5 flagged
`[low-confidence — review]` (kept, not lost) — all heading-only pages the
translator left in French + over-`<i>`-tagged (e.g. `QUATRIÈME MOUVEMENT
DE LA MISE EN GARDE` → untranslated). PDFs: 36 sheets (english-only,
flow-packed from 104 non-empty pages) / 111 (side-by-side, 1 continuation).

- **Bug found + fixed**: `reconcile_page_numbers()` extrapolated
  `[i + mode_offset]` for uncorroborated pages, which went **negative** for
  ~28 front-matter pages (offset was −27) — visible as `· [-19] ·` labels
  in the PDF. Now: corroborated folios kept as-is; pages *after* the first
  anchor extrapolate only while ≥ 1; everything else (front matter,
  would-be ≤ 0) falls back to the sheet ordinal `[n]`. Never negative.
- Only 11 / 109 folios corroborated on this book — olmOCR's page-number
  reads are sparse/unreliable here, so most english-only labels are
  bracketed sheet ordinals. Cosmetic for english-only, irrelevant for
  side-by-side (scan is right there). Could push harder in the prompt
  later; not worth it now.
- `pipeline/reprocess_extracted.py` (NEW): rebuilds `extracted.txt` +
  `page_numbers.txt` from a saved `raw_ocr.txt` using the current
  parse/reconcile logic — re-apply parser fixes to an already-OCR'd book
  without paying the OCR cost again. Used it to fix this book's numbers
  without re-running step 1.

### Round of polish after the full-book run (A/B/C)
**A — `translation_prompt.txt` rewritten.** Was 5 terse rules; now has a
translate-these / keep-these term policy + 4 worked examples. Fixes from
the JR run: `fleuret`→foil, `sabre`→saber, `épée` kept+`<i>`-tagged;
`parade`→parry (never "riposte"); headings / all-caps lines are normal
text (translate, no tag); `<i>` only for a named closed set, not ordinary
vocabulary; keep every numeric callout; drop ornament/motto text.
Validated on the 10 worst JR pages: all 5 ex-`[low-confidence]` pages now
translate cleanly (`QUATRIÈME MOUVEMENT…` → `FOURTH MOVEMENT OF THE
ON-GUARD POSITION.`), no regressions on good pages, and it recovered
sentences the old run had dropped.

Final JR re-run with all of A/B/C: 109 pp, **0 low-confidence flags**
(was 5), `foil`×62 / `parry`×119 / `saber`×42 / `épée` kept ×68, `<i>`
tags down to 64 (was over-tagging). English-only 56 sheets / 6.6 MB
(figures embedded as JPEG @150 dpi q72 — PNG @200 dpi had made it 42 MB),
side-by-side 110 sheets, 62 para-aligned.

**B — figures in the english-only PDF (`3_english_only_pdf.py`).**
`find_figure_band()` row-density-profiles each scan page (searching only
the 8–92% vertical interior, so scanner-bed black at the edges doesn't
count), finds a contiguous near-solid band that is tall or short-but-very-
dense and spans most of the width, crops it, and embeds it inline above
that page's translated text; a whole-page plate is embedded uncr­opped.
Label+figure are atomic (no orphaned label). JR: 40 figures found (cover
claims "48 GRAVURES" — it's a photo-heavy manual). Conservative: misses
faint half-tones and small decorative vignettes. Mixed pages get the band
above the text, which is close enough.

**C — paragraph alignment in the side-by-side (`4_side_by_side_pdf.py`).**
When the scan has an embedded text layer, `paragraph_fracs()` reads each
source paragraph's y-position and `render_aligned()` top-anchors each
translated paragraph at the same fraction down the column (resyncing at
every paragraph, so drift stays local). Only used when the whole page
still fits with that whitespace; else plain flow. No text layer → always
plain flow. JR: 62/109 pages aligned; reading across lands within ~1 cm.

### Still not tested
- A physically photographed (not flatbed) book — everything so far is
  clean library scans.
- Figure detection on a book whose illustrations are line-drawings in the
  text margin rather than framed plates.

### Still not done
- Footnotes for hard-to-translate terms — not implemented; needs a
  `translation_prompt.txt` flag for difficult terms plus footnote layout
  logic in both PDF generators.
- English-only PDF wastes vertical space across a run of consecutive
  figure pages (one figure per sheet). Could pack 2/sheet.
- User Manual Sections I (Overview) and III (How to use) — Section II
  (Host Prerequisites) is the only one drafted so far. Source material:
  `setup/archive/translation_pipeline_manual.md` is the old ROCm 6.x-era
  manual — reusable for structure / step-by-step wording, but its
  environment details are stale (now ROCm 7.2.4 + Vulkan dual backend,
  olmOCR-2 not Qwen2-VL, pipeline owns the llama-servers). (Not to be
  confused with `setup/DEV_ENVIRONMENT_SETUP.md`, which is the maintainer's
  personal dev-box recreation notes, not part of the shipped manual.)
- Open WebUI standalone setup script exists but was never connected /
  brought up.

---

## 2. TOOLING CHANGE THIS SESSION

Moved from claude.ai chat (with copy-paste terminal commands) to
Claude Code running directly in VS Code on this machine
(`nicholas-Ubuntu-AI`), specifically because most of the prior session's
time was lost to terminal paste corruption (heredocs getting mangled
mid-transit) and manual state-verification round-trips (`wc -l`/`grep -c`/
`diff` after every change to confirm a file actually landed correctly).
Claude Code has direct file and shell access, which removes both problems.

Project is now a git repo, pushed to GitHub (private). `.gitignore`
excludes `books/`, `processed/`, `*.gguf`, `.env`, `__pycache__/`.

---

## 3. PROJECT STRUCTURE

(unchanged — see MANIFEST.md and prior HANDOFF content in git history)

---

## 4. IMMEDIATE NEXT STEPS, IN ORDER

1. ~~Test olmOCR-2 on a hard page and compare against Tesseract.~~ DONE —
   see "Hard-page eval" above.
2. ~~Decide default OCR strategy.~~ DONE — VLM/olmOCR-2 path is the
   default; no Tesseract fallback.
3. ~~Run steps 3 & 4; full-length book end-to-end.~~ DONE — Joseph-Renaud
   (109 pp) through all 4 steps, see "Full-length book" above. Page-number
   negative-extrapolation bug fixed.
4. ~~`translation_prompt.txt` quality pass; english-only figures;
   side-by-side alignment.~~ DONE this session — see "Round of polish
   (A/B/C)" above. JR was re-translated + re-rendered with all three.
5. Remaining translation-quality items: braced groupings still flatten
   sometimes (drill rule lands ~half the time), "PARRY OF TIERCE HIGH"
   word order, the Larousse sower motto still garbles (OCR feeds garbage
   from the logo — really an OCR-side ignore). Footnotes for
   hard-to-translate terms still not implemented.
6. Second full book (Spinnewyn or LaMarche full) to shake out the A/B/C
   changes on different layouts / a book with a text layer everywhere.
7. User Manual Sections I and III.
8. (design, future session) Backend Selection & Remote GPU — see the
   appended spec below; largest concrete task there is de-hardcoding
   `localhost:11434/11435` in `1_`/`2_`/`test_vlm_single_page.py`.



# Backend Selection & Remote GPU — Design Handoff for Claude Code

Status: design only, nothing implemented yet. This document is the spec to
work from in a future Claude Code session. It assumes that session has
real file access to `BookTranslator`, `LocalAIChat`, and
`BackendBuildstage` — this doc describes target behavior and schema, not
line-level diffs, since it was written without seeing BookTranslator's
actual current Dockerfile/entrypoint/pipeline source.

---

## 1. Context

Today, GPU inference happens locally: both `BookTranslator` and
`LocalAIChat` build a self-contained Docker image with ROCm + Vulkan +
llama.cpp compiled in, and an env var (`LLAMA_BACKEND`) picks which
backend binary runs.

Two things are being added on top of that, decided in planning
conversation, not yet built:

1. **A second serving mode**: instead of running `llama-server` inside
   the Ubuntu box's own Docker container, point at `llama-server.exe`
   running natively on a separate Windows gaming PC over the LAN, using
   that machine's GPU via Vulkan. The gaming PC runs nothing else related
   to this project — no pipeline, no Docker, just that one process.
2. **A third local backend variant**: alongside Vulkan (current default)
   and ROCm 7.2.4 (stable, confirmed working), add ROCm 7.14+ once it's
   stable enough to un-park (see `docker/ROCM714_NOTES.md` — previously
   blocked on `hip-lang-config.cmake` not being shipped in the preview
   SDK).

**Explicit decision: local self-contained ROCm/Vulkan is being kept, not
replaced.** The goal is a single config file that switches between local
and remote, and between local backend variants, without a rebuild for
the local-variant switch and without requiring the Windows PC to be
involved at all unless remote mode is selected.

---

## 2. Decisions already made — do not re-litigate these

- `BookTranslator` and `LocalAIChat` remain fully independent products at
  the setup/deploy level (separate repos, separate Dockerfiles, no shared
  runtime or build dependency). See prior handoff docs for that
  reasoning. This backend-selection work must preserve that — the config
  schema below is duplicated per-project by convention, not shared via
  any file or image dependency.
- `BackendBuildstage` is a reference-only sandbox, never a build/runtime
  dependency of either product. It exists so a new backend can be
  compiled and smoke-tested in isolation before either product's
  Dockerfile is touched. Updates flow from there into each product by
  copy (currently via `sync_to_product.sh`, marker-based).
- Restart policy stays `"no"` everywhere; nothing in this design should
  reintroduce always-on servers or `restart: unless-stopped`.
- Remote mode does not require rebuilding either product's Docker image
  — it's a runtime config choice. Local backend variant switching (once
  more than one variant is actually built into the image) is also
  intended to be a runtime choice, not a rebuild.

---

## 3. Proposed config schema

One config file per product (`backend.env`, or fold into the existing
`.env` — Claude Code's call once it sees how each project currently
loads env vars), same schema in both:

```
# backend.env

BACKEND_MODE=local          # local | remote

# --- used only when BACKEND_MODE=local ---
LLAMA_BACKEND=vulkan        # vulkan | rocm7 | rocm714

# --- used only when BACKEND_MODE=remote ---
REMOTE_HOST=                # e.g. 192.168.1.50 — the Windows gaming PC's LAN address
REMOTE_PORT=11500
REMOTE_API_KEY=             # matches --api-key passed to llama-server.exe on the remote side
```

Notes:
- `LLAMA_BACKEND` already exists as a concept in the current compose
  setup (`LLAMA_BACKEND=${LLAMA_BACKEND:-vulkan}`) — extend it with the
  `rocm714` value rather than introducing a new variable name.
- `rocm714` is a valid config value from day one even though it isn't
  buildable yet — selecting it before the buildstage exists should fail
  fast with a clear error (see open questions, §6), not silently fall
  back to something else.
- Only one of the local/remote variable groups is meaningful at a time;
  the other group's values are ignored. Validate this at startup rather
  than trusting the person editing the file to leave the unused half
  blank.

---

## 4. Behavior required at each layer

**Entrypoint / server-lifecycle script** (`entrypoint.sh` /
`start_servers.sh` — whichever currently owns starting `llama-server`,
per the "pipeline owns server lifecycle" re-architecture already done in
`BookTranslator`):

- Read `BACKEND_MODE`.
- If `local`: map `LLAMA_BACKEND` to the corresponding pre-built binary
  path inside the image (e.g. `/usr/local/lib/llama-vulkan/llama-server`,
  `/usr/local/lib/llama-rocm7/llama-server`, and eventually
  `/usr/local/lib/llama-rocm714/llama-server`), start it, and set
  whatever internal variable the pipeline scripts read for the server URL
  to `http://localhost:<port>`.
- If `remote`: **do not start any local `llama-server` process.** Set the
  pipeline's server URL to `http://${REMOTE_HOST}:${REMOTE_PORT}` and
  configure the `Authorization: Bearer ${REMOTE_API_KEY}` header for
  outgoing requests.
- If `local` + `LLAMA_BACKEND=rocm714` but that binary doesn't exist in
  the image yet: fail immediately with a clear message pointing at
  `BackendBuildstage`, don't silently start a different backend.

**Pipeline scripts** (`1_ocr_and_extract_vlm.py`, `2_translate.py`, and
LocalAIChat's equivalent model-calling code):

- Confirm these already read the server URL/API key from environment
  rather than hardcoding `localhost:11434` / `localhost:11435`. If they
  don't yet, that's an implementation task here — this is exactly the
  kind of hardcoded-port assumption that would silently break remote
  mode.

**Docker Compose**: no new services needed for remote mode — the
model-server service simply isn't what's contacted; whether it still
gets built/started at all when `BACKEND_MODE=remote` (vs. skipped
entirely to save time) is an open question, §6.

---

## 5. Remote GPU (Windows gaming PC) setup

This is the non-Docker half — runs directly on the Windows machine, no
code from either product lives there.

1. Download a Windows Vulkan build of llama.cpp (prebuilt binaries are
   published upstream; Vulkan uses the GPU driver already installed for
   gaming — no ROCm install needed on the Windows side). AMD also
   publishes ROCm-validated Windows builds if more performance is wanted
   later, but that requires installing ROCm runtime components on
   Windows — Vulkan is the lower-friction starting point.
2. Run it bound to the LAN, with auth enabled:
   ```
   llama-server.exe -m your_model.gguf --host 0.0.0.0 --port 11500 --api-key <shared-secret>
   ```
3. Windows Firewall: allow inbound TCP on that port, scoped to the Ubuntu
   box's IP specifically rather than the whole LAN if easy to do.
4. Give the gaming PC a static IP or DHCP reservation so `REMOTE_HOST`
   in the config file doesn't go stale.
5. Start it manually before a translation/chat run, matching the
   on-demand pattern already used everywhere else in this project — no
   requirement to make this auto-start.

---

## 6. BackendBuildstage extensions needed

The sandbox repo currently assumes **one** canonical build stage with a
single `# BUILDSTAGE START` / `# BUILDSTAGE END` marker pair. That needs
to become **one per backend variant**, since a product image now needs
to contain all buildable variants simultaneously (per "install and ready
to go"):

- Split `Dockerfile.buildstage` into `Dockerfile.buildstage.vulkan`,
  `Dockerfile.buildstage.rocm7`, and (once un-parked)
  `Dockerfile.buildstage.rocm714`.
- Give each a distinct marker pair in the product Dockerfiles, e.g.
  `# BUILDSTAGE-VULKAN START/END`, `# BUILDSTAGE-ROCM7 START/END`,
  `# BUILDSTAGE-ROCM714 START/END`, so `sync_to_product.sh` can update
  one variant without touching the others.
- `sync_to_product.sh` needs a backend-name argument to know which
  marker pair to target.
- `test-build.sh` should be able to build and smoke-test each variant
  independently.
- `rocm714`'s buildstage file can exist now as a placeholder carrying
  forward the existing parked notes from `docker/ROCM714_NOTES.md`, to
  be filled in once AMD ships a build SDK (not just runtime wheels) for
  that version.

---

## 7. Open questions for Claude Code to resolve during implementation

- Where should `backend.env` actually live and how should it be loaded —
  `docker compose --env-file`, a mounted file read by a small Python
  config loader, or something else? Depends on how each project
  currently structures its env handling.
- Should selecting `BACKEND_MODE=remote` still build the local
  ROCm/Vulkan stages into the image (simpler, always "ready to go" per
  the stated preference) or skip building them to save image size/build
  time when the person knows they'll always run remote? Leaning toward
  "always build, since that's the explicit ask," but worth confirming
  before doing the (potentially slow) work of making it conditional.
- Exact validation behavior for invalid/incomplete config (e.g.
  `BACKEND_MODE=remote` with `REMOTE_HOST` blank) — fail at container
  start with a clear message, ideally before any model-load attempt.
- Whether `LocalAIChat`'s Dockerfile TODO block gets filled in from
  `BackendBuildstage` before or after this config work — probably
  before, so both products are on equal footing when this lands.

---

## 8. Suggested order of work

1. Seed `BackendBuildstage/Dockerfile.buildstage` from BookTranslator's
   current confirmed ROCm 7.2.4 + Vulkan stage (already-pending TODO from
   prior handoff), then split it into per-backend files as described in
   §6.
2. Fill in `LocalAIChat/docker/Dockerfile`'s TODO block via
   `sync_to_product.sh` once that split exists.
3. Implement the `backend.env` schema and entrypoint logic in
   `BookTranslator` first (it's the more mature codebase), verify all
   three states — local/vulkan, local/rocm7, remote — actually switch
   correctly with no rebuild between the local ones.
4. Port the same logic to `LocalAIChat`.
5. Add `rocm714` as a real option once it's un-parked, following the same
   sandbox-first workflow already established (build/test in
   `BackendBuildstage`, sync out, never re-derive from scratch).