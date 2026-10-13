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
| `scripts/dry_run.py <stem> … / --all` | **before reprocessing for real after a change to step 1's code** (parsing, running heads, page numbers, sentence join, captions): re-derives copies of the books with the current code and reports, per book, the pages, page numbers and running heads that would change, and whether text only *moved* across a page break or words were added / removed. Seconds per book, no GPU, books untouched; `processed/_audit/dry_run/report.md` |
| `evals/freeze.py <stem>` | snapshot a reviewed book's `translation.txt`, `extracted.txt`, `figures.json`, `headers.json` and `page_numbers.txt` into `evals/baselines/<stem>/`, with a `MANIFEST.json` (commit, time) |
| `evals/check.py [stem] [--quiet]` | compare the current output with every baseline: pages, page numbers, figures and headers that changed (with diffs), plus a before/after health table. Exits non-zero on any change; writes `evals/reports/<time>.md` |
| `evals/metrics.py` | per-book health counts (pages with French left in, all-caps pages, unconfirmed page numbers, figures, italics, footnotes). No model calls |
| `evals/_rules.py` | loads the helper functions out of the pipeline scripts without running them (the scripts aren't importable). Fails loudly if the scripts are restructured |

A change to step 1's code goes: `test_rules.py` green → `dry_run.py --all`
→ read every book's changes (each should be what the fix intends; "words
added / removed" in a book you didn't mean to touch is the warning) →
`reprocess_extracted.py` the affected books → the queue's `book <stem>`
retranslates only the pages whose text changed. Every fix in this file's
history that skipped the dry run broke a book that had been signed off.

```bash
python3 evals/freeze.py <stem>              # after a book's PDFs pass review
# ...change a prompt or rule, rerun the affected steps on frozen books...
python3 evals/test_rules.py                 # the frozen cases
python3 evals/check.py                      # "these N pages of book X changed"
python3 evals/freeze.py <stem> --force      # accept a change as an improvement
```

**Same input, same output.** Production translates each page's first
try at temperature 0 (since 2026-10-10; it was 0.3), so a rerun mostly
reproduces itself (35 of 36 test pages word for word) -- but retries run
hotter and the model server isn't bit-exact, so for a check run set
`EVAL_DETERMINISTIC=1` (optionally `EVAL_SEED=<n>`, default
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
| `gold_passages.py sample \| run <label> \| score <label> ...` | compare translation *settings* (temperature, server flags): ~60 page-sized passages of aligned sentences, scored per passage (paired) against the reference; two runs of one setting give the noise floor. Writes `reports/gold-passages/score.md` + `score.html` (side by side, largest disagreement first) |

Current scores (chrF++, 2026-10-01): Rosaroll 54.7, Pecoraro 46.7.
Settings test (2026-10-10, `gold_passages.py`, 60 passages): temperature 0
46.49, temperature 0.3 46.31 / 46.22, MTP at 0 46.34 -- see `docs/STATUS.md`.
La Boëssière's alignment pairs unrelated passages; leave it out of scoring.

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

## 3. The translation model: choice, settings, and how good it is

One investigation, in the order it happened: which model, which settings,
which prompt, why it still errs, and how far it is from the best possible.
Each step used the tools below; their results are what the current setup
rests on.

### 3.1 Where it is set

The model is set by `TRANSLATION_MODEL_PATH` locally (default in
`docker-compose.yml` and `2_translate.py`) or by the llama-swap key in
remote mode (`fmt-translate`; `TRANSLATION_MODEL_NAME` to pick another
key). OCR and translation run one after the other, so each has the whole
GPU. Make a model or setting the default only by changing
`docker-compose.yml` (locally) or the llama-swap entry (remotely); a full
retranslation of every book is then a deliberate, scheduled run.

**Current model:** Qwen3.8-27B, 4-bit (`i1-Q4_K_S`, ~15.8 GB). It doesn't
fit in 16 GB of VRAM whole, so part of its 64 layers go on the GPU: 46
locally (`TRANSLATION_GPU_LAYERS`), 37 on the shared Windows system with
speculative decoding (38 without; `docs/remote-gpu.md`). The previous
model, Qwen2.5-14B-Instruct Q4_K_M (8.6 GB, fits whole), stays available
as `fmt-translate-qwen25`.

### 3.2 Choosing the model (2026-09-25)

`evals/translation_model_test.py` translates a fixed set of hard and
control pages through the real `translate_page()` (same prompt, retries
and repairs as a book run) and scores them with the review report's
checks, side by side, without touching any book:

```bash
docker exec -e TRANSLATION_MODEL_NAME=<llama-swap key> fmt-translator \
  python3 /data/evals/translation_model_test.py run <label>
docker exec fmt-translator python3 /data/evals/translation_model_test.py current
docker exec fmt-translator python3 /data/evals/translation_model_test.py compare current <label> ...
```

The 37-page set (30 hard pages from the batch-2 review, plus controls) was
run with Qwen2.5-14B (then the default), Qwen3.8-27B at a larger quant and
at Q4 (twice), and a Gemma 4 model (key `bt-translate-gemma4`). With the
checks of the day, Qwen3.8 Q4 flagged 0 pages against 14 for Qwen2.5, and
had the most accurate fencing terms; Gemma 4 was about twice as fast
(~20 s/page against ~40) but weaker on terms. Re-scored with the checks
as they are on 2026-10-11 (more wording checks since):

| Run | Pages with a check | Low-confidence / unavailable | s/page |
|---|---|---|---|
| Qwen2.5-14B (then current) | 21/36 | 10 | — |
| Qwen3.8-27B, larger quant | 18/36 | 0 | 46 |
| Qwen3.8-27B Q4 (two runs) | 19/36, 20/36 | 0 | 40 |
| Gemma 4 | 20/36 | 0 | 20 |

So the three newer models are close on what the checks see; the choice
rested on terms, read side by side. Follow a candidate up with
`translate_gold.py` + `gold_report.py` on a gold book or two (§2).

### 3.3 Settings: temperature, speculative decoding (2026-10-10/11)

A server setting is tested by running the set twice with
`EVAL_DETERMINISTIC=1`, with and without it, and comparing the text word
for word, plus one repeat of the unchanged setting to tell differences
from rounding noise; then `gold_passages.py` for accuracy. `FIRST_TEMP=<t>`
tries another first-try temperature.

- **Temperature 0 for the first try** (was 0.3, a value inherited from
  the 7B): gold score 46.49 against 46.31 / 46.22 for two runs at 0.3
  (their gap, 0.09, is the noise floor); 35 of 36 pages reproduce word for
  word. 0.1 changed 24 of 36 pages. Retries stay at 0.55 / 0.8.
- **Speculative decoding (MTP)**, Qwen3.8's built-in draft head: 2.2x
  faster, ~0.3 GB VRAM; not word for word the same as the plain entry (23
  of 36 pages identical), equal in accuracy and closeness to the source.
  Default since 2026-10-11 (`docs/STATUS.md`, "Non-obvious decisions").
- **More GPU layers** is ruled out: the Windows system has other uses
  (`docs/remote-gpu.md`).

### 3.4 Prompt tests

A prompt change is tried without editing the prompt file: add it to
`evals/_prompt_variants.py` and run with `PROMPT_VARIANT=<name>`, on the
page set and on `gold_passages.py`. Closeness to the source (the share of
content words with a source look-alike, `gold_passages.py score`) measures
literalness, which the published-translation score can't: most references
are free translations, so chrF++ rewards paraphrase.

- **Literal translation, rule 0 (2026-10-10/11):** closeness 30.54% →
  31.32%, gold score within noise. Its first version made false-friend
  errors; v2 names them (`docs/model-quirks.md` 2.15).
- **Kept-term guard, quotations, glossary (2026-10-11):** after a
  retranslation with rule 0, `evals/retranslation_compare.py` and the
  meaning reviews (§4) showed kept terms replaced by English look-alikes
  and many glossary gaps. `PAGE_SET=retranslation` runs the pages they
  came from, plus controls (`translation_model_test.py`, results under
  `reports/translation-model-test/retranslation/`). Round 3 (97 pages, 2026-10-12)
  fixed most of its 32 new pages (the weapon parts, over / under, *incontro*,
  *destramente*, the Masiello page left in Italian); the review report's
  checks flagged 1 page against 4 before; 54.7 s/page. Rules changed after
  a run are rechecked on their pages with `PAGE_SET=followup`.

### 3.5 Why it errs: the model's own probabilities

`evals/word_probabilities.py <stem> <scan>` translates one page at
temperature 0 and prints the model's top next words wherever a watched
word (default: the line names) is a candidate. Use the plain entry
(`--entry fmt-translate-plain`, the default): with MTP on, drafted tokens
come back with no alternatives. On Kirchhoffer scan 52, where the source
says quarte three times, "the straight riposte after ___" was tierce
34.3%, quarte 32.6% -- a near-tie the model's prior from other fencing
text nearly won -- and after writing tierce once it kept it (84%, 64%).
That is why a hotter retry alone doesn't fix such a page, and why the
check plus a targeted retry hint does (`docs/model-quirks.md` 2.16). With the
glossary-round prompt (2026-10-11) the same spot reads quarte 47.8%, tierce
14.5%: the prompt as a whole moves these odds, not only the lines it
adds about line names.

### 3.6 How far from the best: the ceiling test (2026-10-11)

The checks and the gold score measure the local model against itself and
against free human translations; neither says how much better a
translation of these pages could be. The ceiling test answers that: a
Claude agent translates the `PAGE_SET=retranslation` pages from the source
alone, with the same prompt files and without seeing any existing English
(saved as `reports/translation-model-test/retranslation/claude.json`), and
a second agent judges the versions blind against the source -- meaning
errors and wrong terms per page. It is a yardstick only: the books are
translated locally, and the cover says so. A large gap means prompt and
glossary work still pays; a small one means the remaining errors are for
review to catch.

**Result (2026-10-11, 63 pages, of which 46 had a known error):** the
prompt with the glossary rounds fixed about 30 of the 46 -- every line-name
swap, the kept terms, and most glossary terms (*battement*, *à fond*, *coup
fourré*, *contre*, *développement*, *effacé*, *défendre*). What it left are
sentence-level errors: lost negations (Kirchhoffer 50, 64, 86), *ne … que*
read as "not" (Joseph-Renaud 39), *ignorait* → "knew", the possessive that
swaps forte and foible (Gomard 139), and a long French quotation left
untranslated (Masiello 128). Claude's translation, on the 55 of these
pages it covered, got all six of those sentence errors right (Kirchhoffer
50, 64, 86; Joseph-Renaud 39, 264; Masiello 128) and the same terms right. So the remaining gap is comprehension of the
sentence, not vocabulary: more glossary work won't close it, review does
(§4). Controls were stable (length and closeness within a point or two).

---

## 4. Checking a retranslation, and reading for meaning

The regression checks (§1) protect pages that should not change. A
deliberate retranslation (a new prompt, a new model) changes most pages,
so it needs a different check: did any page get worse, and is the
English right?

- **`evals/retranslation_compare.py <stem> [--before <tag>]`** compares a
  book's translation with a copy saved before the run
  (`translation.<tag>.txt`, `quality_report.<tag>.json` in the book's
  `intermediate/`): pages shorter by 15%+ or longer by 30%+, kept terms that
  lost their italics (counting the line names the PDF italicises when
  drawn, and no more than the source has), footnote notes that vanished
  (not ones that moved to the term's earlier first use), new
  low-confidence pages, new review-report checks, source-language runs,
  `[[fix]]` entries that no longer apply (find and replacement both gone
  from the page -- step 2 applies the fixes at the end of each run, so a
  working one leaves its replacement); and closeness before and after.
  `reports/retranslation/<stem>.md` + `.html`, worst first, by side-by-side
  pdf page, source / before / after side by side. Save the copies before
  the run: `cp translation.txt translation.<tag>.txt` (and the review
  report's json). It flags; a person or the meaning review decides.
  It does not see a page cut off mid-sentence that is not much shorter:
  step 2 now rejects a reply that ran out of room (`finish_reason:
  length`), and the meaning review checks page endings.
- **Meaning review** (`evals/meaning_review.md`): a Claude agent reads the
  source against the English page by page, about 130-190 pages per agent,
  two or three agents at a time, and lists each place the English says
  something different -- source words, the English now (an exact quote, so
  it can become a `[[fix]]` find), a literal correction, a plain
  explanation, marked where a fencing term is involved, by side-by-side pdf
  page. It reads the prompt's glossary rules first, so the reviewer's
  decided renderings are not reported, and skips what a `[[fix]]` already
  corrects. It catches what no check can: a lost negation, a reversed
  clause, a term guessed wrong, a page cut off, a table that vanished.
  Check a few of each report's worst items against the page before using
  it.

  **The loop.** Recurring errors across books become a decision list for
  the reviewer (one line each: source, now, proposed, where); the decided
  renderings go into the prompt's glossary, which fixes every book. Then:
  save a copy (`translation.before-<round>.txt`), retranslate only the
  pages that show the patterns, compare, and re-review those pages -- is
  each error fixed, did anything break? A page that got worse is restored
  from the copy. Single errors (negations, swapped roles, OCR misreadings)
  become `[[fix]]` entries, re-checked against the text after the
  retranslation. Each round's own lines need watching: in round 4
  (2026-10-13) the worst new errors came from glossary lines applied to a
  neighbouring phrase (`docs/model-quirks.md` 2.19), so every line names
  its scope.

---

## 5. Fine-tuning  (not started)

The pipeline only runs pretrained models; training would happen outside
it.

- **Method:** QLoRA fine-tuning on examples, optionally followed by
  preference tuning (DPO). In practice, train on a rented cloud GPU (a few
  hours, roughly $5-20 for a 7B model, more for larger), merge, convert
  to GGUF, and run it locally like any other model.
- **Data is most of the work:** ~1,000 examples of `{prompt, source page,
  English page}`. English from (a) reviewed pages of the operator's own
  books, (b) aligned gold pairs (§2; human translations beat
  model-written ones) and/or (c) a stronger model's output, corrected --
  the ceiling test (§3.6) shows how much a stronger model would add.
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
