# How the PDF steps work

Steps 3 and 4 turn `translation.txt` (+ the source scan + a couple of
sidecar files) into the two output PDFs. This is the map of how — the
tools, the shared layout engine, and the knobs — for anyone making
formatting decisions.

- [`pipeline/_render.py`](../pipeline/_render.py) — the shared text
  layout engine
- [`pipeline/3_english_only_pdf.py`](../pipeline/3_english_only_pdf.py) —
  the flowing English reading copy
- [`pipeline/4_side_by_side_pdf.py`](../pipeline/4_side_by_side_pdf.py) —
  scan-left / translation-right

---

## The tool: PyMuPDF (`fitz`)

One library does everything. It's imported as `fitz`. Only a handful of
its primitives are used:

| Call | What it does |
|---|---|
| `fitz.open()` | new empty PDF (output); `fitz.open(path)` opens the scan |
| `doc.new_page(width, height)` | add a blank sheet; sizes are in **points** (72 pt = 1 inch) |
| `page.insert_text((x, y), s, fontsize, fontname)` | one short string at a point (labels, headers) |
| `page.insert_image(rect, stream=…)` | drop a JPEG into a rectangle |
| `page.draw_line`, `page.draw_rect` | the divider, the footnote rules |
| `page.show_pdf_page(rect, src, i)` | paint page `i` of another PDF into a rectangle — this is how the scan gets onto the left of the side-by-side |
| `src_page.get_pixmap(matrix, clip)` | rasterise (part of) a scan page to pixels; `.tobytes("jpeg", jpg_quality=…)` for embedding |
| `src_page.get_text("blocks")` | the scan's own embedded OCR text layer, as positioned boxes — used only for paragraph alignment |

Coordinates: origin is **top-left**, y grows **downward**. A "rect" is
`(x0, y0, x1, y1)`.

Fonts are the PDF base-14 (`times-roman`, `times-italic`, `times-bold`) —
always present, no font files to ship.

---

## `_render.py` — the text layout engine

Both PDF steps flow body text through this. It exists because the old
code did `text.replace("\n", " ")` and one `insert_textbox`, which threw
away every line break, indent and `<i>` span (turning fencing drills into
a wall of prose) and **silently truncated** anything that didn't fit.

### The model

Text → a list of `Line` objects. A `Line` has:

- `indent` — left offset in points (from counting leading spaces in the
  source line: drill blocks, braced command groups keep their shape).
  `indent < 0` is a **sentinel** meaning "draw a horizontal rule here"
  (the footnote separator).
- `spans` — `[(text, italic_flag), …]`, so `<i>…</i>` becomes real
  italics.
- `gap` — a blank line (paragraph break); drawn as partial leading, not a
  full empty line.

### The two functions that matter

- **`layout(text, width, size) -> [Line]`** — splits on `\n`, derives
  each line's indent, greedy-word-wraps the rest to `width` at `size`.
  Wrapping widths use `fitz.Font(...).text_length()` so accented and
  italic glyphs advance correctly (an earlier version summed
  `get_text_length()` and words like `DEUXIÈME` collided with the next
  word).
- **`draw(page, rect, lines, size, *, dry=False) -> (leftover, y_end)`** —
  draws as many `Line`s as fit in `rect`, using a `fitz.TextWriter` (one
  batched write, correct glyph metrics). **Returns the lines that didn't
  fit** — the caller flows them to the next slot/page. Nothing is ever
  dropped. `dry=True` measures without drawing (used to pick a font size
  that fits).

### Footnotes

`2_translate.py` appends a `___FOOTNOTES___` block to a page's text.
`split_footnotes(text)` peels it off; `footnote_lines(notes, …)` lays it
as: a thin rule, an italic **"Translator's notes (AI):"** label (so it
reads as distinct from the author's own inline `(1)(2)(3)` notes), then
each note with a hanging indent.

---

## Step 3 — English-only (`3_english_only_pdf.py`)

**Sheet:** portrait US Letter (612 × 792 pt), 56 pt margins → one text
column `COL`. Body text 10 pt.

**The `Flow` class** owns "the current output page and the y cursor" so
the rest of the code never thinks about page breaks:

- `section_label(page_num, header)` — draws `— [n] —   HEADER` + a rule.
  Starts a fresh sheet if the label (plus a reserved height for a figure
  that's about to follow) wouldn't fit — so a figure's label is never
  orphaned at the bottom of a sheet. `header` is only a **genuine
  repeated running head** by the time it gets here (`reconcile_headers()`
  in step 1 sends one-off titles to the body); a `left\tright` value is
  drawn as a two-part head.
- `body(text)` — `split_footnotes`, then `_flow(layout(body))`, then the
  footnote block smaller and grey. `_flow` loops `draw()` → new page →
  `draw()` until there's no leftover.
- `figure(src_page, band, caption)` — rasterise the scan page (or the
  `band` sub-rectangle) at 150 dpi, embed as JPEG scaled to the column
  width (capped at 82 % of column height), translated caption beneath.

**Per source page**, the loop:

1. Gets `page_num` / `header` from `extracted.txt` (`header` looked up in
   `headers.json` for the English version).
2. Builds figure **placements** for the page:
   - Each entry in `figures.json` for this page → `band_for_figure()`.
     `plate` = whole page (cropped to the density profiler's ink band
     when portrait, to strip the running header / folio / scan margin);
     `inset` = the profiler's band.
   - If `figures.json` flagged **nothing**, run `find_figure_band()`
     anyway — the VLM under-reports. A profiler hit → an `inset`
     placement with a generic `[illustration from the original]` caption.
3. `section_label`, then draw each figure, then `body(tr)` — **unless**
   the body is just the figure's caption again (≤ 20 words, matches a
   caption), in which case the body is skipped.

Text flows continuously — consecutive source pages pack onto a sheet;
a page's text can resume mid-sentence after an inserted run of plates,
which is faithful to books that bind their plates mid-chapter.

**`find_figure_band(page)`** — the density profiler. Rasterises the page
small, profiles each row's dark-pixel fraction, finds the tallest
contiguous run of near-solid rows in the interior 8–92 % that also spans
> 30 % of the width. Returns `(y0, y1, x0, x1)` fractions or `None`. Tuned
for framed half-tone plates; weak on faint line art (hence the VLM flag
carries detection, this just does geometry).

---

## Step 4 — Side-by-side (`4_side_by_side_pdf.py`)

**Sheet:** landscape US Letter (792 × 612 pt). Left rect `LEFT` for the
scan, a vertical divider at x≈396, right rect `RIGHT` for the translation.

**Left column:** `page.show_pdf_page(LEFT, src, i)` paints the whole scan
page, fit-and-centred. No cropping, no detection — the figure comes along
for free (this is why figures were easy here and hard in step 3).

**Right column:**

- Header row: `Page [n]` + the (translated) running header + a rule.
- Body: try font size **9, then 8, then 7 pt** — the largest that fits
  the column (`draw(dry=True)`). If even 7 pt overflows, draw what fits
  and spill the rest onto a `(cont.)` continuation spread (same scan
  repeated on the left). Text is never truncated.
- Footnotes: a fixed strip is reserved at the **bottom** of the right
  column; the body flows above it.
- **Plate pages** (no body text): instead of `[blank / no text in
  original]`, if `figures.json` has the page → `[plate — see facing
  page]` + the translated caption. The scan on the left carries the
  picture.

### Paragraph alignment

When it works, each translated paragraph on the right is top-anchored to
roughly the same fraction down the page as its source paragraph sits on
the scan, so reading across lands in the right place.

- `paragraph_fracs(src_page)` — from `src_page.get_text("blocks")`
  (the scan's embedded OCR layer): the y-fraction where each source
  paragraph starts. `None` if the scan has no usable layer (many library
  scans don't).
- `reconcile_fracs(fracs, n_paras)` — maps those anchors onto the
  translation's paragraph count. If the scan split into **line-level**
  blocks (more anchors than paragraphs, > 2×), it resamples evenly — a
  raw one-to-one map pinned whole pages' text to the top. `None` for < 2
  anchors.
- `render_aligned()` — anchoring can only *add* whitespace (push a
  paragraph down to its source fraction), never remove it, so the dry pass
  measures three things: total added whitespace `W`, the largest single
  anchor gap `Gmax`, and overflow `X`.
  - `Gmax` > ~55 pt, or `W` > 35 % of the column → the anchors are
    untrustworthy (patchy Google Books layer) → **plain flow**.
  - `0 < X ≤ 0.8·W` → compress every gap by `1 − X/W` so a bigger font
    still fits (better than shrinking to 7 pt with full gaps).
  - otherwise → draw anchored as-is.

Alignment rate is a rough scan-quality indicator: Gallica ~70–80 %,
Google Books ~30–45 %.

---

## Cover page (`_cover.py`)

Both steps append a first page built by `_cover.render(doc, stem, …)` —
one shared renderer so the two covers can't drift (they did once: a
hard-coded model name outlived a model swap and sat wrong on every cover).

What it shows and where each piece comes from:

| Block | Source |
|---|---|
| title · author · year · publisher | `books/<stem>.meta.json` (falls back to the bare stem) |
| "The original work is in the public domain." + digitisation source/URL | `meta.json` (`rights`, `source`, `source_id`, `source_url`, `source_terms`) |
| disclosure sentence, method, reading-conventions | constants in `_cover.py` |
| OCR / translation model names | OCR: live from `VLM_MODEL_PATH` (label drops the quant tag). Translation: step 2's per-page record (`intermediate/translated_from.json`), with page counts if models were mixed; pages with no record (translated before 2026-09-25) are credited to Qwen2.5-14B-Instruct-Q4_K_M |
| `Pipeline <sha> · generated <date>` | `PIPELINE_VERSION` env (set by `run_full_pipeline.sh`); omitted on a bare `docker exec` |
| CC0 line + GitHub URL | constants |
| `pipeline id: <stem>` footer | the stem, for traceability back to `processed/` |

`books/<stem>.meta.json` is generated from `books/library.md` by
`scripts/make_book_meta.py` (`--force` to overwrite, or pass stems).
`publisher` is usually left `""` for you to hand-add. The `books/`
sidecars are git-ignored, like the rest of the private corpus.

### Other per-book override files (next to the PDF in `books/`)

| File | Effect |
|---|---|
| `<stem>.crop` | `left,top,right,bottom` fractions trimmed before OCR |
| `<stem>.rotate` | `cw` / `ccw` / `180`, optionally `all:` (default `plates:`) — rotates the embedded scan when a book's plates are printed sideways (Gomard). `plates:` = figure pages only. Steps 3 & 4, via `_render.load_rotate()`. |

### Tables and folios in the PDFs

- A scoring / poule grid is a `[[TABLE]]` sentinel in `translation.txt`.
  Step 2 replaces every `<table>…</table>` (and a `<table>` the OCR
  truncated at the page foot) with the sentinel, collapses repeats, and
  — if what's left is only a short heading — translates just that
  heading and skips the model on the grid. If the model still mangles a
  surviving sentinel to a bare `TABLE` line, a post-pass snaps it back.
  Step 4 renders `[table — see facing page]` among the prose; step 3
  embeds the source page and drops the sentinel.
- A folio the reconciler only inferred (`[n]`) — or left blank (`""`) —
  produces **no** "Page" line at all in either PDF; front matter and
  unnumbered plate leaves just get the section rule. A run of ≥2 unread
  leaves between two trusted folios that spans more leaves than the folio
  gap covers is left blank rather than seesaw-interpolated
  (`[17][18][19][20][13][14]…` off the nearest anchor). Tight-gap-forced
  folios (incl. a single misread slot between two trusted folios, e.g.
  Gomard `903`→`303`, and monotonic-chain folios across unnumbered plate
  leaves) were promoted upstream and *do* show.
- **Two-part running head** (`SABRE` / `ATTAQUE ET PARADE`): drawn with
  the first half at the column's left edge and the second flush right.
- A full-page decorative vignette is **not** embedded as a whole-page
  "Figure N" in the English-only PDF when the page also has 40+ words of
  translated prose — the text wins, the vignette is dropped.

## Sidecar files (all in `processed/<book>_translated/intermediate/`)

| File | Written by | Used by | Contents |
|---|---|---|---|
| `translation.txt` | step 2 | 3, 4 | one translated page per record, `\x1e`-separated |
| `extracted.txt` | step 1 | 2, 3, 4 | `PAGE_NUMBER / HEADER / BODY_TEXT` scaffold per page |
| `page_numbers.txt` | step 1 | (reference) | one reconciled folio per line; `[n]` = interpolated, not printed |
| `figures.json` | step 1 (captions filled by step 2) | 3, 4 | `{ page_index: [ {kind: plate\|inset, caption, caption_en} ] }` |
| `headers.json` | step 2 | 3, 4 | `{ raw_running_header: english }` |
| `<book>_OCR.pdf` | step 1 | 3, 4 | verbatim copy of the source scan |

---

## Knobs

All near the top of each file:

- **Page size / margins** — `PAGE_W`, `PAGE_H`, `MARGIN`, `COL`, `LEFT`,
  `RIGHT`, `DIVIDER_X`.
- **Font sizes** — step 3 `FS = 10`; step 4 `SIZES = (9, 8, 7)`.
- **Figure height cap** — step 3 `FIGURE_MAX_H = COL.height * 0.82`.
- **Figure JPEG** — 150 dpi, `jpg_quality=72` (in `Flow.figure`). Raise
  for sharper plates at the cost of file size.
- **Density-profiler thresholds** — inside `find_figure_band()`: row
  darkness `> 0.30`, height `> 8 %` (or `> 3 %` if very dense), width
  `> 30 %`. Loosen to catch fainter illustrations, at the cost of false
  positives on dense text.
- **Alignment resample trigger** — `reconcile_fracs`: `len(fracs) >
  n_paras * 2`.
- **Leading / paragraph gap** — `_render.draw`'s `leading=1.28`,
  `gap_frac=0.55`.
