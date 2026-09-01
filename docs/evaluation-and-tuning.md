# Evaluation & tuning

**Maintainer-facing.** None of this is needed to run the pipeline — it is
the scaffolding for changing it safely: regression-checking prompt/rule
edits, comparing models, and (later) fine-tuning. The end-user path is
`setup/MANUAL_SECTION_III_USAGE.md`; this document is the other audience.

All of it operates on the operator's own books, which never enter the
repo (`books/`, `processed/`, `evals/gold/`, `evals/baselines/`,
`evals/reports/` are git-ignored). The harness *code* is tracked; the
data it works on is not.

---

## 1. The regression harness  (`evals/`)  — implemented

Prompt and rule changes are global; review is per-book. Without a net, a
regex you broaden for book B silently damages book A that you already
signed off. The harness is that net.

| tool | what |
|---|---|
| `evals/test_rules.py` | frozen input→output cases for the deterministic helpers (`apply_always_translate`, `match_case`, `reconcile_page_numbers`, `parse_figures_block`, `enforce_kept_terms`, …). One per bug ever fixed. Run with or without pytest. **Must stay green.** |
| `evals/freeze.py <stem>` | snapshot a book's accepted `translation.txt` / `extracted.txt` / `figures.json` / `headers.json` / `page_numbers.txt` into `evals/baselines/<stem>/` + a `MANIFEST.json` (git commit, timestamp). |
| `evals/check.py [stem] [--quiet]` | diff current pipeline output vs every baseline — pages changed (with unified diff), folio labels changed, figure rows changed, headers changed — plus a baseline→current **metrics** table. Exit ≠ 0 on any change. Writes `evals/reports/<ts>.md`. |
| `evals/metrics.py` | per-book health counts (French-residue pages, all-caps pages, untrusted folios, figure rows, italic spans, footnote markers). No model calls. |
| `evals/_rules.py` | loads the pure helpers out of the two pipeline scripts without running their `main` (they aren't importable). Breaks loudly if those files are restructured. |

**Loop:**

```bash
python3 evals/freeze.py <stem>                       # after a book's PDFs pass review
# ...change a prompt/rule, re-run reprocess+translate+render for frozen books...
python3 evals/test_rules.py                          # deterministic layer
python3 evals/check.py                               # "these N pages of book X moved"
python3 evals/freeze.py <stem> --force               # if the change is an improvement you accept
```

**Nondeterminism:** at the production `temp 0.3` the model rewords every
page slightly, so a plain re-run diff of `translation.txt` is noise. For
an eval run set **`EVAL_DETERMINISTIC=1`** (optionally `EVAL_SEED=<n>`,
default 1234) — `2_translate.py::translate_with_llama` then forces
`temperature 0` + that seed on every request, so an unchanged book
reproduces byte-for-byte and a `check.py` page-diff is a real change.
The retry ladder still runs but every attempt is the same greedy decode
(a failing page fails identically). Use it for `check.py`; leave it off
for production. The deterministic sidecars (`figures.json`,
`page_numbers.txt`, `headers.json`, `extracted.txt`) don't need it.

```bash
docker exec -e EVAL_DETERMINISTIC=1 booktranslator-translator-1 \
  python3 /app/2_translate.py       # then steps 3-4, then check.py
```

---

## 2. Gold references & scoring  (`evals/gold/`)  — partially built

A public-domain original + a published translation lets you score the
pipeline's output, not just diff it against itself.

```
evals/gold/<stem>/
  src.pdf  ref.pdf        # original + published translation (operator supplies)
  src.txt  ref.txt        # OCR'd (olmOCR-2 handles English too)
  aligned.jsonl           # {src, ref} pairs after sentence/paragraph alignment
```

- **Alignment** is the hard part — a published translation is not
  sentence-aligned to the original (merged/split sentences, dropped
  clauses, different pagination). Coarse (by heading) is enough for
  glossary mining and picking few-shot examples; sentence-level needs
  `hunalign` (length + dictionary) or `vecalign` (multilingual
  embeddings).
- **Metrics**: `chrF++` (immediate), `COMET` (needs `unbabel-comet` +
  a ~2 GB model — semantic, tolerates free translation and register
  drift better than n-gram metrics).
- **Register mismatch**: a Victorian-era translation scores low on
  surface metrics against a modern-English target even when the MT is
  correct. Options: lean on COMET; or have a frontier model lightly
  modernise the reference's diction (meaning fixed) to match the target.
- **Choosing a gold book**: a drill-heavy technique manual exercises the
  pipeline's hard cases (command lists, terse instruction, dense
  terminology); a literary treatise mostly tests prose translation, which
  is both the pipeline's weakest area and the least representative.
- **Also yields**: a term-authority table (how the professional
  translator handled `prise de fer`, `liement`, …) feeding rules 2/3 and
  the prompt's term rules (2 / 3 / 3b); hand-picked few-shot passages for its
  EXAMPLES block.
- **Copyright**: originals are public domain; translations may not be.
  Private use as an eval reference / glossary source / local-model SFT
  data is defensible; redistributing the reference text or a model that
  memorised it is not. `evals/gold/` is git-ignored for this reason.

---

## 3. Swapping the translation model  — procedure

The translation model is chosen by `TRANSLATION_MODEL_PATH` (env), default
in `docker-compose.yml` (`MODEL_PATH`) and `2_translate.py`. OCR and
translation load sequentially — each gets the whole GPU — so a bigger
translation model doesn't have to coexist with olmOCR.

16 GB budget, 8k context:

| model | quant | ~weights | fits |
|---|---|---|---|
| **default** | Qwen2.5-14B-Instruct Q4_K_M | 8.6 GB | yes (~11 GB total) |
| alternative | Qwen2.5-7B-Instruct Q6_K | 6.3 GB | yes |
| candidate | Qwen2.5-14B-Instruct Q4_K_M | 8.6 GB | yes (~11 GB total, ~5 GB headroom) |
| — | 14B Q5_K_M | 10 GB | tight |
| — | 14B Q6_K / 32B any | — | no |

**A/B without touching the default:**

```bash
docker exec -e TRANSLATION_MODEL_PATH=/models/<new>.gguf \
  booktranslator-translator-1 python3 /app/2_translate.py     # then steps 3-4
```

Save the incumbent's `translation.txt` / `figures.json` / `headers.json`
as `*.7b` first; `touch extracted.txt` for the book(s) you want
re-translated (step 2 skips up-to-date books). Compare with `check.py`
metrics + a manual read of the known-hard pages. Make it the default only
by editing `docker-compose.yml`.

**Observed (14B Q4_K_M vs 7B Q6_K, one book):** no speed penalty;
fewer quality-gate failures; markedly better figure captions and rule-2
term compliance; body prose more *accurate* but occasionally more literal;
still fabricates over a fragmentary page start. A real but modest upgrade.

---

## 4. Fine-tuning  — planned, not started

Consumes pretrained models; no training in the pipeline itself.

- **Method**: QLoRA SFT (only thing that trains a 7–14B in 16 GB), then
  optionally DPO. Realistically, run the training job on a rented cloud
  GPU (a few hours, ~$5–20 for a 7B QLoRA), merge the adapter, quantise
  to GGUF, drop it in for local inference.
- **Data (80% of the work)**: ~1k instruction examples —
  `{system: <translation_prompt.txt>, user: <french page>, assistant:
  <english page>}`. Targets from (a) distilling a frontier model over
  ~800 pages then spot-fixing, and/or (b) the operator's own
  review-passed pages, and/or (c) aligned gold pairs (§2) — human gold
  beats distilled. Overweight the hard cases (drills, plate captions,
  cross-page fragments, term-dense passages). Hold out ~15% and score it
  with §1 + §2 before/after.
- **DPO pairs**: (gold or corrected = chosen, raw 7B output = rejected)
  to sharpen the register and convention adherence.
- **Payoff**: mostly *consistency* — conventions baked in instead of
  hoped-for, sharp drop in catastrophic failures. Does not raise the
  reasoning ceiling (subtle meaning errors, name hallucinations largely
  survive). Tune the 14B, not the 7B, on the same set.
- **Other languages**: the language-specific pieces are small — the
  stop-word set + elision detector (`contains_too_much_non_english`),
  `_ALWAYS_TRANSLATE`'s term list, and the prompt's term rules/examples.
  Extract those into a per-language module (`lang/fr.py`, `lang/it.py`)
  selected by `<stem>.lang` when a non-French book arrives; the rest of
  the pipeline is language-agnostic.
