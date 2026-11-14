# Tools

Every command in the repository, by what it is for. Each script's own
header (`python3 <script>` with no arguments, or its docstring) has the
details; this page is where to find the right one.

**Where it runs.** *Host* = on the system, from the repository folder
(`books/` is writable only there). *Container* = inside `fmt-translator`:
`docker exec fmt-translator python3 /app/<script>` for `pipeline/`,
`docker exec fmt-translator sh -c 'cd /data/evals && python3 <script>'`
for `evals/`. Every script that changes a book takes the books by name
or `--all`, and leaves a book marked `[book] final = true` alone unless
given `--unlock` (`pipeline/_books.py`).

## Running the pipeline

| Command | Runs | What it does |
|---|---|---|
| `scripts/queue.py` | host | The work queue: whole books, pages to retranslate, PDF rebuilds, captions, tables, run one after another, unattended. `add … [--front]`, `start`, `pause`, `stop`, `list`, `hold`/`release`. Refuses to build PDFs while `pipeline/` has uncommitted changes, so a cover's revision stamp is always true. **Use this instead of shell loops.** |
| `scripts/run_full_pipeline.sh` | host | Steps 1–4 and the review report on the named books, without the queue. |
| `pipeline/1_ocr_and_extract_vlm.py` | container | Step 1: OCR (olmOCR-2) and extraction → `raw_ocr.txt`, `extracted.txt`. |
| `pipeline/2_translate.py` | container | Step 2: translation → `translation.txt`; `--pages`, `--flagged`. |
| `pipeline/3_english_only_pdf.py` | container | Step 3: the English-only PDF ([pdf-layout.md](pdf-layout.md)). |
| `pipeline/4_side_by_side_pdf.py` | container | Step 4: the side-by-side PDF. |
| `pipeline/quality_report.py` | container | The review report: every page the pipeline has reason to doubt (low-confidence or cut-off pages, English much shorter than the French, fixes not found, names, tables …). **Read it after every run** — it is where a page that failed quietly shows up. |
| `pipeline/start_servers.sh` | container | One model server for use outside the pipeline (the pipeline starts its own). |

## Watching a run

| Command | Runs | What it does |
|---|---|---|
| `scripts/progress.sh` / `scripts/progress.py` | host | Live view: book, step, pages done/left, speed, ETA, and the inference backend (local GPU or remote, per `backend.env`). |
| `scripts/monitor_gpu.sh` | host | Live GPU use during a local run. |
| `scripts/check_gpu_usage.sh` | host | Quick check that the GPU is seen and answers one inference. |

## Corrections and review (per book)

The per-book files these write are described in [per-book-files.md](per-book-files.md).

| Command | Runs | What it does |
|---|---|---|
| `pipeline/apply_overrides.py` | container | Puts `books/<stem>.overrides.toml` (fixes, headers, captions, page settings) and hand edits into the book's outputs — no model. Then rebuild the PDFs. |
| `pipeline/edit_page.py` | host | Hand-edit a page's English (`books/<stem>.edits/`); the edit wins on every later run. |
| `pipeline/repoint_fixes.py` | host | A `[[fix]]` reported "not found" because a later fix or a case rule changed its result: sets its replace to the page's wording. Dry run unless `--write`; leaves a fix whose result is gone from the page for a person. |
| `scripts/repeated_fixes.py` | host | The corrections that keep being made: every `[[fix]]` reduced to the words it changes, counted across books (or the books you name). A change made many times is a candidate for a rule; you decide whether it is one for the language (`pipeline/glossary_*.toml`), for one book (`[terms]`, `[[book_fix]]`), or stays as fixes. Run after reviewing a book. |
| `pipeline/review_page.py` | container | The local review page: one card per review-report item, answered offline → `books/<stem>.review`. |
| `pipeline/apply_review.py` | host | Writes the review page's find / replace answers into the overrides file as `[[fix]]`. |
| `pipeline/decoration_sheet.py` | container | Offline page to keep or drop decorative pictures → `books/<stem>.pictures`. |
| `pipeline/review_sheet.py` | container | A checklist section or caption entries to fill in, as text. |
| `pipeline/verify_captions.py` | container | A second reading of figure captions (model), for the report. |
| `pipeline/read_folios.py` | container | A second look (model) at page numbers that had to be worked out. |
| `pipeline/table_from_scan.py` | container | Rebuilds a ruled table's rows from the scan's own text layer and printed column rules, where the OCR lost or shifted rows (no model). Each page is checked against the row count expected (`<scan>:<rows>`) and must fill every cell, or it is left alone; a page that fails is tried again with rules that drift (an askew scan), the table's height from its longest rule, and a printed «—» the text layer missed -- a page that rebuilds as it is never changes; `--write` replaces the table in `raw_ocr.txt` (backup kept), then reprocess and translate the tables. |
| `pipeline/reread_pages.py` | container | Re-read chosen pages with the OCR model and re-derive the book's text from them. |
| `pipeline/layout_scan.py` | container | The page-layout detector → `intermediate/layout.json` (pictures, columns, margin notes, running heads). |

## Diagnosing a page or a change

| Command | Runs | What it does |
|---|---|---|
| `pipeline/show_page.py` | container | Everything about one page: raw OCR, extracted fields, English, figures, layout boxes, its report items and decisions, plus pictures of the scan page and the side-by-side sheet (`processed/_audit/show/`). Start here for any flagged page. |
| `scripts/dry_run.py` | host | What a step-1 code change would do to every book, before reprocessing for real. |
| `evals/test_rules.py` | host and container | One frozen test per past fix. Run after every change; a failure means another book regressed. |
| `evals/test_glossary_table.py` | host and container | Tests for the term tables: they say what the prompt files say, source forms match printed forms, a book's own `[terms]` outrank a table check. Run after any change to a prompt file or a `glossary_*.toml`. |
| `evals/test_pdf_steps.py` | container | Steps 3 and 4 run for real on a three-page book made up on the spot (a temporary folder; no model, none of your books): both PDFs are written, every page's text is in them, a kept term is italic, a note is drawn. Run after a change to the Dockerfile's package versions, to `_render.py`, or to anything both steps share. |
| `evals/omission_check.py` | container | Where the English may have dropped or doubled text, paragraph by paragraph (finer than the report's whole-page length check). Changes nothing. |
| `evals/format_check.py` | container | How far the English layout is from the print: paragraph starts and gaps against the scan's text layer. |
| `evals/retranslation_compare.py` | host | After a full retranslation: the pages that got worse against a saved copy. |
| `pipeline/test_vlm_single_page.py` | container | OCR one page with the real prompt and image preparation. |

## Book files and the scan

| Command | Runs | What it does |
|---|---|---|
| `pipeline/patch_scan.py` | container | Replace pages of a scan with pages from another copy, keeping the work done on the rest. |
| `pipeline/split_spreads.py` | container | Split a scan of two-page spreads into one book page per scan page. |
| `pipeline/reprocess_extracted.py` | container | Rebuild `extracted.txt` from a saved `raw_ocr.txt` (no model). |
| `scripts/rename_book.py` | host | Rename a book's stem in every place it lives (books/, processed/, evals/), in step. `--dry-run`. |
| `scripts/make_book_meta.py` | host | `books/<stem>.meta.json` for the PDF cover, from `books/library.md`. |
| `scripts/library_holdings.py` | host | Rewrites the "Holdings" section of `books/library.md` (what you have, and where). |
| `scripts/release.py` | host | Copies a book's finished PDFs out under readable names, for sharing. |

## Setup and Docker

| Command | Runs | What it does |
|---|---|---|
| `scripts/install_hooks.sh` | host | Once per clone: the rule tests run before every commit, and a failure stops it (`scripts/git-hooks/pre-commit`; under a second, no container). |
| `scripts/setup.sh` | host | One-time setup: checks the host, builds the image (`--remote` for a host with no GPU). |
| `scripts/startup_docker.sh` / `scripts/shutdown_docker.sh` | host | Start Docker and the container / stop them to free RAM and VRAM. |
| `scripts/clean_rebuild.sh` | host | Rebuild this project's image from scratch. |
| `scripts/compact_docker_storage.sh` / `scripts/full_cleanup.sh` | host | Remove failed build layers / every Docker artefact. |
| `scripts/benchmark_backends.sh`, `scripts/test_translation.sh` | host | Backend speed comparison; a one-off translation through the server. |

## Evaluation (maintainers)

Described in [evaluation-and-tuning.md](evaluation-and-tuning.md):
`evals/check.py`, `freeze.py`, `metrics.py` (regression baselines);
`gold_extract.py`, `ocr_gold.py`, `gold_align.py`, `translate_gold.py`,
`gold_report.py`, `gold_passages.py` (scoring against published
translations); `translation_model_test.py`, `word_probabilities.py`
(model choice); `prompt_run_compare.py` (two test runs set against each
other: tokens, cut-off replies, the reviewer's fixes), `glossary_score.py`
(the term tables' checks scored against the fixes already made); `meaning_judge.py`, `judge_key.py`, `judge_score.py`,
`judge_verify.py` (local meaning review, not adopted);
`folio_choice_test.py` (a page-number question test).

## Internal modules

`pipeline/_*.py` are imported, not run. The pipeline code layout is in
`CLAUDE.md`; [pdf-layout.md](pdf-layout.md) covers the layout engine.
Two small ones: `_progress.py` writes the run status `scripts/progress.py`
reads (`processed/_status/`); `_tx_common.py` holds what step 2's
modules share (the footnote separator, the source-language names).
`_glossary_table.py` reads the term tables (`glossary_fr.toml`,
`glossary_it.toml`: every term rule of the prompt as one entry, with the
source forms that put it on a page) and builds the page prompt step 2
sends when the whole prompt would not fit the model's window, the checks
step 2 runs after a page is translated, and the kept / footnoted lists
the code corrections work from (`_glossary_fr.py`, `_glossary_it.py` read
them from it).
