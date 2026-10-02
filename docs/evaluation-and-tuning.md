# Evaluation & tuning

**For maintainers.** None of this is needed to run the pipeline; it is
what makes changing it safe: checking that a prompt or rule change didn't
break a book already reviewed, scoring translations against published
ones, and choosing or (later) fine-tuning a model. To run the pipeline,
see `setup/MANUAL_SECTION_III_USAGE.md`.

Everything here works on the operator's own books, which never enter the
repo: `books/`, `processed/`, `evals/gold/`, `evals/baselines/` and
`evals/reports/` are git-ignored. The tools are tracked; the data is not.

---

## 1. Regression checks  (`evals/`)

Prompt and rule changes apply to every book; review happens one book at a
time. Without a check, a pattern widened for book B quietly damages book A
that was already signed off.

| tool | what it does |
|---|---|
| `evals/test_rules.py` | frozen input → output cases for the code that doesn't call a model (page numbers, running heads, sentence join, term enforcement, cover lines, …): one per bug ever fixed. Runs with or without pytest. **Run after every change; must stay green.** |
| `evals/freeze.py <stem>` | snapshot a reviewed book's `translation.txt`, `extracted.txt`, `figures.json`, `headers.json` and `page_numbers.txt` into `evals/baselines/<stem>/`, with a `MANIFEST.json` (commit, time) |
| `evals/check.py [stem] [--quiet]` | compare the current output with every baseline: pages, page numbers, figures and headers that changed (with diffs), plus a before/after health table. Exits non-zero on any change; writes `evals/reports/<time>.md` |
| `evals/metrics.py` | per-book health counts (pages with French left in, all-caps pages, unconfirmed page numbers, figures, italics, footnotes). No model calls |
| `evals/_rules.py` | loads the helper functions out of the pipeline scripts without running them (the scripts aren't importable). Fails loudly if the scripts are restructured |

```bash
python3 evals/freeze.py <stem>              # after a book's PDFs pass review
# ...change a prompt or rule, rerun the affected steps on frozen books...
python3 evals/test_rules.py                 # the frozen cases
python3 evals/check.py                      # "these N pages of book X changed"
python3 evals/freeze.py <stem> --force      # accept a change as an improvement
```

**Same input, same output.** At the production temperature (0.3) the
model rewords every page slightly, so a plain rerun diff is noise. For a
check run set `EVAL_DETERMINISTIC=1` (optionally `EVAL_SEED=<n>`, default
1234): step 2 then uses temperature 0 and that seed on every request, so
an unchanged book reproduces exactly and every difference `check.py`
reports is real. Retries still run, but each attempt is the same, so a
failing page fails the same way. Leave it off for production. The files
step 1 writes are already deterministic.

```bash
docker exec -e EVAL_DETERMINISTIC=1 fmt-translator \
  python3 /app/2_translate.py       # then steps 3-4, then check.py
```

**Baselines are out of date.** The two that exist (Gomard, Kirchhoffer)
were frozen on 2026-08-29, before the change of translation model and the
sentence join, so `check.py` will report most of their pages. Refreeze
each book once its current review passes.

---

## 2. Scoring against published translations  (`evals/gold/`)

A public-domain original paired with a published translation lets the
pipeline's English be scored, not just compared with itself.

```
evals/gold/<stem>/
  src.pdf  ref.pdf     # original + published translation (supplied by the operator)
  src.txt  ref.txt     # their text (from the PDF's text layer, or OCR)
  aligned.jsonl        # sentence pairs {src, ref}
  hyp.txt              # the pipeline's translation of src
  meta.json            # language, and src_pages / ref_pages: the part the reference covers
  report.md
```

| tool | what it does |
|---|---|
| `gold_extract.py <stem> [src\|ref]` | text from a PDF that already has a usable text layer: strips running heads, joins hyphenated words, reflows paragraphs, marks each source page |
| `ocr_gold.py <stem> [src\|ref] [--pages a-b]` | OCR with olmOCR-2 when there is no text layer (plain transcription, not the book scaffold) |
| `gold_align.py <stem>` | sentence alignment by length (Gale & Church; no model). How often sentences pair one-to-one tells you whether the translation is close or free |
| `translate_gold.py <stem> [--model …]` | translate the `src_pages` range with the real step 2 (same prompt for the book's language, retries and checks) into `hyp.txt` |
| `gold_report.py <stem>` | how close the reference is, how it renders fencing terms, and chrF++ of `hyp.txt` against it, over the matching page ranges |

Current scores (chrF++, 2026-10-01): Rosaroll 54.7, Pecoraro 46.7.

- **Only score the part the reference covers.** Translating the whole book
  but scoring a page range misaligns the comparison (Rosaroll scored 22.8
  instead of 54.7 that way); `translate_gold.py` translates only
  `src_pages`.
- **Free translations score low.** A loose or reworked translation (such
  as Barbasetti's own 1936 English) scores low even when the pipeline is
  right. Use those for terminology, not for the score. COMET, which
  measures meaning and tolerates rewording, would help; it isn't set up
  (needs `unbabel-comet` and a ~2 GB model).
- **Pick drill-heavy books.** Technique manuals exercise the hard cases
  (command lists, terse instructions, dense terminology); a literary
  treatise mostly tests general prose.
- **Also useful for terms:** how the human translator rendered `prise de
  fer`, `liement`, `cavazione` … feeds the prompts' term lists.
- **Copyright:** the originals are public domain; the translations
  usually aren't. Private use as a scoring reference or term source is
  defensible; redistributing the reference text, or a model trained to
  reproduce it, is not. This is why `evals/gold/` is git-ignored.

---

## 3. Choosing the translation model

The model is set by `TRANSLATION_MODEL_PATH` locally (default in
`docker-compose.yml` and `2_translate.py`) or by the llama-swap key in
remote mode (`fmt-translate`; `TRANSLATION_MODEL_NAME` to pick another
key). OCR and translation run one after the other, so each has the whole
GPU.

**Current model:** Qwen3.8-27B, 4-bit (`i1-Q4_K_S`, ~15.8 GB), chosen
2026-09-25 by the translation model selection test: no flagged pages out
of 37 hard pages, against 14 for Qwen2.5-14B, and the most accurate
fencing terms. It doesn't fit in 16 GB of VRAM whole, so part of its 64
layers go on the GPU: 46 locally (`TRANSLATION_GPU_LAYERS`), 42 on the
shared Windows system (the `--n-gpu-layers` of the llama-swap entry;
`docs/remote-gpu.md`): ~40-100 s per page, depending on page size. The previous
model, Qwen2.5-14B-Instruct Q4_K_M (8.6 GB, fits whole), stays available
as `fmt-translate-qwen25`. See `docs/remote-gpu.md` for the GPU memory
limits on Windows.

**Testing a candidate:** `evals/translation_model_test.py` translates a
fixed set of hard and control pages with each model and scores them side
by side, without touching any book:

```bash
docker exec -e TRANSLATION_MODEL_NAME=<llama-swap key> fmt-translator \
  python3 /data/evals/translation_model_test.py run <label>
docker exec fmt-translator python3 /data/evals/translation_model_test.py current
docker exec fmt-translator python3 /data/evals/translation_model_test.py compare current <label> ...
```

Follow up with `translate_gold.py` + `gold_report.py` on a gold book or
two (§2). Make a model the default only by changing `docker-compose.yml`
(locally) or the llama-swap entry (remotely); a full retranslation of
every book is then a deliberate, scheduled run.

---

## 4. Fine-tuning  (not started)

The pipeline only runs pretrained models; training would happen outside
it.

- **Method:** QLoRA fine-tuning on examples, optionally followed by
  preference tuning (DPO). In practice, train on a rented cloud GPU (a few
  hours, roughly $5-20 for a 7B model, more for larger), merge, convert
  to GGUF, and run it locally like any other model.
- **Data is most of the work:** ~1,000 examples of `{prompt, source page,
  English page}`. English from (a) reviewed pages of the operator's own
  books, (b) aligned gold pairs (§2; human translations beat
  model-written ones) and/or (c) a stronger model's output, corrected.
  Overweight the hard cases (drills, plate captions, pages that start
  mid-sentence, term-dense passages). Hold back ~15% and score them with
  §1 and §2 before and after.
- **Preference pairs:** a reviewed or human translation (preferred)
  against the model's raw output (rejected), to fix tone and conventions.
- **What to expect:** mostly consistency (conventions learned instead of
  prompted, fewer badly failed pages). It won't fix subtle meaning errors.

**Languages.** French and Italian are both handled: the book's
`meta.json` `language` selects the prompt (`translation_prompt.txt` or
`translation_prompt_it.txt`), and the review report uses language-specific
term checks. Adding a language means a prompt, its kept-term and
always-translate lists, and the review report's checks for it; the rest
of the pipeline doesn't depend on the language.
