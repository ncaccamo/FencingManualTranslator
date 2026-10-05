# Status & Roadmap

Current state of the pipeline, what's next, and the non-obvious decisions
worth not re-litigating. Session-by-session narrative lives in `git log`.

---

## Current state

What the pipeline does today. (How far any particular corpus has got is
the operator's business and is not tracked here.)

- **Languages:** French and Italian, each with its own term glossary
  (`pipeline/translation_prompt.txt`, `translation_prompt_it.txt`),
  selected by the book's `meta.json` `language`. Italian was checked
  against published translations before use (`docs/evaluation-and-tuning.md`
  §2).
- **Term policy:** three categories (translated / footnoted / kept): manual
  I.4, step 2. An "explained" category with notes was tried and dropped
  (`docs/model-quirks.md` 2.8).
- **OCR:** olmOCR-2-7B-1025 via llama-server (:11435 locally) is the only
  path. A layout detector (Docling's `docling-layout-heron`, CPU) supplies
  running-head, figure and margin-note boxes (`intermediate/layout.json`).
- **Translation:** Qwen3.8-27B (i1-Q4_K_S, ~15.8 GB), chosen by the
  translation model selection test (`evals/translation_model_test.py`);
  llama-swap key `fmt-translate` remotely, :11434 locally. Too big for 16 GB
  of VRAM whole: part of its 64 layers on the GPU (46 locally,
  `TRANSLATION_GPU_LAYERS`; 42 on the shared Windows system, see
  `docs/remote-gpu.md`), ~40-100 s/page depending on page size. A page
  is never dropped; low-confidence output is flagged inline.
- **Page breaks:** a sentence split across a page break is joined onto one
  page before translation (`join_split_sentences()`, the shorter fragment
  moves), so it is translated whole. The review report flags any split left
  (tables across a break, mostly). A page-context alternative was tested
  and rejected: it leaked text between pages.
- **Page numbers:** shown only when corroborated by the sequence; a hidden
  number is confirmed by a second, margin-only read
  (`pipeline/read_folios.py`, `apply_folio_witness()`), which never hides
  or changes one. Misses and invented numbers left after that are fixed by
  override.
- **Scans:** two-page-spread photographs are cut into single pages first
  (`pipeline/split_spreads.py`, per-book `.split` corrections); single
  pages can be re-read (`pipeline/reread_pages.py`) or patched in from
  another copy (`pipeline/patch_scan.py`).
- **Review loop:** each run writes a review report (French and Italian
  checks); decisions go in `books/<stem>.overrides.toml` and are re-applied
  on every run; only pages whose source text changed are retranslated.
- **Cover page:** title (original, English, full title as printed), author,
  first-published year and the scan's edition, source and its terms, the
  models and the pipeline revision and date behind each page, and the
  translation's licence (CC BY-NC-SA 4.0).
- **PDFs:** side-by-side (scan left, translation right) is the primary
  output; English-only is deferred (open item 2). Both render through
  `pipeline/_render.py`: italics, drill/brace indentation, footnote blocks,
  margin notes. Details: `docs/pdf-layout.md`.
- **Backends:** `backend.env` switches local (`vulkan` | `rocm7` | `rocm714`)
  vs. remote (LAN llama-server / llama-swap, keys `fmt-ocr` /
  `fmt-translate`, old `bt-*` names kept as aliases). See
  `docs/remote-gpu.md`.
- **Outputs:** `processed/<stem>_translated/` holds the two PDFs and the
  review report; everything intermediate is in `intermediate/`.
- **Unattended runs:** step 2 saves progress after every page and resumes
  where it stopped; in remote mode a translation request that finds the GPU
  server unreachable waits for it (up to `SERVER_WAIT_HOURS`, default 24)
  and resends, instead of keeping the original text.
- **Regression net:** `python3 evals/test_rules.py`: frozen input → output
  tests, one per bug fixed. Run it after any pipeline change; a failure
  means a fix broke a different book.

## Open items

1. **Glossary page — undecided.** A one-page glossary at the end of each
   PDF listing the kept terms that occur in that book, with one-line
   definitions. Book-independent, unlike the dropped per-use notes
   (model-quirks 2.8).
2. **English-only PDF — deferred (2026-09-25).** Review has been
   side-by-side only; expect a backlog of layout problems. When it resumes,
   consider decoupling the English-only renderer from code shared with the
   side-by-side, so fixes to one stop regressing the other. Content
   difference: book titles in bibliographies are translated, which suits
   the side-by-side (the original is on the facing scan); English-only
   should keep them in the original language.
3. **Heading hierarchy — future work.** The renderer has a running head and
   one body size; a part / chapter / section title in the body reads as one
   more prose line. The fix is a per-book heading scheme with levels in
   `_render.py` (sizes and spacing per level). Supersedes the
   `fix_subheading_spacing()` stop-gap. A `TITLE:` field in the OCR prompt
   was tested and not adopted (2026-09-25, 20/25 pages right); the layout
   detector is the better signal (23/25).
4. **Running heads with OCR typos** ("FIORETTTO", "MASIEMLO") are kept as
   read; for now an override per book (`[headers]`). Seen in four books,
   enough to consider a rule that corrects a head one or two letters off
   the book's dominant one.
5. **Known translation soft spots** (see `docs/model-quirks.md`): braced
   command groups sometimes flatten; literary prose leaves a few
   `[low-confidence]` pages partly untranslated.
6. **Translation speed — options, not planned.** The current light GPU
   load suits unattended runs. If that changes: 8-bit context memory
   (`-ctk q8_0 -ctv q8_0`, ~3 more GPU layers, ~10-15% faster) or
   speculative decoding with a small draft model (often 1.5-2x). Either
   needs a model selection test run first.
7. **`rocm714` backend** — wired, not compiled; `docker/ROCM714_NOTES.md`.

## Not yet tested

- The local-mode launch of the 46-layer Qwen3.8 split under Linux (all
  Qwen3.8 runs so far were remote).
- NVIDIA GPUs (`docs/remote-gpu.md` §5 outlines both routes).
- How many mid-book plates the OCR model under-reports; sideways fold-out
  engravings on books without a `.rotate` file.
- Paragraph alignment on a patchy scan text layer: below `ALIGN_MIN_RATE`
  the side-by-side falls back to plain flow for the whole book.

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
- **Raw intermediates are always kept** (`intermediate/raw_ocr.txt`,
  `intermediate/extracted.txt`, `intermediate/translation.txt`) so you
  can iterate on a prompt without re-running the previous stage.
  `pipeline/reprocess_extracted.py` re-applies the parse/reconcile logic
  to a saved `raw_ocr.txt` without re-OCR.
- **olmOCR-2 fabricates page numbers** from stray marks (engraving plate
  numbers, stamps) and normalizes roman numerals.
  `reconcile_page_numbers()` only trusts a folio corroborated by ≥2
  consecutive pages; everything else is a bracketed sheet ordinal `[n]`.
  Never emits ≤ 0. **Adjacent-pages false agreement, fixed 2026-09-25:**
  two misreads that differ by exactly 1 (Gomard 298/299 → 208/209,
  Prévost 198/199 → 108/109, Spinnewyn invented 10/11) no longer vouch for
  each other when the pages around them agree; a repeated folio on a
  different-text page is dropped (a duplicate scan keeps it). Writeup:
  `docs/model-quirks.md` §1.2.
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
  `scripts/monitor_gpu.sh` reads `rocm-smi` from inside the container so a
  ROCm-free host still has GPU monitoring (or use host `nvtop`).
  (Decided 2026-08-27; `setup_inference_backend.sh`, which did a host
  ROCm install, was retired for `scripts/setup.sh`.) In `remote` mode the
  host needs no GPU at all — `scripts/setup.sh --remote` skips the GPU
  checks and `docker-compose.override.yml` `!reset`s the compose device
  mounts.

- **A skipped book is not processed.** `books/_skip/` holds books with a
  readily available English translation (they become gold pairs) and
  out-of-scope scans. Nothing reads from it; to work on a book again, move
  it back to `books/` (its overrides file travels with it).
- **One glossary per source language.** French in `translation_prompt.txt`,
  Italian in `translation_prompt_it.txt` (replaces the French term rules);
  the review report's term checks are per language too — the Italian list
  fired on French books (their own *volant*, Italian titles in
  bibliographies) until it was scoped.
- **Provenance is per page and never guessed.** `translated_from.json`
  records model, revision and date for each page; the cover summarises
  them. Pages from before the record existed got a date range only where
  the evidence supported it (model record + file dates), no revision.
- **Term categories have names in code, numbers only in docs.** The code
  says "footnoted" / "kept": the numbering changed twice
  (2026-09-30) and a number in an identifier would have gone wrong again.

