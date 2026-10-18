# Per-book files: every setting a book can have

Everything particular to one book lives in `books/`, next to its scan, under
the scan's file name (its *stem*). The pipeline reads these files on every
run. Nothing under `processed/` is ever edited by hand: it is regenerated,
and a hand edit there is lost on the next run.

This page lists every such file and every setting in it: what it's for, a
real example, which step reads it, and what to rerun to see the change.
How to use the review report that leads you here is in manual III.5a; the
step-by-step workflow is in III.5b and III.5c.

**Pages are scan pages**: the page number of the source scan PDF in
`books/`, counting from 1. That isn't the number printed on the page, and
it isn't the side-by-side PDF's page, which shifts when a long page spills
onto a second sheet. The review report gives both.

**Book names**: every pipeline script takes the books to work on by name
(`2_translate.py florio_1844_scienza`), or `--all`. Nothing runs on every
book by default, so a finished book is never reprocessed because another
was added.

| File | What it holds |
|---|---|
| `<stem>.pdf` | the scan |
| `<stem>.meta.json` | the cover's bibliographic block, the book's language, the editor's name |
| `<stem>.overrides.toml` | your review decisions: page numbers, running heads, wording, terms, fixes, book-wide settings |
| `<stem>.edits/scan-NNNN.txt` | whole pages of English edited by hand |
| `<stem>.crop` | a border to trim off every page before OCR |
| `<stem>.split` | corrections for cutting two-page spreads apart |
| `<stem>.rotate` | which plates to turn upright in the PDFs |
| `_alt/<stem>_original.pdf` | the untouched scan, kept when a scan is split or patched |

---

## `<stem>.overrides.toml`

A TOML file. A typo in a key or a malformed file stops the run with a
message naming it; a decision is never silently ignored. Start one with a
comment saying what the book is. Every section is optional.

### `[book]`: book-wide settings

| Key | Meaning | Example |
|---|---|---|
| `running_heads = false` | The book prints **no running heads**. The line the layout detector takes for one is really the page number. With this set, only the OCR's own reading decides whether a page has a running head, and none is copied onto a page from its neighbours. | Florio 1844 (`⁂ 3 ⁂` centred between ornaments), Bruno 1891 (a bare centred `19`), Lambertini 1870 (the page number in the outer corner). |
| `paragraphs = "spaced"` | The book prints a **blank line between paragraphs** and no first-line indent. Unset (`"indented"`): each paragraph opens with an indent and no blank line, as most of these books print them. Only the PDFs change (rebuild steps 3 & 4). | A book whose scans show spaced paragraphs. |
| `page_numbers = "outer"` | Where the book prints its **page numbers**, for scans the layout detector can't read for it: `"outer"` (odd right, even left), `"inner"`, `"centre"`, or `"bottom-outer"` etc. The running head is then centred. Unset: learned from the scans. PDFs only. | Jacob-André 1887 (yellowed scans, no page-number boxes detected). |
| `final = true` | The book is **finished**. Every script that would change it (steps 1–4, `reprocess_extracted.py`, `apply_overrides.py`, `reread_pages.py`, …) skips it, even under `--all`, unless run with `--unlock`. The review report still runs. | Set it once a book is released. |

**When to use `running_heads = false`:** the review report says so. It
shows a note under *About this book* ("…it is probably the page number…")
and stops listing "the page prints a running head, but none is shown" on
every page. Check two or three pages of the scan to confirm, then set it.
**Rerun:** `reprocess_extracted.py <stem>`, then `2_translate.py <stem>`
(only pages whose text changed are retranslated: chapter titles move from
the header back into the text), then the PDFs.

### `[pages.N]`: one scan page

| Key | Meaning | Example |
|---|---|---|
| `page_number = "7"` | the page number to show (`""` = none) | Florio scan 17 prints 7; the OCR read 1. |
| `header = "…"` | the running head, in the source language, as in `extracted.txt` (`""` = none) | Jacob scan 49: a title, not a running head → `header = ""`. |
| `caption = "…"` | the printed caption of the page's plate or figure, corrected (source language). Step 2 translates it. | La Boëssière's engraved captions the OCR can't read. |
| `caption_en = "…"` | your own English for that caption (wins over the model's) | |
| `figure = "inset"` | only if the OCR missed the figure entirely: `"plate"` (default) or `"inset"` (a small picture on a text page) | |
| `retranslate = true` | retranslate this page with `2_translate.py --pages <stem>` (once per version of its source text, so the flag can stay) | |
| `note = "…"` | free text, ignored by the pipeline: why you decided it | `"prints 7 between ornaments; the OCR read 1"` |

**Rerun:** `page_number` and `header`: `apply_overrides.py <stem>`, then the
PDFs. `caption`: also `2_translate.py --pages <stem> N`. `retranslate`:
`2_translate.py --pages <stem>`, then the PDFs.

### `[headers]`: a running head's English

```toml
[headers]
"DES COUPS SIMPLES." = "OF SIMPLE THRUSTS."
```

The left side is the head exactly as in `extracted.txt`. **Rerun:**
`apply_overrides.py`, then the PDFs.

### `[terms]`: the book's own glossary

```toml
[terms]
"cartoccio volante" = "flying <i>cartoccio</i>"
```

Added to the translation prompt for this book only. It overrides the
general term lists, for a term the book uses in its own way. **Rerun:**
retranslate the pages that use the term (`2_translate.py --pages <stem>
<pages>`), then the PDFs.

### `[[fix]]`: a small edit to the English

```toml
[[fix]]                    # replace a phrase
scan = 153
find = "ON THE COUNTER-<i>RIPOSTE</i>"
replace = "OF THE COUNTER-<i>RIPOSTE</i>"

[[fix]]                    # add a line at the top of the page
scan = 314
prepend = "OF DISARMING."
```

Applied after every translation run. A `find` that no longer matches (the
page was retranslated since) is reported, not an error: update or remove
it. For more than a sentence, use a page edit (below). **Rerun:**
`apply_overrides.py`, then the PDFs.

---

## `<stem>.edits/`: pages edited by hand

For the last few errors in a book that's nearly done: a garbled paragraph,
a subtitle the OCR dropped, wording no prompt change will fix. Rerunning
would cost more than fixing it by hand.

1. **On the host**, from the repo (`books/` is read-only in the container):
   ```bash
   python3 pipeline/edit_page.py florio_1844_scienza 91
   ```
   This writes `books/florio_1844_scienza.edits/scan-0091.txt`: a header
   with the page's original text for reference, a `---` line, then the
   page's current English.
2. Edit the English **below the `---` line** in any editor. Keep the
   markup (`<i>…</i>`, `<fn>…</fn>`, `⟦MARGIN: …⟧`) as it is.
3. In the container:
   ```bash
   python3 /app/apply_overrides.py florio_1844_scienza
   python3 /app/3_english_only_pdf.py florio_1844_scienza
   python3 /app/4_side_by_side_pdf.py florio_1844_scienza
   ```

The edit replaces the machine English on every later run (after the
`[[fix]]` edits). If the page's source text changes afterwards (a re-read,
a pipeline fix), the review report lists the edit as possibly out of date.
The review report skips its machine checks on an edited page, since a
person has read it. Delete the file to go back to the machine translation.

**The cover says so.** Every page a person changed, through a `[[fix]]` or
a page edit, is counted on the cover: *"Corrected by hand on 14 pages."* To
be named, set `"edited_by": "Your Name"` in `<stem>.meta.json`: *"Corrected
by hand on 14 pages, edited by Your Name."*

---

## `<stem>.meta.json`

The cover's bibliographic block. Generate it from `books/library.md` with
`scripts/make_book_meta.py`, then check it against the scan's title page.

| Key | Meaning |
|---|---|
| `title`, `title_en` | short title as printed, and in English |
| `title_full`, `title_full_en` | the full title-page wording, when it says more |
| `author` | as it should appear on the cover |
| `author_short` | the author as shared file names give it, when the stem runs it together: `"Joseph-Renaud"`, `"La Marche"`, `"Kirchhoffer et al."` (`scripts/release.py`) |
| `title_short_en` | the English title for shared file names, when `title_en` cut at its first colon or full stop reads badly |
| `year_first` | first published (the year in the stem, so stems sort as a timeline) |
| `year_edition`, `edition`, `publisher`, `first_as` | the edition this scan is |
| `language` | `French` / `Italian`: picks the translation prompt and term lists |
| `rights` | the original's status (default: public domain) |
| `source`, `source_id`, `source_url`, `source_terms` | where the scan came from, and its terms of reuse |
| `source_changes` | a note when the scan was patched from another copy (`patch_scan.py` writes it) |
| `edited_by` | your name, when you want hand corrections credited (see above) |

**Rerun:** the PDFs (`language` also needs a retranslation).

---

## Scan preparation

Set these **before a book's first run**. Changing one later means rerunning
step 1 for the whole book.

| File | Content | Effect |
|---|---|---|
| `<stem>.crop` | `left,top,right,bottom` as fractions, e.g. `0.06,0.04,0.06,0.08` | Trims that much off each page image before OCR: a dark scanner-bed border or a gutter shadow that the vision model keeps transcribing. |
| `<stem>.split` | `140: 0.512` (cut at that share of the width) or `66-95: whole`, one per line | Corrections for `split_spreads.py`, which cuts a scan of two-page spreads into single pages. |
| `<stem>.rotate` | `cw` / `ccw` / `180` (every plate), or page lists: `cw: 35, 39, 55-229` and `ccw: 241` | Turns sideways plates upright in both PDFs (read by steps 3 and 4 only, so it can be changed any time; rebuild the PDFs). |

A page missing, scanned twice, or a folded plate is fixed by patching the
scan with pages from another digitised copy: `patch_scan.py` (manual
III.5a). The original is kept in `books/_alt/`.

---

## Which correction for which problem

| The problem | The fix |
|---|---|
| The report: "probably the page number" under *About this book* | `[book] running_heads = false` |
| A book is released and must not change by accident | `[book] final = true` |
| A page number is misread, or shown where none is printed | `[pages.N] page_number` |
| A running head is wrong, or shouldn't be there | `[pages.N] header` |
| A running head's English is wrong | `[headers]` |
| A term should always be rendered one way in this book | `[terms]`, then retranslate the pages that use it |
| A word or sentence in the English is wrong | `[[fix]]` |
| A paragraph or page needs rewriting, or a line is missing | `edit_page.py` (`<stem>.edits/`) |
| A page's translation is poor and a retranslation may fix it | `2_translate.py --pages <stem> N` or `[pages.N] retranslate` |
| A plate caption is misread | `[pages.N] caption` (+ `caption_en`) |
| Plates are sideways | `<stem>.rotate` |
| A border or shadow is transcribed as text | `<stem>.crop` (before the first run) |
| A page is missing or damaged in this copy | `patch_scan.py` |

**Keep corrections few.** When the same mistake shows up on many pages or
in several books, it is a pipeline rule to improve, not a list of
corrections to maintain. The corrections files are for what is genuinely
particular to one book.

---

## Sharing a finished book: `scripts/release.py`

```bash
python3 scripts/release.py <stem> --dry-run     # show the names
python3 scripts/release.py <stem>               # copy the PDFs to release/<Name>/
```

Copies the two PDFs under readable names (author, year, short English
title, edition), in plain ASCII so they survive URLs and chat uploads:

```
release/Florio-1844-The-Science-of-Fencing-Explained/
    Florio-1844-The-Science-of-Fencing-Explained-English.pdf
    Florio-1844-The-Science-of-Fencing-Explained-Parallel-Italian-English.pdf
```

The names come from `author_short` (or the stem), the year in the stem, and
`title_short_en` / `title_en`. It warns when the book isn't marked
`final`, its review report still lists pages to check, or a PDF is older
than the translation. `processed/` keeps the pipeline's own names.

