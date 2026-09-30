# User Manual — Section III: Using the Pipeline

Day-to-day operation, once Section II's host setup is done and the image
is built. Section I explains what each step does and why.

Throughout, the container is `fmt-translator` and the project
root is `~/FencingManualTranslator`. Helper scripts live in
`~/FencingManualTranslator/scripts/`.

---

## III.1 — Quick start

```bash
cd ~/FencingManualTranslator

# one-time (verifies the host, writes .env/backend.env, builds, brings up):
bash scripts/setup.sh                    # ~20-30 min first build

# every run:
cp "/path/to/My Fencing Book (1875).pdf" books/
scripts/run_full_pipeline.sh
```

`scripts/setup.sh` is the first-run path (see Section II for the host
steps it checks). After that, `startup_docker.sh` / `run_full_pipeline.sh`
/ `shutdown_docker.sh` are all you need.

**Remote GPU:** on a GPU-less host, `bash scripts/setup.sh --remote`
instead, then fill `REMOTE_HOST` / `REMOTE_API_KEY` in `backend.env` and
stand up the GPU system (`docs/remote-gpu.md`). Day-to-day is the same —
with llama-swap on the GPU system, `run_full_pipeline.sh` works unchanged;
`monitor_gpu.sh` and the local server-log tips below don't apply (watch
the GPU system's console instead).

When it finishes, the two PDFs are in
`processed/My Fencing Book (1875)_translated/`.

`run_full_pipeline.sh` recreates the container, then runs steps 1→2→3→4 in
order, then prints a summary and waits for Enter. If step 1 fails it stops
before translating.

## III.2 — The four steps, run individually

The full-pipeline script is just these four `docker exec` calls with
framing. Run them one at a time when you're iterating.

```bash
docker compose up -d       # container must be up; comes up idle

docker exec fmt-translator python3 /app/1_ocr_and_extract_vlm.py
docker exec fmt-translator python3 /app/2_translate.py
docker exec fmt-translator python3 /app/3_english_only_pdf.py
docker exec fmt-translator python3 /app/4_side_by_side_pdf.py
```

Each step decides for itself which books to act on:

| Step | Acts on a book when… | To force a re-run |
|---|---|---|
| 1 — OCR | `processed/<stem>_translated/` does **not** exist yet | `rm -rf processed/<stem>_translated/` (loses everything), or see III.5 to re-parse without re-OCR |
| | …or its OCR run was interrupted: OCR saves after every page (`intermediate/raw_ocr.partial.json`), and re-running step 1 resumes after the last saved page, reading any failed pages again. The save is used only while the scan's page count and crop are unchanged, and is deleted once `raw_ocr.txt` is written | start over instead: `rm processed/<stem>_translated/intermediate/raw_ocr.partial.json` |
| 2 — translate | `intermediate/translation.txt` is missing (whole book); otherwise only the pages whose body text changed since they were translated (fingerprints in `intermediate/translated_from.json`) | whole book: `rm processed/<stem>_translated/intermediate/translation.txt`; chosen pages: `2_translate.py --pages <stem> 12,40-42` (III.5b) |
| | A whole-book run saves after every page (`intermediate/translation.partial.json`); if it crashes or is cancelled, re-running step 2 resumes after the last saved page, with the footnote state it had then. The save is used only while those pages' source text is unchanged, and is deleted when the book finishes | start over instead: `rm processed/<stem>_translated/intermediate/translation.partial.json` |
| 3 — English-only PDF | `intermediate/translation.txt` exists | just re-run it — it overwrites |
| 4 — side-by-side PDF | `intermediate/translation.txt` and `intermediate/<stem>_OCR.pdf` exist | just re-run it — it overwrites |

So the normal iteration loops are:

- **Tune the translation** — edit `pipeline/translation_prompt.txt` or
  the term rules in `pipeline/translation_prompt.txt`, `rm intermediate/translation.txt`, re-run steps
  2, 3, 4.
- **Tune a PDF's layout** — edit `pipeline/3_…` or `pipeline/4_…`, re-run
  just that step. `pipeline/` is mounted read-only into the container at
  `/app`, so your host edits are picked up immediately with no rebuild.
- **Tune the OCR prompt** — edit `pipeline/vlm_ocr_prompt.txt`, then see
  III.5 (you usually don't need to re-run the vision model).

## III.3 — Preparing a source PDF

- **Format:** a PDF, scanned or born-digital. Any page count. Spaces and
  punctuation in the filename are fine — the filename stem becomes the
  output folder name.
- **Quality:** clean flatbed / library scans work best. 300 DPI or better
  in the source. Colour or greyscale both fine. Phone photos of a book
  have not been tested and will probably align poorly in step 4.
- **Put it in `books/`.** That directory is mounted read-only into the
  container; the pipeline never writes there.

### Per-book overrides

Drop a sidecar file next to the PDF in `books/`, named with the same stem:

| File | Content | Effect |
|---|---|---|
| `<stem>.crop` | `left,top,right,bottom` — fractions, e.g. `0.06,0.04,0.06,0.08` | Trims that fraction off each page image before OCR. Use it to cut a dark scanner-bed border or a gutter shadow that the vision model keeps transcribing as noise. |
| `<stem>.lang` | Tesseract language codes, e.g. `fra+lat` | Only used by the legacy non-VLM `1_ocr_and_extract.py`. The default VLM path ignores it. |
| `<stem>.rotate` | `cw` / `ccw` / `180` (every plate), or page lists: `cw: 35, 39, 55-229` and `ccw: 241` | Turns sideways-printed plates upright in both PDFs. Page lists are for books whose plates face different ways. See `docs/pdf-layout.md`. |

## III.4 — What lands in `processed/<stem>_translated/`

Only the two finished PDFs sit in the book's own folder root. Everything
else — the raw OCR, the parsed scaffold, the reconciled page numbers, the
translation, and the verbatim OCR-source PDF step 4 draws its left column
from — lives in an `intermediate/` subfolder, so the root stays clean.

| File | From step | What it is |
|---|---|---|
| `intermediate/<stem>_OCR.pdf` | 1 | Verbatim copy of the source, for step 4 to draw the left column from |
| `intermediate/raw_ocr.txt` | 1 | The vision model's untouched per-page responses. The recovery point — never deleted by any step |
| `intermediate/extracted.txt` | 1 | Canonical `PAGE_NUMBER / HEADER / BODY_TEXT` scaffold per page |
| `intermediate/page_numbers.txt` | 1 | One reconciled folio per line; `[n]` = inferred sheet ordinal, not a printed number |
| `intermediate/translation.txt` | 2 | English, one record per page (U+001E separator), aligned with `extracted.txt` |
| `… — English Only (Portrait).pdf` | 3 | Reading copy |
| `… — Side by Side (Landscape).pdf` | 4 | Comparison copy |

Because the container runs as your user, these are owned by you. (On a
fresh checkout where `processed/` was created by an older root container,
run `sudo chown -R "$(id -u):$(id -g)" processed/` once —
`run_full_pipeline.sh` also does this automatically.)

## III.5 — Re-parsing OCR without re-running the vision model

`intermediate/raw_ocr.txt` holds the model's raw output. The page-number
reconciliation and the repetition-loop cleanup that produce
`intermediate/extracted.txt` and `intermediate/page_numbers.txt` run *on
that file*. If you improve that logic — or just want the latest cleanup
applied to a book OCR'd before it existed — rebuild the two derived files
without paying for OCR again:

```bash
# every book that has a raw_ocr.txt:
docker exec fmt-translator python3 /app/reprocess_extracted.py

# or just some:
docker exec fmt-translator python3 /app/reprocess_extracted.py \
  /data/processed/<stem>_translated
```

Then re-run steps 2→4. This does **not** re-apply the OCR *prompt* —
changing `vlm_ocr_prompt.txt` genuinely needs the vision model, which
means `rm -rf` the book's folder and re-running step 1.

## III.5a — The review report: which pages to check

Every full run ends with a **review report** per book,
`processed/<stem>_translated/<stem> — Review Report.md`. It lists the pages
the pipeline itself has reason to doubt, by side-by-side **pdf page**
(plus scan page and printed page number), each with the reason. No model is
involved, so it runs in seconds, and you can rerun it any time:

```bash
docker exec fmt-translator python3 /app/quality_report.py <stem>   # or --all
```

Rebuild the PDFs first, since pdf page numbers are read from the built
side-by-side PDF. What it checks:

- **Translation:** pages step 2 flagged low-confidence or couldn't
  translate, paragraphs left in French, English much shorter than the French
  (something dropped), model chatter, and a plate's "Planche N." label
  missing from the English.
- **Wording:** misspellings the model is known to make (THUST, THROUGHS) and
  renderings against the glossary ("THROWS" for *coup*, "romp" for *rompre*,
  "plank" for *planche*). This covers running heads too.
- **Page numbers:** a shown number that goes backwards or jumps further than
  the scans allow. Both pages involved are listed.
- **Terminology to settle:** a book-wide section, not per page. One French
  phrase rendered several ways across the book (e.g. *coups composés* as
  "blows" on some pages and "thrusts" on others). Choose one, put it under
  `[terms]` (III.5b), and retranslate the other pages with `--pages`.
- **Notes:** things worth a glance but often fine. For example, a paragraph
  moved to the notes as a margin note, which is a heuristic and is sometimes wrong.

- **Layout:** a running head shown on a page that prints none (title page,
  plate, chapter opener), or one missing from a page that prints one. This
  comes from a page-layout detection model (`pipeline/_layout.py`, CPU only)
  that step 1 runs on every page into `intermediate/layout.json`. For a book
  OCR'd before it existed, run
  `docker exec fmt-translator python3 /app/layout_scan.py <stem>`
  (about 0.5 s per page). Pages where the detector and the OCR disagree are
  listed as notes.

Not covered yet: a title the OCR never transcribed (Gomard pdf 316).

The review loop: read the report → look at those pages → record decisions
in the overrides file (III.5b) → `apply_overrides.py` and/or
`2_translate.py --pages` → rebuild the PDFs → rerun the report.

### Margin notes

Printed margin (shoulder) notes are found by the layout detector, read by the
OCR model, and placed in the translation as a grey italic line with a side
bar, right before the paragraph (or sentence) they're printed beside. New
books get this in step 1. For a book OCR'd earlier:

```bash
docker exec fmt-translator python3 /app/layout_scan.py --margins <stem>
docker exec fmt-translator python3 /app/reprocess_extracted.py /data/processed/<stem>_translated
docker exec fmt-translator python3 /app/2_translate.py   # retranslates just the changed pages
```

### Running heads the OCR left out

On some pages the OCR's reply has no running-head field at all (on
LaMarche, more than half the pages). The running head is then read
straight from the box the layout detector found at the top of the page —
the same way margin notes are read — and used as the OCR's own answer; the
book's usual spelling is kept, and a box holding only the page number or a
plate label is ignored. New books get this in step 1 (`intermediate/
heads.json`). For a book OCR'd earlier:

```bash
docker exec fmt-translator python3 /app/layout_scan.py --heads <stem>
docker exec fmt-translator python3 /app/reprocess_extracted.py /data/processed/<stem>_translated
```

then translate any new running-head wording (`2_translate.py --pages <stem>
N` for one page that carries it) and rebuild the PDFs.

### Patching a scan with pages from another copy

When a scan has a defect another digitised copy doesn't — a missing page, a
page scanned twice, a folded plate — `patch_scan.py` builds a corrected scan
and keeps the OCR and translation of every other page. A plan file
(`books/_alt/<stem>.patch.json`) lists the new scan page by page: a number is
a page of the original scan, `{"file": "_alt/x.pdf", "page": 1}` a page of
another PDF, `{"image": "_alt/plate.jpg"}` a picture. Its `note` goes on the
cover ("Changes to this scan: …"), since the scan is then no longer only the
digitisation it names. The untouched original is kept as
`books/_alt/<stem>_original.pdf`, and the book's data is backed up.

```bash
X="docker exec fmt-translator python3"
$X /app/patch_scan.py <stem> /data/books/_alt/<stem>.patch.json            # dry run
$X /app/patch_scan.py <stem> /data/books/_alt/<stem>.patch.json --apply    # stage scan, move data
# books/ is read-only in the container: run the printed host commands, which keep
# the original in books/_alt/ and move the staged scan (processed/_patch/) into books/
docker exec -e PDF_ONLY=<stem> fmt-translator python3 /app/1_ocr_and_extract_vlm.py  # reads the new pages only
$X /app/patch_scan.py <stem> x --finish                                     # re-parse; prints the pages to translate
$X /app/2_translate.py --pages <stem> <pages>                               # as printed
```

To change a plan that has already been applied (say, a better copy of the
plates turns up), first put the book's data back as it was: move
`processed/<stem>_translated/intermediate` aside and copy
`intermediate.before_patch-<time>` in its place — the plan's page numbers
refer to the original scan, so the data has to as well — then `--apply` the
new plan.

Then rebuild the PDFs and the review report as usual. Renumbering pages
shifts scan page numbers after the first change, so a book with an
overrides file needs its scan numbers updated (the dry run warns).

## III.5b — Recording your review decisions: the overrides file

When a review finds something the pipeline got wrong and can't fix
automatically — a misread page number, a header that shouldn't be there,
wording you want changed — record the decision in
`books/<stem>.overrides.toml`, **not** by editing files under
`processed/`. Those files are regenerated (III.5, step 2) and a hand edit
there is silently lost; the overrides file is re-applied on every run.

Pages are **scan pages**: the page number of the source scan PDF, starting
at 1. (The side-by-side PDF's numbering shifts when a page overflows onto a
continuation sheet, so it can't key a durable decision.)

```toml
[headers]                        # running head (French, as extracted) -> English
"DES COUPS SIMPLES." = "OF SIMPLE THRUSTS."

[terms]                          # book-specific glossary, added to the translation prompt
"contre-opposé" = "opposing counter"

[pages.314]                      # scan page 314
page_number = "298"              # folio to show ("" = none)
header = ""                      # French running head ("" = none)
note = "free text"

[pages.346]                      # a plate whose engraved caption the OCR misread
caption = "Coup de tierce, 3e coup paré dans la même position en tierce."
caption_en = "..."               # optional: your own English (else step 2 translates it)
# figure = "inset"               # only if the OCR missed the figure entirely and it's a
                                 # small picture on a text page (default "plate")

[[fix]]                          # edit to the English, re-applied after every translation
scan = 314
prepend = "OF DISARMING."        # skipped if the page already starts with it
[[fix]]
scan = 153
find = "ON THE COUNTER-<i>RIPOSTE</i>"
replace = "OF THE COUNTER-<i>RIPOSTE</i>"
```

Apply it to a book's existing outputs (no model, seconds), then rebuild
the PDFs:

```bash
docker exec fmt-translator python3 /app/apply_overrides.py <stem>
docker exec -e PDF_ONLY=<stem> fmt-translator python3 /app/4_side_by_side_pdf.py
```

Step 1, `reprocess_extracted.py` and step 2 apply the same file themselves.
A malformed file or a typo'd key stops the run with a message naming it,
rather than silently ignoring a decision. A `find` that no longer matches
(the page was retranslated since) is reported, not an error.

### Retranslating individual pages

To redo a few pages without retranslating the book (a changed source page,
a bad translation, a new `[terms]` entry), name the scan pages:

```bash
docker exec fmt-translator python3 /app/2_translate.py --pages <stem> 12,40-42
```

or mark them in the overrides file and run without a list:

```toml
[pages.40]
retranslate = true
```
```bash
docker exec fmt-translator python3 /app/2_translate.py --pages <stem>
```

Marked pages are done once per version of their source text
(`intermediate/retranslated.json` keeps a fingerprint), so the flag can stay
in the file: rerunning costs nothing until that page's extracted text
changes. Only the named pages change. Footnote first-use is worked out from
the rest of the book, and the header/title harmonization and your overrides
are re-applied afterwards. Rebuild the PDFs as above. GPU cost is about 10–30
seconds per page.

## III.5c — Manual corrections: which tool for which problem

The pipeline handles most of a book on its own, but some books need a
human decision here and there: an engraved caption the OCR can't read, a
page missing from the scan, a heading worded differently. Every such
correction is **a small file in `books/`, next to the scan** — never an edit
of the files under `processed/`, which are regenerated and would silently
lose it. The pipeline re-applies these files on every run, so a correction
is made once and survives retranslation and rebuilding.

Pages are always **scan pages**: the page number your PDF viewer shows for
the source scan in `books/`, counting from 1 — not the number printed on the
page, and not the side-by-side PDF's page (a long page can spill onto a
second sheet there). The review report lists pages as scan pages.

| The problem | The fix | Where it's described |
|---|---|---|
| A word or sentence in the English is wrong | `[[fix]]` find/replace in `<stem>.overrides.toml` | III.5b |
| A heading / running head is translated badly | `[headers]` in the overrides file | III.5b |
| A term should always be rendered a certain way in this book | `[terms]` in the overrides file (then retranslate the pages that use it) | III.5b |
| A page number is misread, or shown where none is printed | `[pages.N] page_number = "…"` | III.5b |
| A running head is wrong, or shouldn't be there | `[pages.N] header = "…"` | III.5b |
| A plate caption is misread (engraved script, damage) | `[pages.N] caption = "…"` (the printed text, corrected), optionally `caption_en` | III.5b |
| A page's translation is poor and should be redone | `2_translate.py --pages <stem> N` or `[pages.N] retranslate = true` | III.5b |
| Plates are printed sideways | `<stem>.rotate` | III.3 |
| A dark border or gutter shadow gets transcribed as text | `<stem>.crop` | III.3 |
| A page is missing, scanned twice, or a plate is folded/cut in this copy | patch the scan with pages from another digitised copy: `patch_scan.py` | III.5a, "Patching a scan" |

**What each correction needs rerun.** Every correction is read by the step
it affects, and that step re-applies it on every run — so order doesn't
matter, and a later rerun never undoes one. What differs is how much has to
run again before you see it:

| Correction | Read by | To see it | GPU? |
|---|---|---|---|
| `[headers]`, `[[fix]]`, `caption_en` | end of step 2; `apply_overrides.py` | `apply_overrides.py`, rebuild PDFs | no |
| `<stem>.rotate` | steps 3 and 4 | rebuild PDFs | no |
| `source_changes` in `<stem>.meta.json` (cover note) | steps 3 and 4 | rebuild PDFs | no |
| `[pages.N]` `page_number`, `header` | step 1 / `reprocess_extracted.py` (after the automatic logic, so the decision wins) | `apply_overrides.py`, rebuild PDFs | no |
| `[pages.N] caption` | step 1 / `reprocess_extracted.py`, and step 2 (translates it) | `apply_overrides.py`, `2_translate.py --pages <stem> N`, rebuild PDFs | yes, seconds |
| `[terms]`, `[pages.N] retranslate` | step 2 | `2_translate.py --pages <stem> <pages>`, rebuild PDFs | yes, ~10–30 s per page |
| `<stem>.crop` | step 1 (trims the image before OCR) | a full rerun: remove `processed/<stem>_translated/` (III.2), then steps 1–4 — so set it before a book's first run | yes, whole book |
| patched scan (`patch_scan.py`) | step 1 (new pages only) | the `patch_scan.py` sequence | yes, new pages only |

The review report doesn't read corrections directly; it checks the output
they produced, so rerun it last.

After editing the overrides file:

```bash
X="docker exec fmt-translator python3"
$X /app/apply_overrides.py <stem>                   # writes the decisions in (no model, seconds)
$X /app/2_translate.py --pages <stem> <pages>       # only if English is needed: [terms], retranslate, a new caption
docker exec -e PDF_ONLY=<stem> fmt-translator python3 /app/3_english_only_pdf.py
docker exec -e PDF_ONLY=<stem> fmt-translator python3 /app/4_side_by_side_pdf.py
$X /app/quality_report.py <stem>                    # confirm the page no longer shows up
```

`apply_overrides.py` says which pages still need a translation (a new
caption without `caption_en`, for example). A typo'd key or malformed file
stops the run with a message naming it; a `[[fix]]` whose text no longer
matches (the page was retranslated since) is reported so you can update it.

**Keep corrections few.** If the same kind of mistake shows up on many
pages or in several books, it's a pipeline rule to improve, not a list of
corrections to maintain — report it (with scan page numbers) rather than
adding dozens of `[[fix]]` entries. The corrections file is for what is
genuinely particular to one book: its engraved captions, its print damage,
its one odd heading.

## III.6 — Processing several books at once

Put them all in `books/` and run the pipeline once. Each step walks every
PDF / every `*_translated` folder that it hasn't already handled, so a
second book added later doesn't disturb the first.

Step 2 leaves already-translated books alone unless their source text
changed. It retranslates only the changed pages, and skips a book with
none (III.2), so adding or reprocessing one book never costs GPU time on
the others.

## III.7 — Long runs: watching progress

A 350-page book is a few hours, most of it in step 1. Both steps print
per-page progress to the `docker exec` stdout. In separate terminals:

```bash
# live GPU: util should spike 50-90%, SCLK 1500-2500 MHz, VRAM 60-80%
scripts/monitor_gpu.sh

# the running server's own log
docker exec fmt-translator tail -f /tmp/vlm_server.log
docker exec fmt-translator tail -f /tmp/translation_server.log
```

Step 2 prints a running count of pages, marks footnoted pages with
`(N fn)`, and flags anything it had to keep as low-confidence.

## III.8 — Helper scripts

All in `scripts/`, run from anywhere.

| Script | Purpose |
|---|---|
| `run_full_pipeline.sh` | Recreate container, run 1→2→3→4, print summary. The normal entry point. |
| `startup_docker.sh` | `systemctl start docker` + `docker compose up -d`. Start-of-session. |
| `shutdown_docker.sh` | Stop the container and the Docker service — frees all VRAM/RAM for other use (e.g. gaming). Keeps images, volumes, data. |
| `monitor_gpu.sh` | `watch`-based live GPU + container-resource display. Ctrl-C to stop. |
| `check_gpu_usage.sh` | One-shot: container up? GPU visible? server healthy? single test inference. Run after setup or a rebuild. |
| `benchmark_backends.sh` | Runs `llama-bench` for each compiled backend against both models. Use when deciding vulkan vs rocm7. |
| `clean_rebuild.sh` | Stop, remove *this project's* image, clear build cache, rebuild, restart. Models are untouched (they live in `~/llm_models`). ~15-20 min. Use after a Dockerfile change. |
| `compact_docker_storage.sh` | Remove dangling images and unused build cache. Monthly housekeeping. Frees 2-10 GB. |
| `full_cleanup.sh` | Nuclear: every container, image, volume, and the overlay2 tree. Requires typing `YES`. Keeps `~/llm_models`, `books/`, `processed/`. Rebuild is ~20 min afterward (no model re-download). |

> Note: `debug_vlm.sh` and `test_translation.sh` still reference the old
> Qwen2-VL model path and are stale — use `check_gpu_usage.sh` and the
> server logs instead.

## III.9 — Switching inference backend

Edit `backend.env`, then `docker compose up -d` (recreate, **no rebuild**):

```bash
BACKEND_MODE=local
LLAMA_BACKEND=rocm7        # or: vulkan (default)
```

`vulkan` and `rocm7` are both compiled into the image, so the switch is
~10 seconds. `rocm714` is wired but not built and will fail fast with a
pointer to `BackendBuildstage`.

For `BACKEND_MODE=remote` (inference on another GPU system on your LAN), see
`docs/remote-gpu.md`. Set `REMOTE_HOST` / `REMOTE_PORT` / `REMOTE_API_KEY`
(and `VLM_MODEL_NAME` / `TRANSLATION_MODEL_NAME`) in `backend.env`. If the
GPU system runs **llama-swap**, it swaps the vision and text models
automatically by request name — nothing manual between steps, and
`run_full_pipeline.sh` works. With a bare `llama-server` on that system you
restart it with the other model between step 1 and step 2 (run the steps
individually). A GPU-less pipeline host is prepared by
`scripts/setup.sh --remote`.

## III.10 — Troubleshooting

### The vision server won't start / step 1 exits immediately

```bash
docker exec fmt-translator tail -50 /tmp/vlm_server.log
```

- **`libllama-server-impl.so: cannot open shared object file`** — a
  `docker exec` shell that starts a server without the backend lib dir on
  `LD_LIBRARY_PATH`. The pipeline scripts handle this themselves; if you
  see it, you're launching `llama-server` by hand — prepend
  `LD_LIBRARY_PATH=/usr/local/lib/llama-<vulkan|rocm|rocm714>`.
- **Model file not found** — check `~/llm_models` on the host is mounted
  and holds `allenai_olmOCR-2-7B-1025-Q6_K.gguf` +
  `mmproj-allenai_olmOCR-2-7B-1025-f16.gguf`. In `local` mode the
  entrypoint downloads missing models on `up`; watch
  `docker logs fmt-translator`.
- **RADV / device-lost on load** — transient. The wait loop now notices a
  dead server and reports it instead of hanging; re-run the step. If it
  repeats, try `LLAMA_BACKEND=rocm7` (III.9).

### `[VLM Error: …]` on some pages in `extracted.txt`

```bash
grep -n "VLM Error" processed/<stem>_translated/intermediate/extracted.txt
```

Step 1 already retries a failed page up to 3 times, restarting the server
and dropping to a smaller image on the last try. Pages that still fail are
genuinely hard (enormous resolution, mangled scan). Options: add a
`.crop` to trim a noisy border, or accept it — step 2 will carry the error
line through as `[translation unavailable — original text follows]` and
the page count stays correct.

### A page reads `[low-confidence translation — review]` or `[translation unavailable …]`

Step 2 never drops a page. These markers mean 3 attempts failed the
wrong-script / too-much-non-English / too-short checks. Usually the OCR
for that page is garbled — look at the same page in
`intermediate/extracted.txt`. If OCR is clean and it's a translation
miss, adjust `translation_prompt.txt`, `rm intermediate/translation.txt`,
re-run step 2.

### A page reads `[Empty page]`

Expected for a blank leaf, a section-break page, or a page whose OCR body
was just a stray mark or a bare page number — step 2 detects near-blank
input and doesn't call the model (which would otherwise reply *"Please
provide the text you would like to translate"* and leak that into the
output). If you see `[Empty page]` where there was real text, the OCR for
that page failed — check `intermediate/extracted.txt`, and re-OCR with a
`.crop` if a scanner border is the cause.

The full catalogue of model failure modes and why each guard exists:
[`docs/model-quirks.md`](../docs/model-quirks.md).

### One page in the output is split in two / pages misalign after some point

The old `\n---\n` page separator was content-fragile — a stray Markdown
`---` from the translation model once split one page into two, shifting
every later page against its source. Pages are now separated by U+001E, a
control character no model output contains (`pipeline/_pageio.py`), so
this can't recur in new files; old `---` files are still read. If pages
misalign anyway, compare the page counts of `extracted.txt` and
`translation.txt` (step 2 refuses a partial retranslation when they
differ) and re-run step 2 for that book.

### Translation step never starts / server timeout

```bash
docker exec fmt-translator pkill -f llama-server
docker compose restart && sleep 20
docker exec fmt-translator python3 /app/2_translate.py
```

Step 2 resumes — it skips books whose `intermediate/translation.txt` is
already current, and a book interrupted mid-run continues after its last
saved page (`intermediate/translation.partial.json`; see the step table
at the top of this section).

### Container won't start

- **`Unable to find group render` / GPU device errors** — the
  `RENDER_GID` / `VIDEO_GID` in `.env` don't match this host. Re-run the
  Section II group-check (`getent group render video`) and fix `.env`.
- **`permission denied … docker daemon`** — `sudo usermod -aG docker
  $USER`, then log out and back in.
- **Crash loop right after `up`** — the container is *meant* to crash
  visibly rather than restart (`restart: "no"`). Read
  `docker logs fmt-translator`; don't add a restart policy
  to paper over it.

### Processing hangs mid-book

```bash
df -h                                          # need ≥10 GB free
docker ps                                      # container still up?
docker logs fmt-translator | tail -50
docker compose restart && sleep 20
# then re-run the step it stopped on
```

Steps 1 and 2 save after every page and resume from the last saved page
(`raw_ocr.partial.json` / `translation.partial.json` in
`intermediate/`); step 1 also reads pages that failed again. Steps 3 and
4 just rebuild their PDF from the start (a few minutes).

### Remote mode (`BACKEND_MODE=remote`)

```bash
docker exec -w /app fmt-translator \
  python3 -c "import backend_config as b; print(b.describe())"
```

- **`local -> …`** instead of `remote -> …` — `backend.env` didn't take.
  It must have `BACKEND_MODE=remote`; then `docker compose up -d` to
  recreate the container (env changes need a recreate, not `restart`).
- **`(auth: NO KEY)`** — `REMOTE_API_KEY` is empty in `backend.env`.
- **Step exits "server not reachable"** — the GPU system's `llama-server` /
  llama-swap isn't up, the firewall isn't open to this host, or
  `REMOTE_HOST` is stale. Test: `curl -sS http://$REMOTE_HOST:$REMOTE_PORT/v1/models`.
- **`404` / "model not found" from the remote** — the `"model"` the
  pipeline sends doesn't match a llama-swap key. Set `VLM_MODEL_NAME` /
  `TRANSLATION_MODEL_NAME` in `backend.env` to the llama-swap map keys.
- **`401`** — `REMOTE_API_KEY` here ≠ the server's `--api-key` (watch for
  a trailing newline).
- **OCR (step 1) fails, translate (step 2) is fine** — the remote is
  missing the `mmproj` projector, or (rarely) its `llama-server` build
  chokes on the base64 image upload. Confirm with a `_sample.pdf` first.
- There is **no local server log** in remote mode — watch the
  `llama-server` / llama-swap console on the GPU system.

## III.11 — Ending a session

```bash
scripts/shutdown_docker.sh
```

Stops the container and Docker, releasing all VRAM and RAM. Images,
models, `books/`, and `processed/` are all preserved. Restart later with
`scripts/startup_docker.sh`.

## III.12 — Housekeeping

- Each book costs roughly 2–3× its source PDF size in `processed/`.
  Archive finished books elsewhere and delete the folder.
- Run `scripts/compact_docker_storage.sh` monthly, or after a failed
  build.
- Models live in `~/llm_models` (host), not in the image — a rebuild or
  even `full_cleanup.sh` never re-downloads them.
- Keep an untouched copy of every source PDF outside `books/` — the
  pipeline treats `books/` as disposable input.
