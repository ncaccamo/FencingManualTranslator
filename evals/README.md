# evals/ — regression harness

Prompt and rule changes are **global**; review is **per-book**. This
directory is the safety net: freeze a book's accepted output, and every
later change tells you exactly which pages of every frozen book moved.

Nothing here calls a model. It runs on the host with stock Python 3
(heavy pipeline deps — `fitz`, `PIL`, `numpy` — are stubbed; see
`_rules.py`).

## Layout

| path | what |
|---|---|
| `_rules.py` | loads the pipeline's *pure* helpers (`apply_always_translate`, `match_case`, `reconcile_page_numbers`, `parse_figures_block`, …) by exec-ing the definition slice of `pipeline/2_translate.py` and `pipeline/1_ocr_and_extract_vlm.py`. Breaks loudly if those files are restructured. |
| `test_rules.py` | frozen input→output cases for those helpers, one per bug ever fixed. `python3 evals/test_rules.py` (or `pytest`). |
| `metrics.py` | crude per-book health numbers (French-residue pages, all-caps pages, untrusted folios, figure rows, italic spans, …). |
| `freeze.py` | snapshot a book's accepted artifacts into `baselines/<book>/`. |
| `check.py` | diff current pipeline output against the baselines + metrics table → `reports/<timestamp>.md`. Exit ≠ 0 if anything changed. |
| `baselines/<book>/` | the frozen artifacts (`translation.txt`, `extracted.txt`, `figures.json`, `headers.json`, `page_numbers.txt`) + `MANIFEST.json` (git commit, timestamp). **Committed.** ~1–2 MB/book, text only — never the PDFs. |
| `reports/` | check.py output. Git-ignored. |

## Workflow

```bash
# 1. once a book's PDFs pass your review:
python3 evals/freeze.py Joseph-Renaud
git add evals/baselines/Joseph-Renaud_Lescrime && git commit -m "freeze JR baseline"

# 2. make a prompt / rule change, then re-run the pipeline for the frozen
#    books (reprocess is enough for parser/reconcile changes; add step 2
#    for term/translation changes):
docker exec fmt-translator python3 /app/reprocess_extracted.py <folders>
docker exec fmt-translator python3 /app/2_translate.py
docker exec fmt-translator python3 /app/3_english_only_pdf.py
docker exec fmt-translator python3 /app/4_side_by_side_pdf.py

# 3. see what moved:
python3 evals/test_rules.py          # deterministic layer — must stay green
python3 evals/check.py               # per-book page/figure/folio/metric diff

# 4. a change is not automatically a regression. Read the flagged pages.
#    If the change is an improvement you want, re-freeze:
python3 evals/freeze.py Joseph-Renaud --force
git add evals/baselines/Joseph-Renaud_Lescrime && git commit -m "re-freeze JR: <why>"
```

`check.py Joseph-Renaud` limits to one book; `--quiet` drops the per-page
diffs and prints only counts + the metrics table.
